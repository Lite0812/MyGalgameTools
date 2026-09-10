from __future__ import annotations

import argparse
import json
from pathlib import Path

from aoi_mya_json_tool import JSON_ENCODING, SCRIPT_ENCODING, collect_json_entries
from aoi_mya_opcode_common import ParseError, disassemble_buffer


def build_flat_json_name(relative_path: Path) -> str:
    return "__".join(relative_path.parts) + ".json"


def collect_select_payload(script_path: Path, script_encoding: str) -> list[dict[str, str]]:
    instructions = disassemble_buffer(script_path.read_bytes())
    payload, refs = collect_json_entries(instructions, script_encoding=script_encoding)
    return [{"message": item["message"]} for item, ref in zip(payload, refs) if ref.kind == "select"]


def run_dump(input_root: Path, output_root: Path, script_encoding: str) -> None:
    if not input_root.is_dir():
        raise RuntimeError(f"输入目录不存在: {input_root}")

    txt_files = sorted(path for path in input_root.rglob("*.txt") if path.is_file())
    if not txt_files:
        raise RuntimeError(f"未在 {input_root} 找到任何 .txt 文件")

    output_root.mkdir(parents=True, exist_ok=True)

    dumped_files = 0
    dumped_items = 0

    for script_path in txt_files:
        relative_path = script_path.relative_to(input_root)
        select_payload = collect_select_payload(script_path, script_encoding=script_encoding)
        if not select_payload:
            continue

        output_path = output_root / build_flat_json_name(relative_path)
        output_path.write_text(
            json.dumps(select_payload, ensure_ascii=False, indent=2) + "\n",
            encoding=JSON_ENCODING,
            newline="\n",
        )
        dumped_files += 1
        dumped_items += len(select_payload)
        print(f"选项提取完成: {script_path} -> {output_path} ({len(select_payload)} 条)")

    print(f"提取汇总: 共输出 {dumped_files} 个 JSON，选项 {dumped_items} 条。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="提取 box_out_json_build 中的 SELECT 选项为平铺 JSON")
    parser.add_argument("input_root", nargs="?", default="box_out_json_build", help="输入脚本目录，默认 box_out_json_build")
    parser.add_argument("output_root", nargs="?", default="select", help="输出目录，默认 select")
    parser.add_argument("--script-encoding", default=SCRIPT_ENCODING, help=f"脚本文本编码，默认 {SCRIPT_ENCODING}")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        run_dump(
            input_root=Path(args.input_root),
            output_root=Path(args.output_root),
            script_encoding=args.script_encoding,
        )
    except (ParseError, RuntimeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"错误: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
