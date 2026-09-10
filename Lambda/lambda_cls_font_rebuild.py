#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lambda CLS bitmap font rebuild tool.

Usage:
  python lambda_cls_font_rebuild.py rebuild out_base\\000051_CID_FONT26B_DAT out_base\\000038_DID_FONT26B_TXT -o _work\\000051_CID_FONT26B_DAT
  python lambda_cls_font_rebuild.py inspect out_base\\000051_CID_FONT26B_DAT out_base\\000038_DID_FONT26B_TXT

The mapping file is { simplified_cn: sjis_slot_char }.  This tool reverses it
and draws the simplified glyph into the mapped SJIS slot cell.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFont


SIGNATURE = b"CLS_TEXFILE"
DEFAULT_SUBS = "subs_cn_jp_v1.json"
DEFAULT_TTF_GLOB = "*.ttf"
DEFAULT_ENCODING = "cp932"
CHANNEL_ORDER = (2, 1, 0, 3)


class ClsError(Exception):
    pass


@dataclass
class FrameInfo:
    index: int
    offset: int
    header_size: int
    width: int
    height: int
    compressed: bool
    format_code: int
    channels: int
    channel_offsets: list[int]
    channel_sizes: list[int]


@dataclass
class GlyphCell:
    slot: int
    slot_char: str
    draw_char: str
    row: int
    col: int
    box: tuple[int, int, int, int]


def u32(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def p32(data: bytearray, off: int, value: int) -> None:
    struct.pack_into("<I", data, off, value)


def read_text_chars(path: Path, encoding: str) -> list[str]:
    text = path.read_bytes().decode(encoding)
    return [ch for ch in text if ch not in "\r\n"]


def load_subs(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ClsError(f"映射表必须是 JSON object: {path}")
    rev: dict[str, str] = {}
    skipped = 0
    for cn, slot in raw.items():
        if not isinstance(cn, str) or not isinstance(slot, str) or not cn or not slot:
            skipped += 1
            continue
        rev[slot[0]] = cn[0]
    if skipped:
        print(f"[警告] 映射表跳过异常条目: {skipped}")
    return rev


class ClsImage:
    def __init__(self, raw: bytes):
        self.raw = bytearray(raw)
        if self.raw[: len(SIGNATURE)] != SIGNATURE:
            raise ClsError("不是 CLS_TEXFILE 图像")
        self.frame_count = u32(self.raw, 0x10)
        self.frame_table = u32(self.raw, 0x14)
        self.first_frame = u32(self.raw, 0x18)
        self.total_size = u32(self.raw, 0x1C)
        if self.frame_count <= 0 or self.frame_count > 256:
            raise ClsError(f"异常帧数: {self.frame_count}")
        self.frames = self._read_frames()

    def _read_frames(self) -> list[FrameInfo]:
        offsets = [self.first_frame]
        for i in range(max(0, self.frame_count - 1)):
            offsets.append(u32(self.raw, self.frame_table + i * 8))
        frames: list[FrameInfo] = []
        for index, off in enumerate(offsets):
            if off <= 0 or off >= len(self.raw):
                raise ClsError(f"帧偏移越界: frame={index}, off=0x{off:X}")
            header_size = u32(self.raw, off)
            width = u32(self.raw, off + 0x1C)
            height = u32(self.raw, off + 0x20)
            compressed = self.raw[off + 0x30] != 0
            format_code = self.raw[off + 0x31]
            if format_code == 2:
                channels = 1
            elif format_code == 4:
                channels = 3
            elif format_code == 5:
                channels = 4
            else:
                raise ClsError(f"不支持的 CLS format code: {format_code}")
            channel_offsets = [u32(self.raw, off + 0x48 + i * 4) for i in range(channels)]
            channel_sizes = [u32(self.raw, off + 0x58 + i * 4) for i in range(channels)]
            frames.append(
                FrameInfo(
                    index=index,
                    offset=off,
                    header_size=header_size,
                    width=width,
                    height=height,
                    compressed=compressed,
                    format_code=format_code,
                    channels=channels,
                    channel_offsets=channel_offsets,
                    channel_sizes=channel_sizes,
                )
            )
        return frames

    def decode_frame(self, frame: FrameInfo) -> Image.Image:
        if frame.channels == 1:
            channel = self._decode_channel(frame, 0)
            return Image.frombytes("L", (frame.width, frame.height), bytes(channel)).convert("RGBA")

        channel_data = [self._decode_channel(frame, i) for i in range(frame.channels)]
        pixel_count = frame.width * frame.height
        out_channels = 4 if frame.channels == 3 else frame.channels
        out = bytearray(pixel_count * out_channels)
        for i, data in enumerate(channel_data):
            dst_chan = CHANNEL_ORDER[i]
            for p, value in enumerate(data):
                out[p * out_channels + dst_chan] = value
        if frame.channels == 3:
            for p in range(pixel_count):
                out[p * 4 + 3] = 255
        return Image.frombytes("RGBA", (frame.width, frame.height), bytes(out))

    def _decode_channel(self, frame: FrameInfo, channel_index: int) -> bytearray:
        pos = frame.offset + frame.channel_offsets[channel_index]
        size = frame.channel_sizes[channel_index]
        if not frame.compressed:
            data = self.raw[pos : pos + size]
            return self._read_v0(data, frame.width, frame.height)
        method = struct.unpack_from(">H", self.raw, pos)[0]
        data = self.raw[pos + 2 : pos + size]
        if method == 0:
            return self._read_v0(data, frame.width, frame.height)
        if method == 1:
            return self._read_v1(data, frame.width, frame.height)
        raise ClsError(f"不支持的压缩方法: frame={frame.index}, channel={channel_index}, method={method}")

    @staticmethod
    def _read_v0(data: bytes | bytearray, width: int, height: int) -> bytearray:
        row_width = len(data) // height
        out = bytearray(width * height)
        if row_width == width:
            out[: len(data)] = data[: len(out)]
            return out
        src = 0
        dst = 0
        for _ in range(height):
            out[dst : dst + row_width] = data[src : src + row_width]
            src += row_width
            dst += width
        return out

    @staticmethod
    def _read_v1(data: bytes | bytearray, width: int, height: int) -> bytearray:
        pos = 0
        row_sizes: list[int] = []
        while pos + 2 <= len(data) and len(row_sizes) < height:
            chunk_size = struct.unpack_from(">H", data, pos)[0]
            row_sizes.append(chunk_size)
            pos += 2
            if pos + sum(row_sizes) >= len(data):
                break
        rows_data_pos = 2 * len(row_sizes)
        expected = rows_data_pos + sum(row_sizes)
        if expected != len(data):
            raise ClsError(f"RLE 行大小不匹配: expected={expected}, actual={len(data)}")
        pos = rows_data_pos
        out = bytearray(width * height)
        dst = 0
        for chunk_size in row_sizes:
            row_end = pos + chunk_size
            x_left = width
            while pos < row_end:
                rle = data[pos]
                pos += 1
                if rle < 0x81:
                    count = rle + 1
                    out[dst : dst + count] = data[pos : pos + count]
                    pos += count
                else:
                    count = 0x101 - rle
                    value = data[pos]
                    pos += 1
                    out[dst : dst + count] = bytes([value]) * count
                dst += count
                x_left -= count
            if x_left > 0:
                dst += x_left
        return out

    @staticmethod
    def encode_rle_channel(channel: bytes | bytearray, width: int, height: int) -> bytes:
        rows = []
        body = bytearray()
        for y in range(height):
            row = channel[y * width : (y + 1) * width]
            encoded = bytearray()
            pos = 0
            while pos < width:
                run = 1
                while pos + run < width and run < 128 and row[pos] == row[pos + run]:
                    run += 1
                if run > 1:
                    encoded.append(0x101 - run)
                    encoded.append(row[pos])
                    pos += run
                    continue
                literal_start = pos
                pos += 1
                while pos < width:
                    if pos + 1 < width and row[pos] == row[pos + 1]:
                        break
                    if pos - literal_start >= 128:
                        break
                    pos += 1
                literal_len = pos - literal_start
                encoded.append(literal_len - 1)
                encoded.extend(row[literal_start : literal_start + literal_len])
            if len(encoded) > 0xFFFF:
                raise ClsError("单行 RLE 数据超过 0xFFFF")
            rows.append(len(encoded))
            body.extend(encoded)
        out = bytearray()
        for size in rows:
            out.extend(struct.pack(">H", size))
        out.extend(body)
        return bytes(out)

    def rebuild_from_images(self, images: list[Image.Image]) -> bytes:
        if len(images) != len(self.frames):
            raise ClsError("重建图像帧数不一致")
        header_size = 0x40 if len(self.frames) > 1 else 0x30
        out = bytearray(header_size)
        out[:0x10] = self.raw[:0x10]
        p32(out, 0x10, len(self.frames))
        p32(out, 0x14, 0x20)

        frame_blobs: list[bytearray] = []
        new_offsets: list[int] = []
        new_sizes: list[int] = []
        cursor = header_size
        for frame, image in zip(self.frames, images):
            image = image.convert("RGBA")
            if image.size != (frame.width, frame.height):
                raise ClsError(f"帧尺寸变化: frame={frame.index}")
            channels = self._split_image_channels(image, frame.channels)
            frame_header = bytearray(self.raw[frame.offset : frame.offset + frame.header_size])
            frame_header[0x30] = 1
            frame_header[0x31] = frame.format_code
            channel_blobs = [b"\x00\x01" + self.encode_rle_channel(ch, frame.width, frame.height) for ch in channels]
            running = frame.header_size
            for i, blob in enumerate(channel_blobs):
                p32(frame_header, 0x48 + i * 4, running)
                p32(frame_header, 0x58 + i * 4, len(blob))
                running += len(blob)
            blob = bytearray(frame_header)
            for channel_blob in channel_blobs:
                blob.extend(channel_blob)
            new_offsets.append(cursor)
            new_sizes.append(len(blob))
            frame_blobs.append(blob)
            cursor += len(blob)

        p32(out, 0x18, new_offsets[0])
        p32(out, 0x1C, cursor)
        if len(self.frames) > 1:
            p32(out, 0x20, new_offsets[1])
            p32(out, 0x24, new_sizes[1])
        else:
            p32(out, 0x20, new_offsets[0])
            p32(out, 0x24, new_sizes[0])
        for blob in frame_blobs:
            out.extend(blob)
        return bytes(out)

    @staticmethod
    def _split_image_channels(image: Image.Image, channels: int) -> list[bytes]:
        raw = image.tobytes()
        pixel_count = image.width * image.height
        out = [bytearray(pixel_count) for _ in range(channels)]
        for p in range(pixel_count):
            r = raw[p * 4 + 0]
            g = raw[p * 4 + 1]
            b = raw[p * 4 + 2]
            a = raw[p * 4 + 3]
            values = (b, g, r, a)
            for i in range(channels):
                out[i][p] = values[i]
        return [bytes(ch) for ch in out]


def find_default_ttf() -> Path:
    for named in (Path("我是可爱字体.ttf"), Path("错误错误错误.ttf")):
        if named.exists():
            return named
    matches = sorted(Path(".").glob(DEFAULT_TTF_GLOB))
    if not matches:
        raise ClsError("找不到 TTF 字体，请使用 --ttf 指定")
    return matches[0]


def infer_cell_width(width: int, cols: int) -> int:
    return width // cols


def infer_cell_height(height: int, glyph_count: int, cols: int) -> int:
    rows = max(1, math.ceil(glyph_count / cols))
    return height // max(rows, 1) if rows <= 18 else width // cols


def build_cells(
    chars: list[str],
    rev_subs: dict[str, str],
    width: int,
    height: int,
    cols: int,
    cell_w: int,
    cell_h: int,
    pad_x: int,
    pad_y: int,
    max_count: int | None,
    subs_only: bool,
) -> tuple[list[GlyphCell], int]:
    cells: list[GlyphCell] = []
    skipped_not_mapped = 0
    for slot, ch in enumerate(chars):
        if max_count is not None and slot >= max_count:
            break
        draw_char = rev_subs.get(ch)
        if not draw_char:
            if subs_only:
                skipped_not_mapped += 1
                continue
            draw_char = ch
        row, col = divmod(slot, cols)
        x0 = col * cell_w + pad_x
        y0 = row * cell_h + pad_y
        x1 = min(width, x0 + cell_w)
        y1 = min(height, y0 + cell_h)
        if x0 >= width or y0 >= height:
            continue
        cells.append(GlyphCell(slot, ch, draw_char, row, col, (x0, y0, x1, y1)))
    return cells, skipped_not_mapped


def sample_background(tile: Image.Image) -> tuple[int, int, int, int]:
    corners = [
        tile.getpixel((0, 0)),
        tile.getpixel((tile.width - 1, 0)),
        tile.getpixel((0, tile.height - 1)),
        tile.getpixel((tile.width - 1, tile.height - 1)),
    ]
    return max(set(corners), key=corners.count)


def fit_font_size(font_path: Path, chars: Iterable[str], requested: int, max_w: int, max_h: int, stroke: int) -> int:
    chars = list(chars)[:128] or ["漢"]
    for size in range(requested, 5, -1):
        font = ImageFont.truetype(str(font_path), size)
        ok = True
        for ch in chars:
            bbox = font.getbbox(ch, stroke_width=stroke)
            if bbox is None:
                continue
            if bbox[2] - bbox[0] > max_w or bbox[3] - bbox[1] > max_h:
                ok = False
                break
        if ok:
            return size
    return requested


def draw_glyph_mask(font: ImageFont.FreeTypeFont, ch: str, size: tuple[int, int], dx: int, dy: int, stroke: int) -> Image.Image:
    w, h = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    bbox = font.getbbox(ch, stroke_width=stroke)
    if bbox is None:
        return mask
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (w - tw) // 2 - bbox[0] + dx
    y = (h - th) // 2 - bbox[1] + dy
    draw.text((x, y), ch, font=font, fill=255, stroke_width=stroke, stroke_fill=255)
    return mask


def draw_glyph_layer(
    font: ImageFont.FreeTypeFont,
    ch: str,
    size: tuple[int, int],
    dx: int,
    dy: int,
    stroke: int,
    fill: tuple[int, int, int, int],
    stroke_fill: tuple[int, int, int, int],
) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bbox = font.getbbox(ch, stroke_width=stroke)
    if bbox is None:
        return layer
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size[0] - tw) // 2 - bbox[0] + dx
    y = (size[1] - th) // 2 - bbox[1] + dy
    draw.text((x, y), ch, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return layer


def paint_cell(
    image: Image.Image,
    cell: GlyphCell,
    font: ImageFont.FreeTypeFont,
    dx: int,
    dy: int,
    stroke: int,
    clear: bool,
    preserve_alpha: bool,
) -> None:
    x0, y0, x1, y1 = cell.box
    tile = image.crop(cell.box).convert("RGBA")
    bg = sample_background(tile)
    if clear:
        tile = Image.new("RGBA", tile.size, bg)

    mask = draw_glyph_mask(font, cell.draw_char, tile.size, dx, dy, stroke)
    if preserve_alpha:
        src_alpha = image.crop(cell.box).convert("RGBA").getchannel("A")
        mask = ImageChops.multiply(mask, src_alpha.point(lambda p: 255 if p else 0))

    if bg[3] == 255 and max(bg[:3]) < 128:
        fill = (0, 0, 0, 255)
        stroke_fill = (255, 255, 255, 255)
    else:
        fill = (255, 255, 255, 255)
        stroke_fill = (0, 0, 0, 255)
    glyph = draw_glyph_layer(font, cell.draw_char, tile.size, dx, dy, stroke, fill, stroke_fill)
    if preserve_alpha:
        glyph.putalpha(mask)
    tile = Image.alpha_composite(tile, glyph)
    image.paste(tile, cell.box)


def verify_diff_inside_cells(before: list[Image.Image], after: list[Image.Image], cells: list[GlyphCell]) -> tuple[bool, list[tuple[int, tuple[int, int, int, int]]]]:
    bad: list[tuple[int, tuple[int, int, int, int]]] = []
    for idx, (a, b) in enumerate(zip(before, after)):
        diff = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA"))
        allowed = Image.new("L", a.size, 0)
        draw = ImageDraw.Draw(allowed)
        for cell in cells:
            draw.rectangle((cell.box[0], cell.box[1], cell.box[2] - 1, cell.box[3] - 1), fill=255)
        outside = ImageChops.multiply(diff.convert("L"), ImageChops.invert(allowed))
        bbox = outside.getbbox()
        if bbox:
            bad.append((idx, bbox))
    return not bad, bad


def count_changed_pixels(before: list[Image.Image], after: list[Image.Image]) -> int:
    changed = 0
    for a, b in zip(before, after):
        diff = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA")).convert("L")
        if hasattr(diff, "get_flattened_data"):
            data = diff.get_flattened_data()
        else:
            data = diff.getdata()
        changed += sum(1 for px in data if px)
    return changed


def write_preview(images: list[Image.Image], out_dir: Path, stem: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, image in enumerate(images, 1):
        image.save(out_dir / f"{stem}_frame{i:02d}.png")


def cmd_inspect(args: argparse.Namespace) -> int:
    dat = Path(args.dat)
    txt = Path(args.txt)
    cls = ClsImage(dat.read_bytes())
    chars = read_text_chars(txt, args.encoding)
    print(f"[信息] DAT: {dat}")
    print(f"[信息] TXT: {txt}")
    print(f"[信息] 字符数: {len(chars)}")
    print(f"[信息] 帧数: {len(cls.frames)}")
    for f in cls.frames:
        print(
            f"  frame#{f.index + 1}: {f.width}x{f.height} fmt={f.format_code} "
            f"channels={f.channels} compressed={int(f.compressed)} off=0x{f.offset:X}"
        )
    cols = args.cols
    cell_w = args.cell_w or infer_cell_width(cls.frames[0].width, cols)
    cell_h = args.cell_h or cell_w
    rows = math.ceil(len(chars) / cols)
    print(f"[信息] 网格: cols={cols}, rows={rows}, cell={cell_w}x{cell_h}, covered={cols * rows}")
    if args.subs:
        rev = load_subs(Path(args.subs))
        hits = sum(1 for ch in chars if ch in rev)
        print(f"[信息] 映射命中: {hits}/{len(chars)}")
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    dat = Path(args.dat)
    txt = Path(args.txt)
    out = Path(args.out)
    subs = Path(args.subs)
    ttf = Path(args.ttf) if args.ttf else find_default_ttf()
    if not dat.exists():
        raise ClsError(f"找不到 DAT: {dat}")
    if not txt.exists():
        raise ClsError(f"找不到 TXT: {txt}")
    if not subs.exists():
        raise ClsError(f"找不到映射表: {subs}")
    if not ttf.exists():
        raise ClsError(f"找不到字体: {ttf}")

    cls = ClsImage(dat.read_bytes())
    chars = read_text_chars(txt, args.encoding)
    rev = load_subs(subs)
    first = cls.frames[0]
    cols = args.cols
    cell_w = args.cell_w or infer_cell_width(first.width, cols)
    cell_h = args.cell_h or cell_w
    max_count = args.max_count
    cells, skipped_not_mapped = build_cells(
        chars,
        rev,
        first.width,
        first.height,
        cols,
        cell_w,
        cell_h,
        args.pad_x,
        args.pad_y,
        max_count,
        args.subs_only,
    )
    if not cells:
        raise ClsError("没有任何字形可重绘")

    font_size = fit_font_size(ttf.resolve(), (c.draw_char for c in cells), args.size, cell_w - 2, cell_h - 2, args.stroke)
    font = ImageFont.truetype(str(ttf.resolve()), font_size)
    before = [cls.decode_frame(f) for f in cls.frames]
    after = [im.copy() for im in before]
    for image in after:
        for cell in cells:
            paint_cell(
                image,
                cell,
                font,
                args.dx,
                args.dy,
                args.stroke,
                clear=not args.no_clear,
                preserve_alpha=args.preserve_alpha,
            )

    ok, bad = verify_diff_inside_cells(before, after, cells)
    if not ok:
        for frame_index, bbox in bad:
            print(f"[错误] frame#{frame_index + 1} 非目标格发生变化: bbox={bbox}", file=sys.stderr)
        return 2

    rebuilt = cls.rebuild_from_images(after)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rebuilt)

    if args.preview_dir:
        preview_dir = Path(args.preview_dir)
        write_preview(before, preview_dir, out.stem + "_before")
        write_preview(after, preview_dir, out.stem + "_after")

    changed_pixels = count_changed_pixels(before, after)

    print(f"[完成] 输出: {out}")
    print(f"[信息] DAT 帧数: {len(cls.frames)}  网格: cols={cols}, cell={cell_w}x{cell_h}")
    mapped_count = sum(1 for cell in cells if cell.slot_char in rev)
    mode = "仅映射表" if args.subs_only else "全部重绘"
    print(f"[信息] TXT 字符数: {len(chars)}  重绘字形: {len(cells)}  映射替换: {mapped_count}  未映射跳过: {skipped_not_mapped}")
    print(f"[信息] 模式: {mode}")
    print(f"[信息] 字体: {ttf.resolve()}  size={font_size} stroke={args.stroke} dx={args.dx} dy={args.dy}")
    print(f"[验证] 非目标格差异: 0  改动像素数: {changed_pixels}")
    print(f"[说明] 输出重新 RLE 压缩，文件大小可能变化；图像内容只在目标字格内变化。")
    return 0


def cmd_clean_work(args: argparse.Namespace) -> int:
    work = Path(args.work)
    if not work.exists():
        print(f"[信息] 不存在: {work}")
        return 0
    for child in work.iterdir():
        if child.name == "__pycache__":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    print(f"[完成] 已清理: {work}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lambda CLS FONT26 位图字体重绘工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--encoding", default=DEFAULT_ENCODING, help="TXT 编码，默认 cp932")
    parent.add_argument("--cols", type=int, default=36, help="字库网格列数，默认 36")
    parent.add_argument("--cell-w", type=int, help="字格宽度，默认 width//cols")
    parent.add_argument("--cell-h", type=int, help="字格高度，默认同 cell-w")
    parent.add_argument("--pad-x", type=int, default=0, help="网格 X 偏移")
    parent.add_argument("--pad-y", type=int, default=0, help="网格 Y 偏移")
    parent.add_argument("--subs", default=DEFAULT_SUBS, help="简体到日繁码位映射 JSON")

    p = sub.add_parser("inspect", parents=[parent], help="检查 DAT/TXT 字库信息")
    p.add_argument("dat")
    p.add_argument("txt")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("rebuild", parents=[parent], help="按映射表重绘指定 CLS 字库")
    p.add_argument("dat")
    p.add_argument("txt")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--ttf", help="用于绘制简体的 TTF，默认使用 错误错误错误.ttf")
    p.add_argument("--size", type=int, default=26, help="字体尺寸，默认 26")
    p.add_argument("--stroke", type=int, default=1, help="描边粗细，默认 1")
    p.add_argument("--dx", type=int, default=0)
    p.add_argument("--dy", type=int, default=0)
    p.add_argument("--max-count", type=int, help="最多处理 TXT 前 N 个字符")
    p.add_argument("--subs-only", action="store_true", help="旧模式：只重绘映射表命中的槽位")
    p.add_argument("--no-clear", action="store_true", help="不清旧字，直接叠画")
    p.add_argument("--preserve-alpha", action="store_true", help="只在原字格有 alpha 的位置绘制")
    p.add_argument("--preview-dir", help="输出 before/after PNG 预览目录")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("clean-work", help="清理 _work 内临时文件，保留 __pycache__")
    p.add_argument("work", nargs="?", default="_work")
    p.set_defaults(func=cmd_clean_work)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ClsError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
