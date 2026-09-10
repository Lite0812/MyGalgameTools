from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from aoi_mya_opcode_common import (
    OPCODE_ARG_COUNTS,
    OPCODE_NAMES,
    ParseError,
    disassemble_buffer,
    is_script_binary_path,
    iter_files,
    opcode_name,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AOI MYA opcode 解析工具")
    subparsers = parser.add_subparsers(dest="mode")

    table_parser = subparsers.add_parser("table", help="输出 opcode 表")
    table_parser.add_argument("--format", choices=("json", "markdown"), default="markdown")

    scan_parser = subparsers.add_parser("scan", help="扫描脚本中使用到的 opcode")
    scan_parser.add_argument("input_path", help="输入脚本文件或目录")
    scan_parser.add_argument("--format", choices=("json", "text"), default="text")
    return parser


def dump_table_json() -> None:
    payload = []
    for opcode, argc in enumerate(OPCODE_ARG_COUNTS):
        if argc == 0 and opcode not in OPCODE_NAMES:
            continue
        payload.append(
            {
                "opcode": opcode,
                "opcode_hex": f"0x{opcode:02X}",
                "signed_opcode": opcode if opcode < 0x80 else opcode - 0x100,
                "name": opcode_name(opcode),
                "arg_count": argc,
            }
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def dump_table_markdown() -> None:
    print("| opcode | signed | name | arg_count |")
    print("| --- | ---: | --- | ---: |")
    for opcode, argc in enumerate(OPCODE_ARG_COUNTS):
        if argc == 0 and opcode not in OPCODE_NAMES:
            continue
        signed = opcode if opcode < 0x80 else opcode - 0x100
        print(f"| `0x{opcode:02X}` | `{signed}` | `{opcode_name(opcode)}` | `{argc}` |")


def scan_usage(input_path: Path, output_format: str) -> None:
    counter: Counter[int] = Counter()
    file_count = 0
    for file_path in iter_files(input_path):
        if not is_script_binary_path(file_path):
            continue
        file_count += 1
        buffer = file_path.read_bytes()
        for insn in disassemble_buffer(buffer):
            counter[insn.opcode] += 1

    if output_format == "json":
        payload = {
            "file_count": file_count,
            "opcodes": [
                {
                    "opcode": opcode,
                    "opcode_hex": f"0x{opcode:02X}",
                    "signed_opcode": opcode if opcode < 0x80 else opcode - 0x100,
                    "name": opcode_name(opcode),
                    "arg_count": OPCODE_ARG_COUNTS[opcode],
                    "count": count,
                }
                for opcode, count in sorted(counter.items())
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"扫描文件数: {file_count}")
    for opcode, count in sorted(counter.items()):
        signed = opcode if opcode < 0x80 else opcode - 0x100
        print(
            f"0x{opcode:02X} ({signed:4d}) "
            f"{opcode_name(opcode):<20} argc={OPCODE_ARG_COUNTS[opcode]} count={count}"
        )


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    try:
        if args.mode == "table":
            if args.format == "json":
                dump_table_json()
            else:
                dump_table_markdown()
            return 0
        if args.mode == "scan":
            scan_usage(Path(args.input_path), args.format)
            return 0
        print("用法: aoi_mya_opcode_parser.py <table|scan> ...")
        return 1
    except ParseError as exc:
        print(f"解析失败: {exc}")
        return 1
    except Exception as exc:
        print(f"错误: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
