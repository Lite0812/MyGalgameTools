#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
G00 Type=3 提取器/构建器（Encrypted JPEG -> PNG 优先） + JSON

对应 C#：ImageG00Jpeg.cs（Siglus / RealLive 的 G00/JPEG）
----------------------------------------------------------------
Type=3 文件结构（最常见实现）：
- 头部固定 5 字节：
    byte  type   = 3
    uint16 width  (LE)
    uint16 height (LE)
- 从 offset=5 开始：加密的 JPEG 数据（直到文件末尾）

加/解密方式：
- 对 offset=5 起的 payload 做循环 XOR：
    payload[i] ^= DefaultKey[(i % keyLen)]
  （C# 是 StreamRegion(file, 5) + ByteStringEncryptedStream(DefaultKey)，
   在 region 内 position 从 0 计，因此这里 start_pos=0）

导出策略（按你的要求改动）：
- extract 默认：导出 PNG + JSON（优先 PNG）
- extract 可选：--export-jpg 额外导出解密后的 JPG（原始 JPEG 字节，非重编码）
- build 默认：--src auto，但 auto 会“优先 PNG”，找不到再用 JPG

重要提醒（回封/编辑）：
- Type=3 的真实存储是 JPEG（有损、无 alpha）。
- 如果你编辑的是 PNG，再 build 回 Type=3：
    PNG -> 重新编码成 JPEG -> XOR 加密写回
  这会：
    1) 产生有损压缩（可通过 --jpeg-quality 控制）
    2) 丢失 alpha 通道（会转成 RGB）
- 如果你想做到“无损回封”，需要使用 extract 时导出的 JPG（--export-jpg），
  或者不要改动 PNG 后再回封。

用法示例：
  # 默认：只导出 PNG + JSON（不导出 JPG）
  g00_type3 extract input.g00 out_dir

  # 导出 PNG + JSON，并额外导出解密后的 JPG
  g00_type3 extract input.g00 out_dir --export-jpg

  # 回封：auto 模式优先找 PNG（默认），再找 JPG
  g00_type3 build input.g00 out_dir/input.json out_dir output.g00

  # 回封：强制用 JPG（无损回封前提：你没改过该 JPG）
  g00_type3 build input.g00 out_dir/input.json out_dir output.g00 --src jpeg

  # 回封：用 PNG（会重新编码为 JPEG），设置 JPEG 质量
  g00_type3 build input.g00 out_dir/input.json out_dir output.g00 --src png --jpeg-quality 95
"""

import argparse
import io
import json
import os
import struct
import sys
from typing import Dict, Any, Optional, Callable

# 导入 time 用于进度回调节流
import time

try:
    from PIL import Image
except ImportError:
    print("错误: 需要安装 Pillow: pip install pillow")
    sys.exit(1)

# 导入优化模块（可选）
try:
    from g00_optimizer import (
        optimized_xor_crypt, NUMBA_AVAILABLE as OPT_NUMBA_AVAILABLE
    )
    OPTIMIZER_AVAILABLE = True
except ImportError:
    OPTIMIZER_AVAILABLE = False
    OPT_NUMBA_AVAILABLE = False

# 尝试导入 NumPy 用于向量化 XOR
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

# ------------------ PSD 导出依赖 ------------------
try:
    from psd_tools import PSDImage
    PSD_TOOLS_AVAILABLE = True
except ImportError:
    PSD_TOOLS_AVAILABLE = False

# ------------------ 从同模块导入图像辅助函数 ------------------
from g00_type1 import load_png_rgba


# GUI 进度回调类型定义
ProgressCallback = Callable[[int, int], None]  # (current, total)

# ------------------ Windows 长路径辅助 ------------------
def _nt_longpath(p: str) -> str:
    p = os.path.abspath(p)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    return p


# ------------------ 与 C# ByteStringEncryptedStream 一致的 XOR ------------------
def xor_crypt(data: bytes, key: bytes, start_pos: int = 0) -> bytes:
    """
    位置相关的循环 XOR。
    对于 Type=3：payload 从 offset=5 开始，region 内 position 从 0 计，
    因此 start_pos=0 即可对齐 C# 行为。
    
    优化：若 NumPy 可用，使用向量化操作加速
    """
    klen = len(key)
    if klen == 0:
        raise ValueError("key 不能为空")
    start_pos %= klen
    
    # 尝试使用优化模块
    if OPTIMIZER_AVAILABLE:
        try:
            return optimized_xor_crypt(data, key, start_pos)
        except Exception:
            pass  # 回退到其他实现
    
    # 向量化 XOR（NumPy 加速）
    if NUMPY_AVAILABLE and len(data) > 1024:  # 大于 1KB 时使用向量化
        data_arr = np.frombuffer(data, dtype=np.uint8)
        n = len(data)
        
        # 创建完整的 key 循环数组
        full_cycles = n // klen
        remainder = n % klen
        
        # 构建 key 数组
        key_arr = np.frombuffer(key, dtype=np.uint8)
        
        # 处理 start_pos 偏移
        if start_pos > 0:
            # 重排 key 以处理偏移
            key_arr = np.concatenate([key_arr[start_pos:], key_arr[:start_pos]])
        
        # 创建完整的 key 序列
        key_repeated = np.tile(key_arr, full_cycles + 1)[:n]
        
        # 执行向量化 XOR
        result = np.bitwise_xor(data_arr, key_repeated)
        return bytes(result)
    
    # 原始 Python 实现（回退）
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ key[(start_pos + i) % klen]
    return bytes(out)


# ------------------ DefaultKey（来自 C# ImageG00Jpeg.cs） ------------------
DEFAULT_KEY = bytes([
    0x45, 0x0C, 0x85, 0xC0, 0x75, 0x14, 0xE5, 0x5D, 0x8B, 0x55, 0xEC, 0xC0, 0x5B, 0x8B, 0xC3, 0x8B,
    0x81, 0xFF, 0x00, 0x00, 0x04, 0x00, 0x85, 0xFF, 0x6A, 0x00, 0x76, 0xB0, 0x43, 0x00, 0x76, 0x49,
    0x00, 0x8B, 0x7D, 0xE8, 0x8B, 0x75, 0xA1, 0xE0, 0x0C, 0x85, 0xC0, 0xC0, 0x75, 0x78, 0x30, 0x44,
    0x00, 0x85, 0xFF, 0x76, 0x37, 0x81, 0x1D, 0xD0, 0xFF, 0x00, 0x00, 0x75, 0x44, 0x8B, 0xB0, 0x43,
    0x45, 0xF8, 0x8D, 0x55, 0xFC, 0x52, 0x00, 0x76, 0x68, 0x00, 0x00, 0x04, 0x00, 0x6A, 0x43, 0x8B,
    0xB1, 0x43, 0x00, 0x6A, 0x05, 0xFF, 0x50, 0xFF, 0xD3, 0xA1, 0xE0, 0x04, 0x00, 0x56, 0x15, 0x2C,
    0x44, 0x00, 0x85, 0xC0, 0x74, 0x09, 0xC3, 0xA1, 0x5F, 0x5E, 0x33, 0x8B, 0xE5, 0x5D, 0xE0, 0x30,
    0x04, 0x00, 0x81, 0xC6, 0x00, 0x00, 0x81, 0xEF, 0x04, 0x00, 0x85, 0x30, 0x44, 0x00, 0x00, 0x00,
    0x5D, 0xC3, 0x8B, 0x55, 0xF8, 0x8D, 0x5E, 0x5B, 0x4D, 0xFC, 0x51, 0xC4, 0x04, 0x5F, 0x8B, 0xE5,
    0x43, 0x00, 0xEB, 0xD8, 0x8B, 0x45, 0xFF, 0x15, 0xE8, 0x83, 0xC0, 0x57, 0x56, 0x52, 0x2C, 0xB1,
    0x01, 0x00, 0x8B, 0x7D, 0xE8, 0x89, 0x00, 0xE8, 0x45, 0xF4, 0x8B, 0x20, 0x50, 0x6A, 0x47, 0x28,
    0x00, 0x50, 0x53, 0xFF, 0x15, 0x34, 0xE4, 0x6A, 0xB1, 0x43, 0x00, 0x0C, 0x8B, 0x45, 0x00, 0x6A,
    0x8B, 0x4D, 0xEC, 0x89, 0x08, 0x8A, 0x85, 0xC0, 0x45, 0xF0, 0x84, 0x8B, 0x45, 0x10, 0x74, 0x05,
    0xF5, 0x28, 0x01, 0x00, 0x83, 0xC4, 0x52, 0x6A, 0x08, 0x89, 0x45, 0x83, 0xC2, 0x20, 0x00, 0xE8,
    0xE8, 0xF4, 0xFB, 0xFF, 0xFF, 0x8B, 0x8B, 0x5D, 0x45, 0x0C, 0x83, 0xC0, 0x74, 0xC5, 0xF8, 0x53,
    0xC4, 0x08, 0x85, 0xC0, 0x75, 0x56, 0x30, 0x44, 0x8B, 0x1D, 0xD0, 0xF0, 0xA1, 0xE0, 0x00, 0x83,
])


# ------------------ JSON I/O ------------------
def save_manifest(path: str, obj: Dict[str, Any]) -> None:
    p = _nt_longpath(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as w:
        json.dump(obj, w, ensure_ascii=False, indent=2)


def load_manifest(path: str) -> Dict[str, Any]:
    p = _nt_longpath(path)
    with open(p, "r", encoding="utf-8-sig") as r:
        return json.load(r)


# ------------------ Core read/write ------------------
def read_g00_type3(path: str) -> Dict[str, Any]:
    p = _nt_longpath(path)
    with open(p, "rb") as f:
        header5 = f.read(5)
        if len(header5) != 5:
            raise ValueError("文件过小（缺少 5 字节头）")
        typ = header5[0]
        if typ != 3:
            raise ValueError(f"不是 type=3 的 g00（byte0={typ}）")
        w = struct.unpack_from("<H", header5, 1)[0]
        h = struct.unpack_from("<H", header5, 3)[0]
        enc = f.read()

    dec_jpeg = xor_crypt(enc, DEFAULT_KEY, start_pos=0)
    return {"header5": header5, "width": w, "height": h, "enc": enc, "jpeg": dec_jpeg}


def write_g00_type3(out_path: str, header5: bytes, jpeg_plain: bytes) -> None:
    enc = xor_crypt(jpeg_plain, DEFAULT_KEY, start_pos=0)
    p = _nt_longpath(out_path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "wb") as f:
        f.write(header5)
        f.write(enc)


# ------------------ Image helpers ------------------
def jpeg_bytes_to_png(jpeg_bytes: bytes, png_path: str) -> None:
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGBA")
    p = _nt_longpath(png_path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    img.save(p)


def png_to_jpeg_bytes(png_path: str, quality: int = 95) -> bytes:
    p = _nt_longpath(png_path)
    img = Image.open(p).convert("RGB")  # JPEG 不支持 alpha
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=int(quality), optimize=True)
    return buf.getvalue()


def read_file_bytes(path: str) -> bytes:
    p = _nt_longpath(path)
    with open(p, "rb") as f:
        return f.read()


# ------------------ Commands ------------------
def extract_cmd(input_g00: str, out_dir: str, export_jpg: bool,
                file_progress_cb: Optional[ProgressCallback] = None) -> None:
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(input_g00))[0]
    
    # 进度回调：Type3 没有复杂的压缩，使用简单的阶段回调
    if file_progress_cb:
        file_progress_cb(10, 100)  # 读取开始

    g = read_g00_type3(input_g00)
    
    if file_progress_cb:
        file_progress_cb(40, 100)  # 读取完成

    # 默认：优先导出 PNG（如果解码失败，再提示并依然可导出 JPG）
    png_name = f"{base}.png"
    png_path = os.path.join(out_dir, png_name)
    png_ok = True
    try:
        jpeg_bytes_to_png(g["jpeg"], png_path)
    except Exception as e:
        png_ok = False
        print(f"[WARN] JPEG 解码为 PNG 失败：{e}")
    
    if file_progress_cb:
        file_progress_cb(70, 100)  # PNG 转换完成

    jpg_name: Optional[str] = None
    if export_jpg:
        jpg_name = f"{base}.jpg"
        jpg_path = os.path.join(out_dir, jpg_name)
        with open(_nt_longpath(jpg_path), "wb") as f:
            f.write(g["jpeg"])

    # 如果 PNG 失败且未导出 JPG，则兜底导出 JPG（至少保证能拿到内容）
    if not png_ok and not export_jpg:
        jpg_name = f"{base}.jpg"
        jpg_path = os.path.join(out_dir, jpg_name)
        with open(_nt_longpath(jpg_path), "wb") as f:
            f.write(g["jpeg"])
        print("[WARN] 已兜底导出 JPG（因为 PNG 导出失败）")
    
    if file_progress_cb:
        file_progress_cb(90, 100)  # 导出完成

    manifest = {
        "format": "RealLive.G00.Type3",
        "version": 1,
        "sourceFile": os.path.basename(input_g00),
        "type": 3,
        "width": g["width"],
        "height": g["height"],
        "png": png_name if png_ok else None,
        "jpeg": jpg_name,  # 默认 null；加 --export-jpg 才有
        "note": "payload(offset=5..) is encrypted JPEG; crypt = XOR(DefaultKey) with region position",
    }
    json_path = os.path.join(out_dir, f"{base}.json")
    save_manifest(json_path, manifest)
    
    if file_progress_cb:
        file_progress_cb(100, 100)  # 完成

    print(f"OK: extracted -> {out_dir}")
    if png_ok:
        print(f"  PNG : {png_path}")
    if jpg_name:
        print(f"  JPG : {os.path.join(out_dir, jpg_name)}")
    print(f"  JSON: {json_path}")


def build_cmd(input_g00: str, json_path: str, img_dir: str, output_g00: str,
              src: str, jpeg_quality: int,
              file_progress_cb: Optional[ProgressCallback] = None) -> None:
    manifest = load_manifest(json_path)
    if int(manifest.get("type", -1)) != 3:
        raise ValueError("Manifest type != 3")

    if file_progress_cb:
        file_progress_cb(10, 100)  # 开始读取

    # 复用原始 header5（更稳，避免某些游戏对 w/h 或其他隐含约束敏感）
    g = read_g00_type3(input_g00)
    header5 = g["header5"]

    if file_progress_cb:
        file_progress_cb(30, 100)  # 读取完成

    jpeg_plain: Optional[bytes] = None

    def _try_png() -> Optional[bytes]:
        png_name = manifest.get("png")
        if not png_name:
            return None
        p = os.path.join(img_dir, png_name)
        if os.path.isfile(_nt_longpath(p)):
            return png_to_jpeg_bytes(p, quality=jpeg_quality)
        return None

    def _try_jpg() -> Optional[bytes]:
        jpg_name = manifest.get("jpeg")
        if not jpg_name:
            return None
        p = os.path.join(img_dir, jpg_name)
        if os.path.isfile(_nt_longpath(p)):
            return read_file_bytes(p)
        return None

    if src == "auto":
        # 按你的要求：auto 模式也优先 PNG
        jpeg_plain = _try_png() or _try_jpg()
        if jpeg_plain is None:
            raise FileNotFoundError("auto 模式下未找到可用的 png/jpg（检查 img_dir 与 manifest）")
    elif src == "png":
        jpeg_plain = _try_png()
        if jpeg_plain is None:
            raise FileNotFoundError("src=png 但找不到 PNG（检查 manifest.png 与 img_dir）")
    elif src == "jpeg":
        jpeg_plain = _try_jpg()
        if jpeg_plain is None:
            raise FileNotFoundError("src=jpeg 但找不到 JPG（检查 manifest.jpeg 与 img_dir）")
    else:
        raise ValueError(f"未知 src: {src}")
    
    if file_progress_cb:
        file_progress_cb(70, 100)  # 图像处理完成

    write_g00_type3(output_g00, header5, jpeg_plain)
    
    if file_progress_cb:
        file_progress_cb(100, 100)  # 完成

    print(f"OK: built -> {output_g00}")
    print(f"  src={src}, jpeg_quality={jpeg_quality}, jpeg_bytes={len(jpeg_plain)}")

def export_psd_cmd(json_path: str, img_dir: str, output_psd: str, 
                    show_progress: bool = True) -> None:
    """
    根据 JSON 配置和 PNG/JPG 图片，生成单个图层的 PSD 文件。
    
    参数:
        json_path: 提取生成的 JSON 文件路径
        img_dir: PNG/JPG 图片所在目录
        output_psd: 输出的 PSD 文件路径
        show_progress: 显示进度
    """
    if not PSD_TOOLS_AVAILABLE:
        print("错误: 需要安装 psd-tools 来创建 PSD 文件")
        print("安装命令: pip install psd-tools")
        sys.exit(1)
    
    # 加载 manifest
    manifest = load_manifest(json_path)
    if int(manifest.get("type", -1)) != 3:
        raise ValueError("Manifest type != 3")
    
    canvas_width = int(manifest["width"])
    canvas_height = int(manifest["height"])
    png_name = manifest.get("png")
    jpg_name = manifest.get("jpeg")
    
    if show_progress:
        print(f"创建 PSD: {canvas_width}x{canvas_height}")
    
    # 加载 PNG 或 JPG 图片
    img_path = None
    if png_name:
        img_path = os.path.join(img_dir, png_name)
    elif jpg_name:
        img_path = os.path.join(img_dir, jpg_name)
    else:
        print("错误: manifest 中没有 png 或 jpeg 字段")
        sys.exit(1)
    
    if not os.path.isfile(img_path):
        print(f"错误: 图片不存在 {img_path}")
        sys.exit(1)
    
    try:
        rgba = load_png_rgba(img_path)
    except Exception as e:
        print(f"错误: 无法加载图片 {img_path}: {e}")
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

def main():
    ap = argparse.ArgumentParser(description="Type=3 G00 提取/构建工具（Encrypted JPEG，默认导出 PNG）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_ex = sub.add_parser("extract", help="提取 type3 g00 -> PNG + JSON（可选导出 JPG）")
    ap_ex.add_argument("input_g00")
    ap_ex.add_argument("out_dir")
    ap_ex.add_argument("--export-jpg", action="store_true", default=False,
                       help="额外导出解密后的 JPG（原始 JPEG 字节，非重编码）")

    ap_b = sub.add_parser("build", help="从 原始 g00 + json + png/jpg 构建新的 type3 g00")
    ap_b.add_argument("input_g00")
    ap_b.add_argument("json_path")
    ap_b.add_argument("img_dir")
    ap_b.add_argument("output_g00")
    ap_b.add_argument("--src", choices=["auto", "png", "jpeg"], default="auto",
                      help="回封使用哪个源图；auto 会优先 png 再 jpg")
    ap_b.add_argument("--jpeg-quality", type=int, default=95,
                      help="当使用 PNG 回封时，PNG->JPEG 的质量（有损）")
    ap_b.add_argument("--no-progress", action="store_true", default=False)

    ap_psd = sub.add_parser("export_psd", help="从 json + png/jpg 导出为 PSD 文件（单个图层）")
    ap_psd.add_argument("json_path")
    ap_psd.add_argument("img_dir")
    ap_psd.add_argument("output_psd")
    ap_psd.add_argument("--no-progress", action="store_true", default=False)

    args = ap.parse_args()
    if args.cmd == "extract":
        extract_cmd(args.input_g00, args.out_dir, export_jpg=args.export_jpg)
    elif args.cmd == "build":
        build_cmd(args.input_g00, args.json_path, args.img_dir, args.output_g00,
                  src=args.src, jpeg_quality=args.jpeg_quality)
    elif args.cmd == "export_psd":
        export_psd_cmd(args.json_path, args.img_dir, args.output_psd,
                      show_progress=(not args.no_progress))
    else:
        ap.error("未知命令")


if __name__ == "__main__":
    main()
