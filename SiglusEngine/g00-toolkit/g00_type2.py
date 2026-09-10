#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
G00 Type=2 提取器/构建器，带快速 LZSS 压缩器（NumPy + xxhash + 可选 Numba）
- extract：g00 -> PNG + {basename}.json
- build：  原始 g00 + {basename}.json + 已编辑 PNG -> 新的 g00
- to-psd：g00 -> G00Pack 官方兼容 PSD（默认格式）

Type=2 格式行为与提供的 C# 代码一致：
- 头部：type(1) + w(2) + h(2) + count(int16)(2) + reserved(2) => 9 字节
- 索引表：count * 0x18 字节；前 8 字节为 X(int32), Y(int32)
- LZ 块：uint32 blockSize, uint32 outSize，后跟打包字节（blockSize-8）
- 解压后的负载：
    int32 frameCount
    frameCount * (uint32 offset, uint32 size)
    每个 frame 块（若 size>0）：
        uint16 tile_type == 1
        uint16 tile_count
        跳过 0x70
        重复 tile_count 次：
            uint16 tile_x, uint16 tile_y, int16 unk, uint16 tile_w, uint16 tile_h
            跳过 0x52
            BGRA 像素：tile_w*tile_h*4

提取
:: 旧功能（整张画布）——默认就是 full
g00_type2 extract ef_story_text00.g00 output [--mode full]

:: 只导出最小覆盖区域（推荐）
g00_type2 extract ef_story_text00.g00 output --mode bbox

:: 只导出每个 tile 小图
g00_type2 extract ef_story_text00.g00 output --mode tiles

:: 同时保留 full + bbox（或 full + tiles）
g00_type2 extract ef_story_text00.g00 output --mode both

回封
:: 自动根据 json 选择（json 里会记录 editMode），找不到就回退
g00_type2 build ef_story_text00.g00 output/ef_story_text00.json output new.g00 --mode auto --preset max

:: 强制按 bbox 回封
g00_type2 build ef_story_text00.g00 output/ef_story_text00.json output new.g00 --mode bbox --preset max

:: 强制按 tiles 回封
g00_type2 build ef_story_text00.g00 output/ef_story_text00.json output new.g00 --mode tiles --preset max

:: 仍然支持 full（你现在的方式）
g00_type2 build ef_story_text00.g00 output/ef_story_text00.json output new.g00 --mode full --preset max

导出 PSD（G00Pack 官方兼容格式）
:: 导出为 G00Pack 官方兼容的 PSD 文件
g00_type2 to-psd ef_story_text00.g00 output.psd

"""

import argparse
import json
import os
import struct
import sys
import time
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple, Optional, Callable
from collections import defaultdict, deque

# ------------------ 可选依赖 ------------------
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

# PSD 创建依赖（可选）
try:
    # 先导入独立的 packbits 模块，然后注入到 pytoshop 的命名空间
    # 这样 pytoshop.codecs 就能找到 packbits 模块了
    import sys
    import packbits as standalone_packbits
    sys.modules['pytoshop.packbits'] = standalone_packbits
    
    import pytoshop
    from pytoshop import layers as psd_layers
    from pytoshop.enums import BlendMode, ColorMode
    PYTOSHOP_AVAILABLE = True
except ImportError:
    PYTOSHOP_AVAILABLE = False

# PSD 读取依赖（可选）
try:
    from psd_tools import PSDImage
    PSD_TOOLS_AVAILABLE = True
except ImportError:
    PSD_TOOLS_AVAILABLE = False

try:
    import xxhash
    _FAST_HASH = getattr(xxhash, "xxh3_64_intdigest", xxhash.xxh64_intdigest)
    XXHASH_AVAILABLE = True
except ImportError:
    XXHASH_AVAILABLE = False
    _FAST_HASH = None

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

try:
    import packbits
    PACKBITS_AVAILABLE = True
except ImportError:
    PACKBITS_AVAILABLE = False

# ------------------ 可选依赖：ConsoleProgressBar ------------------
try:
    from progress_utils import ConsoleProgressBar
    PROGRESS_UTILS_AVAILABLE = True
except ImportError:
    PROGRESS_UTILS_AVAILABLE = False

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
    if os.name == 'nt':
        if not p.startswith('\\\\?\\'):
            p = '\\\\?\\' + p
    return p


# ------------------ 进度显示辅助 + GUI 回调支持 ------------------
# GUI 进度回调类型定义
ProgressCallback = Callable[[int, int], None]  # (current, total)


class Progress:
    """
    如果可用并且 stdout 是 tty，则使用 tqdm；否则偶尔输出简单百分比。
    
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
            self._tqdm = tqdm(total=self.total, desc=desc, unit="B", unit_scale=True, unit_divisor=1024, file=sys.stdout)

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

        # 简单文本回退
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


# ------------------ LZSS（type=2）解压 ------------------

def lz_decompress(packed: bytes, output_size: int, min_count: int, bytes_pp: int,
                  progress: Optional[Progress] = None) -> bytes:
    """
    LZSS 解压
    
    格式：
    - 每组 8 个项对应一个 ctrl 字节，按 LSB 优先
    - bit=1 => 字面量（复制 bytes_pp 字节）
    - bit=0 => 回溯引用 token（小端 2 字节）：
        length = (token & 0xF) + min_count
        distance = token >> 4
        从 out[dst - distance*bytes_pp] 开始复制
    
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

            # 重叠拷贝
            for i in range(length):
                out[dst + i] = out[dst - back + i]
            dst += length

        if progress:
            progress.update_to(dst)

    if progress:
        progress.update_to(output_size)

    return bytes(out)


# ------------------ 快速匹配器（受 png2pgd_ge.py FastMatcher 启发） ------------------
# 参考：使用 xxhash 分桶 + 可选 numba 的 LCP（最长公共前缀）加速

def _py_hash_bytes(b: bytes) -> int:
    # 若缺少 xxhash，则使用 Python 的 hash 作为回退
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
    """
    滑动窗口匹配器：
    - 为 k 字节 key 维护 hash 桶
    - 每个桶保存最近的位置（由 max_bucket 限制）
    - find() 逆序扫描候选（优先最近的）
    """
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
        # 为可选的 numba LCP 创建 numpy 视图
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
        # limit 较小（通常 <= 17），Python 循环也足够
        mv = self.mv
        i = 0
        while i < limit and mv[a + i] == mv[b + i]:
            i += 1
        return i

    def find(self, pos: int, max_len: int, min_len: int) -> Tuple[int, int]:
        """
        返回 (最佳距离 best_dist, 最佳长度 best_len)
        """
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


# ------------------ LZSS（type=2）压缩（快速） ------------------

def lz_compress_type2(data: bytes, preset: str = "normal", progress: Optional[Progress] = None) -> bytes:
    """
    编码为与 lz_decompress(min_count=2, bytes_pp=1) 兼容的 LZSS 数据。
    Token：
      token = (dist << 4) | (len - 2)
      其中 1 <= dist <= 0xFFF，2 <= len <= 17
    
    优化：若优化模块可用，自动使用优化的匹配器
    """
    n = len(data)
    if n == 0:
        return b""
    
    # 命令行进度条
    pbar = None
    if PROGRESS_UTILS_AVAILABLE and progress is None:
        try:
            pbar = ConsoleProgressBar(
                total=n,
                desc=f"Type2 {preset}压缩",
                width=50,
                show_speed=True,
                show_eta=False
            )
            pbar.update(0)
        except Exception:
            pass
    
    # 优化器加速版本 - Type2 使用 min_count=2, bytes_pp=1
    if OPTIMIZER_AVAILABLE:
        def _progress_adapter(cur: int, total: int):
            if progress:
                progress.update_to(cur)
            elif pbar:
                pbar.current = cur
                pbar.update(0)
        
        result = OptimizedLZSS.compress(
            data, min_count=2, bytes_pp=1, preset=preset,
            progress_cb=_progress_adapter if (progress or pbar) else None
        )
        
        if progress:
            progress.update_to(n)
        if pbar:
            pbar.close()
        
        return result

    min_count = 2
    max_back = 0xFFF
    max_len = min_count + 0xF  # 17

    # 预设（速度/压缩率取舍）
    if preset == "fast":
        k, bucket, lazy, candidates = 8, 32, 1, 96
    elif preset == "max":
        k, bucket, lazy, candidates = 4, 128, 2, 512
    else:  # normal
        k, bucket, lazy, candidates = 8, 64, 2, 256

    matcher = FastMatcher(window=max_back, k=k, max_bucket=bucket, max_candidates=candidates)
    matcher.bind(data)
    mv = memoryview(data)

    out = bytearray()
    pos = 0

    if progress:
        progress.update_to(0)

    # 辅助：向匹配器喂入一段范围内的位置
    def feed_range(a: int, b: int):
        # 喂入 [a, b)
        for p in range(a, b):
            matcher.feed(p)

    while pos < n:
        ctrl_pos = len(out)
        out.append(0)  # ctrl 占位
        ctrl = 0

        for bit in range(8):
            if pos >= n:
                break

            # 在喂入当前 pos 之前先找匹配（避免匹配到自身）
            best_dist, best_len = matcher.find(pos, max_len=max_len, min_len=min_count)

            # 惰性匹配：若向前看能获得更好的净收益，则先输出字面量
            # （max_len 很小，所以简单惰性策略足够）
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
                    # 输出字面量
                    ctrl |= (1 << bit)
                    out.append(mv[pos])
                    matcher.feed(pos)
                    pos += 1
                    if progress:
                        progress.update_to(pos)
                    continue

            if best_len >= min_count:
                # 限制 dist 和 len
                if best_dist <= 0 or best_dist > max_back:
                    # 回退为字面量（理论上不该发生）
                    ctrl |= (1 << bit)
                    out.append(mv[pos])
                    matcher.feed(pos)
                    pos += 1
                    if progress:
                        progress.update_to(pos)
                    continue

                if best_len > max_len:
                    best_len = max_len

                token = (best_dist << 4) | (best_len - min_count)
                out += struct.pack("<H", token & 0xFFFF)

                # 喂入当前和中间位置（提升后续匹配质量）
                start = pos
                end = pos + best_len
                # 喂入经过的位置；为避免过度喂入，可只喂到 end-k+1，但这里成本很低
                feed_range(start, min(end, n))
                pos = end
            else:
                # 字面量
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


# ------------------ G00 Type=2 结构 ------------------

@dataclass
class Tile:
    dst_x: int
    dst_y: int
    w: int
    h: int
    data_offset: int


@dataclass
class Frame:
    index: int
    x: int
    y: int
    offset: int
    size: int
    tiles: List[Tile]


class G00Type2:
    def __init__(self) -> None:
        self.header9: bytes = b""
        self.index_raw: bytes = b""
        self.width: int = 0
        self.height: int = 0
        self.count: int = 0
        self.entry_xy: List[Tuple[int, int]] = []
        self.decompressed: bytearray = bytearray()
        self.frames: List[Frame] = []

    @staticmethod
    def read(path: str, show_progress: bool = True,
             gui_callback: Optional[ProgressCallback] = None) -> "G00Type2":
        g = G00Type2()
        p = _nt_longpath(path)
        with open(p, "rb") as f:
            header9 = f.read(9)
            if len(header9) != 9:
                raise ValueError("文件过小")
            g.header9 = header9

            typ = header9[0]
            if typ != 2:
                raise ValueError("不是 type=2 的 g00 文件（byte0 != 2）")

            g.width = struct.unpack_from("<H", header9, 1)[0]
            g.height = struct.unpack_from("<H", header9, 3)[0]
            # ✅ 修复：使用 int32 读取 count 以兼容 rldev
            # rldev 使用 IO.read_i32 读取 region_count
            g.count = struct.unpack_from("<i", header9, 5)[0]
            if g.count < 1 or g.count > 0x1000:
                raise ValueError(f"无效的帧数: {g.count}")

            index_len = g.count * 0x18
            index_raw = f.read(index_len)
            if len(index_raw) != index_len:
                raise ValueError("索引表被截断")
            g.index_raw = index_raw

            g.entry_xy = []
            for i in range(g.count):
                base = i * 0x18
                x = struct.unpack_from("<i", index_raw, base + 0)[0]
                y = struct.unpack_from("<i", index_raw, base + 4)[0]
                g.entry_xy.append((x, y))

            hdr = f.read(8)
            if len(hdr) != 8:
                raise ValueError("缺少 LZ 头")
            block_size, out_size = struct.unpack("<II", hdr)
            packed_len = block_size - 8
            if packed_len < 0:
                raise ValueError("LZ 头中的 block_size 无效")

            packed = f.read(packed_len)
            if len(packed) != packed_len:
                raise ValueError("压缩数据被截断")

            pb = Progress(out_size, "Decompress", enabled=show_progress, gui_callback=gui_callback)
            dec = lz_decompress(packed, out_size, min_count=2, bytes_pp=1, progress=pb)
            pb.close()
            g.decompressed = bytearray(dec)

        g._parse_frames_and_tiles()
        return g

    def _parse_frames_and_tiles(self) -> None:
        buf = self.decompressed
        if len(buf) < 4:
            raise ValueError("解压缓冲区过小")

        count_in = struct.unpack_from("<i", buf, 0)[0]
        if count_in != self.count:
            raise ValueError("解压后的 frameCount 与头部不一致")

        table_off = 4
        frame_table: List[Tuple[int, int]] = []
        for i in range(self.count):
            off, sz = struct.unpack_from("<II", buf, table_off + i * 8)
            frame_table.append((int(off), int(sz)))

        frames: List[Frame] = []
        for i in range(self.count):
            x, y = self.entry_xy[i]
            off, sz = frame_table[i]
            tiles: List[Tile] = []
            if sz != 0:
                tiles = self._parse_tiles_for_frame(i, x, y, off, sz)
            frames.append(Frame(index=i, x=x, y=y, offset=off, size=sz, tiles=tiles))
        self.frames = frames

    def _parse_tiles_for_frame(self, idx: int, frame_x: int, frame_y: int, off: int, sz: int) -> List[Tile]:
        buf = self.decompressed
        if off < 0 or off >= len(buf):
            raise ValueError(f"Frame[{idx}] 偏移超出范围")
        if off + sz > len(buf):
            raise ValueError(f"Frame[{idx}] 大小超出范围")

        pos = off
        tile_type, tile_count = struct.unpack_from("<HH", buf, pos)
        pos += 4
        if tile_type != 1:
            raise ValueError(f"Frame[{idx}] tile_type != 1")

        pos += 0x70

        tiles: List[Tile] = []
        for t in range(tile_count):
            tile_x, tile_y, _unk, tile_w, tile_h = struct.unpack_from("<HHhHH", buf, pos)
            pos += 10
            pos += 0x52

            data_offset = pos
            pixel_len = tile_w * tile_h * 4
            pos += pixel_len

            dst_x = frame_x + tile_x
            dst_y = frame_y + tile_y

            if dst_x < 0 or dst_y < 0 or dst_x + tile_w > self.width or dst_y + tile_h > self.height:
                raise ValueError(f"Frame[{idx}] tile[{t}] 越界")

            tiles.append(Tile(dst_x=dst_x, dst_y=dst_y, w=tile_w, h=tile_h, data_offset=data_offset))

        if pos > off + sz:
            raise ValueError(f"Frame[{idx}] 解析超出 frame 大小（可能损坏？）")
        return tiles

    def write(self, out_path: str, packed_payload: bytes) -> None:
        p = _nt_longpath(out_path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "wb") as f:
            # ✅ 修复：重新构建 header9 确保 count 使用 int32 格式以兼容 rldev
            # 格式：type(1) + width(2) + height(2) + count(4) = 9 字节
            header9_fixed = struct.pack("<BHHI", 2, self.width, self.height, self.count)
            f.write(header9_fixed)
            f.write(self.index_raw)
            f.write(struct.pack("<II", len(packed_payload) + 8, len(self.decompressed)))
            f.write(packed_payload)

    def update_frame_xy(self, idx: int, x: int, y: int) -> None:
        if 0 <= idx < len(self.entry_xy):
            self.entry_xy[idx] = (x, y)
            
            if isinstance(self.index_raw, bytes):
                self.index_raw = bytearray(self.index_raw)
            
            base = idx * 0x18
            if len(self.index_raw) >= base + 8:
                struct.pack_into("<ii", self.index_raw, base, x, y)


# ------------------ JSON 辅助 ------------------

def load_manifest(json_path: str) -> Dict[str, Any]:
    p = _nt_longpath(json_path)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"JSON not found: {json_path}")

    with open(p, "rb") as f:
        head = f.read(64).lstrip()
    if not (head.startswith(b"{") or head.startswith(b"[")):
        raise ValueError(
            f"'{json_path}' 看起来不像 JSON。\n"
            "build 用法: build <input.g00> <basename>.json <png_dir> <output.g00>\n"
            "示例: g00 build ef_story_text00.g00 output\\ef_story_text00.json output new.g00 --mode auto"
        )

    with open(p, "r", encoding="utf-8-sig") as r:
        return json.load(r)


def save_manifest(path: str, obj: Dict[str, Any]) -> None:
    p = _nt_longpath(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as w:
        json.dump(obj, w, ensure_ascii=False, indent=2)


def norm_join(base: str, rel: str) -> str:
    rel2 = rel.replace("/", os.sep)
    return os.path.join(base, rel2)


# ------------------ 图像（NumPy）辅助 ------------------

def rgba_to_bgra(arr_rgba: np.ndarray) -> np.ndarray:
    # arr: (H,W,4) uint8
    return arr_rgba[..., [2, 1, 0, 3]]


def bgra_to_rgba(arr_bgra: np.ndarray) -> np.ndarray:
    return arr_bgra[..., [2, 1, 0, 3]]


def load_png_rgba(path: str) -> np.ndarray:
    p = _nt_longpath(path)
    img = Image.open(p).convert("RGBA")
    return np.array(img, dtype=np.uint8)


def save_png_rgba(path: str, rgba: np.ndarray) -> None:
    p = _nt_longpath(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    img = Image.fromarray(rgba, mode="RGBA")
    img.save(p)


def compute_bbox(tiles: List[Tile]) -> Optional[Tuple[int, int, int, int]]:
    if not tiles:
        return None
    minx = min(t.dst_x for t in tiles)
    miny = min(t.dst_y for t in tiles)
    maxx = max(t.dst_x + t.w for t in tiles)
    maxy = max(t.dst_y + t.h for t in tiles)
    return (minx, miny, maxx - minx, maxy - miny)


# ------------------ 提取模式 ------------------

def extract_cmd(input_g00: str, out_dir: str, mode: str, show_progress: bool = True,
                file_progress_cb: Optional[ProgressCallback] = None) -> None:
    os.makedirs(out_dir, exist_ok=True)
    
    # 获取基础文件名（不含扩展名）
    base_name = os.path.splitext(os.path.basename(input_g00))[0]

    g = G00Type2.read(input_g00, show_progress=show_progress, gui_callback=file_progress_cb)

    manifest: Dict[str, Any] = {
        "format": "RealLive.G00.Type2",
        "version": 1,
        "sourceFile": os.path.basename(input_g00),
        "baseName": base_name,  # 记录基础文件名供构建时使用
        "type": 2,
        "width": g.width,
        "height": g.height,
        "frameCount": g.count,
        "editMode": mode,
        "frames": []
    }

    # 为解压缓冲区建立一个 memoryview，方便对 tile 像素进行视图访问
    dec_mv = memoryview(g.decompressed)

    for fr in g.frames:
        # 使用基础文件名和编号生成PNG文件名
        png_basename = f"{base_name}#{fr.index:03d}"
        
        frame_item: Dict[str, Any] = {
            "index": fr.index,
            "x": fr.x,
            "y": fr.y,
            "offset": fr.offset,
            "size": fr.size,
            "png": f"{png_basename}.png",
            "tiles": [
                {
                    "dstX": t.dst_x,
                    "dstY": t.dst_y,
                    "w": t.w,
                    "h": t.h,
                    "dataOffset": t.data_offset
                } for t in fr.tiles
            ]
        }

        # full / bbox / both
        need_full = mode in ("full", "both")
        need_bbox = mode in ("bbox", "both")

        # 只有在需要 full 或 bbox 时才渲染整张画布
        if need_full or need_bbox:
            canvas_bgra = np.zeros((g.height, g.width, 4), dtype=np.uint8)
            for t in fr.tiles:
                pix = np.frombuffer(dec_mv[t.data_offset: t.data_offset + t.w * t.h * 4], dtype=np.uint8)
                pix = pix.reshape((t.h, t.w, 4))
                canvas_bgra[t.dst_y:t.dst_y + t.h, t.dst_x:t.dst_x + t.w, :] = pix

            if need_full:
                out_png_path = os.path.join(out_dir, frame_item["png"])
                save_png_rgba(out_png_path, bgra_to_rgba(canvas_bgra))

            if need_bbox:
                bbox_png = f"{png_basename}.bbox.png"
                bb = compute_bbox(fr.tiles)
                if bb is None:
                    save_png_rgba(os.path.join(out_dir, bbox_png), np.zeros((1, 1, 4), dtype=np.uint8))
                    frame_item["bbox"] = {"x": 0, "y": 0, "w": 0, "h": 0, "png": bbox_png}
                else:
                    bx, by, bw, bh = bb
                    crop_bgra = canvas_bgra[by:by + bh, bx:bx + bw, :]
                    save_png_rgba(os.path.join(out_dir, bbox_png), bgra_to_rgba(crop_bgra))
                    frame_item["bbox"] = {"x": bx, "y": by, "w": bw, "h": bh, "png": bbox_png}

        # tiles / both
        if mode in ("tiles", "both"):
            tiles_subdir = os.path.join(out_dir, "tiles", png_basename)
            os.makedirs(tiles_subdir, exist_ok=True)
            for ti, t in enumerate(fr.tiles):
                pix = np.frombuffer(dec_mv[t.data_offset: t.data_offset + t.w * t.h * 4], dtype=np.uint8)
                pix = pix.reshape((t.h, t.w, 4))
                tile_png = f"{ti:03d}.png"
                rel = f"tiles/{png_basename}/{tile_png}"
                save_png_rgba(os.path.join(out_dir, rel), bgra_to_rgba(pix))
                frame_item["tiles"][ti]["png"] = rel

        manifest["frames"].append(frame_item)

    # JSON文件名使用基础文件名
    json_path = os.path.join(out_dir, f"{base_name}.json")
    save_manifest(json_path, manifest)

    print(f"OK: extracted -> {out_dir}")
    print(f"  PNG frames: {g.count} (mode={mode})")
    print(f"  JSON: {json_path}")


# ------------------ 构建模式 ------------------

def choose_mode(manifest: Dict[str, Any], mode_arg: str) -> str:
    """
    auto：
      1) 优先使用 manifest['editMode']
      2) 从 schema 推断（存在 bbox => bbox；tile 带 png => tiles；否则 full）
    """
    if mode_arg != "auto":
        return mode_arg

    m = manifest.get("editMode")
    if m in ("full", "bbox", "tiles"):
        return m
    if m == "both":
        # 之后根据回退逻辑选最合适的方式
        return "full"

    frames = manifest.get("frames") or []
    for mf in frames:
        if "bbox" in mf:
            return "bbox"
        tiles = mf.get("tiles") or []
        if tiles and isinstance(tiles[0], dict) and "png" in tiles[0]:
            return "tiles"

    return "full"


def patch_from_full_png(g: G00Type2, mf: Dict[str, Any], png_dir: str) -> None:
    png_path = _nt_longpath(norm_join(png_dir, mf["png"]))
    if not os.path.isfile(png_path):
        raise FileNotFoundError(f"Full PNG not found: {png_path}")

    rgba = load_png_rgba(png_path)
    if rgba.shape[1] != g.width or rgba.shape[0] != g.height:
        raise ValueError(f"Full PNG 尺寸不匹配: {png_path} got {rgba.shape[1]}x{rgba.shape[0]}, expected {g.width}x{g.height}")
    bgra = rgba_to_bgra(rgba)

    dec = g.decompressed
    for t in mf.get("tiles", []):
        x, y, w, h, off = t["dstX"], t["dstY"], t["w"], t["h"], t["dataOffset"]
        tile_bgra = np.ascontiguousarray(bgra[y:y + h, x:x + w, :])
        b = tile_bgra.tobytes(order="C")
        dec[off:off + len(b)] = b


def patch_from_bbox_png(g: G00Type2, mf: Dict[str, Any], png_dir: str) -> None:
    bbox = mf.get("bbox")
    if not bbox:
        raise ValueError(f"Frame {mf['index']} 的 JSON 中没有 bbox 信息")

    bx, by, bw, bh = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
    bbox_path = _nt_longpath(norm_join(png_dir, bbox["png"]))
    if not os.path.isfile(bbox_path):
        raise FileNotFoundError(f"BBox PNG not found: {bbox_path}")

    if bw == 0 and bh == 0:
        return

    rgba = load_png_rgba(bbox_path)
    if rgba.shape[1] != bw or rgba.shape[0] != bh:
        raise ValueError(f"BBox PNG 尺寸不匹配: {bbox_path} got {rgba.shape[1]}x{rgba.shape[0]}, expected {bw}x{bh}")
    bgra = rgba_to_bgra(rgba)

    dec = g.decompressed
    for t in mf.get("tiles", []):
        x, y, w, h, off = t["dstX"], t["dstY"], t["w"], t["h"], t["dataOffset"]
        rx, ry = x - bx, y - by
        tile_bgra = np.ascontiguousarray(bgra[ry:ry + h, rx:rx + w, :])
        b = tile_bgra.tobytes(order="C")
        dec[off:off + len(b)] = b


def patch_from_tiles_png(g: G00Type2, mf: Dict[str, Any], png_dir: str) -> None:
    dec = g.decompressed
    tiles = mf.get("tiles", [])
    for t in tiles:
        rel = t.get("png")
        if not rel:
            raise ValueError(f"Frame={mf['index']} 的 JSON 中缺少 tile png 路径")
        tile_path = _nt_longpath(norm_join(png_dir, rel))
        if not os.path.isfile(tile_path):
            raise FileNotFoundError(f"Tile PNG not found: {tile_path}")

        rgba = load_png_rgba(tile_path)
        w, h = t["w"], t["h"]
        if rgba.shape[1] != w or rgba.shape[0] != h:
            raise ValueError(f"Tile PNG 尺寸不匹配: {tile_path} got {rgba.shape[1]}x{rgba.shape[0]}, expected {w}x{h}")
        bgra = rgba_to_bgra(rgba)
        b = np.ascontiguousarray(bgra).tobytes(order="C")

        off = t["dataOffset"]
        dec[off:off + len(b)] = b


def rebuild_structure(g: G00Type2, manifest: Dict[str, Any], png_dir: str, mode: str) -> None:
    print("正在重新构建 G00 结构 (Rebuild)...")
    if mode == "both":
        mode = "full"
    def load_full_canvas(mf: Dict[str, Any]) -> Optional[np.ndarray]:
        png_rel = mf.get("png")
        if not png_rel:
            return None
        png_path = _nt_longpath(norm_join(png_dir, png_rel))
        if not os.path.isfile(png_path):
            return None
        rgba = load_png_rgba(png_path)
        if rgba.shape[1] != g.width or rgba.shape[0] != g.height:
            return None
        return rgba

    def load_bbox_canvas(mf: Dict[str, Any]) -> Optional[np.ndarray]:
        bbox_info = mf.get("bbox")
        if not bbox_info:
            return None
        bx = bbox_info.get("x", 0)
        by = bbox_info.get("y", 0)
        bw = bbox_info.get("w", 0)
        bh = bbox_info.get("h", 0)
        if bw <= 0 or bh <= 0:
            return np.zeros((g.height, g.width, 4), dtype=np.uint8)
        png_rel = bbox_info.get("png")
        if not png_rel:
            return None
        png_path = _nt_longpath(norm_join(png_dir, png_rel))
        if not os.path.isfile(png_path):
            return None
        rgba = load_png_rgba(png_path)
        if rgba.shape[1] != bw or rgba.shape[0] != bh:
            raise ValueError(f"Frame {mf.get('index', '?')}: BBox PNG 尺寸不匹配: {png_path} got {rgba.shape[1]}x{rgba.shape[0]}, expected {bw}x{bh}")
        canvas = np.zeros((g.height, g.width, 4), dtype=np.uint8)
        canvas[by:by + bh, bx:bx + bw, :] = rgba
        return canvas

    def load_tiles_canvas(mf: Dict[str, Any], strict: bool) -> Optional[np.ndarray]:
        tiles = mf.get("tiles", [])
        if not tiles:
            return None
        canvas = np.zeros((g.height, g.width, 4), dtype=np.uint8)
        for t in tiles:
            rel = t.get("png")
            if not rel:
                if strict:
                    raise ValueError(f"Frame={mf.get('index', '?')} 的 JSON 中缺少 tile png 路径")
                return None
            tile_path = _nt_longpath(norm_join(png_dir, rel))
            if not os.path.isfile(tile_path):
                if strict:
                    raise FileNotFoundError(f"Tile PNG not found: {tile_path}")
                return None
            rgba = load_png_rgba(tile_path)
            w, h = t["w"], t["h"]
            if rgba.shape[1] != w or rgba.shape[0] != h:
                raise ValueError(f"Tile PNG 尺寸不匹配: {tile_path} got {rgba.shape[1]}x{rgba.shape[0]}, expected {w}x{h}")
            dst_x = t["dstX"]
            dst_y = t["dstY"]
            canvas[dst_y:dst_y + h, dst_x:dst_x + w, :] = rgba
        return canvas
    
    new_decompressed = bytearray()
    # 1. Frame count
    new_decompressed.extend(struct.pack("<i", g.count))
    
    # 2. Reserve space for frame table (offset, size) * count
    frame_table_offset = len(new_decompressed)
    frame_table_size = g.count * 8
    new_decompressed.extend(b'\x00' * frame_table_size)
    
    frame_entries = [] # (offset, size) relative to start of buffer
    
    frames = manifest.get("frames", [])
    
    # Pre-check frames length
    if len(frames) != g.count:
        raise ValueError(f"Manifest frames count ({len(frames)}) != G00 frames count ({g.count})")
    
    for i in range(g.count):
        mf = frames[i]
        
        # Prepare variables for frame construction
        crop_bgra = None
        frame_x = 0
        frame_y = 0
        base_x = int(mf.get("x", 0))
        base_y = int(mf.get("y", 0))
        
        if mode == "bbox":
            # --- BBox Mode Rebuild ---
            bbox_info = mf.get("bbox")
            if not bbox_info:
                 # Check if frame is empty (w=0 or h=0)
                 # Sometimes manifest just has empty bbox for empty frames?
                 # If no bbox info at all, we can try to fallback or raise error.
                 # Let's assume empty if not found? No, that's risky.
                 # If user chose bbox mode, they expect bbox info.
                 raise ValueError(f"Frame {i}: Missing bbox info in manifest for bbox rebuild")
            
            bx = bbox_info.get("x", 0)
            by = bbox_info.get("y", 0)
            bw = bbox_info.get("w", 0)
            bh = bbox_info.get("h", 0)
            
            if bw <= 0 or bh <= 0:
                # Empty frame
                frame_entries.append((0, 0))
                continue
                
            png_rel = bbox_info.get("png")
            if png_rel:
                png_path = _nt_longpath(norm_join(png_dir, png_rel))
                if os.path.isfile(png_path):
                    rgba = load_png_rgba(png_path)
                    h, w = rgba.shape[:2]
                    if bx + w > g.width:
                        pass
                    crop_bgra = rgba_to_bgra(rgba)
                    frame_x = bx
                    frame_y = by
            if crop_bgra is None:
                canvas = load_full_canvas(mf)
                if canvas is None:
                    canvas = load_tiles_canvas(mf, strict=False)
                if canvas is None:
                    canvas = load_bbox_canvas(mf)
                if canvas is None:
                    raise ValueError(f"Frame {i}: 缺少可用 PNG（full/bbox/tiles）")
                alpha = canvas[:, :, 3]
                rows = np.any(alpha > 0, axis=1)
                cols = np.any(alpha > 0, axis=0)
                if not np.any(rows):
                    frame_entries.append((0, 0))
                    continue
                min_y, max_y = np.where(rows)[0][[0, -1]]
                min_x, max_x = np.where(cols)[0][[0, -1]]
                h = int(max_y - min_y + 1)
                w = int(max_x - min_x + 1)
                crop_rgba = canvas[min_y:min_y+h, min_x:min_x+w]
                crop_bgra = rgba_to_bgra(crop_rgba)
                frame_x = min_x
                frame_y = min_y
            
        elif mode == "tiles":
            canvas = None
            if mf.get("tiles"):
                canvas = load_tiles_canvas(mf, strict=True)
            if canvas is None:
                canvas = load_full_canvas(mf)
            if canvas is None:
                canvas = load_bbox_canvas(mf)
            if canvas is None:
                raise ValueError(f"Frame {i}: 缺少可用 PNG（tiles/full/bbox）")
            alpha = canvas[:, :, 3]
            rows = np.any(alpha > 0, axis=1)
            cols = np.any(alpha > 0, axis=0)
            if not np.any(rows):
                frame_entries.append((0, 0))
                continue
            min_y, max_y = np.where(rows)[0][[0, -1]]
            min_x, max_x = np.where(cols)[0][[0, -1]]
            h = int(max_y - min_y + 1)
            w = int(max_x - min_x + 1)
            crop_rgba = canvas[min_y:min_y+h, min_x:min_x+w]
            crop_bgra = rgba_to_bgra(crop_rgba)
            frame_x = min_x
            frame_y = min_y
        else:
            # --- Full Mode Rebuild (default) ---
            # Currently only supporting rebuild from full PNGs as it's the most robust way
            # to handle arbitrary edits.
            canvas = load_full_canvas(mf)
            if canvas is None:
                canvas = load_bbox_canvas(mf)
            if canvas is None:
                canvas = load_tiles_canvas(mf, strict=False)
            if canvas is None:
                raise ValueError(f"Frame {i}: 缺少可用 PNG（full/bbox/tiles）")
            # Find bbox of non-transparent pixels
            alpha = canvas[:, :, 3]
            rows = np.any(alpha > 0, axis=1)
            cols = np.any(alpha > 0, axis=0)
            
            if not np.any(rows):
                # Empty frame
                frame_entries.append((0, 0))
                continue
            
            min_y, max_y = np.where(rows)[0][[0, -1]]
            min_x, max_x = np.where(cols)[0][[0, -1]]
            
            h = int(max_y - min_y + 1)
            w = int(max_x - min_x + 1)
            
            # Crop
            crop_rgba = canvas[min_y:min_y+h, min_x:min_x+w]
            crop_bgra = rgba_to_bgra(crop_rgba)
            frame_x = min_x
            frame_y = min_y

        # --- Create Tile and Append ---
        
        # Create tile data
        tile_header = bytearray()
        # G00_CUT_HEADER_STRUCT (116 bytes)
        # Offset 0: type(1) + pad(1) + count(2) -> pack "<HH" (1, 1) sets type=1, count=1
        tile_header.extend(struct.pack("<HH", 1, 1)) 
        
        # Offset 4: x(4), y(4), disp_xl(4), disp_yl(4), xc(4), yc(4), cut_xl(4), cut_yl(4)
        # x,y: Display offset.
        # User feedback indicates Game uses THIS for display position, ignoring Index Table.
        # So we must set it to frame_x, frame_y.
        tile_header.extend(struct.pack("<8i", int(frame_x), int(frame_y), w, h, 0, 0, g.width, g.height))
        
        # Offset 36: keep[20] (80 bytes)
        tile_header.extend(b'\x00' * 80)
        
        # Update global frame offset using manifest entry values
        g.update_frame_xy(i, base_x, base_y)
        
        h, w, _ = crop_bgra.shape
        
        # Tile definition
        rel_x = int(frame_x - base_x)
        rel_y = int(frame_y - base_y)
        tile_def = struct.pack("<HHhHH", rel_x, rel_y, 1, w, h)
        tile_header.extend(tile_def)
        tile_header.extend(b'\x00' * 0x52)
        
        pixel_data = crop_bgra.tobytes(order="C")
        
        frame_data = tile_header + pixel_data
        
        # Append
        offset_in_buf = len(new_decompressed)
        new_decompressed.extend(frame_data)
        size = len(frame_data)
        
        frame_entries.append((offset_in_buf, size))

    # Write frame table
    for i, (off, sz) in enumerate(frame_entries):
        struct.pack_into("<II", new_decompressed, frame_table_offset + i*8, off, sz)
        
    g.decompressed = new_decompressed
    # Re-parse to ensure consistency
    g._parse_frames_and_tiles()


def build_cmd(input_g00: str, json_path: str, png_dir: str, output_g00: str,
              mode_arg: str, preset: str, show_progress: bool = True,
              file_progress_cb: Optional[ProgressCallback] = None,
              rebuild: bool = False) -> None:
    manifest = load_manifest(json_path)
    if manifest.get("type") != 2:
        raise ValueError("Manifest type != 2")

    # 读取时不显示进度（节省时间，主要进度在压缩阶段）
    g = G00Type2.read(input_g00, show_progress=False, gui_callback=None)

    if manifest.get("width") != g.width or manifest.get("height") != g.height:
        raise ValueError("Manifest 与 g00 的宽/高不一致")
    if manifest.get("frameCount") != g.count:
        raise ValueError("Manifest 与 g00 的 frameCount 不一致")

    frames = manifest.get("frames", [])
    if len(frames) != g.count:
        raise ValueError("Manifest 的 frames 数量 != frameCount")

    mode = choose_mode(manifest, mode_arg)

    if rebuild:
        if mode not in ("full", "bbox", "tiles", "both"):
             print(f"Warning: Rebuild mode implementation for '{mode}' is experimental/incomplete.")
        rebuild_structure(g, manifest, png_dir, mode)
    else:
        def patch_one_frame(mf: Dict[str, Any]) -> None:
            idx = mf["index"]
            fr = g.frames[idx]
            if mf.get("x") != fr.x or mf.get("y") != fr.y:
                raise ValueError(f"Frame X/Y 不一致: index={idx}")

            # 如果强制指定模式，则直接使用该模式
            if mode == "full":
                patch_from_full_png(g, mf, png_dir)
                return
            if mode == "bbox":
                patch_from_bbox_png(g, mf, png_dir)
                return
            if mode == "tiles":
                patch_from_tiles_png(g, mf, png_dir)
                return

            raise ValueError(f"未知的 build 模式: {mode}")

        # 若 auto 选了 full 但找不到文件，则尽可能回退到 bbox/tiles
        for mf in frames:
            if mode == "full":
                try:
                    patch_from_full_png(g, mf, png_dir)
                except FileNotFoundError:
                    if mf.get("bbox"):
                        patch_from_bbox_png(g, mf, png_dir)
                    else:
                        tiles = mf.get("tiles", [])
                        if tiles and tiles[0].get("png"):
                            patch_from_tiles_png(g, mf, png_dir)
                        else:
                            raise
            elif mode == "bbox":
                patch_from_bbox_png(g, mf, png_dir)
            elif mode == "tiles":
                patch_from_tiles_png(g, mf, png_dir)
            else:
                patch_one_frame(mf)

    # 压缩
    pb = Progress(len(g.decompressed), f"Compress({preset})", enabled=show_progress, gui_callback=file_progress_cb)
    packed_payload = lz_compress_type2(bytes(g.decompressed), preset=preset, progress=pb)
    pb.close()

    g.write(output_g00, packed_payload)

    print(f"OK: built -> {output_g00}")
    print(f"  mode_arg={mode_arg} (effective={mode}), preset={preset}")
    print(f"  packed payload bytes: {len(packed_payload)}")
    print(f"  decompressed bytes: {len(g.decompressed)}")
    if not XXHASH_AVAILABLE:
        print("  NOTE: xxhash 未安装，压缩会慢一些。建议: pip install xxhash")
    if not NUMBA_AVAILABLE:
        print("  NOTE: numba 未安装，匹配会慢一些。建议: pip install numba")

# ------------------ PSD 导出功能 ------------------

def export_psd_cmd(json_path: str, png_dir: str, output_psd: str, 
                    use_bbox: bool = True, show_progress: bool = True) -> None:
    """
    根据 JSON 配置和 PNG 图片，生成带图层的 PSD 文件。
    
    参数:
        json_path: 提取生成的 JSON 文件路径
        png_dir: PNG 图片所在目录
        output_psd: 输出的 PSD 文件路径
        use_bbox: 是否使用 bbox 位置（True=使用 bbox.png 和 bbox 位置，False=使用 full png 位置 0,0）
        show_progress: 显示进度
    """
    if not PSD_TOOLS_AVAILABLE:
        print("错误: 需要安装 psd-tools 来创建 PSD 文件")
        print("安装命令: pip install psd-tools")
        sys.exit(1)
    
    # 加载 manifest
    manifest = load_manifest(json_path)
    if manifest.get("type") != 2:
        raise ValueError("Manifest type != 2")
    
    canvas_width = manifest["width"]
    canvas_height = manifest["height"]
    frames = manifest.get("frames", [])
    base_name = manifest.get("baseName", "g00")
    
    if show_progress:
        print(f"创建 PSD: {canvas_width}x{canvas_height}, {len(frames)} 图层")
    
    # 加载第一个 PNG 图片
    if len(frames) == 0:
        print("错误: 没有帧数据")
        sys.exit(1)
    
    mf = frames[0]
    idx = mf["index"]
    
    # 确定使用哪个 PNG 和位置
    if use_bbox and "bbox" in mf:
        bbox = mf["bbox"]
        png_rel = bbox.get("png", mf["png"])
        layer_x = bbox["x"]
        layer_y = bbox["y"]
        layer_w = bbox["w"]
        layer_h = bbox["h"]
    else:
        png_rel = mf["png"]
        layer_x = mf.get("x", 0)
        layer_y = mf.get("y", 0)
        layer_w = canvas_width
        layer_h = canvas_height
    
    # 加载 PNG 图片
    png_path = _nt_longpath(norm_join(png_dir, png_rel))
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
    layer_name = f"{base_name}#{idx:03d}"
    
    if show_progress:
        print(f"  图层 {idx}: {layer_name} @ ({layer_x}, {layer_y}) {img_w}x{img_h}")
    
    # 使用 psd-tools 创建 PSD 文件
    from PIL import Image
    from psd_tools import constants
    
    # 创建一个新的 PSD 文件
    psd = PSDImage.new(mode='RGBA', size=(canvas_width, canvas_height), color=(0, 0, 0, 0))
    
    # 创建图层（使用 RLE 压缩，并设置正确的位置）
    pil_image = Image.fromarray(rgba)
    layer = psd.create_pixel_layer(pil_image, name=layer_name, compression=constants.Compression.RLE, top=layer_y, left=layer_x)
    psd.append(layer)
    
    # 保存 PSD 文件
    output_path = _nt_longpath(output_psd)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    
    psd.save(output_path)
    
    print(f"OK: PSD 已保存 -> {output_path}")
    print(f"  图层数: 1")
    print(f"  画布大小: {canvas_width}x{canvas_height}")

# ------------------ PSD 导入功能 ------------------

def import_psd_cmd(psd_path: str, json_path: str, output_dir: str, 
                    update_json: bool = True, show_progress: bool = True) -> None:
    """
    从 PSD 文件提取图层，并根据图层位置/大小更新 bbox 和 tiles 数据。
    
    参数:
        psd_path: 输入的 PSD 文件路径
        json_path: 原始的 JSON 文件路径
        output_dir: 输出目录（PNG 和更新后的 JSON）
        update_json: 是否更新 JSON 中的 bbox/tiles 信息
        show_progress: 显示进度
    """
    if not PSD_TOOLS_AVAILABLE:
        print("错误: 需要安装 psd-tools 来读取 PSD 文件")
        print("安装命令: pip install psd-tools")
        sys.exit(1)
    
    # 加载原始 manifest
    manifest = load_manifest(json_path)
    if manifest.get("type") != 2:
        raise ValueError("Manifest type != 2")
    
    frames = manifest.get("frames", [])
    base_name = manifest.get("baseName", "g00")
    canvas_width = manifest["width"]
    canvas_height = manifest["height"]
    
    # 加载 PSD 文件
    psd = PSDImage.open(_nt_longpath(psd_path))
    
    if show_progress:
        print(f"读取 PSD: {psd.width}x{psd.height}, {len(psd)} 图层")
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 构建图层名称到索引的映射
    name_to_index = {}
    for mf in frames:
        idx = mf["index"]
        # 支持多种命名格式
        name_to_index[f"{base_name}#{idx:03d}"] = idx
        name_to_index[f"#{idx:03d}"] = idx
        name_to_index[f"{idx:03d}"] = idx
        name_to_index[str(idx)] = idx
    
    # 处理每个图层
    updated_count = 0
    
    for layer in psd:
        # 跳过组图层（只处理像素图层）
        if hasattr(layer, 'is_group') and layer.is_group():
            continue
        
        layer_name = layer.name
        
        # 尝试匹配图层名称到 frame 索引
        matched_idx = None
        if layer_name in name_to_index:
            matched_idx = name_to_index[layer_name]
        else:
            # 尝试从名称中提取数字
            import re
            nums = re.findall(r'\d+', layer_name)
            if nums:
                try:
                    potential_idx = int(nums[-1])
                    if 0 <= potential_idx < len(frames):
                        matched_idx = potential_idx
                except ValueError:
                    pass
        
        if matched_idx is None:
            if show_progress:
                print(f"  跳过无法匹配的图层: {layer_name}")
            continue
        
        # 获取图层信息（新的 bbox）
        new_bbox_x = layer.left
        new_bbox_y = layer.top
        new_bbox_w = layer.width
        new_bbox_h = layer.height
        
        if new_bbox_w == 0 or new_bbox_h == 0:
            if show_progress:
                print(f"  跳过空图层: {layer_name}")
            continue
        
        # 将图层导出为 PNG
        layer_image = layer.composite()
        if layer_image is None:
            if show_progress:
                print(f"  无法合成图层: {layer_name}")
            continue
        
        # 转换为 RGBA
        if layer_image.mode != 'RGBA':
            layer_image = layer_image.convert('RGBA')
        
        # 保存 bbox PNG
        bbox_png_name = f"{base_name}#{matched_idx:03d}.bbox.png"
        bbox_png_path = os.path.join(output_dir, bbox_png_name)
        layer_image.save(_nt_longpath(bbox_png_path))
        
        # 更新 manifest 中的 bbox 和 tiles 信息
        if update_json:
            mf = frames[matched_idx]
            
            # 获取原始 bbox
            old_bbox = mf.get("bbox", {})
            old_bbox_x = old_bbox.get("x", 0)
            old_bbox_y = old_bbox.get("y", 0)
            
            # 计算位置偏移量
            delta_x = new_bbox_x - old_bbox_x
            delta_y = new_bbox_y - old_bbox_y
            
            # 更新 bbox
            if "bbox" not in mf:
                mf["bbox"] = {}
            
            mf["bbox"]["x"] = new_bbox_x
            mf["bbox"]["y"] = new_bbox_y
            mf["bbox"]["w"] = new_bbox_w
            mf["bbox"]["h"] = new_bbox_h
            mf["bbox"]["png"] = bbox_png_name
            
            # 如果位置变化了，同步更新所有 tiles 的位置
            if delta_x != 0 or delta_y != 0:
                tiles = mf.get("tiles", [])
                for tile in tiles:
                    tile["dstX"] = tile["dstX"] + delta_x
                    tile["dstY"] = tile["dstY"] + delta_y
                
                if show_progress:
                    print(f"  图层 {matched_idx}: {layer_name}")
                    print(f"    新 bbox: ({new_bbox_x}, {new_bbox_y}) {new_bbox_w}x{new_bbox_h}")
                    print(f"    原 bbox: ({old_bbox_x}, {old_bbox_y})")
                    print(f"    位置偏移: dx={delta_x}, dy={delta_y}")
                    print(f"    已更新 {len(tiles)} 个 tiles 的位置")
            else:
                if show_progress:
                    print(f"  图层 {matched_idx}: {layer_name}")
                    print(f"    位置: ({new_bbox_x}, {new_bbox_y}) 大小: {new_bbox_w}x{new_bbox_h}")
        
        updated_count += 1
    
    # 保存更新后的 JSON
    if update_json:
        # 更新 editMode 为 bbox
        manifest["editMode"] = "bbox"
        
        output_json = os.path.join(output_dir, os.path.basename(json_path))
        save_manifest(output_json, manifest)
        
        print(f"OK: 已更新 JSON -> {output_json}")
    
    print(f"OK: 已处理 {updated_count} 个图层")
    print(f"  输出目录: {output_dir}")


# ------------------ G00 导出为 G00Pack 官方兼容 PSD ------------------

def _sanitize_layer_name(name: str) -> str:
    """
    将图层名转换为 G00Pack 兼容的格式。
    G00Pack 规则：只允许 a-z, A-Z, 0-9, _，且不能以数字开头。
    """
    import re
    # 替换非法字符为下划线
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    # 如果以数字开头，添加前缀
    if sanitized and sanitized[0].isdigit():
        sanitized = 'f_' + sanitized
    # 如果为空，使用默认名
    if not sanitized:
        sanitized = 'unnamed'
    return sanitized


def export_g00pack_psd_cmd(input_g00: str, output_psd: str, 
                           show_progress: bool = True,
                           gui_callback: Optional[ProgressCallback] = None) -> None:
    """
    将 G00 文件导出为 G00Pack 官方兼容的 PSD 文件（默认格式）。
    
    生成的 PSD 可以直接被 G00PackMax.exe 读取并重新打包为 G00。
    
    特性：
    - 添加 #CUT 透明空图层（让 G00Pack 识别为切割转换模式）
    - 图层名使用 G00Pack 兼容的命名（a-z, A-Z, 0-9, _）
    - 图层按正确顺序排列（#CUT 在最上面，然后是帧 0, 1, 2...）
    - 所有图层不锁定
    
    参数:
        input_g00: 输入的 G00 文件路径
        output_psd: 输出的 PSD 文件路径
        show_progress: 显示进度
    """
    if not PSD_TOOLS_AVAILABLE:
        print("错误: 需要安装 psd-tools 来创建 PSD 文件")
        print("安装命令: pip install psd-tools")
        sys.exit(1)
    
    # 读取 G00 文件
    g = G00Type2.read(input_g00, show_progress=show_progress, gui_callback=gui_callback)
    
    base_name = os.path.splitext(os.path.basename(input_g00))[0]
    base_name_safe = _sanitize_layer_name(base_name)
    canvas_width = g.width
    canvas_height = g.height
    
    if show_progress:
        print(f"创建 G00Pack 官方兼容 PSD: {canvas_width}x{canvas_height}, {g.count} 帧")
    
    # 为解压缓冲区建立 memoryview
    dec_mv = memoryview(g.decompressed)
    
    # 收集所有图层记录
    layer_records = []
    
    # 先收集所有有效帧的图层（不含命令图层）
    frame_layers = []
    for fr in g.frames:
        idx = fr.index
        
        # 计算 bbox
        bb = compute_bbox(fr.tiles)
        if bb is None:
            if show_progress:
                print(f"  跳过空帧 {idx}")
            continue
        
        bx, by, bw, bh = bb
        
        if bw == 0 or bh == 0:
            if show_progress:
                print(f"  跳过空帧 {idx}")
            continue
        
        # 渲染帧到 bbox 区域
        canvas_bgra = np.zeros((canvas_height, canvas_width, 4), dtype=np.uint8)
        for t in fr.tiles:
            pix = np.frombuffer(dec_mv[t.data_offset: t.data_offset + t.w * t.h * 4], dtype=np.uint8)
            pix = pix.reshape((t.h, t.w, 4))
            canvas_bgra[t.dst_y:t.dst_y + t.h, t.dst_x:t.dst_x + t.w, :] = pix
        
        # 裁剪到 bbox 区域
        crop_bgra = canvas_bgra[by:by + bh, bx:bx + bw, :]
        rgba = bgra_to_rgba(crop_bgra)
        
        # 创建 G00Pack 兼容的图层名称
        # 格式：{base_name}_{index:03d}
        layer_name = f"{base_name_safe}_{idx:03d}"
        
        frame_layers.append({"idx": idx, "name": layer_name, "rgba": rgba, "bx": bx, "by": by})
        
        if show_progress:
            print(f"  帧 {idx}: {layer_name} @ ({bx}, {by}) {bw}x{bh}")
    
    # 使用 psd-tools 创建 PSD 文件
    # 注意：psd-tools 会自动应用 RLE 压缩并添加必要的元数据
    # 测试显示这会使文件变小（3.01 MB vs 12.22 MB）
    from PIL import Image
    from psd_tools import constants
    
    if show_progress:
        print(f"  使用 psd-tools 创建 PSD...")
    
    # 创建一个新的 PSD 文件
    psd = PSDImage.new(mode='RGBA', size=(canvas_width, canvas_height), color=(0, 0, 0, 0))
    
    # 生成图层顺序（顶部到下方），插入 #INDEX 命令以跳过空帧
    # 顶部顺序示例：
    # [#CUT, frame_000, #INDEX2, frame_002, #INDEX5, frame_005, #INDEX10]
    ordered_top = []
    # 顶部的 #CUT 命令层
    ordered_top.append({"type": "command", "name": "#CUT"})
    # 按索引排序有效帧
    frame_layers.sort(key=lambda x: x["idx"])
    expected_next = 0
    for rec in frame_layers:
        idx = rec["idx"]
        if idx > expected_next:
            ordered_top.append({"type": "command", "name": f"#INDEX{idx}"})
            if show_progress:
                print(f"  插入命令: #INDEX{idx}（跳过 {expected_next}..{idx-1}）")
        ordered_top.append({"type": "frame", **rec})
        expected_next = idx + 1
    # 若末尾仍有空帧，插入终止的 #INDEX{g.count}
    if expected_next < g.count:
        ordered_top.append({"type": "command", "name": f"#INDEX{g.count}"})
        if show_progress:
            print(f"  插入命令: #INDEX{g.count}（结束，{expected_next}..{g.count-1} 为空帧）")
    
    # 反转为自底向上的添加顺序，使最后添加的位于顶部
    for item in reversed(ordered_top):
        if item["type"] == "frame":
            pil_image = Image.fromarray(item["rgba"])
            layer = psd.create_pixel_layer(
                pil_image,
                name=item["name"],
                compression=constants.Compression.RLE,
                top=item["by"],
                left=item["bx"],
            )
            psd.append(layer)
        else:
            # 命令层（#CUT / #INDEXN）：透明空图层
            cmd_img = np.zeros((1, 1, 4), dtype=np.uint8)
            cmd_pil = Image.fromarray(cmd_img)
            cmd_layer = psd.create_pixel_layer(cmd_pil, name=item["name"], compression=constants.Compression.RLE)
            psd.append(cmd_layer)
    
    # 保存 PSD 文件
    output_path = _nt_longpath(output_psd)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    
    psd.save(output_path)
    
    if show_progress:
        print(f"  PSD 保存完成")
    
    print(f"OK: G00Pack 官方兼容 PSD 已保存 -> {output_psd}")
    print(f"  图层数: {len(psd)} (含 #CUT/#INDEX 命令层)")
    print(f"  画布大小: {canvas_width}x{canvas_height}")

# ------------------ 命令行（CLI） ------------------
def main():
    ap = argparse.ArgumentParser(description="Type=2 G00 提取/构建工具（PNG + JSON + PSD）- 快速版")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_ex = sub.add_parser("extract", help="提取 g00 -> PNGs + {basename}.json")
    ap_ex.add_argument("input_g00")
    ap_ex.add_argument("out_dir")
    ap_ex.add_argument(
        "--mode",
        choices=["full", "bbox", "tiles", "both"],
        default="full",
        help="full=整张画布 png，bbox=覆盖 tile 的最小矩形，tiles=每个 tile 单独 png，both=full+bbox+tiles"
    )
    ap_ex.add_argument("--no-progress", action="store_true", default=False, help="禁用进度显示")

    ap_b = sub.add_parser("build", help="从 原始 g00 + {basename}.json + 已编辑 png 构建新的 g00")
    ap_b.add_argument("input_g00")
    ap_b.add_argument("json_path")
    ap_b.add_argument("png_dir")
    ap_b.add_argument("output_g00")
    ap_b.add_argument(
        "--mode",
        choices=["auto", "full", "bbox", "tiles"],
        default="auto",
        help="auto=使用 manifest/推断；full/bbox/tiles=强制指定"
    )
    ap_b.add_argument(
        "--preset",
        choices=["fast", "normal", "max", "promax"],
        default="normal",
        help="压缩预设（速度 vs 压缩率，promax=暴力搜索）"
    )
    ap_b.add_argument("--no-progress", action="store_true", default=False, help="禁用进度显示")
    ap_b.add_argument("--rebuild", action="store_true", default=False, help="完全重建 G00 结构（解决 tile 复用导致显示不全的问题）")

    # PSD 导出命令
    ap_psd_ex = sub.add_parser("export-psd", help="从 JSON + PNG 生成带图层的 PSD 文件")
    ap_psd_ex.add_argument("json_path", help="提取生成的 JSON 文件路径")
    ap_psd_ex.add_argument("png_dir", help="PNG 图片所在目录")
    ap_psd_ex.add_argument("output_psd", help="输出的 PSD 文件路径")
    ap_psd_ex.add_argument(
        "--use-full",
        action="store_true",
        default=False,
        help="使用 full PNG 而不是 bbox PNG（图层位置将为 0,0）"
    )
    ap_psd_ex.add_argument("--no-progress", action="store_true", default=False, help="禁用进度显示")

    # PSD 导入命令
    ap_psd_im = sub.add_parser("import-psd", help="从 PSD 提取图层并更新 bbox/tiles 数据")
    ap_psd_im.add_argument("psd_path", help="输入的 PSD 文件路径")
    ap_psd_im.add_argument("json_path", help="原始的 JSON 文件路径")
    ap_psd_im.add_argument("output_dir", help="输出目录（PNG 和更新后的 JSON）")
    ap_psd_im.add_argument(
        "--no-update-json",
        action="store_true",
        default=False,
        help="不更新 JSON 中的 bbox/tiles 信息，只导出 PNG"
    )
    ap_psd_im.add_argument("--no-progress", action="store_true", default=False, help="禁用进度显示")

    # G00 导出为 G00Pack 官方兼容 PSD 命令
    ap_g00pack = sub.add_parser("to-psd", help="将 G00 导出为 G00Pack 官方兼容的 PSD 文件")
    ap_g00pack.add_argument("input_g00", help="输入的 G00 文件路径")
    ap_g00pack.add_argument("output_psd", help="输出的 PSD 文件路径")
    ap_g00pack.add_argument("--no-progress", action="store_true", default=False, help="禁用进度显示")

    args = ap.parse_args()

    if args.cmd == "extract":
        extract_cmd(args.input_g00, args.out_dir, args.mode, show_progress=(not args.no_progress))
    elif args.cmd == "build":
        build_cmd(args.input_g00, args.json_path, args.png_dir, args.output_g00,
                  mode_arg=args.mode, preset=args.preset, show_progress=(not args.no_progress),
                  rebuild=args.rebuild)
    elif args.cmd == "export-psd":
        export_psd_cmd(args.json_path, args.png_dir, args.output_psd,
                       use_bbox=(not args.use_full), show_progress=(not args.no_progress))
    elif args.cmd == "import-psd":
        import_psd_cmd(args.psd_path, args.json_path, args.output_dir,
                       update_json=(not args.no_update_json), show_progress=(not args.no_progress))
    elif args.cmd == "to-psd":
        export_g00pack_psd_cmd(args.input_g00, args.output_psd, 
                               show_progress=(not args.no_progress))
    else:
        ap.error("未知命令")


if __name__ == "__main__":
    main()
