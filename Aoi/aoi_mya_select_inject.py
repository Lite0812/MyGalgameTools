from __future__ import annotations

import argparse
import copy
import json
import shutil
from pathlib import Path

from aoi_mya_json_tool import (
    JSON_ENCODING,
    SCRIPT_ENCODING,
    _replace_string_arg,
    collect_json_entries,
    labelize_instruction_sequence,
)
from aoi_mya_opcode_common import ParseError, assemble_instruction, disassemble_buffer


def build_flat_json_name(relative_path: Path) -> str:
    return "__".join(relative_path.parts) + ".json"


def assemble_instruction_sequence(instructions, labels_by_offset: dict[int, str]) -> bytes:
    label_positions: dict[str, int] = {}
    current_offset = 0
    assigned_offsets: set[int] = set()

    for insn in instructions:
        label_name = labels_by_offset.get(insn.offset)
        if label_name is not None and insn.offset not in assigned_offsets:
            label_positions[label_name] = current_offset
            assigned_offsets.add(insn.offset)
        current_offset += len(assemble_instruction(insn, None))

    out = bytearray()
    for insn in instructions:
        out.extend(assemble_instruction(insn, label_positions))
    return bytes(out)


def load_select_json(json_path: Path) -> list[dict[str, str]]:
    raw_text = json_path.read_text(encoding=JSON_ENCODING)
    if raw_text.strip() == "":
        return []

    items = json.loads(raw_text)
    if not isinstance(items, list):
        raise RuntimeError(f"JSON 顶层必须是数组: {json_path}")

    normalized: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise RuntimeError(f"{json_path} 第 {index} 项不是对象")
        unknown_keys = set(item.keys()) - {"message"}
        if unknown_keys:
            raise RuntimeError(f"{json_path} 第 {index} 项包含无效键: {sorted(unknown_keys)}")
        if "message" not in item:
            raise RuntimeError(f"{json_path} 第 {index} 项缺少 message 字段")
        normalized.append({"message": str(item["message"])})
    return normalized


def inject_select_file(
    input_script_path: Path,
    input_json_path: Path,
    output_script_path: Path,
    input_script_encoding: str,
    output_script_encoding: str,
) -> int:
    source_instructions = disassemble_buffer(input_script_path.read_bytes())
    _, refs = collect_json_entries(source_instructions, script_encoding=input_script_encoding)
    select_refs = [ref for ref in refs if ref.kind == "select"]
    items = load_select_json(input_json_path)

    if len(items) != len(select_refs):
        raise RuntimeError(
            f"选项数量不匹配: {input_script_path} | JSON={len(items)} 脚本={len(select_refs)}"
        )

    instructions, labels_by_offset = labelize_instruction_sequence(source_instructions)

    for entry_index in range(len(select_refs) - 1, -1, -1):
        item = items[entry_index]
        ref = select_refs[entry_index]
        target_insn = instructions[ref.message_instruction_indexes[0]]
        target_insn.args[ref.message_arg_index] = _replace_string_arg(
            copy.deepcopy(target_insn.args[ref.message_arg_index]),
            item["message"],
            output_script_encoding,
        )

    output_script_path.parent.mkdir(parents=True, exist_ok=True)
    output_script_path.write_bytes(assemble_instruction_sequence(instructions, labels_by_offset))
    print(f"选项回注完成: {input_script_path} + {input_json_path} -> {output_script_path}")
    return len(items)


def run_inject(
    input_root: Path,
    json_root: Path,
    output_root: Path,
    input_script_encoding: str,
    output_script_encoding: str,
) -> None:
    if not input_root.is_dir():
        raise RuntimeError(f"输入目录不存在: {input_root}")
    if not json_root.is_dir():
        raise RuntimeError(f"JSON 目录不存在: {json_root}")

    all_files = sorted(path for path in input_root.rglob("*") if path.is_file())
    if not all_files:
        raise RuntimeError(f"未在 {input_root} 找到任何文件")

    copied_files = 0
    injected_files = 0
    injected_items = 0

    for source_path in all_files:
        relative_path = source_path.relative_to(input_root)
        output_path = output_root / relative_path

        if source_path.suffix.lower() != ".txt":
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, output_path)
            copied_files += 1
            continue

        json_path = json_root / build_flat_json_name(relative_path)
        if not json_path.is_file():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, output_path)
            copied_files += 1
            continue

        injected_items += inject_select_file(
            input_script_path=source_path,
            input_json_path=json_path,
            output_script_path=output_path,
            input_script_encoding=input_script_encoding,
            output_script_encoding=output_script_encoding,
        )
        injected_files += 1

    print(
        f"回注汇总: 成功回注 {injected_files} 个脚本，共 {injected_items} 条选项；"
        f"原样复制 {copied_files} 个文件。"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把平铺 select JSON 回注到 box_out_json_build 脚本")
    parser.add_argument("input_root", nargs="?", default="box_out_json_build", help="原始脚本目录，默认 box_out_json_build")
    parser.add_argument("json_root", nargs="?", default="select", help="平铺 JSON 目录，默认 select")
    parser.add_argument("output_root", nargs="?", default="select_pack", help="输出目录，默认 select_pack")
    parser.add_argument("--script-encoding", default=SCRIPT_ENCODING, help=f"输入/输出脚本文本编码，默认 {SCRIPT_ENCODING}")
    parser.add_argument("--input-script-encoding", help="输入脚本编码，默认跟 --script-encoding 一致")
    parser.add_argument("--output-script-encoding", help="输出脚本编码，默认跟 --script-encoding 一致")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_script_encoding = args.input_script_encoding or args.script_encoding
    output_script_encoding = args.output_script_encoding or args.script_encoding

    try:
        run_inject(
            input_root=Path(args.input_root),
            json_root=Path(args.json_root),
            output_root=Path(args.output_root),
            input_script_encoding=input_script_encoding,
            output_script_encoding=output_script_encoding,
        )
    except (ParseError, RuntimeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"错误: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
