from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aoi_mya_json_tool import JSON_ENCODING, SCRIPT_ENCODING, collect_json_entries
from aoi_mya_opcode_common import ParseError, disassemble_buffer


NAME_MARKER = (0x03, 0x88)
MESSAGE_MARKER = (0x02, 0x88)
ALT_MESSAGE_MARKER = (0xBC, 0x88)
MESSAGE_MARKERS = {MESSAGE_MARKER, ALT_MESSAGE_MARKER}
TEXT_MARKERS = {NAME_MARKER, *MESSAGE_MARKERS}


def escape_segment(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def try_read_text_part(buffer: bytes, start: int, script_encoding: str):
    if start + 2 >= len(buffer):
        return None
    marker = (buffer[start], buffer[start + 1])
    if marker not in TEXT_MARKERS:
        return None
    length = buffer[start + 2]
    end = start + 3 + length
    if end > len(buffer):
        return None
    text_bytes = buffer[start + 3 : end]
    try:
        text_bytes.decode(script_encoding)
    except UnicodeDecodeError:
        return None
    return marker, text_bytes, end


def read_same_marker_run(buffer: bytes, start: int, script_encoding: str):
    first_part = try_read_text_part(buffer, start, script_encoding)
    if first_part is None:
        return start, None
    marker, text_bytes, pos = first_part
    parts = [text_bytes]
    while True:
        part = try_read_text_part(buffer, pos, script_encoding)
        if part is None or part[0] != marker:
            break
        _, text_bytes, pos = part
        parts.append(text_bytes)
    return pos, {"marker": marker, "parts": parts}


def run_to_escaped_text(run: dict, script_encoding: str) -> str:
    return "\\n".join(escape_segment(part.decode(script_encoding)) for part in run["parts"])


def collect_legacy_source_entries(buffer: bytes, script_encoding: str):
    entries: list[dict[str, str]] = []
    pending_name_run = None
    i = 0
    while i < len(buffer) - 1:
        marker = (buffer[i], buffer[i + 1])
        if marker in TEXT_MARKERS:
            part = try_read_text_part(buffer, i, script_encoding)
            if part is None:
                i += 1
                continue
            i, run = read_same_marker_run(buffer, i, script_encoding)
            if run is None:
                i += 1
                continue
            if marker == NAME_MARKER:
                pending_name_run = run
                continue
            if marker in MESSAGE_MARKERS:
                item: dict[str, str] = {}
                if pending_name_run is not None:
                    item["name"] = run_to_escaped_text(pending_name_run, script_encoding)
                item["message"] = run_to_escaped_text(run, script_encoding)
                entries.append(item)
                pending_name_run = None
                continue
        i += 1
    return entries


def build_match_key(item: dict[str, str]) -> tuple[str, str]:
    return item.get("name", ""), item.get("message", "")


def map_old_to_new_indexes(old_source: list[dict[str, str]], new_source: list[dict[str, str]]) -> dict[int, int]:
    mapping: dict[int, int] = {}
    new_index = 0
    for old_index, old_item in enumerate(old_source):
        old_key = build_match_key(old_item)
        found = False
        while new_index < len(new_source):
            new_key = build_match_key(new_source[new_index])
            if new_key == old_key:
                mapping[old_index] = new_index
                new_index += 1
                found = True
                break
            new_index += 1
        if not found:
            continue
    return mapping


def script_path_from_old_json(old_json_path: Path, old_json_root: Path, script_root: Path) -> Path:
    relative = old_json_path.relative_to(old_json_root)
    name = relative.name
    if name.lower().endswith(".json"):
        name = name[:-5]
    return script_root / relative.with_name(name)


def full_output_path(script_path: Path, script_root: Path, full_output_root: Path) -> Path:
    relative = script_path.relative_to(script_root)
    return full_output_root / relative.with_name(relative.name + ".json")


def migrate_single_file(
    old_json_path: Path,
    script_path: Path,
    full_output_path_value: Path,
    full_output_root: Path,
    script_encoding: str,
):
    old_translated_items = json.loads(old_json_path.read_text(encoding=JSON_ENCODING))
    if not isinstance(old_translated_items, list):
        raise RuntimeError(f"旧 JSON 顶层不是数组: {old_json_path}")

    script_buffer = script_path.read_bytes()
    old_source_items = collect_legacy_source_entries(script_buffer, script_encoding=script_encoding)
    new_source_items, _ = collect_json_entries(disassemble_buffer(script_buffer), script_encoding=script_encoding)
    mapping = map_old_to_new_indexes(old_source_items, new_source_items)

    converted = [dict(item) for item in new_source_items]
    applied = 0
    limited = min(len(old_translated_items), len(old_source_items))
    for old_index in range(limited):
        mapped_new_index = mapping.get(old_index)
        if mapped_new_index is None:
            continue
        translated_item = old_translated_items[old_index]
        if not isinstance(translated_item, dict):
            continue
        if "message" in translated_item:
            converted[mapped_new_index]["message"] = str(translated_item["message"])
        if "name" in translated_item and "name" in converted[mapped_new_index]:
            converted[mapped_new_index]["name"] = str(translated_item["name"])
        applied += 1

    translated_new_indexes = set(mapping.get(i) for i in range(limited) if mapping.get(i) is not None)
    untranslated_records: list[dict[str, object]] = []
    rel_output = full_output_path_value.relative_to(full_output_root)
    for idx, item in enumerate(converted):
        if idx in translated_new_indexes:
            continue
        record: dict[str, object] = {
            "file": str(rel_output).replace("\\", "/"),
            "index": idx,
            "message": item.get("message", ""),
        }
        if "name" in item:
            record["name"] = item["name"]
        untranslated_records.append(record)

    full_output_path_value.parent.mkdir(parents=True, exist_ok=True)
    full_output_path_value.write_text(
        json.dumps(converted, ensure_ascii=False, indent=2) + "\n",
        encoding=JSON_ENCODING,
        newline="\n",
    )
    return {
        "old_count": len(old_translated_items),
        "old_source_count": len(old_source_items),
        "new_count": len(new_source_items),
        "applied": applied,
        "untranslated_count": len(untranslated_records),
        "untranslated_records": untranslated_records,
    }


def run_migrate(
    old_json_root: Path,
    script_root: Path,
    full_output_root: Path,
    untranslated_output_json: Path,
    script_encoding: str = SCRIPT_ENCODING,
) -> None:
    old_json_files = sorted(path for path in old_json_root.rglob("*.json") if path.is_file())
    if not old_json_files:
        raise RuntimeError(f"未在 {old_json_root} 找到任何旧 JSON 文件")

    total_files = 0
    converted_files = 0
    missing_script_files = 0
    total_untranslated = 0
    all_untranslated: list[dict[str, object]] = []

    for old_json_file in old_json_files:
        total_files += 1
        script_path = script_path_from_old_json(old_json_file, old_json_root, script_root)
        if not script_path.is_file():
            print(f"警告: 找不到对应脚本，已跳过: {old_json_file} -> {script_path}")
            missing_script_files += 1
            continue
        output_path = full_output_path(script_path, script_root, full_output_root)
        stats = migrate_single_file(
            old_json_path=old_json_file,
            script_path=script_path,
            full_output_path_value=output_path,
            full_output_root=full_output_root,
            script_encoding=script_encoding,
        )
        converted_files += 1
        total_untranslated += int(stats["untranslated_count"])
        all_untranslated.extend(stats["untranslated_records"])
        print(
            f"迁移完成: {old_json_file} -> {output_path} | "
            f"旧JSON={stats['old_count']} 旧源={stats['old_source_count']} 新源={stats['new_count']} "
            f"已套用={stats['applied']} 待翻译={stats['untranslated_count']}"
        )

    untranslated_output_json.parent.mkdir(parents=True, exist_ok=True)
    untranslated_output_json.write_text(
        json.dumps(all_untranslated, ensure_ascii=False, indent=2) + "\n",
        encoding=JSON_ENCODING,
        newline="\n",
    )
    print(
        f"迁移汇总: 旧JSON文件 {total_files}，成功迁移 {converted_files}，缺失脚本 {missing_script_files}，"
        f"新增待翻译条目 {total_untranslated}。"
    )
    print(f"待翻译汇总 JSON: {untranslated_output_json}")


def run_apply_untranslated(
    full_json_root: Path,
    untranslated_json: Path,
    merged_output_root: Path,
) -> None:
    records = json.loads(untranslated_json.read_text(encoding=JSON_ENCODING))
    if not isinstance(records, list):
        raise RuntimeError("未翻译汇总 JSON 顶层必须为数组")

    grouped: dict[str, list[dict[str, object]]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        file_path = item.get("file")
        index = item.get("index")
        if not isinstance(file_path, str) or not isinstance(index, int):
            continue
        grouped.setdefault(file_path, []).append(item)

    processed_files = 0
    updated_entries = 0
    failed_files = 0
    for rel_file, items in grouped.items():
        try:
            source_path = Path(rel_file)
            if not source_path.is_absolute():
                candidate = full_json_root / source_path
                if candidate.is_file():
                    source_path = candidate
                else:
                    parts = source_path.parts
                    if parts and parts[0].lower() == full_json_root.name.lower():
                        candidate2 = full_json_root.parent.joinpath(*parts)
                        source_path = candidate2
                    else:
                        source_path = candidate
            if not source_path.is_file():
                print(f"警告: 未找到待合并目标 JSON: {source_path}")
                continue
            payload = json.loads(source_path.read_text(encoding=JSON_ENCODING))
            if not isinstance(payload, list):
                print(f"警告: 目标 JSON 顶层不是数组，已跳过: {source_path}")
                continue
            for item in items:
                idx = item["index"]
                if idx < 0 or idx >= len(payload):
                    continue
                if not isinstance(payload[idx], dict):
                    continue
                if "message" in item:
                    payload[idx]["message"] = str(item["message"])
                    updated_entries += 1
                if "name" in item and "name" in payload[idx]:
                    payload[idx]["name"] = str(item["name"])
            output_path = merged_output_root / source_path.relative_to(full_json_root)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding=JSON_ENCODING,
                newline="\n",
            )
            processed_files += 1
            print(f"已回并: {source_path} -> {output_path}")
        except Exception as exc:
            failed_files += 1
            print(f"警告: 回并失败 {rel_file}: {exc}")

    print(f"回并汇总: 处理文件 {processed_files}，更新条目 {updated_entries}，失败 {failed_files}。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="旧实现 JSON -> 新实现 JSON 迁移工具")
    subparsers = parser.add_subparsers(dest="mode")

    migrate_parser = subparsers.add_parser("migrate", help="迁移旧翻译 JSON 到新实现 JSON")
    migrate_parser.add_argument("old_json_root", help="旧实现翻译 JSON 目录（如 new）")
    migrate_parser.add_argument("script_root", help="原始脚本目录（如 box_out）")
    migrate_parser.add_argument("full_output_root", help="输出：可直接回注的新实现完整 JSON 目录")
    migrate_parser.add_argument("untranslated_output_json", help="输出：新实现未覆盖翻译条目的汇总 JSON 文件")
    migrate_parser.add_argument("--script-encoding", default=SCRIPT_ENCODING)

    apply_parser = subparsers.add_parser("apply-untranslated", help="把未翻译汇总 JSON 回并到完整新 JSON")
    apply_parser.add_argument("full_json_root", help="完整新 JSON 目录（migrate 的 full_output_root）")
    apply_parser.add_argument("untranslated_json", help="待回并的未翻译汇总 JSON（翻译后）")
    apply_parser.add_argument("merged_output_root", help="输出：回并后的完整新 JSON 目录")

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    try:
        if args.mode == "migrate":
            run_migrate(
                old_json_root=Path(args.old_json_root),
                script_root=Path(args.script_root),
                full_output_root=Path(args.full_output_root),
                untranslated_output_json=Path(args.untranslated_output_json),
                script_encoding=args.script_encoding,
            )
            return 0
        if args.mode == "apply-untranslated":
            run_apply_untranslated(
                full_json_root=Path(args.full_json_root),
                untranslated_json=Path(args.untranslated_json),
                merged_output_root=Path(args.merged_output_root),
            )
            return 0
        parser.print_help()
        return 1
    except (ParseError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"错误: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
