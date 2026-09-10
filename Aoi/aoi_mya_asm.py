from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aoi_mya_opcode_common import (
    MYU_OPCODE_ARG_COUNTS,
    OPCODE_ARG_COUNTS,
    ParseError,
    SCRIPT_ENCODING,
    asm_input_bin_path,
    assemble_text,
    is_disasm_path,
    iter_files,
    script_character_width,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AOI 脚本汇编器")
    parser.add_argument("input_path", help="输入 .asm.txt 文件或目录")
    parser.add_argument("output_path", help="输出目录")
    parser.add_argument(
        "--output-script-encoding",
        default=SCRIPT_ENCODING,
        help=f"回编脚本字符串编码，默认 {SCRIPT_ENCODING}",
    )
    return parser


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


def run_asm(
    input_path: Path,
    output_root: Path,
    output_script_encoding: str = SCRIPT_ENCODING,
    recursive: bool = True,
    keep_structure: bool = True,
    opcode_counts: list[int] | None = None,
) -> None:
    if opcode_counts is None:
        opcode_counts = (
            MYU_OPCODE_ARG_COUNTS
            if script_character_width(output_script_encoding) == 2
            else OPCODE_ARG_COUNTS
        )
    input_root = input_path if input_path.is_dir() else input_path.parent
    processed_files = 0
    for file_path in _iter_input_files(input_path, recursive=recursive):
        if not is_disasm_path(file_path):
            continue
        try:
            asm_text = file_path.read_text(encoding="utf-8")
            buffer = assemble_text(
                asm_text,
                script_encoding=output_script_encoding,
                opcode_counts=opcode_counts,
            )
        except Exception as exc:
            raise ParseError(f"汇编文件失败: {file_path}: {exc}") from exc
        if input_path.is_file() and not _is_dir_like_output(output_root):
            output_path = output_root
        elif keep_structure:
            output_path = asm_input_bin_path(file_path, input_root, output_root)
        else:
            target_name = file_path.name[:-8] if file_path.name.lower().endswith(".asm.txt") else file_path.name
            output_path = output_root / target_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(buffer)
        print(f"汇编完成: {file_path} -> {output_path}")
        processed_files += 1
    print(f"汇编汇总: 共处理 {processed_files} 个脚本。")


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    input_path = Path(args.input_path)
    output_root = Path(args.output_path)

    try:
        opcode_counts = (
            MYU_OPCODE_ARG_COUNTS
            if script_character_width(args.output_script_encoding) == 2
            else OPCODE_ARG_COUNTS
        )
        run_asm(
            input_path=input_path,
            output_root=output_root,
            output_script_encoding=args.output_script_encoding,
            opcode_counts=opcode_counts,
        )
        return 0
    except ParseError as exc:
        print(f"汇编失败: {exc}")
        return 1
    except Exception as exc:
        print(f"错误: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
