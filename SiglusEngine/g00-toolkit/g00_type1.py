#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
G00 Type=1 提取器/构建器（PNG + JSON）+ 进度条（tqdm 优先，回退百分比）

Type=1（对应 C# UnpackV1）：
- 文件头 5 字节：type(1) + w(2) + h(2)
- 从 offset=5 开始是 LZ 块：
    uint32 blockSize
    uint32 outSize
    packed bytes (blockSize - 8)
- 解压参数：min_count=2, bytes_pp=1
- 解压后的 payload：
    uint16 colors
    colors * 4 bytes palette entries (B,G,R,A)
    index data (Indexed8)，可能包含 stride padding：index_total_len = outSize - (2 + colors*4)

提取：
  g00_type1 extract input.g00 out_dir
  -> out_dir/<base>.png   （RGBA 真彩图，已应用调色板 Alpha）
  -> out_dir/<base>.json  （包含 palette/stride/outSize 等信息，回封用）

回封：
  g00_type1 build input.g00 out_dir/<base>.json out_dir output.g00 --preset max
  - 默认“保持原 palette 不变”，把 PNG 每像素映射回最接近的 palette 色（含 Alpha）
  - 按 manifest 里的 stride 重建索引缓冲区（含 padding），并保持 outSize 不变

依赖：
  pip install pillow numpy
可选（更快/更好看）：
  pip install tqdm xxhash
可选（更快压缩）：
  pip install numba
"""

import argparse
import json
import os
import struct
import sys
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, List, Callable

# ------------------ 可选依赖：tqdm 进度条 ------------------
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# ------------------ 可选依赖：ConsoleProgressBar ------------------
try:
    from progress_utils import ConsoleProgressBar
    PROGRESS_UTILS_AVAILABLE = True
except ImportError:
    PROGRESS_UTILS_AVAILABLE = False

# ------------------ 依赖 ------------------
try:
    import numpy as np
except ImportError:
    print("错误: 需要安装 NumPy: pip install numpy")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    print("错误: 需要安装 Pillow: pip install pillow")
    sys.exit(1)

# 可选：更快 hash
try:
    import xxhash
    _FAST_HASH = getattr(xxhash, "xxh3_64_intdigest", xxhash.xxh64_intdigest)
    XXHASH_AVAILABLE = True
except ImportError:
    XXHASH_AVAILABLE = False
    _FAST_HASH = None

# ------------------ PSD 导出依赖 ------------------
try:
    from psd_tools import PSDImage
    PSD_TOOLS_AVAILABLE = True
except ImportError:
    PSD_TOOLS_AVAILABLE = False

# 可选：更快 LCP（压缩更快）
try:
    from numba import njit
    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False

# 导入优化模块（可选）
try:
    from g00_optimizer import (
        OptimizedLZSS, OptimizedMatcher, get_optimizer_config,
        NUMBA_AVAILABLE as OPT_NUMBA_AVAILABLE
    )
    OPTIMIZER_AVAILABLE = True
except ImportError:
    OPTIMIZER_AVAILABLE = False
    OPT_NUMBA_AVAILABLE = False


# ------------------ 路径辅助（Windows 长路径） ------------------
def _nt_longpath(p: str) -> str:
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    return p


# ------------------ 进度显示（tqdm 优先 + 回退百分比 + GUI 回调支持） ------------------
# GUI 进度回调类型定义
ProgressCallback = Callable[[int, int], None]  # (current, total)


class Progress:
    """
    如果可用并且 stdout 是 tty，则使用 tqdm；
    否则偶尔输出简单百分比（每 2% 或 100%）。
    
    新增 GUI 回调支持：可传入 gui_callback 参数，
    进度更新时会调用回调函数 (current, total)。
    """
    def __init__(self, total: int, desc: str, enabled: bool = True,
                 gui_callback: Optional[ProgressCallback] = None):
        self.total = max(1, int(total))
        self.desc = desc
        self.enabled = enabled
        self.gui_callback = gui_callback
        self._last_print = -1
        self._cur = 0
        self._tqdm = None
        self._last_cb_time = 0.0
        self._cb_throttle = 0.05  # GUI 回调节流间隔（秒）

        # 启用 tqdm：命令行模式或 GUI 模式都显示（GUI 仅用于命令行窗口反馈）
        if self.enabled and TQDM_AVAILABLE and sys.stdout.isatty():
            self._tqdm = tqdm(
                total=self.total,
                desc=desc,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                file=sys.stdout,
            )

    def update_to(self, cur: int):
        if not self.enabled and self.gui_callback is None:
            return
        cur = int(max(0, min(self.total, cur)))

        # GUI 回调模式（优先）
        if self.gui_callback is not None:
            import time
            now = time.time()
            # 节流：避免过于频繁的回调，但 100% 时必须立即更新
            if (now - self._last_cb_time >= self._cb_throttle) or (cur >= self.total):
                self.gui_callback(cur, self.total)
                self._last_cb_time = now
            self._cur = cur
            return

        if self._tqdm is not None:
            delta = cur - self._cur
            if delta:
                self._tqdm.update(delta)
            self._cur = cur
            return

        if not sys.stdout.isatty():
            self._cur = cur
            return

        percent = int(cur * 100 / self.total)
        if percent != self._last_print and (percent % 2 == 0 or percent == 100):
            self._last_print = percent
            sys.stdout.write(f"\r{self.desc}: {percent:3d}% ({cur}/{self.total})")
            sys.stdout.flush()
            if percent == 100:
                sys.stdout.write("\n")
                sys.stdout.flush()

        self._cur = cur

    def close(self):
        # 确保完成时回调 100%
        if self.gui_callback is not None:
            self.gui_callback(self.total, self.total)
        if self._tqdm is not None:
            self._tqdm.close()
            self._tqdm = None


# ------------------ LZSS 解压（与 C# LzDecompress 一致） ------------------
def lz_decompress(packed: bytes, output_size: int, min_count: int, bytes_pp: int,
                  progress: Optional[Progress] = None) -> bytes:
    """
    LZSS 解压
    
    优化：若优化模块可用，自动使用 Numba JIT 加速版本
    """
    # 优化器加速版本
    if OPTIMIZER_AVAILABLE:
        # 性能优化：不传递内部进度回调，避免 165 倍性能损失
        # 直接显示开始和结束即可
        if progress:
            progress.update_to(0)
        
        result = OptimizedLZSS.decompress(
            packed, output_size, min_count, bytes_pp,
            progress_cb=None  # 不传递回调，使用快速 Numba 版本
        )
        
        if progress:
            progress.update_to(output_size)
        return result
    
    # 原始 Python 实现（回退）
    out = bytearray(output_size)
    dst = 0
    bits = 2
    src = 0
    packed_size = len(packed)

    if progress:
        progress.update_to(0)

    while dst < output_size and packed_size > 0:
        bits >>= 1
        if bits == 1:
            if src >= len(packed):
                break
            bits = packed[src] | 0x100
            src += 1
            packed_size -= 1

        if bits & 1:
            if src + bytes_pp > len(packed):
                break
            out[dst:dst + bytes_pp] = packed[src:src + bytes_pp]
            src += bytes_pp
            dst += bytes_pp
            packed_size -= bytes_pp
        else:
            if packed_size < 2 or src + 2 > len(packed):
                break
            token = packed[src] | (packed[src + 1] << 8)
            src += 2
            packed_size -= 2

            length = (token & 0xF) + min_count
            back = (token >> 4) * bytes_pp
            length *= bytes_pp
            if back <= 0:
                raise ValueError("无效的 LZ 回溯距离")

            for i in range(length):
                out[dst + i] = out[dst - back + i]
            dst += length

        if progress:
            progress.update_to(dst)

    if progress:
        progress.update_to(output_size)

    return bytes(out)


# ------------------ LZSS 压缩（type1/type2：min_count=2, bytes_pp=1） ------------------
def _py_hash_bytes(b: bytes) -> int:
    return hash(b) & 0xFFFFFFFFFFFFFFFF


if NUMBA_AVAILABLE:
    @njit(cache=True, fastmath=True)
    def _lcp_numba(buf: np.ndarray, a: int, b: int, limit: int) -> int:
        i = 0
        while i < limit:
            if buf[a + i] != buf[b + i]:
                break
            i += 1
        return i


class FastMatcher:
    def __init__(self, window: int = 4095, k: int = 8, max_bucket: int = 64, max_candidates: int = 256):
        self.window = window
        self.k = k
        self.max_bucket = max_bucket
        self.max_candidates = max_candidates
        self.table: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.max_bucket))
        self.mv: Optional[memoryview] = None
        self.n = 0
        self.npbuf: Optional[np.ndarray] = None

    def bind(self, data: bytes):
        self.mv = memoryview(data)
        self.n = len(self.mv)
        self.table.clear()
        self.npbuf = np.frombuffer(self.mv, dtype=np.uint8)

    def _h(self, pos: int) -> int:
        if self.mv is None:
            return -1
        if pos < 0 or pos + self.k > self.n:
            return -1
        chunk = self.mv[pos:pos + self.k]
        if XXHASH_AVAILABLE:
            return _FAST_HASH(chunk)
        return _py_hash_bytes(bytes(chunk))

    def feed(self, pos: int):
        h = self._h(pos)
        if h == -1:
            return
        self.table[h].append(pos)

    def _lcp(self, a: int, b: int, limit: int) -> int:
        if limit <= 0:
            return 0
        if NUMBA_AVAILABLE and self.npbuf is not None:
            return int(_lcp_numba(self.npbuf, a, b, limit))
        mv = self.mv
        i = 0
        while i < limit and mv[a + i] == mv[b + i]:
            i += 1
        return i

    def find(self, pos: int, max_len: int, min_len: int) -> Tuple[int, int]:
        if self.mv is None:
            return (0, 0)
        if pos + self.k > self.n:
            return (0, 0)
        h = self._h(pos)
        if h == -1:
            return (0, 0)
        bucket = self.table.get(h)
        if not bucket:
            return (0, 0)

        window_start = max(0, pos - self.window)
        best_len = 0
        best_dist = 0
        checked = 0

        for cand in reversed(bucket):
            if cand < window_start:
                break
            dist = pos - cand
            if dist <= 0 or dist > self.window:
                continue
            checked += 1
            if checked > self.max_candidates:
                break

            limit = max_len
            if limit > dist:
                limit = dist
            remaining = self.n - pos
            if limit > remaining:
                limit = remaining
            if limit <= best_len:
                continue

            ln = self._lcp(pos, cand, limit)
            if ln >= min_len and ln > best_len:
                best_len = ln
                best_dist = dist
                if best_len == max_len:
                    break

        return (best_dist, best_len)


def lz_compress_type1(data: bytes, preset: str = "normal", progress: Optional[Progress] = None) -> bytes:
    """
    编码为与 lz_decompress(min_count=2, bytes_pp=1) 兼容的 LZSS 数据。
    token = (dist << 4) | (len - 2)
    1 <= dist <= 0xFFF, 2 <= len <= 17
    
    优化：若优化模块可用，自动使用优化的匹配器
    """
    n = len(data)
    if n == 0:
        return b""
    
    # 命令行进度条（仅当 progress=None 时启用）
    pbar = None
    if PROGRESS_UTILS_AVAILABLE and progress is None:
        try:
            pbar = ConsoleProgressBar(
                total=n,
                desc=f"Type1 {preset}压缩",
                width=50,
                show_speed=True,
                show_eta=False
            )
        except Exception:
            pass
    
    # GUI 模式优化：不传递内部进度回调，避免性能损失
    # 压缩进度由批量层显示即可
    should_pass_progress = False  # 压缩也不传递内部回调
    
    # 使用优化器
    if OPTIMIZER_AVAILABLE:
        if progress:
            progress.update_to(0)
        
        result = OptimizedLZSS.compress(
            data, min_count=2, bytes_pp=1, preset=preset,
            progress_cb=None  # 不传递回调
        )
        
        if progress:
            progress.update_to(n)
        elif pbar:
            pbar.close()
        
        return result

    min_count = 2
    max_back = 0xFFF
    max_len = min_count + 0xF  # 17

    if preset == "fast":
        k, bucket, lazy, candidates = 8, 32, 1, 96
    elif preset == "max":
        k, bucket, lazy, candidates = 4, 128, 2, 512
    else:
        k, bucket, lazy, candidates = 8, 64, 2, 256

    matcher = FastMatcher(window=max_back, k=k, max_bucket=bucket, max_candidates=candidates)
    matcher.bind(data)
    mv = memoryview(data)

    out = bytearray()
    pos = 0

    if progress:
        progress.update_to(0)

    def feed_range(a: int, b: int):
        for p in range(a, b):
            matcher.feed(p)

    while pos < n:
        ctrl_pos = len(out)
        out.append(0)
        ctrl = 0

        for bit in range(8):
            if pos >= n:
                break

            best_dist, best_len = matcher.find(pos, max_len=max_len, min_len=min_count)

            if best_len >= min_count and lazy > 0:
                improved = False
                for look in range(1, lazy + 1):
                    if pos + look >= n:
                        break
                    d2, l2 = matcher.find(pos + look, max_len=max_len, min_len=min_count)
                    if l2 > best_len + look:
                        improved = True
                        break
                if improved:
                    ctrl |= (1 << bit)
                    out.append(mv[pos])
                    matcher.feed(pos)
                    pos += 1
                    if progress:
                        progress.update_to(pos)
                    continue

            if best_len >= min_count and 1 <= best_dist <= max_back:
                if best_len > max_len:
                    best_len = max_len
                token = (best_dist << 4) | (best_len - min_count)
                out += struct.pack("<H", token & 0xFFFF)

                start = pos
                end = min(n, pos + best_len)
                feed_range(start, end)
                pos = end
            else:
                ctrl |= (1 << bit)
                out.append(mv[pos])
                matcher.feed(pos)
                pos += 1

            if progress:
                progress.update_to(pos)

        out[ctrl_pos] = ctrl

    if progress:
        progress.update_to(n)

    return bytes(out)


# ------------------ 图像/调色板辅助 ------------------
def load_png_rgba(path: str) -> np.ndarray:
    p = _nt_longpath(path)
    img = Image.open(p).convert("RGBA")
    return np.array(img, dtype=np.uint8)


def save_png_rgba(path: str, rgba: np.ndarray) -> None:
    p = _nt_longpath(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(p)


def palette_bgra_bytes_to_rgba(pal_bgra: bytes, colors: int) -> np.ndarray:
    arr = np.frombuffer(pal_bgra, dtype=np.uint8).reshape((colors, 4))  # B G R A
    rgba = arr[:, [2, 1, 0, 3]]
    return rgba.copy()


def palette_rgba_to_bgra_bytes(pal_rgba: np.ndarray) -> bytes:
    # pal_rgba: (N,4) RGBA -> bytes in BGRA order
    bgra = pal_rgba[:, [2, 1, 0, 3]].astype(np.uint8, copy=False)
    return bgra.tobytes(order="C")


def indexed_to_rgba(indices: np.ndarray, pal_rgba: np.ndarray) -> np.ndarray:
    # indices: (H,W) uint8 ; pal_rgba: (N,4)
    return pal_rgba[indices]


def _u32_from_rgba(arr_rgba: np.ndarray) -> np.ndarray:
    # arr_rgba: (...,4) uint8 -> uint32 key little-endian packed
    a = arr_rgba.astype(np.uint32, copy=False)
    return (a[..., 0] |
            (a[..., 1] << 8) |
            (a[..., 2] << 16) |
            (a[..., 3] << 24))


def map_rgba_to_indices(rgba: np.ndarray, pal_rgba: np.ndarray, progress: Optional[Progress] = None) -> np.ndarray:
    """
    将 RGBA 真彩图映射回调色板索引：
    1) 先做精确匹配（RGBA 四通道完全一致）
    2) 对不匹配的颜色做最近邻（RGBA 欧式距离，含 alpha）
    """
    h, w, _ = rgba.shape
    pix_u32 = _u32_from_rgba(rgba.reshape((-1, 4)))
    pal_u32 = _u32_from_rgba(pal_rgba.reshape((-1, 4)))

    # 精确映射表
    exact = {int(pal_u32[i]): i for i in range(pal_u32.size)}

    # unique 压缩问题规模
    uniq, inv = np.unique(pix_u32, return_inverse=True)
    out_map = np.empty(uniq.shape[0], dtype=np.uint16)

    if progress:
        progress.update_to(0)

    # 先填精确匹配
    miss_mask = np.ones(uniq.shape[0], dtype=bool)
    for i, key in enumerate(uniq):
        idx = exact.get(int(key), -1)
        if idx >= 0:
            out_map[i] = idx
            miss_mask[i] = False

    miss = np.nonzero(miss_mask)[0]
    if miss.size == 0:
        if progress:
            progress.update_to(progress.total)
        return out_map[inv].astype(np.uint8).reshape((h, w))

    # 最近邻：对 miss 的 uniq 颜色做 nearest palette
    # 解析 miss 的 RGBA（从 u32 还原）
    miss_u32 = uniq[miss].astype(np.uint32, copy=False)
    miss_rgba = np.empty((miss.size, 4), dtype=np.int16)
    miss_rgba[:, 0] = (miss_u32 & 0xFF).astype(np.int16)
    miss_rgba[:, 1] = ((miss_u32 >> 8) & 0xFF).astype(np.int16)
    miss_rgba[:, 2] = ((miss_u32 >> 16) & 0xFF).astype(np.int16)
    miss_rgba[:, 3] = ((miss_u32 >> 24) & 0xFF).astype(np.int16)

    pal16 = pal_rgba.astype(np.int16, copy=False)

    # 分块计算距离，避免一次性占太多内存
    # chunk * colors 的距离矩阵，colors<=256 一般很稳
    chunk = 32768
    total = miss.size
    done = 0

    for start in range(0, total, chunk):
        end = min(total, start + chunk)
        c = miss_rgba[start:end]  # (M,4)
        # (M,1,4) - (1,N,4) -> (M,N,4)
        diff = c[:, None, :] - pal16[None, :, :]
        dist2 = (diff * diff).sum(axis=2)  # (M,N)
        nn = dist2.argmin(axis=1).astype(np.uint16)
        out_map[miss[start:end]] = nn

        done += (end - start)
        if progress:
            progress.update_to(min(progress.total, done))

    if progress:
        progress.update_to(progress.total)

    return out_map[inv].astype(np.uint8).reshape((h, w))


# ------------------ Type=1 读写 ------------------
@dataclass
class G00Type1:
    width: int
    height: int
    block_size: int
    out_size: int
    packed: bytes
    decompressed: bytes
    header5: bytes  # type+w+h

    colors: int
    palette_rgba: np.ndarray  # (colors,4)
    stride: int               # bytes per row in index buffer
    index_total_len: int      # stride*height

    @staticmethod
    def read(path: str, show_progress: bool = True,
             gui_callback: Optional[ProgressCallback] = None) -> "G00Type1":
        p = _nt_longpath(path)
        with open(p, "rb") as f:
            header5 = f.read(5)
            if len(header5) != 5:
                raise ValueError("文件过小（缺少 5 字节头）")
            typ = header5[0]
            if typ != 1:
                raise ValueError(f"不是 type=1 的 g00（byte0={typ}）")

            w = struct.unpack_from("<H", header5, 1)[0]
            h = struct.unpack_from("<H", header5, 3)[0]
            if w == 0 or h == 0 or w > 0x8000 or h > 0x8000:
                raise ValueError(f"无效的宽高: {w}x{h}")

            bs_bytes = f.read(4)
            if len(bs_bytes) != 4:
                raise ValueError("缺少 blockSize")
            block_size = struct.unpack("<I", bs_bytes)[0]

            file_len = os.path.getsize(p)
            if block_size + 5 != file_len:
                raise ValueError(f"长度校验失败: blockSize+5={block_size+5}, fileLen={file_len}")

            os_bytes = f.read(4)
            if len(os_bytes) != 4:
                raise ValueError("缺少 outSize")
            out_size = struct.unpack("<I", os_bytes)[0]

            packed_len = int(block_size) - 8
            if packed_len < 0:
                raise ValueError("blockSize 无效（<8）")
            packed = f.read(packed_len)
            if len(packed) != packed_len:
                raise ValueError("压缩数据被截断")

        pb = Progress(out_size, "Decompress", enabled=show_progress, gui_callback=gui_callback)
        dec = lz_decompress(packed, out_size, min_count=2, bytes_pp=1, progress=pb)
        pb.close()

        if out_size < 2:
            raise ValueError("outSize 过小（缺少 colors）")
        colors = struct.unpack_from("<H", dec, 0)[0]
        if colors <= 0 or colors > 256:
            raise ValueError(f"colors 非法或超出 Indexed8 范围: {colors}")

        pal_bytes_len = colors * 4
        if out_size < 2 + pal_bytes_len:
            raise ValueError("outSize 不足以容纳调色板")

        pal_bgra = dec[2:2 + pal_bytes_len]
        pal_rgba = palette_bgra_bytes_to_rgba(pal_bgra, colors)

        index_total_len = out_size - (2 + pal_bytes_len)
        if index_total_len <= 0:
            raise ValueError("没有索引数据")
        # stride 推断：优先用 index_total_len/h
        stride = 0
        if index_total_len % h == 0:
            stride = index_total_len // h
        else:
            # 兜底：认为无 padding
            stride = w
            print(f"警告: 索引区长度 {index_total_len} 不能整除 height={h}，stride 退回为 width={w}（回封可能不完全一致）")

        if stride < w:
            print(f"警告: 推断 stride={stride} < width={w}，stride 强制设为 width")
            stride = w

        return G00Type1(
            width=w,
            height=h,
            block_size=block_size,
            out_size=out_size,
            packed=packed,
            decompressed=dec,
            header5=header5,
            colors=colors,
            palette_rgba=pal_rgba,
            stride=stride,
            index_total_len=index_total_len if index_total_len % h == 0 else (stride * h),
        )

    def write(self, out_path: str, packed_payload: bytes, out_size: int) -> None:
        p = _nt_longpath(out_path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        block_size = len(packed_payload) + 8
        with open(p, "wb") as f:
            f.write(self.header5)
            f.write(struct.pack("<I", block_size))
            f.write(struct.pack("<I", out_size))
            f.write(packed_payload)


# ------------------ JSON ------------------
def save_manifest(path: str, obj: Dict[str, Any]) -> None:
    p = _nt_longpath(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as w:
        json.dump(obj, w, ensure_ascii=False, indent=2)


def load_manifest(path: str) -> Dict[str, Any]:
    p = _nt_longpath(path)
    with open(p, "r", encoding="utf-8-sig") as r:
        return json.load(r)


# ------------------ Extract / Build ------------------
def extract_cmd(input_g00: str, out_dir: str, show_progress: bool = True,
                file_progress_cb: Optional[ProgressCallback] = None) -> None:
    os.makedirs(out_dir, exist_ok=True)
    g = G00Type1.read(input_g00, show_progress=show_progress, gui_callback=file_progress_cb)
    base = os.path.splitext(os.path.basename(input_g00))[0]

    # 取索引数据（可能含 stride padding），导出 png 用“去 padding 后的 w*h”
    pal_bytes_len = g.colors * 4
    idx_off = 2 + pal_bytes_len
    idx = g.decompressed[idx_off: idx_off + (g.stride * g.height)]
    if len(idx) < g.stride * g.height:
        raise ValueError("索引区不足（文件可能损坏）")

    idx_arr = np.frombuffer(idx, dtype=np.uint8).reshape((g.height, g.stride))
    idx_arr = idx_arr[:, :g.width]  # 去掉每行 padding

    rgba = indexed_to_rgba(idx_arr, g.palette_rgba)  # (H,W,4)
    png_name = f"{base}.png"
    png_path = os.path.join(out_dir, png_name)
    save_png_rgba(png_path, rgba)

    manifest = {
        "format": "RealLive.G00.Type1",
        "version": 1,
        "sourceFile": os.path.basename(input_g00),
        "type": 1,
        "width": g.width,
        "height": g.height,
        "png": png_name,
        "colors": g.colors,
        "paletteRGBA": g.palette_rgba.astype(np.uint8).tolist(),  # list of [r,g,b,a]
        "stride": g.stride,
        "outSize": g.out_size,
        "note": "pixelFormat=Indexed8+Palette(BGRA stored); LZ(min_count=2, bytes_pp=1). PNG is exported as RGBA after palette applied.",
    }
    json_path = os.path.join(out_dir, f"{base}.json")
    save_manifest(json_path, manifest)

    print(f"OK: extracted -> {out_dir}")
    print(f"  PNG:  {png_path}")
    print(f"  JSON: {json_path}")
    print(f"  colors={g.colors}, stride={g.stride}, outSize={g.out_size}")

def export_psd_cmd(json_path: str, png_dir: str, output_psd: str, 
                    show_progress: bool = True) -> None:
    """
    根据 JSON 配置和 PNG 图片，生成单个图层的 PSD 文件。
    
    参数:
        json_path: 提取生成的 JSON 文件路径
        png_dir: PNG 图片所在目录
        output_psd: 输出的 PSD 文件路径
        show_progress: 显示进度
    """
    if not PSD_TOOLS_AVAILABLE:
        print("错误: 需要安装 psd-tools 来创建 PSD 文件")
        print("安装命令: pip install psd-tools")
        sys.exit(1)
    
    # 加载 manifest
    manifest = load_manifest(json_path)
    if manifest.get("type") != 1:
        raise ValueError("Manifest type != 1")
    
    canvas_width = int(manifest["width"])
    canvas_height = int(manifest["height"])
    png_name = manifest.get("png", f"{os.path.splitext(os.path.basename(json_path))[0]}.png")
    
    if show_progress:
        print(f"创建 PSD: {canvas_width}x{canvas_height}")
    
    # 加载 PNG 图片
    png_path = os.path.join(png_dir, png_name)
    if not os.path.isfile(png_path):
        print(f"错误: 图片不存在 {png_path}")
        sys.exit(1)
    
    try:
        rgba = load_png_rgba(png_path)
    except Exception as e:
        print(f"错误: 无法加载图片 {png_path}: {e}")
        sys.exit(1)
    
    # 检查尺寸
    img_h, img_w = rgba.shape[:2]
    if img_w == 0 or img_h == 0:
        print("错误: 图片尺寸为 0")
        sys.exit(1)
    
    # 创建图层名称
    base_name = os.path.splitext(os.path.basename(json_path))[0]
    layer_name = f"{base_name}#000"
    
    if show_progress:
        print(f"  图层 0: {layer_name} @ (0, 0) {img_w}x{img_h}")
    
    # 使用 psd-tools 创建 PSD 文件
    # 创建一个新的 PSD 文件
    psd = PSDImage.new(mode='RGBA', size=(canvas_width, canvas_height), color=(0, 0, 0, 0))
    
    # 创建图层（使用 RLE 压缩，并设置正确的位置）
    pil_image = Image.fromarray(rgba)
    layer = psd.create_pixel_layer(pil_image, name=layer_name)
    psd.append(layer)
    
    # 保存 PSD 文件
    output_path = _nt_longpath(output_psd)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    
    psd.save(output_path)
    
    print(f"OK: PSD 已保存 -> {output_path}")
    print(f"  图层数: 1")
    print(f"  画布大小: {canvas_width}x{canvas_height}")

def build_cmd(input_g00: str, json_path: str, png_dir: str, output_g00: str,
              preset: str, show_progress: bool = True,
              file_progress_cb: Optional[ProgressCallback] = None) -> None:
    manifest = load_manifest(json_path)
    if manifest.get("type") != 1:
        raise ValueError("Manifest type != 1")

    # 读取时不显示进度（节省时间，主要进度在压缩阶段）
    g1 = G00Type1.read(input_g00, show_progress=False, gui_callback=None)

    w = int(manifest["width"])
    h = int(manifest["height"])
    if w != g1.width or h != g1.height:
        raise ValueError(f"Manifest 宽高与原 g00 不一致: manifest={w}x{h}, g00={g1.width}x{g1.height}")

    colors = int(manifest["colors"])
    if colors != g1.colors:
        # 你也可以放开这个限制，但默认更安全：保持 palette 不变
        raise ValueError(f"Manifest colors 与原 g00 不一致: manifest={colors}, g00={g1.colors}")

    stride = int(manifest.get("stride", g1.stride))
    out_size = int(manifest.get("outSize", g1.out_size))
    if stride < w:
        raise ValueError(f"manifest stride({stride}) < width({w})")

    pal_list = manifest["paletteRGBA"]
    pal_rgba = np.array(pal_list, dtype=np.uint8)
    if pal_rgba.shape != (colors, 4):
        raise ValueError("paletteRGBA 形状不正确")

    png_name = manifest.get("png", f"{os.path.splitext(os.path.basename(input_g00))[0]}.png")
    png_path = os.path.join(png_dir, png_name)
    rgba = load_png_rgba(png_path)
    if rgba.shape[0] != h or rgba.shape[1] != w:
        raise ValueError(f"PNG 尺寸不匹配: got {rgba.shape[1]}x{rgba.shape[0]}, expected {w}x{h}")

    # 映射回索引
    map_pb = Progress(max(1, rgba.shape[0] * rgba.shape[1]), "MapToPalette", enabled=show_progress, gui_callback=file_progress_cb)
    idx2d = map_rgba_to_indices(rgba, pal_rgba, progress=map_pb)
    map_pb.close()

    # 重建索引缓冲区（含 stride padding）
    idx_buf = np.zeros((h, stride), dtype=np.uint8)
    idx_buf[:, :w] = idx2d
    idx_bytes = idx_buf.tobytes(order="C")

    # 组装 payload：colors + palette(BGRA) + index bytes
    pal_bgra_bytes = palette_rgba_to_bgra_bytes(pal_rgba)
    payload = struct.pack("<H", colors) + pal_bgra_bytes + idx_bytes

    # 保持 outSize 一致（更安全）
    if out_size != len(payload):
        # 如果 manifest 的 outSize 不对，则以 payload 为准，但会提示
        print(f"警告: manifest outSize={out_size} 与当前 payload={len(payload)} 不一致，回封将使用 payload 长度。")
        out_size = len(payload)

    # 压缩
    pb = Progress(len(payload), f"Compress({preset})", enabled=show_progress, gui_callback=file_progress_cb)
    packed_payload = lz_compress_type1(payload, preset=preset, progress=pb)
    pb.close()

    # 写出
    g1.write(output_g00, packed_payload=packed_payload, out_size=out_size)

    print(f"OK: built -> {output_g00}")
    print(f"  preset={preset}")
    print(f"  colors={colors}, stride={stride}, outSize={out_size}")
    print(f"  packed payload bytes: {len(packed_payload)}")
    if not XXHASH_AVAILABLE:
        print("  NOTE: xxhash 未安装，压缩会慢一些。建议: pip install xxhash")
    if not NUMBA_AVAILABLE:
        print("  NOTE: numba 未安装，压缩会慢一些。建议: pip install numba")


# ------------------ CLI ------------------
def main():
    ap = argparse.ArgumentParser(description="Type=1 G00 提取/构建工具（PNG + JSON）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_ex = sub.add_parser("extract", help="提取 type1 g00 -> <base>.png + <base>.json")
    ap_ex.add_argument("input_g00")
    ap_ex.add_argument("out_dir")
    ap_ex.add_argument("--no-progress", action="store_true", default=False)

    ap_b = sub.add_parser("build", help="从 原始 g00 + json + png 构建新的 type1 g00")
    ap_b.add_argument("input_g00")
    ap_b.add_argument("json_path")
    ap_b.add_argument("png_dir")
    ap_b.add_argument("output_g00")
    ap_b.add_argument("--preset", choices=["fast", "normal", "max", "promax"], default="normal")
    ap_b.add_argument("--no-progress", action="store_true", default=False)

    ap_psd = sub.add_parser("export_psd", help="从 json + png 导出为 PSD 文件（单个图层）")
    ap_psd.add_argument("json_path")
    ap_psd.add_argument("png_dir")
    ap_psd.add_argument("output_psd")
    ap_psd.add_argument("--no-progress", action="store_true", default=False)

    args = ap.parse_args()
    if args.cmd == "extract":
        extract_cmd(args.input_g00, args.out_dir, show_progress=(not args.no_progress))
    elif args.cmd == "build":
        build_cmd(args.input_g00, args.json_path, args.png_dir, args.output_g00,
                  preset=args.preset, show_progress=(not args.no_progress))
    elif args.cmd == "export_psd":
        export_psd_cmd(args.json_path, args.png_dir, args.output_psd,
                      show_progress=(not args.no_progress))
    else:
        ap.error("未知命令")


if __name__ == "__main__":
    main()
