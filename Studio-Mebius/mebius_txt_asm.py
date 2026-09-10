#!/usr/bin/env python3

from __future__ import annotations

import argparse
import pathlib

import mebius_txt_op


def strip_line_comment(line: str) -> str:
    current: list[str] = []
    in_string = False
    escaped = False
    for char in line:
        if in_string:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            current.append(char)
            continue
        if char == ";":
            break
        current.append(char)
    return "".join(current).rstrip()


def parse_instruction_text(line: str) -> tuple[str, list[str]]:
    stripped = strip_line_comment(line).strip()
    if not stripped:
        raise ValueError("empty instruction line")
    parts = stripped.split(None, 1)
    mnemonic = parts[0]
    operands = mebius_txt_op.split_top_level(parts[1]) if len(parts) > 1 else []
    return mnemonic, operands


def assemble_source(
    text: str,
    spec: dict[str, object] | None = None,
    text_encoding: str | None = None,
) -> bytes:
    mebius_txt_op.set_text_encoding(text_encoding)
    _, spec_by_mnemonic = mebius_txt_op.build_spec_indexes(spec)
    header = 0
    labels: dict[str, int] = {}
    parsed: list[tuple[int, int, dict[str, object] | None, list[str], bytes]] = []
    offset = 2

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = strip_line_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith(".header"):
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise ValueError(f"invalid .header directive at line {line_number}")
            header = mebius_txt_op.parse_int_token(parts[1]) & 0xFFFF
            continue
        if line.endswith(":"):
            label = line[:-1].strip()
            if not label:
                raise ValueError(f"empty label at line {line_number}")
            labels[label] = offset
            continue
        if line.startswith(".raw"):
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise ValueError(f"invalid .raw directive at line {line_number}")
            raw_bytes = mebius_txt_op.parse_blob_token(parts[1])
            parsed.append((line_number, offset, None, [], raw_bytes))
            offset += len(raw_bytes)
            continue
        mnemonic, operands = parse_instruction_text(line)
        spec_entry = spec_by_mnemonic.get(mnemonic)
        if spec_entry is None:
            raise ValueError(f"unknown mnemonic at line {line_number}: {mnemonic}")
        parsed.append((line_number, offset, spec_entry, operands, b""))
        offset += mebius_txt_op.measure_instruction(spec_entry, operands)

    instruction_offsets = {
        instruction_offset
        for _, instruction_offset, spec_entry, _, _ in parsed
        if spec_entry is not None
    }
    for line_number, _, spec_entry, operands, _ in parsed:
        if spec_entry is None:
            continue
        for target in mebius_txt_op.resolve_asm_script_targets(spec_entry, operands, labels):
            if target not in instruction_offsets:
                raise ValueError(
                    f"line {line_number} resolves script target {target:#x}, but no instruction starts at that offset"
                )

    out = bytearray(header.to_bytes(2, "big"))
    for line_number, _, spec_entry, operands, raw_bytes in parsed:
        if spec_entry is None:
            out.extend(raw_bytes)
            continue
        try:
            out.extend(mebius_txt_op.encode_instruction(spec_entry, operands, labels))
        except Exception as exc:
            raise ValueError(f"failed to assemble line {line_number}: {exc}") from exc
    return bytes(out)


def assemble_file(
    input_path: pathlib.Path,
    output_path: pathlib.Path | None = None,
    text_encoding: str = mebius_txt_op.DEFAULT_TEXT_ENCODING,
) -> pathlib.Path:
    text = input_path.read_text(encoding="utf-8")
    data = assemble_source(text, mebius_txt_op.build_spec(), text_encoding=text_encoding)
    if output_path is None:
        if input_path.name.endswith(".asm.txt"):
            target = input_path.with_name(input_path.name[:-8])
        else:
            target = input_path.with_suffix("")
    else:
        target = output_path
    target.write_bytes(data)
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assemble human-readable asm.txt back into MEBIUS TXT binary")
    parser.add_argument("inputs", nargs="+", help="input asm.txt file(s)")
    parser.add_argument("-o", "--output", help="output TXT path; valid only with a single input")
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
        target = assemble_file(input_path, output_path, text_encoding=args.text_encoding)
        print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())