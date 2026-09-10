"""Assembler for the semantic assembly emitted by :mod:`disassembler`.

The assembler deliberately keeps the short/long expression encodings used in
the source file.  This is required for byte-for-byte reconstruction: changing
``I6`` to an equivalent ``I14`` changes the script even though the VM value is
the same.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from opcodelist import EXPR_OPERATOR_BYTES, MNEMONIC_TO_OPCODE


class AssemblyError(ValueError):
    def __init__(self, message: str, line: int | None = None) -> None:
        if line is not None:
            message = f"line {line}: {message}"
        super().__init__(message)
        self.line = line


@dataclass
class Statement:
    mnemonic: str
    operands: tuple[str, ...]
    line: int


@dataclass
class Source:
    statements: list[Statement]
    labels_at: dict[str, int]
    terminal_label: str | None
    member_size: int | None
    encoding: str | None


_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:$")
_INT_RE = re.compile(r"^[+-]?(?:0[xX][0-9A-Fa-f]+|[0-9]+)$")
_TOKEN_RE = re.compile(r"^(I6|I14|V6|V14|VRAW6|VRAW14)\((0[xX][0-9A-Fa-f]+|[0-9]+)\)$")
_PLACEHOLDER_RE = re.compile(r"\{\{([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})*)\}\}")


def _strip_comment(line: str) -> str:
    quoted = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if quoted and char == "\\":
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            continue
        if char == ";" and not quoted:
            return line[:index]
    return line


def _split_operands(text: str, line: int) -> tuple[str, ...]:
    result: list[str] = []
    start = 0
    square = 0
    quoted = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quoted and char == "\\":
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
        elif not quoted and char == "[":
            square += 1
        elif not quoted and char == "]":
            square -= 1
            if square < 0:
                raise AssemblyError("unexpected ']'", line)
        elif not quoted and char == "," and square == 0:
            part = text[start:index].strip()
            if not part:
                raise AssemblyError("empty operand", line)
            result.append(part)
            start = index + 1
    if quoted or square:
        raise AssemblyError("unterminated quote or expression", line)
    tail = text[start:].strip()
    if tail:
        result.append(tail)
    return tuple(result)


def _parse_int(token: str, line: int, *, maximum: int | None = None) -> int:
    token = token.strip()
    if not _INT_RE.fullmatch(token):
        raise AssemblyError(f"expected integer, got {token!r}", line)
    value = int(token, 0)
    if value < 0 or (maximum is not None and value > maximum):
        bound = f" <= 0x{maximum:X}" if maximum is not None else ""
        raise AssemblyError(f"integer out of range: {token!r}{bound}", line)
    return value


def _parse_quoted(token: str, line: int, encoding: str) -> bytes:
    token = token.strip()
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        raise AssemblyError("expected a quoted string", line)
    text = token[1:-1]
    output = bytearray()
    position = 0
    while position < len(text):
        if text[position] == "{" and text.startswith("{{", position):
            match = _PLACEHOLDER_RE.match(text, position)
            if not match:
                raise AssemblyError("invalid byte placeholder", line)
            output.extend(int(piece, 16) for piece in match.group(1).split(":"))
            position = match.end()
            continue
        if text[position] == "\\":
            if position + 1 >= len(text):
                raise AssemblyError("trailing backslash in string", line)
            next_char = text[position + 1]
            if next_char == "n":
                output.extend((0x5C, 0x6E, 0x6E))
                position += 2
                continue
            if next_char == "\\":
                output.append(0x5C)
                position += 2
                continue
            if next_char == '"':
                output.append(0x22)
                position += 2
                continue
            # Only the documented escapes have special meaning.  Preserve
            # every other backslash literally and encode the following text.
            output.extend("\\".encode(encoding))
            position += 1
            continue
        if text[position] == "{" and not text.startswith("{{", position):
            output.extend("{".encode(encoding))
            position += 1
            continue
        start = position
        while position < len(text) and text[position] not in "{\\":
            position += 1
        chunk = text[start:position]
        try:
            output.extend(chunk.encode(encoding))
        except UnicodeEncodeError as error:
            raise AssemblyError(f"cannot encode text as {encoding}: {error}", line) from error
    return bytes(output)


def _parse_expression(token: str, line: int) -> bytes:
    token = token.strip()
    if len(token) < 2 or token[0] != "[" or token[-1] != "]":
        raise AssemblyError("expected [postfix expression]", line)
    inner = token[1:-1].strip()
    if not inner:
        raise AssemblyError("empty expression", line)
    encoded = bytearray()
    depth = 0
    for part in inner.split():
        match = _TOKEN_RE.fullmatch(part)
        if match:
            kind, raw_value = match.groups()
            value = int(raw_value, 0)
            if kind == "I6":
                if value > 0x36:
                    raise AssemblyError("I6 value must be <= 0x36", line)
                encoded.append(0x40 | value)
            elif kind == "I14":
                if value > 0x3FFF:
                    raise AssemblyError("I14 value must be <= 0x3FFF", line)
                encoded.extend(((value >> 8) & 0x3F, value & 0xFF))
            elif kind == "V6":
                if value > 0x3F:
                    raise AssemblyError("V6 index must be <= 0x3F", line)
                encoded.append(0x80 | value)
            elif kind == "V14":
                if value > 0x3FFF:
                    raise AssemblyError("V14 index must be <= 0x3FFF", line)
                encoded.extend((0xC0 | ((value >> 8) & 0x3F), value & 0xFF))
            elif kind == "VRAW6":
                if value > 0x3F:
                    raise AssemblyError("VRAW6 index must be <= 0x3F", line)
                encoded.append(value)
            else:  # VRAW14
                if value > 0x3FFF:
                    raise AssemblyError("VRAW14 index must be <= 0x3FFF", line)
                encoded.extend((0x40 | ((value >> 8) & 0x3F), value & 0xFF))
            depth += 1
            continue
        operator = EXPR_OPERATOR_BYTES.get(part.upper())
        if operator is None:
            raise AssemblyError(f"unknown expression token {part!r}", line)
        if depth < 2:
            raise AssemblyError(f"operator {part!r} causes stack underflow", line)
        encoded.append(operator)
        depth -= 1
    if depth != 1:
        raise AssemblyError(f"expression ends with stack depth {depth}, expected 1", line)
    encoded.append(0x7F)
    return bytes(encoded)


def _instruction_size(statement: Statement, encoding: str) -> int:
    mnemonic, operands, line = statement.mnemonic, statement.operands, statement.line
    if mnemonic == "TEXT" or mnemonic == "DEFINE_NAME":
        if len(operands) != 1:
            raise AssemblyError(f"{mnemonic} expects one string", line)
        return len(_parse_quoted(operands[0], line, encoding)) + (2 if mnemonic == "DEFINE_NAME" else 0)
    if mnemonic == "CHOICE_BEGIN":
        if len(operands) != 1:
            raise AssemblyError("CHOICE_BEGIN expects one target", line)
        return 3
    if mnemonic in {"CHOICE_END", "LOCAL_RET", "END_BLOCK", "TEXT_BREAK", "RESTART_PAGE", "ADVANCE_LINE"}:
        if operands:
            raise AssemblyError(f"{mnemonic} takes no operands", line)
        return 1 if mnemonic != "LOCAL_RET" else 3
    if mnemonic == "TAIL_END":
        return 1
    if mnemonic == "TAIL_ENTRY":
        return 5
    if mnemonic == "TAIL_IF":
        if len(operands) != 3:
            raise AssemblyError("TAIL_IF expects expression, key, target", line)
        return 1 + len(_parse_expression(operands[0], line)) + 4
    definition = MNEMONIC_TO_OPCODE.get(mnemonic)
    if definition is None:
        raise AssemblyError(f"unknown mnemonic {mnemonic!r}", line)
    if mnemonic in {"JMP", "LOCAL_CALL", "IF_FALSE_JMP"}:
        expr_size = 0
        if mnemonic == "IF_FALSE_JMP":
            if len(operands) != 2:
                raise AssemblyError(f"{mnemonic} expects expression and target", line)
            expr_size = len(_parse_expression(operands[0], line))
        elif len(operands) != 1:
            raise AssemblyError(f"{mnemonic} expects one target", line)
        return 1 + (2 if mnemonic != "IF_FALSE_JMP" else expr_size + 2)
    if mnemonic == "SET_VAR":
        if len(operands) != 2:
            raise AssemblyError("SET_VAR expects varref and expression", line)
        match = _TOKEN_RE.fullmatch(operands[0].strip())
        if not match or not match.group(1).startswith("V"):
            raise AssemblyError("invalid SET_VAR variable reference", line)
        kind, raw = match.groups()
        value = int(raw, 0)
        if kind.endswith("6"):
            if value > 0x3F:
                raise AssemblyError(f"{kind} index out of range", line)
            var_size = 1
        else:
            if value > 0x3FFF:
                raise AssemblyError(f"{kind} index out of range", line)
            var_size = 2
        return 1 + var_size + len(_parse_expression(operands[1], line))
    if mnemonic == "IF_FALSE_SKIP":
        if len(operands) != 2:
            raise AssemblyError("IF_FALSE_SKIP expects expression and target", line)
        return 1 + len(_parse_expression(operands[0], line)) + 1
    if mnemonic in {"PAGE_CALL", "PAGE_GOTO", "LOAD_GRAPHIC", "LOAD_STATE", "SAVE_STATE"}:
        if len(operands) != 1:
            raise AssemblyError(f"{mnemonic} expects one expression", line)
        return 1 + len(_parse_expression(operands[0], line))
    if mnemonic in {"WINDOW_CONTROL", "WINDOW_SLOT"}:
        expected = 7 if mnemonic == "WINDOW_CONTROL" else 6
        if len(operands) != expected:
            raise AssemblyError(f"{mnemonic} expects {expected} operands", line)
        extra = 1 if mnemonic == "WINDOW_CONTROL" else 0
        exprs = operands[1:] if mnemonic == "WINDOW_CONTROL" else operands
        return 1 + extra + sum(len(_parse_expression(value, line)) for value in exprs)
    if mnemonic == "PRINT_VALUE":
        if len(operands) != 2:
            raise AssemblyError("PRINT_VALUE expects imm8 and expression", line)
        _parse_int(operands[0], line, maximum=0xFF)
        return 2 + len(_parse_expression(operands[1], line))
    if mnemonic in {"BLIT_RECT", "SET_PALETTE"}:
        expected = 6 if mnemonic == "BLIT_RECT" else 4
        if len(operands) != expected:
            raise AssemblyError(f"{mnemonic} expects {expected} expressions", line)
        return 1 + sum(len(_parse_expression(value, line)) for value in operands)
    if mnemonic in {"GRAPHIC_0E", "SET_TEXT_POS", "LOAD_GRAPHIC_EX", "NAME_BUFFER_COPY", "SYSTEM", "CONFIG"}:
        if len(operands) != 2:
            raise AssemblyError(f"{mnemonic} expects two expressions", line)
        return 1 + sum(len(_parse_expression(value, line)) for value in operands)
    if mnemonic == "INPUT_WAIT":
        if len(operands) != 1:
            raise AssemblyError("INPUT_WAIT expects subcode", line)
        _parse_int(operands[0], line, maximum=0xFF)
        return 2
    if mnemonic in {"MAP_CONTROL"}:
        if len(operands) != 3:
            raise AssemblyError("MAP_CONTROL expects subcode and two expressions", line)
        _parse_int(operands[0], line, maximum=0xFF)
        return 2 + sum(len(_parse_expression(value, line)) for value in operands[1:])
    if mnemonic == "BIT_VECTOR":
        if len(operands) != 2:
            raise AssemblyError("BIT_VECTOR expects two expressions", line)
        return 1 + sum(len(_parse_expression(value, line)) for value in operands)
    if mnemonic in {"LOAD_AUDIO", "INSERT_NAME"}:
        if len(operands) != 1:
            raise AssemblyError(f"{mnemonic} expects imm8", line)
        _parse_int(operands[0], line, maximum=0xFF)
        return 2
    raise AssemblyError(f"unsupported mnemonic {mnemonic!r}", line)


def _parse_source(text: str) -> Source:
    statements: list[Statement] = []
    labels: dict[str, int] = {}
    terminal_label: str | None = None
    member_size: int | None = None
    encoding: str | None = None
    eof_seen = False
    for line_number, original in enumerate(text.splitlines(), 1):
        line = _strip_comment(original).strip()
        if not line:
            continue
        if eof_seen:
            continue
        if line.endswith(":") and _LABEL_RE.fullmatch(line):
            name = line[:-1]
            if name in labels:
                raise AssemblyError(f"duplicate label {name!r}", line_number)
            labels[name] = -1
            statements.append(Statement(f"__LABEL__:{name}", (), line_number))
            continue
        if line.startswith("."):
            parts = line.split(None, 1)
            directive = parts[0].lower()
            argument = parts[1].strip() if len(parts) > 1 else ""
            if directive == ".encoding":
                if not argument:
                    raise AssemblyError(".encoding requires a codec", line_number)
                try:
                    encoding = _parse_quoted(argument, line_number, "ascii").decode("ascii")
                except (UnicodeDecodeError, AssemblyError):
                    raise AssemblyError(".encoding must contain an ASCII quoted codec name", line_number)
                continue
            if directive == ".member_size":
                member_size = _parse_int(argument, line_number)
                continue
            if directive == ".terminal":
                terminal_label = argument
                continue
            if directive in {".source"}:
                continue
            if directive == ".eof":
                eof_seen = True
                continue
            raise AssemblyError(f"unknown directive {directive!r}", line_number)
        parts = line.split(None, 1)
        mnemonic = parts[0].upper()
        operands = _split_operands(parts[1], line_number) if len(parts) > 1 else ()
        statements.append(Statement(mnemonic, operands, line_number))
    return Source(statements, labels, terminal_label, member_size, encoding)


def _resolve_target(token: str, labels: dict[str, int], line: int) -> int:
    token = token.strip()
    if token in labels:
        value = labels[token]
        if value < 0:
            raise AssemblyError(f"label {token!r} has no address", line)
        return value
    return _parse_int(token, line, maximum=0xFFFF)


def _encode_statement(statement: Statement, labels: dict[str, int], position: int, encoding: str) -> bytes:
    mnemonic, operands, line = statement.mnemonic, statement.operands, statement.line
    if mnemonic.startswith("__LABEL__:"):
        return b""
    if mnemonic == "TEXT":
        if len(operands) != 1:
            raise AssemblyError("TEXT expects one string", line)
        return _parse_quoted(operands[0], line, encoding)
    if mnemonic == "DEFINE_NAME":
        if len(operands) != 1:
            raise AssemblyError("DEFINE_NAME expects one string", line)
        return bytes([0x4D]) + _parse_quoted(operands[0], line, encoding) + b":"
    if mnemonic == "CHOICE_BEGIN":
        if len(operands) != 1:
            raise AssemblyError("CHOICE_BEGIN expects one target", line)
        return b"\x24" + _resolve_target(operands[0], labels, line).to_bytes(2, "little")
    if mnemonic == "CHOICE_END":
        if operands:
            raise AssemblyError("CHOICE_END takes no operands", line)
        return b"\x24"
    if mnemonic == "LOCAL_RET":
        if operands:
            raise AssemblyError("LOCAL_RET takes no operands", line)
        return b"\x5C\x00\x00"
    if mnemonic == "END_BLOCK":
        if operands:
            raise AssemblyError("END_BLOCK takes no operands", line)
        return b"\x5D"
    if mnemonic == "TAIL_END":
        if len(operands) != 1:
            raise AssemblyError("TAIL_END expects marker byte", line)
        return bytes([_parse_int(operands[0], line, maximum=0xFF)])
    if mnemonic == "TAIL_ENTRY":
        if len(operands) != 2:
            raise AssemblyError("TAIL_ENTRY expects key and target", line)
        key = _parse_int(operands[0], line, maximum=0xFFFF)
        target = _resolve_target(operands[1], labels, line)
        return b"[" + key.to_bytes(2, "big") + target.to_bytes(2, "big")
    if mnemonic == "TAIL_IF":
        if len(operands) != 3:
            raise AssemblyError("TAIL_IF expects expression, key, target", line)
        expr = _parse_expression(operands[0], line)
        key = _parse_int(operands[1], line, maximum=0xFFFF)
        target = _resolve_target(operands[2], labels, line)
        return b":" + expr + key.to_bytes(2, "big") + target.to_bytes(2, "big")

    definition = MNEMONIC_TO_OPCODE.get(mnemonic)
    if definition is None:
        raise AssemblyError(f"unknown mnemonic {mnemonic!r}", line)
    opcode = definition.bytecode
    if mnemonic == "SET_VAR":
        if len(operands) != 2:
            raise AssemblyError("SET_VAR expects varref and expression", line)
        var = operands[0].strip()
        match = _TOKEN_RE.fullmatch(var)
        if not match or not match.group(1).startswith("V"):
            raise AssemblyError("invalid SET_VAR variable reference", line)
        kind, raw = match.groups()
        value = int(raw, 0)
        if kind == "V6":
            if value > 0x3F: raise AssemblyError("V6 index out of range", line)
            var_bytes = bytes([0x80 | value])
        elif kind == "V14":
            if value > 0x3FFF: raise AssemblyError("V14 index out of range", line)
            var_bytes = bytes([0xC0 | ((value >> 8) & 0x3F), value & 0xFF])
        elif kind == "VRAW6":
            if value > 0x3F: raise AssemblyError("VRAW6 index out of range", line)
            var_bytes = bytes([value])
        else:
            if value > 0x3FFF: raise AssemblyError("VRAW14 index out of range", line)
            var_bytes = bytes([0x40 | ((value >> 8) & 0x3F), value & 0xFF])
        return bytes([opcode]) + var_bytes + _parse_expression(operands[1], line)
    if mnemonic in {"PAGE_CALL", "PAGE_GOTO", "LOAD_GRAPHIC", "LOAD_STATE", "SAVE_STATE"}:
        if len(operands) != 1: raise AssemblyError(f"{mnemonic} expects one expression", line)
        return bytes([opcode]) + _parse_expression(operands[0], line)
    if mnemonic == "JMP":
        if len(operands) != 1: raise AssemblyError("JMP expects one target", line)
        return bytes([opcode]) + _resolve_target(operands[0], labels, line).to_bytes(2, "little")
    if mnemonic in {"TEXT_BREAK", "RESTART_PAGE", "ADVANCE_LINE", "END_BLOCK"}:
        return bytes([opcode])
    if mnemonic == "WINDOW_CONTROL":
        if len(operands) != 7: raise AssemblyError("WINDOW_CONTROL expects 7 operands", line)
        sub = _parse_int(operands[0], line, maximum=0xFF)
        return bytes([opcode, sub]) + b"".join(_parse_expression(value, line) for value in operands[1:])
    if mnemonic == "WINDOW_SLOT":
        if len(operands) != 6: raise AssemblyError("WINDOW_SLOT expects 6 expressions", line)
        return bytes([opcode]) + b"".join(_parse_expression(value, line) for value in operands)
    if mnemonic == "PRINT_VALUE":
        if len(operands) != 2: raise AssemblyError("PRINT_VALUE expects imm8 and expression", line)
        return bytes([opcode, _parse_int(operands[0], line, maximum=0xFF)]) + _parse_expression(operands[1], line)
    if mnemonic in {"BLIT_RECT", "SET_PALETTE"}:
        expected = 6 if mnemonic == "BLIT_RECT" else 4
        if len(operands) != expected: raise AssemblyError(f"{mnemonic} expects {expected} expressions", line)
        return bytes([opcode]) + b"".join(_parse_expression(value, line) for value in operands)
    if mnemonic in {"GRAPHIC_0E", "SET_TEXT_POS", "LOAD_GRAPHIC_EX", "NAME_BUFFER_COPY", "SYSTEM", "CONFIG"}:
        if len(operands) != 2: raise AssemblyError(f"{mnemonic} expects two expressions", line)
        return bytes([opcode]) + b"".join(_parse_expression(value, line) for value in operands)
    if mnemonic in {"INPUT_WAIT", "LOAD_AUDIO", "INSERT_NAME"}:
        if len(operands) != 1: raise AssemblyError(f"{mnemonic} expects imm8", line)
        return bytes([opcode, _parse_int(operands[0], line, maximum=0xFF)])
    if mnemonic == "MAP_CONTROL":
        if len(operands) != 3: raise AssemblyError("MAP_CONTROL expects subcode and two expressions", line)
        return bytes([opcode, _parse_int(operands[0], line, maximum=0xFF)]) + b"".join(_parse_expression(value, line) for value in operands[1:])
    if mnemonic == "BIT_VECTOR":
        if len(operands) != 2: raise AssemblyError("BIT_VECTOR expects two expressions", line)
        return bytes([opcode]) + b"".join(_parse_expression(value, line) for value in operands)
    if mnemonic in {"LOCAL_CALL"}:
        if len(operands) != 1: raise AssemblyError("LOCAL_CALL expects one target", line)
        return bytes([opcode]) + _resolve_target(operands[0], labels, line).to_bytes(2, "little")
    if mnemonic == "IF_FALSE_JMP":
        if len(operands) != 2: raise AssemblyError("IF_FALSE_JMP expects expression and target", line)
        return bytes([opcode]) + _parse_expression(operands[0], line) + _resolve_target(operands[1], labels, line).to_bytes(2, "little")
    if mnemonic == "IF_FALSE_SKIP":
        if len(operands) != 2: raise AssemblyError("IF_FALSE_SKIP expects expression and target", line)
        prefix = bytes([opcode]) + _parse_expression(operands[0], line)
        target = _resolve_target(operands[1], labels, line)
        next_ip = position + len(prefix) + 1
        displacement = target - next_ip
        if displacement < 0 or displacement > 0xFF:
            raise AssemblyError(f"IF_FALSE_SKIP target displacement out of range: {displacement}", line)
        return prefix + bytes([displacement])
    raise AssemblyError(f"unsupported mnemonic {mnemonic!r}", line)


def assemble_text(text: str, encoding: str | None = None) -> bytes:
    source = _parse_source(text)
    selected_encoding = encoding or source.encoding or "cp932"
    try:
        "".encode(selected_encoding)
    except LookupError as error:
        raise AssemblyError(f"unknown text encoding {selected_encoding!r}") from error

    # First pass determines every label address.  Statements begin at file
    # offset two because the terminal field occupies the first two bytes.
    position = 2
    labels: dict[str, int] = {}
    for statement in source.statements:
        if statement.mnemonic.startswith("__LABEL__:"):
            name = statement.mnemonic.split(":", 1)[1]
            labels[name] = position
            continue
        position += _instruction_size(statement, selected_encoding)
    if source.terminal_label is None:
        raise AssemblyError("missing .terminal directive")
    terminal_token = source.terminal_label.strip()
    terminal = labels.get(terminal_token)
    if terminal is None:
        terminal = _parse_int(terminal_token, 0, maximum=0xFFFF)
    if terminal < 2 or terminal >= position + 1:
        raise AssemblyError(f"terminal offset 0x{terminal:04X} is invalid")
    if source.member_size is None:
        member_size = ((position + 1 + 0xFF) // 0x100) * 0x100
    else:
        member_size = source.member_size
    if member_size < position + 1 or member_size % 0x100:
        raise AssemblyError(f"member size 0x{member_size:X} is too small or not 256-byte aligned")

    body = bytearray()
    position = 2
    for statement in source.statements:
        encoded = _encode_statement(statement, labels, position, selected_encoding)
        body.extend(encoded)
        position += len(encoded)
    # .eof is not a bytecode record; the host's file terminator follows the
    # tail record stream and the remainder of the member is zero-filled.
    result = bytearray(member_size)
    result[0:2] = terminal.to_bytes(2, "little")
    result[2 : 2 + len(body)] = body
    eof_offset = 2 + len(body)
    if eof_offset >= member_size:
        raise AssemblyError("member has no room for 0x1A terminator")
    result[eof_offset] = 0x1A
    return bytes(result)


def assemble_file(input_path: Path, output_path: Path | None = None, encoding: str | None = None) -> Path:
    input_path = input_path.resolve()
    if output_path is None:
        output_path = input_path.with_suffix(".rebuild")
    data = assemble_text(input_path.read_text(encoding="utf-8"), encoding)
    output_path.write_bytes(data)
    return output_path


def _expand_inputs(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path.is_file():
            result.append(path)
            continue
        if not path.is_dir():
            raise FileNotFoundError(path)
        candidates = sorted(path.rglob("*.asm.txt"))
        if not candidates:
            candidates = sorted(path.rglob("*.asm"))
        result.extend(candidate for candidate in candidates if candidate.is_file())
    return list(dict.fromkeys(candidate.resolve() for candidate in result))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assemble ADV.COM semantic assembly.")
    parser.add_argument("inputs", nargs="+", type=Path, help="assembly files")
    parser.add_argument("-o", "--output", type=Path, help="output path (single input only)")
    parser.add_argument("--encoding", help="text encoding (overrides .encoding directive)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        inputs = _expand_inputs(args.inputs)
        if not inputs:
            raise AssemblyError("no input assembly files found")
        if args.output is not None and len(inputs) != 1:
            raise AssemblyError("--output can only be used with one input file")
        for path in inputs:
            output = assemble_file(path, args.output, args.encoding)
            print(f"{path} -> {output}")
        return 0
    except (OSError, UnicodeError, ValueError) as error:
        print(f"assembler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
