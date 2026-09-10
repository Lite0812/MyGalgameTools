#!/usr/bin/env python3

from __future__ import annotations

import codecs
import copy
import dataclasses
import functools
import re
from typing import cast


DISPATCH_BASE = 0xA0D7F0

OPERAND_ABBREVIATIONS = {
    "be16_length": "len16",
    "blob": "blob",
    "cstring_array": "strs",
    "raw8": "u8",
    "raw16": "u16",
    "raw_be32": "u32",
    "raw_cstring": "strz",
    "tag8": "tag8",
    "tag8_index": "idx8",
    "tag16": "tag16",
    "tag32": "tag32",
    "tag32_x100": "tag32x100",
}

NOTE_TRANSLATIONS = {
    "contains loop in main-path control flow": "主路径控制流中包含循环",
    "direct pc access in main-path body": "主路径中直接读写 pc",
    "main-path contains conditional reads": "主路径存在条件分支读参",
    "loads another SEEN file": "加载另一个 SEEN 文件",
    "loads secondary script context": "加载副脚本上下文",
    "pushes current file/pc and jumps to another SEEN file at pc=2": "压栈当前 file/pc，并跳到另一个 SEEN 文件的 pc=2",
    "reads a 32-bit big-endian immediate directly from the script stream": "直接从脚本流读取 32 位大端立即数",
    "reads a 16-bit big-endian variable slot id directly from the script stream": "直接从脚本流读取 16 位大端变量槽编号",
    "IDA export for this handler is not present in decompile/": "decompile/ 中没有这个 handler 的 IDA 导出文件",
}

TOKEN_PREFIX_BY_KIND = {
    "tag8": ("imm8", "ref8"),
    "tag8_index": ("imm8idx", "ref8idx"),
    "tag16": ("imm16", "ref16"),
    "tag32": ("imm32", "ref32"),
    "tag32_x100": ("imm32x100", "ref32x100"),
}

EXPR_VALUE_OPCODE_TO_NAME = {
    0x10: "slot",
    0x11: "imm",
}

EXPR_BINARY_OPCODE_TO_NAME = {
    0x20: "eq",
    0x21: "le",
    0x22: "ge",
    0x23: "lt",
    0x24: "gt",
    0x25: "or",
    0x26: "and",
    0x27: "ne",
    0x30: "add",
    0x31: "sub",
    0x32: "mul",
    0x33: "div",
    0x34: "mod",
}

EXPR_NAME_TO_BINARY_OPCODE = {name: opcode for opcode, name in EXPR_BINARY_OPCODE_TO_NAME.items()}
CHAINABLE_EXPR_NAMES = {"and", "or"}

TAG_TOKEN_RE = re.compile(r"^([A-Za-z0-9_]+)\((.*)\)$")
BLOB_TOKEN_RE = re.compile(r"^blob\{([0-9A-Fa-f]*)\}$")
STRING_PLACEHOLDER_TOKEN_RE = re.compile(r"\{\{ff:([0-9A-Fa-f]{2})\}\}", re.IGNORECASE)
SCRIPT_TARGET_OPERANDS_BY_OPCODE = {
    0x0030: (0,),
    0x0031: (0,),
    0x0040: (1,),
}
DEFAULT_TEXT_ENCODING = "cp932"
MAX_LITERAL_PROBE_BYTES = 4
_TEXT_ENCODING = DEFAULT_TEXT_ENCODING


def normalize_note(note: str) -> str:
    return NOTE_TRANSLATIONS.get(note, note)


def normalize_notes(notes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(normalize_note(note) for note in notes)


def normalize_encoding(encoding: str | None) -> str:
    candidate = (encoding or DEFAULT_TEXT_ENCODING).strip()
    if not candidate:
        candidate = DEFAULT_TEXT_ENCODING
    return codecs.lookup(candidate).name


def set_text_encoding(encoding: str | None) -> str:
    global _TEXT_ENCODING
    _TEXT_ENCODING = normalize_encoding(encoding)
    return _TEXT_ENCODING


def get_text_encoding() -> str:
    return _TEXT_ENCODING


def resolve_text_encoding(encoding: str | None = None) -> str:
    if encoding is None:
        return _TEXT_ENCODING
    return normalize_encoding(encoding)


def handler_impl_hex(handler: str) -> str:
    return f"0x{handler.removeprefix('sub_').upper()}"

def format_hex(value: int, width: int) -> str:
    return f"0x{value:0{width}X}"

def escape_text_fragment(text: str) -> str:
    parts: list[str] = []
    for char in text:
        if char == "\\":
            parts.append("\\\\")
        elif char == '"':
            parts.append('\\"')
        elif char == "\n":
            parts.append("\\n")
        elif char == "\r":
            parts.append("\\r")
        elif char == "\t":
            parts.append("\\t")
        else:
            parts.append(char)
    return "".join(parts)


def decode_literal_chunk(data: bytes, offset: int, encoding: str) -> tuple[str, int] | None:
    max_probe = min(MAX_LITERAL_PROBE_BYTES, len(data) - offset)
    for size in range(max_probe, 0, -1):
        chunk = data[offset : offset + size]
        if 0xFF in chunk:
            continue
        try:
            decoded = chunk.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not decoded:
            continue
        try:
            if decoded.encode(encoding) != chunk:
                continue
        except UnicodeEncodeError:
            continue
        return decoded, size
    return None


def format_string_placeholder(value: int) -> str:
    return f"{{{{ff:{value:02X}}}}}"


def bytes_to_string_literal(data: bytes, encoding: str | None = None) -> str:
    active_encoding = resolve_text_encoding(encoding)
    parts: list[str] = []
    index = 0
    while index < len(data):
        if data[index] == 0xFF:
            if index + 1 < len(data):
                parts.append(format_string_placeholder(data[index + 1]))
                index += 2
                continue
            parts.append("\\xFF")
            index += 1
            continue
        decoded_chunk = decode_literal_chunk(data, index, active_encoding)
        if decoded_chunk is None:
            parts.append(f"\\x{data[index]:02X}")
            index += 1
            continue
        decoded, size = decoded_chunk
        parts.append(escape_text_fragment(decoded))
        index += size
    return '"' + "".join(parts) + '"'

def encode_literal_char(char: str, encoding: str | None = None) -> bytes:
    active_encoding = resolve_text_encoding(encoding)
    try:
        return char.encode(active_encoding)
    except UnicodeEncodeError:
        if ord(char) <= 0xFF:
            return bytes([ord(char)])
        raise

def parse_string_escape(body: str, index: int, encoding: str | None = None) -> tuple[bytes, int]:
    if index >= len(body):
        raise ValueError("trailing backslash in string literal")

    escape = body[index]
    simple_escapes = {
        "\\": "\\",
        '"': '"',
        "'": "'",
        "a": "\a",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
    }

    if escape in simple_escapes:
        return encode_literal_char(simple_escapes[escape], encoding), index + 1

    if escape == "x":
        hex_digits = body[index + 1 : index + 3]
        if len(hex_digits) != 2 or re.fullmatch(r"[0-9A-Fa-f]{2}", hex_digits) is None:
            raise ValueError("invalid \\x escape in string literal")
        return bytes([int(hex_digits, 16)]), index + 3

    if escape == "u":
        hex_digits = body[index + 1 : index + 5]
        if len(hex_digits) != 4 or re.fullmatch(r"[0-9A-Fa-f]{4}", hex_digits) is None:
            raise ValueError("invalid \\u escape in string literal")
        return encode_literal_char(chr(int(hex_digits, 16)), encoding), index + 5

    if escape == "U":
        hex_digits = body[index + 1 : index + 9]
        if len(hex_digits) != 8 or re.fullmatch(r"[0-9A-Fa-f]{8}", hex_digits) is None:
            raise ValueError("invalid \\U escape in string literal")
        return encode_literal_char(chr(int(hex_digits, 16)), encoding), index + 9

    if escape in "01234567":
        end = index + 1
        while end < len(body) and end - index < 3 and body[end] in "01234567":
            end += 1
        return bytes([int(body[index:end], 8)]), end

    raise ValueError(f"unsupported string escape \\{escape}")

def string_literal_to_bytes(token: str, encoding: str | None = None) -> bytes:
    active_encoding = resolve_text_encoding(encoding)
    token = token.strip()
    if len(token) < 2 or token[0] not in {'"', "'"} or token[-1] != token[0]:
        raise ValueError(f"expected string literal, got {token!r}")

    body = token[1:-1]
    result = bytearray()
    index = 0

    while index < len(body):
        placeholder_match = STRING_PLACEHOLDER_TOKEN_RE.match(body, index)
        if placeholder_match is not None:
            result.extend((0xFF, int(placeholder_match.group(1), 16)))
            index = placeholder_match.end()
            continue
        char = body[index]
        if char != "\\":
            result.extend(encode_literal_char(char, active_encoding))
            index += 1
            continue
        chunk, index = parse_string_escape(body, index + 1, active_encoding)
        result.extend(chunk)

    return bytes(result)

def split_top_level(text: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    pairs = {"[": "]", "(": ")", "{": "}"}
    openers = set(pairs)
    closers = set(pairs.values())

    for char in text:
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
        if char in openers:
            depth += 1
            current.append(char)
            continue
        if char in closers:
            depth -= 1
            current.append(char)
            continue
        if char == delimiter and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)

    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts

def parse_int_token(token: str, labels: dict[str, int] | None = None) -> int:
    labels = labels or {}
    if token in labels:
        return labels[token]
    return int(token, 0)

def decode_tag_operand(kind: str, data: bytes, offset: int) -> tuple[str, int]:
    tag = data[offset]
    immediate_name, ref_name = TOKEN_PREFIX_BY_KIND[kind]
    if kind in {"tag32", "tag32_x100"}:
        if tag == 0:
            value = int.from_bytes(data[offset + 1 : offset + 5], "big")
            return f"{immediate_name}({format_hex(value, 8)})", 5
        index = int.from_bytes(data[offset + 1 : offset + 3], "big")
        return f"{ref_name}({format_hex(tag, 2)}, {format_hex(index, 4)})", 3
    if kind == "tag16":
        if tag == 0:
            value = int.from_bytes(data[offset + 1 : offset + 3], "big")
            return f"{immediate_name}({format_hex(value, 4)})", 3
        index = int.from_bytes(data[offset + 1 : offset + 3], "big")
        return f"{ref_name}({format_hex(tag, 2)}, {format_hex(index, 4)})", 3
    if tag == 0:
        value = data[offset + 1]
        return f"{immediate_name}({format_hex(value, 2)})", 2
    index = int.from_bytes(data[offset + 1 : offset + 3], "big")
    return f"{ref_name}({format_hex(tag, 2)}, {format_hex(index, 4)})", 3

def encode_tag_operand(kind: str, token: str, labels: dict[str, int] | None = None) -> bytes:
    labels = labels or {}
    match = TAG_TOKEN_RE.match(token)
    if not match:
        raise ValueError(f"invalid tagged operand token: {token}")
    name, inner = match.groups()
    values = split_top_level(inner)
    immediate_name, ref_name = TOKEN_PREFIX_BY_KIND[kind]
    if name == immediate_name:
        if len(values) != 1:
            raise ValueError(f"{name} expects 1 argument")
        value = parse_int_token(values[0], labels)
        if kind in {"tag32", "tag32_x100"}:
            return b"\x00" + value.to_bytes(4, "big")
        if kind == "tag16":
            return b"\x00" + value.to_bytes(2, "big")
        return bytes([0, value & 0xFF])
    if name == ref_name:
        if len(values) != 2:
            raise ValueError(f"{name} expects 2 arguments")
        prefix = parse_int_token(values[0], labels) & 0xFF
        index = parse_int_token(values[1], labels) & 0xFFFF
        return bytes([prefix]) + index.to_bytes(2, "big")
    raise ValueError(f"unexpected tagged operand token: {token}")

def decode_cstring(data: bytes, offset: int, encoding: str | None = None) -> tuple[str, int]:
    end = data.index(0, offset)
    return bytes_to_string_literal(data[offset:end], encoding), end - offset + 1

def decode_text_payload(data: bytes, offset: int, terminator: int, encoding: str | None = None) -> tuple[str, int]:
    parts: list[str] = []
    payload = bytearray()
    cursor = offset
    while cursor < len(data):
        raw = data[cursor]
        if raw == terminator:
            if payload:
                parts.append(bytes_to_string_literal(bytes(payload), encoding)[1:-1])
            return '"' + "".join(parts) + '"', cursor - offset + 1
        if cursor + 7 < len(data) and (raw ^ 0xAA) == 0xFF and (data[cursor + 1] ^ 0xAA) == 0x02:
            if payload:
                parts.append(bytes_to_string_literal(bytes(payload), encoding)[1:-1])
                payload.clear()
            parts.append("\\xFF\\x02")
            parts.extend(f"\\x{value:02X}" for value in data[cursor + 2 : cursor + 8])
            cursor += 8
            continue
        payload.append(raw ^ 0xAA)
        cursor += 1
    raise ValueError(f"unterminated text payload at offset {offset:#x}")

def encode_text_payload(token: str, terminator: int, encoding: str | None = None) -> bytes:
    payload = string_literal_to_bytes(token, encoding)
    out = bytearray()
    cursor = 0
    while cursor < len(payload):
        if cursor + 7 < len(payload) and payload[cursor] == 0xFF and payload[cursor + 1] == 0x02:
            out.extend((0x55, 0xA8))
            out.extend(payload[cursor + 2 : cursor + 8])
            cursor += 8
            continue
        out.append(payload[cursor] ^ 0xAA)
        cursor += 1
    out.append(terminator)
    return bytes(out)

def string_list_to_bytes(token: str, encoding: str | None = None) -> list[bytes]:
    token = token.strip()
    if len(token) < 2 or token[0] != "[" or token[-1] != "]":
        raise ValueError(f"expected string list literal, got {token!r}")
    inner = token[1:-1].strip()
    if not inner:
        return []
    return [string_literal_to_bytes(item, encoding) for item in split_top_level(inner)]

def parse_blob_token(token: str) -> bytes:
    match = BLOB_TOKEN_RE.match(token)
    if not match:
        raise ValueError(f"invalid blob token: {token}")
    blob_hex = match.group(1)
    if len(blob_hex) % 2 != 0:
        raise ValueError(f"blob token must contain even number of hex digits: {token}")
    return bytes.fromhex(blob_hex)

def blob_to_token(data: bytes) -> str:
    return "blob{" + data.hex().upper() + "}"

def expr_cell(opcode: int, value: int = 0) -> bytes:
    return bytes([opcode]) + (value & 0xFFFFFFFF).to_bytes(4, "big")

def decode_expr_cells(data: bytes) -> list[tuple[int, int]]:
    if not data or data[-1] != 0xFF or (len(data) - 1) % 5 != 0:
        raise ValueError("expr blob is not a well-formed cell stream")
    cells: list[tuple[int, int]] = []
    cursor = 0
    limit = len(data) - 1
    while cursor < limit:
        cells.append((data[cursor], int.from_bytes(data[cursor + 1 : cursor + 5], "big")))
        cursor += 5
    return cells

def parse_expr_node(cells: list[tuple[int, int]], index: int) -> tuple[tuple[object, ...], int]:
    opcode, value = cells[index]
    value_name = EXPR_VALUE_OPCODE_TO_NAME.get(opcode)
    if value_name is not None:
        return (value_name, value), index + 1
    if opcode != 0x01 or value != 0:
        raise ValueError(f"unsupported expr cell opcode {opcode:#04x}")

    left, index = parse_expr_node(cells, index + 1)
    operator_opcode, operator_value = cells[index]
    operator_name = EXPR_BINARY_OPCODE_TO_NAME.get(operator_opcode)
    if operator_name is None or operator_value != 0:
        raise ValueError(f"unsupported expr operator {operator_opcode:#04x}")

    right, index = parse_expr_node(cells, index + 1)
    if operator_name in CHAINABLE_EXPR_NAMES:
        chain_nodes: list[tuple[object, ...]] = [left, right]
        while index < len(cells):
            next_opcode, next_value = cells[index]
            if next_opcode == 0x02 and next_value == 0:
                break
            if next_opcode != operator_opcode or next_value != 0:
                raise ValueError(f"mixed or unsupported chained expr operator {next_opcode:#04x}")
            next_node, index = parse_expr_node(cells, index + 1)
            chain_nodes.append(next_node)
        close_opcode, close_value = cells[index]
        if close_opcode != 0x02 or close_value != 0:
            raise ValueError("expr group is missing a matching close cell")
        return (operator_name, *chain_nodes), index + 1

    close_opcode, close_value = cells[index]
    if close_opcode != 0x02 or close_value != 0:
        raise ValueError("expr group is missing a matching close cell")
    return (operator_name, left, right), index + 1

def expr_node_to_text(node: tuple[object, ...]) -> str:
    kind = cast(str, node[0])
    if kind == "slot":
        return f"slot({format_hex(cast(int, node[1]), 8)})"
    if kind == "imm":
        return format_hex(cast(int, node[1]), 8)
    args = ", ".join(expr_node_to_text(cast(tuple[object, ...], child)) for child in node[1:])
    return f"{kind}({args})"

def parse_cond_node(text: str) -> tuple[object, ...]:
    token = text.strip()
    if not token:
        raise ValueError("empty cond expression")
    if re.fullmatch(r"0[xX][0-9A-Fa-f]+|\d+", token):
        return ("imm", parse_int_token(token))

    match = TAG_TOKEN_RE.match(token)
    if not match:
        raise ValueError(f"invalid cond expression token: {token}")

    name, inner = match.groups()
    args = split_top_level(inner)
    if name == "slot":
        if len(args) != 1:
            raise ValueError("slot() expects 1 argument")
        return ("slot", parse_int_token(args[0]))
    if name == "imm":
        if len(args) != 1:
            raise ValueError("imm() expects 1 argument")
        return ("imm", parse_int_token(args[0]))
    if name in EXPR_NAME_TO_BINARY_OPCODE:
        if name in CHAINABLE_EXPR_NAMES:
            if len(args) < 2:
                raise ValueError(f"{name}() expects at least 2 arguments")
            return (name, *(parse_cond_node(arg) for arg in args))
        if len(args) != 2:
            raise ValueError(f"{name}() expects 2 arguments")
        return (name, parse_cond_node(args[0]), parse_cond_node(args[1]))
    raise ValueError(f"unsupported cond function: {name}")

def encode_expr_node(node: tuple[object, ...]) -> bytes:
    kind = cast(str, node[0])
    if kind == "slot":
        return expr_cell(0x10, cast(int, node[1]))
    if kind == "imm":
        return expr_cell(0x11, cast(int, node[1]))
    operator_opcode = EXPR_NAME_TO_BINARY_OPCODE.get(kind)
    if operator_opcode is None:
        raise ValueError(f"unsupported expr node kind: {kind}")
    children = [cast(tuple[object, ...], child) for child in node[1:]]
    if kind in CHAINABLE_EXPR_NAMES:
        if len(children) < 2:
            raise ValueError(f"{kind} requires at least 2 children")
        out = bytearray(expr_cell(0x01))
        out.extend(encode_expr_node(children[0]))
        for child in children[1:]:
            out.extend(expr_cell(operator_opcode))
            out.extend(encode_expr_node(child))
        out.extend(expr_cell(0x02))
        return bytes(out)
    if len(children) != 2:
        raise ValueError(f"{kind} requires exactly 2 children")
    left = encode_expr_node(children[0])
    right = encode_expr_node(children[1])
    return expr_cell(0x01) + left + expr_cell(operator_opcode) + right + expr_cell(0x02)

def parse_expr_blob_token(token: str) -> bytes:
    token = token.strip()
    if token.startswith("blob{"):
        return parse_blob_token(token)
    if token.startswith("cond{") and token.endswith("}"):
        node = parse_cond_node(token[5:-1])
        return encode_expr_node(node) + b"\xFF"
    if not token.startswith("expr{") or not token.endswith("}"):
        raise ValueError(f"invalid expr blob token: {token}")

    inner = token[5:-1].strip()
    if not inner:
        return b""

    parts = split_top_level(inner)
    result = bytearray()
    for index, part in enumerate(parts):
        cell = part.strip()
        if index == len(parts) - 1 and re.fullmatch(r"FF", cell, re.IGNORECASE):
            result.append(0xFF)
            continue
        match = re.fullmatch(r"([0-9A-Fa-f]{2})\s*:\s*([0-9A-Fa-f]{8})", cell)
        if not match:
            raise ValueError(f"invalid expr cell: {cell}")
        op_hex, value_hex = match.groups()
        result.append(int(op_hex, 16))
        result.extend(bytes.fromhex(value_hex))
    return bytes(result)

def expr_blob_to_token(data: bytes) -> str:
    try:
        cells = decode_expr_cells(data)
        node, index = parse_expr_node(cells, 0)
        if index == len(cells):
            return "cond{" + expr_node_to_text(node) + "}"
    except ValueError:
        pass

    if not data or data[-1] != 0xFF or (len(data) - 1) % 5 != 0:
        return blob_to_token(data)

    parts: list[str] = []
    cursor = 0
    last_index = len(data) - 1
    while cursor < last_index:
        parts.append(f"{data[cursor]:02X}:{data[cursor + 1:cursor + 5].hex().upper()}")
        cursor += 5
    parts.append("FF")
    return "expr{" + ", ".join(parts) + "}"

def split_expr_blob_operands(operands: list[str]) -> tuple[str | None, str, str]:
    if len(operands) == 2:
        return None, operands[0], operands[1]
    if len(operands) == 3:
        return operands[0], operands[1], operands[2]
    raise ValueError(f"expr_blob expects 2 or 3 operands, got {len(operands)}")


def get_decoded_script_target_operand_indexes(opcode: int) -> tuple[int, ...]:
    return SCRIPT_TARGET_OPERANDS_BY_OPCODE.get(opcode, ())


def resolve_asm_script_targets(
    spec: dict[str, object],
    operands: list[str],
    labels: dict[str, int] | None = None,
) -> tuple[int, ...]:
    labels = labels or {}
    opcode = cast(int, spec["opcode"])
    if opcode in {0x0030, 0x0031}:
        if not operands:
            raise ValueError(f"{spec['mnemonic']} expects 1 operand")
        return (parse_int_token(operands[0], labels),)
    if opcode == 0x0040:
        _, target_token, _ = split_expr_blob_operands(operands)
        return (parse_int_token(target_token, labels),)
    return ()

STATIC_DISPATCH_DEFAULT_HANDLER = "sub_4035E0"
STATIC_DISPATCH_DEFAULT_SPAN = 0x10000

@dataclasses.dataclass(frozen=True, slots=True)
class StaticOpcodeRow:
    opcode: int
    handler: str
    schema: str
    operand_kinds: tuple[str, ...] = ()
    variable: bool = False
    length_expr: str = "2"
    notes: tuple[str, ...] = ()


@dataclasses.dataclass(slots=True)
class DecodedInstruction:
    offset: int
    opcode: int
    spec: dict[str, object]
    operands: list[str]

def static_row(
    opcode: int,
    handler: str,
    schema: str,
    operand_kinds: tuple[str, ...] = (),
    *,
    variable: bool = False,
    length_expr: str = "2",
    notes: tuple[str, ...] = (),
) -> StaticOpcodeRow:
    return StaticOpcodeRow(opcode, handler, schema, operand_kinds, variable, length_expr, notes)

STATIC_OPCODE_ROWS = (
    static_row(0x0000, 'sub_43C5B0', 'text0', variable=True, length_expr='2 + text_body + 1', notes=('载荷以原始 0xAA 终止', '普通字节按 raw_byte ^ 0xAA 解码', '原始 0x55 0xA8 表示内嵌控制块')),
    static_row(0x0001, 'sub_43D100', 'text1', variable=True, length_expr='2 + text_body + 1', notes=('载荷以原始 0xAB 终止', '普通字节按 raw_byte ^ 0xAA 解码', '原始 0x55 0xA8 表示内嵌控制块')),
    static_row(0x0003, 'sub_43D720', 'operands', ('raw16',), length_expr='2 + 2', notes=('由 decompile/43D720.c 与 decompile/41CDC0.c 控制流确认', '先读取一个 16 位大端存档位标识，再更新相关菜单状态')),
    static_row(0x0004, 'sub_43D7C0', 'operands', ('raw16', 'tag32'), length_expr='2 + 2 + 3|5', notes=('由 decompile/43D7C0.c 与 decompile/41CDC0.c 控制流确认', '先读取一个 16 位大端存档位标识，再读取一个 tag32 参数')),
    static_row(0x0005, 'sub_43D860', 'operands'),
    static_row(0x0006, 'sub_401000', 'operands'),
    static_row(0x0007, 'sub_401020', 'operands'),
    static_row(0x0008, 'sub_422D50', 'operands', ('tag32',), length_expr='2 + 3|5'),
    static_row(0x0009, 'sub_422DB0', 'operands', ('tag32',), length_expr='2 + 3|5'),
    static_row(0x000A, 'sub_43D9F0', 'operands', ('tag8', 'tag8'), length_expr='2 + 2|3 + 2|3'),
    static_row(0x000B, 'sub_422E20', 'operands', ('tag8', 'tag8'), length_expr='2 + 2|3 + 2|3'),
    static_row(0x000C, 'sub_401140', 'operands', variable=True, notes=('主路径控制流中包含循环',)),
    static_row(0x000D, 'sub_4012B0', 'operands', variable=True, notes=('主路径控制流中包含循环',)),
    static_row(0x000E, 'sub_401310', 'operands', variable=True, notes=('主路径控制流中包含循环',)),
    static_row(0x000F, 'sub_401400', 'operands', variable=True, notes=('主路径控制流中包含循环',)),
    static_row(0x0010, 'sub_401460', 'raw_be16_setvar', ('raw16',), length_expr='2 + 2', notes=('直接从脚本流读取 16 位大端变量槽编号',)),
    static_row(0x0011, 'sub_422EA0', 'operands', ('raw16',), length_expr='2 + 2', notes=('由 MEBIUS34.exe .text 0x422EA0 汇编确认', '读取 16 位大端表索引，并将该槽值复制到当前目标槽')),
    static_row(0x0012, 'sub_422F50', 'raw_be32', ('raw_be32',), variable=True, notes=('主路径体内直接访问程序计数器', '直接从脚本流读取一个 32 位大端立即数')),
    static_row(0x0013, 'sub_423000', 'operands', ('raw16',), length_expr='2 + 2', notes=('由 decompile/423000.c 控制流确认', '读取 16 位大端表索引，并将该槽值加到当前目标槽')),
    static_row(0x0014, 'sub_4230B0', 'raw_be32', ('raw_be32',), variable=True, notes=('主路径体内直接访问程序计数器', '直接从脚本流读取一个 32 位大端立即数')),
    static_row(0x0015, 'sub_423160', 'operands', ('raw16',), length_expr='2 + 2', notes=('由 decompile/423160.c 控制流确认', '读取 16 位大端表索引，并从当前目标槽减去该槽值')),
    static_row(0x0016, 'sub_423210', 'raw_be32', ('raw_be32',), variable=True, notes=('主路径体内直接访问程序计数器', '直接从脚本流读取一个 32 位大端立即数')),
    static_row(0x0017, 'sub_4232C0', 'operands', ('raw16',), length_expr='2 + 2', notes=('由 decompile/4232C0.c 控制流确认', '读取 16 位大端表索引，并将当前目标槽乘以该槽值')),
    static_row(0x0018, 'sub_423380', 'raw_be32', ('raw_be32',), variable=True, notes=('主路径体内直接访问程序计数器', '直接从脚本流读取一个 32 位大端立即数')),
    static_row(0x0019, 'sub_423440', 'operands', ('raw16',), length_expr='2 + 2', notes=('由 MEBIUS34.exe .text 0x423440 汇编确认', '读取 16 位大端表索引，并用该槽值做整除运算')),
    static_row(0x001A, 'sub_423500', 'raw_be32', ('raw_be32',), variable=True, notes=('主路径体内直接访问程序计数器', '直接从脚本流读取一个 32 位大端立即数')),
    static_row(0x001B, 'sub_4235C0', 'operands', ('raw16',), length_expr='2 + 2', notes=('由 MEBIUS34.exe .text 0x4235C0 汇编确认', '读取 16 位大端表索引，并将整除余数写回当前槽')),
    static_row(0x001C, 'sub_423680', 'raw_be32', ('raw_be32',), variable=True, notes=('主路径体内直接访问程序计数器', '直接从脚本流读取一个 32 位大端立即数')),
    static_row(0x001D, 'sub_423740', 'operands', ('raw16',), length_expr='2 + 2', notes=('由 MEBIUS34.exe .text 0x423740 汇编确认', '读取 16 位大端表索引，并将其与当前槽做按位或')),
    static_row(0x001E, 'sub_4237F0', 'raw_be32', ('raw_be32',), variable=True, notes=('主路径体内直接访问程序计数器', '直接从脚本流读取一个 32 位大端立即数')),
    static_row(0x001F, 'sub_4238A0', 'operands', ('raw16',), length_expr='2 + 2', notes=('由 decompile/4238A0.c 控制流确认', '读取 16 位大端表索引，并将其与当前槽做按位与')),
    static_row(0x0020, 'sub_423950', 'raw_be32', ('raw_be32',), variable=True, notes=('主路径体内直接访问程序计数器', '直接从脚本流读取一个 32 位大端立即数')),
    static_row(0x0021, 'sub_423A00', 'operands', ('raw16',), length_expr='2 + 2', notes=('由 decompile/423A00.c 控制流确认', '读取 16 位大端表索引，并将其与当前槽做按位异或')),
    static_row(0x0022, 'sub_423AB0', 'raw_be32', ('raw_be32',), variable=True, notes=('主路径体内直接访问程序计数器', '直接从脚本流读取一个 32 位大端立即数')),
    static_row(0x0028, 'sub_43DAA0', 'counted_cstrings', ('raw8', 'raw8', 'cstring_array'), variable=True, length_expr='2 + 1 + 1 + strings_block', notes=('由 MEBIUS34.exe .text 0x43DAA0 汇编确认', '先读取 1 字节条目数和 1 字节标志，再读取 count 个以 0 结尾的字符串')),
    static_row(0x002C, 'sub_423B70', 'operands', ('tag16',), length_expr='2 + 3', notes=('由 MEBIUS34.exe .text 0x423B70 汇编确认', '读取 tag16，随后结合随机值与除法结果写回槽位')),
    static_row(0x002D, 'sub_423BE0', 'operands', ('tag8',), length_expr='2 + 2|3', notes=('主路径中包含条件读取',)),
    static_row(0x002E, 'sub_423EA0', 'operands', ('tag8',), length_expr='2 + 2|3'),
    static_row(0x0030, 'sub_4014E0', 'operands', ('raw_be32',), length_expr='2 + 4', notes=('由 decompile/4014E0.c 控制流确认', '直接读取 32 位大端目标偏移，并把当前程序计数器改写为该绝对地址')),
    static_row(0x0031, 'sub_401590', 'raw_be32', ('raw_be32',), length_expr='2 + 4', notes=('由 MEBIUS34.exe .text 0x401590 汇编确认', '直接读取 32 位大端目标偏移，并把当前程序计数器压入内部跳转栈')),
    static_row(0x0032, 'sub_401680', 'operands', variable=True, notes=('主路径控制流中包含循环', '主路径体内直接访问程序计数器')),
    static_row(0x0033, 'sub_43DB70', 'call_file', ('tag16',), length_expr='2 + 3', notes=('由 MEBIUS34.exe .text 0x43DB70 汇编确认', '读取 tag16 后切换到另一个 SEEN 文件，并轮转一组返回栈缓冲区')),
    static_row(0x0034, 'sub_43DCD0', 'return', notes=('从返回栈恢复脚本编号和程序计数器',)),
    static_row(0x0035, 'sub_43DDF0', 'call_file', ('tag16',), variable=True, length_expr='2 + 3', notes=('加载另一个 SEEN 文件', '加载辅助脚本上下文', '主路径控制流中包含循环', '主路径体内直接访问程序计数器', '压入当前文件/程序计数器并跳转到另一个 SEEN 文件的程序计数器偏移 2 处')),
    static_row(0x0036, 'sub_424190', 'operands', ('tag8',), length_expr='2 + 2|3', notes=('主路径中包含条件读取',)),
    static_row(0x0038, 'sub_4016E0', 'operands'),
    static_row(0x0039, 'sub_424560', 'operands', ('tag32',), length_expr='2 + 3|5'),
    static_row(0x003A, 'sub_4245B0', 'operands', ('tag32',), length_expr='2 + 3|5', notes=('由 decompile/4245B0.c 控制流确认', '读取一个 tag32，并更新一组等待/计时相关全局状态')),
    static_row(0x003B, 'sub_401720', 'operands'),
    static_row(0x003C, 'sub_424600', 'operands', ('tag32',), length_expr='2 + 3|5'),
    static_row(0x0040, 'sub_401770', 'expr_blob', ('be16_length', 'raw32', 'blob'), variable=True, length_expr='2 + 2 + 4 + blob_size', notes=('读取 16 位大端二进制数据块长度和 32 位大端表达式参数', '从脚本流复制 blob_size 字节，并按该长度推进程序计数器')),
    static_row(0x0048, 'sub_402120', 'operands', variable=True, notes=('主路径体内直接访问程序计数器',)),
    static_row(0x0049, 'sub_402240', 'operands'),
    static_row(0x004A, 'sub_4023E0', 'operands', ('raw16', 'raw16', 'raw16', 'raw16', 'raw16', 'raw16', 'raw16', 'raw16', 'raw16'), length_expr='2 + 2*9', notes=('由 MEBIUS34.exe .text 0x4023E0 汇编确认', '连续读取 9 个 16 位大端槽位编号，用于后续布局/状态初始化')),
    static_row(0x004B, 'sub_424630', 'operands', notes=('由 MEBIUS34.exe .text 0x424630 汇编确认', '不从脚本流继续取参，只重置多组槽位和状态')),
    static_row(0x0050, 'sub_424850', 'operands', ('tag8_index', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0051, 'sub_4249D0', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32', 'tag8'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 2|3'),
    static_row(0x0052, 'sub_424AE0', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag8'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 2|3', notes=('主路径中包含条件读取',)),
    static_row(0x0053, 'sub_424D30', 'operands', ('tag8_index',), variable=True, length_expr='2 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x0054, 'sub_424E70', 'operands', ('tag8_index', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5', notes=('由 MEBIUS34.exe .text 0x424E70 汇编确认', '读取 tag8_index、tag32、tag32')),
    static_row(0x0058, 'sub_424F20', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32_x100', 'tag32_x100', 'tag32_x100'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5'),
    static_row(0x0059, 'sub_425030', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32_x100', 'tag32_x100', 'tag32_x100'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5'),
    static_row(0x005A, 'sub_4027E0', 'operands', variable=True, notes=('主路径控制流中包含循环',)),
    static_row(0x005B, 'sub_402A70', 'operands', variable=True, notes=('主路径控制流中包含循环',)),
    static_row(0x005C, 'sub_402D00', 'operands', variable=True, notes=('主路径控制流中包含循环',)),
    static_row(0x0060, 'sub_43DEC0', 'operands', ('tag8_index', 'raw_cstring', 'tag8'), variable=True, length_expr='2 + 2|3 + cstring+1 + 2|3', notes=('由 MEBIUS34.exe .text 0x43DEC0 汇编确认', '读取 tag8_index、一个原始字符串和一个 tag8，用于资源名与状态初始化')),
    static_row(0x0061, 'sub_43DFF0', 'operands', ('tag8_index', 'raw_cstring', 'tag32', 'tag32', 'tag8'), variable=True, length_expr='2 + 2|3 + cstring+1 + 3|5 + 3|5 + 2|3'),
    static_row(0x0062, 'sub_43E160', 'operands', ('tag8_index', 'raw_cstring', 'tag8'), variable=True, length_expr='2 + 2|3 + cstring+1 + 2|3'),
    static_row(0x0063, 'sub_43E2B0', 'operands', ('tag8_index', 'raw_cstring', 'tag32', 'tag32', 'tag8'), variable=True, length_expr='2 + 2|3 + cstring+1 + 3|5 + 3|5 + 2|3'),
    static_row(0x0068, 'sub_4251A0', 'operands', ('tag8_index', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0070, 'sub_43E430', 'counted_flags', ('raw8', 'blob'), variable=True, length_expr='2 + 1 + count', notes=('由 decompile/43E430.c 控制流确认', '先读取 1 字节条目数，再读取 count 个标志字节；非零条目会按当前索引调用 sub_437920')),
    static_row(0x0071, 'sub_43E4E0', 'counted_flags_tail3', ('raw8', 'blob', 'raw8', 'raw8', 'raw8'), variable=True, length_expr='2 + 1 + count + 1 + 1 + 1', notes=('由 decompile/43E4E0.c 控制流确认', '先读取 1 字节条目数和 count 个标志字节；随后再读取 3 个附加标志字节，分别控制 sub_419B60(0..2)')),
    static_row(0x0072, 'sub_43E630', 'operands', variable=True, notes=('主路径控制流中包含循环',)),
    static_row(0x0073, 'sub_425590', 'operands', ('tag8_index', 'tag8'), variable=True, length_expr='2 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x0074, 'sub_43E6E0', 'operands', ('tag8_index', 'tag8'), variable=True, length_expr='2 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x0075, 'sub_425830', 'operands', ('tag8_index', 'tag8'), variable=True, length_expr='2 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x0076, 'sub_43EA20', 'operands', ('tag8_index', 'tag8'), variable=True, length_expr='2 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x0077, 'sub_425AD0', 'operands', ('tag8_index', 'tag8'), variable=True, length_expr='2 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x0078, 'sub_43ED20', 'operands', ('tag8_index', 'tag8'), variable=True, length_expr='2 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x0079, 'sub_425D70', 'operands', ('tag8',), length_expr='2 + 2|3', notes=('由 MEBIUS34.exe .text 0x425D70 汇编确认', '读取一个 tag8 并更新全局状态标志')),
    static_row(0x007A, 'sub_402E70', 'operands'),
    static_row(0x007B, 'sub_425DC0', 'operands', ('tag8_index', 'tag8'), variable=True, length_expr='2 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x007C, 'sub_425F40', 'operands', ('tag8_index', 'tag8'), variable=True, length_expr='2 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x007D, 'sub_426100', 'operands', ('tag8_index', 'tag8'), variable=True, length_expr='2 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x007E, 'sub_426280', 'operands', ('tag8_index', 'tag8'), variable=True, length_expr='2 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x0080, 'sub_426460', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0081, 'sub_4265A0', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0084, 'sub_426720', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5', notes=('由 MEBIUS34.exe .text 0x426720 汇编确认', '读取 1 个 tag8_index 和 6 个 tag32，用于一组几何/运动参数')),
    static_row(0x0085, 'sub_4268B0', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0086, 'sub_426B70', 'operands', ('tag8_index', 'raw8', 'tag32_x100', 'tag32', 'raw8', 'tag32_x100', 'tag32', 'raw8', 'tag32_x100', 'tag32'), length_expr='2 + 2|3 + 1 + 3|5 + 3|5 + 1 + 3|5 + 3|5 + 1 + 3|5 + 3|5', notes=('由 decompile/426B70.c 控制流确认', '读取一个 tag8_index，以及 3 组(raw8 开关, tag32_x100, tag32)参数，用于更新三段状态/参数槽位')),
    static_row(0x0087, 'sub_426D70', 'operands', ('tag8_index', 'tag32_x100', 'tag32', 'tag32_x100', 'tag32', 'tag32_x100', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x008C, 'sub_426FA0', 'operands', ('tag8_index', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x008D, 'sub_4270A0', 'operands', ('tag8_index', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x008E, 'sub_427200', 'operands', ('tag8_index', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x008F, 'sub_427300', 'operands', ('tag8_index', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0090, 'sub_427460', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0091, 'sub_427600', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0092, 'sub_427860', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0093, 'sub_427AA0', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0096, 'sub_427C80', 'operands', ('tag8_index', 'tag32_x100', 'tag32_x100', 'tag32_x100'), variable=True, length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5', notes=('主路径控制流中包含循环', '主路径体内直接访问程序计数器', '主路径中包含条件读取')),
    static_row(0x0097, 'sub_4281C0', 'operands', ('tag8_index', 'tag32_x100', 'tag32_x100', 'tag32_x100'), variable=True, length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5', notes=('主路径控制流中包含循环', '主路径体内直接访问程序计数器', '主路径中包含条件读取')),
    static_row(0x0098, 'sub_43F030', 'counted_cstrings16', ('tag8_index', 'tag32', 'tag32', 'raw16', 'cstring_array'), variable=True, length_expr='2 + 2|3 + 3|5 + 3|5 + 2 + strings_block', notes=('由 decompile/43F030.c 控制流确认', '读取 tag8_index、两个 tag32、一个 16 位字符串数，再读取 count 个以 0 结尾的资源名字符串并批量加载对应 .mcg 文件')),
    static_row(0x0099, 'sub_428CA0', 'operands', ('tag8_index',), variable=True, length_expr='2 + 2|3', notes=('主路径控制流中包含循环', '主路径体内直接访问程序计数器')),
    static_row(0x009A, 'sub_43FB90', 'operands', ('tag8_index',), variable=True, length_expr='2 + 2|3', notes=('主路径控制流中包含循环', '主路径体内直接访问程序计数器')),
    static_row(0x009B, 'sub_428F90', 'tag16_tag32_pairs', ('tag8_index', 'raw16', 'pair_table'), variable=True, length_expr='2 + 2|3 + 2 + count*(3 + 3|5)', notes=('由 decompile/428F90.c 控制流确认', '读取一个 tag8_index、一个 16 位条目数，然后依次读取 count 组(tag16, tag32)参数表')),
    static_row(0x009C, 'sub_440010', 'operands', ('tag8_index',), variable=True, length_expr='2 + 2|3', notes=('主路径控制流中包含循环', '主路径体内直接访问程序计数器')),
    static_row(0x009D, 'sub_429280', 'operands', ('tag8_index',), variable=True, length_expr='2 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x009E, 'sub_4296E0', 'operands', ('tag8_index',), variable=True, length_expr='2 + 2|3', notes=('主路径控制流中包含循环', '主路径体内直接访问程序计数器', '主路径中包含条件读取')),
    static_row(0x009F, 'sub_4299E0', 'operands', ('tag8_index',), variable=True, length_expr='2 + 2|3', notes=('主路径控制流中包含循环', '主路径体内直接访问程序计数器')),
    static_row(0x00A0, 'sub_429E50', 'operands', ('tag8_index', 'tag32', 'tag32', 'tag32', 'tag32'), length_expr='2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5'),
    static_row(0x00A1, 'sub_429F20', 'operands', ('tag8_index',), length_expr='2 + 2|3', notes=('主路径中包含条件读取',)),
    static_row(0x00C0, 'sub_402EC0', 'operands'),
    static_row(0x00C1, 'sub_429F70', 'operands', ('tag16',), length_expr='2 + 3', notes=('主路径中包含条件读取',)),
    static_row(0x00C2, 'sub_440490', 'operands', variable=True, notes=('加载辅助脚本上下文', '主路径控制流中包含循环', '主路径体内直接访问程序计数器')),
    static_row(0x00C3, 'sub_471A60', 'operands', ('tag16', 'raw_cstring', 'tag8'), variable=True, length_expr='2 + 3 + cstring+1 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x00C4, 'sub_42A0A0', 'operands', ('tag16', 'raw16', 'raw16', 'raw16', 'raw16', 'raw16', 'raw16', 'raw16'), length_expr='2 + 3 + 2*7', notes=('由 decompile/42A0A0.c 控制流确认', '读取一个 tag16 存档号，以及 7 个 raw16 变量槽位；成功时把存档中的 7 字节时间戳/状态字段写入这些槽位')),
    static_row(0x00C5, 'sub_440500', 'operands', ('tag16', 'tag8_index', 'tag8'), variable=True, length_expr='2 + 3 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x00C6, 'sub_440930', 'operands', ('tag16', 'tag8_index', 'tag32', 'tag32', 'tag8'), variable=True, length_expr='2 + 3 + 2|3 + 3|5 + 3|5 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x00C7, 'sub_440D90', 'operands', ('tag16', 'tag8_index', 'tag8'), variable=True, length_expr='2 + 3 + 2|3 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x00C8, 'sub_441260', 'operands', ('tag16', 'tag8_index', 'tag32', 'tag32', 'tag8'), variable=True, length_expr='2 + 3 + 2|3 + 3|5 + 3|5 + 2|3', notes=('主路径控制流中包含循环', '主路径中包含条件读取')),
    static_row(0x00E0, 'sub_42A4F0', 'operands', ('raw_cstring', 'tag8_index'), variable=True, length_expr='2 + cstring+1 + 2|3'),
    static_row(0x00E1, 'sub_42A560', 'operands', ('raw_cstring', 'tag8_index'), variable=True, length_expr='2 + cstring+1 + 2|3'),
    static_row(0x00E2, 'sub_42A5D0', 'operands', ('raw_cstring', 'tag8_index'), variable=True, length_expr='2 + cstring+1 + 2|3'),
    static_row(0x00E3, 'sub_42A640', 'operands', ('raw_cstring', 'tag8_index'), variable=True, length_expr='2 + cstring+1 + 2|3'),
    static_row(0x00E4, 'sub_42A6B0', 'operands', ('tag8_index',), length_expr='2 + 2|3', notes=('主路径中包含条件读取',)),
    static_row(0x00E5, 'sub_402EE0', 'operands'),
    static_row(0x00E6, 'sub_42A6F0', 'operands', ('raw_cstring', 'tag8_index', 'tag32'), variable=True, length_expr='2 + cstring+1 + 2|3 + 3|5'),
    static_row(0x00E7, 'sub_42A780', 'operands', ('raw_cstring', 'tag8_index', 'tag32'), variable=True, length_expr='2 + cstring+1 + 2|3 + 3|5'),
    static_row(0x00E8, 'sub_42A810', 'operands', ('raw_cstring', 'tag8_index', 'tag32'), variable=True, length_expr='2 + cstring+1 + 2|3 + 3|5'),
    static_row(0x00E9, 'sub_42A8A0', 'operands', ('raw_cstring', 'tag8_index', 'tag32'), variable=True, length_expr='2 + cstring+1 + 2|3 + 3|5'),
    static_row(0x00EA, 'sub_42A930', 'operands', ('tag8_index', 'tag32'), length_expr='2 + 2|3 + 3|5'),
    static_row(0x00EB, 'sub_42A9A0', 'operands', ('tag32',), length_expr='2 + 3|5', notes=('由 MEBIUS34.exe .text 0x42A9A0 汇编确认', '读取一个 tag32，并把同一数值广播到 10 组槽位')),
    static_row(0x00EE, 'sub_42AA40', 'operands', ('raw_cstring', 'tag8_index'), variable=True, length_expr='2 + cstring+1 + 2|3', notes=('主路径中包含条件读取',)),
    static_row(0x00EF, 'sub_42AB00', 'operands', ('raw_cstring', 'tag8_index'), variable=True, length_expr='2 + cstring+1 + 2|3', notes=('主路径中包含条件读取',)),
    static_row(0x00F0, 'sub_42AC00', 'operands', ('tag8', 'tag8_index'), length_expr='2 + 2|3 + 2|3', notes=('由 MEBIUS34.exe .text 0x42AC00 汇编确认', '读取 tag8、tag8_index')),
    static_row(0x00F1, 'sub_42ACB0', 'operands', ('tag8', 'tag8_index'), length_expr='2 + 2|3 + 2|3'),
    static_row(0x00F2, 'sub_42AD60', 'operands', ('tag8', 'tag8_index'), length_expr='2 + 2|3 + 2|3'),
    static_row(0x00F3, 'sub_42AE10', 'operands', ('tag8_index',), length_expr='2 + 2|3', notes=('主路径中包含条件读取',)),
    static_row(0x00F4, 'sub_402F40', 'operands'),
    static_row(0x00F5, 'sub_42AE50', 'operands', ('tag8', 'tag8_index', 'tag32'), length_expr='2 + 2|3 + 2|3 + 3|5'),
    static_row(0x00F6, 'sub_42AF30', 'operands', ('tag8', 'tag8_index', 'tag32'), length_expr='2 + 2|3 + 2|3 + 3|5'),
    static_row(0x00F7, 'sub_42B010', 'operands', ('tag8', 'tag8_index', 'tag32'), length_expr='2 + 2|3 + 2|3 + 3|5'),
    static_row(0x00F8, 'sub_42B0F0', 'operands', ('tag8_index', 'tag32'), length_expr='2 + 2|3 + 3|5'),
    static_row(0x00F9, 'sub_42B170', 'operands', ('tag32',), length_expr='2 + 3|5', notes=('主路径中包含条件读取',)),
    static_row(0x0100, 'sub_42ABC0', 'operands', ('tag8_index',), length_expr='2 + 2|3', notes=('主路径中包含条件读取',)),
    static_row(0x0101, 'sub_402F20', 'operands'),
    static_row(0x0104, 'sub_42B1C0', 'operands', ('tag8', 'tag8_index'), length_expr='2 + 2|3 + 2|3'),
    static_row(0x0110, 'sub_402F60', 'operands', variable=True, notes=('主路径控制流中包含循环',)),
    static_row(0x0111, 'sub_42B270', 'operands'),
    static_row(0x0112, 'sub_42B290', 'operands'),
    static_row(0x0113, 'sub_403380', 'operands'),
    static_row(0x0114, 'sub_4033C0', 'operands'),
    static_row(0x0115, 'sub_4033E0', 'operands'),
    static_row(0x0116, 'sub_403420', 'operands'),
    static_row(0x0120, 'sub_403460', 'operands'),
    static_row(0x0121, 'sub_403480', 'operands'),
    static_row(0x0122, 'sub_4034B0', 'operands'),
    static_row(0x0123, 'sub_4034E0', 'operands'),
    static_row(0x0124, 'sub_403500', 'operands'),
    static_row(0x0125, 'sub_403530', 'operands'),
    static_row(0x0128, 'sub_42B2B0', 'operands', ('tag8', 'raw8', 'tag16', 'tag16', 'tag16', 'tag16'), length_expr='2 + 2|3 + 1 + 3 + 3 + 3 + 3', notes=('由 MEBIUS34.exe .text 0x42B2B0 汇编确认', '先读取 tag8 和一个原始字节，再连续读取 4 个 tag16')),
    static_row(0x0129, 'sub_403570', 'operands'),
    static_row(0x012A, 'sub_42B390', 'operands', ('tag8',), length_expr='2 + 2|3', notes=('由 MEBIUS34.exe .text 0x42B390 汇编确认', '读取一个 tag8 并置位对应条目的激活标记')),
    static_row(0x012B, 'sub_403590', 'operands'),
    static_row(0x012C, 'sub_42B400', 'operands', ('tag8',), length_expr='2 + 2|3', notes=('由 decompile/42B400.c 控制流确认', '手动读取一个 tag8，并把对应 UI 状态条目标记为启用')),
    static_row(0x012D, 'sub_42B470', 'operands', ('tag8', 'raw16'), length_expr='2 + 2|3 + 2', notes=('由 decompile/42B470.c 控制流确认', '手动读取一个 tag8 和一个 raw16，并更新对应 UI 状态条目的附加数值')),
    static_row(0x012E, 'sub_42B510', 'operands', ('tag8', 'raw8', 'tag16', 'tag16', 'tag16', 'tag16'), length_expr='2 + 2|3 + 1 + 3 + 3 + 3 + 3', notes=('由 decompile/42B510.c 控制流确认', '先读取一个 tag8 和原始字节，再读取 4 个 tag16，并据此更新 UI 条目的矩形区域与启用状态')),
    static_row(0x012F, 'sub_42B5F0', 'operands', variable=True, notes=('主路径体内直接访问程序计数器',)),
    static_row(0x0130, 'sub_42B650', 'operands', ('raw_cstring',), variable=True, length_expr='2 + cstring+1', notes=('由 MEBIUS34.exe .text 0x42B650 汇编确认', '读取一个以 0 结尾的原始字符串并交给 476079 处理')),
    static_row(0x0132, 'sub_42B740', 'operands', ('raw_cstring',), variable=True, length_expr='2 + cstring+1', notes=('主路径中包含条件读取',)),
    static_row(0x0134, 'sub_42B6D0', 'operands', ('raw_cstring',), variable=True, length_expr='2 + cstring+1', notes=('主路径中包含条件读取',)),
    static_row(0x0136, 'sub_42B740', 'operands', ('raw_cstring',), variable=True, length_expr='2 + cstring+1', notes=('主路径中包含条件读取',)),
    static_row(0x0140, 'sub_4035B0', 'operands'),
    static_row(0x0FFFF, 'sub_43D860', 'operands'),
)

FUNCTIONAL_MNEMONIC_OVERRIDES = {
    0x0000: "text_aa",
    0x0001: "text_ab",
    0x0003: "set_save_bit",
    0x0004: "set_save_bit_tag32",
    0x0005: "check_flag_transition",
    0x0006: "begin_scene_state",
    0x0007: "latch_scene_state",
    0x0008: "load_tagged_resource",
    0x0009: "load_tagged_resource_alt",
    0x000A: "poll_flag_transition",
    0x000B: "set_flag_pair",
    0x000C: "clear_slot_bank_a_save",
    0x000D: "clear_slot_bank_a",
    0x000E: "clear_slot_bank_b_save",
    0x000F: "clear_slot_bank_b",
    0x0010: "set_slot_raw16",
    0x0011: "copy_slot_ref16",
    0x0012: "set_slot_imm32",
    0x0013: "add_slot_ref16",
    0x0014: "add_slot_imm32",
    0x0015: "sub_slot_ref16",
    0x0016: "sub_slot_imm32",
    0x0017: "mul_slot_ref16",
    0x0018: "mul_slot_imm32",
    0x0019: "div_slot_ref16",
    0x001A: "div_slot_imm32",
    0x001B: "mod_slot_ref16",
    0x001C: "mod_slot_imm32",
    0x001D: "or_slot_ref16",
    0x001E: "or_slot_imm32",
    0x001F: "and_slot_ref16",
    0x0020: "and_slot_imm32",
    0x0021: "xor_slot_ref16",
    0x0022: "xor_slot_imm32",
    0x0028: "define_string_table",
    0x002C: "random_div_ref16",
    0x002D: "check_install_game_registry",
    0x002E: "check_install_game_env",
    0x0030: "jump_abs32",
    0x0031: "jump_push_abs32",
    0x0032: "dispatch_engine_state",
    0x0033: "call_seen_rotate",
    0x0034: "return_seen",
    0x0035: "call_seen",
    0x0036: "select_install_game",
    0x0038: "mark_time_checkpoint",
    0x0039: "set_wait_timer_hold",
    0x003A: "set_wait_timer",
    0x003B: "store_elapsed_ticks",
    0x003C: "advance_wait_checkpoint",
    0x0040: "jump_if_false",
    0x0048: "dispatch_layout_state",
    0x0049: "sample_pointer_state",
    0x004A: "init_layout_slots9",
    0x004B: "reset_layout_groups",
    0x0050: "define_layout_window",
    0x0051: "define_layout_motion",
    0x0052: "define_layout_window_motion",
    0x0053: "clear_layout_window",
    0x0054: "set_layout_bounds",
    0x0058: "set_layout_transform",
    0x0059: "set_layout_transform_ext",
    0x005A: "reset_ui_bank_full",
    0x005B: "reset_ui_bank_half",
    0x005C: "reset_ui_bank_aux",
    0x0060: "init_named_resource",
    0x0061: "define_mcg_slot",
    0x0062: "define_mcg_slot_basic",
    0x0063: "define_mcg_slot_pos",
    0x0068: "configure_resource_entry",
    0x0070: "dispatch_flagged_entries",
    0x0071: "dispatch_flagged_entries_tail3",
    0x0072: "dispatch_all_entries_tail3",
    0x0073: "enable_dispatch_entry",
    0x0074: "select_dispatch_entry",
    0x0075: "disable_dispatch_entry",
    0x0076: "select_dispatch_entry_alt",
    0x0077: "show_dispatch_entry",
    0x0078: "hide_dispatch_entry",
    0x0079: "set_global_flag",
    0x007A: "clear_ui_cache_state",
    0x007B: "set_mask_entry_a",
    0x007C: "start_mask_entry_a",
    0x007D: "set_mask_entry_b",
    0x007E: "start_mask_entry_b",
    0x0080: "set_ui_move_target",
    0x0081: "start_ui_move",
    0x0084: "set_motion_geometry",
    0x0085: "set_ui_move_box",
    0x0086: "set_segment_params",
    0x0087: "set_segment_curve",
    0x008C: "set_ui_scale_x",
    0x008D: "start_ui_scale_x",
    0x008E: "set_ui_scale_y",
    0x008F: "start_ui_scale_y",
    0x0090: "set_ui_motion2d",
    0x0091: "start_ui_motion2d",
    0x0092: "set_ui_motion3d",
    0x0093: "start_ui_motion3d",
    0x0096: "define_ui_motion_path",
    0x0097: "prepare_ui_motion_path",
    0x0098: "load_mcg_batch",
    0x0099: "apply_ui_timeline",
    0x009A: "define_ui_timeline_a",
    0x009B: "define_pair_table",
    0x009C: "define_ui_timeline_b",
    0x009D: "step_ui_timeline",
    0x009E: "define_ui_path_points",
    0x009F: "start_ui_path_points",
    0x00A0: "set_ui_quad_rect",
    0x00A1: "arm_ui_entry",
    0x00C0: "trigger_save_state",
    0x00C1: "write_save_timestamp",
    0x00C2: "load_secondary_seen",
    0x00C3: "define_save_entry_text",
    0x00C4: "read_save_timestamp_slots",
    0x00C5: "define_save_entry_slot",
    0x00C6: "define_save_entry_rect",
    0x00C7: "define_save_entry_state",
    0x00C8: "define_save_entry_state_rect",
    0x00E0: "request_voice_a",
    0x00E1: "request_voice_b",
    0x00E2: "request_voice_c",
    0x00E3: "request_voice_d",
    0x00E4: "request_voice_slot",
    0x00E5: "refresh_media_group_a",
    0x00E6: "request_voice_param_a",
    0x00E7: "request_voice_param_b",
    0x00E8: "request_voice_param_c",
    0x00E9: "request_voice_param_d",
    0x00EA: "set_media_group_a_value",
    0x00EB: "broadcast_tag32_10x",
    0x00EE: "request_voice_named_a",
    0x00EF: "request_voice_named_b",
    0x00F0: "set_media_group_mode",
    0x00F1: "request_bgm_a",
    0x00F2: "request_bgm_b",
    0x00F5: "request_bgm_param_a",
    0x00F6: "request_bgm_param_b",
    0x00F7: "request_bgm_param_c",
    0x00F9: "set_ui_value",
    0x00F3: "request_bgm_slot",
    0x00F4: "refresh_media_group_b",
    0x00F8: "set_media_group_b_value",
    0x0100: "arm_ui_state_entry",
    0x0101: "arm_bgm_slots_all",
    0x0128: "define_ui_entry",
    0x0104: "request_bgm_named",
    0x0110: "release_ui_buffers",
    0x0111: "enable_ui_context",
    0x0112: "disable_ui_context",
    0x0113: "enable_ui_mode_a",
    0x0114: "disable_ui_mode_a",
    0x0115: "enable_ui_mode_b",
    0x0116: "disable_ui_mode_b",
    0x0120: "begin_frame_hold",
    0x0121: "clear_frame_hold",
    0x0122: "enable_ui_plane_a",
    0x0123: "disable_ui_plane_a",
    0x0124: "enable_ui_plane_b",
    0x0125: "capture_ui_plane_b",
    0x0129: "refresh_ui_group_a",
    0x012A: "activate_ui_entry",
    0x012B: "refresh_ui_group_b",
    0x012C: "enable_ui_state_entry",
    0x012D: "set_ui_state_value",
    0x012E: "set_ui_entry_rect",
    0x012F: "dispatch_ui_state",
    0x0130: "load_named_asset",
    0x0132: "load_movie_flagged",
    0x0134: "load_movie_plain",
    0x0136: "load_movie_flagged_alias",
    0x0140: "mark_scene_dirty",
    0xFFFF: "check_flag_transition_alias",
}

def infer_mnemonic_domain(opcode: int, operand_kinds: tuple[str, ...], notes: tuple[str, ...]) -> str:
    note_text = " ".join(normalize_notes(notes))
    note_lower = note_text.lower()
    if "save" in note_lower or "存档" in note_text or 0x00C0 <= opcode <= 0x00C8:
        return "save"
    if "布局" in note_text or 0x0048 <= opcode <= 0x005F:
        return "layout"
    if "mcg" in note_lower or "资源" in note_text or "raw_cstring" in operand_kinds or 0x0060 <= opcode <= 0x0068:
        return "resource"
    if "ui" in note_lower or "矩形" in note_text or "条目" in note_text or 0x0080 <= opcode <= 0x0130:
        return "ui"
    if "secondary script context" in note_lower or "脚本" in note_text:
        return "script"
    if "状态" in note_text or "标志" in note_text or "等待" in note_text or "计时" in note_text:
        return "state"
    return "engine"

def infer_mnemonic_object(schema: str, operand_kinds: tuple[str, ...]) -> str:
    if schema == "tag16_tag32_pairs":
        return "table"
    if schema in {"counted_cstrings", "counted_cstrings16", "counted_flags", "counted_flags_tail3"}:
        return "entries"
    if "tag32_x100" in operand_kinds:
        return "transform"
    if "raw_cstring" in operand_kinds and "tag8_index" in operand_kinds:
        return "slot"
    if "raw_cstring" in operand_kinds:
        return "text"
    if operand_kinds[:1] == ("tag8_index",):
        return "entry"
    if operand_kinds[:1] == ("tag16",):
        return "slot"
    if operand_kinds[:1] == ("tag8",):
        return "mode"
    if operand_kinds[:1] == ("tag32",):
        return "value"
    if len(operand_kinds) >= 4:
        return "bundle"
    return "state"

def infer_mnemonic_action(schema: str, operand_kinds: tuple[str, ...], notes: tuple[str, ...]) -> str:
    note_text = " ".join(normalize_notes(notes)).lower()
    if schema == "call_file":
        return "call"
    if schema == "return":
        return "return"
    if schema == "expr_blob":
        return "branch"
    if schema in {"counted_cstrings", "counted_cstrings16"}:
        return "load"
    if schema in {"counted_flags", "counted_flags_tail3"}:
        return "dispatch"
    if schema == "tag16_tag32_pairs":
        return "define"
    if "direct pc access" in note_text:
        return "dispatch"
    if "raw_cstring" in operand_kinds:
        return "define"
    if not operand_kinds:
        return "trigger"
    if operand_kinds[0] in {"tag8_index", "tag16"}:
        return "configure"
    if operand_kinds[0] in {"tag8", "tag32"}:
        return "set"
    return "update"

def build_functional_mnemonic(opcode: int, schema: str, operand_kinds: tuple[str, ...], notes: tuple[str, ...]) -> str:
    override = FUNCTIONAL_MNEMONIC_OVERRIDES.get(opcode)
    if override is not None:
        return override
    action = infer_mnemonic_action(schema, operand_kinds, notes)
    domain = infer_mnemonic_domain(opcode, operand_kinds, notes)
    obj = infer_mnemonic_object(schema, operand_kinds)
    return f"{action}_{domain}_{obj}_{opcode:04x}"

def build_static_opcode_entry(row: StaticOpcodeRow, used: set[str]) -> dict[str, object]:
    mnemonic = build_functional_mnemonic(row.opcode, row.schema, row.operand_kinds, row.notes)
    if mnemonic in used:
        mnemonic = f"{mnemonic}_{row.opcode:04x}"
    used.add(mnemonic)
    notes = normalize_notes(row.notes)
    return {
        "opcode": row.opcode,
        "opcode_hex": format_hex(row.opcode, 4),
        "mnemonic": mnemonic,
        "handler": row.handler,
        "impl_hex": handler_impl_hex(row.handler),
        "source_file": f"{row.handler.removeprefix('sub_')}.c",
        "schema": row.schema,
        "operand_kinds": list(row.operand_kinds),
        "script_target_operands": list(get_decoded_script_target_operand_indexes(row.opcode)),
        "variable": row.variable,
        "length_expr": row.length_expr,
        "notes": list(notes),
        "evidence": ["static opcode table"],
    }

def build_static_spec() -> dict[str, object]:
    used: set[str] = set()
    opcodes = [build_static_opcode_entry(row, used) for row in STATIC_OPCODE_ROWS]
    return {
        "dispatch_default_handler": STATIC_DISPATCH_DEFAULT_HANDLER,
        "dispatch_default_span": STATIC_DISPATCH_DEFAULT_SPAN,
        "dispatch_base": f"0x{DISPATCH_BASE:08X}",
        "explicit_opcode_count": len(opcodes),
        "opcodes": opcodes,
    }

def build_spec_indexes(
    spec: dict[str, object] | None = None,
) -> tuple[dict[int, dict[str, object]], dict[str, dict[str, object]]]:
    spec = spec or build_spec()
    opcodes = cast(list[dict[str, object]], spec["opcodes"])
    by_opcode = {cast(int, entry["opcode"]): entry for entry in opcodes}
    by_mnemonic = {cast(str, entry["mnemonic"]): entry for entry in opcodes}
    return by_opcode, by_mnemonic

def decode_counted_cstrings(data: bytes, offset: int) -> tuple[list[str], int, int, int]:
    count = data[offset]
    flag = data[offset + 1]
    cursor = offset + 2
    strings: list[str] = []
    for _ in range(count):
        token, size = decode_cstring(data, cursor)
        strings.append(token)
        cursor += size
    return strings, count, flag, cursor - offset

def decode_string_array(data: bytes, offset: int, count: int) -> tuple[list[str], int]:
    cursor = offset
    strings: list[str] = []
    for _ in range(count):
        token, size = decode_cstring(data, cursor)
        strings.append(token)
        cursor += size
    return strings, cursor - offset

def measure_instruction(spec: dict[str, object], operands: list[str]) -> int:
    schema = cast(str, spec["schema"])
    operand_kinds = cast(list[str], spec["operand_kinds"])
    size = 2

    if schema == "text0":
        if len(operands) != 1:
            raise ValueError(f"{spec['mnemonic']} expects 1 operand")
        return size + len(encode_text_payload(operands[0], 0xAA))

    if schema == "text1":
        if len(operands) != 1:
            raise ValueError(f"{spec['mnemonic']} expects 1 operand")
        return size + len(encode_text_payload(operands[0], 0xAB))

    if schema == "expr_blob":
        _, _, blob_token = split_expr_blob_operands(operands)
        return size + 2 + 4 + len(parse_expr_blob_token(blob_token))

    if schema == "counted_flags":
        if len(operands) != 2:
            raise ValueError(f"{spec['mnemonic']} expects 2 operands")
        count = parse_int_token(operands[0])
        flags = parse_blob_token(operands[1])
        if count != len(flags):
            raise ValueError(
                f"flag count mismatch for {spec['mnemonic']}: declared {count}, actual {len(flags)}"
            )
        return size + 1 + len(flags)

    if schema == "counted_flags_tail3":
        if len(operands) != 5:
            raise ValueError(f"{spec['mnemonic']} expects 5 operands")
        count = parse_int_token(operands[0])
        flags = parse_blob_token(operands[1])
        if count != len(flags):
            raise ValueError(
                f"flag count mismatch for {spec['mnemonic']}: declared {count}, actual {len(flags)}"
            )
        return size + 1 + len(flags) + 3

    if schema == "counted_cstrings16":
        if len(operands) != 5:
            raise ValueError(f"{spec['mnemonic']} expects 5 operands")
        count = parse_int_token(operands[3])
        strings = string_list_to_bytes(operands[4])
        if count != len(strings):
            raise ValueError(
                f"string count mismatch for {spec['mnemonic']}: declared {count}, actual {len(strings)}"
            )
        return (
            size
            + len(encode_tag_operand("tag8_index", operands[0]))
            + len(encode_tag_operand("tag32", operands[1]))
            + len(encode_tag_operand("tag32", operands[2]))
            + 2
            + sum(len(item) + 1 for item in strings)
        )

    if schema == "tag16_tag32_pairs":
        if len(operands) < 2:
            raise ValueError(f"{spec['mnemonic']} expects at least 2 operands")
        count = parse_int_token(operands[1])
        pair_operands = operands[2:]
        if len(pair_operands) != count * 2:
            raise ValueError(
                f"pair count mismatch for {spec['mnemonic']}: declared {count}, actual {len(pair_operands) // 2}"
            )
        size += len(encode_tag_operand("tag8_index", operands[0]))
        size += 2
        for index in range(0, len(pair_operands), 2):
            size += len(encode_tag_operand("tag16", pair_operands[index]))
            size += len(encode_tag_operand("tag32", pair_operands[index + 1]))
        return size

    if schema == "counted_cstrings":
        if len(operands) != 3:
            raise ValueError(f"{spec['mnemonic']} expects 3 operands")
        strings = string_list_to_bytes(operands[2])
        return size + 1 + 1 + sum(len(item) + 1 for item in strings)

    if len(operands) != len(operand_kinds):
        raise ValueError(f"{spec['mnemonic']} expects {len(operand_kinds)} operands, got {len(operands)}")

    for kind, token in zip(operand_kinds, operands):
        if kind in TOKEN_PREFIX_BY_KIND:
            size += len(encode_tag_operand(kind, token))
        elif kind == "raw8":
            size += 1
        elif kind == "raw16":
            size += 2
        elif kind == "raw_be32":
            size += 4
        elif kind == "raw_cstring":
            size += len(string_literal_to_bytes(token)) + 1
        else:
            raise ValueError(f"unsupported operand kind in size calculation: {kind}")
    return size

def encode_instruction(
    spec: dict[str, object],
    operands: list[str],
    labels: dict[str, int] | None = None,
) -> bytes:
    labels = labels or {}
    opcode = cast(int, spec["opcode"])
    schema = cast(str, spec["schema"])
    operand_kinds = cast(list[str], spec["operand_kinds"])
    out = bytearray(opcode.to_bytes(2, "big"))

    if schema == "text0":
        if len(operands) != 1:
            raise ValueError(f"{spec['mnemonic']} expects 1 operand")
        out.extend(encode_text_payload(operands[0], 0xAA))
        return bytes(out)

    if schema == "text1":
        if len(operands) != 1:
            raise ValueError(f"{spec['mnemonic']} expects 1 operand")
        out.extend(encode_text_payload(operands[0], 0xAB))
        return bytes(out)

    if schema == "expr_blob":
        len_token, target_token, blob_token = split_expr_blob_operands(operands)
        blob = parse_expr_blob_token(blob_token)
        blob_len = len(blob) if len_token is None else parse_int_token(len_token, labels)
        raw32 = parse_int_token(target_token, labels)
        if blob_len != len(blob):
            raise ValueError(
                f"blob length mismatch for {spec['mnemonic']}: declared {blob_len}, actual {len(blob)}"
            )
        out.extend(blob_len.to_bytes(2, "big"))
        out.extend(raw32.to_bytes(4, "big"))
        out.extend(blob)
        return bytes(out)

    if schema == "counted_flags":
        if len(operands) != 2:
            raise ValueError(f"{spec['mnemonic']} expects 2 operands")
        count = parse_int_token(operands[0], labels) & 0xFF
        flags = parse_blob_token(operands[1])
        if count != len(flags):
            raise ValueError(
                f"flag count mismatch for {spec['mnemonic']}: declared {count}, actual {len(flags)}"
            )
        out.append(count)
        out.extend(flags)
        return bytes(out)

    if schema == "counted_flags_tail3":
        if len(operands) != 5:
            raise ValueError(f"{spec['mnemonic']} expects 5 operands")
        count = parse_int_token(operands[0], labels) & 0xFF
        flags = parse_blob_token(operands[1])
        if count != len(flags):
            raise ValueError(
                f"flag count mismatch for {spec['mnemonic']}: declared {count}, actual {len(flags)}"
            )
        out.append(count)
        out.extend(flags)
        for token in operands[2:]:
            out.append(parse_int_token(token, labels) & 0xFF)
        return bytes(out)

    if schema == "counted_cstrings16":
        if len(operands) != 5:
            raise ValueError(f"{spec['mnemonic']} expects 5 operands")
        out.extend(encode_tag_operand("tag8_index", operands[0], labels))
        out.extend(encode_tag_operand("tag32", operands[1], labels))
        out.extend(encode_tag_operand("tag32", operands[2], labels))
        count = parse_int_token(operands[3], labels) & 0xFFFF
        strings = string_list_to_bytes(operands[4])
        if count != len(strings):
            raise ValueError(
                f"string count mismatch for {spec['mnemonic']}: declared {count}, actual {len(strings)}"
            )
        out.extend(count.to_bytes(2, "big"))
        for item in strings:
            out.extend(item)
            out.append(0)
        return bytes(out)

    if schema == "tag16_tag32_pairs":
        if len(operands) < 2:
            raise ValueError(f"{spec['mnemonic']} expects at least 2 operands")
        out.extend(encode_tag_operand("tag8_index", operands[0], labels))
        count = parse_int_token(operands[1], labels) & 0xFFFF
        pair_operands = operands[2:]
        if len(pair_operands) != count * 2:
            raise ValueError(
                f"pair count mismatch for {spec['mnemonic']}: declared {count}, actual {len(pair_operands) // 2}"
            )
        out.extend(count.to_bytes(2, "big"))
        for index in range(0, len(pair_operands), 2):
            out.extend(encode_tag_operand("tag16", pair_operands[index], labels))
            out.extend(encode_tag_operand("tag32", pair_operands[index + 1], labels))
        return bytes(out)

    if schema == "counted_cstrings":
        if len(operands) != 3:
            raise ValueError(f"{spec['mnemonic']} expects 3 operands")
        count = parse_int_token(operands[0], labels) & 0xFF
        flag = parse_int_token(operands[1], labels) & 0xFF
        strings = string_list_to_bytes(operands[2])
        if count != len(strings):
            raise ValueError(
                f"string count mismatch for {spec['mnemonic']}: declared {count}, actual {len(strings)}"
            )
        out.append(count)
        out.append(flag)
        for item in strings:
            out.extend(item)
            out.append(0)
        return bytes(out)

    if len(operands) != len(operand_kinds):
        raise ValueError(f"{spec['mnemonic']} expects {len(operand_kinds)} operands, got {len(operands)}")

    for kind, token in zip(operand_kinds, operands):
        if kind in TOKEN_PREFIX_BY_KIND:
            out.extend(encode_tag_operand(kind, token, labels))
        elif kind == "raw8":
            out.append(parse_int_token(token, labels) & 0xFF)
        elif kind == "raw16":
            out.extend((parse_int_token(token, labels) & 0xFFFF).to_bytes(2, "big"))
        elif kind == "raw_be32":
            out.extend((parse_int_token(token, labels) & 0xFFFFFFFF).to_bytes(4, "big"))
        elif kind == "raw_cstring":
            out.extend(string_literal_to_bytes(token))
            out.append(0)
        else:
            raise ValueError(f"unsupported operand kind in encoder: {kind}")
    return bytes(out)

def decode_instruction(
    data: bytes,
    offset: int,
    spec_by_opcode: dict[int, dict[str, object]],
) -> tuple[DecodedInstruction, int]:
    if offset + 2 > len(data):
        raise ValueError(f"truncated opcode at offset {offset:#x}")
    opcode = int.from_bytes(data[offset : offset + 2], "big")
    spec = spec_by_opcode.get(opcode)
    if spec is None:
        raise ValueError(f"unknown opcode {opcode:#06x} at offset {offset:#x}")

    schema = cast(str, spec["schema"])
    operand_kinds = cast(list[str], spec["operand_kinds"])
    cursor = offset + 2
    operands: list[str] = []

    if schema == "text0":
        token, size = decode_text_payload(data, cursor, 0xAA)
        operands.append(token)
        cursor += size
    elif schema == "text1":
        token, size = decode_text_payload(data, cursor, 0xAB)
        operands.append(token)
        cursor += size
    elif schema == "expr_blob":
        blob_len = int.from_bytes(data[cursor : cursor + 2], "big")
        cursor += 2
        raw32 = int.from_bytes(data[cursor : cursor + 4], "big")
        cursor += 4
        blob = data[cursor : cursor + blob_len]
        cursor += blob_len
        operands.extend([format_hex(blob_len, 4), format_hex(raw32, 8), expr_blob_to_token(blob)])
    elif schema == "counted_flags":
        count = data[cursor]
        cursor += 1
        flags = data[cursor : cursor + count]
        cursor += count
        operands.extend([format_hex(count, 2), blob_to_token(flags)])
    elif schema == "counted_flags_tail3":
        count = data[cursor]
        cursor += 1
        flags = data[cursor : cursor + count]
        cursor += count
        tail = data[cursor : cursor + 3]
        cursor += 3
        operands.append(format_hex(count, 2))
        operands.append(blob_to_token(flags))
        operands.extend(format_hex(value, 2) for value in tail)
    elif schema == "counted_cstrings16":
        token, size = decode_tag_operand("tag8_index", data, cursor)
        operands.append(token)
        cursor += size
        token, size = decode_tag_operand("tag32", data, cursor)
        operands.append(token)
        cursor += size
        token, size = decode_tag_operand("tag32", data, cursor)
        operands.append(token)
        cursor += size
        count = int.from_bytes(data[cursor : cursor + 2], "big")
        cursor += 2
        strings, size = decode_string_array(data, cursor, count)
        cursor += size
        operands.append(format_hex(count, 4))
        operands.append("[" + ", ".join(strings) + "]")
    elif schema == "tag16_tag32_pairs":
        token, size = decode_tag_operand("tag8_index", data, cursor)
        operands.append(token)
        cursor += size
        count = int.from_bytes(data[cursor : cursor + 2], "big")
        cursor += 2
        operands.append(format_hex(count, 4))
        for _ in range(count):
            token, size = decode_tag_operand("tag16", data, cursor)
            operands.append(token)
            cursor += size
            token, size = decode_tag_operand("tag32", data, cursor)
            operands.append(token)
            cursor += size
    elif schema == "counted_cstrings":
        strings, count, flag, size = decode_counted_cstrings(data, cursor)
        cursor += size
        operands.append(format_hex(count, 2))
        operands.append(format_hex(flag, 2))
        operands.append("[" + ", ".join(strings) + "]")
    else:
        for kind in operand_kinds:
            if kind in TOKEN_PREFIX_BY_KIND:
                token, size = decode_tag_operand(kind, data, cursor)
            elif kind == "raw8":
                token, size = format_hex(data[cursor], 2), 1
            elif kind == "raw16":
                token, size = format_hex(int.from_bytes(data[cursor : cursor + 2], "big"), 4), 2
            elif kind == "raw_be32":
                token, size = format_hex(int.from_bytes(data[cursor : cursor + 4], "big"), 8), 4
            elif kind == "raw_cstring":
                token, size = decode_cstring(data, cursor)
            else:
                raise ValueError(f"unsupported operand kind in decoder: {kind}")
            operands.append(token)
            cursor += size

    return DecodedInstruction(offset=offset, opcode=opcode, spec=spec, operands=operands), cursor

@functools.lru_cache(maxsize=1)
def _build_spec_cached() -> dict[str, object]:
    return build_static_spec()

def build_spec() -> dict[str, object]:
    return copy.deepcopy(_build_spec_cached())

__all__ = [
    "DEFAULT_TEXT_ENCODING",
    "DecodedInstruction",
    "blob_to_token",
    "build_spec",
    "build_spec_indexes",
    "bytes_to_string_literal",
    "decode_instruction",
    "decode_text_payload",
    "encode_instruction",
    "encode_text_payload",
    "format_hex",
    "get_text_encoding",
    "get_decoded_script_target_operand_indexes",
    "measure_instruction",
    "normalize_encoding",
    "parse_blob_token",
    "parse_int_token",
    "resolve_asm_script_targets",
    "resolve_text_encoding",
    "set_text_encoding",
    "split_top_level",
    "string_literal_to_bytes",
]