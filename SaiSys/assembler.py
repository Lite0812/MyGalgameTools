"""Assembler for the semantic Dual Colors SSB assembly format."""

from __future__ import annotations

import argparse
import codecs
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from opcodelist import DATA_XOR, Opcode, opcode_for_mnemonic, unsigned32


class AssemblyError(RuntimeError):
    """Raised for invalid assembly input."""


@dataclass
class Item:
    section: str
    kind: str
    args: tuple[str, ...]
    line_no: int
    text: str = ""


PLACEHOLDER_RE = re.compile(r"\{\{([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})*)\}\}")
LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _split_comment(line: str) -> str:
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ";" and not quoted:
            return line[:index]
    return line


def _parse_quoted(text: str, line_no: int) -> str:
    text = text.strip()
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        raise AssemblyError(f"line {line_no}: expected a quoted string")
    inner = text[1:-1]
    if '"' in inner:
        raise AssemblyError(f"line {line_no}: quotes inside TEXT must use byte placeholders")
    return inner


def _parse_inline_data_suffix(suffix: str, line_no: int) -> list[Item]:
    """Parse the compact Data part following a PUSHI operand."""

    suffix = suffix.strip()
    body: list[Item] = []
    if suffix.upper().startswith("TEXT "):
        opening_quote = suffix.find('"', 5)
        closing_quote = suffix.find('"', opening_quote + 1) if opening_quote >= 0 else -1
        if opening_quote < 0 or closing_quote < 0:
            raise AssemblyError(f"line {line_no}: inline TEXT is not terminated")
        body.append(
            Item(
                "data",
                "text",
                (),
                line_no,
                _parse_quoted(suffix[opening_quote : closing_quote + 1], line_no),
            )
        )
        suffix = suffix[closing_quote + 1 :].strip()
    if suffix:
        command, _, operand = suffix.partition(" ")
        if command.upper() != ".ZERO" or not operand.strip():
            raise AssemblyError(
                f"line {line_no}: inline Data supports TEXT followed by .zero only"
            )
        count = _parse_int(operand.strip(), line_no)
        if count < 0:
            raise AssemblyError(f"line {line_no}: .zero length must not be negative")
        body.append(Item("data", "zero", (str(count),), line_no))
    if not body:
        raise AssemblyError(f"line {line_no}: empty inline Data suffix")
    return body


def _parse_int(token: str, line_no: int) -> int:
    try:
        return int(token, 0)
    except ValueError as exc:
        raise AssemblyError(f"line {line_no}: invalid integer {token!r}") from exc


def _parse_byte_list(text: str, line_no: int) -> tuple[int, ...]:
    if not text.strip():
        raise AssemblyError(f"line {line_no}: .byte requires at least one value")
    values: list[int] = []
    for token in text.split(","):
        value = _parse_int(token.strip(), line_no)
        if not 0 <= value <= 0xFF:
            raise AssemblyError(f"line {line_no}: byte out of range: {value}")
        values.append(value)
    return tuple(values)


def parse_text_bytes(text: str, encoding: str, line_no: int) -> bytes:
    """Encode TEXT while treating ``{{AA:BB}}`` as literal bytes.

    ``\n`` represents the VM's three-byte line-break marker and ``\\`` a
    literal backslash.  Other backslash sequences remain ordinary text rather
    than Python-style escapes.
    """

    result = bytearray()
    plain: list[str] = []
    index = 0

    def flush_plain() -> None:
        if plain:
            try:
                result.extend("".join(plain).encode(encoding, errors="strict"))
            except UnicodeEncodeError as exc:
                raise AssemblyError(
                    f"line {line_no}: text cannot be encoded as {encoding}: {exc}"
                ) from exc
            plain.clear()

    while index < len(text):
        if text.startswith(r"\\", index):
            plain.append("\\")
            index += 2
            continue
        if text.startswith(r"\n", index):
            flush_plain()
            result.extend(b"\\nn")
            index += 2
            continue
        if text.startswith("{{", index):
            end = text.find("}}", index + 2)
            if end < 0:
                raise AssemblyError(f"line {line_no}: unterminated byte placeholder")
            token = text[index : end + 2]
            match = PLACEHOLDER_RE.fullmatch(token)
            if not match:
                raise AssemblyError(f"line {line_no}: invalid byte placeholder {token!r}")
            flush_plain()
            result.extend(int(part, 16) for part in match.group(1).split(":"))
            index = end + 2
            continue
        plain.append(text[index])
        index += 1
    flush_plain()
    return bytes(result)


def parse_assembly(
    path: Path,
    encoding_override: str | None,
    file_encoding_override: str | None = None,
) -> tuple[list[Item], str, str | None, str | None, int]:
    """Parse source lines into neutral items and header metadata."""

    raw_bytes = path.read_bytes()
    header_match = re.search(rb"(?mi)^\s*\.encoding\s+\"([^\"]+)\"", raw_bytes)
    file_header_match = re.search(rb"(?mi)^\s*\.file_encoding\s+\"([^\"]+)\"", raw_bytes)
    header_encoding = header_match.group(1).decode("ascii") if header_match else "cp932"
    file_header_encoding = file_header_match.group(1).decode("ascii") if file_header_match else "cp932"
    input_encoding = file_encoding_override or file_header_encoding
    try:
        raw = raw_bytes.decode(input_encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise AssemblyError(f"cannot decode {path} using {input_encoding}: {exc}") from exc
    current = "code"
    items: list[Item] = []
    encoding = encoding_override or header_encoding
    source_code: str | None = None
    source_data: str | None = None
    data_xor = DATA_XOR
    block_return_section: str | None = None

    for line_no, original in enumerate(raw.splitlines(), 1):
        line = _split_comment(original).strip()
        if not line:
            continue

        # Accept a label on its own line or before a statement.
        while ":" in line:
            candidate, remainder = line.split(":", 1)
            candidate = candidate.strip()
            if not LABEL_RE.fullmatch(candidate):
                break
            if candidate.lower().startswith("data_"):
                current = "data"
            elif candidate.lower().startswith("loc_") and current == "data":
                current = "code"
            items.append(Item(current, "label", (candidate,), line_no))
            line = remainder.strip()
            if not line:
                break
        if not line:
            continue

        if line.lower().startswith(".data_block"):
            if block_return_section is not None:
                raise AssemblyError(f"line {line_no}: nested .data_block is not allowed")
            if current != "code":
                raise AssemblyError(f"line {line_no}: .data_block must be embedded in the code section")
            operands = [part.strip() for part in line[len(".data_block") :].split(",")]
            if len(operands) != 2 or not LABEL_RE.fullmatch(operands[0]):
                raise AssemblyError(
                    f"line {line_no}: expected .data_block <label>, <order>"
                )
            order = _parse_int(operands[1], line_no)
            if order < 0:
                raise AssemblyError(f"line {line_no}: data block order must not be negative")
            block_return_section = current
            current = "data"
            items.append(Item("data", "data_block", (operands[0], str(order)), line_no))
            continue
        if line.lower() == ".end_data_block":
            if block_return_section is None:
                raise AssemblyError(f"line {line_no}: .end_data_block without .data_block")
            items.append(Item("data", "end_data_block", (), line_no))
            current = block_return_section
            block_return_section = None
            continue

        if line.lower().startswith(".section"):
            if block_return_section is not None:
                raise AssemblyError(f"line {line_no}: section change inside .data_block")
            parts = line.split()
            if len(parts) != 2 or parts[1].lower() not in {"code", "data"}:
                raise AssemblyError(f"line {line_no}: expected .section code or .section data")
            current = parts[1].lower()
            items.append(Item(current, "section", (current,), line_no))
            continue
        if line.lower().startswith(".encoding"):
            encoding = _parse_quoted(line[len(".encoding") :], line_no)
            try:
                codecs.lookup(encoding)
            except LookupError as exc:
                raise AssemblyError(f"line {line_no}: unknown encoding {encoding!r}") from exc
            continue
        if line.lower().startswith(".file_encoding"):
            file_encoding = _parse_quoted(line[len(".file_encoding") :], line_no)
            try:
                codecs.lookup(file_encoding)
            except LookupError as exc:
                raise AssemblyError(f"line {line_no}: unknown file encoding {file_encoding!r}") from exc
            continue
        if line.lower().startswith(".source_code"):
            source_code = _parse_quoted(line[len(".source_code") :], line_no)
            continue
        if line.lower().startswith(".source_data"):
            source_data = _parse_quoted(line[len(".source_data") :], line_no)
            continue
        if line.lower().startswith(".data_xor"):
            data_xor = _parse_int(line[len(".data_xor") :].strip(), line_no)
            if not 0 <= data_xor <= 0xFF:
                raise AssemblyError(f"line {line_no}: .data_xor must be a byte")
            continue

        command, _, operand_text = line.partition(" ")
        command_upper = command.upper()
        operand_text = operand_text.strip()

        if command_upper == ".ALIGN":
            if not operand_text:
                raise AssemblyError(f"line {line_no}: .align requires an alignment")
            alignment = _parse_int(operand_text, line_no)
            if alignment <= 0:
                raise AssemblyError(f"line {line_no}: .align must be positive")
            items.append(Item(current, "align", (str(alignment),), line_no))
            continue
        if command_upper == ".BYTE":
            if current != "data":
                raise AssemblyError(f"line {line_no}: .byte is only valid in the data section")
            items.append(Item(current, "byte", tuple(str(v) for v in _parse_byte_list(operand_text, line_no)), line_no))
            continue
        if command_upper == ".ZERO":
            if current != "data":
                raise AssemblyError(f"line {line_no}: .zero is only valid for data")
            if not operand_text:
                raise AssemblyError(f"line {line_no}: .zero requires a length")
            count = _parse_int(operand_text, line_no)
            if count < 0:
                raise AssemblyError(f"line {line_no}: .zero length must not be negative")
            items.append(Item(current, "zero", (str(count),), line_no))
            continue
        if command_upper == ".WORD":
            if current != "code":
                current = "code"
            if not operand_text:
                raise AssemblyError(f"line {line_no}: .word requires a value")
            items.append(Item(current, "word", (operand_text,), line_no))
            continue
        if command_upper == "TEXT":
            if current != "data":
                raise AssemblyError(f"line {line_no}: TEXT is only valid in the data section")
            items.append(Item(current, "text", (), line_no, _parse_quoted(operand_text, line_no)))
            continue

        # A data block has no explicit end marker in the unified format.  The
        # first code statement after its data directives resumes Code output.
        if current == "data":
            current = "code"

        op = opcode_for_mnemonic(command_upper)
        if command_upper == "PUSHI":
            if not operand_text:
                raise AssemblyError(f"line {line_no}: PUSHI requires an operand")
            operand, separator, inline_text = operand_text.partition(" ")
            if separator and inline_text.upper().startswith(("TEXT ", ".ZERO ")):
                if not operand.lower().startswith("data_"):
                    raise AssemblyError(
                        f"line {line_no}: inline TEXT requires a data_ label"
                    )
                _parse_inline_data_suffix(inline_text, line_no)
                items.append(Item(current, "pushi", (operand,), line_no))
                items.append(
                    Item(
                        "data",
                        "inline_data",
                        (operand, str(_data_label_order(operand, 0))),
                        line_no,
                        inline_text,
                    )
                )
            else:
                items.append(Item(current, "pushi", (operand_text,), line_no))
            continue
        if op is None:
            raise AssemblyError(f"line {line_no}: unknown instruction or directive {command!r}")
        if current != "code":
            raise AssemblyError(f"line {line_no}: instruction {command} is outside code section")
        operands = (operand_text,) if operand_text else ()
        if op.branch_kind:
            if len(operands) > 1:
                raise AssemblyError(f"line {line_no}: {command} accepts at most one target")
        elif operands:
            raise AssemblyError(f"line {line_no}: {command} does not accept operands")
        items.append(Item(current, "opcode", (command_upper,) + operands, line_no))

    if block_return_section is not None:
        raise AssemblyError("unterminated .data_block at end of file")
    return items, encoding, source_code, source_data, data_xor


def _align(value: int, alignment: int) -> int:
    remainder = value % alignment
    return value if remainder == 0 else value + alignment - remainder


def _is_label(token: str) -> bool:
    return bool(LABEL_RE.fullmatch(token))


def _code_item_words(item: Item) -> int:
    if item.kind == "pushi" or item.kind == "word" or item.kind == "opcode":
        if item.kind == "opcode":
            op = opcode_for_mnemonic(item.args[0])
            if op is not None and op.branch_kind and len(item.args) == 2:
                return 2
        return 1
    return 0


def _data_item_size(item: Item, encoding: str) -> int:
    if item.kind == "byte":
        return len(item.args)
    if item.kind == "text":
        return len(parse_text_bytes(item.text, encoding, item.line_no)) + 1
    if item.kind == "zero":
        return int(item.args[0])
    return 0


def _data_label_order(name: str, fallback: int) -> int:
    prefix = "data_"
    suffix = name[len(prefix) :]
    if name.lower().startswith(prefix) and suffix and re.fullmatch(r"[0-9A-Fa-f]+", suffix):
        return int(suffix, 16) * 4
    return fallback


def _ordered_inline_blocks(items: list[Item]) -> list[tuple[int, str, int, list[Item]]]:
    """Collect unified data labels and their bodies in Data order."""

    blocks: list[tuple[int, str, int, list[Item]]] = []
    current: tuple[int, str, int, list[Item]] | None = None
    explicit_block = False
    source_order = 0
    for item in items:
        if item.kind == "inline_data":
            blocks.append(
                (
                    int(item.args[1]),
                    item.args[0],
                    item.line_no,
                    _parse_inline_data_suffix(item.text, item.line_no),
                )
            )
            continue
        if item.kind == "data_block":
            if current is not None:
                raise AssemblyError(f"line {item.line_no}: nested data block")
            current = (int(item.args[1]), item.args[0], item.line_no, [])
            explicit_block = True
            continue
        if item.kind == "end_data_block":
            if current is None:
                raise AssemblyError(f"line {item.line_no}: unmatched data block end")
            blocks.append(current)
            current = None
            explicit_block = False
            continue
        if item.kind == "label" and item.section == "data":
            if current is not None:
                blocks.append(current)
            name = item.args[0]
            current = (_data_label_order(name, source_order), name, item.line_no, [])
            explicit_block = False
            source_order += 1
            continue
        if current is not None and item.section == "data":
            current[3].append(item)
    if current is not None:
        if explicit_block:
            raise AssemblyError(f"line {current[2]}: unterminated data block")
        blocks.append(current)
    blocks.sort(key=lambda block: block[0])
    for left, right in zip(blocks, blocks[1:]):
        if left[0] == right[0]:
            raise AssemblyError(
                f"line {right[2]}: duplicate data block order 0x{right[0]:X}"
            )
    return blocks


def _legacy_data_items(items: list[Item]) -> list[Item]:
    return [
        item
        for item in items
        if item.section == "data" and item.kind not in {"section", "data_block", "end_data_block"}
    ]


def first_pass(items: list[Item], encoding: str) -> tuple[dict[str, int], dict[str, int], int, int]:
    code_labels: dict[str, int] = {}
    data_labels: dict[str, int] = {}
    code_pc = 0
    data_offset = 0
    current = "code"
    for item in items:
        if item.kind == "section":
            current = item.args[0]
            continue
        if item.section != current:
            continue
        if item.kind == "label" and current == "code":
            name = item.args[0]
            if name in code_labels or name in data_labels:
                raise AssemblyError(f"line {item.line_no}: duplicate label {name}")
            code_labels[name] = code_pc
            continue
        if item.kind == "align":
            alignment = int(item.args[0])
            if current == "data":
                data_offset = _align(data_offset, alignment)
            continue
        if current == "code":
            code_pc += _code_item_words(item)

    inline_blocks = _ordered_inline_blocks(items)
    if inline_blocks:
        for _, name, line_no, body in inline_blocks:
            data_offset = _align(data_offset, 4)
            if name in code_labels or name in data_labels:
                raise AssemblyError(f"line {line_no}: duplicate label {name}")
            data_labels[name] = data_offset
            for item in body:
                if item.kind == "label":
                    label = item.args[0]
                    if label in code_labels or label in data_labels:
                        raise AssemblyError(f"line {item.line_no}: duplicate label {label}")
                    data_labels[label] = data_offset
                elif item.kind == "align":
                    data_offset = _align(data_offset, int(item.args[0]))
                else:
                    data_offset += _data_item_size(item, encoding)
    else:
        for item in _legacy_data_items(items):
            if item.kind == "label":
                name = item.args[0]
                if name in code_labels or name in data_labels:
                    raise AssemblyError(f"line {item.line_no}: duplicate label {name}")
                data_labels[name] = data_offset
            elif item.kind == "align":
                data_offset = _align(data_offset, int(item.args[0]))
            else:
                data_offset += _data_item_size(item, encoding)
    return code_labels, data_labels, code_pc, data_offset


def _resolve_code_operand(token: str, labels: dict[str, int], line_no: int) -> int:
    if _is_label(token):
        if token not in labels:
            raise AssemblyError(f"line {line_no}: unknown code label {token}")
        return labels[token]
    return _parse_int(token, line_no)


def _resolve_push_operand(
    token: str,
    *,
    pc: int,
    next_op: Opcode | None,
    code_labels: dict[str, int],
    data_labels: dict[str, int],
    line_no: int,
) -> int:
    if _is_label(token):
        if token in data_labels:
            offset = data_labels[token]
            if offset % 4:
                raise AssemblyError(f"line {line_no}: Data label {token} is not 4-byte aligned")
            return offset // 4
        if token not in code_labels:
            raise AssemblyError(f"line {line_no}: unknown label {token}")
        target = code_labels[token]
        if next_op is not None and next_op.relative:
            return unsigned32(target - (pc + 2))
        return unsigned32(target)
    return unsigned32(_parse_int(token, line_no))


def _next_code_opcode(items: list[Item], index: int) -> Opcode | None:
    # A PUSHI immediately followed by a relative branch is the explicit
    # stack form accepted by the assembler.  Look only through labels and
    # section markers; an intervening executable item changes the stack.
    for candidate_index in range(index + 1, len(items)):
        candidate = items[candidate_index]
        if candidate.section != "code":
            continue
        if candidate.kind in {"section", "label", "align"}:
            continue
        if candidate.kind == "opcode":
            return opcode_for_mnemonic(candidate.args[0])
        return None
    return None


def assemble_code(
    items: list[Item],
    code_labels: dict[str, int],
    data_labels: dict[str, int],
) -> bytes:
    output = bytearray()
    current = "code"
    pc = 0
    for index, item in enumerate(items):
        if item.kind == "section":
            current = item.args[0]
            continue
        if current != "code" or item.section != "code":
            continue
        if item.kind in {"label", "align"}:
            continue
        if item.kind == "pushi":
            next_op = _next_code_opcode(items, index)
            value = _resolve_push_operand(
                item.args[0],
                pc=pc,
                next_op=next_op,
                code_labels=code_labels,
                data_labels=data_labels,
                line_no=item.line_no,
            )
            output.extend(struct.pack("<I", value))
            pc += 1
            continue
        if item.kind == "word":
            token = item.args[0]
            value = _resolve_push_operand(
                token,
                pc=pc,
                next_op=None,
                code_labels=code_labels,
                data_labels=data_labels,
                line_no=item.line_no,
            )
            output.extend(struct.pack("<I", value))
            pc += 1
            continue
        if item.kind != "opcode":
            continue
        op = opcode_for_mnemonic(item.args[0])
        if op is None:
            raise AssemblyError(f"line {item.line_no}: unknown opcode {item.args[0]}")
        if op.branch_kind and len(item.args) == 2:
            target = _resolve_code_operand(item.args[1], code_labels, item.line_no)
            if op.relative:
                target = target - (pc + 2)
            output.extend(struct.pack("<I", unsigned32(target)))
            output.extend(struct.pack("<I", op.value))
            pc += 2
        else:
            output.extend(struct.pack("<I", op.value))
            pc += 1
    return bytes(output)


def assemble_data(items: list[Item], encoding: str) -> bytes:
    output = bytearray()
    inline_blocks = _ordered_inline_blocks(items)
    if inline_blocks:
        data_items: list[Item] = []
        for _, _, line_no, body in inline_blocks:
            data_items.append(Item("data", "align", ("4",), line_no))
            data_items.extend(body)
    else:
        data_items = _legacy_data_items(items)
    for item in data_items:
        if item.kind == "label":
            continue
        if item.kind == "align":
            alignment = int(item.args[0])
            target = _align(len(output), alignment)
            output.extend(b"\0" * (target - len(output)))
            continue
        if item.kind == "byte":
            output.extend(int(value, 0) for value in item.args)
            continue
        if item.kind == "zero":
            output.extend(b"\0" * int(item.args[0]))
            continue
        if item.kind == "text":
            output.extend(parse_text_bytes(item.text, encoding, item.line_no))
            output.append(0)
    target = _align(len(output), 4)
    output.extend(b"\0" * (target - len(output)))
    return bytes(output)


def _default_outputs(asm_path: Path, source_code: str | None, source_data: str | None) -> tuple[Path, Path]:
    if source_code:
        code_path = asm_path.parent / (Path(source_code).name + ".rebuild")
    else:
        code_path = asm_path.with_suffix(".rebuild")
    if source_data:
        data_path = asm_path.parent / (Path(source_data).name + ".rebuild")
    else:
        data_path = code_path.with_name(code_path.stem + ".data" + code_path.suffix)
    return code_path, data_path


def assemble_file(
    asm_path: Path,
    *,
    code_output: Path | None = None,
    data_output: Path | None = None,
    encoding_override: str | None = None,
    file_encoding_override: str | None = None,
) -> tuple[Path, Path]:
    items, encoding, source_code, source_data, data_xor = parse_assembly(
        asm_path,
        encoding_override,
        file_encoding_override,
    )
    code_labels, data_labels, _, _ = first_pass(items, encoding)
    code = assemble_code(items, code_labels, data_labels)
    data_plain = assemble_data(items, encoding)
    data = bytes(byte ^ data_xor for byte in data_plain)
    if code_output is None or data_output is None:
        default_code, default_data = _default_outputs(asm_path, source_code, source_data)
        code_output = code_output or default_code
        data_output = data_output or default_data
    code_output.parent.mkdir(parents=True, exist_ok=True)
    data_output.parent.mkdir(parents=True, exist_ok=True)
    code_output.write_bytes(code)
    data_output.write_bytes(data)
    return code_output, data_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble Dual Colors semantic SSB assembly")
    parser.add_argument("inputs", nargs="+", type=Path, help="asm.txt file(s) or directories")
    parser.add_argument("-o", "--output", type=Path, help="Code.ssb output for one input")
    parser.add_argument("--data-output", type=Path, help="Data.ssb output for one input")
    parser.add_argument("--encoding", help="override the script .encoding directive")
    parser.add_argument("--file-encoding", help="override the assembly file encoding")
    args = parser.parse_args(argv)
    if (args.output is not None or args.data_output is not None) and len(args.inputs) != 1:
        parser.error("output options require exactly one input")

    try:
        for input_path in args.inputs:
            if input_path.is_dir():
                sources = sorted(input_path.rglob("*.asm.txt"))
                if not sources:
                    sources = sorted(input_path.rglob("asm.txt"))
                if not sources:
                    raise AssemblyError(f"no asm.txt found below {input_path}")
            else:
                sources = [input_path]
            for source in sources:
                code_output = args.output if len(args.inputs) == 1 and source == input_path else None
                data_output = args.data_output if len(args.inputs) == 1 and source == input_path else None
                code_path, data_path = assemble_file(
                    source,
                    code_output=code_output,
                    data_output=data_output,
                    encoding_override=args.encoding,
                    file_encoding_override=args.file_encoding,
                )
                print(f"wrote {code_path}")
                print(f"wrote {data_path}")
    except (LookupError, OSError, AssemblyError) as exc:
        print(f"assembler: error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
