#!/usr/bin/env python3
"""Assemble WS2 semantic asm text back to a compressed script package."""

from __future__ import annotations

import argparse
import ast
import bz2
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

from opcodelist import (
    COMPRESSION_HEADER_SIZE,
    FORMAT_COPY,
    FORMAT_COPY_ALIAS,
    FORMAT_XB_BZIP2,
    FORMAT_XG_ZLIB,
    MNEMONIC_TO_OPCODE,
    OpcodeDef,
)


DEFAULT_ENCODING = "cp932"


@dataclass
class HeaderInfo:
    format_id: int = FORMAT_XB_BZIP2
    unpacked_size: int | None = None
    codec_header: bytes = b"\x00" * 24


@dataclass
class Statement:
    kind: str
    value: object = None
    line_no: int = 0


@dataclass
class Instruction:
    definition: OpcodeDef
    operands: list[object]
    line_no: int


@dataclass
class ByteData:
    data: bytes
    line_no: int


@dataclass
class IndexLine:
    script_id: int
    labels: list[str]
    line_no: int


@dataclass
class ParsedAsm:
    encoding: str | None
    header: HeaderInfo
    body: list[Statement]
    strings: list[bytes]
    string_table_extra: bytes
    indexes: list[IndexLine]


def pack_u16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"u16 operand out of range: {value}")
    return struct.pack("<H", value)


def pack_u32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"u32 operand out of range: {value}")
    return struct.pack("<I", value)


def strip_comment(line: str) -> str:
    in_quote = False
    i = 0
    while i < len(line):
        if not in_quote and line.startswith("{{", i):
            end = line.find("}}", i + 2)
            if end == -1:
                return line
            i = end + 2
            continue
        ch = line[i]
        if ch == '"':
            in_quote = not in_quote
        elif ch == ";" and not in_quote:
            return line[:i]
        i += 1
    return line


def split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_quote = False
    i = 0
    while i < len(text):
        if in_quote and text.startswith("{{", i):
            end = text.find("}}", i + 2)
            if end == -1:
                raise ValueError("unterminated placeholder")
            i = end + 2
            continue
        ch = text[i]
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth < 0:
                    raise ValueError("unmatched ]")
            elif ch == "," and depth == 0:
                parts.append(text[start:i].strip())
                start = i + 1
        i += 1
    if in_quote:
        raise ValueError("unterminated string literal")
    if depth != 0:
        raise ValueError("unmatched [")
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def parse_int(text: str) -> int:
    return int(text.strip(), 0)


def extract_quoted(text: str) -> str:
    text = text.strip()
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        raise ValueError(f"expected quoted string, got {text!r}")
    return text[1:-1]


def parse_asm_string(text: str, encoding: str) -> bytes:
    body = extract_quoted(text)
    out = bytearray()
    pos = 0
    while pos < len(body):
        if body.startswith("{{", pos):
            end = body.find("}}", pos + 2)
            if end == -1:
                raise ValueError("unterminated {{ }} placeholder")
            spec = body[pos + 2:end]
            if not re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})*", spec):
                raise ValueError(f"invalid byte placeholder {{{{{spec}}}}}")
            out.extend(int(part, 16) for part in spec.split(":"))
            pos = end + 2
        else:
            out.extend(body[pos].encode(encoding))
            pos += 1
    return bytes(out)


def encode_ws2_string(data: bytes) -> bytes:
    if len(data) > 0xFFFF:
        raise ValueError("WS2_STRING is too long")
    if not data:
        return b"\x00\x00"
    return pack_u16(len(data)) + data + b"\x00"


def parse_string_list(text: str, encoding: str) -> list[bytes]:
    text = text.strip()
    if len(text) < 2 or text[0] != "[" or text[-1] != "]":
        raise ValueError(f"expected string list, got {text!r}")
    inner = text[1:-1].strip()
    if not inner:
        return []
    return [parse_asm_string(part, encoding) for part in split_top_level_commas(inner)]


def parse_header(line: str) -> HeaderInfo:
    # Example: .ws2_header format=2, unpacked_size=0x001BE038, codec_header=[...]
    fields = {}
    rest = line[len(".ws2_header"):].strip()
    for part in split_top_level_commas(rest):
        if "=" not in part:
            raise ValueError(f"invalid .ws2_header field {part!r}")
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    header = HeaderInfo()
    if "format" in fields:
        header.format_id = parse_int(fields["format"])
    if "unpacked_size" in fields:
        header.unpacked_size = parse_int(fields["unpacked_size"])
    if "codec_header" in fields:
        items = ast.literal_eval(fields["codec_header"])
        if not isinstance(items, list):
            raise ValueError("codec_header must be a list")
        header.codec_header = bytes(int(x) & 0xFF for x in items)
        if len(header.codec_header) != 24:
            raise ValueError("codec_header must contain 24 bytes")
    return header


def parse_operands(definition: OpcodeDef, text: str, encoding: str) -> list[object]:
    parts = split_top_level_commas(text) if text.strip() else []
    if len(parts) != len(definition.operands):
        raise ValueError(f"{definition.mnemonic} expects {len(definition.operands)} operands, got {len(parts)}")
    operands: list[object] = []
    for schema, part in zip(definition.operands, parts):
        typ = schema["type"]
        if typ in {"u8", "u16", "u32", "i8"}:
            operands.append(parse_int(part))
        elif typ == "script_id":
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part.strip()):
                operands.append(part.strip())
            else:
                operands.append(parse_int(part))
        elif typ == "ws2_string":
            operands.append(parse_asm_string(part, encoding))
        elif typ == "string_list_u8":
            operands.append(parse_string_list(part, encoding))
        else:
            raise ValueError(f"unknown operand type {typ}")
    return operands


def parse_asm(path: Path, cli_encoding: str | None) -> ParsedAsm:
    text = path.read_text(encoding="utf-8-sig")
    encoding = cli_encoding
    header = HeaderInfo()
    body: list[Statement] = []
    strings: list[bytes] = []
    string_table_extra = bytearray()
    indexes: list[IndexLine] = []
    section = "body"

    # First pass only needs the .encoding directive for string decoding.
    if encoding is None:
        for raw in text.splitlines():
            line = strip_comment(raw).strip()
            if line.startswith(".encoding"):
                encoding = extract_quoted(line[len(".encoding"):].strip())
                break
    if encoding is None:
        encoding = DEFAULT_ENCODING

    for line_no, raw in enumerate(text.splitlines(), 1):
        line = strip_comment(raw).strip()
        if not line:
            continue
        try:
            if line.startswith(".encoding"):
                continue
            if line.startswith(".ws2_header"):
                header = parse_header(line)
                continue
            if line.startswith(".section"):
                fields = line.split()
                if len(fields) != 2:
                    raise ValueError(".section expects one name")
                section = fields[1]
                continue
            if line.endswith(":"):
                label = line[:-1].strip()
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", label):
                    raise ValueError(f"invalid label {label!r}")
                body.append(Statement("label", label, line_no))
                continue
            if line.startswith(".byte"):
                data = bytes(parse_int(part) & 0xFF for part in split_top_level_commas(line[len(".byte"):].strip()))
                if section == "body":
                    body.append(Statement("bytes", ByteData(data, line_no), line_no))
                elif section == "string_table":
                    string_table_extra.extend(data)
                else:
                    raise ValueError(".byte is only supported in body or string_table section")
                continue
            if line.startswith(".string_table"):
                section = "string_table"
                continue
            if line.startswith(".ws2_string"):
                if section != "string_table":
                    raise ValueError(".ws2_string must follow .string_table")
                strings.append(parse_asm_string(line[len(".ws2_string"):].strip(), encoding))
                continue
            if line.startswith(".index"):
                section = "index_table"
                rest = line[len(".index"):].strip()
                parts = split_top_level_commas(rest)
                if len(parts) != 2:
                    raise ValueError(".index expects script_id and [labels]")
                script_id = parse_int(parts[0])
                label_text = parts[1].strip()
                if len(label_text) < 2 or label_text[0] != "[" or label_text[-1] != "]":
                    raise ValueError(".index second operand must be [labels]")
                inner = label_text[1:-1].strip()
                labels = [p.strip() for p in split_top_level_commas(inner)] if inner else []
                for label in labels:
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", label):
                        raise ValueError(f"invalid .index label {label!r}")
                indexes.append(IndexLine(script_id, labels, line_no))
                continue
            if line.startswith(".ws2_footer"):
                section = "footer"
                continue
            mnemonic, _, rest = line.partition(" ")
            definition = MNEMONIC_TO_OPCODE.get(mnemonic)
            if definition is None:
                raise ValueError(f"unknown directive or instruction {mnemonic!r}")
            if section != "body":
                raise ValueError(f"instruction {mnemonic} appears outside body section")
            body.append(Statement("instruction", Instruction(definition, parse_operands(definition, rest, encoding), line_no), line_no))
        except Exception as exc:
            raise ValueError(f"line {line_no}: {exc}") from exc

    return ParsedAsm(encoding, header, body, strings, bytes(string_table_extra), indexes)


def instruction_size(ins: Instruction) -> int:
    size = len(ins.definition.code)
    for schema, value in zip(ins.definition.operands, ins.operands):
        typ = schema["type"]
        if typ in {"u8", "i8"}:
            size += 1
        elif typ == "u16":
            size += 2
        elif typ in {"u32", "script_id"}:
            size += 4
        elif typ == "ws2_string":
            size += len(encode_ws2_string(value))  # type: ignore[arg-type]
        elif typ == "string_list_u8":
            values = value  # type: ignore[assignment]
            if len(values) > 0xFF:
                raise ValueError(f"line {ins.line_no}: string list has more than 255 items")
            size += 1 + sum(len(encode_ws2_string(v)) for v in values)  # type: ignore[union-attr]
    return size


def assign_body_offsets(body: list[Statement]) -> tuple[dict[str, int], int]:
    labels: dict[str, int] = {}
    offset = 0
    for stmt in body:
        if stmt.kind == "label":
            label = stmt.value  # type: ignore[assignment]
            if label in labels:
                raise ValueError(f"line {stmt.line_no}: duplicate label {label}")
            labels[label] = offset
        elif stmt.kind == "bytes":
            offset += len(stmt.value.data)  # type: ignore[union-attr]
        elif stmt.kind == "instruction":
            offset += instruction_size(stmt.value)  # type: ignore[arg-type]
    return labels, offset


def encode_instruction(ins: Instruction, label_to_script: dict[str, int]) -> bytes:
    out = bytearray(ins.definition.code)
    for schema, value in zip(ins.definition.operands, ins.operands):
        typ = schema["type"]
        if typ == "u8":
            if not 0 <= int(value) <= 0xFF:
                raise ValueError(f"line {ins.line_no}: u8 operand out of range")
            out.append(int(value))
        elif typ == "i8":
            ivalue = int(value)
            if not -128 <= ivalue <= 127:
                raise ValueError(f"line {ins.line_no}: i8 operand out of range")
            out.append(ivalue & 0xFF)
        elif typ == "u16":
            out.extend(pack_u16(int(value)))
        elif typ == "u32":
            out.extend(pack_u32(int(value)))
        elif typ == "script_id":
            if isinstance(value, str):
                if value not in label_to_script:
                    raise ValueError(f"line {ins.line_no}: label {value} is not a default script entry in .index")
                out.extend(pack_u32(label_to_script[value]))
            else:
                out.extend(pack_u32(int(value)))
        elif typ == "ws2_string":
            out.extend(encode_ws2_string(value))  # type: ignore[arg-type]
        elif typ == "string_list_u8":
            values = value  # type: ignore[assignment]
            if len(values) > 0xFF:
                raise ValueError(f"line {ins.line_no}: string list has more than 255 items")
            out.append(len(values))  # type: ignore[arg-type]
            for item in values:  # type: ignore[union-attr]
                out.extend(encode_ws2_string(item))
        else:
            raise ValueError(f"line {ins.line_no}: unknown operand type {typ}")
    return bytes(out)


def build_unpacked(parsed: ParsedAsm) -> bytes:
    body_labels, body_size = assign_body_offsets(parsed.body)

    # The default label of each index entry maps back to the script_id for label operands.
    label_to_script: dict[str, int] = {}
    for index in parsed.indexes:
        if index.labels:
            label_to_script.setdefault(index.labels[0], index.script_id)

    body = bytearray()
    for stmt in parsed.body:
        if stmt.kind == "label":
            continue
        if stmt.kind == "bytes":
            body.extend(stmt.value.data)  # type: ignore[union-attr]
        elif stmt.kind == "instruction":
            body.extend(encode_instruction(stmt.value, label_to_script))  # type: ignore[arg-type]
    if len(body) != body_size:
        raise ValueError("internal body size mismatch")

    string_table_offset = len(body)
    if len(parsed.strings) > 0xFF:
        raise ValueError("string table has more than 255 entries")
    string_table = bytearray([len(parsed.strings)])
    for item in parsed.strings:
        string_table.extend(encode_ws2_string(item))
    string_table.extend(parsed.string_table_extra)

    index_table_offset = string_table_offset + len(string_table)
    index_table = bytearray()
    for index in parsed.indexes:
        index_table.extend(pack_u32(index.script_id))
        index_table.extend(pack_u32(len(index.labels)))
        for label in index.labels:
            if label not in body_labels:
                raise ValueError(f"line {index.line_no}: .index references unknown body label {label}")
            index_table.extend(pack_u32(body_labels[label]))

    unpacked = bytes(body) + bytes(string_table) + bytes(index_table) + pack_u32(string_table_offset) + pack_u32(index_table_offset)
    if parsed.header.unpacked_size is not None and parsed.header.unpacked_size != len(unpacked):
        # Text edits can legitimately change the image size.  The header value is metadata from the source asm,
        # so update it instead of rejecting the rebuild.
        pass
    return unpacked


def compress_ws2(unpacked: bytes, header_info: HeaderInfo) -> bytes:
    format_id = header_info.format_id
    if format_id in (FORMAT_COPY, FORMAT_COPY_ALIAS):
        payload = unpacked
    elif format_id == FORMAT_XB_BZIP2:
        payload = bz2.compress(unpacked, compresslevel=9)
    elif format_id == FORMAT_XG_ZLIB:
        payload = zlib.compress(unpacked, level=9)
    else:
        raise ValueError(f"unsupported compression format_id {format_id}")
    header = pack_u32(format_id) + pack_u32(len(unpacked)) + header_info.codec_header
    if len(header) != COMPRESSION_HEADER_SIZE:
        raise ValueError("internal header size mismatch")
    return header + payload


def assemble_file(input_path: Path, output_path: Path | None, encoding: str | None) -> Path:
    parsed = parse_asm(input_path, encoding)
    unpacked = build_unpacked(parsed)
    raw = compress_ws2(unpacked, parsed.header)
    if output_path is None:
        output_path = input_path.with_suffix(input_path.suffix + ".rebuild")
    output_path.write_bytes(raw)
    return output_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assemble WS2 asm text back to a compressed script package.")
    parser.add_argument("inputs", nargs="+", help="input asm file(s)")
    parser.add_argument("-o", "--output", help="output binary path; only valid with one input")
    parser.add_argument("--encoding", help="script text encoding; overrides .encoding")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.output and len(args.inputs) != 1:
        parser.error("-o/--output can only be used with a single input")
    for name in args.inputs:
        input_path = Path(name)
        output_path = Path(args.output) if args.output else None
        written = assemble_file(input_path, output_path, args.encoding)
        print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
