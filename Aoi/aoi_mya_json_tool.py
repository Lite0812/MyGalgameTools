from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from aoi_mya_opcode_common import (
    CONTROL_TARGET_ARG_INDEXES,
    LabelRef,
    ParseError,
    ParsedArg,
    ParsedInstruction,
    SCRIPT_ENCODING,
    assemble_instruction,
    collect_control_labels,
    decode_script_text_lossless,
    disassemble_buffer,
)


JSON_ENCODING = "utf-8"
NAME_OP = "NAME"
TEXT_OP = "TEXT"
MESSAGE_ARG_OPS = {
    "CELESFETTLE": 0,
    "ITEMIS": 0,
    "ITEMGET": 0,
    "POPUP": 0,
    "SELECT": 1,
}
JP_TEXT_RE = re.compile(r"[ぁ-んァ-ヶ一-龯]")


@dataclass
class JsonEntryRef:
    kind: str
    name_instruction_index: int | None
    message_instruction_indexes: list[int]
    message_arg_index: int


def escape_segment(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def split_escaped_segments(text: str) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "\\":
            current.append(ch)
            i += 1
            continue

        i += 1
        if i >= len(text):
            current.append("\\")
            break

        esc = text[i]
        if esc == "n":
            segments.append("".join(current))
            current = []
        elif esc == "r":
            current.append("\r")
        elif esc == "\\":
            current.append("\\")
        else:
            current.append(esc)
        i += 1

    segments.append("".join(current))
    return segments


def _iter_input_files(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    iterator = input_path.rglob("*") if recursive else input_path.iterdir()
    return sorted(path for path in iterator if path.is_file())


def _is_dir_like_output(path: Path) -> bool:
    if path.exists():
        return path.is_dir()
    text = str(path)
    if text.endswith(("\\", "/")):
        return True
    return path.suffix == ""


def json_output_path(input_path: Path, input_root: Path, output_root: Path) -> Path:
    relative = input_path.relative_to(input_root) if input_root.is_dir() else Path(input_path.name)
    return output_root / relative.with_name(relative.name + ".json")


def json_input_path(input_path: Path, input_root: Path, json_root: Path) -> Path:
    relative = input_path.relative_to(input_root) if input_root.is_dir() else Path(input_path.name)
    return json_root / relative.with_name(relative.name + ".json")


def copy_extra_input_files(
    input_root: Path,
    output_root: Path,
    recursive: bool,
    keep_structure: bool,
) -> int:
    if not input_root.is_dir():
        return 0

    copied = 0
    for file_path in _iter_input_files(input_root, recursive=recursive):
        if file_path.suffix.lower() == ".txt":
            continue
        if keep_structure:
            output_path = output_root / file_path.relative_to(input_root)
        else:
            output_path = output_root / file_path.name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, output_path)
        copied += 1
        print(f"已复制无关文件: {file_path} -> {output_path}")
    return copied


def _decode_string_arg(arg: ParsedArg, script_encoding: str) -> str:
    if arg.kind != "string" or arg.mode != "immediate" or not isinstance(arg.value, bytes):
        raise RuntimeError("目标参数不是立即数字符串")
    text = decode_script_text_lossless(arg.value, script_encoding=script_encoding)
    if text is None:
        raise RuntimeError("字符串无法按指定编码无损解码")
    return text


def _replace_string_arg(arg: ParsedArg, text: str, script_encoding: str) -> ParsedArg:
    if arg.kind != "string" or arg.mode != "immediate":
        raise RuntimeError("目标参数不是立即数字符串")
    try:
        raw = text.encode(script_encoding)
    except UnicodeEncodeError as exc:
        raise RuntimeError(f"字符串无法按 {script_encoding} 编码: {text!r}") from exc
    return ParsedArg(
        flags=arg.flags,
        kind=arg.kind,
        mode=arg.mode,
        value=raw,
        extra=copy.deepcopy(arg.extra),
    )


def has_japanese_text(text: str) -> bool:
    return bool(JP_TEXT_RE.search(text))


def iter_skill_dun_message_args(insn: ParsedInstruction, script_encoding: str) -> list[tuple[int, str]]:
    op_name = insn.display_name.upper()
    if "SKILL" not in op_name and "DUN" not in op_name:
        return []

    result: list[tuple[int, str]] = []
    for arg_index, arg in enumerate(insn.args):
        if arg.kind != "string" or arg.mode != "immediate":
            continue
        text = _decode_string_arg(arg, script_encoding)
        if not has_japanese_text(text):
            continue
        result.append((arg_index, text))
    return result


def collect_json_entries(
    instructions: list[ParsedInstruction],
    script_encoding: str,
) -> tuple[list[dict[str, str]], list[JsonEntryRef]]:
    payload: list[dict[str, str]] = []
    refs: list[JsonEntryRef] = []
    pending_name_text: str | None = None
    pending_name_index: int | None = None
    index = 0

    while index < len(instructions):
        insn = instructions[index]
        op_name = insn.display_name

        if op_name == NAME_OP and insn.args and insn.args[0].kind == "string":
            pending_name_text = escape_segment(_decode_string_arg(insn.args[0], script_encoding))
            pending_name_index = index
            index += 1
            continue

        if op_name == TEXT_OP and insn.args and insn.args[0].kind == "string":
            text_indexes: list[int] = []
            text_segments: list[str] = []
            while index < len(instructions):
                current = instructions[index]
                if current.display_name != TEXT_OP or not current.args or current.args[0].kind != "string":
                    break
                text_indexes.append(index)
                text_segments.append(escape_segment(_decode_string_arg(current.args[0], script_encoding)))
                index += 1

            item: dict[str, str] = {}
            if pending_name_text not in (None, ""):
                item["name"] = pending_name_text
            item["message"] = "\\n".join(text_segments)
            payload.append(item)
            refs.append(
                JsonEntryRef(
                    kind="text_block",
                    name_instruction_index=pending_name_index,
                    message_instruction_indexes=text_indexes,
                    message_arg_index=0,
                )
            )
            pending_name_text = None
            pending_name_index = None
            continue

        message_arg_index = MESSAGE_ARG_OPS.get(op_name)
        if message_arg_index is not None and len(insn.args) > message_arg_index:
            arg = insn.args[message_arg_index]
            if arg.kind == "string" and arg.mode == "immediate":
                payload.append({"message": escape_segment(_decode_string_arg(arg, script_encoding))})
                refs.append(
                    JsonEntryRef(
                        kind=op_name.lower(),
                        name_instruction_index=None,
                        message_instruction_indexes=[index],
                        message_arg_index=message_arg_index,
                    )
                )

        for arg_index, text in iter_skill_dun_message_args(insn, script_encoding):
            payload.append({"message": escape_segment(text)})
            refs.append(
                JsonEntryRef(
                    kind=op_name.lower(),
                    name_instruction_index=None,
                    message_instruction_indexes=[index],
                    message_arg_index=arg_index,
                )
            )
        pending_name_text = None
        pending_name_index = None
        index += 1

    return payload, refs


def labelize_instruction_sequence(
    instructions: list[ParsedInstruction],
) -> tuple[list[ParsedInstruction], dict[int, str]]:
    labels_by_offset = collect_control_labels(instructions)
    cloned: list[ParsedInstruction] = copy.deepcopy(instructions)
    for insn in cloned:
        for arg_index in CONTROL_TARGET_ARG_INDEXES.get(insn.opcode, []):
            arg = insn.args[arg_index]
            if arg.kind != "number" or arg.mode != "immediate" or not isinstance(arg.value, int):
                continue
            arg.value = LabelRef(labels_by_offset[arg.value])
    return cloned, labels_by_offset


def assemble_instruction_sequence(
    instructions: list[ParsedInstruction],
    labels_by_offset: dict[int, str],
) -> bytes:
    label_positions: dict[str, int] = {}
    assigned_offsets: set[int] = set()
    current_offset = 0
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


def dump_json_file(input_path: Path, output_path: Path, script_encoding: str) -> int:
    instructions = disassemble_buffer(input_path.read_bytes())
    payload, _ = collect_json_entries(instructions, script_encoding=script_encoding)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding=JSON_ENCODING,
        newline="\n",
    )
    print(f"JSON 提取完成: {input_path} -> {output_path}")
    return len(payload)


def load_json_items(input_json_path: Path) -> list[object]:
    raw_text = input_json_path.read_text(encoding=JSON_ENCODING)
    if raw_text.strip() == "":
        return []
    items = json.loads(raw_text)
    if not isinstance(items, list):
        raise RuntimeError("JSON 顶层必须是数组")
    return items


def inject_json_file(
    input_script_path: Path,
    input_json_path: Path,
    output_script_path: Path,
    input_script_encoding: str,
    output_script_encoding: str,
) -> int:
    source_instructions = disassemble_buffer(input_script_path.read_bytes())
    items = load_json_items(input_json_path)

    payload, refs = collect_json_entries(source_instructions, script_encoding=input_script_encoding)
    if len(items) != len(refs):
        raise RuntimeError(f"JSON 条目数量不匹配: JSON={len(items)}，脚本={len(refs)}")

    instructions, labels_by_offset = labelize_instruction_sequence(source_instructions)

    for entry_index in range(len(refs) - 1, -1, -1):
        item = items[entry_index]
        ref = refs[entry_index]
        if not isinstance(item, dict):
            raise RuntimeError(f"第 {entry_index} 项不是对象")
        unknown_keys = set(item.keys()) - {"name", "message"}
        if unknown_keys:
            raise RuntimeError(f"第 {entry_index} 项包含无效键: {sorted(unknown_keys)}")
        if "message" not in item:
            raise RuntimeError(f"第 {entry_index} 项缺少 message 字段")

        if ref.kind == "text_block":
            segments = split_escaped_segments(str(item["message"]))
            if not segments:
                segments = [""]
            original_indexes = ref.message_instruction_indexes
            original_block = [instructions[idx] for idx in original_indexes]
            replacement_block: list[ParsedInstruction] = []
            for segment_index, segment in enumerate(segments):
                prototype = copy.deepcopy(original_block[min(segment_index, len(original_block) - 1)])
                prototype.args[ref.message_arg_index] = _replace_string_arg(
                    prototype.args[ref.message_arg_index],
                    segment,
                    output_script_encoding,
                )
                replacement_block.append(prototype)
            start = original_indexes[0]
            end = original_indexes[-1] + 1
            instructions[start:end] = replacement_block

            if ref.name_instruction_index is not None and "name" in item:
                name_insn = instructions[ref.name_instruction_index]
                name_insn.args[0] = _replace_string_arg(
                    name_insn.args[0],
                    str(item["name"]),
                    output_script_encoding,
                )
            continue

        target_insn = instructions[ref.message_instruction_indexes[0]]
        target_insn.args[ref.message_arg_index] = _replace_string_arg(
            target_insn.args[ref.message_arg_index],
            str(item["message"]),
            output_script_encoding,
        )

    output_script_path.parent.mkdir(parents=True, exist_ok=True)
    output_script_path.write_bytes(assemble_instruction_sequence(instructions, labels_by_offset))
    print(f"JSON 回注完成: {input_script_path} + {input_json_path} -> {output_script_path}")
    return len(items)


def run_json_dump(
    input_path: Path,
    output_root: Path,
    script_encoding: str = SCRIPT_ENCODING,
    recursive: bool = True,
    keep_structure: bool = True,
) -> None:
    input_root = input_path if input_path.is_dir() else input_path.parent
    processed_files = 0
    total_entries = 0
    for file_path in _iter_input_files(input_path, recursive=recursive):
        if file_path.suffix.lower() != ".txt":
            continue
        if input_path.is_file() and not _is_dir_like_output(output_root):
            output_path = output_root
        elif keep_structure:
            output_path = json_output_path(file_path, input_root, output_root)
        else:
            output_path = output_root / f"{file_path.name}.json"
        total_entries += dump_json_file(file_path, output_path, script_encoding=script_encoding)
        processed_files += 1
    print(f"JSON 提取汇总: 共处理 {processed_files} 个脚本，导出 {total_entries} 条 message。")


def run_json_inject(
    input_script_path: Path,
    input_json_root: Path,
    output_root: Path,
    input_script_encoding: str = SCRIPT_ENCODING,
    output_script_encoding: str = SCRIPT_ENCODING,
    recursive: bool = True,
    keep_structure: bool = True,
    copy_extra_files: bool = False,
) -> None:
    input_root = input_script_path if input_script_path.is_dir() else input_script_path.parent
    copied_extra_count = 0
    processed_files = 0
    injected_files = 0
    copied_original_files = 0
    total_messages = 0
    if copy_extra_files:
        copied_extra_count = copy_extra_input_files(
            input_root=input_script_path if input_script_path.is_dir() else input_root,
            output_root=output_root,
            recursive=recursive,
            keep_structure=keep_structure,
        )

    for file_path in _iter_input_files(input_script_path, recursive=recursive):
        if file_path.suffix.lower() != ".txt":
            continue
        if input_script_path.is_file():
            json_path = input_json_root
            if _is_dir_like_output(input_json_root):
                json_path = input_json_root / f"{file_path.name}.json"
        elif keep_structure:
            json_path = json_input_path(file_path, input_root, input_json_root)
        else:
            json_path = input_json_root / f"{file_path.name}.json"

        if input_script_path.is_file() and not _is_dir_like_output(output_root):
            output_path = output_root
        elif keep_structure:
            output_path = output_root / file_path.relative_to(input_root)
        else:
            output_path = output_root / file_path.name

        if not json_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, output_path)
            print(f"警告: 未找到 {json_path}，已复制原文件到 {output_path}")
            processed_files += 1
            copied_original_files += 1
            continue

        total_messages += inject_json_file(
            input_script_path=file_path,
            input_json_path=json_path,
            output_script_path=output_path,
            input_script_encoding=input_script_encoding,
            output_script_encoding=output_script_encoding,
        )
        processed_files += 1
        injected_files += 1

    if copy_extra_files:
        print(f"无关文件复制完成，共 {copied_extra_count} 个文件。")
    print(
        f"JSON 回注汇总: 共处理 {processed_files} 个脚本，"
        f"成功回注 {injected_files} 个，因缺少 JSON 复制原文件 {copied_original_files} 个，"
        f"共写入 {total_messages} 条 message。"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AOI MYA JSON 提取/回注工具")
    subparsers = parser.add_subparsers(dest="mode")

    dump_parser = subparsers.add_parser("dump", help="提取 JSON")
    dump_parser.add_argument("input_path", help="输入脚本文件或目录")
    dump_parser.add_argument("output_path", help="输出 JSON 文件或目录")
    dump_parser.add_argument("--script-encoding", default=SCRIPT_ENCODING)

    inject_parser = subparsers.add_parser("inject", help="根据原始脚本和 JSON 生成新脚本")
    inject_parser.add_argument("input_script_path", help="原始脚本文件或目录")
    inject_parser.add_argument("input_json_path", help="JSON 文件或目录")
    inject_parser.add_argument("output_path", help="输出脚本文件或目录")
    inject_parser.add_argument("--script-encoding", default=SCRIPT_ENCODING)
    inject_parser.add_argument("--input-script-encoding")
    inject_parser.add_argument("--output-script-encoding")
    inject_parser.add_argument("--copy-extra-files", action="store_true", help="复制输入脚本目录中的非 .txt 文件到输出目录")

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    try:
        if args.mode == "dump":
            run_json_dump(
                input_path=Path(args.input_path),
                output_root=Path(args.output_path),
                script_encoding=args.script_encoding,
            )
            return 0

        if args.mode == "inject":
            input_script_encoding = args.input_script_encoding or args.script_encoding
            output_script_encoding = args.output_script_encoding or input_script_encoding
            run_json_inject(
                input_script_path=Path(args.input_script_path),
                input_json_root=Path(args.input_json_path),
                output_root=Path(args.output_path),
                input_script_encoding=input_script_encoding,
                output_script_encoding=output_script_encoding,
                copy_extra_files=args.copy_extra_files,
            )
            return 0

        parser.print_help()
        return 1
    except (ParseError, RuntimeError, UnicodeError) as exc:
        print(f"错误: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
