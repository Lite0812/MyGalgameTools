#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import tempfile
from dataclasses import dataclass

import mebius_txt_asm
import mebius_txt_disasm
import mebius_txt_op


RE_HEX_ESCAPE = re.compile(r"^[xX][0-9A-Fa-f]{2}$")
RE_UNICODE_ESCAPE = re.compile(r"^[u][0-9A-Fa-f]{4}$")
RE_UNICODE_LONG_ESCAPE = re.compile(r"^[U][0-9A-Fa-f]{8}$")
RE_OCTAL_ESCAPE = re.compile(r"^[0-7]{1,3}$")
SIMPLE_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    '"': '"',
    "'": "'",
}


@dataclass(slots=True)
class DialogEntry:
    name: str | None
    message: str
    name_line_index: int | None
    message_line_index: int
    is_select: bool
    name_string_index: int | None = None
    message_string_index: int = 0


def _split_comment_suffix(line: str) -> tuple[str, str, str]:
    newline = "\n" if line.endswith("\n") else ""
    body = line[:-1] if newline else line
    in_string = False
    escaped = False
    for index, char in enumerate(body):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == ";":
            return body[:index].rstrip(), body[index:], newline
    return body.rstrip(), "", newline


def _parse_instruction_line(line: str) -> tuple[str, list[str]] | None:
    body, _, _ = _split_comment_suffix(line)
    stripped = body.strip()
    if not stripped or stripped.endswith(":") or stripped.startswith("."):
        return None
    return mebius_txt_asm.parse_instruction_text(body)


def _decode_dialog_string(token: str) -> str:
    token = token.strip()
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        raise ValueError(f"expected quoted string literal, got {token!r}")
    body = token[1:-1]
    result: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue
        index += 1
        if index >= len(body):
            raise ValueError("trailing backslash in string literal")
        escape = body[index]
        if escape in SIMPLE_ESCAPES:
            result.append(SIMPLE_ESCAPES[escape])
            index += 1
            continue
        if escape in {"x", "X"}:
            chunk = body[index : index + 3]
            if not RE_HEX_ESCAPE.fullmatch(chunk):
                raise ValueError(f"invalid hex escape in string literal: \\{chunk}")
            result.append("\\" + chunk)
            index += 3
            continue
        if escape == "u":
            chunk = body[index : index + 5]
            if not RE_UNICODE_ESCAPE.fullmatch(chunk):
                raise ValueError(f"invalid unicode escape in string literal: \\{chunk}")
            result.append("\\" + chunk)
            index += 5
            continue
        if escape == "U":
            chunk = body[index : index + 9]
            if not RE_UNICODE_LONG_ESCAPE.fullmatch(chunk):
                raise ValueError(f"invalid unicode escape in string literal: \\{chunk}")
            result.append("\\" + chunk)
            index += 9
            continue
        if escape in "01234567":
            end = index + 1
            while end < len(body) and end - index < 3 and body[end] in "01234567":
                end += 1
            chunk = body[index:end]
            if not RE_OCTAL_ESCAPE.fullmatch(chunk):
                raise ValueError(f"invalid octal escape in string literal: \\{chunk}")
            result.append("\\" + chunk)
            index = end
            continue
        result.append("\\" + escape)
        index += 1
    return "".join(result)


def _encode_dialog_string(text: str) -> str:
    result: list[str] = ['"']
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            if index + 3 < len(text) and RE_HEX_ESCAPE.fullmatch(text[index + 1 : index + 4]):
                result.append(text[index : index + 4])
                index += 4
                continue
            if index + 5 < len(text) and RE_UNICODE_ESCAPE.fullmatch(text[index + 1 : index + 6]):
                result.append(text[index : index + 6])
                index += 6
                continue
            if index + 9 < len(text) and RE_UNICODE_LONG_ESCAPE.fullmatch(text[index + 1 : index + 10]):
                result.append(text[index : index + 10])
                index += 10
                continue
            octal_end = index + 1
            while octal_end < len(text) and octal_end - index <= 3 and text[octal_end] in "01234567":
                octal_end += 1
            if octal_end > index + 1 and RE_OCTAL_ESCAPE.fullmatch(text[index + 1 : octal_end]):
                result.append(text[index:octal_end])
                index = octal_end
                continue
            result.append("\\\\")
            index += 1
            continue
        if char == '"':
            result.append('\\"')
        elif char == "\n":
            result.append("\\n")
        elif char == "\r":
            result.append("\\r")
        elif char == "\t":
            result.append("\\t")
        elif char == "\a":
            result.append("\\a")
        elif char == "\b":
            result.append("\\b")
        elif char == "\f":
            result.append("\\f")
        elif char == "\v":
            result.append("\\v")
        else:
            result.append(char)
        index += 1
    result.append('"')
    return "".join(result)


def _decode_string_list(token: str) -> list[str]:
    token = token.strip()
    if len(token) < 2 or token[0] != "[" or token[-1] != "]":
        raise ValueError(f"expected string list literal, got {token!r}")
    inner = token[1:-1].strip()
    if not inner:
        return []
    return [_decode_dialog_string(part) for part in mebius_txt_op.split_top_level(inner)]


def _encode_string_list(items: list[str]) -> str:
    return "[" + ", ".join(_encode_dialog_string(item) for item in items) + "]"


def _rebuild_instruction_line(
    line: str,
    mnemonic: str,
    operands: list[str],
) -> str:
    body, comment, newline = _split_comment_suffix(line)
    indent = body[: len(body) - len(body.lstrip())]
    rebuilt = indent + mnemonic
    if operands:
        rebuilt += " " + ", ".join(operands)
    return rebuilt + comment + newline


def _replace_string_operand(line: str, operand_index: int, new_text: str) -> str:
    parsed = _parse_instruction_line(line)
    if parsed is None:
        return line
    mnemonic, operands = parsed
    if operand_index < 0 or operand_index >= len(operands):
        return line
    updated = list(operands)
    updated[operand_index] = _encode_dialog_string(new_text)
    return _rebuild_instruction_line(line, mnemonic, updated)


def _replace_string_list_item(line: str, operand_index: int, item_index: int, new_text: str) -> str:
    parsed = _parse_instruction_line(line)
    if parsed is None:
        return line
    mnemonic, operands = parsed
    if operand_index < 0 or operand_index >= len(operands):
        return line
    items = _decode_string_list(operands[operand_index])
    if item_index < 0 or item_index >= len(items):
        return line
    items[item_index] = new_text
    updated = list(operands)
    updated[operand_index] = _encode_string_list(items)
    return _rebuild_instruction_line(line, mnemonic, updated)


def extract_dialog_entries(lines: list[str]) -> list[DialogEntry]:
    entries: list[DialogEntry] = []
    pending_name: str | None = None
    pending_name_line_index: int | None = None
    for line_index, line in enumerate(lines):
        parsed = _parse_instruction_line(line)
        if parsed is None:
            continue
        mnemonic, operands = parsed
        if mnemonic == "text_ab" and len(operands) == 1:
            pending_name = _decode_dialog_string(operands[0])
            pending_name_line_index = line_index
            continue
        if mnemonic == "text_aa" and len(operands) == 1:
            entries.append(
                DialogEntry(
                    name=pending_name,
                    message=_decode_dialog_string(operands[0]),
                    name_line_index=pending_name_line_index,
                    message_line_index=line_index,
                    is_select=False,
                    name_string_index=0 if pending_name_line_index is not None else None,
                    message_string_index=0,
                )
            )
            pending_name = None
            pending_name_line_index = None
            continue
        if mnemonic == "define_string_table" and len(operands) == 3:
            for option_index, option in enumerate(_decode_string_list(operands[2])):
                entries.append(
                    DialogEntry(
                        name=None,
                        message=option,
                        name_line_index=None,
                        message_line_index=line_index,
                        is_select=True,
                        name_string_index=None,
                        message_string_index=option_index,
                    )
                )
    return entries


def extract_dialog_json_from_asm(input_path: str | os.PathLike[str], output_json: str | os.PathLike[str]) -> int:
    with open(input_path, "r", encoding="utf-8") as handle:
        lines = handle.readlines()
    entries = extract_dialog_entries(lines)
    payload: list[dict[str, str]] = []
    for entry in entries:
        if entry.name:
            item: dict[str, str] = {"name": entry.name, "message": entry.message}
        else:
            item = {"message": entry.message}
        payload.append(item)
    output_path = pathlib.Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(entries)


def _load_dialog_json_items(input_json: str | os.PathLike[str]) -> list[dict[str, object]]:
    raw_text = pathlib.Path(input_json).read_text(encoding="utf-8")
    if not raw_text.strip():
        return []
    items = json.loads(raw_text)
    if not isinstance(items, list):
        raise Exception("JSON 格式错误: 需要数组格式")
    return items


def import_dialog_json_to_asm(
    input_asm: str | os.PathLike[str],
    input_json: str | os.PathLike[str],
    output_asm: str | os.PathLike[str],
) -> tuple[int, int]:
    with open(input_asm, "r", encoding="utf-8") as handle:
        lines = handle.readlines()
    items = _load_dialog_json_items(input_json)
    entries = extract_dialog_entries(lines)
    if not items:
        output_path = pathlib.Path(output_asm)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(lines), encoding="utf-8")
        return len(entries), 0
    if len(items) != len(entries):
        raise Exception(f"JSON 条目数与对话条目数不一致: json={len(items)} asm={len(entries)}")
    applied = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise Exception(f"第 {index} 项不是对象")
        unknown_keys = set(item.keys()) - {"name", "message"}
        if unknown_keys:
            raise Exception(f"第 {index} 项包含非法字段: {sorted(unknown_keys)}")
        if "message" not in item:
            raise Exception(f"第 {index} 项缺少 message")
        entry = entries[index]
        if "name" in item and entry.name_line_index is not None:
            lines[entry.name_line_index] = _replace_string_operand(lines[entry.name_line_index], 0, str(item["name"]))
            applied += 1
        if entry.is_select:
            lines[entry.message_line_index] = _replace_string_list_item(
                lines[entry.message_line_index],
                2,
                entry.message_string_index,
                str(item["message"]),
            )
        else:
            lines[entry.message_line_index] = _replace_string_operand(
                lines[entry.message_line_index],
                0,
                str(item["message"]),
            )
        applied += 1
    output_path = pathlib.Path(output_asm)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(lines), encoding="utf-8")
    return len(entries), applied


def extract_dialog_json_from_script(
    input_script: str | os.PathLike[str],
    output_json: str | os.PathLike[str],
    encoding: str = mebius_txt_op.DEFAULT_TEXT_ENCODING,
) -> int:
    with tempfile.TemporaryDirectory(prefix="mebius_json_extract_") as temp_dir:
        temp_asm = pathlib.Path(temp_dir) / (pathlib.Path(input_script).name + ".asm.txt")
        mebius_txt_disasm.disassemble_file(pathlib.Path(input_script), temp_asm, text_encoding=encoding)
        return extract_dialog_json_from_asm(temp_asm, output_json)


def import_dialog_json_to_script(
    input_script: str | os.PathLike[str],
    input_json: str | os.PathLike[str],
    output_script: str | os.PathLike[str],
    encoding: str = mebius_txt_op.DEFAULT_TEXT_ENCODING,
    source_encoding: str | None = None,
) -> tuple[int, int]:
    disasm_encoding = source_encoding or encoding
    with tempfile.TemporaryDirectory(prefix="mebius_json_import_") as temp_dir:
        temp_dir_path = pathlib.Path(temp_dir)
        temp_src_asm = temp_dir_path / (pathlib.Path(input_script).name + ".src.asm.txt")
        temp_out_asm = temp_dir_path / (pathlib.Path(input_script).name + ".out.asm.txt")
        mebius_txt_disasm.disassemble_file(pathlib.Path(input_script), temp_src_asm, text_encoding=disasm_encoding)
        count, applied = import_dialog_json_to_asm(temp_src_asm, input_json, temp_out_asm)
        mebius_txt_asm.assemble_file(temp_out_asm, pathlib.Path(output_script), text_encoding=encoding)
        return count, applied


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract or import MEBIUS dialog JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="extract dialog JSON from a TXT script")
    extract_parser.add_argument("input_script", help="input TXT script path")
    extract_parser.add_argument("output_json", help="output JSON path")
    extract_parser.add_argument(
        "-e",
        "--encoding",
        default=mebius_txt_op.DEFAULT_TEXT_ENCODING,
        help="source script text encoding (default: %(default)s)",
    )

    import_parser = subparsers.add_parser("import", help="import dialog JSON into a TXT script")
    import_parser.add_argument("input_script", help="original TXT script path")
    import_parser.add_argument("input_json", help="edited JSON path")
    import_parser.add_argument("output_script", help="output TXT script path")
    import_parser.add_argument(
        "-e",
        "--encoding",
        default=mebius_txt_op.DEFAULT_TEXT_ENCODING,
        help="output script text encoding (default: %(default)s)",
    )
    import_parser.add_argument(
        "--source-encoding",
        help="original script text encoding; defaults to output encoding",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "extract":
        count = extract_dialog_json_from_script(args.input_script, args.output_json, encoding=args.encoding)
        print(f"extracted {count} dialog entries")
        return 0
    count, applied = import_dialog_json_to_script(
        args.input_script,
        args.input_json,
        args.output_script,
        encoding=args.encoding,
        source_encoding=args.source_encoding,
    )
    print(f"imported {applied} fields across {count} dialog entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())