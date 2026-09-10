from __future__ import annotations

import argparse
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from opcodelist import COMMANDS, VALUE_TYPES


HEADER_SIZE = 40
TOKEN_SIZE = 8
LABEL_ENTRY_SIZE = 16
REF_ENTRY_SIZE = 8

REF_KIND_FLAGS = {
    "If2": 1,
    "Else": 2,
    "EndIf": 4,
    "Switch": 16,
    "Case": 32,
    "EndSwitch": 64,
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


@dataclass(frozen=True, slots=True)
class Segment:
    offset: int
    size: int


@dataclass
class AsmState:
    encoding: str = "cp932"
    version: int | None = None
    file_size: int | None = None
    segments: dict[str, Segment] | None = None
    strings: list[tuple[int, bytes]] | None = None
    strings_by_offset: dict[int, bytes] | None = None
    labels: list[tuple[int, int, int, int, int]] | None = None
    refs: list[tuple[int, int, int]] | None = None
    tokens: list[tuple[int, bytes]] | None = None
    token_offset_map: dict[int, int] | None = None
    token_cursor: int = 0
    source_format: bool = False
    pending_token_offset: int | None = None
    current_command_value: int = 0
    auto_string_offset: int = 0
    auto_label_index: int = 0

    def __post_init__(self) -> None:
        if self.segments is None:
            self.segments = {}
        if self.strings is None:
            self.strings = []
        if self.strings_by_offset is None:
            self.strings_by_offset = {}
        if self.labels is None:
            self.labels = []
        if self.refs is None:
            self.refs = []
        if self.tokens is None:
            self.tokens = []
        if self.token_offset_map is None:
            self.token_offset_map = {}


class AsmError(ValueError):
    pass


MNEMONIC_TO_TYPE = {definition.mnemonic: type_code for type_code, definition in VALUE_TYPES.items()}
MNEMONIC_TO_TYPE.update({
    "raw": 0x8000,
    "str": 0x8002,
    "global": 0x8010,
    "local": 0x8110,
})


def strip_comment(line: str) -> str:
    if ";" not in line:
        return line
    in_quote = False
    escape = False
    for index, ch in enumerate(line):
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
            continue
        if ch == ";":
            return line[:index]
    return line


def split_fields(text: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    in_quote = False
    escape = False
    for ch in text.strip():
        if in_quote:
            current.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch.isspace():
            if current:
                fields.append("".join(current))
                current = []
            continue
        current.append(ch)
        if ch == '"':
            in_quote = True
    if in_quote:
        raise AsmError(f"unterminated quoted string: {text}")
    if current:
        fields.append("".join(current))
    return fields


def parse_kv_fields(fields: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in fields:
        if "=" not in field:
            continue
        key, value = field.split("=", 1)
        result[key] = value
    return result


def parse_int(text: str) -> int:
    text = text.strip()
    if text.lower().startswith("-0x"):
        return -int(text[3:], 16)
    return int(text, 0)


def field_value(text: str, key: str) -> str | None:
    needle = key + "="
    pos = text.find(needle)
    if pos < 0:
        return None
    start = pos + len(needle)
    end = text.find(" ", start)
    return text[start:] if end < 0 else text[start:end]


def require_field(text: str, key: str, lineno: int, directive: str) -> str:
    value = field_value(text, key)
    if value is None:
        raise AsmError(f"line {lineno}: {directive} missing {key}")
    return value


def require_kv(kv: dict[str, str], keys: tuple[str, ...], lineno: int, directive: str) -> str:
    for key in keys:
        if key in kv:
            return kv[key]
    raise AsmError(f"line {lineno}: {directive} missing {'/'.join(keys)}")


def parse_json_text(text: str) -> str:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AsmError(f"invalid quoted text {text!r}: {exc}") from exc
    if not isinstance(value, str):
        raise AsmError(f"expected a quoted string, got {text!r}")
    return value


def to_u32(value: int) -> int:
    return value & 0xFFFFFFFF


def to_u16(value: int) -> int:
    return value & 0xFFFF


def parse_bytes_literal(text: str, encoding: str) -> bytes:
    if not (text.startswith('"') and text.endswith('"')):
        raise AsmError(f"expected quoted byte literal, got {text!r}")
    body = text[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        if body.startswith("{{", index):
            end = body.find("}}", index + 2)
            if end < 0:
                raise AsmError(f"unterminated byte placeholder in {text!r}")
            payload = body[index + 2 : end]
            if not payload:
                raise AsmError(f"empty byte placeholder in {text!r}")
            for part in payload.split(":"):
                if not re.fullmatch(r"[0-9A-Fa-f]{2}", part):
                    raise AsmError(f"invalid byte placeholder {{{{{payload}}}}}")
                out.append(int(part, 16))
            index = end + 2
            continue
        next_marker = body.find("{{", index)
        if next_marker < 0:
            next_marker = len(body)
        chunk = body[index:next_marker]
        out.extend(chunk.encode(encoding))
        index = next_marker
    return bytes(out)


def parse_str_label(text: str) -> int:
    if len(text) == 12 and text.startswith("str_"):
        return int(text[4:], 16)
    match = re.fullmatch(r"str_([0-9A-Fa-f]{8})", text)
    if not match:
        raise AsmError(f"invalid string label: {text}")
    return int(match.group(1), 16)


def parse_tok_label(text: str) -> int:
    if len(text) == 12 and text.startswith("tok_"):
        return int(text[4:], 16)
    match = re.fullmatch(r"tok_([0-9A-Fa-f]{8})", text)
    if not match:
        raise AsmError(f"invalid token label: {text}")
    return int(match.group(1), 16)


def parse_segment(line: str, state: AsmState, lineno: int) -> None:
    fields = split_fields(line)
    if len(fields) < 2:
        raise AsmError(f"line {lineno}: malformed .segment")
    name = fields[1]
    kv = parse_kv_fields(fields[2:])
    try:
        offset = parse_int(kv["offset"])
        size = parse_int(kv["size"])
    except KeyError as exc:
        raise AsmError(f"line {lineno}: .segment missing {exc.args[0]}") from exc
    state.segments[name] = Segment(offset, size)


def add_string_entry(state: AsmState, offset: int, raw: bytes, lineno: int) -> None:
    assert state.strings is not None
    assert state.strings_by_offset is not None
    previous = state.strings_by_offset.get(offset)
    if previous is not None:
        if previous != raw:
            raise AsmError(f"line {lineno}: conflicting string bytes at str_{offset:08X}")
        return
    state.strings_by_offset[offset] = raw
    state.strings.append((offset, raw))
    if state.source_format and offset == state.auto_string_offset:
        state.auto_string_offset += len(raw)


def add_auto_string_entry(state: AsmState, raw: bytes, lineno: int) -> int:
    offset = state.auto_string_offset
    add_string_entry(state, offset, raw, lineno)
    return offset


def parse_string_raw(kv: dict[str, str], state: AsmState, lineno: int, directive: str) -> bytes:
    raw_text = kv.get("raw")
    if raw_text is None:
        raise AsmError(f"line {lineno}: {directive} missing raw")
    raw = parse_bytes_literal(raw_text, state.encoding)
    if kv.get("terminated") != "false" and not raw.endswith(b"\x00"):
        raw += b"\x00"
    return raw


def add_inline_string(state: AsmState, kv: dict[str, str], offset: int, lineno: int, directive: str) -> None:
    if "raw" not in kv or offset < 0:
        return
    add_string_entry(state, offset, parse_string_raw(kv, state, lineno, directive), lineno)


def parse_left_string_assignment(line: str, marker: str, state: AsmState, lineno: int) -> int | None:
    prefix = line.split(marker, 1)[0].rstrip()
    if prefix.endswith("#"):
        prefix = prefix[:-1].rstrip()
    match = re.search(r"(str_[0-9A-Fa-f]{8})\s*=\s*(\".*\")(\s+terminated=false)?\s*$", prefix)
    if not match:
        return None
    offset = parse_str_label(match.group(1))
    raw = parse_bytes_literal(match.group(2), state.encoding)
    if match.group(3) is None and not raw.endswith(b"\x00"):
        raw += b"\x00"
    add_string_entry(state, offset, raw, lineno)
    return offset


def parse_string_line(line: str, state: AsmState, lineno: int) -> None:
    match = re.fullmatch(r"(str_[0-9A-Fa-f]{8}):\s+\.string\s+(\".*\")(\s+terminated=false)?", line)
    if not match:
        if ".str_alias" in line:
            return
        raise AsmError(f"line {lineno}: malformed .string")
    offset = parse_str_label(match.group(1))
    raw = parse_bytes_literal(match.group(2), state.encoding)
    if match.group(3) is None and not raw.endswith(b"\x00"):
        raw += b"\x00"
    add_string_entry(state, offset, raw, lineno)


def parse_pool_line(line: str, state: AsmState, lineno: int) -> None:
    if "@pool" in line:
        _, rest = line.split("@pool", 1)
        directive = "@pool"
    else:
        _, rest = line.split(".pool", 1)
        directive = ".pool"
    fields = split_fields(rest)
    if len(fields) < 2:
        raise AsmError(f"line {lineno}: malformed {directive}")
    offset = parse_str_label(fields[0])
    kv = parse_kv_fields(fields[1:])
    add_string_entry(state, offset, parse_string_raw(kv, state, lineno, directive), lineno)


def add_token_entry(state: AsmState, offset: int | None, raw: bytes) -> None:
    if offset is None:
        offset = state.token_cursor
    state.tokens.append((offset, raw))
    if state.token_offset_map is not None:
        for delta in range(0, len(raw), TOKEN_SIZE):
            state.token_offset_map[offset + delta] = offset + delta
    state.token_cursor = offset + len(raw)


def parse_plain_string_assignment(line: str, state: AsmState, lineno: int) -> bool:
    match = re.fullmatch(r"\s*(str_[0-9A-Fa-f]{8})\s*=\s*(\".*\")(\s+terminated=false)?\s*", line)
    if not match:
        return False
    offset = parse_str_label(match.group(1))
    raw = parse_bytes_literal(match.group(2), state.encoding)
    if match.group(3) is None and not raw.endswith(b"\x00"):
        raw += b"\x00"
    add_string_entry(state, offset, raw, lineno)
    return True


def parse_label_entry(line: str, state: AsmState, lineno: int) -> None:
    fields = split_fields(line)
    directive = fields[0]
    kv = parse_kv_fields(fields[1:])
    index_text = kv.get("index")
    if index_text is None:
        if directive != "label" or not state.source_format:
            raise AsmError(f"line {lineno}: {directive} missing index")
        index = state.auto_label_index
    else:
        index = parse_int(index_text)
    state.auto_label_index = max(state.auto_label_index, index + 1)
    name_len = parse_int(kv.get("name_len", "0"))
    hash_next = parse_int(kv.get("hash_next", "0"))
    if "name_offset" in kv:
        name_offset = parse_int(kv["name_offset"])
    else:
        positional = [field for field in fields[1:] if "=" not in field]
        name = kv.get("name")
        if name is None and ("raw" in kv or positional):
            raw_text = kv.get("raw") or positional[0]
            raw = parse_bytes_literal(raw_text, state.encoding)
            if kv.get("terminated") != "false" and not raw.endswith(b"\x00"):
                raw += b"\x00"
            name_offset = add_auto_string_entry(state, raw, lineno)
        else:
            name = "none" if name is None else name
            name_offset = -1 if name == "none" else parse_str_label(name)
    token_offset_text = kv.get("token_offset")
    if token_offset_text is not None:
        token_offset = parse_int(token_offset_text)
    else:
        target = kv.get("target", "here" if directive == "label" and state.source_format else "none")
        if target == "none":
            token_offset = -1
        elif target == "here":
            token_offset = state.token_cursor
        else:
            token_offset = parse_tok_label(target)
    add_inline_string(state, kv, name_offset, lineno, directive)
    state.labels.append((index, hash_next, name_offset, name_len, token_offset))


def parse_ref_kind(kind_text: str, lineno: int) -> int:
    if kind_text in REF_KIND_FLAGS:
        return REF_KIND_FLAGS[kind_text]
    value = parse_int(kind_text)
    if not (0 <= value <= 0xFFFF):
        raise AsmError(f"line {lineno}: ref kind out of range: {kind_text}")
    return value


def parse_ref_line(line: str, state: AsmState, lineno: int) -> None:
    fields = split_fields(line)
    kv = parse_kv_fields(fields[1:])
    try:
        index = parse_int(kv["index"])
    except KeyError as exc:
        raise AsmError(f"line {lineno}: .ref missing {exc.args[0]}") from exc
    if "kind_or_index" in kv:
        kind_or_index = parse_int(kv["kind_or_index"])
    else:
        serial = parse_int(require_kv(kv, ("serial",), lineno, ".ref"))
        kind_flags = parse_ref_kind(require_kv(kv, ("kind",), lineno, ".ref"), lineno)
        if not (0 <= serial <= 0xFFFF):
            raise AsmError(f"line {lineno}: ref serial out of range: {serial}")
        kind_or_index = serial | (kind_flags << 16)
    token_offset_text = kv.get("token_offset")
    if token_offset_text is not None:
        token_offset = parse_int(token_offset_text)
    else:
        target = kv.get("target", "none")
        if target == "none":
            token_offset = -1
        elif target == "here":
            token_offset = state.token_cursor
        else:
            token_offset = parse_tok_label(target)
    state.refs.append((index, kind_or_index, token_offset))


def parse_inline_label_line(line: str, state: AsmState, lineno: int) -> None:
    prefix, rest = line.split("@label", 1)
    fields = split_fields(rest)
    kv = parse_kv_fields(fields)
    index = parse_int(require_kv(kv, ("index", "i"), lineno, "@label"))
    name_len = parse_int(kv.get("name_len") or kv.get("nlen") or "0")
    hash_next = parse_int(kv.get("hash_next") or kv.get("hash") or "0")
    if "name_offset" in kv:
        name_offset = parse_int(kv["name_offset"])
    else:
        name = kv.get("name", "none")
        name_offset = -1 if name == "none" else parse_str_label(name)
    token_offset_text = kv.get("token_offset")
    if token_offset_text is not None:
        token_offset = parse_int(token_offset_text)
    else:
        target = kv.get("target", "none")
        if target == "none":
            token_offset = -1
        elif target.startswith("tok_"):
            token_offset = parse_tok_label(target)
        else:
            token_offset = parse_int(target)
    add_inline_string(state, kv, name_offset, lineno, "@label")
    if "raw" not in kv and name_offset >= 0:
        symbol = prefix.rsplit("#", 1)[0].strip()
        if symbol.endswith(":"):
            symbol = symbol[:-1].strip()
        if symbol:
            add_string_entry(state, name_offset, symbol.encode(state.encoding) + b"\x00", lineno)
    state.labels.append((index, hash_next, name_offset, name_len, token_offset))


def parse_inline_ref_line(line: str, state: AsmState, lineno: int) -> None:
    _, rest = line.split("@ref", 1)
    fields = split_fields(rest)
    kv = parse_kv_fields(fields)
    index = parse_int(require_kv(kv, ("index", "i"), lineno, "@ref"))
    kind_or_index = parse_int(require_kv(kv, ("kind_or_index", "kind"), lineno, "@ref"))
    token_offset_text = kv.get("token_offset")
    if token_offset_text is not None:
        token_offset = parse_int(token_offset_text)
    else:
        target = kv.get("target", "none")
        if target == "none":
            token_offset = -1
        elif target.startswith("tok_"):
            token_offset = parse_tok_label(target)
        else:
            token_offset = parse_int(target)
    state.refs.append((index, kind_or_index, token_offset))


def parse_code_token_offset(line: str, marker: str, lineno: int) -> tuple[int, str]:
    prefix, rest = line.split(marker, 1)
    match = re.match(r"\s*(tok_[0-9A-Fa-f]{8}):", prefix)
    if match:
        return parse_tok_label(match.group(1)), rest.strip()
    kv = parse_kv_fields(split_fields(rest))
    offset_text = kv.get("off") or kv.get("token_offset")
    if offset_text is None:
        raise AsmError(f"line {lineno}: {marker} line missing token offset")
    return parse_int(offset_text), rest.strip()


def parse_token_prefix(line: str, directive: str, lineno: int) -> tuple[int, list[str]]:
    prefix, rest = line.split(directive, 1)
    label = prefix.strip()
    if not label.endswith(":"):
        raise AsmError(f"line {lineno}: token line missing tok label")
    offset = parse_tok_label(label[:-1])
    return offset, split_fields(directive + rest)


def parse_token_rest(line: str, directive: str, lineno: int) -> tuple[int, str]:
    prefix, rest = line.split(directive, 1)
    label = prefix.strip()
    if not label.endswith(":"):
        raise AsmError(f"line {lineno}: token line missing tok label")
    return parse_tok_label(label[:-1]), rest.strip()


def parse_optional_token_prefix(line: str, directive: str, state: AsmState, lineno: int) -> tuple[int, str]:
    if directive == ".command" and ".command" not in line and line.lstrip().startswith("cmd "):
        prefix, rest = line.split("cmd", 1)
    elif directive == ".value" and ".value" not in line and line.lstrip().startswith("val "):
        prefix, rest = line.split("val", 1)
    elif directive == ".unknown_token" and ".unknown_token" not in line and line.lstrip().startswith("unknown_token "):
        prefix, rest = line.split("unknown_token", 1)
    else:
        prefix, rest = line.split(directive, 1)
    label = prefix.strip()
    if label:
        if not label.endswith(":"):
            raise AsmError(f"line {lineno}: token line has malformed prefix before {directive}")
        old_offset = parse_tok_label(label[:-1])
        state.pending_token_offset = None
        if state.source_format:
            if state.token_offset_map is not None:
                state.token_offset_map[old_offset] = state.token_cursor
            return state.token_cursor, rest.strip()
        return old_offset, rest.strip()
    if state.pending_token_offset is None:
        offset = state.token_cursor
    else:
        old_offset = state.pending_token_offset
        state.pending_token_offset = None
        if state.token_offset_map is not None:
            state.token_offset_map[old_offset] = state.token_cursor
        offset = state.token_cursor
    return offset, rest.strip()


def parse_command_fields(rest: str, state: AsmState, lineno: int) -> tuple[int, int, int]:
    fields = split_fields(rest)
    kv = parse_kv_fields(fields)
    cmd_id_text = kv.get("id")
    if cmd_id_text is None:
        name_text = kv.get("name")
        if name_text is None and fields and (fields[0].startswith('"') or "=" not in fields[0]):
            name_text = fields[0]
        if name_text is None:
            raise AsmError(f"line {lineno}: command missing id/name")
        name = parse_json_text(name_text) if name_text.startswith('"') else name_text
        cmd_id = COMMAND_NAME_TO_ID.get(name)
        if cmd_id is None:
            raise AsmError(f"line {lineno}: unknown command name {name!r}")
    else:
        cmd_id = parse_int(cmd_id_text)
    argc = parse_int(require_kv(kv, ("argc",), lineno, "command"))
    value_text = kv.get("value")
    if value_text is None:
        value = state.current_command_value
    else:
        value = parse_int(value_text)
    if cmd_id not in COMMANDS:
        raise AsmError(f"line {lineno}: unknown command id {cmd_id}")
    if not (0 <= argc <= 0xFF):
        raise AsmError(f"line {lineno}: argc out of range: {argc}")
    return cmd_id, argc, value


def parse_call_command_fields(rest: str, state: AsmState, lineno: int) -> tuple[int, int, int, int | None]:
    fields = split_fields(rest)
    kv = parse_kv_fields(fields)
    cmd_id_text = kv.get("id")
    if cmd_id_text is None:
        name_text = kv.get("name")
        if name_text is None and fields and (fields[0].startswith('"') or "=" not in fields[0]):
            name_text = fields[0]
        if name_text is None:
            raise AsmError(f"line {lineno}: call missing command id/name")
        name = parse_json_text(name_text) if name_text.startswith('"') else name_text
        cmd_id = COMMAND_NAME_TO_ID.get(name)
        if cmd_id is None:
            raise AsmError(f"line {lineno}: unknown command name {name!r}")
    else:
        cmd_id = parse_int(cmd_id_text)
    argc = parse_int(require_kv(kv, ("argc",), lineno, "call"))
    value = parse_int(kv["value"]) if kv.get("value") is not None else state.current_command_value
    end_text = kv.get("end")
    end_value = parse_int(end_text) if end_text is not None else None
    if cmd_id not in COMMANDS:
        raise AsmError(f"line {lineno}: unknown command id {cmd_id}")
    if not (0 <= argc <= 0xFF):
        raise AsmError(f"line {lineno}: argc out of range: {argc}")
    return cmd_id, argc, value, end_value


def parse_command_line(line: str, state: AsmState, lineno: int) -> None:
    offset, rest = parse_optional_token_prefix(line, ".command", state, lineno)
    cmd_id, argc, value = parse_command_fields(rest, state, lineno)
    add_token_entry(state, offset, struct.pack("<BBHI", 0x08, argc, cmd_id & 0xFFFF, value & 0xFFFFFFFF))
    state.current_command_value = value + max(1, len(line.strip().encode(state.encoding, errors="replace")))


def consume_token_offset(state: AsmState) -> int:
    if state.pending_token_offset is None:
        return state.token_cursor
    old_offset = state.pending_token_offset
    state.pending_token_offset = None
    if state.token_offset_map is not None:
        state.token_offset_map[old_offset] = state.token_cursor
    return state.token_cursor


def parse_end_line(line: str, state: AsmState, lineno: int) -> None:
    fields = split_fields(line)
    kv = parse_kv_fields(fields[1:])
    value_text = kv.get("value")
    value = state.current_command_value if value_text is None else parse_int(value_text)
    offset = consume_token_offset(state)
    add_token_entry(state, offset, struct.pack("<BBHI", 0x08, 0, 3, value & 0xFFFFFFFF))
    state.current_command_value = value + max(1, len(line.strip().encode(state.encoding, errors="replace")))


def call_inline_raw_text(fields: list[str], index: int) -> str | None:
    arg_key = f"arg{index}="
    for field_index, field in enumerate(fields[:-1]):
        if field.startswith(arg_key) and fields[field_index + 1].startswith('"'):
            return fields[field_index + 1]
    return None


def parse_call_string_raw(kv: dict[str, str], fields: list[str], index: int, state: AsmState, lineno: int) -> bytes | None:
    raw_text = kv.get(f"raw{index}") or call_inline_raw_text(fields, index)
    if raw_text is None:
        return None
    raw = parse_bytes_literal(raw_text, state.encoding)
    if kv.get(f"terminated{index}") != "false" and not raw.endswith(b"\x00"):
        raw += b"\x00"
    return raw


def parse_exact_value_arg_token(text: str, lineno: int, index: int, allow_string: bool = True) -> bytes | None:
    if text.startswith("raw:"):
        return token_raw32(parse_int(text[len("raw:") :]))
    if text.startswith("global:"):
        return token_slot(0x8010, parse_int(text[len("global:") :]))
    if text.startswith("local:"):
        return token_slot(0x8110, parse_int(text[len("local:") :]))
    parts = text.split(":", 3)
    if len(parts) != 4:
        return None
    mnemonic, kind_text, argc_text, value_text = parts
    if mnemonic in MNEMONIC_TO_TYPE:
        type_code = MNEMONIC_TO_TYPE[mnemonic]
    else:
        type_code = parse_int(mnemonic)
    if type_code == 0x8002 and not allow_string:
        raise AsmError(f"line {lineno}: high-call arg{index} cannot use string token shorthand")
    kind = parse_int(kind_text)
    argc = parse_int(argc_text)
    value = parse_int(value_text)
    if not (0 <= kind <= 0xFF and 0 <= argc <= 0xFF):
        raise AsmError(f"line {lineno}: call arg{index} kind/argc out of byte range")
    return struct.pack("<BBHI", kind, argc, type_code & 0xFFFF, value & 0xFFFFFFFF)


def parse_call_arg_token(text: str, kv: dict[str, str], fields: list[str], index: int, state: AsmState, lineno: int) -> bytes:
    if text == "str" or text.startswith("str:"):
        raw = parse_call_string_raw(kv, fields, index, state, lineno)
        if text == "str":
            if raw is None:
                raise AsmError(f"line {lineno}: call arg{index}=str missing raw{index}")
            return token_strref(add_auto_string_entry(state, raw, lineno))
        value_text = text[len("str:") :]
        value = parse_str_label(value_text) if value_text.startswith("str_") else parse_int(value_text)
        if raw is not None:
            add_string_entry(state, value, raw, lineno)
        return token_strref(value)
    raw = parse_exact_value_arg_token(text, lineno, index)
    if raw is None:
        raise AsmError(f"line {lineno}: malformed call arg{index}: {text}")
    return raw


def parse_call_line(line: str, state: AsmState, lineno: int) -> None:
    rest = line[len("call") :].strip()
    fields = split_fields(rest)
    kv = parse_kv_fields(fields)
    cmd_id, argc, value, end_value = parse_call_command_fields(rest, state, lineno)
    offset = consume_token_offset(state)
    for index in range(argc):
        arg_text = require_kv(kv, (f"arg{index}",), lineno, "call")
        add_token_entry(state, offset if index == 0 else None, parse_call_arg_token(arg_text, kv, fields, index, state, lineno))
        offset = None
    add_token_entry(state, None if argc else offset, struct.pack("<BBHI", 0x08, argc, cmd_id & 0xFFFF, value & 0xFFFFFFFF))
    if end_value is not None:
        add_token_entry(state, None, struct.pack("<BBHI", 0x08, 0, 3, end_value & 0xFFFFFFFF))
    state.current_command_value = value + max(1, len(line.strip().encode(state.encoding, errors="replace")))


def parse_value_line(line: str, state: AsmState, lineno: int) -> None:
    offset, rest = parse_optional_token_prefix(line, ".value", state, lineno)
    mnemonic, sep, rest = rest.partition(" ")
    if not sep:
        raise AsmError(f"line {lineno}: .value missing mnemonic")
    if mnemonic not in MNEMONIC_TO_TYPE:
        raise AsmError(f"line {lineno}: unknown value mnemonic {mnemonic}")
    fields = split_fields(rest)
    kv = parse_kv_fields(fields)
    type_code = MNEMONIC_TO_TYPE[mnemonic]
    default_kind, default_argc = VALUE_DEFAULT_FIELDS.get(type_code, (0x00, 0xCC))
    kind = parse_int(kv.get("kind", str(default_kind)))
    argc = parse_int(kv.get("argc", str(default_argc)))
    value_text = kv.get("offset") or kv.get("value") or kv.get("u32") or kv.get("slot")
    positional = [field for field in fields if "=" not in field]
    inline_raw_text = positional[0] if positional and positional[0].startswith('"') else None
    if type_code == 0x8002 and ("raw" in kv or inline_raw_text is not None) and value_text is None:
        if "raw" in kv:
            raw = parse_string_raw(kv, state, lineno, ".value")
        else:
            raw = parse_bytes_literal(inline_raw_text or "", state.encoding)
            if kv.get("terminated") != "false" and not raw.endswith(b"\x00"):
                raw += b"\x00"
        value = add_auto_string_entry(state, raw, lineno)
    else:
        if value_text is None:
            value_text = positional[0] if positional else None
        if value_text is None:
            raise AsmError(f"line {lineno}: .value missing value/u32/offset/slot")
        if re.fullmatch(r"str_[0-9A-Fa-f]{8}", value_text):
            value = parse_str_label(value_text)
        else:
            value = parse_int(value_text)
    if not (0 <= kind <= 0xFF and 0 <= argc <= 0xFF):
        raise AsmError(f"line {lineno}: kind/argc out of byte range")
    add_inline_string(state, kv, value, lineno, ".value")
    add_token_entry(state, offset, struct.pack("<BBHI", kind, argc, type_code, value & 0xFFFFFFFF))


def parse_unknown_token_line(line: str, state: AsmState, lineno: int) -> None:
    offset, rest = parse_optional_token_prefix(line, ".unknown_token", state, lineno)
    kind = parse_int(require_field(rest, "kind", lineno, ".unknown_token"))
    argc = parse_int(require_field(rest, "argc", lineno, ".unknown_token"))
    opcode_or_type = parse_int(require_field(rest, "opcode_or_type", lineno, ".unknown_token"))
    value = parse_int(require_field(rest, "value", lineno, ".unknown_token"))
    add_token_entry(state, offset, struct.pack("<BBHI", kind & 0xFF, argc & 0xFF, opcode_or_type & 0xFFFF, value & 0xFFFFFFFF))


def parse_code_command_line(line: str, state: AsmState, lineno: int, marker: str = "@command") -> None:
    offset, rest = parse_code_token_offset(line, marker, lineno)
    fields = split_fields(rest)
    kv = parse_kv_fields(fields)
    cmd_id = parse_int(require_kv(kv, ("id",), lineno, marker))
    argc = parse_int(require_kv(kv, ("argc", "a"), lineno, marker))
    value = parse_int(require_kv(kv, ("value", "v"), lineno, marker))
    if cmd_id not in COMMANDS:
        raise AsmError(f"line {lineno}: unknown command id {cmd_id}")
    if not (0 <= argc <= 0xFF):
        raise AsmError(f"line {lineno}: argc out of range: {argc}")
    state.tokens.append((offset, struct.pack("<BBHI", 0x08, argc, cmd_id & 0xFFFF, value & 0xFFFFFFFF)))


def parse_code_value_line(line: str, state: AsmState, lineno: int, marker: str = "@value") -> None:
    offset, rest = parse_code_token_offset(line, marker, lineno)
    assigned_string = parse_left_string_assignment(line, marker, state, lineno)
    fields = split_fields(rest)
    if not fields:
        raise AsmError(f"line {lineno}: {marker} missing mnemonic")
    mnemonic = fields[0]
    if mnemonic not in MNEMONIC_TO_TYPE:
        raise AsmError(f"line {lineno}: unknown value mnemonic {mnemonic}")
    kv = parse_kv_fields(fields[1:])
    kind = parse_int(require_kv(kv, ("kind", "k"), lineno, marker))
    argc = parse_int(require_kv(kv, ("argc", "a"), lineno, marker))
    value_text = kv.get("str") or kv.get("offset") or kv.get("value") or kv.get("v") or kv.get("u32") or kv.get("slot")
    if value_text is None:
        raise AsmError(f"line {lineno}: {marker} missing value/u32/offset/slot/str")
    if re.fullmatch(r"str_[0-9A-Fa-f]{8}", value_text):
        value = parse_str_label(value_text)
    else:
        value = parse_int(value_text)
    if not (0 <= kind <= 0xFF and 0 <= argc <= 0xFF):
        raise AsmError(f"line {lineno}: kind/argc out of byte range")
    add_inline_string(state, kv, value, lineno, "@value")
    if assigned_string is not None and assigned_string != value:
        raise AsmError(f"line {lineno}: string assignment does not match {marker} value")
    type_code = MNEMONIC_TO_TYPE[mnemonic]
    state.tokens.append((offset, struct.pack("<BBHI", kind, argc, type_code, value & 0xFFFFFFFF)))


def parse_code_unknown_token_line(line: str, state: AsmState, lineno: int, marker: str = "@unknown_token") -> None:
    offset, rest = parse_code_token_offset(line, marker, lineno)
    fields = split_fields(rest)
    kv = parse_kv_fields(fields)
    kind = parse_int(require_kv(kv, ("kind", "k"), lineno, marker))
    argc = parse_int(require_kv(kv, ("argc", "a"), lineno, marker))
    opcode_or_type = parse_int(require_kv(kv, ("opcode_or_type", "op"), lineno, marker))
    value = parse_int(require_kv(kv, ("value", "v"), lineno, marker))
    state.tokens.append((offset, struct.pack("<BBHI", kind & 0xFF, argc & 0xFF, opcode_or_type & 0xFFFF, value & 0xFFFFFFFF)))


COMMAND_NAME_TO_ID: dict[str, int] = {}
for _cmd_id, _cmd in COMMANDS.items():
    COMMAND_NAME_TO_ID.setdefault(_cmd.name, _cmd_id)
    if _cmd.name.endswith("("):
        COMMAND_NAME_TO_ID.setdefault(_cmd.name[:-1], _cmd_id)

TEXT_INLINE_COMMANDS = tuple(
    sorted(
        {
            ((_cmd.name[:-1] if _cmd.name.endswith("(") else _cmd.name), _cmd.name)
            for _cmd in COMMANDS.values()
            if _cmd.name.startswith("\\")
        },
        key=lambda item: len(item[0]),
        reverse=True,
    )
)

INFIX_PRECEDENCE = {
    "||": 1,
    "&&": 2,
    "|": 3,
    "^": 4,
    "&": 5,
    "==": 6,
    "!=": 6,
    "<": 7,
    "<=": 7,
    ">": 7,
    ">=": 7,
    "<<": 8,
    ">>": 8,
    "+": 9,
    "-": 9,
    "*": 10,
    "/": 10,
    "%": 10,
}
INFIX_OPERATORS_LONGEST_FIRST = tuple(sorted(INFIX_PRECEDENCE, key=len, reverse=True))

UNARY_OPERATOR_NAMES = {"!", "~", "@@", "+", "-", "@"}
BINARY_OPERATOR_TO_ID = {
    _cmd.name: _cmd_id
    for _cmd_id, _cmd in COMMANDS.items()
    if _cmd.name in INFIX_PRECEDENCE and _cmd.left_min > 0
}
UNARY_OPERATOR_TO_ID = {
    _cmd.name: _cmd_id
    for _cmd_id, _cmd in COMMANDS.items()
    if _cmd.name in UNARY_OPERATOR_NAMES and _cmd.left_min == 0
}


def token_raw32(value: int, argc: int = 0xCC) -> bytes:
    return struct.pack("<BBHI", 0x00, argc & 0xFF, 0x8000, value & 0xFFFFFFFF)


def token_strref(offset: int) -> bytes:
    return struct.pack("<BBHI", 0x01, 0xCC, 0x8002, offset & 0xFFFFFFFF)


def token_slot(type_code: int, slot: int) -> bytes:
    return struct.pack("<BBHI", 0x00, 0x00, type_code & 0xFFFF, slot & 0xFFFFFFFF)


def token_command(cmd_id: int, argc: int, value: int = 0) -> bytes:
    return struct.pack("<BBHI", 0x08, argc & 0xFF, cmd_id & 0xFFFF, value & 0xFFFFFFFF)


def compile_command_token(name: str, argc: int, lineno: int, state: AsmState | None = None) -> bytes:
    cmd_id = COMMAND_NAME_TO_ID.get(name)
    if cmd_id is None:
        raise AsmError(f"line {lineno}: unknown readable command/operator {name!r}")
    value = state.current_command_value if state is not None else 0
    return token_command(cmd_id, argc, value)


def compile_infix_operator_token(name: str, lineno: int, state: AsmState | None = None) -> bytes:
    cmd_id = BINARY_OPERATOR_TO_ID.get(name)
    if cmd_id is None:
        raise AsmError(f"line {lineno}: unknown infix operator {name!r}")
    value = state.current_command_value if state is not None else 0
    return token_command(cmd_id, 1, value)


def compile_unary_operator_token(name: str, lineno: int, state: AsmState | None = None) -> bytes:
    cmd_id = UNARY_OPERATOR_TO_ID.get(name)
    if cmd_id is None:
        raise AsmError(f"line {lineno}: unknown unary operator {name!r}")
    value = state.current_command_value if state is not None else 0
    return token_command(cmd_id, 1, value)


class ExprParser:
    def __init__(self, text: str, lineno: int, state: AsmState | None = None):
        self.text = text
        self.lineno = lineno
        self.state = state
        self.pos = 0

    def at_end(self) -> bool:
        self.skip_ws()
        return self.pos >= len(self.text)

    def skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def peek(self, text: str) -> bool:
        self.skip_ws()
        return self.text.startswith(text, self.pos)

    def consume(self, text: str) -> bool:
        if self.peek(text):
            self.pos += len(text)
            return True
        return False

    def expect(self, text: str) -> None:
        if not self.consume(text):
            raise AsmError(f"line {self.lineno}: expected {text!r} near {self.text[self.pos:self.pos+24]!r}")

    def parse_identifier(self) -> str:
        self.skip_ws()
        start = self.pos
        if self.pos < len(self.text) and self.text[self.pos] in "%\\":
            self.pos += 1
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch.isspace() or ch in "(),[]":
                break
            if any(self.text.startswith(op, self.pos) for op in INFIX_OPERATORS_LONGEST_FIRST):
                break
            if ch == "=":
                break
            self.pos += 1
        if self.pos == start:
            raise AsmError(f"line {self.lineno}: expected identifier near {self.text[self.pos:self.pos+24]!r}")
        return self.text[start:self.pos]

    def parse_number_or_label(self) -> bytes:
        self.skip_ws()
        match = re.match(r"-?0x[0-9A-Fa-f]+|-?\d+|str_[0-9A-Fa-f]{8}", self.text[self.pos:])
        if not match:
            raise AsmError(f"line {self.lineno}: expected value near {self.text[self.pos:self.pos+24]!r}")
        value_text = match.group(0)
        self.pos += len(value_text)
        if value_text.startswith("str_"):
            return token_strref(parse_str_label(value_text))
        value = parse_int(value_text)
        argc = 0x00 if value < 0 or (value & 0x80000000) else 0xCC
        return token_raw32(value, argc=argc)

    def parse_string_literal(self) -> bytes:
        self.skip_ws()
        if self.pos >= len(self.text) or self.text[self.pos] != '"':
            raise AsmError(f"line {self.lineno}: expected string near {self.text[self.pos:self.pos+24]!r}")
        start = self.pos
        self.pos += 1
        escape = False
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            self.pos += 1
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                literal = self.text[start:self.pos]
                if self.state is None:
                    raise AsmError(f"line {self.lineno}: string literal needs source-format state")
                raw = parse_bytes_literal(literal, self.state.encoding)
                if not raw.endswith(b"\x00"):
                    raw += b"\x00"
                return token_strref(add_auto_string_entry(self.state, raw, self.lineno))
        raise AsmError(f"line {self.lineno}: unterminated string literal")

    def parse_call_args(self) -> list[bytes]:
        args: list[bytes] = []
        self.expect("(")
        if self.consume(")"):
            return args
        while True:
            args.append(self.parse_expression())
            if self.consume(")"):
                return args
            self.expect(",")

    def parse_primary(self) -> bytes:
        self.skip_ws()
        if self.consume("("):
            out = self.parse_expression()
            self.expect(")")
            return out
        if self.peek("G[") or self.peek("L["):
            is_global = self.consume("G")
            if not is_global:
                self.expect("L")
            self.expect("[")
            self.skip_ws()
            match = re.match(r"\d+", self.text[self.pos:])
            if not match:
                raise AsmError(f"line {self.lineno}: slot index must be a plain integer")
            slot = int(match.group(0), 10)
            self.pos += len(match.group(0))
            self.expect("]")
            return token_slot(0x8010 if is_global else 0x8110, slot)
        if self.peek("@("):
            self.expect("@")
            self.expect("(")
            inner = self.parse_expression()
            self.expect(")")
            return inner + compile_command_token("@", 1, self.lineno, self.state)
        ch = self.text[self.pos] if self.pos < len(self.text) else ""
        if ch == '"':
            return self.parse_string_literal()
        if ch.isdigit() or ch == "-" or self.peek("str_"):
            return self.parse_number_or_label()
        name = self.parse_identifier()
        if self.peek("("):
            args = self.parse_call_args()
            argc = len(args)
            return b"".join(args) + compile_command_token(name, argc, self.lineno, self.state)
        raise AsmError(f"line {self.lineno}: unsupported bare identifier {name!r}")

    def parse_postfix(self) -> bytes:
        out = self.parse_primary()
        while self.consume("["):
            index = self.parse_expression()
            self.expect("]")
            out = out + index + compile_command_token("[", 1, self.lineno, self.state)
        return out

    def parse_unary(self) -> bytes:
        self.skip_ws()
        if self.pos < len(self.text) and self.text[self.pos] == "-":
            next_ch = self.text[self.pos + 1] if self.pos + 1 < len(self.text) else ""
            if next_ch.isdigit():
                return self.parse_postfix()
        for op in ("!", "~", "@@"):
            if self.consume(op):
                return self.parse_unary() + compile_unary_operator_token(op, self.lineno, self.state)
        for op in ("-", "+"):
            if self.consume(op):
                return self.parse_unary() + compile_unary_operator_token(op, self.lineno, self.state)
        return self.parse_postfix()

    def parse_expression(self, min_prec: int = 1) -> bytes:
        left = self.parse_unary()
        while True:
            self.skip_ws()
            op = None
            for candidate in INFIX_OPERATORS_LONGEST_FIRST:
                if self.text.startswith(candidate, self.pos):
                    op = candidate
                    break
            if op is None:
                break
            prec = INFIX_PRECEDENCE[op]
            if prec < min_prec:
                break
            self.pos += len(op)
            right = self.parse_expression(prec + 1)
            left = left + right + compile_infix_operator_token(op, self.lineno, self.state)
        return left


def find_top_level_assignment(code: str) -> int:
    depth = 0
    in_quote = False
    escape = False
    for index, ch in enumerate(code):
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == "=" and depth == 0:
            prev_ch = code[index - 1] if index else ""
            next_ch = code[index + 1] if index + 1 < len(code) else ""
            if prev_ch not in "!<>=" and next_ch != "=":
                return index
    return -1


def compile_readable_code(code: str, lineno: int, state: AsmState | None = None) -> bytes:
    code = code.strip()
    if not code:
        return b""
    if code in {"{", "}"}:
        return compile_command_token(code, 0, lineno, state) + compile_command_token(";", 0, lineno, state)
    assign_pos = find_top_level_assignment(code)
    if assign_pos >= 0:
        left = ExprParser(code[:assign_pos], lineno, state).parse_expression()
        right_parser = ExprParser(code[assign_pos + 1 :], lineno, state)
        right = right_parser.parse_expression()
        if not right_parser.at_end():
            raise AsmError(f"line {lineno}: trailing text in assignment")
        return left + right + compile_command_token("=", 1, lineno, state) + compile_command_token(";", 0, lineno, state)
    parser = ExprParser(code, lineno, state)
    out = parser.parse_expression()
    if not parser.at_end():
        raise AsmError(f"line {lineno}: trailing text after expression")
    return out + compile_command_token(";", 0, lineno, state)


def parse_text_literal(text: str, state: AsmState) -> str:
    return parse_json_text(text)


def add_auto_text_token(state: AsmState, text: str, lineno: int) -> bytes:
    return token_strref(add_auto_string_entry(state, text.encode(state.encoding) + b"\x00", lineno))


def split_call_args(text: str, lineno: int) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    in_quote = False
    escape = False
    depth = 0
    for ch in text:
        if in_quote:
            current.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
            current.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        if ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if in_quote:
        raise AsmError(f"line {lineno}: unterminated quoted argument")
    if current or text.strip():
        args.append("".join(current).strip())
    return args


CONTROL_REF_ARG_COMMANDS = {"If2", "Else", "EndIf", "Switch", "Case", "EndSwitch", "Continue", "Break"}


def compile_high_arg(arg: str, command_name: str, state: AsmState, lineno: int, index: int = 0) -> bytes:
    if arg.startswith('"'):
        raw = parse_bytes_literal(arg, state.encoding)
        if not raw.endswith(b"\x00"):
            raw += b"\x00"
        return token_strref(add_auto_string_entry(state, raw, lineno))
    exact = parse_exact_value_arg_token(arg, lineno, index, allow_string=False)
    if exact is not None:
        return exact
    if ":" in arg:
        head = arg.split(":", 1)[0]
        if head in MNEMONIC_TO_TYPE or head in {"raw", "global", "local"}:
            raise AsmError(f"line {lineno}: malformed high-call arg{index}: {arg}")
    if re.fullmatch(r"[GL]\[\d+\]", arg):
        return token_slot(0x8010 if arg[0] == "G" else 0x8110, int(arg[2:-1]))
    if re.fullmatch(r"-?0x[0-9A-Fa-f]+|-?\d+", arg):
        value = parse_int(arg)
        argc = 0x00 if value < 0 or (value & 0x80000000) else 0xCC
        if index == 0 and command_name in CONTROL_REF_ARG_COMMANDS | {"IPageStart", "IGblSelSet"}:
            argc = 0x00
        if index == 0 and command_name == "\\v" and value != 0:
            argc = 0x00
        return token_raw32(value, argc=argc)
    parser = ExprParser(arg, lineno, state)
    out = parser.parse_expression()
    if not parser.at_end():
        raise AsmError(f"line {lineno}: trailing text in argument {index}")
    return out


def split_exact_comment(raw_line: str) -> tuple[str, str | None]:
    in_quote = False
    escape = False
    for index, ch in enumerate(raw_line):
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
            continue
        if ch == ";":
            comment = raw_line[index + 1 :].strip()
            if comment.startswith("exact "):
                return raw_line[:index], comment[len("exact ") :].strip()
            return raw_line[:index], None
    return raw_line, None


def parse_exact_byte(text: str, lineno: int) -> int:
    value_text = text.strip()
    try:
        value = parse_int(value_text) if value_text.lower().startswith("0x") else int(value_text, 16)
    except ValueError as exc:
        raise AsmError(f"line {lineno}: invalid exact byte {text!r}") from exc
    if not (0 <= value <= 0xFF):
        raise AsmError(f"line {lineno}: exact byte out of range: {text}")
    return value


def parse_exact_indices(key: str, singular: str, plural: str, limit: int | None, lineno: int) -> list[int] | None:
    if key.startswith(f"{plural}[") and key.endswith("]"):
        inner = key[len(plural) + 1 : -1].strip()
        if not inner:
            raise AsmError(f"line {lineno}: empty exact {plural} group")
        indices = []
        for part in inner.split(","):
            item = part.strip()
            if not item.isdigit():
                raise AsmError(f"line {lineno}: malformed exact {plural} group index: {item!r}")
            indices.append(int(item))
    elif key.startswith(singular):
        index_text = key[len(singular) :]
        if not index_text.isdigit():
            raise AsmError(f"line {lineno}: malformed exact {singular} index: {key}")
        indices = [int(index_text)]
    else:
        return None
    if limit is not None:
        for index in indices:
            if not (0 <= index < limit):
                raise AsmError(f"line {lineno}: exact {singular} index out of range: {singular}{index}")
    return indices


def parse_exact_overrides(
    exact_text: str,
    argc: int,
    lineno: int,
) -> tuple[dict[int, tuple[int, int]], dict[int, tuple[int, int]]]:
    arg_overrides: dict[int, tuple[int, int]] = {}
    val_overrides: dict[int, tuple[int, int]] = {}
    for field in split_fields(exact_text):
        if "=" not in field:
            raise AsmError(f"line {lineno}: malformed exact field: {field}")
        key, value_text = field.split("=", 1)
        parts = value_text.split(":")
        if len(parts) != 2:
            raise AsmError(f"line {lineno}: exact field {key} must be kind:argc")
        value = (parse_exact_byte(parts[0], lineno), parse_exact_byte(parts[1], lineno))
        arg_indices = parse_exact_indices(key, "arg", "args", argc, lineno)
        if arg_indices is not None:
            for index in arg_indices:
                if index in arg_overrides:
                    raise AsmError(f"line {lineno}: duplicate exact arg override: arg{index}")
                arg_overrides[index] = value
            continue
        val_indices = parse_exact_indices(key, "val", "vals", None, lineno)
        if val_indices is not None:
            for index in val_indices:
                if index in val_overrides:
                    raise AsmError(f"line {lineno}: duplicate exact val override: val{index}")
                val_overrides[index] = value
            continue
        raise AsmError(f"line {lineno}: malformed exact field: {key}")
    return arg_overrides, val_overrides


def apply_exact_val_overrides(raw_tokens: list[bytes], overrides: dict[int, tuple[int, int]], lineno: int) -> None:
    if not overrides:
        return
    value_index = 0
    applied: set[int] = set()
    for token_index, raw in enumerate(raw_tokens):
        _kind, _argc, opcode, _value = struct.unpack("<BBHI", raw)
        if opcode < 0x8000:
            continue
        if value_index in overrides:
            kind, argc = overrides[value_index]
            raw_tokens[token_index] = struct.pack("<BB", kind, argc) + raw[2:]
            applied.add(value_index)
        value_index += 1
    missing = sorted(set(overrides) - applied)
    if missing:
        raise AsmError(f"line {lineno}: exact val index out of range: val{missing[0]}")


def parse_high_call(line: str) -> tuple[str, list[str]] | None:
    open_paren = line.find("(")
    if open_paren <= 0 or not line.endswith(")"):
        return None
    name = line[:open_paren]
    args_text = line[open_paren + 1 : -1]
    if name.startswith("\\\\"):
        name = name[1:]
    return name, split_call_args(args_text or "", 0)


def compile_high_call_line_with_exact(line: str, exact_text: str | None, state: AsmState, lineno: int) -> bool:
    parsed = parse_high_call(line)
    if parsed is None:
        return False
    name, args = parsed
    if name not in COMMAND_NAME_TO_ID:
        return False
    raw_args = [compile_high_arg(arg, name, state, lineno, index) for index, arg in enumerate(args) if arg]
    val_overrides: dict[int, tuple[int, int]] = {}
    if exact_text:
        arg_overrides, val_overrides = parse_exact_overrides(exact_text, len(raw_args), lineno)
        for index, (kind, argc) in arg_overrides.items():
            raw = raw_args[index]
            if len(raw) != TOKEN_SIZE:
                raise AsmError(f"line {lineno}: exact arg{index} can only override a single token argument")
            raw_args[index] = struct.pack("<BB", kind, argc) + raw[2:]
    raw_tokens: list[bytes] = []
    for raw in raw_args:
        if len(raw) % TOKEN_SIZE:
            raise AsmError(f"line {lineno}: compiled argument is not token aligned")
        raw_tokens.extend(raw[raw_offset : raw_offset + TOKEN_SIZE] for raw_offset in range(0, len(raw), TOKEN_SIZE))
    apply_exact_val_overrides(raw_tokens, val_overrides, lineno)
    offset = consume_token_offset(state)
    for raw_token in raw_tokens:
        add_token_entry(state, offset, raw_token)
        offset = None
    add_token_entry(state, None if raw_args else offset, compile_command_token(name, len(raw_args), lineno, state))
    if name in {"IPageEnd", "IPageStart", "CallSub"}:
        add_token_entry(state, None, compile_command_token(";", 0, lineno, state))
    state.current_command_value += max(1, len(line.strip().encode(state.encoding, errors="replace")))
    return True


def compile_high_call_line(line: str, state: AsmState, lineno: int) -> bool:
    return compile_high_call_line_with_exact(line, None, state, lineno)


def find_text_call_end(text: str, open_paren: int, lineno: int, marker: str) -> int:
    depth = 0
    in_quote = False
    escape = False
    for pos in range(open_paren, len(text)):
        ch = text[pos]
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return pos
    raise AsmError(f"line {lineno}: unterminated {marker} text marker")


def compile_inline_text_marker(
    text: str,
    pos: int,
    state: AsmState,
    lineno: int,
) -> tuple[int, list[bytes]] | None:
    if text.startswith("\\R(", pos):
        end = find_text_call_end(text, pos + 2, lineno, "\\R")
        payload = text[pos + 3 : end]
        if "|" not in payload:
            raise AsmError(f"line {lineno}: \\R marker must be \\R(base|ruby)")
        base, ruby = payload.split("|", 1)
        return end + 1, [
            add_auto_text_token(state, base, lineno),
            add_auto_text_token(state, ruby, lineno),
            compile_command_token("\\R(", 2, lineno, state),
        ]

    if text.startswith("\\Name(", pos):
        end = find_text_call_end(text, pos + 5, lineno, "\\Name")
        args = split_call_args(text[pos + 6 : end], lineno)
        if len(args) != 2:
            raise AsmError(f"line {lineno}: \\Name marker must be \\Name(id,variant)")
        return end + 1, [
            token_raw32(parse_int(args[0])),
            token_raw32(parse_int(args[1])),
            compile_command_token("\\Name(", 2, lineno, state),
        ]

    if text.startswith("\\n", pos):
        return pos + 2, [compile_command_token("\\n", 0, lineno, state)]

    for marker, command_name in TEXT_INLINE_COMMANDS:
        prefix = marker + "("
        if not text.startswith(prefix, pos):
            continue
        end = find_text_call_end(text, pos + len(marker), lineno, marker)
        args = split_call_args(text[pos + len(prefix) : end], lineno)
        raw_args = [
            compile_high_arg(arg, command_name, state, lineno, index)
            for index, arg in enumerate(args)
            if arg
        ]
        raw_tokens = [
            raw_arg[raw_offset : raw_offset + TOKEN_SIZE]
            for raw_arg in raw_args
            for raw_offset in range(0, len(raw_arg), TOKEN_SIZE)
        ]
        raw_tokens.append(compile_command_token(command_name, len(raw_args), lineno, state))
        return end + 1, raw_tokens
    return None


def starts_inline_text_marker(text: str, pos: int) -> bool:
    if text.startswith(("\\R(", "\\Name(", "\\n"), pos):
        return True
    return any(text.startswith(marker + "(", pos) for marker, _command_name in TEXT_INLINE_COMMANDS)


def compile_text_line(line: str, state: AsmState, lineno: int) -> bool:
    if not line.startswith("text "):
        return False
    text = parse_text_literal(line[len("text ") :].strip(), state)
    offset = consume_token_offset(state)

    def add_raw(raw: bytes) -> None:
        nonlocal offset
        add_token_entry(state, offset, raw)
        offset = None

    def add_chunk(chunk: str) -> None:
        if not chunk:
            return
        add_raw(add_auto_text_token(state, chunk, lineno))
        add_raw(compile_command_token("MsgOut", 1, lineno, state))

    pos = 0
    chunk_start = 0
    while pos < len(text):
        if text[pos] != "\\" or not starts_inline_text_marker(text, pos):
            pos += 1
            continue
        add_chunk(text[chunk_start:pos])
        marker = compile_inline_text_marker(text, pos, state, lineno)
        assert marker is not None
        next_pos, raw_tokens = marker
        for raw in raw_tokens:
            add_raw(raw)
        pos = next_pos
        chunk_start = pos
    add_chunk(text[chunk_start:])
    state.current_command_value += max(1, len(line.strip().encode(state.encoding, errors="replace")))
    return True


def compile_readable_source_line_with_exact(line: str, exact_text: str | None, state: AsmState, lineno: int) -> bool:
    if line not in {"{", "}"} and find_top_level_assignment(line) < 0:
        return False
    raw = compile_readable_code(line, lineno, state)
    raw_tokens = [raw[index : index + TOKEN_SIZE] for index in range(0, len(raw), TOKEN_SIZE)]
    if exact_text:
        arg_overrides, val_overrides = parse_exact_overrides(exact_text, 0, lineno)
        if arg_overrides:
            raise AsmError(f"line {lineno}: exact argN is only supported on source calls; use valN")
        apply_exact_val_overrides(raw_tokens, val_overrides, lineno)
    offset = consume_token_offset(state)
    for index, raw_token in enumerate(raw_tokens):
        add_token_entry(state, offset if index == 0 else None, raw_token)
        offset = None
    state.current_command_value += max(1, len(line.strip().encode(state.encoding, errors="replace")))
    return True


def compile_readable_source_line(line: str, state: AsmState, lineno: int) -> bool:
    return compile_readable_source_line_with_exact(line, None, state, lineno)


def logical_asm_lines(text: str) -> list[tuple[int, str]]:
    raw_lines = text.splitlines()
    result: list[tuple[int, str]] = []
    index = 0
    while index < len(raw_lines):
        raw_line = raw_lines[index]
        lineno = index + 1
        stripped = strip_comment(raw_line).strip()
        if stripped.startswith("call "):
            parts = [stripped]
            index += 1
            while index < len(raw_lines):
                child = strip_comment(raw_lines[index]).strip()
                if re.match(r"(?:arg|raw|terminated)\d+=", child):
                    parts.append(child)
                    index += 1
                    continue
                break
            result.append((lineno, " ".join(parts)))
            continue
        result.append((lineno, raw_line))
        index += 1
    return result


def looks_like_source_call(line: str) -> bool:
    return re.match(r"\\?[^\s(),=]+\s*\(.*\)$", line) is not None


def parse_asm(text: str, encoding_override: str | None = None) -> AsmState:
    state = AsmState(encoding=encoding_override or "cp932")
    section = ""
    metadata_markers = (
        "@pool ",
        "@label ",
        "@ref ",
        "@c ",
        "@v ",
        "@u ",
        "@command ",
        "@value ",
        "@unknown_token ",
    )
    for lineno, raw_line in logical_asm_lines(text):
        raw_stripped = raw_line.strip()
        exact_text = None
        if any(marker in raw_stripped for marker in metadata_markers):
            line = raw_stripped
        else:
            code_part, exact_text = split_exact_comment(raw_line)
            line = strip_comment(code_part).strip()
        if not line:
            continue
        if line.endswith(":") and not line.startswith("str_") and not line.startswith("tok_"):
            continue
        if line.startswith(".format "):
            fmt = parse_json_text(line[len(".format ") :].strip())
            if fmt in {"kyoupri-gsx1-source", "kyoupri-gsx1-compact"}:
                state.source_format = True
            continue
        if line.startswith(".source "):
            continue
        if line.startswith(".encoding "):
            if encoding_override is None:
                state.encoding = parse_json_text(line[len(".encoding ") :].strip())
            continue
        if line.startswith(".version "):
            state.version = parse_int(line[len(".version ") :].strip())
            continue
        if line.startswith(".file_size "):
            state.file_size = parse_int(line[len(".file_size ") :].strip())
            continue
        if line.startswith(".segment "):
            parse_segment(line, state, lineno)
            continue
        if line.startswith(".section "):
            section = line[len(".section ") :].strip()
            continue
        if "@pool " in line or line.startswith(".pool "):
            parse_pool_line(line, state, lineno)
            continue
        if "@label " in line:
            parse_inline_label_line(line, state, lineno)
            continue
        if "@ref " in line:
            parse_inline_ref_line(line, state, lineno)
            continue
        if "@c " in line:
            parse_code_command_line(line, state, lineno, "@c")
            continue
        if "@v " in line:
            parse_code_value_line(line, state, lineno, "@v")
            continue
        if "@u " in line:
            parse_code_unknown_token_line(line, state, lineno, "@u")
            continue
        if "@command " in line:
            parse_code_command_line(line, state, lineno)
            continue
        if "@value " in line:
            parse_code_value_line(line, state, lineno)
            continue
        if "@unknown_token " in line:
            parse_code_unknown_token_line(line, state, lineno)
            continue
        if section == "strings" and (".string " in line or ".str_alias " in line):
            parse_string_line(line, state, lineno)
            continue
        if line.startswith(".label_entry ") or line.startswith(".label ") or line.startswith("label "):
            parse_label_entry(line, state, lineno)
            continue
        if line.startswith(".ref ") or line.startswith("ref "):
            parse_ref_line(line, state, lineno)
            continue
        if section == "code" and line.startswith("tok_") and line.endswith(":"):
            state.pending_token_offset = parse_tok_label(line[:-1])
            if state.token_offset_map is not None:
                state.token_offset_map[state.pending_token_offset] = state.token_cursor
            continue
        if section == "code" and line.startswith("call "):
            parse_call_line(line, state, lineno)
            continue
        if section == "code" and line.startswith("end"):
            parse_end_line(line, state, lineno)
            continue
        if section == "code" and compile_text_line(line, state, lineno):
            continue
        if section == "code" and parse_plain_string_assignment(line, state, lineno):
            continue
        if section == "code" and (".command " in line or line.startswith("cmd ")):
            parse_command_line(line, state, lineno)
            continue
        if section == "code" and (".value " in line or line.startswith("val ")):
            parse_value_line(line, state, lineno)
            continue
        if section == "code" and (".unknown_token " in line or line.startswith("unknown_token ")):
            parse_unknown_token_line(line, state, lineno)
            continue
        if section == "code" and state.source_format and exact_text is not None:
            if compile_high_call_line_with_exact(line, exact_text, state, lineno):
                continue
            if compile_readable_source_line_with_exact(line, exact_text, state, lineno):
                continue
            raise AsmError(f"line {lineno}: exact comment is only supported on source calls or readable statements")
        if section == "code" and state.source_format and compile_readable_source_line(line, state, lineno):
            continue
        if section == "code" and compile_high_call_line(line, state, lineno):
            continue
        if section == "code" and state.source_format and looks_like_source_call(line):
            raise AsmError(f"line {lineno}: unsupported or malformed source call: {line}")
        if section == "code":
            continue
        raise AsmError(f"line {lineno}: unsupported or misplaced line: {line}")
    return state


def build_segment_bytes(size: int, entries: list[tuple[int, bytes]], unit: int, name: str) -> bytes:
    data = bytearray(size)
    covered = bytearray(size)
    for offset, raw in entries:
        if offset < 0 or offset + len(raw) > size:
            raise AsmError(f"{name}: entry outside segment at 0x{offset:08X}")
        if any(covered[offset : offset + len(raw)]):
            raise AsmError(f"{name}: overlapping entry at 0x{offset:08X}")
        data[offset : offset + len(raw)] = raw
        covered[offset : offset + len(raw)] = b"\x01" * len(raw)
    if unit > 1:
        for offset in range(0, size, unit):
            if not all(covered[offset : offset + unit]):
                raise AsmError(f"{name}: missing entry at 0x{offset:08X}")
    elif not all(covered):
        missing = covered.find(0)
        raise AsmError(f"{name}: missing bytes at 0x{missing:08X}")
    return bytes(data)


def build_label_bytes(size: int, labels: list[tuple[int, int, int, int, int]]) -> bytes:
    entries: list[tuple[int, bytes]] = []
    for index, hash_next, name_offset, name_len, token_offset in labels:
        raw = struct.pack(
            "<4I",
            hash_next & 0xFFFFFFFF,
            to_u32(name_offset),
            to_u32(name_len),
            to_u32(token_offset),
        )
        entries.append((index * LABEL_ENTRY_SIZE, raw))
    return build_segment_bytes(size, entries, LABEL_ENTRY_SIZE, "label table")


def build_ref_bytes(size: int, refs: list[tuple[int, int, int]]) -> bytes:
    entries: list[tuple[int, bytes]] = []
    for index, kind_or_index, token_offset in refs:
        raw = struct.pack("<2I", kind_or_index & 0xFFFFFFFF, to_u32(token_offset))
        entries.append((index * REF_ENTRY_SIZE, raw))
    return build_segment_bytes(size, entries, REF_ENTRY_SIZE, "ref table")


def require_segment(state: AsmState, name: str) -> Segment:
    if name not in state.segments:
        raise AsmError(f"missing .segment {name}")
    return state.segments[name]


def compact_string_pool(state: AsmState) -> tuple[bytes, dict[int, int]]:
    assert state.strings is not None
    old_to_new: dict[int, int] = {}
    out = bytearray()
    for old_offset, raw in sorted(state.strings, key=lambda item: item[0]):
        if old_offset in old_to_new:
            continue
        old_to_new[old_offset] = len(out)
        out.extend(raw)
    return bytes(out), old_to_new


def relocate_token_strings(tokens: list[tuple[int, bytes]], string_offsets: dict[int, int]) -> list[tuple[int, bytes]]:
    relocated: list[tuple[int, bytes]] = []
    for offset, raw in tokens:
        kind, argc, opcode_or_type, value = struct.unpack("<BBHI", raw)
        if opcode_or_type == 0x8002:
            if value not in string_offsets:
                raise AsmError(f"token at 0x{offset:08X}: missing string for str_{value:08X}")
            raw = struct.pack("<BBHI", kind, argc, opcode_or_type, string_offsets[value])
        relocated.append((offset, raw))
    return relocated


def remap_token_offset(state: AsmState, old_offset: int) -> int:
    assert state.token_offset_map is not None
    if old_offset < 0:
        return old_offset
    if old_offset in state.token_offset_map:
        return state.token_offset_map[old_offset]
    candidates = [old for old in state.token_offset_map if old > old_offset]
    if candidates:
        return state.token_offset_map[min(candidates)]
    raise AsmError(f"cannot remap deleted token offset 0x{old_offset:08X}")


def remap_labels(
    state: AsmState,
    string_offsets: dict[int, int],
) -> list[tuple[int, int, int, int, int]]:
    assert state.labels is not None
    labels: list[tuple[int, int, int, int, int]] = []
    for index, hash_next, name_offset, name_len, token_offset in state.labels:
        if name_offset >= 0:
            if name_offset not in string_offsets:
                raise AsmError(f"label {index}: missing name string str_{name_offset:08X}")
            name_offset = string_offsets[name_offset]
        labels.append((index, hash_next, name_offset, name_len, remap_token_offset(state, token_offset)))
    return labels


def remap_refs(state: AsmState) -> list[tuple[int, int, int]]:
    assert state.refs is not None
    refs: list[tuple[int, int, int]] = []
    for index, kind_or_index, token_offset in state.refs:
        try:
            refs.append((index, kind_or_index, remap_token_offset(state, token_offset)))
        except AsmError:
            continue
    return refs


def assemble_state_reflow(state: AsmState) -> bytes:
    if state.version is None:
        raise AsmError("missing .version")
    strings, string_offsets = compact_string_pool(state)
    tokens_list = relocate_token_strings(sorted(state.tokens, key=lambda item: item[0]), string_offsets)
    token_size = 0 if not tokens_list else max(offset + len(raw) for offset, raw in tokens_list)
    labels_list = remap_labels(state, string_offsets)
    refs_list = remap_refs(state)
    labels_size = len(labels_list) * LABEL_ENTRY_SIZE
    refs_size = len(refs_list) * REF_ENTRY_SIZE
    labels = build_label_bytes(labels_size, labels_list)
    refs = build_ref_bytes(refs_size, refs_list)
    tokens = build_segment_bytes(token_size, tokens_list, TOKEN_SIZE, "token stream")

    strings_offset = HEADER_SIZE
    labels_offset = strings_offset + len(strings)
    refs_offset = labels_offset + len(labels)
    tokens_offset = refs_offset + len(refs)
    file_size = tokens_offset + len(tokens)
    out = bytearray(file_size)
    struct.pack_into(
        "<4s9I",
        out,
        0,
        b"GSX1",
        state.version & 0xFFFFFFFF,
        strings_offset,
        len(strings),
        labels_offset,
        len(labels),
        refs_offset,
        len(refs),
        tokens_offset,
        len(tokens),
    )
    out[strings_offset : strings_offset + len(strings)] = strings
    out[labels_offset : labels_offset + len(labels)] = labels
    out[refs_offset : refs_offset + len(refs)] = refs
    out[tokens_offset : tokens_offset + len(tokens)] = tokens
    return bytes(out)


def assemble_state(state: AsmState) -> bytes:
    if state.version is None:
        raise AsmError("missing .version")
    if state.source_format:
        return assemble_state_reflow(state)
    strings_seg = require_segment(state, "strings")
    labels_seg = require_segment(state, "labels")
    refs_seg = require_segment(state, "refs")
    tokens_seg = require_segment(state, "tokens")
    if strings_seg.offset != HEADER_SIZE:
        raise AsmError(f"strings segment must start at 0x{HEADER_SIZE:08X}")

    strings = build_segment_bytes(strings_seg.size, state.strings, 1, "string pool")
    labels = build_label_bytes(labels_seg.size, state.labels)
    refs = build_ref_bytes(refs_seg.size, state.refs)
    tokens = build_segment_bytes(tokens_seg.size, state.tokens, TOKEN_SIZE, "token stream")

    computed_size = max(
        HEADER_SIZE,
        strings_seg.offset + strings_seg.size,
        labels_seg.offset + labels_seg.size,
        refs_seg.offset + refs_seg.size,
        tokens_seg.offset + tokens_seg.size,
    )
    file_size = state.file_size if state.file_size is not None else computed_size
    if file_size < computed_size:
        raise AsmError(".file_size is smaller than the declared segments")
    out = bytearray(file_size)
    struct.pack_into(
        "<4s9I",
        out,
        0,
        b"GSX1",
        state.version & 0xFFFFFFFF,
        strings_seg.offset,
        strings_seg.size,
        labels_seg.offset,
        labels_seg.size,
        refs_seg.offset,
        refs_seg.size,
        tokens_seg.offset,
        tokens_seg.size,
    )
    out[strings_seg.offset : strings_seg.offset + strings_seg.size] = strings
    out[labels_seg.offset : labels_seg.offset + labels_seg.size] = labels
    out[refs_seg.offset : refs_seg.offset + refs_seg.size] = refs
    out[tokens_seg.offset : tokens_seg.offset + tokens_seg.size] = tokens
    return bytes(out)


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(input_path.name + ".rebuild")


def resolve_outputs(inputs: list[Path], output: Path | None) -> dict[Path, Path]:
    if output is None:
        return {path: default_output_path(path) for path in inputs}
    if len(inputs) == 1:
        return {inputs[0]: output}
    output.mkdir(parents=True, exist_ok=True)
    return {path: output / (path.name + ".rebuild") for path in inputs}


def assemble_file(input_path: Path, output_path: Path, encoding: str | None = None) -> None:
    state = parse_asm(input_path.read_text(encoding="utf-8"), encoding)
    data = assemble_state(state)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assemble KyouPri GSX1 asm files.")
    parser.add_argument("inputs", nargs="+", type=Path, help="Input .asm.txt files.")
    parser.add_argument("-o", "--output", type=Path, help="Output file for one input, or output directory for many.")
    parser.add_argument("--encoding", help="Override script string encoding. Defaults to asm .encoding, then cp932.")
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
        assemble_file(input_path, output_path, args.encoding)
        print(f"{input_path} -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
