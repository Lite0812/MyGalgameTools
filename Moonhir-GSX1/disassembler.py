from __future__ import annotations

import argparse
import bisect
import json
import re
import struct
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from opcodelist import COMMANDS, VALUE_TYPES, CommandDef, ValueTypeDef


HEADER_SIZE = 40
TOKEN_SIZE = 8
LABEL_ENTRY_SIZE = 16
REF_ENTRY_SIZE = 8
COMMAND_NAME_TEXT = {
    cmd_id: json.dumps(definition.name, ensure_ascii=False)
    for cmd_id, definition in COMMANDS.items()
}
COMMAND_NAME_COUNTS: dict[str, int] = {}
for _definition in COMMANDS.values():
    COMMAND_NAME_COUNTS[_definition.name] = COMMAND_NAME_COUNTS.get(_definition.name, 0) + 1


@dataclass(frozen=True, slots=True)
class GsxHeader:
    version: int
    string_offset: int
    string_size: int
    label_offset: int
    label_size: int
    ref_offset: int
    ref_size: int
    token_offset: int
    token_size: int


@dataclass(frozen=True, slots=True)
class StringEntry:
    offset: int
    raw: bytes
    terminated: bool


@dataclass(frozen=True, slots=True)
class StringIndex:
    entries: list[StringEntry]
    starts: list[int]

    @classmethod
    def build(cls, entries: list[StringEntry]) -> "StringIndex":
        return cls(entries, [entry.offset for entry in entries])

    def find(self, offset: int) -> StringEntry | None:
        pos = bisect.bisect_right(self.starts, offset) - 1
        if pos < 0:
            return None
        entry = self.entries[pos]
        if entry.offset <= offset < entry.offset + len(entry.raw):
            return entry
        return None


@dataclass(frozen=True, slots=True)
class LabelEntry:
    index: int
    hash_next: int
    name_offset: int
    name_len: int
    token_offset: int
    symbol: str


@dataclass(frozen=True, slots=True)
class RefEntry:
    index: int
    kind_or_index: int
    token_offset: int


REF_KIND_NAMES = {
    1: "If2",
    2: "Else",
    4: "EndIf",
    16: "Switch",
    32: "Case",
    64: "EndSwitch",
}

VALUE_DEFAULT_FIELDS = {
    0x8000: (0x00, 0xCC),
    0x8001: (0x00, 0xCC),
    0x8002: (0x01, 0xCC),
    0x8010: (0x00, 0x00),
    0x8011: (0x00, 0x00),
    0x8012: (0x00, 0x00),
    0x8110: (0x00, 0x00),
    0x8111: (0x00, 0x00),
    0x8112: (0x00, 0x00),
}


def ref_serial(kind_or_index: int) -> int:
    return kind_or_index & 0xFFFF


def ref_kind_flags(kind_or_index: int) -> int:
    return (kind_or_index >> 16) & 0xFFFF


def ref_kind_text(kind_or_index: int) -> str:
    flags = ref_kind_flags(kind_or_index)
    return REF_KIND_NAMES.get(flags, f"0x{flags:04X}")


@dataclass(frozen=True, slots=True)
class Token:
    index: int
    offset: int
    kind: int
    argc_or_state: int
    opcode_or_type: int
    value: int


@dataclass(frozen=True, slots=True)
class SourceExpr:
    text: str
    tokens: tuple[Token, ...]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def i32_from_u32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def parse_header(data: bytes, path: Path) -> GsxHeader:
    if len(data) < HEADER_SIZE:
        raise ValueError(f"{path}: file is too small for a GSX1 header")
    if data[:4] != b"GSX1":
        raise ValueError(f"{path}: magic is not GSX1")
    fields = struct.unpack_from("<9I", data, 4)
    header = GsxHeader(
        version=fields[0],
        string_offset=fields[1],
        string_size=fields[2],
        label_offset=fields[3],
        label_size=fields[4],
        ref_offset=fields[5],
        ref_size=fields[6],
        token_offset=fields[7],
        token_size=fields[8],
    )
    validate_header_ranges(data, header, path)
    return header


def validate_header_ranges(data: bytes, header: GsxHeader, path: Path) -> None:
    checks = [
        ("string pool", header.string_offset, header.string_size, 1),
        ("label table", header.label_offset, header.label_size, LABEL_ENTRY_SIZE),
        ("ref table", header.ref_offset, header.ref_size, REF_ENTRY_SIZE),
        ("token stream", header.token_offset, header.token_size, TOKEN_SIZE),
    ]
    for name, offset, size, align in checks:
        if offset > len(data) or size > len(data) - offset:
            raise ValueError(
                f"{path}: {name} range is outside the file "
                f"(offset=0x{offset:08X}, size=0x{size:08X})"
            )
        if size % align:
            raise ValueError(f"{path}: {name} size is not aligned to {align}")


def parse_string_pool(data: bytes, header: GsxHeader) -> list[StringEntry]:
    pool = data[header.string_offset : header.string_offset + header.string_size]
    entries: list[StringEntry] = []
    offset = 0
    while offset < len(pool):
        end = pool.find(b"\x00", offset)
        if end < 0:
            entries.append(StringEntry(offset, pool[offset:], False))
            break
        entries.append(StringEntry(offset, pool[offset : end + 1], True))
        offset = end + 1
    return entries


def parse_tokens(data: bytes, header: GsxHeader) -> list[Token]:
    tokens: list[Token] = []
    stream = data[header.token_offset : header.token_offset + header.token_size]
    for index, (kind, argc_or_state, opcode_or_type, value) in enumerate(struct.iter_unpack("<BBhI", stream)):
        tokens.append(Token(index, index * TOKEN_SIZE, kind, argc_or_state, opcode_or_type, value))
    return tokens


def decode_pool_text(raw: bytes, encoding: str) -> str:
    if raw.endswith(b"\x00"):
        raw = raw[:-1]
    return raw.decode(encoding, errors="replace")


def parse_display_text(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, errors="replace")


def sanitize_symbol(text: str, fallback: str, used: set[str]) -> str:
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[^0-9A-Za-z_]", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text or text[0].isdigit():
        text = fallback
    base = text
    suffix = 1
    while text in used:
        text = f"{base}_{suffix}"
        suffix += 1
    used.add(text)
    return text


def find_string_entry(entries: list[StringEntry], offset: int) -> StringEntry | None:
    return StringIndex.build(entries).find(offset)


def parse_labels(
    data: bytes,
    header: GsxHeader,
    strings: list[StringEntry],
    encoding: str,
    string_index: StringIndex | None = None,
) -> list[LabelEntry]:
    labels: list[LabelEntry] = []
    used: set[str] = set()
    indexer = string_index or StringIndex.build(strings)
    for index in range(header.label_size // LABEL_ENTRY_SIZE):
        off = header.label_offset + index * LABEL_ENTRY_SIZE
        hash_next, name_offset_u, name_len_u, token_offset_u = struct.unpack_from("<4I", data, off)
        name_offset = i32_from_u32(name_offset_u)
        token_offset = i32_from_u32(token_offset_u)
        name_len = i32_from_u32(name_len_u)
        fallback = f"label_{index:04d}"
        if name_offset >= 0:
            entry = indexer.find(name_offset)
            if entry and entry.offset == name_offset:
                text = decode_pool_text(entry.raw, encoding)
                symbol = sanitize_symbol(text, fallback, used)
            else:
                symbol = sanitize_symbol("", fallback, used)
        else:
            symbol = sanitize_symbol("", fallback, used)
        labels.append(LabelEntry(index, hash_next, name_offset, name_len, token_offset, symbol))
    return labels


def parse_refs(data: bytes, header: GsxHeader) -> list[RefEntry]:
    refs: list[RefEntry] = []
    for index in range(header.ref_size // REF_ENTRY_SIZE):
        off = header.ref_offset + index * REF_ENTRY_SIZE
        kind_or_index, token_offset_u = struct.unpack_from("<2I", data, off)
        refs.append(RefEntry(index, kind_or_index, i32_from_u32(token_offset_u)))
    return refs


def bytes_literal(raw: bytes, encoding: str) -> str:
    text = raw.decode(encoding, errors="surrogateescape")
    out: list[str] = []
    for ch in text:
        codepoint = ord(ch)
        if 0xDC80 <= codepoint <= 0xDCFF:
            out.append(f"{{{{{codepoint - 0xDC00:02X}}}}}")
            continue
        encoded = ch.encode(encoding, errors="surrogateescape")
        if (
            unicodedata.category(ch)[0] == "C"
            or (len(encoded) == 1 and (encoded[0] < 0x20 or encoded[0] == 0x7F))
            or ch in {'"', "{", "}"}
        ):
            out.extend(f"{{{{{byte:02X}}}}}" for byte in encoded)
        else:
            out.append(ch)
    return "".join(out)


def quoted_bytes(raw: bytes, encoding: str) -> str:
    return '"' + bytes_literal(raw, encoding) + '"'


def quoted_text(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def display_text(text: str) -> str:
    return quoted_text(comment_text(text, 60))


def comment_text(text: str, limit: int = 80) -> str:
    text = text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def str_label(offset: int) -> str:
    return f"str_{offset:08X}"


def tok_label(offset: int) -> str:
    return f"tok_{offset:08X}"


def collect_string_refs(tokens: Iterable[Token], labels: Iterable[LabelEntry], pool_size: int) -> set[int]:
    refs: set[int] = set()
    for token in tokens:
        if (token.opcode_or_type & 0xFFFF) == 0x8002 and 0 <= token.value < pool_size:
            refs.add(token.value)
    for label in labels:
        if 0 <= label.name_offset < pool_size:
            refs.add(label.name_offset)
    return refs


def string_ref_text(
    offset: int,
    entries: list[StringEntry],
    pool_size: int,
    string_index: StringIndex | None = None,
) -> str:
    if not (0 <= offset < pool_size):
        return f"0x{offset:08X}"
    indexer = string_index or StringIndex.build(entries)
    entry = indexer.find(offset)
    if not entry:
        return f"{str_label(offset)}"
    if entry.offset == offset:
        return str_label(offset)
    return f"{str_label(offset)}"


def string_preview(
    offset: int,
    entries: list[StringEntry],
    pool_size: int,
    encoding: str,
    string_index: StringIndex,
) -> str | None:
    if not (0 <= offset < pool_size):
        return None
    entry = string_index.find(offset)
    if entry is None:
        return None
    start = offset - entry.offset
    raw = entry.raw[start:]
    if raw.endswith(b"\x00"):
        raw = raw[:-1]
    return raw.decode(encoding, errors="replace")


def token_operand_text(
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
) -> str:
    if token.opcode_or_type >= 0:
        command = COMMANDS.get(token.opcode_or_type)
        return command.name if command else f"CMD_{token.opcode_or_type}"
    type_u = token.opcode_or_type & 0xFFFF
    if type_u == 0x8002:
        preview = string_preview(token.value, strings, header.string_size, encoding, string_index)
        if preview is not None:
            return quoted_text(comment_text(preview, 40))
        return string_ref_text(token.value, strings, header.string_size, string_index)
    if type_u in {0x8010, 0x8011, 0x8012}:
        return f"G[{token.value}]"
    if type_u in {0x8110, 0x8111, 0x8112}:
        return f"L[{token.value}]"
    return str(i32_from_u32(token.value))


def code_meta_line(code: str, meta: str) -> str:
    code = code.rstrip()
    if len(code) < 56:
        return f"    {code:<56} # {meta}"
    return f"    {code}  # {meta}"


def code_line(code: str) -> str:
    return f"    {code.rstrip()}"


def hidden_meta_line(meta: str) -> str:
    return f"    # {meta}"


def compact_string_assignment(ref: str, entry: StringEntry, encoding: str) -> str:
    raw = entry.raw[:-1] if entry.terminated else entry.raw
    suffix = "" if entry.terminated else " terminated=false"
    return f"{ref} = {quoted_bytes(raw, encoding)}{suffix}"


def value_display_text(
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
) -> str:
    type_u = token.opcode_or_type & 0xFFFF
    if type_u == 0x8002:
        return string_ref_text(token.value, strings, header.string_size, string_index)
    if type_u in {0x8010, 0x8011, 0x8012}:
        return f"G[{token.value}]"
    if type_u in {0x8110, 0x8111, 0x8112}:
        return f"L[{token.value}]"
    signed = i32_from_u32(token.value)
    if -999999 <= signed <= 999999:
        return str(signed)
    return f"0x{token.value:08X}"


def source_value_display_text(
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
) -> str | None:
    type_u = token.opcode_or_type & 0xFFFF
    if type_u == 0x8002:
        entry = string_index.find(token.value) if 0 <= token.value < header.string_size else None
        if entry is None or entry.offset != token.value or not entry.terminated:
            return None
        return quoted_bytes(entry.raw[:-1], encoding)
    if type_u in {0x8010, 0x8011, 0x8012}:
        return f"G[{token.value}]"
    if type_u in {0x8110, 0x8111, 0x8112}:
        return f"L[{token.value}]"
    if type_u in {0x8000, 0x8001}:
        signed = i32_from_u32(token.value)
        if -999999 <= signed <= 999999:
            return str(signed)
        return f"0x{token.value:08X}"
    return None


def command_display_text(
    index: int,
    tokens: list[Token],
    token: Token,
    command: CommandDef,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
) -> str:
    name = command.name
    if name == ";":
        return "stmt_end"
    if name == ",":
        return "comma"
    if name in {"{", "}"}:
        return name
    if name in {"(", ")", "[", "]"}:
        return f"operator({quoted_text(name)})"
    display_name = name[:-1] if name.endswith("(") else name
    start = max(0, index - token.argc_or_state)
    args = [
        token_operand_text(arg, strings, header, encoding, string_index)
        for arg in tokens[start:index]
    ]
    if any(ch.isalpha() for ch in display_name) or display_name.startswith(("%", "\\")):
        return f"{display_name}({', '.join(args)})"
    if args:
        return f"operator({quoted_text(display_name)}, {', '.join(args)})"
    return f"operator({quoted_text(display_name)})"


BINARY_OPERATORS = {"*", "/", "%", "+", "-", "<<", ">>", "<=", "<", ">=", ">", "==", "!=", "&", "^", "|", "&&", "||"}
UNARY_OPERATORS = {"~", "!", "+", "-", "@@", "@"}


def fallback_command_args(
    index: int,
    tokens: list[Token],
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
) -> list[str]:
    start = max(0, index - token.argc_or_state)
    return [
        token_operand_text(arg, strings, header, encoding, string_index)
        for arg in tokens[start:index]
    ]


def pop_expression_args(expr_stack: list[str], count: int) -> list[str] | None:
    if count <= 0:
        return []
    if len(expr_stack) < count:
        return None
    args = expr_stack[-count:]
    del expr_stack[-count:]
    return args


def command_expr_text(command: CommandDef, args: list[str]) -> str:
    name = command.name
    if name == ";":
        return ";"
    if name == "{":
        return "{"
    if name == "}":
        return "}"
    if name == ",":
        return ", ".join(args) if args else "comma"
    if name == "[" and len(args) >= 2:
        return f"{args[-2]}[{args[-1]}]"
    if name in BINARY_OPERATORS and len(args) >= 2:
        return f"({args[-2]} {name} {args[-1]})"
    if name == "=" and len(args) >= 2:
        return f"{args[-2]} = {args[-1]}"
    if name in UNARY_OPERATORS and args:
        if name == "@":
            return f"@({args[-1]})"
        return f"({name}{args[-1]})"
    display_name = name[:-1] if name.endswith("(") else name
    if any(ch.isalpha() for ch in display_name) or display_name.startswith(("%", "\\")):
        return f"{display_name}({', '.join(args)})"
    if args:
        return f"operator({quoted_text(display_name)}, {', '.join(args)})"
    return f"operator({quoted_text(display_name)})"


def command_stack_display_text(
    index: int,
    tokens: list[Token],
    token: Token,
    command: CommandDef,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
    expr_stack: list[str],
) -> str:
    if command.name == ";":
        expr_stack.clear()
        return ";"
    consume_count = max(command.left_min, 0) + token.argc_or_state
    args = pop_expression_args(expr_stack, consume_count)
    if args is None:
        args = fallback_command_args(index, tokens, token, strings, header, encoding, string_index)
    display = command_expr_text(command, args)
    if command.push_count:
        expr_stack.append(display)
    return display


def token_comment(
    index: int,
    tokens: list[Token],
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
) -> str:
    type_u = token.opcode_or_type & 0xFFFF
    if token.opcode_or_type < 0 and type_u == 0x8002:
        preview = string_preview(token.value, strings, header.string_size, encoding, string_index)
        return "" if preview is None else f" ; {quoted_text(comment_text(preview))}"
    if token.opcode_or_type >= 0:
        command = COMMANDS.get(token.opcode_or_type)
        if command is None:
            return ""
        if token.argc_or_state == 0:
            return ""
        if not any(ch.isalpha() for ch in command.name):
            return ""
        display_name = command.name[:-1] if command.name.endswith("(") else command.name
        start = max(0, index - token.argc_or_state)
        args = [
            token_operand_text(arg, strings, header, encoding, string_index)
            for arg in tokens[start:index]
        ]
        if len(args) > 8:
            args = args[:8] + ["..."]
        return f" ; {display_name}({', '.join(args)})"
    return ""


def compact_value_mnemonic(value_type: ValueTypeDef) -> str:
    if value_type.mnemonic == "VAL_RAW32":
        return "raw"
    if value_type.mnemonic == "VAL_STRREF":
        return "str"
    if value_type.mnemonic == "VAL_GLOBAL_SLOT_F32":
        return "global"
    if value_type.mnemonic == "VAL_LOCAL_SLOT_F32":
        return "local"
    return value_type.mnemonic


def format_value_token(
    token: Token,
    value_type: ValueTypeDef,
    strings: list[StringEntry],
    header: GsxHeader,
    string_index: StringIndex,
    *,
    include_label: bool = True,
    compact: bool = False,
) -> str:
    type_u = token.opcode_or_type & 0xFFFF
    fields: list[str] = []
    if not compact or VALUE_DEFAULT_FIELDS.get(type_u) != (token.kind, token.argc_or_state):
        fields.extend([f"kind=0x{token.kind:02X}", f"argc=0x{token.argc_or_state:02X}"])
    mnemonic = compact_value_mnemonic(value_type) if compact else value_type.mnemonic
    prefix = f"{'val' if compact else '.value'} {mnemonic}"
    if include_label:
        prefix = f"{tok_label(token.offset)}: " + prefix
    if type_u == 0x8002:
        ref = string_ref_text(token.value, strings, header.string_size, string_index)
        if not compact:
            fields.append(f"value={ref}")
    elif type_u in {0x8010, 0x8011, 0x8012, 0x8110, 0x8111, 0x8112}:
        fields.append(str(token.value) if compact else f"slot={token.value}")
    else:
        signed = i32_from_u32(token.value)
        if compact and -999999 <= signed <= 999999:
            fields.append(str(signed))
        else:
            fields.append(f"u32=0x{token.value:08X}")
    return prefix + (" " + " ".join(fields) if fields else "")


def format_call_arg_token(
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    string_index: StringIndex,
) -> str:
    type_u = token.opcode_or_type & 0xFFFF
    default = VALUE_DEFAULT_FIELDS.get(type_u)
    if type_u == 0x8002:
        return "str"
    if default == (token.kind, token.argc_or_state):
        if type_u == 0x8000:
            return f"raw:{i32_from_u32(token.value)}"
        if type_u in {0x8010, 0x8011, 0x8012}:
            return f"global:{token.value}"
        if type_u in {0x8110, 0x8111, 0x8112}:
            return f"local:{token.value}"
    value_type = VALUE_TYPES.get(type_u)
    mnemonic = value_type.mnemonic if value_type is not None else f"0x{type_u:04X}"
    return f"{mnemonic}:0x{token.kind:02X}:0x{token.argc_or_state:02X}:0x{token.value:08X}"


def format_call_lines(
    args: list[Token],
    command_token: Token,
    command: CommandDef,
    strings: list[StringEntry],
    header: GsxHeader,
    string_index: StringIndex,
    encoding: str,
    command_values: bool,
) -> list[str]:
    name_part = COMMAND_NAME_TEXT[command.cmd_id]
    parts = ["call", name_part]
    if COMMAND_NAME_COUNTS.get(command.name, 0) != 1:
        parts.append(f"id={command.cmd_id}")
    parts.append(f"argc={command_token.argc_or_state}")
    if command_values:
        parts.append(f"value=0x{command_token.value:08X}")
    for index, arg in enumerate(args):
        value = format_call_arg_token(arg, strings, header, string_index)
        parts.append(f"arg{index}={value}")
        if value == "str" or value.startswith("str:"):
            entry = string_index.find(arg.value) if 0 <= arg.value < header.string_size else None
            if entry is not None and entry.offset == arg.value:
                parts.append(f"raw{index}={quoted_bytes(entry.raw[:-1] if entry.terminated else entry.raw, encoding)}")
                if not entry.terminated:
                    parts.append(f"terminated{index}=false")
    return [" ".join(parts)]


CONTROL_REF_ARG_COMMANDS = {"If2", "Else", "EndIf", "Switch", "Case", "EndSwitch", "Continue", "Break"}


def default_high_arg_fields(token: Token, command_name: str, index: int) -> tuple[int, int] | None:
    type_u = token.opcode_or_type & 0xFFFF
    if type_u == 0x8000:
        value = i32_from_u32(token.value)
        argc = 0x00 if value < 0 or (token.value & 0x80000000) else 0xCC
        if index == 0 and command_name in CONTROL_REF_ARG_COMMANDS | {"IPageStart", "IGblSelSet"}:
            argc = 0x00
        if index == 0 and command_name == "\\v" and value != 0:
            argc = 0x00
        return 0x00, argc
    return VALUE_DEFAULT_FIELDS.get(type_u)


def format_high_arg(
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    string_index: StringIndex,
    encoding: str,
    allow_exact_token: bool = False,
) -> str | None:
    type_u = token.opcode_or_type & 0xFFFF
    if type_u == 0x8000:
        value = i32_from_u32(token.value)
        return str(value) if -999999 <= value <= 999999 else f"0x{token.value:08X}"
    if type_u == 0x8002:
        entry = string_index.find(token.value) if 0 <= token.value < header.string_size else None
        if entry is not None and entry.offset == token.value and entry.terminated:
            return quoted_bytes(entry.raw[:-1], encoding)
        return None
    if type_u in {0x8010, 0x8011, 0x8012}:
        return f"G[{token.value}]"
    if type_u in {0x8110, 0x8111, 0x8112}:
        return f"L[{token.value}]"
    if allow_exact_token and type_u in VALUE_TYPES:
        return format_call_arg_token(token, strings, header, string_index)
    return None


def display_command_name(name: str) -> str:
    return ("\\" + name) if name.startswith("\\") else name


def exact_arg_note(index: int, token: Token, command_name: str) -> str | None:
    default = default_high_arg_fields(token, command_name, index)
    if default is None or default == (token.kind, token.argc_or_state):
        return None
    return f"arg{index}={token.kind:02X}:{token.argc_or_state:02X}"


def default_expr_value_fields(token: Token) -> tuple[int, int] | None:
    type_u = token.opcode_or_type & 0xFFFF
    if type_u == 0x8000:
        value = i32_from_u32(token.value)
        return 0x00, 0x00 if value < 0 or (token.value & 0x80000000) else 0xCC
    return VALUE_DEFAULT_FIELDS.get(type_u)


def exact_val_note(index: int, token: Token) -> str | None:
    default = default_expr_value_fields(token)
    if default is None or default == (token.kind, token.argc_or_state):
        return None
    return f"val{index}={token.kind:02X}:{token.argc_or_state:02X}"


def compress_exact_notes(notes: list[str]) -> list[str]:
    grouped: dict[tuple[str, str], list[int]] = {}
    order: list[tuple[str, str]] = []
    for note in notes:
        key, value = note.split("=", 1)
        prefix = ""
        index_text = ""
        if key.startswith("arg") and key[len("arg") :].isdigit():
            prefix = "arg"
            index_text = key[len("arg") :]
        elif key.startswith("val") and key[len("val") :].isdigit():
            prefix = "val"
            index_text = key[len("val") :]
        else:
            continue
        group_key = (prefix, value)
        if group_key not in grouped:
            grouped[group_key] = []
            order.append(group_key)
        grouped[group_key].append(int(index_text))
    compressed: list[str] = []
    for prefix, value in order:
        indices = grouped[(prefix, value)]
        if len(indices) == 1:
            compressed.append(f"{prefix}{indices[0]}={value}")
        else:
            plural = "args" if prefix == "arg" else "vals"
            compressed.append(f"{plural}[{','.join(str(index) for index in indices)}]={value}")
    return compressed


def add_exact_comment(line: str, notes: list[str]) -> str:
    compressed = compress_exact_notes(notes)
    return line + (" ; exact " + " ".join(compressed) if compressed else "")


def format_high_call_line(
    args: list[Token],
    command: CommandDef,
    strings: list[StringEntry],
    header: GsxHeader,
    string_index: StringIndex,
    encoding: str,
    include_exact_comment: bool = False,
) -> str | None:
    formatted = [format_high_arg(arg, strings, header, string_index, encoding, allow_exact_token=False) for arg in args]
    if any(arg is None for arg in formatted):
        return None
    display_name = display_command_name(command.name)
    arg_text = ", ".join(arg or "" for arg in formatted)
    if display_name.endswith("("):
        line = f"{display_name}{arg_text})"
    else:
        line = f"{display_name}({arg_text})"
    if include_exact_comment:
        notes = [note for index, arg in enumerate(args) if (note := exact_arg_note(index, arg, command.name))]
        line = add_exact_comment(line, notes)
    return line


def text_arg(token: Token, header: GsxHeader, string_index: StringIndex, encoding: str) -> str | None:
    if (token.opcode_or_type & 0xFFFF) != 0x8002:
        return None
    entry = string_index.find(token.value) if 0 <= token.value < header.string_size else None
    if entry is None or entry.offset != token.value or not entry.terminated:
        return None
    return parse_display_text(entry.raw[:-1], encoding)


def format_text_line(text: str) -> str:
    return "text " + quoted_text(text)


def format_command_token(
    token: Token,
    command: CommandDef,
    *,
    include_label: bool = True,
    compact: bool = False,
    command_values: bool = True,
) -> str:
    prefix = f"{tok_label(token.offset)}: " if include_label else ""
    value_part = f" value=0x{token.value:08X}" if command_values else ""
    if compact and command.cmd_id == 3 and command.name == ";":
        return f"{prefix}end{value_part}"
    if compact and command.name == ";":
        return f"{prefix}end{value_part}"
    if compact and COMMAND_NAME_COUNTS.get(command.name, 0) == 1:
        return f"{prefix}cmd {COMMAND_NAME_TEXT[command.cmd_id]} argc={token.argc_or_state}{value_part}"
    if compact:
        return f"{prefix}cmd id={command.cmd_id} name={COMMAND_NAME_TEXT[command.cmd_id]} argc={token.argc_or_state}{value_part}"
    return (
        f"{prefix}.command id={command.cmd_id} "
        f"name={COMMAND_NAME_TEXT[command.cmd_id]} argc={token.argc_or_state} "
        f"value=0x{token.value:08X}"
    )


def format_unknown_token(token: Token, *, include_label: bool = True) -> str:
    prefix = f"{tok_label(token.offset)}: " if include_label else ""
    return (
        f"{prefix}.unknown_token "
        f"kind=0x{token.kind:02X} argc=0x{token.argc_or_state:02X} "
        f"opcode_or_type=0x{token.opcode_or_type & 0xFFFF:04X} "
        f"value=0x{token.value:08X}"
    )


def format_token(
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    allow_unknown: bool,
    string_index: StringIndex,
    *,
    include_label: bool = True,
    compact: bool = False,
    command_values: bool = True,
) -> str:
    if token.opcode_or_type >= 0:
        command = COMMANDS.get(token.opcode_or_type)
        if command is None or token.kind != 0x08:
            if allow_unknown:
                return format_unknown_token(token, include_label=include_label)
            raise ValueError(
                f"unknown command token at {tok_label(token.offset)}: "
                f"kind=0x{token.kind:02X}, cmd_id={token.opcode_or_type}"
            )
        return format_command_token(token, command, include_label=include_label, compact=compact, command_values=command_values)

    type_u = token.opcode_or_type & 0xFFFF
    value_type = VALUE_TYPES.get(type_u)
    if value_type is None:
        if allow_unknown:
            return format_unknown_token(token, include_label=include_label)
        raise ValueError(
            f"unknown value token at {tok_label(token.offset)}: "
            f"kind=0x{token.kind:02X}, type=0x{type_u:04X}"
        )
    return format_value_token(token, value_type, strings, header, string_index, include_label=include_label, compact=compact)


def raw_string_fields(entry: StringEntry, encoding: str) -> str:
    raw = entry.raw[:-1] if entry.terminated else entry.raw
    suffix = "" if entry.terminated else " terminated=false"
    return f"raw={quoted_bytes(raw, encoding)}{suffix}"


def maybe_string_fields(
    offset: int,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
    emitted_strings: set[int],
) -> str:
    if not (0 <= offset < header.string_size):
        return ""
    entry = string_index.find(offset)
    if entry is None or entry.offset in emitted_strings:
        return ""
    emitted_strings.add(entry.offset)
    return " " + raw_string_fields(entry, encoding)


def format_value_code_token(
    token: Token,
    value_type: ValueTypeDef,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
    emitted_strings: set[int],
) -> str:
    type_u = token.opcode_or_type & 0xFFFF
    display = value_display_text(token, strings, header, encoding, string_index)
    meta = f"@v {value_type.mnemonic} off=0x{token.offset:08X} k=0x{token.kind:02X} a=0x{token.argc_or_state:02X}"
    if type_u == 0x8002:
        ref = string_ref_text(token.value, strings, header.string_size, string_index)
        entry = string_index.find(token.value) if 0 <= token.value < header.string_size else None
        if entry is not None and entry.offset not in emitted_strings:
            display = compact_string_assignment(ref, entry, encoding)
            emitted_strings.add(entry.offset)
            return code_meta_line(display, f"{meta} str={ref}")
        else:
            display = ref
        return hidden_meta_line(f"{meta} str={ref}")
    if type_u in {0x8010, 0x8011, 0x8012, 0x8110, 0x8111, 0x8112}:
        return hidden_meta_line(f"{meta} slot={token.value}")
    return hidden_meta_line(f"{meta} u32=0x{token.value:08X} s32={i32_from_u32(token.value)}")


def format_command_code_token(
    index: int,
    tokens: list[Token],
    token: Token,
    command: CommandDef,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
    expr_stack: list[str],
) -> str:
    display = command_stack_display_text(index, tokens, token, command, strings, header, encoding, string_index, expr_stack)
    meta = f"@c off=0x{token.offset:08X} id={command.cmd_id} a={token.argc_or_state} v=0x{token.value:08X}"
    if command.name == ";" or (command.push_count and command.name != "="):
        return hidden_meta_line(meta)
    return code_meta_line(display, meta)


def format_unknown_code_token(token: Token) -> str:
    return code_meta_line(
        "unknown_token",
        f"@u off=0x{token.offset:08X} k=0x{token.kind:02X} a=0x{token.argc_or_state:02X} "
        f"op=0x{token.opcode_or_type & 0xFFFF:04X} v=0x{token.value:08X}",
    )


def format_clean_value_token(
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
    emitted_strings: set[int],
) -> str | None:
    type_u = token.opcode_or_type & 0xFFFF
    if type_u != 0x8002:
        return None
    ref = string_ref_text(token.value, strings, header.string_size, string_index)
    entry = string_index.find(token.value) if 0 <= token.value < header.string_size else None
    if entry is None or entry.offset in emitted_strings:
        return None
    emitted_strings.add(entry.offset)
    return code_line(compact_string_assignment(ref, entry, encoding))


def format_clean_command_token(
    index: int,
    tokens: list[Token],
    token: Token,
    command: CommandDef,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    string_index: StringIndex,
    expr_stack: list[str],
) -> str | None:
    display = command_stack_display_text(index, tokens, token, command, strings, header, encoding, string_index, expr_stack)
    if command.name == ";":
        return None
    if command.push_count and command.name != "=":
        return None
    return code_line(display)


def format_clean_token(
    index: int,
    tokens: list[Token],
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    allow_unknown: bool,
    string_index: StringIndex,
    emitted_strings: set[int],
    expr_stack: list[str],
) -> str | None:
    if token.opcode_or_type >= 0:
        command = COMMANDS.get(token.opcode_or_type)
        if command is None or token.kind != 0x08:
            if allow_unknown:
                return hidden_meta_line(
                    f"@u off=0x{token.offset:08X} k=0x{token.kind:02X} a=0x{token.argc_or_state:02X} "
                    f"op=0x{token.opcode_or_type & 0xFFFF:04X} v=0x{token.value:08X}"
                )
            raise ValueError(
                f"unknown command token at {tok_label(token.offset)}: "
                f"kind=0x{token.kind:02X}, cmd_id={token.opcode_or_type}"
            )
        return format_clean_command_token(index, tokens, token, command, strings, header, encoding, string_index, expr_stack)

    type_u = token.opcode_or_type & 0xFFFF
    if type_u not in VALUE_TYPES:
        if allow_unknown:
            return hidden_meta_line(
                f"@u off=0x{token.offset:08X} k=0x{token.kind:02X} a=0x{token.argc_or_state:02X} "
                f"op=0x{token.opcode_or_type & 0xFFFF:04X} v=0x{token.value:08X}"
            )
        raise ValueError(
            f"unknown value token at {tok_label(token.offset)}: "
            f"kind=0x{token.kind:02X}, type=0x{type_u:04X}"
        )
    expr_stack.append(value_display_text(token, strings, header, encoding, string_index))
    return format_clean_value_token(token, strings, header, encoding, string_index, emitted_strings)


def format_code_token(
    index: int,
    tokens: list[Token],
    token: Token,
    strings: list[StringEntry],
    header: GsxHeader,
    encoding: str,
    allow_unknown: bool,
    string_index: StringIndex,
    emitted_strings: set[int],
    expr_stack: list[str],
) -> str:
    if token.opcode_or_type >= 0:
        command = COMMANDS.get(token.opcode_or_type)
        if command is None or token.kind != 0x08:
            if allow_unknown:
                return format_unknown_code_token(token)
            raise ValueError(
                f"unknown command token at {tok_label(token.offset)}: "
                f"kind=0x{token.kind:02X}, cmd_id={token.opcode_or_type}"
            )
        return format_command_code_token(index, tokens, token, command, strings, header, encoding, string_index, expr_stack)

    type_u = token.opcode_or_type & 0xFFFF
    value_type = VALUE_TYPES.get(type_u)
    if value_type is None:
        if allow_unknown:
            return format_unknown_code_token(token)
        raise ValueError(
            f"unknown value token at {tok_label(token.offset)}: "
            f"kind=0x{token.kind:02X}, type=0x{type_u:04X}"
        )
    display = value_display_text(token, strings, header, encoding, string_index)
    expr_stack.append(display)
    return format_value_code_token(token, value_type, strings, header, encoding, string_index, emitted_strings)


def segment_line(name: str, offset: int, size: int) -> str:
    return f".segment {name} offset=0x{offset:08X} size=0x{size:08X}"


def emit_raw_disassembly(path: Path, data: bytes, encoding: str, allow_unknown: bool) -> str:
    header = parse_header(data, path)
    strings = parse_string_pool(data, header)
    string_index = StringIndex.build(strings)
    tokens = parse_tokens(data, header)
    labels = parse_labels(data, header, strings, encoding, string_index)
    refs = parse_refs(data, header)

    labels_by_token: dict[int, list[LabelEntry]] = {}
    for label in labels:
        if 0 <= label.token_offset < header.token_size and label.token_offset % TOKEN_SIZE == 0:
            labels_by_token.setdefault(label.token_offset, []).append(label)

    string_refs = collect_string_refs(tokens, labels, header.string_size)
    string_starts = {entry.offset for entry in strings}
    string_aliases = sorted(offset for offset in string_refs if offset not in string_starts)

    lines: list[str] = []
    lines.append('.format "kyoupri-gsx1"')
    lines.append(f'.encoding {quoted_text(encoding)}')
    lines.append(f".file_size 0x{len(data):08X}")
    lines.append(f".version 0x{header.version:08X}")
    lines.append(segment_line("strings", header.string_offset, header.string_size))
    lines.append(segment_line("labels", header.label_offset, header.label_size))
    lines.append(segment_line("refs", header.ref_offset, header.ref_size))
    lines.append(segment_line("tokens", header.token_offset, header.token_size))
    lines.append("")
    lines.append(".section strings")
    for entry in strings:
        raw = entry.raw[:-1] if entry.terminated else entry.raw
        suffix = "" if entry.terminated else " terminated=false"
        lines.append(f"{str_label(entry.offset)}: .string {quoted_bytes(raw, encoding)}{suffix}")
    for offset in string_aliases:
        base = string_index.find(offset)
        if base is None:
            lines.append(f"{str_label(offset)}: .str_alias base=<invalid> add=0")
        else:
            lines.append(f"{str_label(offset)}: .str_alias base={str_label(base.offset)} add={offset - base.offset}")

    lines.append("")
    lines.append(".section labels")
    for label in labels:
        name_ref = "none"
        if label.name_offset >= 0:
            name_ref = string_ref_text(label.name_offset, strings, header.string_size, string_index)
        target = "none"
        if label.token_offset >= 0:
            target = tok_label(label.token_offset)
        lines.append(
            f".label_entry symbol={label.symbol} index={label.index} name={name_ref} "
            f"name_offset={label.name_offset} name_len={label.name_len} "
            f"target={target} hash_next=0x{label.hash_next:08X}"
        )

    lines.append("")
    lines.append(".section refs")
    for ref in refs:
        target = "none" if ref.token_offset < 0 else tok_label(ref.token_offset)
        lines.append(
            f".ref index={ref.index} kind_or_index=0x{ref.kind_or_index:08X} "
            f"target={target} token_offset={ref.token_offset}"
        )

    lines.append("")
    lines.append(".section code")
    for index, token in enumerate(tokens):
        for label in labels_by_token.get(token.offset, []):
            lines.append("")
            lines.append(f"{label.symbol}:")
        line = format_token(token, strings, header, allow_unknown, string_index)
        line += token_comment(index, tokens, token, strings, header, encoding, string_index)
        lines.append(line)

    lines.append("")
    return "\n".join(lines)


def emit_source_disassembly(path: Path, data: bytes, encoding: str, allow_unknown: bool, command_values: bool = False) -> str:
    header = parse_header(data, path)
    strings = parse_string_pool(data, header)
    string_index = StringIndex.build(strings)
    tokens = parse_tokens(data, header)
    labels = parse_labels(data, header, strings, encoding, string_index)
    refs = parse_refs(data, header)

    labels_by_token: dict[int, list[LabelEntry]] = {}
    for label in labels:
        if 0 <= label.token_offset < header.token_size and label.token_offset % TOKEN_SIZE == 0:
            labels_by_token.setdefault(label.token_offset, []).append(label)

    lines: list[str] = []
    lines.append('.format "kyoupri-gsx1-compact"')
    lines.append(f'.encoding {quoted_text(encoding)}')
    lines.append(f".version 0x{header.version:08X}")
    lines.append("")
    lines.append(".section code")

    inferable_strings: set[int] = set()
    for token in tokens:
        if (token.opcode_or_type & 0xFFFF) == 0x8002:
            entry = string_index.find(token.value)
            if entry is not None and entry.offset == token.value:
                inferable_strings.add(entry.offset)
    for label in labels:
        if label.name_offset >= 0:
            entry = string_index.find(label.name_offset)
            if entry is not None and entry.offset == label.name_offset:
                inferable_strings.add(entry.offset)
    refs_by_token: dict[int, list[RefEntry]] = {}
    for ref in refs:
        if 0 <= ref.token_offset < header.token_size and ref.token_offset % TOKEN_SIZE == 0:
            refs_by_token.setdefault(ref.token_offset, []).append(ref)
    blocked_anchor_offsets = set(labels_by_token)
    blocked_anchor_offsets.update(refs_by_token)

    next_label_index = 0

    def emit_token_anchors(token: Token) -> None:
        nonlocal next_label_index
        token_labels = labels_by_token.get(token.offset, [])
        if token_labels:
            lines.append("")
            for label in token_labels:
                lines.append(f"{label.symbol}:")
                name_ref = "none"
                if label.name_offset >= 0:
                    name_ref = string_ref_text(label.name_offset, strings, header.string_size, string_index)
                raw_part = ""
                entry = string_index.find(label.name_offset) if label.name_offset >= 0 else None
                if entry is not None and entry.offset == label.name_offset:
                    raw_part = " " + quoted_bytes(entry.raw[:-1] if entry.terminated else entry.raw, encoding)
                    if not entry.terminated:
                        raw_part += " terminated=false"
                parts: list[str] = []
                if label.index != next_label_index:
                    parts.append(f"index={label.index}")
                next_label_index = max(next_label_index, label.index + 1)
                if label.hash_next:
                    parts.append(f"hash_next=0x{label.hash_next:08X}")
                if name_ref != "none" and not raw_part:
                    parts.append(f"name={name_ref}")
                if label.name_len:
                    parts.append(f"name_len={label.name_len}")
                if raw_part:
                    parts.append(raw_part.strip())
                lines.append("    label " + " ".join(parts))
        for ref in refs_by_token.get(token.offset, []):
            lines.append(
                f"    ref index={ref.index} serial={ref_serial(ref.kind_or_index)} "
                f"kind={ref_kind_text(ref.kind_or_index)} target=here"
            )

    def can_fold_call(start: int, command_index: int, command: CommandDef) -> bool:
        if command.name == ";" or tokens[command_index].kind != 0x08:
            return False
        if command.left_min:
            return False
        if command.push_count:
            return False
        if command_index - start != tokens[command_index].argc_or_state:
            return False
        for token_index in range(start, command_index):
            token = tokens[token_index]
            if token.opcode_or_type >= 0:
                return False
            if (token.opcode_or_type & 0xFFFF) not in VALUE_TYPES:
                return False
        for token_index in range(start + 1, command_index + 1):
            if tokens[token_index].offset in blocked_anchor_offsets:
                return False
        return True

    folded_call_cache: dict[int, tuple[list[Token], Token, CommandDef, int] | None] = {}

    def folded_call_at(start: int) -> tuple[list[Token], Token, CommandDef, int] | None:
        if start in folded_call_cache:
            return folded_call_cache[start]
        result = None
        for command_index in range(start, min(len(tokens), start + 257)):
            command_token = tokens[command_index]
            if command_token.opcode_or_type < 0:
                continue
            command = COMMANDS.get(command_token.opcode_or_type)
            if command is not None and can_fold_call(start, command_index, command):
                result = (tokens[start:command_index], command_token, command, command_index + 1)
                break
        folded_call_cache[start] = result
        return result

    def followed_by_statement_end(index: int) -> int | None:
        if index >= len(tokens):
            return None
        token = tokens[index]
        command = COMMANDS.get(token.opcode_or_type) if token.opcode_or_type >= 0 else None
        if command is None or command.name != ";" or token.kind != 0x08 or token.argc_or_state != 0:
            return None
        if token.offset in blocked_anchor_offsets:
            return None
        return index + 1

    def high_statement_at(start: int) -> tuple[int, str] | None:
        folded_call = folded_call_at(start)
        if folded_call is None:
            return None
        args, _command_token, command, next_index = folded_call
        end_index = followed_by_statement_end(next_index)
        if command.name not in {"IPageEnd", "IPageStart", "CallSub"}:
            end_index = next_index
        if end_index is None:
            return None
        line = format_high_call_line(args, command, strings, header, string_index, encoding, include_exact_comment=True)
        if line is None:
            return None
        return end_index, line

    def readable_statement_at(start: int) -> tuple[int, str] | None:
        expr_stack: list[SourceExpr] = []
        index = start
        while index < len(tokens):
            token = tokens[index]
            if index > start and (token.offset in blocked_anchor_offsets):
                return None
            if token.opcode_or_type < 0:
                if (token.opcode_or_type & 0xFFFF) not in VALUE_TYPES:
                    return None
                value_text = format_high_arg(token, strings, header, string_index, encoding)
                if value_text is None:
                    return None
                expr_stack.append(SourceExpr(value_text, (token,)))
                index += 1
                continue

            command = COMMANDS.get(token.opcode_or_type)
            if command is None or token.kind != 0x08:
                return None
            consume_count = max(command.left_min, 0) + token.argc_or_state
            if len(expr_stack) < consume_count:
                return None
            arg_exprs = expr_stack[-consume_count:] if consume_count else []
            if consume_count:
                del expr_stack[-consume_count:]
            arg_texts = [arg.text for arg in arg_exprs]
            display = command_expr_text(command, arg_texts)
            if "operator(" in display:
                return None
            visible = command.name != ";" and (not command.push_count or command.name == "=")
            if visible:
                if display not in {"{", "}"}:
                    notes: list[str] = []
                    arg_note_offsets: set[int] = set()
                    if command.name != "=":
                        for arg_index, arg_expr in enumerate(arg_exprs):
                            if len(arg_expr.tokens) != 1:
                                continue
                            arg_token = arg_expr.tokens[0]
                            if arg_token.opcode_or_type < 0:
                                note = exact_arg_note(arg_index, arg_token, command.name)
                                if note is not None:
                                    notes.append(note)
                                    arg_note_offsets.add(arg_token.offset)
                    value_index = 0
                    for arg_expr in arg_exprs:
                        for arg_token in arg_expr.tokens:
                            if arg_token.opcode_or_type >= 0:
                                continue
                            note = exact_val_note(value_index, arg_token)
                            if note is not None and arg_token.offset not in arg_note_offsets:
                                notes.append(note)
                            value_index += 1
                    display = add_exact_comment(display, notes)
                next_index = index + 1
                if command.name == "=" or display in {"{", "}"} or command.name in {"IPageEnd", "IPageStart", "CallSub"}:
                    end_index = followed_by_statement_end(next_index)
                    if end_index is None:
                        return None
                    next_index = end_index
                return next_index, display
            if command.push_count:
                expr_tokens: list[Token] = []
                for arg_expr in arg_exprs:
                    expr_tokens.extend(arg_expr.tokens)
                expr_tokens.append(token)
                expr_stack.append(SourceExpr(display, tuple(expr_tokens)))
            if command.name == ";":
                return None
            index += 1
        return None

    def direct_text_piece_at(start: int) -> tuple[int, str] | None:
        folded_call = folded_call_at(start)
        if folded_call is None:
            return None
        args, _command_token, command, next_index = folded_call
        if command.name == "MsgOut" and len(args) == 1:
            text = text_arg(args[0], header, string_index, encoding)
            return (next_index, text) if text is not None else None
        if command.name == "\\R(" and len(args) == 2:
            base = text_arg(args[0], header, string_index, encoding)
            ruby = text_arg(args[1], header, string_index, encoding)
            return (next_index, f"\\R({base}|{ruby})") if base is not None and ruby is not None else None
        if command.name == "\\n" and not args:
            return next_index, "\\n"
        if command.name == "\\Name(" and len(args) == 2:
            first = format_high_arg(args[0], strings, header, string_index, encoding)
            second = format_high_arg(args[1], strings, header, string_index, encoding)
            return (next_index, f"\\Name({first},{second})") if first is not None and second is not None else None
        return None

    def text_control_bridge_at(start: int) -> tuple[int, str] | None:
        statement = readable_statement_at(start)
        if statement is None:
            return None
        next_index, line = statement
        if not line.startswith("\\") or " ; exact " in line:
            return None
        return next_index, line

    def text_run_at(start: int) -> tuple[int, str] | None:
        first = direct_text_piece_at(start)
        if first is None:
            return None
        index, piece = first
        pieces = [piece]
        while index < len(tokens):
            direct = direct_text_piece_at(index)
            if direct is not None:
                index, piece = direct
                pieces.append(piece)
                continue

            bridge_index = index
            bridges: list[str] = []
            while bridge_index < len(tokens):
                if direct_text_piece_at(bridge_index) is not None:
                    break
                bridge = text_control_bridge_at(bridge_index)
                if bridge is None:
                    break
                bridge_index, bridge_text = bridge
                bridges.append(bridge_text)
            if not bridges:
                break
            following = direct_text_piece_at(bridge_index)
            if following is None:
                break
            index, piece = following
            pieces.extend(bridges)
            pieces.append(piece)
        return index, format_text_line("".join(pieces))

    from assembler import (
        AsmState,
        compile_high_call_line,
        compile_high_call_line_with_exact,
        compile_readable_source_line,
        compile_readable_source_line_with_exact,
        compile_text_line,
        parse_end_line,
        split_exact_comment,
    )

    compiled_shape_cache: dict[str, tuple[tuple[int, int, int, int], ...] | None] = {}

    def compiled_source_shape(line_text: str) -> tuple[tuple[int, int, int, int], ...] | None:
        if line_text in compiled_shape_cache:
            return compiled_shape_cache[line_text]
        try:
            state = AsmState(encoding=encoding, version=header.version, source_format=True)
            lineno = 1
            code_text, exact_text = split_exact_comment(line_text)
            code_text = code_text.strip()
            if code_text.startswith("text "):
                handled = compile_text_line(code_text, state, lineno)
            elif code_text.startswith("end"):
                parse_end_line(code_text, state, lineno)
                handled = True
            elif exact_text is not None:
                handled = compile_high_call_line_with_exact(code_text, exact_text, state, lineno)
                if not handled:
                    handled = compile_readable_source_line_with_exact(code_text, exact_text, state, lineno)
            elif compile_readable_source_line(code_text, state, lineno):
                handled = True
            else:
                handled = compile_high_call_line(code_text, state, lineno)
            if not handled or state.tokens is None:
                compiled_shape_cache[line_text] = None
                return None
            shape = tuple(
                (kind, argc, opcode, value)
                for kind, argc, opcode, value in (
                    struct.unpack("<BBHI", raw) for _offset, raw in state.tokens
                )
            )
        except Exception:
            shape = None
        compiled_shape_cache[line_text] = shape
        return shape

    def can_emit_rebuildable_source(line_text: str, start: int, end: int) -> bool:
        shape = compiled_source_shape(line_text)
        if shape is None or len(shape) != end - start:
            return False
        for token, (kind, argc, opcode, value) in zip(tokens[start:end], shape):
            token_opcode = token.opcode_or_type & 0xFFFF
            if (token.kind, token.argc_or_state, token_opcode) != (kind, argc, opcode):
                return False
            if token.opcode_or_type < 0 and token_opcode != 0x8002 and token.value != value:
                return False
            if command_values and token.opcode_or_type >= 0 and token.value != value:
                return False
        return True

    lines.append("")
    index = 0
    while index < len(tokens):
        token = tokens[index]
        emit_token_anchors(token)
        text_run = text_run_at(index)
        if text_run is not None:
            start = index
            next_index, text_line = text_run
            if can_emit_rebuildable_source(text_line, start, next_index):
                index = next_index
                lines.append("    " + text_line)
                continue
        high_statement = high_statement_at(index)
        if high_statement is not None:
            start = index
            next_index, high_line = high_statement
            if can_emit_rebuildable_source(high_line, start, next_index):
                index = next_index
                lines.append("    " + high_line)
                continue
        readable_statement = readable_statement_at(index)
        if readable_statement is not None:
            start = index
            next_index, readable_line = readable_statement
            if can_emit_rebuildable_source(readable_line, start, next_index):
                index = next_index
                lines.append("    " + readable_line)
                continue
        folded_call = folded_call_at(index)
        if folded_call is not None:
            args, command_token, command, next_index = folded_call
            high_line = format_high_call_line(args, command, strings, header, string_index, encoding, include_exact_comment=True)
            if high_line is not None and can_emit_rebuildable_source(high_line, index, next_index):
                lines.append("    " + high_line)
                index = next_index
                continue
            for call_line in format_call_lines(
                args,
                command_token,
                command,
                strings,
                header,
                string_index,
                encoding,
                command_values,
            ):
                lines.append("    " + call_line)
            index = next_index
            continue

        token_text = format_token(
            token,
            strings,
            header,
            allow_unknown,
            string_index,
            include_label=False,
            compact=True,
            command_values=command_values,
        )
        if not token_text.strip():
            token_text = format_token(
                token,
                strings,
                header,
                allow_unknown,
                string_index,
                include_label=False,
                compact=False,
                command_values=command_values,
            )
        line = "    " + token_text
        if (token.opcode_or_type & 0xFFFF) == 0x8002:
            entry = string_index.find(token.value)
            if entry is not None and entry.offset == token.value:
                line += " " + quoted_bytes(entry.raw[:-1] if entry.terminated else entry.raw, encoding)
                if not entry.terminated:
                    line += " terminated=false"
        lines.append(line)
        index += 1

    for entry in strings:
        if entry.offset not in inferable_strings:
            raw = entry.raw[:-1] if entry.terminated else entry.raw
            suffix = "" if entry.terminated else " terminated=false"
            lines.append(f"    .pool {str_label(entry.offset)} raw={quoted_bytes(raw, encoding)}{suffix}")

    for label in labels:
        if label.token_offset < 0:
            name_ref = "none"
            if label.name_offset >= 0:
                name_ref = string_ref_text(label.name_offset, strings, header.string_size, string_index)
            parts = [f"index={label.index}"]
            if label.hash_next:
                parts.append(f"hash_next=0x{label.hash_next:08X}")
            if name_ref != "none" and name_ref != str_label(label.name_offset):
                parts.append(f"name={name_ref}")
            if label.name_len:
                parts.append(f"name_len={label.name_len}")
            parts.append("target=none")
            if label.symbol and label.symbol != f"label_{label.index:04d}":
                parts.append(f"symbol={label.symbol}")
            lines.append("    label " + " ".join(parts))
    for ref in refs:
        if ref.token_offset < 0:
            lines.append(
                f"    ref index={ref.index} serial={ref_serial(ref.kind_or_index)} "
                f"kind={ref_kind_text(ref.kind_or_index)} target=none"
            )

    lines.append("")
    return "\n".join(lines)


def emit_code_disassembly(path: Path, data: bytes, encoding: str, allow_unknown: bool) -> str:
    header = parse_header(data, path)
    strings = parse_string_pool(data, header)
    string_index = StringIndex.build(strings)
    tokens = parse_tokens(data, header)
    labels = parse_labels(data, header, strings, encoding, string_index)
    refs = parse_refs(data, header)

    labels_by_token: dict[int, list[LabelEntry]] = {}
    for label in labels:
        if 0 <= label.token_offset < header.token_size and label.token_offset % TOKEN_SIZE == 0:
            labels_by_token.setdefault(label.token_offset, []).append(label)

    refs_by_token: dict[int, list[RefEntry]] = {}
    for ref in refs:
        if 0 <= ref.token_offset < header.token_size and ref.token_offset % TOKEN_SIZE == 0:
            refs_by_token.setdefault(ref.token_offset, []).append(ref)

    lines: list[str] = []
    lines.append('.format "kyoupri-gsx1-code"')
    lines.append(f'.encoding {quoted_text(encoding)}')
    lines.append(f".file_size 0x{len(data):08X}")
    lines.append(f".version 0x{header.version:08X}")
    lines.append(segment_line("strings", header.string_offset, header.string_size))
    lines.append(segment_line("labels", header.label_offset, header.label_size))
    lines.append(segment_line("refs", header.ref_offset, header.ref_size))
    lines.append(segment_line("tokens", header.token_offset, header.token_size))
    lines.append("")
    lines.append(".section code")

    emitted_strings: set[int] = set()
    expr_stack: list[str] = []
    for index, token in enumerate(tokens):
        token = tokens[index]
        label_lines = labels_by_token.get(token.offset, [])
        if label_lines:
            lines.append("")
        for label in label_lines:
            name_ref = "none"
            meta_parts = [f"@label i={label.index}"]
            if label.name_offset >= 0:
                name_ref = string_ref_text(label.name_offset, strings, header.string_size, string_index)
                meta_parts.append(f"name={name_ref}")
                entry = string_index.find(label.name_offset)
                if entry is not None and entry.offset == label.name_offset:
                    label_text = decode_pool_text(entry.raw, encoding)
                    if label_text != label.symbol:
                        meta_parts.append(f"raw={quoted_bytes(entry.raw[:-1] if entry.terminated else entry.raw, encoding)}")
                    emitted_strings.add(entry.offset)
            meta_parts.append(f"target=0x{label.token_offset:08X}")
            if label.name_len != 0:
                meta_parts.append(f"nlen={label.name_len}")
            if label.hash_next != 0:
                meta_parts.append(f"hash=0x{label.hash_next:08X}")
            lines.append(
                f"{label.symbol}:  # {' '.join(meta_parts)}"
            )
        for ref in refs_by_token.get(token.offset, []):
            lines.append(
                f"    # @ref i={ref.index} kind=0x{ref.kind_or_index:08X} target=0x{ref.token_offset:08X}"
            )
        lines.append(format_code_token(index, tokens, token, strings, header, encoding, allow_unknown, string_index, emitted_strings, expr_stack))

    for entry in strings:
        if entry.offset not in emitted_strings:
            lines.append(f"    # @pool {str_label(entry.offset)} {raw_string_fields(entry, encoding)}")
            emitted_strings.add(entry.offset)

    lines.append("")
    return "\n".join(lines)


def emit_disassembly(
    path: Path,
    data: bytes,
    encoding: str,
    allow_unknown: bool,
    style: str = "source",
    command_values: bool = False,
) -> str:
    if style == "raw":
        return emit_raw_disassembly(path, data, encoding, allow_unknown)
    if style == "code":
        return emit_code_disassembly(path, data, encoding, allow_unknown)
    if style == "source":
        return emit_source_disassembly(path, data, encoding, allow_unknown, command_values)
    raise ValueError(f"unknown disassembly style: {style}")


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(input_path.name + ".asm.txt")


def resolve_outputs(inputs: list[Path], output: Path | None) -> dict[Path, Path]:
    if output is None:
        return {path: default_output_path(path) for path in inputs}
    if len(inputs) == 1:
        return {inputs[0]: output}
    output.mkdir(parents=True, exist_ok=True)
    return {path: output / (path.name + ".asm.txt") for path in inputs}


def disassemble_file(
    input_path: Path,
    output_path: Path,
    encoding: str,
    allow_unknown: bool,
    style: str,
    command_values: bool,
) -> None:
    data = input_path.read_bytes()
    text = emit_disassembly(input_path, data, encoding, allow_unknown, style, command_values)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8", newline="\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Disassemble KyouPri GSX1 script files.")
    parser.add_argument("inputs", nargs="+", type=Path, help="Input .gkx/.GSX1 files.")
    parser.add_argument("-o", "--output", type=Path, help="Output file for one input, or output directory for many.")
    parser.add_argument("--encoding", default="cp932", help="Script string encoding, default: cp932.")
    parser.add_argument("--allow-unknown", action="store_true", help="Emit .unknown_token instead of failing.")
    parser.add_argument(
        "--style",
        choices=("source", "code", "raw"),
        default="source",
        help="Output style: source (default), legacy code, or raw section dump.",
    )
    parser.add_argument(
        "--keep-command-values",
        action="store_true",
        help="Keep command token source-position values for byte-exact round-trip; default hides them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    inputs = [path.resolve() for path in args.inputs]
    for path in inputs:
        if not path.is_file():
            parser.error(f"input does not exist or is not a file: {path}")
    outputs = resolve_outputs(inputs, args.output.resolve() if args.output else None)
    for input_path in inputs:
        output_path = outputs[input_path]
        disassemble_file(input_path, output_path, args.encoding, args.allow_unknown, args.style, args.keep_command_values)
        print(f"{input_path} -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
