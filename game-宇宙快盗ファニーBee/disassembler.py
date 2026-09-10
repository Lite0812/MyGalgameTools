"""Disassembler for ADV.COM scenario members.

Usage:
    python disassembler.py member_001.bin
    python disassembler.py ADISK/members BDISK/members
    python disassembler.py member_001.bin -o asm.txt --encoding cp932
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from opcodelist import EXPR_OPERATORS, OPCODES, is_text_lead, is_top_level_text_start


class DisassemblyError(ValueError):
    def __init__(self, message: str, offset: int | None = None) -> None:
        if offset is not None:
            message = f"offset 0x{offset:08X}: {message}"
        super().__init__(message)
        self.offset = offset


@dataclass(frozen=True)
class ExprToken:
    kind: str
    value: int | str


@dataclass(frozen=True)
class Expression:
    tokens: tuple[ExprToken, ...]


@dataclass(frozen=True)
class VariableRef:
    kind: str
    index: int


@dataclass
class Record:
    offset: int
    mnemonic: str
    operands: tuple[object, ...]
    end: int


class Reader:
    def __init__(self, data: bytes, position: int, limit: int) -> None:
        self.data = data
        self.position = position
        self.limit = limit

    def read_u8(self) -> int:
        if self.position >= self.limit:
            raise DisassemblyError("unexpected end of structured script", self.position)
        value = self.data[self.position]
        self.position += 1
        return value

    def read_u16le(self) -> int:
        low = self.read_u8()
        high = self.read_u8()
        return low | (high << 8)

    def read_u16be(self) -> int:
        high = self.read_u8()
        low = self.read_u8()
        return (high << 8) | low


def _read_varref(reader: Reader) -> VariableRef:
    first = reader.read_u8()
    if first & 0x40:
        index = ((first & 0x3F) << 8) | reader.read_u8()
        kind = "V14" if first & 0x80 else "VRAW14"
    else:
        index = first & 0x3F
        kind = "V6" if first & 0x80 else "VRAW6"
    return VariableRef(kind, index)


def _read_expression(reader: Reader) -> Expression:
    tokens: list[ExprToken] = []
    depth = 0
    start = reader.position
    while True:
        token_offset = reader.position
        byte = reader.read_u8()
        if byte == 0x7F:
            if depth != 1:
                raise DisassemblyError(
                    f"expression ends with stack depth {depth}, expected 1",
                    token_offset,
                )
            return Expression(tuple(tokens))
        if byte & 0x80:
            if byte & 0x40:
                index = ((byte & 0x3F) << 8) | reader.read_u8()
                tokens.append(ExprToken("V14", index))
            else:
                tokens.append(ExprToken("V6", byte & 0x3F))
            depth += 1
            continue
        if byte < 0x77:
            if byte & 0x40:
                tokens.append(ExprToken("I6", byte & 0x3F))
            else:
                value = (byte << 8) | reader.read_u8()
                tokens.append(ExprToken("I14", value))
            depth += 1
            continue
        operator = EXPR_OPERATORS.get(byte)
        if operator is None:
            raise DisassemblyError("invalid expression token", token_offset)
        if depth < 2:
            raise DisassemblyError(f"{operator} causes expression stack underflow", token_offset)
        tokens.append(ExprToken("OP", operator))
        depth -= 1
        if reader.position <= start:
            raise AssertionError("reader did not advance")


def _read_text_atom(reader: Reader) -> bytes:
    start = reader.position
    first = reader.read_u8()
    if not is_top_level_text_start(first):
        raise DisassemblyError("byte is not a top-level text atom", start)
    if is_text_lead(first):
        reader.read_u8()
    return reader.data[start : reader.position]


def _read_colon_text(reader: Reader) -> bytes:
    result = bytearray()
    while True:
        offset = reader.position
        first = reader.read_u8()
        if first == 0x3A:
            return bytes(result)
        result.append(first)
        if is_text_lead(first):
            result.append(reader.read_u8())
        if reader.position <= offset:
            raise AssertionError("reader did not advance")


def _parse_instruction(reader: Reader, choice_open: bool) -> tuple[Record, bool]:
    start = reader.position
    opcode = reader.read_u8()
    if opcode not in OPCODES:
        raise DisassemblyError(f"undefined top-level opcode 0x{opcode:02X}", start)

    operands: list[object] = []
    mnemonic = OPCODES[opcode].mnemonic

    if opcode == 0x21:
        operands.extend((_read_varref(reader), _read_expression(reader)))
    elif opcode == 0x24:
        if choice_open:
            mnemonic = "CHOICE_END"
            choice_open = False
        else:
            mnemonic = "CHOICE_BEGIN"
            operands.append(reader.read_u16le())
            choice_open = True
    elif opcode in {0x25, 0x26, 0x47, 0x4C, 0x51}:
        operands.append(_read_expression(reader))
    elif opcode == 0x40:
        operands.append(reader.read_u16le())
    elif opcode == 0x42:
        operands.append(reader.read_u8())
        operands.extend(_read_expression(reader) for _ in range(6))
    elif opcode == 0x45:
        operands.extend(_read_expression(reader) for _ in range(6))
    elif opcode == 0x48:
        operands.extend((reader.read_u8(), _read_expression(reader)))
    elif opcode == 0x49:
        operands.extend(_read_expression(reader) for _ in range(6))
    elif opcode == 0x4A:
        operands.extend(_read_expression(reader) for _ in range(2))
    elif opcode == 0x4B:
        operands.append(reader.read_u8())
    elif opcode == 0x4D:
        operands.append(_read_colon_text(reader))
    elif opcode == 0x4E:
        operands.append(reader.read_u8())
        operands.extend(_read_expression(reader) for _ in range(2))
    elif opcode == 0x4F:
        # Both runtime variants occupy two expression-shaped fields. In pack
        # mode the second one must be a bare variable reference plus 0x7F.
        operands.extend(_read_expression(reader) for _ in range(2))
    elif opcode == 0x50:
        operands.extend(_read_expression(reader) for _ in range(4))
    elif opcode == 0x53:
        operands.append(reader.read_u8())
    elif opcode in {0x54, 0x55, 0x56, 0x59, 0x5A}:
        operands.extend(_read_expression(reader) for _ in range(2))
    elif opcode == 0x57:
        operands.extend(_read_expression(reader) for _ in range(3))
    elif opcode == 0x58:
        operands.append(reader.read_u8())
    elif opcode == 0x5C:
        target = reader.read_u16le()
        if target == 0:
            mnemonic = "LOCAL_RET"
        else:
            operands.append(target)
    elif opcode == 0x7B:
        operands.extend((_read_expression(reader), reader.read_u16le()))
    elif opcode == 0x7D:
        expression = _read_expression(reader)
        displacement = reader.read_u8()
        operands.extend((expression, reader.position + displacement))

    return Record(start, mnemonic, tuple(operands), reader.position), choice_open


def _parse_script(data: bytes) -> tuple[list[Record], set[int], int, int]:
    if len(data) < 4:
        raise DisassemblyError("script member is too short")
    if len(data) % 256:
        raise DisassemblyError("member length is not a multiple of 256 bytes")

    terminal = int.from_bytes(data[0:2], "little")
    if terminal < 2 or terminal >= len(data) - 1:
        raise DisassemblyError(f"terminal offset 0x{terminal:04X} is outside the member")

    reader = Reader(data, 2, terminal)
    records: list[Record] = []
    choice_open = False
    while reader.position < terminal:
        start = reader.position
        byte = data[start]
        if is_top_level_text_start(byte):
            raw = _read_text_atom(reader)
            records.append(Record(start, "TEXT", (raw,), reader.position))
        else:
            record, choice_open = _parse_instruction(reader, choice_open)
            records.append(record)

    if reader.position != terminal:
        raise DisassemblyError("instruction overlaps terminal offset", reader.position)
    if choice_open:
        raise DisassemblyError("unterminated CHOICE_BEGIN/CHOICE_END pair", terminal)

    # The host parses a separate record stream at terminal_offset. The current
    # corpus uses a single 0x5D terminator, but the implemented [/: forms are
    # retained for compatible members.
    tail_reader = Reader(data, terminal, len(data))
    while True:
        start = tail_reader.position
        marker = tail_reader.read_u8()
        if marker == 0x5B:
            key = tail_reader.read_u16be()
            target = tail_reader.read_u16be()
            records.append(Record(start, "TAIL_ENTRY", (key, target), tail_reader.position))
            continue
        if marker == 0x3A:
            condition = _read_expression(tail_reader)
            key = tail_reader.read_u16be()
            target = tail_reader.read_u16be()
            records.append(Record(start, "TAIL_IF", (condition, key, target), tail_reader.position))
            continue
        if marker == 0x5D:
            records.append(Record(start, "END_BLOCK", (), tail_reader.position))
        else:
            records.append(Record(start, "TAIL_END", (marker,), tail_reader.position))
        break

    eof_offset = tail_reader.position
    if tail_reader.read_u8() != 0x1A:
        raise DisassemblyError("missing 0x1A member terminator", eof_offset)
    if any(data[tail_reader.position :]):
        first_nonzero = next(
            index
            for index in range(tail_reader.position, len(data))
            if data[index] != 0
        )
        raise DisassemblyError("non-zero data after 0x1A terminator", first_nonzero)

    labels = {2, terminal}
    for record in records:
        if record.mnemonic in {"JMP", "LOCAL_CALL", "CHOICE_BEGIN"}:
            labels.add(int(record.operands[-1]))
        elif record.mnemonic in {"IF_FALSE_JMP", "IF_FALSE_SKIP"}:
            labels.add(int(record.operands[-1]))
        elif record.mnemonic in {"TAIL_ENTRY", "TAIL_IF"}:
            labels.add(int(record.operands[-1]))

    boundaries = {record.offset for record in records}
    invalid = sorted(target for target in labels if target not in boundaries)
    if invalid:
        rendered = ", ".join(f"0x{target:04X}" for target in invalid[:8])
        raise DisassemblyError(f"control-flow target is not a record boundary: {rendered}")

    return records, labels, terminal, eof_offset


def _format_label(offset: int) -> str:
    return f"loc_{offset:08X}"


def _format_varref(reference: VariableRef) -> str:
    width = 2 if reference.kind.endswith("6") else 4
    return f"{reference.kind}(0x{reference.index:0{width}X})"


def _format_expression(expression: Expression) -> str:
    pieces: list[str] = []
    for token in expression.tokens:
        if token.kind == "OP":
            pieces.append(str(token.value))
        else:
            width = 2 if token.kind.endswith("6") else 4
            pieces.append(f"{token.kind}(0x{int(token.value):0{width}X})")
    return "[" + " ".join(pieces) + "]"


def _placeholder(raw: bytes) -> str:
    return "{{" + ":".join(f"{byte:02X}" for byte in raw) + "}}"


def _decode_text(raw: bytes, encoding: str) -> str:
    result: list[str] = []
    position = 0
    while position < len(raw):
        if raw.startswith(b"\\nn", position):
            result.append("\\n")
            position += 3
            continue

        first = raw[position]
        width = 2 if is_text_lead(first) and position + 1 < len(raw) else 1
        atom = raw[position : position + width]
        position += width

        if atom == b"\\":
            result.append("\\\\")
            continue
        if atom == b'"':
            result.append('\\"')
            continue
        if atom == b"\r":
            result.append("{{0D}}")
            continue
        if atom == b"\n":
            result.append("{{0A}}")
            continue
        try:
            text = atom.decode(encoding)
        except UnicodeDecodeError:
            result.append(_placeholder(atom))
            continue
        if not text or any(ord(char) < 0x20 or 0xE000 <= ord(char) <= 0xF8FF for char in text):
            result.append(_placeholder(atom))
            continue
        result.append(text)
    return "".join(result)


def _quote_text(raw: bytes, encoding: str) -> str:
    return '"' + _decode_text(raw, encoding) + '"'


def _format_record(record: Record, encoding: str) -> str:
    mnemonic = record.mnemonic
    operands = list(record.operands)
    if mnemonic == "TEXT":
        return f"TEXT {_quote_text(bytes(operands[0]), encoding)}"
    if mnemonic == "DEFINE_NAME":
        return f"DEFINE_NAME {_quote_text(bytes(operands[0]), encoding)}"
    if mnemonic == "LOCAL_RET":
        return "LOCAL_RET"

    rendered: list[str] = []
    for index, operand in enumerate(operands):
        if isinstance(operand, Expression):
            rendered.append(_format_expression(operand))
        elif isinstance(operand, VariableRef):
            rendered.append(_format_varref(operand))
        elif mnemonic in {"JMP", "LOCAL_CALL", "CHOICE_BEGIN"}:
            rendered.append(_format_label(int(operand)))
        elif mnemonic in {"IF_FALSE_JMP", "IF_FALSE_SKIP"} and index == len(operands) - 1:
            rendered.append(_format_label(int(operand)))
        elif mnemonic in {"TAIL_ENTRY", "TAIL_IF"} and index == len(operands) - 1:
            rendered.append(_format_label(int(operand)))
        else:
            value = int(operand)
            width = 2 if value <= 0xFF else 4
            rendered.append(f"0x{value:0{width}X}")
    return mnemonic if not rendered else f"{mnemonic} " + ", ".join(rendered)


def disassemble_bytes(data: bytes, source_name: str = "script.bin", encoding: str = "cp932") -> str:
    # Validate the requested codec before parsing so failures are actionable.
    "".encode(encoding)
    records, labels, terminal, _ = _parse_script(data)

    lines = [
        "; ADV.COM scenario assembly",
        f'.encoding "{encoding}"',
        f'.source "{source_name.replace(chr(34), chr(95))}"',
        f".member_size 0x{len(data):X}",
        f".terminal {_format_label(terminal)}",
    ]

    index = 0
    while index < len(records):
        record = records[index]
        if record.offset in labels:
            lines.append("")
            lines.append(f"{_format_label(record.offset)}:")

        if record.mnemonic == "TEXT":
            raw = bytearray(record.operands[0])
            end = record.end
            index += 1
            while index < len(records):
                following = records[index]
                if following.mnemonic != "TEXT" or following.offset in labels or following.offset != end:
                    break
                raw.extend(following.operands[0])
                end = following.end
                index += 1
            lines.append("    " + _format_record(Record(record.offset, "TEXT", (bytes(raw),), end), encoding))
            continue

        lines.append("    " + _format_record(record, encoding))
        index += 1

    lines.extend(("", "    .eof", ""))
    return "\n".join(lines)


def disassemble_file(input_path: Path, output_path: Path | None = None, encoding: str = "cp932") -> Path:
    input_path = input_path.resolve()
    if output_path is None:
        output_path = input_path.with_name(input_path.stem + ".asm.txt")
    text = disassemble_bytes(input_path.read_bytes(), input_path.name, encoding)
    output_path.write_text(text, encoding="utf-8", newline="\n")
    return output_path


def _expand_inputs(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path.is_file():
            result.append(path)
            continue
        if not path.is_dir():
            raise FileNotFoundError(path)
        members = sorted(path.rglob("member_*.bin"))
        if members:
            result.extend(members)
        else:
            result.extend(sorted(candidate for candidate in path.iterdir() if candidate.is_file()))
    # Preserve order while removing duplicate paths.
    return list(dict.fromkeys(candidate.resolve() for candidate in result))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Disassemble ADV.COM scenario members.")
    parser.add_argument("inputs", nargs="+", type=Path, help="script member files or directories")
    parser.add_argument("-o", "--output", type=Path, help="output path (single input only)")
    parser.add_argument("--encoding", default="cp932", help="script text encoding (default: cp932)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        inputs = _expand_inputs(args.inputs)
        if not inputs:
            raise ValueError("no input files found")
        if args.output is not None and len(inputs) != 1:
            raise ValueError("--output can only be used with one input file")
        for input_path in inputs:
            output = disassemble_file(input_path, args.output, args.encoding)
            print(f"{input_path} -> {output}")
        return 0
    except (OSError, UnicodeError, ValueError) as error:
        print(f"disassembler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
