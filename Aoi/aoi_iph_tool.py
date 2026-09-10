from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image


HEADER_SIZE = 0x58
RIFF_SIGNATURE = b"RIFF"
FORM_TYPES = {b"IPH\x00", b"IPH ", b"IPG\x00"}
DEFAULT_FMT_PAYLOAD_HEX = (
    "03000300"
    "0000000000000000"
    "0000000000000000"
    "0000000000000000"
)


class IphError(RuntimeError):
    pass


def read_u16_le(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def read_u32_le(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def write_u16_le(value: int) -> bytes:
    return int(value).to_bytes(2, "little")


def write_u32_le(value: int) -> bytes:
    return int(value).to_bytes(4, "little")


def iph_meta_path_for_png(png_path: Path) -> Path:
    return png_path.with_suffix(".iph.json")


def is_iph_path(path: Path) -> bool:
    return path.suffix.lower() == ".iph"


def is_png_path(path: Path) -> bool:
    return path.suffix.lower() == ".png"


def iter_files(root: Path, recursive: bool) -> list[Path]:
    if root.is_file():
        return [root]
    iterator = root.rglob("*") if recursive else root.iterdir()
    return sorted(path for path in iterator if path.is_file())


def decode_bgr555_to_rgba(pixel: int, transparency_key: int) -> tuple[int, int, int, int]:
    alpha = 0 if ((pixel & 0x8000) != 0 or ((pixel & 0x7FFF) == transparency_key and transparency_key != 0xFFFF)) else 255
    color = pixel & 0x7FFF
    blue = color & 0x1F
    green = (color >> 5) & 0x1F
    red = (color >> 10) & 0x1F
    r = (red << 3) | (red >> 2)
    g = (green << 3) | (green >> 2)
    b = (blue << 3) | (blue >> 2)
    return r, g, b, alpha


def encode_rgba_to_bgr555(rgba: tuple[int, int, int, int]) -> int:
    r, g, b, a = rgba
    red = min(31, max(0, (r * 31 + 127) // 255))
    green = min(31, max(0, (g * 31 + 127) // 255))
    blue = min(31, max(0, (b * 31 + 127) // 255))
    value = (red << 10) | (green << 5) | blue
    if a < 128:
        value |= 0x8000
    return value


def decode_iph_buffer(data: bytes) -> tuple[dict[str, object], bytes]:
    if len(data) < HEADER_SIZE:
        raise IphError("IPH 文件过小")
    signature = data[0:4]
    form_type = data[8:12]
    if signature != RIFF_SIGNATURE:
        raise IphError(f"无效 IPH 外层签名: {signature!r}")
    if form_type not in FORM_TYPES:
        raise IphError(f"无效 IPH form type: {form_type!r}")

    riff_size = read_u32_le(data, 4)
    fmt_chunk = data[12:16]
    fmt_size = read_u32_le(data, 16)
    bmp_chunk = data[0x38:0x3C]
    packed_size = read_u32_le(data, 0x3C)
    width = read_u16_le(data, 0x40)
    height = read_u16_le(data, 0x42)
    canvas_width = read_u16_le(data, 0x4C)
    canvas_height = read_u16_le(data, 0x4E)
    bpp = read_u16_le(data, 0x50)
    compression = read_u16_le(data, 0x52)
    transparency_key = read_u16_le(data, 0x54)
    reserved_56_57 = data[0x56:0x58]

    if fmt_chunk != b"fmt ":
        raise IphError("缺少 fmt chunk")
    if bmp_chunk != b"bmp ":
        raise IphError("缺少 bmp chunk")
    if bpp != 16:
        raise IphError(f"暂不支持 bpp={bpp}")

    src = memoryview(data)[HEADER_SIZE : HEADER_SIZE + packed_size]
    expected_raw_size = width * height * 2
    out = bytearray(expected_raw_size)

    if compression == 0:
        if len(src) < expected_raw_size:
            raise IphError("未压缩数据长度不足")
        out[:] = src[:expected_raw_size]
    elif compression == 1:
        src_pos = 0
        line_size = width * 2
        extra_line = bytearray(line_size)
        for y in range(height):
            row = line_size * y
            if src_pos >= len(src):
                raise IphError("压缩数据提前结束")
            ctl = src[src_pos]
            src_pos += 1
            if ctl:
                dst = row
                pixel = 0
                while dst < expected_raw_size:
                    if src_pos >= len(src):
                        raise IphError("读取压缩控制字失败")
                    ctl = src[src_pos]
                    src_pos += 1
                    if ctl == 0xFF:
                        break
                    if ctl == 0xFE:
                        if src_pos + 3 > len(src):
                            raise IphError("读取 RLE 像素失败")
                        count = src[src_pos] + 1
                        src_pos += 1
                        pixel = int.from_bytes(src[src_pos : src_pos + 2], "little")
                        src_pos += 2
                        for _ in range(count):
                            out[dst : dst + 2] = write_u16_le(pixel)
                            dst += 2
                    elif ctl < 0x80:
                        if src_pos >= len(src):
                            raise IphError("读取原始像素低字节失败")
                        lo = src[src_pos]
                        src_pos += 1
                        out[dst] = lo
                        out[dst + 1] = ctl
                        dst += 2
                        pixel = (ctl << 8) | lo
                    else:
                        delta = ctl & 0x7F
                        r = (pixel & 0x7C00) >> 10
                        g = (pixel & 0x03E0) >> 5
                        b = pixel & 0x1F
                        pixel = (
                            (b + (delta // 25) % 5 - 2)
                            | ((g + (delta // 5) % 5 - 2) << 5)
                            | ((r + delta % 5 - 2) << 10)
                        )
                        out[dst : dst + 2] = write_u16_le(pixel & 0xFFFF)
                        dst += 2
            else:
                if src_pos + line_size > len(src):
                    raise IphError("读取未压缩行失败")
                out[row : row + line_size] = src[src_pos : src_pos + line_size]
                src_pos += line_size
                if src_pos >= len(src):
                    raise IphError("未压缩行后缺少分隔字节")
                src_pos += 1

            if src_pos >= len(src):
                raise IphError("缺少透明扩展控制字")
            ctl = src[src_pos]
            src_pos += 1
            if ctl != 0:
                dst = 0
                while True:
                    if src_pos >= len(src):
                        raise IphError("读取透明扩展数据失败")
                    ctl = src[src_pos]
                    src_pos += 1
                    if ctl == 0xFF:
                        break
                    if ctl >= 0x80:
                        if src_pos >= len(src):
                            raise IphError("读取透明扩展重复计数失败")
                        b = ctl & 0x7F
                        count = src[src_pos] + 1
                        src_pos += 1
                        for _ in range(count):
                            if dst >= len(extra_line):
                                raise IphError("透明扩展缓冲区越界")
                            extra_line[dst] = b
                            dst += 1
                    else:
                        if dst >= len(extra_line):
                            raise IphError("透明扩展缓冲区越界")
                        extra_line[dst] = ctl
                        dst += 1
                dst = row + 1
                for i in range(width):
                    v46 = extra_line[i // 6]
                    if ((32 >> (i % 6)) & v46) != 0:
                        out[dst] |= 0x80
                    dst += 2
            else:
                if src_pos >= len(src):
                    raise IphError("透明扩展零标记后缺少分隔字节")
                src_pos += 1
    else:
        raise IphError(f"暂不支持 compression={compression}")

    metadata = {
        "signature": signature.decode("latin1"),
        "form_type": form_type.decode("latin1"),
        "riff_size": riff_size,
        "fmt_size": fmt_size,
        "fmt_payload_hex": data[0x14:0x38].hex(),
        "width": width,
        "height": height,
        "reserved_44_4f_hex": data[0x44:0x4C].hex(),
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "bpp": bpp,
        "compression": compression,
        "transparency_key": transparency_key,
        "reserved_56_57_hex": reserved_56_57.hex(),
        "packed_size": packed_size,
    }
    return metadata, bytes(out)


def raw_pixels_to_image(raw_pixels: bytes, width: int, height: int, transparency_key: int) -> Image.Image:
    pixels = bytearray(width * height * 4)
    pos = 0
    out_pos = 0
    for _ in range(width * height):
        value = int.from_bytes(raw_pixels[pos : pos + 2], "little")
        pos += 2
        r, g, b, a = decode_bgr555_to_rgba(value, transparency_key)
        pixels[out_pos : out_pos + 4] = bytes((r, g, b, a))
        out_pos += 4
    return Image.frombytes("RGBA", (width, height), bytes(pixels))


def image_to_raw_pixels(image: Image.Image) -> bytes:
    rgba = image.convert("RGBA")
    out = bytearray(rgba.width * rgba.height * 2)
    pos = 0
    for pixel in rgba.getdata():
        value = encode_rgba_to_bgr555(pixel)
        out[pos : pos + 2] = write_u16_le(value)
        pos += 2
    return bytes(out)


def build_iph_header(meta: dict[str, object], raw_pixels: bytes) -> bytes:
    width = int(meta["width"])
    height = int(meta["height"])
    packed_size = len(raw_pixels)
    signature = str(meta.get("signature", "RIFF")).encode("latin1")
    form_type = str(meta.get("form_type", "IPH\x00")).encode("latin1")
    if len(signature) != 4 or len(form_type) != 4:
        raise IphError("metadata.signature / metadata.form_type 长度必须为 4")
    fmt_payload = bytes.fromhex(str(meta.get("fmt_payload_hex", DEFAULT_FMT_PAYLOAD_HEX)))
    if len(fmt_payload) != 0x24:
        raise IphError("fmt_payload_hex 长度必须为 0x24 字节")
    reserved_44_4f = bytes.fromhex(str(meta.get("reserved_44_4f_hex", "0000000000000000")))
    reserved_56_57 = bytes.fromhex(str(meta.get("reserved_56_57_hex", "0000")))
    if len(reserved_44_4f) != 8 or len(reserved_56_57) != 2:
        raise IphError("保留字段长度不正确")

    out = bytearray()
    out.extend(signature)
    out.extend(write_u32_le(0x38))
    out.extend(form_type)
    out.extend(b"fmt ")
    out.extend(write_u32_le(0x24))
    out.extend(fmt_payload)
    out.extend(b"bmp ")
    out.extend(write_u32_le(packed_size))
    out.extend(write_u16_le(width))
    out.extend(write_u16_le(height))
    out.extend(reserved_44_4f)
    out.extend(write_u16_le(int(meta.get("canvas_width", 0))))
    out.extend(write_u16_le(int(meta.get("canvas_height", 0))))
    out.extend(write_u16_le(16))
    out.extend(write_u16_le(0))  # Python 版统一输出未压缩
    out.extend(write_u16_le(int(meta.get("transparency_key", 0xFFFF))))
    out.extend(reserved_56_57)
    if len(out) != HEADER_SIZE:
        raise IphError(f"生成头大小错误: {len(out)}")
    return bytes(out)


def decode_iph_file(input_path: Path, png_path: Path, meta_path: Path) -> None:
    metadata, raw_pixels = decode_iph_buffer(input_path.read_bytes())
    image = raw_pixels_to_image(raw_pixels, int(metadata["width"]), int(metadata["height"]), int(metadata["transparency_key"]))
    png_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(png_path)
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"已转换为 PNG: {input_path} -> {png_path}")
    print(f"已写入 metadata: {meta_path}")


def encode_iph_file(png_path: Path, iph_path: Path, meta_path: Path | None) -> None:
    image = Image.open(png_path)
    raw_pixels = image_to_raw_pixels(image)
    metadata: dict[str, object]
    if meta_path is not None and meta_path.is_file():
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        metadata = {
            "signature": "RIFF",
            "form_type": "IPH\x00",
            "fmt_payload_hex": DEFAULT_FMT_PAYLOAD_HEX,
            "reserved_44_4f_hex": "0000000000000000",
            "reserved_56_57_hex": "0000",
            "canvas_width": 0,
            "canvas_height": 0,
            "transparency_key": 0xFFFF,
        }
    metadata["width"] = image.width
    metadata["height"] = image.height
    header = build_iph_header(metadata, raw_pixels)
    iph_path.parent.mkdir(parents=True, exist_ok=True)
    iph_path.write_bytes(header + raw_pixels)
    print(f"已转换为 IPH: {png_path} -> {iph_path}")
    if meta_path is not None and meta_path.is_file():
        print(f"使用 metadata: {meta_path}")


def default_png_output_path(input_path: Path, input_root: Path, output_root: Path, keep_structure: bool = True) -> Path:
    if keep_structure:
        relative = input_path.relative_to(input_root) if input_root.is_dir() else Path(input_path.name)
    else:
        relative = Path(input_path.name)
    return output_root / relative.with_suffix(".png")


def default_iph_output_path(input_path: Path, input_root: Path, output_root: Path, keep_structure: bool = True) -> Path:
    if keep_structure:
        relative = input_path.relative_to(input_root) if input_root.is_dir() else Path(input_path.name)
    else:
        relative = Path(input_path.name)
    return output_root / relative.with_suffix(".iph")


def run_decode(input_path: Path, output_root: Path, recursive: bool = True, keep_structure: bool = True) -> None:
    input_root = input_path if input_path.is_dir() else input_path.parent
    processed = 0
    for file_path in iter_files(input_path, recursive=recursive):
        if not is_iph_path(file_path):
            continue
        png_path = default_png_output_path(file_path, input_root, output_root, keep_structure=keep_structure)
        meta_path = iph_meta_path_for_png(png_path)
        decode_iph_file(file_path, png_path, meta_path)
        processed += 1
    print(f"IPH 解码汇总: 共处理 {processed} 个文件。")


def run_encode(input_path: Path, output_root: Path, recursive: bool = True, keep_structure: bool = True) -> None:
    input_root = input_path if input_path.is_dir() else input_path.parent
    processed = 0
    for file_path in iter_files(input_path, recursive=recursive):
        if not is_png_path(file_path):
            continue
        meta_path = iph_meta_path_for_png(file_path)
        iph_path = default_iph_output_path(file_path, input_root, output_root, keep_structure=keep_structure)
        encode_iph_file(file_path, iph_path, meta_path if meta_path.exists() else None)
        processed += 1
    print(f"IPH 编码汇总: 共处理 {processed} 个文件。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IPH <-> PNG 转换工具")
    subparsers = parser.add_subparsers(dest="mode")

    decode_parser = subparsers.add_parser("decode", help="IPH 转 PNG")
    decode_parser.add_argument("input_path", help="输入 IPH 文件或目录")
    decode_parser.add_argument("output_path", help="输出 PNG 目录")
    decode_parser.add_argument("--no-recursive", action="store_true")

    encode_parser = subparsers.add_parser("encode", help="PNG 转 IPH")
    encode_parser.add_argument("input_path", help="输入 PNG 文件或目录")
    encode_parser.add_argument("output_path", help="输出 IPH 目录")
    encode_parser.add_argument("--no-recursive", action="store_true")

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    try:
        if args.mode == "decode":
            run_decode(Path(args.input_path), Path(args.output_path), recursive=not args.no_recursive)
            return 0
        if args.mode == "encode":
            run_encode(Path(args.input_path), Path(args.output_path), recursive=not args.no_recursive)
            return 0
        parser.print_help()
        return 1
    except (IphError, ValueError, json.JSONDecodeError) as exc:
        print(f"错误: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
