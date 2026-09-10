#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import pathlib
import re
import tempfile

import mebius_txt_asm
import mebius_txt_disasm
import mebius_txt_op
from mebius_dialog_json import (
    _replace_string_list_item,
    _replace_string_operand,
    extract_dialog_entries,
)


RE_TARGET_LINE = re.compile(r"^\s*★(\d{6})([NTS])★(.*)$")
RE_TEXT_ESCAPE = re.compile(r"\\([\\nrt])")


def _escape_txt_text(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _unescape_txt_text(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        value = match.group(1)
        if value == "n":
            return "\n"
        if value == "r":
            return "\r"
        if value == "t":
            return "\t"
        return "\\"

    return RE_TEXT_ESCAPE.sub(repl, str(text))


def _build_units(lines: list[str]) -> list[dict[str, object]]:
    units: list[dict[str, object]] = []
    for entry in extract_dialog_entries(lines):
        if entry.name:
            units.append(
                {
                    "type": "N",
                    "text": entry.name,
                    "line_index": entry.name_line_index,
                    "string_index": entry.name_string_index,
                    "is_select": False,
                }
            )
        units.append(
            {
                "type": "S" if entry.is_select else "T",
                "text": entry.message,
                "line_index": entry.message_line_index,
                "string_index": entry.message_string_index,
                "is_select": entry.is_select,
            }
        )
    return units


def extract_dialog_txt_from_asm(input_path: str | os.PathLike[str], output_txt: str | os.PathLike[str]) -> tuple[int, int]:
    with open(input_path, "r", encoding="utf-8") as handle:
        lines = handle.readlines()
    units = _build_units(lines)
    out_lines: list[str] = []
    for index, unit in enumerate(units, start=1):
        tag = f"{index:06d}{unit['type']}"
        src_text = _escape_txt_text(str(unit["text"]))
        out_lines.append(f"☆{tag}☆{src_text}\n")
        out_lines.append(f"★{tag}★{src_text}\n")
        out_lines.append("\n")
    output_path = pathlib.Path(output_txt)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(out_lines), encoding="utf-8")
    return len(extract_dialog_entries(lines)), len(units)


def import_dialog_txt_to_asm(
    input_asm: str | os.PathLike[str],
    input_txt: str | os.PathLike[str],
    output_asm: str | os.PathLike[str],
) -> tuple[int, int, int]:
    with open(input_asm, "r", encoding="utf-8") as handle:
        lines = handle.readlines()
    entries = extract_dialog_entries(lines)
    units = _build_units(lines)
    txt_lines = pathlib.Path(input_txt).read_text(encoding="utf-8").splitlines()
    if txt_lines:
        txt_lines[0] = txt_lines[0].lstrip("\ufeff")

    target_items: list[dict[str, object]] = []
    for line in txt_lines:
        match = RE_TARGET_LINE.match(line)
        if not match:
            continue
        target_items.append(
            {
                "id": int(match.group(1)),
                "type": match.group(2),
                "text": _unescape_txt_text(match.group(3)),
            }
        )
    if not target_items:
        output_path = pathlib.Path(output_asm)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(lines), encoding="utf-8")
        return len(entries), 0, len(units)
    if len(target_items) != len(units):
        raise Exception(f"TXT 条目数与脚本条目数不一致: txt={len(target_items)} expected={len(units)}")

    applied = 0
    for index, (target, unit) in enumerate(zip(target_items, units), start=1):
        if target["id"] != index:
            raise Exception(f"第 {index} 项编号不匹配: txt={int(target['id']):06d} expected={index:06d}")
        if target["type"] != unit["type"]:
            raise Exception(f"第 {index} 项类型不匹配: txt={target['type']} expected={unit['type']}")
        line_index = unit["line_index"]
        if line_index is None:
            raise Exception(f"第 {index} 项目标行无效")
        if bool(unit["is_select"]):
            lines[int(line_index)] = _replace_string_list_item(
                lines[int(line_index)],
                2,
                int(unit["string_index"]),
                str(target["text"]),
            )
        else:
            lines[int(line_index)] = _replace_string_operand(lines[int(line_index)], 0, str(target["text"]))
        applied += 1

    output_path = pathlib.Path(output_asm)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(lines), encoding="utf-8")
    return len(entries), applied, len(units)


def extract_dialog_txt_from_script(
    input_script: str | os.PathLike[str],
    output_txt: str | os.PathLike[str],
    encoding: str = mebius_txt_op.DEFAULT_TEXT_ENCODING,
) -> tuple[int, int]:
    with tempfile.TemporaryDirectory(prefix="mebius_txt_extract_") as temp_dir:
        temp_asm = pathlib.Path(temp_dir) / (pathlib.Path(input_script).name + ".asm.txt")
        mebius_txt_disasm.disassemble_file(pathlib.Path(input_script), temp_asm, text_encoding=encoding)
        return extract_dialog_txt_from_asm(temp_asm, output_txt)


def import_dialog_txt_to_script(
    input_script: str | os.PathLike[str],
    input_txt: str | os.PathLike[str],
    output_script: str | os.PathLike[str],
    encoding: str = mebius_txt_op.DEFAULT_TEXT_ENCODING,
    source_encoding: str | None = None,
) -> tuple[int, int, int]:
    disasm_encoding = source_encoding or encoding
    with tempfile.TemporaryDirectory(prefix="mebius_txt_import_") as temp_dir:
        temp_dir_path = pathlib.Path(temp_dir)
        temp_src_asm = temp_dir_path / (pathlib.Path(input_script).name + ".src.asm.txt")
        temp_out_asm = temp_dir_path / (pathlib.Path(input_script).name + ".out.asm.txt")
        mebius_txt_disasm.disassemble_file(pathlib.Path(input_script), temp_src_asm, text_encoding=disasm_encoding)
        count, applied, units = import_dialog_txt_to_asm(temp_src_asm, input_txt, temp_out_asm)
        mebius_txt_asm.assemble_file(temp_out_asm, pathlib.Path(output_script), text_encoding=encoding)
        return count, applied, units


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract or import MEBIUS dialog TXT")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="extract dialog TXT from a TXT script")
    extract_parser.add_argument("input_script", help="input TXT script path")
    extract_parser.add_argument("output_txt", help="output TXT path")
    extract_parser.add_argument(
        "-e",
        "--encoding",
        default=mebius_txt_op.DEFAULT_TEXT_ENCODING,
        help="source script text encoding (default: %(default)s)",
    )

    import_parser = subparsers.add_parser("import", help="import dialog TXT into a TXT script")
    import_parser.add_argument("input_script", help="original TXT script path")
    import_parser.add_argument("input_txt", help="edited TXT path")
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
        count, units = extract_dialog_txt_from_script(args.input_script, args.output_txt, encoding=args.encoding)
        print(f"extracted {count} dialog entries into {units} TXT units")
        return 0
    count, applied, units = import_dialog_txt_to_script(
        args.input_script,
        args.input_txt,
        args.output_script,
        encoding=args.encoding,
        source_encoding=args.source_encoding,
    )
    print(f"imported {applied} TXT units across {count} dialog entries ({units} units total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())