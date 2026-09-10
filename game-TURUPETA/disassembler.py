#!/usr/bin/env python3
"""Disassemble WS2 script packages to semantic asm text."""

from __future__ import annotations

import argparse
import bz2
import os
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

from opcodelist import COMPRESSION_HEADER_SIZE, FORMAT_COPY, FORMAT_COPY_ALIAS, FORMAT_XB_BZIP2, FORMAT_XG_ZLIB, OPCODES, OpcodeDef, opcode_from_bytes


DEFAULT_ENCODING = "cp932"


@dataclass
class Ws2String:
    data: bytes
    raw_size: int


@dataclass
class IndexEntry:
    script_id: int
    offsets: list[int]


@dataclass
class Package:
    header: bytes
    format_id: int
    unpacked: bytes
    string_table_offset: int
    index_table_offset: int
    strings: list[bytes]
    string_table_extra: bytes
    index_entries: list[IndexEntry]


def u32le(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def u16le(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def decompress_ws2(raw: bytes) -> tuple[bytes, int, bytes]:
    if len(raw) < COMPRESSION_HEADER_SIZE:
        raise ValueError("input is too small for a 0x20-byte compression header")
    header = raw[:COMPRESSION_HEADER_SIZE]
    format_id = u32le(header, 0)
    unpacked_size = u32le(header, 4)
    payload = raw[COMPRESSION_HEADER_SIZE:]
    if format_id in (FORMAT_COPY, FORMAT_COPY_ALIAS):
        unpacked = payload
    elif format_id == FORMAT_XB_BZIP2:
        unpacked = bz2.decompress(payload)
    elif format_id == FORMAT_XG_ZLIB:
        unpacked = zlib.decompress(payload)
    else:
        raise ValueError(f"unsupported compression format_id {format_id}")
    if len(unpacked) != unpacked_size:
        raise ValueError(f"unpacked size mismatch: header says {unpacked_size}, got {len(unpacked)}")
    return unpacked, format_id, header


def read_ws2_string(data: bytes, offset: int, limit: int) -> Ws2String | None:
    if offset + 2 > limit:
        return None
    length = u16le(data, offset)
    if length == 0:
        return Ws2String(b"", 2)
    end = offset + 2 + length + 1
    if end > limit:
        return None
    if data[offset + 2 + length] != 0:
        return None
    return Ws2String(data[offset + 2:offset + 2 + length], 2 + length + 1)


def parse_package(unpacked: bytes, header: bytes, format_id: int) -> Package:
    if len(unpacked) < 8:
        raise ValueError("unpacked image is too small for WS2 footer")
    string_table_offset = u32le(unpacked, len(unpacked) - 8)
    index_table_offset = u32le(unpacked, len(unpacked) - 4)
    if not (0 <= string_table_offset <= index_table_offset <= len(unpacked) - 8):
        raise ValueError(
            f"invalid table offsets: string=0x{string_table_offset:08X}, index=0x{index_table_offset:08X}, size=0x{len(unpacked):08X}"
        )

    strings: list[bytes] = []
    cursor = string_table_offset
    if cursor >= index_table_offset:
        raise ValueError("string table does not contain the count byte")
    string_count = unpacked[cursor]
    cursor += 1
    for _ in range(string_count):
        item = read_ws2_string(unpacked, cursor, index_table_offset)
        if item is None:
            raise ValueError(f"invalid string table entry at 0x{cursor:08X}")
        strings.append(item.data)
        cursor += item.raw_size
    string_table_extra = unpacked[cursor:index_table_offset]

    index_entries: list[IndexEntry] = []
    cursor = index_table_offset
    while cursor < len(unpacked) - 8:
        if cursor + 8 > len(unpacked) - 8:
            raise ValueError(f"truncated index entry at 0x{cursor:08X}")
        script_id = u32le(unpacked, cursor)
        count = u32le(unpacked, cursor + 4)
        cursor += 8
        byte_count = count * 4
        if cursor + byte_count > len(unpacked) - 8:
            raise ValueError(f"truncated index offsets for script {script_id}")
        offsets = [u32le(unpacked, cursor + i * 4) for i in range(count)]
        for off in offsets:
            if off >= string_table_offset:
                raise ValueError(f"index offset 0x{off:08X} for script {script_id} points outside script body")
        index_entries.append(IndexEntry(script_id, offsets))
        cursor += byte_count
    if cursor != len(unpacked) - 8:
        raise ValueError("index table did not end at footer")

    return Package(header, format_id, unpacked, string_table_offset, index_table_offset, strings, string_table_extra, index_entries)


def is_safe_char(ch: str) -> bool:
    code = ord(ch)
    if ch in {'"', '{', '}'}:
        return False
    if code < 0x20 or code == 0x7F:
        return False
    if 0xE000 <= code <= 0xF8FF:
        return False
    return True


def render_bytes_as_string(data: bytes, encoding: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(data):
        if data[i] < 0x20 or data[i] == 0x7F:
            out.append(f"{{{{{data[i]:02X}}}}}")
            i += 1
            continue
        decoded = None
        used = 0
        for width in range(1, min(4, len(data) - i) + 1):
            chunk = data[i:i + width]
            try:
                text = chunk.decode(encoding)
            except UnicodeDecodeError:
                continue
            try:
                if text.encode(encoding) != chunk:
                    continue
            except UnicodeEncodeError:
                continue
            decoded = text
            used = width
            break
        if decoded is None or len(decoded) != 1 or not is_safe_char(decoded):
            out.append(f"{{{{{data[i]:02X}}}}}")
            i += 1
            continue
        out.append(decoded)
        i += used
    return '"' + "".join(out) + '"'


def label_for(offset: int) -> str:
    return f"loc_{offset:08X}"


def parse_instruction(data: bytes, offset: int, limit: int) -> tuple[OpcodeDef, list[object], int] | None:
    definition = opcode_from_bytes(data, offset)
    if definition is None:
        return None
    cursor = offset + len(definition.code)
    operands: list[object] = []
    for schema in definition.operands:
        typ = schema["type"]
        if typ == "u8":
            if cursor + 1 > limit:
                return None
            operands.append(data[cursor])
            cursor += 1
        elif typ == "i8":
            if cursor + 1 > limit:
                return None
            value = data[cursor]
            operands.append(value - 0x100 if value >= 0x80 else value)
            cursor += 1
        elif typ == "u16":
            if cursor + 2 > limit:
                return None
            operands.append(u16le(data, cursor))
            cursor += 2
        elif typ in ("u32", "script_id"):
            if cursor + 4 > limit:
                return None
            operands.append(u32le(data, cursor))
            cursor += 4
        elif typ == "ws2_string":
            item = read_ws2_string(data, cursor, limit)
            if item is None:
                return None
            operands.append(item.data)
            cursor += item.raw_size
        elif typ == "string_list_u8":
            if cursor + 1 > limit:
                return None
            count = data[cursor]
            cursor += 1
            values: list[bytes] = []
            for _ in range(count):
                item = read_ws2_string(data, cursor, limit)
                if item is None:
                    return None
                values.append(item.data)
                cursor += item.raw_size
            operands.append(values)
        else:
            raise ValueError(f"unknown operand type {typ}")
    return definition, operands, cursor - offset


def collect_labels(pkg: Package) -> set[int]:
    labels = {0, pkg.string_table_offset, pkg.index_table_offset, len(pkg.unpacked) - 8}
    for entry in pkg.index_entries:
        labels.update(entry.offsets)
    return {off for off in labels if 0 <= off < len(pkg.unpacked)}


def script_default_labels(pkg: Package) -> dict[int, str]:
    result: dict[int, str] = {}
    for entry in pkg.index_entries:
        if entry.offsets:
            result[entry.script_id] = label_for(entry.offsets[0])
    return result


def render_operand(definition: OpcodeDef, schema: dict[str, object], value: object, encoding: str, script_labels: dict[int, str]) -> str:
    typ = schema["type"]
    if typ == "i8":
        return str(value)
    if typ in ("u8", "u16"):
        return f"0x{int(value):02X}" if typ == "u8" else f"0x{int(value):04X}"
    if typ == "u32":
        return f"0x{int(value):08X}"
    if typ == "script_id":
        number = int(value)
        if definition.mnemonic in {"GOTO_LABEL", "CALL_LABEL"} and number in script_labels:
            return script_labels[number]
        return f"0x{number:08X}"
    if typ == "ws2_string":
        return render_bytes_as_string(value, encoding)  # type: ignore[arg-type]
    if typ == "string_list_u8":
        return "[" + ", ".join(render_bytes_as_string(v, encoding) for v in value) + "]"  # type: ignore[union-attr]
    raise ValueError(f"unknown operand type {typ}")


def render_instruction(definition: OpcodeDef, operands: list[object], encoding: str, script_labels: dict[int, str]) -> str:
    rendered = [render_operand(definition, schema, value, encoding, script_labels) for schema, value in zip(definition.operands, operands)]
    if rendered:
        return f"    {definition.mnemonic} " + ", ".join(rendered)
    return f"    {definition.mnemonic}"


def render_byte_line(chunk: bytes) -> str:
    return "    .byte " + ", ".join(f"0x{b:02X}" for b in chunk)


def render_disassembly(pkg: Package, encoding: str) -> str:
    labels = collect_labels(pkg)
    sorted_labels = sorted(labels)
    body_stop = pkg.string_table_offset
    offset_labels = {off for off in sorted_labels if off < body_stop}
    script_labels = script_default_labels(pkg)

    lines: list[str] = []
    lines.append("; WS2 semantic asm generated from VM analysis")
    lines.append(f'.encoding "{encoding}"')
    codec_header = ", ".join(f"0x{b:02X}" for b in pkg.header[8:])
    lines.append(f".ws2_header format={pkg.format_id}, unpacked_size=0x{len(pkg.unpacked):08X}, codec_header=[{codec_header}]")
    lines.append("")
    lines.append(".section body")

    sorted_body_labels = sorted(offset_labels | {body_stop})
    label_index = 0
    pos = 0
    while pos < body_stop:
        while label_index < len(sorted_body_labels) and sorted_body_labels[label_index] < pos:
            label_index += 1
        if label_index < len(sorted_body_labels) and sorted_body_labels[label_index] == pos:
            if pos in offset_labels:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(f"{label_for(pos)}:")
            label_index += 1
        next_label = sorted_body_labels[label_index] if label_index < len(sorted_body_labels) else body_stop
        parsed = parse_instruction(pkg.unpacked, pos, min(next_label, body_stop))
        if parsed is not None:
            definition, operands, size = parsed
            lines.append(render_instruction(definition, operands, encoding, script_labels))
            pos += size
        else:
            end = min(next_label, body_stop, pos + 16)
            lines.append(render_byte_line(pkg.unpacked[pos:end]))
            pos = end

    if lines and lines[-1] != "":
        lines.append("")
    lines.append(f"{label_for(pkg.string_table_offset)}:")
    lines.append(f".string_table count={len(pkg.strings)}")
    for value in pkg.strings:
        lines.append(f"    .ws2_string {render_bytes_as_string(value, encoding)}")
    if pkg.string_table_extra:
        lines.append(render_byte_line(pkg.string_table_extra))

    lines.append("")
    lines.append(f"{label_for(pkg.index_table_offset)}:")
    for entry in pkg.index_entries:
        labels_text = ", ".join(label_for(off) for off in entry.offsets)
        lines.append(f".index {entry.script_id}, [{labels_text}]")

    footer_offset = len(pkg.unpacked) - 8
    lines.append("")
    lines.append(f"{label_for(footer_offset)}:")
    lines.append(f".ws2_footer string_table={label_for(pkg.string_table_offset)}, index_table={label_for(pkg.index_table_offset)}")
    lines.append("")
    return "\n".join(lines)


def disassemble_file(input_path: Path, output_path: Path | None, encoding: str) -> Path:
    raw = input_path.read_bytes()
    unpacked, format_id, header = decompress_ws2(raw)
    pkg = parse_package(unpacked, header, format_id)
    text = render_disassembly(pkg, encoding)
    if output_path is None:
        output_path = input_path.with_name(input_path.stem + ".asm.txt")
    output_path.write_text(text, encoding="utf-8", newline="\n")
    return output_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Disassemble WS2 script package(s) to asm text.")
    parser.add_argument("inputs", nargs="+", help="input .ws2 script file(s)")
    parser.add_argument("-o", "--output", help="output asm path; only valid with one input")
    parser.add_argument("--encoding", default=DEFAULT_ENCODING, help=f"script text encoding (default: {DEFAULT_ENCODING})")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.output and len(args.inputs) != 1:
        parser.error("-o/--output can only be used with a single input")
    for name in args.inputs:
        input_path = Path(name)
        output_path = Path(args.output) if args.output else None
        written = disassemble_file(input_path, output_path, args.encoding)
        print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
