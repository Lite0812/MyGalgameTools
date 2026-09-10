#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
G00 Type=0 编解码器 - 完全重写版本（严格参考 rldev-master/src/vaconv）

Type=0（BGR24 格式）规格：
- 文件头 5 字节：type(1) + width(2) + height(2)
- 从 offset=5 开始是 LZSS 压缩块：
    uint32 compressed_size（含此8字节header）
    uint32 uncompressed_size（必须等于 width*height*4，rldev 校验要求）
    packed bytes (compressed_size - 8)

LZSS 压缩参数（Type 0专用）：
  min_count = 1（最小匹配长度：1像素）
  bytes_pp = 3（每像素3字节：BGR）
  window_size = 4095（像素单位）
  max_match_len = 16（像素单位）

压缩Token格式（16位小端）：
  ctrl bit = 0: token = (offset_pixels << 4) | (length_pixels - 1)
  ctrl bit = 1: 字面量（3字节BGR）

压缩预设：
  - fast: 快速模式（哈希表，牺牲压缩率）
  - normal: 标准模式（平衡速度与压缩率）
  - max: 高质量模式（增强哈希+惰性匹配）
  - promax: 极致压缩（暴力搜索，完全匹配 rldev brutal 模式）

用法：
  python g00_type0.py extract input.g00 out_dir
  python g00_type0.py build input.g00 out_dir/<name>.json out_dir output.g00 --preset promax

依赖：
  必需：pip install pillow numpy
  可选（强烈推荐）：pip install numba xxhash tqdm

参考实现：
  - rldev-master/src/vaconv/g00-bt.cpp (暴力搜索压缩)
  - rldev-master/src/vaconv/g00.ml (解码与文件格式)
"""

import argparse
import json
import os
import struct
import sys
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable

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

# ------------------ 必需依赖 ------------------
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

# ------------------ 可选优化依赖 ------------------
try:
    from numba import njit
    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False

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

# ------------------ 从同模块导入图像辅助函数 ------------------
from g00_type1 import load_png_rgba


# ------------------ 回调类型定义 ------------------
ProgressCallback = Callable[[int, int], None]


# ------------------ 路径辅助（Windows 长路径） ------------------
def _nt_longpath(p: str) -> str:
    p = os.path.abspath(p)
    if os.name == "nt":
        if not p.startswith("\\\\?\\"):
            p = "\\\\?\\" + p
    return p


# ------------------ 进度显示（tqdm 优先 + 回退百分比） ------------------
class Progress:
    """
    如果可用并且 stdout 是 tty，则使用 tqdm；
    否则偶尔输出简单百分比（每 5% 或 100%）。
    total / cur 都用"字节数"表示。
    优化：减少更新频率，降低GUI卡顿。
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
        self._last_gui_update = 0  # 新增：GUI更新节流

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
        if not self.enabled:
            return
        cur = int(max(0, min(self.total, cur)))

        # GUI 回调模式 - 优化：每次都调用，由GUI侧决定是否更新界面
        if self.gui_callback:
            self.gui_callback(cur, self.total)
            self._cur = cur
            return

        # tqdm 模式
        if self._tqdm is not None:
            delta = cur - self._cur
            if delta:
                self._tqdm.update(delta)
            self._cur = cur
            return

        # 文本回退 - 优化：每5%更新一次
        if not sys.stdout.isatty():
            self._cur = cur
            return

        percent = int(cur * 100 / self.total)
        if percent != self._last_print and (percent % 5 == 0 or percent == 100):
            self._last_print = percent
            sys.stdout.write(f"\r{self.desc}: {percent:3d}% ({cur}/{self.total})")
            sys.stdout.flush()
            if percent == 100:
                sys.stdout.write("\n")
                sys.stdout.flush()
        self._cur = cur

    def close(self):
        if self.gui_callback:
            self.gui_callback(self.total, self.total)
        if self._tqdm is not None:
            self._tqdm.close()
            self._tqdm = None


# ==================== LZSS 解压（严格匹配 rldev va_decompress_g00_0） ====================

def lz_decompress_type0(packed: bytes, output_size: int, progress: Optional[Progress] = None) -> bytes:
    """
    Type 0 LZSS 解压 - 严格按 rldev g00-bt.cpp:va_decompress_g00_0 实现
    
    参数：
        packed: 压缩数据
        output_size: 输出大小（必须等于 width*height*3）
        progress: 进度条对象
    
    编码规则：
      ctrl bit = 1: 字面量 - 复制3字节BGR
      ctrl bit = 0: 回溯引用 token(uint16 LE)
          count = *src++;           // 低8位
          count += (*src++) << 8;   // 高8位
          rp = buf - (count >> 4) * 3;        // 回溯指针（像素单位）
          count = ((count & 0x0f) + 1) * 3;   // 长度（字节）
    
    注意：
      - 以像素（3字节）为单位计算offset
      - min_count=1（1像素=3字节）
      - 支持重叠拷贝
    """
    out = bytearray(output_size)
    dst = 0
    bits = 2  # 初始状态：强制下一次读取ctrl字节
    src = 0
    packed_len = len(packed)
    
    if progress:
        progress.update_to(0)
    
    # 主解压循环
    while dst < output_size and src < packed_len:
        # 每次使用一个 bit
        bits >>= 1
        
        # 当 bits 变成1时，需要读取新的 ctrl 字节
        if bits == 1:
            if src >= packed_len:
                break
            bits = packed[src] | 0x100  # 设置标志位，可走8次
            src += 1
        
        if bits & 1:
            # bit=1: 字面量 - 复制3字节
            if src + 3 > packed_len:
                break
            out[dst] = packed[src]
            out[dst + 1] = packed[src + 1]
            out[dst + 2] = packed[src + 2]
            src += 3
            dst += 3
        else:
            # bit=0: 回溯引用
            if src + 2 > packed_len:
                break
            
            # 读取 token（16位小端）
            token = packed[src] | (packed[src + 1] << 8)
            src += 2
            
            # 解析 token
            offset_pixels = token >> 4           # 高12位：回溯距离（像素单位）
            length_pixels = (token & 0x0F) + 1   # 低4位+1：匹配长度（像素单位）
            
            # 转换为字节单位
            back_bytes = offset_pixels * 3
            copy_bytes = length_pixels * 3
            
            if back_bytes <= 0 or dst - back_bytes < 0:
                raise ValueError(f"无效的 LZ 回溯距离: offset_pixels={offset_pixels}, dst={dst}")
            
            # 重叠拷贝（逐字节，允许 src/dst 重叠）
            for i in range(copy_bytes):
                if dst + i >= output_size:
                    break
                out[dst + i] = out[dst - back_bytes + i]
            dst += copy_bytes
        
        if progress and dst % 4096 == 0:  # 每4KB更新一次进度（优化：从8KB改为4KB，增加更新频率）
            progress.update_to(dst)
    
    if progress:
        progress.update_to(output_size)
    
    return bytes(out)


# ==================== LZSS 压缩（ProMax 暴力搜索，匹配 rldev ek_LZSSCompressType0） ====================

if NUMBA_AVAILABLE:
    @njit(cache=True)
    def _brute_force_search_type0_numba(
        data: np.ndarray,
        pos: int,
        max_back_pixels: int,
        max_len_pixels: int
    ):
        """
        Type 0 暴力搜索最佳匹配 - Numba 加速版本
        
        完全匹配 rldev g00-bt.cpp:ek_findbestmatchType0
        
        Args:
            data: BGR24 数据数组
            pos: 当前位置（字节）
            max_back_pixels: 最大回溯像素数 (4095)
            max_len_pixels: 最大匹配长度（像素） (16)
        
        Returns:
            (best_offset_pixels, best_length_pixels)
        """
        best_length = 0
        best_offset = 0
        data_len = len(data)
        
        # i: 回溯像素数
        i = 1
        while i <= max_back_pixels and i * 3 <= pos and best_length < max_len_pixels:
            # 候选位置（字节）
            cand_pos = pos - i * 3
            if cand_pos < 0:
                break
            
            # 优化：只在候选位置的 bestlength 位置匹配时才深入检查
            if best_length > 0:
                match_start = pos + best_length * 3
                cand_start = cand_pos + best_length * 3
                if (match_start + 2 < data_len and 
                    (data[match_start] != data[cand_start] or
                     data[match_start + 1] != data[cand_start + 1] or
                     data[match_start + 2] != data[cand_start + 2])):
                    i += 1
                    continue
            
            # 计算匹配长度
            j = 0
            while j < max_len_pixels:
                cur_byte = pos + j * 3
                cand_byte = cand_pos + j * 3
                
                # 检查边界
                if cur_byte + 2 >= data_len:
                    break
                
                # 比较3字节（一个像素）
                if (data[cur_byte] != data[cand_byte] or
                    data[cur_byte + 1] != data[cand_byte + 1] or
                    data[cur_byte + 2] != data[cand_byte + 2]):
                    break
                
                j += 1
                if j > best_length:
                    best_length = j
                    best_offset = i
            
            i += 1
        
        return best_offset, best_length
    
    @njit(cache=True)
    def _lzss_compress_promax_type0_numba(data: np.ndarray, progress_arr: np.ndarray):
        """
        Type 0 ProMax 暴力压缩 - Numba 加速版本
        
        完全匹配 rldev g00-bt.cpp:ek_LZSSCompressType0
        
        注意：Numba的@njit函数无法中断，进度只能在完成时更新
        """
        n = len(data)
        max_back_pixels = 4095
        max_len_pixels = 16
        
        # 预分配输出缓冲区（最坏情况）
        out = np.empty(n * 8 // 7 + 16, dtype=np.uint8)
        out_pos = 0
        pos = 0
        
        while pos < n:
            # 控制字节
            ctrl_pos = out_pos
            out[out_pos] = 0
            out_pos += 1
            ctrl = 0
            bitcount = 0
            
            while bitcount < 8 and pos < n:
                # 暴力搜索最佳匹配（以像素为单位）
                best_offset, best_length = _brute_force_search_type0_numba(
                    data, pos, max_back_pixels, max_len_pixels
                )
                
                if best_length >= 1:  # Type 0: 1像素以上就使用引用
                    # 输出 token
                    token = (best_offset << 4) | (best_length - 1)
                    out[out_pos] = token & 0xFF
                    out[out_pos + 1] = (token >> 8) & 0xFF
                    out_pos += 2
                    pos += best_length * 3
                else:
                    # 输出字面量（3字节）
                    ctrl |= (1 << bitcount)
                    if pos + 2 < n:
                        out[out_pos] = data[pos]
                        out[out_pos + 1] = data[pos + 1]
                        out[out_pos + 2] = data[pos + 2]
                    out_pos += 3
                    pos += 3
                
                bitcount += 1
            
            out[ctrl_pos] = ctrl
        
        # 最终进度
        progress_arr[0] = n
        
        return out[:out_pos]


def lz_compress_promax_type0(data: bytes, progress: Optional[Progress] = None) -> bytes:
    """
    Type 0 ProMax 暴力搜索压缩 - Python 接口
    
    匹配 rldev brutal 模式：ek_LZSSCompressType0
    压缩质量最优，但速度最慢（比 max 慢 10-30倍）
    
    注意：ProMax模式使用暴力搜索，Numba的@njit函数无法中断，
    因此进度只能在压缩完成后更新。处理大文件时请耐心等待。
    """
    n = len(data)
    if n == 0:
        return b""
    
    if n % 3 != 0:
        raise ValueError("数据长度必须是3的整数倍（BGR24格式）")
    
    # 命令行进度条
    pbar = None
    if PROGRESS_UTILS_AVAILABLE and progress is None:
        try:
            pbar = ConsoleProgressBar(
                total=n,
                desc="Type0 ProMax压缩",
                width=50,
                show_speed=True,
                show_eta=False
            )
            pbar.update(0)  # 显示开始
        except Exception:
            pass
    
    if progress:
        progress.update_to(0)
    
    # 优化：使用OptimizedLZSS（如果可用）
    try:
        from g00_optimizer import OPTIMIZER_AVAILABLE, OptimizedLZSS
        if OPTIMIZER_AVAILABLE:
            def _progress_adapter(cur: int, total: int):
                if progress:
                    progress.update_to(cur)
                elif pbar:
                    pbar.current = cur
                    pbar.update(0)
            
            result = OptimizedLZSS.compress(
                data, min_count=1, bytes_pp=3, preset="promax",
                progress_cb=_progress_adapter if (progress or pbar) else None
            )
            
            if pbar:
                pbar.close()
            
            return result
    except ImportError:
        pass
    
    # 回退：使用内置Numba版本或Python版本
    if NUMBA_AVAILABLE:
        # 使用 Numba 加速版本
        data_arr = np.frombuffer(data, dtype=np.uint8)
        
        # 创建进度数组（但ProMax无法中断，仅在完成时更新）
        progress_arr = np.array([0], dtype=np.int64)
        result = _lzss_compress_promax_type0_numba(data_arr, progress_arr)
        
        if progress:
            progress.update_to(n)
        if pbar:
            pbar.current = n
            pbar.update(0)
            pbar.close()
        
        return bytes(result)
    else:
        # Python 回退版本（慢）
        result = _lz_compress_promax_type0_python(data, progress)
        
        if pbar:
            pbar.close()
        
        return result


def _lz_compress_promax_type0_python(data: bytes, progress: Optional[Progress] = None) -> bytes:
    """
    Type 0 ProMax 暴力搜索压缩 - 纯 Python 实现（回退）
    """
    n = len(data)
    max_back_pixels = 4095
    max_len_pixels = 16
    
    out = bytearray()
    pos = 0
    
    while pos < n:
        ctrl_pos = len(out)
        out.append(0)
        ctrl = 0
        bitcount = 0
        
        while bitcount < 8 and pos < n:
            # 暴力搜索最佳匹配
            best_offset = 0
            best_length = 0
            
            for i in range(1, min(max_back_pixels + 1, pos // 3 + 1)):
                if best_length >= max_len_pixels:
                    break
                
                cand_pos = pos - i * 3
                if cand_pos < 0:
                    break
                
                # 优化：只检查可能更长的匹配
                if best_length > 0:
                    match_start = pos + best_length * 3
                    cand_start = cand_pos + best_length * 3
                    if match_start + 2 < n:
                        if (data[match_start] != data[cand_start] or
                            data[match_start + 1] != data[cand_start + 1] or
                            data[match_start + 2] != data[cand_start + 2]):
                            continue
                
                # 计算匹配长度
                j = 0
                while j < max_len_pixels and pos + j * 3 + 2 < n:
                    cur_byte = pos + j * 3
                    cand_byte = cand_pos + j * 3
                    if (data[cur_byte] != data[cand_byte] or
                        data[cur_byte + 1] != data[cand_byte + 1] or
                        data[cur_byte + 2] != data[cand_byte + 2]):
                        break
                    j += 1
                    if j > best_length:
                        best_length = j
                        best_offset = i
            
            if best_length >= 1:
                # 输出 token
                token = (best_offset << 4) | (best_length - 1)
                out.append(token & 0xFF)
                out.append((token >> 8) & 0xFF)
                pos += best_length * 3
            else:
                # 输出字面量
                ctrl |= (1 << bitcount)
                out.append(data[pos])
                out.append(data[pos + 1])
                out.append(data[pos + 2])
                pos += 3
            
            bitcount += 1
        
        out[ctrl_pos] = ctrl
        
        if progress and pos % 2048 == 0:  # 优化：每2KB更新一次（从4KB改为2KB，更频繁更新）
            progress.update_to(pos)
    
    if progress:
        progress.update_to(n)
    
    return bytes(out)


# ==================== 图像辅助 ====================

def bgr_bytes_to_png(path: str, bgr: bytes, w: int, h: int) -> None:
    arr = np.frombuffer(bgr, dtype=np.uint8)
    if arr.size != w * h * 3:
        raise ValueError(f"BGR 数据大小不匹配: got={arr.size}, expected={w*h*3}")
    arr = arr.reshape((h, w, 3))
    rgb = arr[..., ::-1]  # BGR -> RGB
    img = Image.fromarray(rgb, mode="RGB")
    p = _nt_longpath(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    img.save(p)


def png_to_bgr_bytes(path: str, w: int, h: int) -> bytes:
    p = _nt_longpath(path)
    img = Image.open(p).convert("RGB")
    if img.size != (w, h):
        raise ValueError(f"PNG 尺寸不匹配: got={img.size}, expected={(w,h)}")
    rgb = np.array(img, dtype=np.uint8)
    bgr = rgb[..., ::-1]  # RGB -> BGR
    return bgr.tobytes(order="C")


# ==================== Type=0 读写 ====================

@dataclass
class G00Type0:
    width: int
    height: int
    compressed_size: int     # blockSize in rldev
    uncompressed_size: int   # outSize in rldev
    packed: bytes
    decompressed: bytes
    header5: bytes  # type + w + h

    @staticmethod
    def read(path: str, show_progress: bool = True,
             gui_callback: Optional[ProgressCallback] = None) -> "G00Type0":
        """
        读取 Type 0 G00 文件 - 严格按 rldev g00.ml:decode_format_0 实现
        """
        p = _nt_longpath(path)
        with open(p, "rb") as f:
            header5 = f.read(5)
            if len(header5) != 5:
                raise ValueError("文件过小（缺少 5 字节头）")

            typ = header5[0]
            if typ != 0:
                raise ValueError(f"不是 type=0 的 g00（byte0={typ}）")

            w = struct.unpack_from("<H", header5, 1)[0]
            h = struct.unpack_from("<H", header5, 3)[0]
            if w == 0 or h == 0 or w > 0x8000 or h > 0x8000:
                raise ValueError(f"无效的宽高: {w}x{h}")

            # 读取压缩块头
            compressed_size = struct.unpack("<I", f.read(4))[0]
            uncompressed_size = struct.unpack("<I", f.read(4))[0]
            
            # rldev 校验：uncompressed_size 必须等于 width*height*4
            expected_size = w * h * 4
            if uncompressed_size != expected_size:
                print(f"警告: uncompressed_size={uncompressed_size} != expected={expected_size}")
                print(f"  rldev 期望 width*height*4，但实际数据是 width*height*3")
                # 兼容模式：继续处理，但输出警告
            
            # 校验文件长度
            file_len = os.path.getsize(p)
            if compressed_size + 5 != file_len:
                raise ValueError(f"长度校验失败: compressed_size+5={compressed_size+5}, fileLen={file_len}")

            packed_len = compressed_size - 8
            if packed_len < 0:
                raise ValueError("compressed_size 无效（<8）")
            packed = f.read(packed_len)
            if len(packed) != packed_len:
                raise ValueError("压缩数据被截断")

        # 解压数据（实际大小是 w*h*3）
        actual_data_size = w * h * 3
        pb = Progress(actual_data_size, "Decompress", enabled=show_progress, gui_callback=gui_callback)
        dec = lz_decompress_type0(packed, actual_data_size, progress=pb)
        pb.close()

        return G00Type0(
            width=w,
            height=h,
            compressed_size=compressed_size,
            uncompressed_size=uncompressed_size,
            packed=packed,
            decompressed=dec,
            header5=header5,
        )

    def write(self, out_path: str, packed_payload: bytes) -> None:
        """
        写入 Type 0 G00 文件 - 严格按 rldev g00.ml:encode_format_0 实现
        """
        p = _nt_longpath(out_path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        compressed_size = len(packed_payload) + 8
        
        # ✅ 关键兼容性修复：uncompressed_size 必须是 width*height*4
        # rldev 在解码时会严格校验这个值
        uncompressed_size_compat = self.width * self.height * 4
        
        with open(p, "wb") as f:
            f.write(self.header5)  # type+w+h
            f.write(struct.pack("<I", compressed_size))
            f.write(struct.pack("<I", uncompressed_size_compat))  # ✅ 使用 *4
            f.write(packed_payload)


# ==================== JSON ====================

def save_manifest(path: str, obj: Dict[str, Any]) -> None:
    p = _nt_longpath(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as w:
        json.dump(obj, w, ensure_ascii=False, indent=2)


def load_manifest(path: str) -> Dict[str, Any]:
    p = _nt_longpath(path)
    with open(p, "r", encoding="utf-8-sig") as r:
        return json.load(r)


# ==================== Commands ====================

def extract_cmd(input_g00: str, out_dir: str, show_progress: bool = True,
                file_progress_cb: Optional[ProgressCallback] = None) -> None:
    os.makedirs(out_dir, exist_ok=True)
    g = G00Type0.read(input_g00, show_progress=show_progress, gui_callback=file_progress_cb)

    base = os.path.splitext(os.path.basename(input_g00))[0]

    png_name = f"{base}.png"
    png_path = os.path.join(out_dir, png_name)
    
    # 严格取 width*height*3 字节
    bgr_data = g.decompressed[: g.width * g.height * 3]
    bgr_bytes_to_png(png_path, bgr_data, g.width, g.height)

    manifest = {
        "format": "RealLive.G00.Type0",
        "version": 2,  # 标记为重写版本
        "sourceFile": os.path.basename(input_g00),
        "type": 0,
        "width": g.width,
        "height": g.height,
        "png": png_name,
        "note": "Rewritten version - strictly follows rldev-master/src/vaconv implementation",
        "compression": "LZSS(min_count=1, bytes_pp=3, window=4095, max_len=16)",
    }
    json_path = os.path.join(out_dir, f"{base}.json")
    save_manifest(json_path, manifest)

    print(f"OK: extracted -> {out_dir}")
    print(f"  PNG:  {png_path}")
    print(f"  JSON: {json_path}")
    print(f"  Implementation: rldev-compatible")



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
    if manifest.get("type") != 0:
        raise ValueError("Manifest type != 0")
    
    canvas_width = int(manifest["width"])
    canvas_height = int(manifest["height"])
    png_name = manifest.get("png", "image.png")
    
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

def build_cmd(
    input_g00: str,
    json_path: str,
    png_dir: str,
    output_g00: str,
    preset: str,
    show_progress: bool = True,
    file_progress_cb: Optional[ProgressCallback] = None,
) -> None:
    manifest = load_manifest(json_path)
    if manifest.get("type") != 0:
        raise ValueError("Manifest type != 0")

    g0 = G00Type0.read(input_g00, show_progress=show_progress, gui_callback=file_progress_cb)

    w = int(manifest["width"])
    h = int(manifest["height"])
    if w != g0.width or h != g0.height:
        raise ValueError(
            f"Manifest 宽高与原 g00 不一致: manifest={w}x{h}, g00={g0.width}x{g0.height}"
        )

    png_name = manifest.get("png", "image.png")
    png_path = os.path.join(png_dir, png_name)
    bgr = png_to_bgr_bytes(png_path, w, h)

    # 压缩数据
    if preset == "ultra_max":
        print(f"使用 Ultra-Max 极限压缩（ProMax暴力搜索，完全匹配rldev）...")
        try:
            from g00_optimizer import OptimizedLZSS
            pb = Progress(len(bgr), f"Compress(ultra_max)", enabled=show_progress, gui_callback=file_progress_cb)
            packed = OptimizedLZSS.compress(
                bgr, min_count=1, bytes_pp=3, preset="ultra_max",
                progress_cb=file_progress_cb
            )
            pb.close()
        except ImportError:
            print("警告: g00_optimizer 不可用，回退到 promax 模式")
            pb = Progress(len(bgr), f"Compress(fallback)", enabled=show_progress, gui_callback=file_progress_cb)
            packed = lz_compress_promax_type0(bgr, progress=pb)
            pb.close()
    elif preset == "promax":
        print(f"使用 ProMax 暴力搜索压缩（完全匹配 rldev brutal 模式）...")
        if not NUMBA_AVAILABLE:
            print("  警告: Numba 未安装，ProMax 模式会非常慢！")
            print("  建议: pip install numba")
        
        pb = Progress(len(bgr), f"Compress(promax)", enabled=show_progress, gui_callback=file_progress_cb)
        packed = lz_compress_promax_type0(bgr, progress=pb)
        pb.close()
    else:
        # 其他预设使用优化模块（如果可用）
        try:
            from g00_optimizer import OptimizedLZSS
            print(f"使用优化模块压缩（preset={preset}）...")
            packed = OptimizedLZSS.compress(
                bgr, min_count=1, bytes_pp=3, preset=preset,
                progress_cb=file_progress_cb
            )
        except ImportError:
            print("警告: g00_optimizer 不可用，回退到 promax 模式")
            pb = Progress(len(bgr), f"Compress(fallback)", enabled=show_progress, gui_callback=file_progress_cb)
            packed = lz_compress_promax_type0(bgr, progress=pb)
            pb.close()

    g0.write(output_g00, packed_payload=packed)

    print(f"OK: built -> {output_g00}")
    print(f"  preset={preset}")
    print(f"  compressed size: {len(packed)} bytes")
    print(f"  uncompressed size (header): {w*h*4} bytes (rldev compat)")
    print(f"  actual data size: {len(bgr)} bytes (width*height*3)")
    print(f"  compression ratio: {len(bgr)/len(packed):.2f}x")


def main():
    ap = argparse.ArgumentParser(description="Type=0 G00 提取/构建工具（重写版本，rldev 兼容）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_ex = sub.add_parser("extract", help="提取 type0 g00 -> <name>.png + <name>.json")
    ap_ex.add_argument("input_g00")
    ap_ex.add_argument("out_dir")
    ap_ex.add_argument("--no-progress", action="store_true", default=False)

    ap_b = sub.add_parser("build", help="从 原始 g00 + json + png 构建新的 type0 g00")
    ap_b.add_argument("input_g00")
    ap_b.add_argument("json_path")
    ap_b.add_argument("png_dir")
    ap_b.add_argument("output_g00")
    ap_b.add_argument("--preset", choices=["fast", "normal", "max", "ultra_max", "promax"], default="promax",
                     help="压缩预设（ultra_max=极限哈希优化, promax=暴力搜索）")
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
        build_cmd(
            args.input_g00,
            args.json_path,
            args.png_dir,
            args.output_g00,
            preset=args.preset,
            show_progress=(not args.no_progress),
        )
    elif args.cmd == "export_psd":
        export_psd_cmd(
            args.json_path,
            args.png_dir,
            args.output_psd,
            show_progress=(not args.no_progress),
        )
    else:
        ap.error("未知命令")


if __name__ == "__main__":
    main()
