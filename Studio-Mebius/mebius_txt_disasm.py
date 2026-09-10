#!/usr/bin/env python3

from __future__ import annotations

import argparse
import pathlib
from typing import cast

import mebius_txt_op


def find_first_uncovered_offset(
    instructions: list[mebius_txt_op.DecodedInstruction],
    data_length: int,
) -> int | None:
    coverage_end = 2
    for instruction in sorted(instructions, key=lambda item: item.offset):
        instruction_end = instruction.offset + mebius_txt_op.measure_instruction(instruction.spec, instruction.operands)
        if instruction_end <= coverage_end:
            continue
        if instruction.offset > coverage_end:
            return coverage_end
        coverage_end = instruction_end
    if coverage_end < data_length:
        return coverage_end
    return None


def build_primary_instruction_sequence(
    instructions: list[mebius_txt_op.DecodedInstruction],
    data_length: int,
) -> list[mebius_txt_op.DecodedInstruction]:
    instruction_by_offset = {instruction.offset: instruction for instruction in instructions}
    primary: list[mebius_txt_op.DecodedInstruction] = []
    cursor = 2
    while cursor < data_length:
        instruction = instruction_by_offset.get(cursor)
        if instruction is None:
            raise ValueError(f"missing primary instruction at offset {cursor:#x}")
        primary.append(instruction)
        cursor += mebius_txt_op.measure_instruction(instruction.spec, instruction.operands)
    return primary


def disassemble_bytes(
    data: bytes,
    spec: dict[str, object] | None = None,
    text_encoding: str | None = None,
) -> tuple[int, list[mebius_txt_op.DecodedInstruction]]:
    if len(data) < 2:
        raise ValueError("TXT script is shorter than 2-byte header")

    mebius_txt_op.set_text_encoding(text_encoding)
    header = int.from_bytes(data[:2], "big")
    spec_by_opcode, _ = mebius_txt_op.build_spec_indexes(spec)
    decoded: dict[int, mebius_txt_op.DecodedInstruction] = {}
    pending = [2]
    seen_block_starts: set[int] = set()

    def drain_pending_blocks() -> None:
        while pending:
            cursor = pending.pop()
            if cursor < 2 or cursor >= len(data) or cursor in seen_block_starts:
                continue
            seen_block_starts.add(cursor)

            while cursor < len(data):
                if cursor in decoded:
                    break
                instruction, next_cursor = mebius_txt_op.decode_instruction(data, cursor, spec_by_opcode)
                decoded[cursor] = instruction

                if instruction.opcode == 0x0040 and len(instruction.operands) >= 2:
                    target = int(instruction.operands[1], 0)
                    if 2 <= target < len(data):
                        pending.append(target)
                    cursor = next_cursor
                    continue

                if instruction.opcode in {0x0030, 0x0031} and instruction.operands:
                    target = int(instruction.operands[0], 0)
                    if 2 <= target < len(data):
                        pending.append(target)
                    if instruction.opcode == 0x0031 and next_cursor < len(data):
                        pending.append(next_cursor)
                    break

                if instruction.opcode in {0x0033, 0x0035}:
                    if next_cursor < len(data):
                        pending.append(next_cursor)
                    break

                if instruction.opcode == 0x0034:
                    break

                cursor = next_cursor

    while True:
        drain_pending_blocks()

        instructions = sorted(decoded.values(), key=lambda item: item.offset)
        gap_start = find_first_uncovered_offset(instructions, len(data))
        if gap_start is None:
            break

        if gap_start in seen_block_starts:
            raise ValueError(f"undecoded gap at offset {gap_start:#x}")

        pending.append(gap_start)

    instructions = sorted(decoded.values(), key=lambda item: item.offset)
    gap_start = find_first_uncovered_offset(instructions, len(data))
    if gap_start is not None:
        raise ValueError(f"undecoded gap at offset {gap_start:#x}")

    return header, build_primary_instruction_sequence(instructions, len(data))


def build_label_name(offset: int) -> str:
    return f"loc_{offset:06X}"


def build_referenced_labels(
    instructions: list[mebius_txt_op.DecodedInstruction],
) -> dict[int, str]:
    instruction_offsets = {instruction.offset for instruction in instructions}
    referenced_offsets: set[int] = set()
    for instruction in instructions:
        for operand_index in mebius_txt_op.get_decoded_script_target_operand_indexes(instruction.opcode):
            if operand_index >= len(instruction.operands):
                continue
            target = int(instruction.operands[operand_index], 0)
            if target in instruction_offsets:
                referenced_offsets.add(target)
    return {offset: build_label_name(offset) for offset in sorted(referenced_offsets)}


def format_instruction_operands(
    instruction: mebius_txt_op.DecodedInstruction,
    labels: dict[int, str],
) -> list[str]:
    operands = list(instruction.operands)
    for operand_index in mebius_txt_op.get_decoded_script_target_operand_indexes(instruction.opcode):
        if operand_index >= len(operands):
            continue
        target = int(operands[operand_index], 0)
        if target in labels:
            operands[operand_index] = labels[target]
    if instruction.opcode == 0x0040 and len(operands) >= 2:
        if len(operands) >= 3:
            return [operands[1], operands[2]]
    return operands


def format_asm(header: int, instructions: list[mebius_txt_op.DecodedInstruction]) -> str:
    labels = build_referenced_labels(instructions)
    lines = [f".header {mebius_txt_op.format_hex(header, 4)}", ""]
    for instruction in instructions:
        label = labels.get(instruction.offset)
        if label is not None:
            if lines[-1] != "":
                lines.append("")
            lines.append(f"{label}:")
        mnemonic = cast(str, instruction.spec["mnemonic"])
        operands = format_instruction_operands(instruction, labels)
        if operands:
            body = f"    {mnemonic} " + ", ".join(operands)
        else:
            body = f"    {mnemonic}"
        lines.append(body)
    if lines[-1] != "":
        lines.append("")
    return "\n".join(lines)


def disassemble_file(
    input_path: pathlib.Path,
    output_path: pathlib.Path | None = None,
    text_encoding: str = mebius_txt_op.DEFAULT_TEXT_ENCODING,
) -> pathlib.Path:
    data = input_path.read_bytes()
    spec = mebius_txt_op.build_spec()
    header, instructions = disassemble_bytes(data, spec, text_encoding=text_encoding)
    asm_text = format_asm(header, instructions)
    target = output_path or input_path.with_suffix(input_path.suffix + ".asm.txt")
    target.write_text(asm_text, encoding="utf-8")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Disassemble MEBIUS TXT script to human-readable asm.txt")
    parser.add_argument("inputs", nargs="+", help="input TXT script file(s)")
    parser.add_argument("-o", "--output", help="output asm.txt path; valid only with a single input")
    parser.add_argument(
        "-e",
        "--text-encoding",
        default=mebius_txt_op.DEFAULT_TEXT_ENCODING,
        help="text encoding for script string payloads (default: %(default)s)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.output and len(args.inputs) != 1:
        parser.error("-o/--output can only be used with a single input file")

    for index, raw_path in enumerate(args.inputs):
        input_path = pathlib.Path(raw_path)
        output_path = pathlib.Path(args.output) if index == 0 and args.output else None
        target = disassemble_file(input_path, output_path, text_encoding=args.text_encoding)
        print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())