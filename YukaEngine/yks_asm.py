#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, local
import time


MAGIC_PREFIX = b"YKS001"
HEADER_SIZE = 0x30
SCRIPT_ENCODING = "cp932"
ASM_FORMAT_VERSION = "LOVE-DERE-YKS-ASM-3"
XOR_AA_TABLE = bytes(value ^ 0xAA for value in range(256))
CONTROL_FLOW_TOKENS = {"{", "}", "else"}
NON_REUSABLE_CLEAN_TOKENS = {"{", "}"}
OPERATOR_TOKENS = {"=", "+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "&&", "||"}
COMMAND_F2_FORCE_ZERO = {
    "AlreadySkipModeCheck",
    "AutoModeCheck",
    "AutoSpeedGet",
    "ExclusiveCheck",
    "FullScreenModeGet",
    "KeyWaitFunctionGet",
    "NumberToString",
    "Random",
    "RGB",
    "SaveDataCheck",
    "SkipModeCheck",
    "SoundGroupVolumeGet",
    "TextBoxStringGet",
    "TextSpeedGet",
    "TimerGet",
}


class ParseError(RuntimeError):
    pass


@dataclass(slots=True)
class Node:
    type: int
    f1: int
    f2: int
    f3: int


@dataclass(slots=True)
class PoolEntry:
    label: str
    offset: int
    kind: str
    value: object
    size: int


@dataclass(slots=True)
class PoolAlias:
    label: str
    offset: int
    target: str
    delta: int


@dataclass(slots=True)
class BatchResult:
    path: Path
    ok: bool
    detail: str


@dataclass(slots=True)
class CleanPrecreateAction:
    kind: str
    visible: str
    role: str
    target_line_index: int | None = None
    target_token_index: int | None = None
    patch_zero_line_index: int | None = None
    reuse_existing: bool | None = None
    command_name: str | None = None
    allocate_hidden_temp: bool = False


@dataclass(slots=True)
class NodeLookupCache:
    """Per-assembly lookup cache for clean-mode node reuse.

    The clean assembler repeatedly asks questions like "which existing node
    displays as this visible token for this role?".  The old implementation
    answered every query by scanning the whole node table.  Large scripts do
    this thousands of times, so keeping an incremental, per-assembly cache
    avoids most repeated O(N) scans while staying local to the worker thread.
    """

    by_visible_role: dict[tuple[str, str], list[int]] = field(default_factory=dict)
    visible_scanned_count: dict[tuple[str, str], int] = field(default_factory=dict)
    temp_slots: dict[int, int] = field(default_factory=dict)
    temp_scanned_count: int = 0
    summary_cache: dict[int, str] = field(default_factory=dict)

    def invalidate(self) -> None:
        self.by_visible_role.clear()
        self.visible_scanned_count.clear()
        self.temp_slots.clear()
        self.temp_scanned_count = 0
        self.summary_cache.clear()

    def node_summary(self, s4: bytes | bytearray, nodes: list[Node], index: int) -> str:
        summary = self.summary_cache.get(index)
        if summary is None:
            summary = node_summary_data(s4, nodes[index])
            self.summary_cache[index] = summary
        return summary

    def visible_candidates(
        self,
        visible: str,
        role: str,
        nodes: list[Node],
        s4: bytes | bytearray,
    ) -> list[int]:
        key = (visible, role)
        scanned = self.visible_scanned_count.get(key, 0)
        if scanned > len(nodes):
            self.invalidate()
            scanned = 0
        candidates = self.by_visible_role.setdefault(key, [])
        for index in range(scanned, len(nodes)):
            if node_matches_visible_role(s4, nodes[index], visible, role, self, nodes, index):
                candidates.append(index)
        self.visible_scanned_count[key] = len(nodes)
        return candidates

    def temp_slot_index(self, nodes: list[Node], slot: int) -> int | None:
        if self.temp_scanned_count > len(nodes):
            self.invalidate()
        for index in range(self.temp_scanned_count, len(nodes)):
            node = nodes[index]
            if node.type == 10 and node.f2 not in self.temp_slots:
                self.temp_slots[node.f2] = index
        self.temp_scanned_count = len(nodes)
        return self.temp_slots.get(slot)


_NODE_LOOKUP_CONTEXT = local()


def current_node_lookup_cache() -> NodeLookupCache | None:
    return getattr(_NODE_LOOKUP_CONTEXT, "cache", None)


@dataclass(slots=True)
class YKSFile:
    version: int
    unknown_08: int
    unknown_2c: int
    temp_count: int
    s1: list[int]
    s2: list[Node]
    s4: bytes

    @classmethod
    def load(cls, path: Path | str) -> "YKSFile":
        data = Path(path).read_bytes()
        return cls.from_bytes(data, source=Path(path))

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview, source: Path | str | None = None) -> "YKSFile":
        data = bytes(data)
        if data[:6] != MAGIC_PREFIX:
            source_text = f": {Path(source)}" if source is not None else ""
            raise ParseError(f"not a YKS file{source_text}")
        version = struct.unpack_from("<H", data, 0x06)[0]
        unknown_08 = struct.unpack_from("<Q", data, 0x08)[0]
        s1_off, s1_count, s2_off, s2_count, s4_off, s4_size, temp_count, unknown_2c = struct.unpack_from(
            "<8I", data, 0x10
        )
        s1 = list(struct.unpack_from(f"<{s1_count}I", data, s1_off))
        s2 = [Node(*struct.unpack_from("<IIII", data, s2_off + index * 0x10)) for index in range(s2_count)]
        s4 = data[s4_off : s4_off + s4_size]
        if version == 1:
            s4 = s4.translate(XOR_AA_TABLE)
        return cls(
            version=version,
            unknown_08=unknown_08,
            unknown_2c=unknown_2c,
            temp_count=temp_count,
            s1=s1,
            s2=s2,
            s4=s4,
        )

    def to_bytes(self) -> bytes:
        s1_off = HEADER_SIZE
        s2_off = s1_off + len(self.s1) * 4
        s4_off = s2_off + len(self.s2) * 0x10
        buffer = bytearray(s4_off + len(self.s4))
        buffer[:6] = MAGIC_PREFIX
        struct.pack_into("<H", buffer, 0x06, self.version)
        struct.pack_into("<Q", buffer, 0x08, self.unknown_08)
        struct.pack_into(
            "<8I",
            buffer,
            0x10,
            s1_off,
            len(self.s1),
            s2_off,
            len(self.s2),
            s4_off,
            len(self.s4),
            self.temp_count,
            self.unknown_2c,
        )
        for index, value in enumerate(self.s1):
            struct.pack_into("<I", buffer, s1_off + index * 4, value)
        for index, node in enumerate(self.s2):
            struct.pack_into("<IIII", buffer, s2_off + index * 0x10, node.type, node.f1, node.f2, node.f3)
        s4_data = self.s4.translate(XOR_AA_TABLE) if self.version == 1 else self.s4
        buffer[s4_off : s4_off + len(s4_data)] = s4_data
        return bytes(buffer)

    def save(self, path: Path | str) -> None:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(self.to_bytes())


def pool_label(offset: int) -> str:
    return f"@p{offset:08X}"


def node_label(index: int) -> str:
    return f"@n{index:08X}"


def stream_label(offset: int) -> str:
    return f"@L{offset:08X}"


def s1_label(index: int) -> str:
    return f"@w{index:08X}"


def decode_text_lossless(raw: bytes, encoding: str = SCRIPT_ENCODING) -> str | None:
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        return None
    try:
        if text.encode(encoding) != raw:
            return None
    except UnicodeEncodeError:
        return None
    return text


def quote_text(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def parse_int(text: str) -> int:
    return int(text, 0)


def parse_label_index(label: str) -> int:
    if not label.startswith("@") or len(label) < 3:
        raise ParseError(f"invalid label: {label}")
    return int(label[2:], 16)


def pack_dword(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def append_cstr_offset(s4: bytearray, text: str) -> int:
    offset = len(s4)
    s4.extend(text.encode(SCRIPT_ENCODING) + b"\x00")
    return offset


def append_dword_offset(s4: bytearray, value: int) -> int:
    offset = len(s4)
    s4.extend(pack_dword(value))
    return offset


def type1_default_aux_value(visible: str) -> int:
    if visible in OPERATOR_TOKENS:
        return 0x0000FFFF
    return 0xFFFFFFFF


def unpack_dword(raw: bytes) -> int:
    return struct.unpack("<I", raw)[0]


def format_dword(value: int) -> str:
    if value == 0xFFFFFFFF:
        return "-1"
    if value <= 0x7FFFFFFF:
        return str(value)
    signed = value - 0x100000000
    if -999999 <= signed < 0:
        return str(signed)
    return f"0x{value:08X}"


def read_cstring(s4: bytes, offset: int) -> bytes:
    if offset < 0 or offset >= len(s4):
        raise ParseError(f"string offset out of range: 0x{offset:X}")
    end = s4.find(b"\x00", offset)
    if end < 0:
        raise ParseError(f"unterminated cstring at 0x{offset:X}")
    return s4[offset : end + 1]


def split_comment(line: str) -> tuple[str, str]:
    quote: str | None = None
    depth = 0
    escaped = False
    for index, char in enumerate(line):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char in "([{" :
            depth += 1
        elif char in ")]}" and depth > 0:
            depth -= 1
        elif char == ";" and depth == 0:
            return line[:index].rstrip(), line[index + 1 :].strip()
    return line.rstrip(), ""


def split_code_metadata_comment(line: str) -> tuple[str, str]:
    for marker in (" #| ", " ;# "):
        head, sep, tail = line.rpartition(marker)
        if sep:
            return head.rstrip(), tail.strip()
    return line.rstrip(), ""


def split_top_level(text: str, separator: str = ",") -> list[str]:
    items: list[str] = []
    quote: str | None = None
    depth = 0
    escaped = False
    start = 0
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}":
            if depth > 0:
                depth -= 1
            continue
        if char == separator and depth == 0:
            items.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        items.append(tail)
    return items


def split_top_level_assignment(text: str) -> tuple[str, str] | None:
    quote: str | None = None
    depth = 0
    escaped = False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}":
            if depth > 0:
                depth -= 1
            continue
        if char == "=" and depth == 0:
            return text[:index].strip(), text[index + 1 :].strip()
    return None


def parse_value(text: str):
    text = text.strip()
    if not text:
        raise ParseError("empty value")
    if text.startswith("@"):
        return text
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [parse_value(item) for item in split_top_level(inner)]
    if text.startswith(("'", '"')):
        return ast.literal_eval(text)
    if text.lower() == "none":
        return None
    try:
        return parse_int(text)
    except ValueError as exc:
        raise ParseError(f"cannot parse value: {text}") from exc


def parse_call(text: str) -> tuple[str, list[object], dict[str, object]]:
    head, sep, tail = text.partition("(")
    if not sep or not tail.endswith(")"):
        raise ParseError(f"invalid call syntax: {text}")
    name = head.strip()
    body = tail[:-1].strip()
    positional: list[object] = []
    keyword: dict[str, object] = {}
    if body:
        for item in split_top_level(body):
            assignment = split_top_level_assignment(item)
            if assignment is not None:
                left, right = assignment
                keyword[left] = parse_value(right)
            else:
                positional.append(parse_value(item))
    return name, positional, keyword


def split_code_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    quote: str | None = None
    escaped = False
    current: list[str] = []
    for char in text:
        if quote:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
            current.append(char)
            continue
        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if quote:
        raise ParseError(f"unterminated string token: {text}")
    if current:
        tokens.append("".join(current))
    return tokens


def parse_bound_token(text: str) -> tuple[str, int, bool]:
    has_colon = text.endswith(":")
    core = text[:-1] if has_colon else text
    if len(core) < 10 or core[-10:-8] != "@n":
        raise ParseError(f"missing node binding: {text}")
    label = core[-10:]
    visible = core[:-10]
    if not visible:
        raise ParseError(f"missing visible text in token: {text}")
    return visible, parse_label_index(label), has_colon


def ensure_cstr_offset(s4: bytearray, current_offset: int, text: str) -> int:
    raw = text.encode(SCRIPT_ENCODING) + b"\x00"
    if 0 <= current_offset < len(s4):
        end = s4.find(b"\x00", current_offset)
        if end >= 0 and bytes(s4[current_offset : end + 1]) == raw:
            return current_offset
    offset = bytes(s4).find(raw)
    if offset >= 0:
        return offset
    offset = len(s4)
    s4.extend(raw)
    return offset


def ensure_dword_offset(s4: bytearray, current_offset: int, value: int, reuse_existing: bool = True) -> int:
    raw = pack_dword(value)
    if 0 <= current_offset <= len(s4) - 4:
        if bytes(s4[current_offset : current_offset + 4]) != raw:
            s4[current_offset : current_offset + 4] = raw
        return current_offset
    if reuse_existing:
        offset = bytes(s4).find(raw)
        if offset >= 0:
            return offset
    offset = len(s4)
    s4.extend(raw)
    return offset


def parse_string_literal(text: str) -> str:
    value = ast.literal_eval(text)
    if not isinstance(value, str):
        raise ParseError(f"expected string literal: {text}")
    return value


def parse_indexed_value(text: str) -> tuple[str, int]:
    if not text.endswith("]") or "[" not in text:
        raise ParseError(f"expected indexed value: {text}")
    left, _, right = text.rpartition("[")
    name = left.strip()
    index_text = right[:-1].strip()
    if not name or not index_text:
        raise ParseError(f"expected indexed value: {text}")
    return name, parse_int(index_text)


def parse_temp_slot(text: str, prefix: str) -> int:
    if not text.startswith(prefix) or not text.endswith("]"):
        raise ParseError(f"expected {prefix}[n]: {text}")
    index_text = text[len(prefix) : -1].strip()
    if not index_text.startswith("["):
        raise ParseError(f"expected {prefix}[n]: {text}")
    return parse_int(index_text[1:])


def node_name_text(s4: bytes | bytearray, node: Node) -> str:
    raw = read_cstring(s4, node.f1)
    text = decode_text_lossless(raw[:-1])
    if text is None:
        raise ParseError(f"cannot decode node name at 0x{node.f1:X}")
    return text


def node_summary_data(s4: bytes | bytearray, node: Node) -> str:
    def text_at(offset: int) -> str:
        raw = read_cstring(s4, offset)
        text = decode_text_lossless(raw[:-1])
        return quote_text(text) if text is not None else pool_label(offset)

    if node.type == 0:
        return decode_text_lossless(read_cstring(s4, node.f1)[:-1]) or pool_label(node.f1)
    if node.type == 1:
        return decode_text_lossless(read_cstring(s4, node.f1)[:-1]) or pool_label(node.f1)
    if node.type == 2:
        name = decode_text_lossless(read_cstring(s4, node.f1)[:-1]) or pool_label(node.f1)
        return f"{name}={format_dword(unpack_dword(s4[node.f2:node.f2 + 4]))}"
    if node.type == 3:
        name = decode_text_lossless(read_cstring(s4, node.f1)[:-1]) or pool_label(node.f1)
        return f"{name}={text_at(node.f2)}"
    if node.type == 4:
        return format_dword(unpack_dword(s4[node.f2:node.f2 + 4]))
    if node.type == 5:
        return text_at(node.f2)
    if node.type == 6:
        return decode_text_lossless(read_cstring(s4, node.f1)[:-1]) or pool_label(node.f1)
    if node.type == 7:
        return decode_text_lossless(read_cstring(s4, node.f1)[:-1]) or pool_label(node.f1)
    if node.type == 8:
        name = decode_text_lossless(read_cstring(s4, node.f1)[:-1]) or pool_label(node.f1)
        return f"{name}[{format_dword(unpack_dword(s4[node.f3:node.f3 + 4]))}]"
    if node.type == 9:
        name = decode_text_lossless(read_cstring(s4, node.f1)[:-1]) or pool_label(node.f1)
        return f"{name}[{format_dword(unpack_dword(s4[node.f3:node.f3 + 4]))}]"
    if node.type == 10:
        return f"temp[{node.f2}]"
    if node.type == 11:
        return f"temp_num[{node.f2}]"
    if node.type == 12:
        return f"temp_str[{node.f2}]"
    return f"type{node.type}(f1={node.f1}, f2={node.f2}, f3={node.f3})"


def format_code_token(s4: bytes | bytearray, nodes: list[Node], node_index: int) -> str:
    return f"{node_summary_data(s4, nodes[node_index])}{node_label(node_index)}"


def format_pretty_code_token(s4: bytes | bytearray, nodes: list[Node], node_index: int) -> str:
    return node_summary_data(s4, nodes[node_index])


def format_code_metadata(node_indexes: list[int]) -> str:
    return ",".join(f"{index:X}" for index in node_indexes)


def parse_code_metadata(comment: str) -> list[int]:
    text = comment.strip()
    if not text:
        return []
    if text.startswith("#"):
        body = text[1:].strip()
    else:
        body = text
    if body.lower().startswith("ids="):
        body = body[4:].strip()
    if not body:
        return []
    values: list[int] = []
    for item in body.replace(",", " ").split():
        values.append(int(item, 16))
    return values


def node_matches_visible_role(
    s4: bytes | bytearray,
    node: Node,
    visible: str,
    role: str,
    cache: NodeLookupCache | None = None,
    nodes: list[Node] | None = None,
    node_index: int | None = None,
) -> bool:
    if cache is not None and nodes is not None and node_index is not None:
        summary = cache.node_summary(s4, nodes, node_index)
    else:
        summary = node_summary_data(s4, node)
    if summary != visible:
        return False
    if role in {"cmd", "assign-op", "zero-cmd"}:
        return node.type == 0
    if role == "label":
        return node.type == 1 and not is_special_label_text(visible)
    if role == "token":
        return node.type == 1
    if role == "label-ref":
        return node.type == 1 and not is_special_label_text(visible)
    if role == "value":
        return node.type != 0
    raise ParseError(f"unsupported code token role: {role}")


def find_visible_node_candidates(
    visible: str,
    role: str,
    nodes: list[Node],
    s4: bytes | bytearray,
    cache: NodeLookupCache | None = None,
) -> list[int]:
    cache = cache if cache is not None else current_node_lookup_cache()
    if cache is not None:
        return list(cache.visible_candidates(visible, role, nodes, s4))
    return [
        index
        for index, node in enumerate(nodes)
        if node_matches_visible_role(s4, node, visible, role)
    ]


def is_inline_bound_token(text: str) -> bool:
    return "@n" in text


def append_new_node(nodes: list[Node], node: Node) -> int:
    nodes.append(node)
    return len(nodes) - 1


def type1_aux_offset(s4: bytearray, current_offset: int = -1, value: int = 0xFFFFFFFF) -> int:
    return ensure_dword_offset(s4, current_offset, value, reuse_existing=False)


def is_indexed_family_value(visible: str) -> bool:
    if not visible.endswith("]") or "[" not in visible:
        return False
    try:
        name, _ = parse_indexed_value(visible)
    except ParseError:
        return False
    return name not in {"temp", "temp_num", "temp_str"}


def is_fresh_literal_value(visible: str) -> bool:
    if visible.startswith(('"', "'")):
        return True
    try:
        parse_int(visible)
    except ValueError:
        return False
    return True


def is_temp_slot_value(visible: str) -> bool:
    return visible.startswith("temp[") and visible.endswith("]")


def should_reuse_clean_node(visible: str, role: str) -> bool:
    del role
    return (
        visible not in NON_REUSABLE_CLEAN_TOKENS
        and not is_indexed_family_value(visible)
        and not is_fresh_literal_value(visible)
    )


def ensure_temp_slot_node(nodes: list[Node], slot: int, cache: NodeLookupCache | None = None) -> int:
    cache = cache if cache is not None else current_node_lookup_cache()
    if cache is not None:
        existing_index = cache.temp_slot_index(nodes, slot)
        if existing_index is not None:
            return existing_index
        new_index = append_new_node(nodes, Node(10, 0, slot, 0))
        cache.temp_slots[slot] = new_index
        cache.temp_scanned_count = len(nodes)
        return new_index
    for index, node in enumerate(nodes):
        if node.type == 10 and node.f2 == slot:
            return index
    return append_new_node(nodes, Node(10, 0, slot, 0))


def ensure_assign_op_node(nodes: list[Node], s4: bytearray, cache: NodeLookupCache | None = None) -> int:
    candidates = find_visible_node_candidates("=", "assign-op", nodes, s4, cache)
    if candidates:
        return candidates[0]
    return append_new_node(nodes, Node(0, append_cstr_offset(s4, "="), 0, 0))


def allocate_hidden_temp_slot(nodes: list[Node], next_slot: int, cache: NodeLookupCache | None = None) -> int:
    cache = cache if cache is not None else current_node_lookup_cache()
    slot = next_slot
    if cache is not None:
        while cache.temp_slot_index(nodes, slot) is not None:
            slot += 1
        ensure_temp_slot_node(nodes, slot, cache)
        return slot + 1
    while any(node.type == 10 and node.f2 == slot for node in nodes):
        slot += 1
    ensure_temp_slot_node(nodes, slot)
    return slot + 1


def ensure_temp_slot_coverage(nodes: list[Node], temp_count: int) -> None:
    cache = NodeLookupCache()
    for slot in range(max(temp_count, 0)):
        ensure_temp_slot_node(nodes, slot, cache)


def append_indexed_value_node(
    visible: str,
    nodes: list[Node],
    s4: bytearray,
    *,
    index_before_name: bool,
    hidden_before_node: bool,
) -> int:
    name, index = parse_indexed_value(visible)
    if index_before_name:
        index_offset = append_dword_offset(s4, index)
        name_offset = append_cstr_offset(s4, name)
    else:
        name_offset = append_cstr_offset(s4, name)
        index_offset = append_dword_offset(s4, index)
    node_type = 8 if name in {"Flag", "GlobalFlag"} else 9
    if hidden_before_node:
        append_new_node(nodes, Node(4, 0, index_offset, 0))
        return append_new_node(nodes, Node(node_type, name_offset, 0, index_offset))
    node_index = append_new_node(nodes, Node(node_type, name_offset, 0, index_offset))
    append_new_node(nodes, Node(4, 0, index_offset, 0))
    return node_index


def recent_literal_offset(nodes: list[Node], s4: bytes | bytearray, value: int) -> int | None:
    target = value & 0xFFFFFFFF
    for node in reversed(nodes[-8:]):
        if node.type != 4:
            continue
        if not (0 <= node.f2 <= len(s4) - 4):
            continue
        if unpack_dword(s4[node.f2 : node.f2 + 4]) == target:
            return node.f2
    return None


def create_indexed_family_placeholder(visible: str, nodes: list[Node], s4: bytearray) -> int:
    name, _ = parse_indexed_value(visible)
    node_type = 6 if name in {"Flag", "GlobalFlag"} else 7
    return append_new_node(nodes, Node(node_type, append_cstr_offset(s4, name), 0, 0))


def create_node_for_visible(
    visible: str,
    role: str,
    nodes: list[Node],
    s4: bytearray,
    command_name: str | None = None,
) -> int:
    if role in {"cmd", "assign-op", "zero-cmd"}:
        return append_new_node(nodes, Node(0, append_cstr_offset(s4, visible), 0, 0))
    if role == "label":
        return append_new_node(nodes, Node(1, append_cstr_offset(s4, visible), type1_aux_offset(s4), 0xFFFFFFFF))
    if visible in CONTROL_FLOW_TOKENS or visible in OPERATOR_TOKENS:
        return append_new_node(
            nodes,
            Node(1, append_cstr_offset(s4, visible), type1_aux_offset(s4, value=type1_default_aux_value(visible)), 0xFFFFFFFF),
        )
    if visible.startswith("temp[") and visible.endswith("]"):
        return append_new_node(nodes, Node(10, 0, parse_temp_slot(visible, "temp"), 0))
    if visible.startswith("temp_num[") and visible.endswith("]"):
        return append_new_node(nodes, Node(11, 0, parse_temp_slot(visible, "temp_num"), 0))
    if visible.startswith("temp_str[") and visible.endswith("]"):
        return append_new_node(nodes, Node(12, 0, parse_temp_slot(visible, "temp_str"), 0))
    if visible.startswith(('"', "'")):
        return append_new_node(nodes, Node(5, 0, append_cstr_offset(s4, parse_string_literal(visible)), 0))
    try:
        value = parse_int(visible)
    except ValueError:
        value = None
    if value is not None:
        return append_new_node(nodes, Node(4, 0, append_dword_offset(s4, value), 0))
    if visible.endswith("]") and "[" in visible:
        return append_indexed_value_node(
            visible,
            nodes,
            s4,
            index_before_name=command_name is not None,
            hidden_before_node=command_name is not None,
        )
    if command_name == "ScriptJump" or role == "label-ref":
        return append_new_node(nodes, Node(1, append_cstr_offset(s4, visible), type1_aux_offset(s4), 0xFFFFFFFF))
    return append_new_node(nodes, Node(1, append_cstr_offset(s4, visible), type1_aux_offset(s4), 0xFFFFFFFF))


def bind_or_create_visible_node(
    visible: str,
    role: str,
    nodes: list[Node],
    s4: bytearray,
    bound_ids: list[int],
    cursor: int,
    command_name: str | None = None,
    line_number: int | None = None,
    reuse_existing: bool = True,
) -> tuple[int, int]:
    if cursor < len(bound_ids):
        node_index = bound_ids[cursor]
        if not (0 <= node_index < len(nodes)):
            raise ParseError(f"code metadata node index out of range: {node_index}")
        apply_visible_text_to_node(node_index, visible, nodes, s4)
        return node_index, cursor + 1
    if not reuse_existing:
        return create_node_for_visible(visible, role, nodes, s4, command_name), cursor
    candidates = find_visible_node_candidates(visible, role, nodes, s4)
    if len(candidates) == 1:
        return candidates[0], cursor
    if len(candidates) > 1:
        if len({nodes[index].type for index in candidates}) == 1:
            return candidates[0], cursor
        location = f" on line {line_number}" if line_number is not None else ""
        raise ParseError(
            f"ambiguous code token{location}: {visible}; keep #| metadata for this line"
        )
    return create_node_for_visible(visible, role, nodes, s4, command_name), cursor


def bind_or_create_statement_token(
    token_position: int,
    visible: str,
    role: str,
    planned_token_nodes: dict[int, int] | None,
    nodes: list[Node],
    s4: bytearray,
    bound_ids: list[int],
    cursor: int,
    command_name: str | None = None,
    line_number: int | None = None,
    reuse_existing: bool = True,
) -> tuple[int, int]:
    if planned_token_nodes is not None and token_position in planned_token_nodes:
        node_index = planned_token_nodes[token_position]
        if not (0 <= node_index < len(nodes)):
            raise ParseError(f"planned code token node index out of range: {node_index}")
        apply_visible_text_to_node(node_index, visible, nodes, s4)
        return node_index, cursor
    return bind_or_create_visible_node(
        visible,
        role,
        nodes,
        s4,
        bound_ids,
        cursor,
        command_name,
        line_number,
        reuse_existing,
    )


def apply_visible_text_to_node(node_index: int, visible: str, nodes: list[Node], s4: bytearray) -> None:
    lookup_cache = current_node_lookup_cache()
    if lookup_cache is not None:
        lookup_cache.invalidate()
    node = nodes[node_index]
    if node.type == 0:
        new_f1 = ensure_cstr_offset(s4, node.f1, visible)
        if new_f1 != node.f1:
            node.f1 = new_f1
            node.f3 = 0
        return
    if node.type == 1:
        node.f1 = ensure_cstr_offset(s4, node.f1, visible)
        if visible == "else":
            node.f2 = ensure_dword_offset(s4, node.f2, 0xFFFFFFFF, reuse_existing=False)
        return
    if node.type == 2:
        assignment = split_top_level_assignment(visible)
        if assignment is None:
            raise ParseError(f"expected name=value for node {node_label(node_index)}")
        left, right = assignment
        node.f1 = ensure_cstr_offset(s4, node.f1, left)
        node.f2 = ensure_dword_offset(s4, node.f2, parse_int(right))
        return
    if node.type == 3:
        assignment = split_top_level_assignment(visible)
        if assignment is None:
            raise ParseError(f"expected name=value for node {node_label(node_index)}")
        left, right = assignment
        node.f1 = ensure_cstr_offset(s4, node.f1, left)
        node.f2 = ensure_cstr_offset(s4, node.f2, parse_string_literal(right))
        return
    if node.type == 4:
        node.f2 = ensure_dword_offset(s4, node.f2, parse_int(visible))
        return
    if node.type == 5:
        node.f2 = ensure_cstr_offset(s4, node.f2, parse_string_literal(visible))
        return
    if node.type == 6 or node.type == 7:
        if visible.endswith("]") and "[" in visible:
            name, index = parse_indexed_value(visible)
            node.f1 = ensure_cstr_offset(s4, node.f1, name)
            expected = index & 0xFFFFFFFF
            if 0 <= node.f3 <= len(s4) - 4 and unpack_dword(s4[node.f3 : node.f3 + 4]) == expected:
                node.type = 8 if name in {"Flag", "GlobalFlag"} else 9
                node.f2 = 0
                return
            index_offset = append_dword_offset(s4, index)
            node.type = 8 if name in {"Flag", "GlobalFlag"} else 9
            node.f2 = 0
            node.f3 = index_offset
            append_new_node(nodes, Node(4, 0, index_offset, 0))
            return
        node.f1 = ensure_cstr_offset(s4, node.f1, visible)
        return
    if node.type == 8 or node.type == 9:
        name, index = parse_indexed_value(visible)
        node.f1 = ensure_cstr_offset(s4, node.f1, name)
        node.f3 = ensure_dword_offset(s4, node.f3, index)
        return
    if node.type == 10:
        node.f2 = parse_temp_slot(visible, "temp")
        return
    if node.type == 11:
        node.f2 = parse_temp_slot(visible, "temp_num")
        return
    if node.type == 12:
        node.f2 = parse_temp_slot(visible, "temp_str")
        return
    raise ParseError(f"unsupported code token node type: {node.type}")


def scan_s1_statements(s1: list[int], nodes: list[Node], s4: bytes | bytearray) -> list[dict[str, object]]:
    statements: list[dict[str, object]] = []
    pc = 0
    while pc < len(s1):
        current_index = s1[pc]
        if not (0 <= current_index < len(nodes)):
            raise ParseError(f"S1 node index out of range at 0x{pc:X}: {current_index}")
        current_node = nodes[current_index]
        if pc + 1 < len(s1):
            next_index = s1[pc + 1]
            if 0 <= next_index < len(nodes):
                next_node = nodes[next_index]
                if current_node.type != 0 and next_node.type == 0 and node_name_text(s4, next_node) == "=":
                    if pc + 2 >= len(s1):
                        raise ParseError(f"truncated assignment at 0x{pc:X}")
                    argc = s1[pc + 2]
                    end = pc + 3 + argc
                    if end > len(s1):
                        raise ParseError(f"assignment out of range at 0x{pc:X}")
                    statements.append(
                        {
                            "kind": "assign",
                            "pc": pc,
                            "lhs": current_index,
                            "cmd": next_index,
                            "args": s1[pc + 3 : end],
                        }
                    )
                    pc = end
                    continue
        if current_node.type == 0:
            if pc + 1 >= len(s1):
                raise ParseError(f"truncated command at 0x{pc:X}")
            argc = s1[pc + 1]
            end = pc + 2 + argc
            if end > len(s1):
                raise ParseError(f"command out of range at 0x{pc:X}")
            statements.append({"kind": "command", "pc": pc, "cmd": current_index, "args": s1[pc + 2 : end]})
            pc = end
            continue
        statements.append({"kind": "token", "pc": pc, "node": current_index, "text": node_summary_data(s4, current_node)})
        pc += 1
    return statements


def set_node_aux_value(node_index: int, value: int, nodes: list[Node], s4: bytearray) -> None:
    node = nodes[node_index]
    if node.type != 1:
        raise ParseError(f"aux update requires type 1 node: {node_label(node_index)}")
    node.f2 = ensure_dword_offset(s4, node.f2, value, reuse_existing=False)


def is_special_label_text(text: str) -> bool:
    return text in {"{", "}", "else", "=", "+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "&&", "||"}


def rebuild_control_links(
    s1: list[int],
    nodes: list[Node],
    s4: bytearray,
    declared_labels: dict[int, int],
    declared_label_pcs: dict[str, int],
    jump_targets: dict[int, str],
) -> None:
    statements = scan_s1_statements(s1, nodes, s4)
    statement_by_pc = {int(statement["pc"]): index for index, statement in enumerate(statements)}
    open_stack: list[int] = []
    brace_pairs: dict[int, int] = {}

    for index, statement in enumerate(statements):
        if statement["kind"] == "command" and node_name_text(s4, nodes[int(statement["cmd"])]) == "if":
            if index + 1 >= len(statements) or statements[index + 1]["kind"] != "token" or statements[index + 1]["text"] != "{":
                raise ParseError(f"if must be followed by '{{' at 0x{int(statement['pc']):X}")
        if statement["kind"] != "token":
            continue
        text = str(statement["text"])
        pc = int(statement["pc"])
        if text == "{":
            open_stack.append(pc)
        elif text == "}":
            if not open_stack:
                raise ParseError(f"unmatched '}}' at 0x{pc:X}")
            open_pc = open_stack.pop()
            brace_pairs[open_pc] = pc

    if open_stack:
        raise ParseError(f"unmatched '{{' at 0x{open_stack[-1]:X}")

    for open_pc, close_pc in brace_pairs.items():
        close_statement_index = statement_by_pc[close_pc]
        target_pc = close_pc
        if close_statement_index + 2 < len(statements):
            maybe_else = statements[close_statement_index + 1]
            maybe_open = statements[close_statement_index + 2]
            if maybe_else["kind"] == "token" and maybe_else["text"] == "else" and maybe_open["kind"] == "token" and maybe_open["text"] == "{":
                target_pc = int(maybe_open["pc"])
        set_node_aux_value(s1[open_pc], target_pc, nodes, s4)
        set_node_aux_value(s1[close_pc], open_pc, nodes, s4)

    for statement in statements:
        if statement["kind"] != "token":
            continue
        text = str(statement["text"])
        node_index = int(statement["node"])
        if text == "else":
            set_node_aux_value(node_index, 0xFFFFFFFF, nodes, s4)

    for node_index, pc in declared_labels.items():
        set_node_aux_value(node_index, pc, nodes, s4)

    missing = sorted({label_name for label_name in jump_targets.values() if label_name not in declared_label_pcs})
    if missing:
        names = ", ".join(missing)
        raise ParseError(f"label target not declared in .code: {names}")

    for node_index, label_name in jump_targets.items():
        set_node_aux_value(node_index, declared_label_pcs[label_name], nodes, s4)


def rebuild_command_node_f2(s1: list[int], nodes: list[Node], s4: bytes | bytearray) -> None:
    for node in nodes:
        if node.type == 0 and node_name_text(s4, node) != "=":
            node.f2 = 0
    for statement in scan_s1_statements(s1, nodes, s4):
        if statement["kind"] != "command":
            continue
        cmd_index = int(statement["cmd"])
        if nodes[cmd_index].type != 0:
            continue
        command_name = node_name_text(s4, nodes[cmd_index])
        if command_name == "=":
            continue
        if command_name in COMMAND_F2_FORCE_ZERO:
            continue
        nodes[cmd_index].f2 = int(statement["pc"])


def split_compound_code_tokens(tokens: list[str], line_number: int, raw_line: str) -> list[list[str]]:
    if not tokens:
        return []
    if tokens[0] == "}":
        parts: list[list[str]] = [["}"]]
        cursor = 1
        if cursor < len(tokens) and tokens[cursor] == "else":
            parts.append(["else"])
            cursor += 1
        if cursor < len(tokens) and tokens[cursor] == "{":
            parts.append(["{"])
            cursor += 1
        if cursor != len(tokens):
            raise ParseError(f"cannot parse compound .code line {line_number}: {raw_line}")
        return parts
    if len(tokens) >= 2 and tokens[-1] == "{":
        return [tokens[:-1], ["{"]]
    return [tokens]


def build_clean_precreate_actions(
    code_lines: list[tuple[int, str, str]],
) -> dict[int, list[CleanPrecreateAction]]:
    actions_by_line: dict[int, list[CleanPrecreateAction]] = {}
    line_index = 0

    while line_index + 2 < len(code_lines):
        head_tokens = split_code_tokens(code_lines[line_index][1].strip())
        if len(head_tokens) != 2 or head_tokens[1] != "=" or not is_temp_slot_value(head_tokens[0]):
            line_index += 1
            continue

        producer_line_indexes: list[int] = []
        producer_temps: list[str] = []
        scan_index = line_index
        while scan_index + 1 < len(code_lines):
            maybe_head = split_code_tokens(code_lines[scan_index][1].strip())
            maybe_body = split_code_tokens(code_lines[scan_index + 1][1].strip())
            if len(maybe_head) != 2 or maybe_head[1] != "=" or not is_temp_slot_value(maybe_head[0]):
                break
            if len(maybe_body) < 2 or maybe_body[0] in CONTROL_FLOW_TOKENS or maybe_body[1] == "=":
                break
            producer_line_indexes.append(scan_index)
            producer_temps.append(maybe_head[0])
            scan_index += 2

        if not producer_line_indexes or scan_index + 1 >= len(code_lines):
            line_index += 1
            continue

        expr_tokens = split_code_tokens(code_lines[scan_index][1].strip())
        consumer_tokens = split_code_tokens(code_lines[scan_index + 1][1].strip())
        if len(expr_tokens) < 5 or expr_tokens[1] != "=" or not is_temp_slot_value(expr_tokens[0]):
            line_index += 1
            continue
        if len(consumer_tokens) < 2 or consumer_tokens[0] in CONTROL_FLOW_TOKENS or expr_tokens[0] not in consumer_tokens[1:]:
            line_index += 1
            continue

        literal_before_temp: dict[str, str] = {}
        literal_token_pos: dict[str, int] = {}
        current_literal: str | None = None
        current_literal_pos: int | None = None
        for token_pos, token in enumerate(expr_tokens[2:], start=2):
            if token == "+":
                continue
            if token.startswith(('"', "'")):
                current_literal = token
                current_literal_pos = token_pos
                continue
            if token in producer_temps and current_literal is not None and token not in literal_before_temp:
                literal_before_temp[token] = current_literal
                if current_literal_pos is None:
                    raise ParseError("missing literal token position in clean precreate analysis")
                literal_token_pos[token] = current_literal_pos
                current_literal = None
                current_literal_pos = None

        if len(literal_before_temp) != len(producer_temps):
            line_index += 1
            continue

        actions_by_line.setdefault(producer_line_indexes[0], []).append(
            CleanPrecreateAction(
                kind="node",
                visible=consumer_tokens[0],
                role="cmd",
                reuse_existing=True,
            )
        )
        for producer_line_index, producer_temp in zip(producer_line_indexes[1:], producer_temps[1:]):
            literal_token = literal_before_temp[producer_temp]
            actions_by_line.setdefault(producer_line_index, []).append(
                CleanPrecreateAction(
                    kind="node",
                    visible=literal_token,
                    role="value",
                    target_line_index=scan_index,
                    target_token_index=literal_token_pos[producer_temp],
                    reuse_existing=False,
                )
            )

        first_temp = producer_temps[0]
        actions_by_line.setdefault(producer_line_indexes[0], []).append(
            CleanPrecreateAction(
                kind="node",
                visible=literal_before_temp[first_temp],
                role="value",
                target_line_index=scan_index,
                target_token_index=literal_token_pos[first_temp],
                reuse_existing=False,
            )
        )
        if "+" in expr_tokens[2:]:
            actions_by_line.setdefault(producer_line_indexes[0], []).append(
                CleanPrecreateAction(
                    kind="node",
                    visible="+",
                    role="value",
                    reuse_existing=True,
                )
            )

        line_index = scan_index + 2

    for line_index in range(len(code_lines) - 1):
        tokens = split_code_tokens(code_lines[line_index][1].strip())
        next_tokens = split_code_tokens(code_lines[line_index + 1][1].strip())
        if (
            len(tokens) < 3
            or tokens[1] != "="
            or not is_temp_slot_value(tokens[0])
            or len(next_tokens) < 2
            or next_tokens[0] in CONTROL_FLOW_TOKENS
            or next_tokens[1] == "="
            or tokens[0] not in next_tokens[1:]
        ):
            continue

        temp_token_index = next_tokens.index(tokens[0], 1)
        command_name = next_tokens[0]

        # When a command argument was decompiled into a short run of temp
        # assignments followed by a consumer command, the original builder can
        # emit non-temp leading command args (for example the leading `5` in
        # `GraphicLoad 5 temp[a] temp[b] 0 0`) before the first producer line.
        # Precreate those arguments on the first producer line so the rebuilt
        # node order matches the original temp-lifted command layout more
        # closely.
        run_start = line_index
        earlier_producer_temps: set[str] = set()
        required_producer_temps = {
            token for token in next_tokens[1:temp_token_index] if is_temp_slot_value(token)
        }
        required_producer_temps.update(token for token in tokens[2:] if is_temp_slot_value(token))
        if temp_token_index > 1 or required_producer_temps:
            scan_index = line_index - 1
            while scan_index >= 0:
                prev_tokens = split_code_tokens(code_lines[scan_index][1].strip())
                if len(prev_tokens) < 3 or prev_tokens[1] != "=" or not is_temp_slot_value(prev_tokens[0]):
                    break
                if prev_tokens[0] not in required_producer_temps:
                    break
                run_start = scan_index
                earlier_producer_temps.add(prev_tokens[0])
                required_producer_temps.discard(prev_tokens[0])
                required_producer_temps.update(token for token in prev_tokens[2:] if is_temp_slot_value(token))
                scan_index -= 1

        actions_by_line.setdefault(run_start, []).append(
            CleanPrecreateAction(
                kind="node",
                visible=next_tokens[0],
                role="cmd",
                reuse_existing=True,
            )
        )
        for token_pos in range(1, temp_token_index):
            role = "label-ref" if command_name == "ScriptJump" else "value"
            action_line_index = line_index
            if run_start != line_index and next_tokens[token_pos] not in earlier_producer_temps:
                action_line_index = run_start
            actions_by_line.setdefault(action_line_index, []).append(
                CleanPrecreateAction(
                    kind="node",
                    visible=next_tokens[token_pos],
                    role=role,
                    target_line_index=line_index + 1,
                    target_token_index=token_pos,
                    reuse_existing=role == "cmd" or should_reuse_clean_node(next_tokens[token_pos], role),
                    command_name=command_name,
                    allocate_hidden_temp=role == "value" and is_indexed_family_value(next_tokens[token_pos]),
                )
            )

    for line_index in range(len(code_lines) - 1):
        tokens = split_code_tokens(code_lines[line_index][1].strip())
        next_tokens = split_code_tokens(code_lines[line_index + 1][1].strip())
        if (
            len(tokens) < 3
            or tokens[1] != "="
            or not is_temp_slot_value(tokens[0])
            or len(next_tokens) < 4
            or next_tokens[1] != "="
            or not is_indexed_family_value(next_tokens[0])
            or tokens[0] not in next_tokens[2:]
        ):
            continue

        # Decompiled temp assignments often come from a single original
        # assignment expression.  In cases like
        # `temp[n] = Flag[a] * 10` followed by
        # `Flag[b] = temp[n] + Flag[c]`, the original compiler had already
        # emitted the indexed LHS (`Flag[b]`) before the lifted temp producer.
        # Precreate that LHS on the producer line so the clean rebuild keeps the
        # original node order without changing the readable statement order.
        actions_by_line.setdefault(line_index, []).append(
            CleanPrecreateAction(
                kind="node",
                visible=next_tokens[0],
                role="value",
                target_line_index=line_index + 1,
                target_token_index=0,
                reuse_existing=should_reuse_clean_node(next_tokens[0], "value"),
            )
        )

    for line_index in range(len(code_lines) - 2):
        tokens = split_code_tokens(code_lines[line_index][1].strip())
        mid_tokens = split_code_tokens(code_lines[line_index + 1][1].strip())
        next_tokens = split_code_tokens(code_lines[line_index + 2][1].strip())
        if (
            len(tokens) != 2
            or tokens[1] != "="
            or not is_temp_slot_value(tokens[0])
            or len(mid_tokens) < 2
            or mid_tokens[0] in CONTROL_FLOW_TOKENS
            or mid_tokens[1] == "="
            or len(next_tokens) < 2
            or next_tokens[0] in CONTROL_FLOW_TOKENS
            or next_tokens[1] == "="
            or tokens[0] not in next_tokens[1:]
        ):
            continue

        temp_token_index = next_tokens.index(tokens[0], 1)
        command_name = next_tokens[0]
        actions_by_line.setdefault(line_index, []).append(
            CleanPrecreateAction(
                kind="node",
                visible=command_name,
                role="cmd",
                reuse_existing=True,
            )
        )
        for token_pos in range(1, temp_token_index):
            role = "label-ref" if command_name == "ScriptJump" else "value"
            actions_by_line.setdefault(line_index, []).append(
                CleanPrecreateAction(
                    kind="node",
                    visible=next_tokens[token_pos],
                    role=role,
                    target_line_index=line_index + 2,
                    target_token_index=token_pos,
                    reuse_existing=role == "cmd" or should_reuse_clean_node(next_tokens[token_pos], role),
                    command_name=command_name,
                    allocate_hidden_temp=role == "value" and is_indexed_family_value(next_tokens[token_pos]),
                )
            )

    for line_index in range(len(code_lines) - 2):
        first_tokens = split_code_tokens(code_lines[line_index][1].strip())
        second_tokens = split_code_tokens(code_lines[line_index + 1][1].strip())
        third_tokens = split_code_tokens(code_lines[line_index + 2][1].strip())
        if (
            len(first_tokens) == 3
            and first_tokens[1] == "="
            and is_temp_slot_value(first_tokens[0])
            and is_indexed_family_value(first_tokens[2])
            and len(second_tokens) == 3
            and second_tokens[0] == "0"
            and second_tokens[1] == "="
            and second_tokens[2] == first_tokens[0]
            and len(third_tokens) >= 3
            and third_tokens[1] == "="
            and is_indexed_family_value(third_tokens[0])
        ):
            _, third_index = parse_indexed_value(third_tokens[0])
            if third_index != 0:
                continue
            actions_by_line.setdefault(line_index, []).append(
                CleanPrecreateAction(
                    kind="placeholder",
                    visible=third_tokens[0],
                    role="value",
                    target_line_index=line_index + 2,
                    target_token_index=0,
                    patch_zero_line_index=line_index + 1,
                )
            )

    return actions_by_line


def append_code_statement(
    tokens: list[str],
    line_number: int,
    raw_line: str,
    bound_ids: list[int],
    cursor: int,
    nodes: list[Node],
    s4: bytearray,
    s1: list[int],
    declared_labels: dict[int, int],
    declared_label_pcs: dict[str, int],
    jump_targets: dict[int, str],
    next_hidden_temp_slot: int,
    exact_mode: bool,
    planned_token_nodes: dict[int, int] | None = None,
    previous_statement_tokens: list[str] | None = None,
    planned_hidden_temp_token_positions: set[int] | None = None,
) -> tuple[int, int]:
    if not tokens:
        return cursor, next_hidden_temp_slot

    use_inline_binding = any(is_inline_bound_token(token) for token in tokens)

    if use_inline_binding and len(tokens) == 1 and tokens[0].endswith(":"):
        visible, node_index, _ = parse_bound_token(tokens[0])
        apply_visible_text_to_node(node_index, visible, nodes, s4)
        if visible in declared_label_pcs:
            raise ParseError(f"duplicate label declaration on line {line_number}: {visible}")
        declared_labels[node_index] = len(s1)
        declared_label_pcs[visible] = len(s1)
        s1.append(node_index)
        return cursor, next_hidden_temp_slot

    if use_inline_binding and len(tokens) == 1:
        visible, node_index, _ = parse_bound_token(tokens[0])
        apply_visible_text_to_node(node_index, visible, nodes, s4)
        if nodes[node_index].type == 0:
            s1.extend([node_index, 0])
            return cursor, next_hidden_temp_slot
        if nodes[node_index].type == 1 and not is_special_label_text(visible):
            if visible in declared_label_pcs:
                raise ParseError(f"duplicate label declaration on line {line_number}: {visible}")
            declared_labels[node_index] = len(s1)
            declared_label_pcs[visible] = len(s1)
        s1.append(node_index)
        return cursor, next_hidden_temp_slot

    if use_inline_binding:
        first_visible, first_index, first_colon = parse_bound_token(tokens[0])
        if first_colon:
            raise ParseError(f"unexpected ':' on line {line_number}: {raw_line}")
        second_visible, second_index, second_colon = parse_bound_token(tokens[1])
        if second_colon:
            raise ParseError(f"unexpected ':' on line {line_number}: {raw_line}")

        if second_visible == "=" and nodes[second_index].type == 0 and nodes[first_index].type != 0:
            apply_visible_text_to_node(first_index, first_visible, nodes, s4)
            apply_visible_text_to_node(second_index, second_visible, nodes, s4)
            arg_indexes: list[int] = []
            for token in tokens[2:]:
                visible, node_index, has_colon = parse_bound_token(token)
                if has_colon:
                    raise ParseError(f"unexpected ':' in assignment on line {line_number}: {raw_line}")
                apply_visible_text_to_node(node_index, visible, nodes, s4)
                arg_indexes.append(node_index)
            s1.extend([first_index, second_index, len(arg_indexes), *arg_indexes])
            return cursor, next_hidden_temp_slot

        if nodes[first_index].type == 0:
            apply_visible_text_to_node(first_index, first_visible, nodes, s4)
            arg_indexes = []
            for token in tokens[1:]:
                visible, node_index, has_colon = parse_bound_token(token)
                if has_colon:
                    raise ParseError(f"unexpected ':' in command on line {line_number}: {raw_line}")
                apply_visible_text_to_node(node_index, visible, nodes, s4)
                arg_indexes.append(node_index)
            s1.extend([first_index, len(arg_indexes), *arg_indexes])
            if first_visible == "ScriptJump":
                for node_index in arg_indexes:
                    if nodes[node_index].type == 1 and not is_special_label_text(node_summary_data(s4, nodes[node_index])):
                        jump_targets[node_index] = node_summary_data(s4, nodes[node_index])
            return cursor, next_hidden_temp_slot

        raise ParseError(f"cannot parse .code line {line_number}: {raw_line}")

    if len(tokens) == 1 and tokens[0].endswith(":"):
        visible = tokens[0][:-1]
        node_index, cursor = bind_or_create_statement_token(
            0,
            visible,
            "label",
            planned_token_nodes,
            nodes,
            s4,
            bound_ids,
            cursor,
            line_number=line_number,
            reuse_existing=exact_mode or should_reuse_clean_node(visible, "label"),
        )
        if visible in declared_label_pcs:
            raise ParseError(f"duplicate label declaration on line {line_number}: {visible}")
        declared_labels[node_index] = len(s1)
        declared_label_pcs[visible] = len(s1)
        s1.append(node_index)
        return cursor, next_hidden_temp_slot

    if len(tokens) == 1:
        visible = tokens[0]
        if visible in {"{", "}", "else"}:
            node_index, cursor = bind_or_create_statement_token(
                0,
                visible,
                "token",
                planned_token_nodes,
                nodes,
                s4,
                bound_ids,
                cursor,
                line_number=line_number,
                reuse_existing=exact_mode or should_reuse_clean_node(visible, "token"),
            )
            s1.append(node_index)
            return cursor, next_hidden_temp_slot
        node_index, cursor = bind_or_create_statement_token(
            0,
            visible,
            "zero-cmd",
            planned_token_nodes,
            nodes,
            s4,
            bound_ids,
            cursor,
            line_number=line_number,
            reuse_existing=exact_mode or should_reuse_clean_node(visible, "zero-cmd"),
        )
        if nodes[node_index].type == 0:
            s1.extend([node_index, 0])
        else:
            if nodes[node_index].type == 1 and not is_special_label_text(visible):
                if visible in declared_label_pcs:
                    raise ParseError(f"duplicate label declaration on line {line_number}: {visible}")
                declared_labels[node_index] = len(s1)
                declared_label_pcs[visible] = len(s1)
            s1.append(node_index)
        return cursor, next_hidden_temp_slot

    if tokens[1] == "=":
        if (
            not exact_mode
            and is_temp_slot_value(tokens[0])
            and len(tokens) >= 5
            and is_indexed_family_value(tokens[2])
            and tokens[3] == "="
        ):
            rhs_name, rhs_index = parse_indexed_value(tokens[2])
            # Restrict cross-statement literal reuse to the known Flag[0] clean-mode
            # pattern where the immediately preceding statement is a literal-zero
            # assignment into an indexed value. Broader reuse regresses files like
            # HI_H_02.yks and logo_e.yks, where the original binary keeps a fresh
            # hidden index literal for Flag[0].
            can_reuse_recent_zero = (
                rhs_index == 0
                and previous_statement_tokens is not None
                and len(previous_statement_tokens) == 3
                and previous_statement_tokens[1] == "="
                and is_indexed_family_value(previous_statement_tokens[2])
            )
            if can_reuse_recent_zero:
                try:
                    can_reuse_recent_zero = parse_int(previous_statement_tokens[0]) == 0
                except ValueError:
                    can_reuse_recent_zero = False
            rhs_index_offset = recent_literal_offset(nodes, s4, rhs_index) if can_reuse_recent_zero else None
            if rhs_index_offset is None:
                rhs_index_offset = append_dword_offset(s4, rhs_index)
                append_new_node(nodes, Node(4, 0, rhs_index_offset, 0))
            rhs_name_offset = append_cstr_offset(s4, rhs_name)
            rhs_type = 8 if rhs_name in {"Flag", "GlobalFlag"} else 9
            rhs_value_index = append_new_node(nodes, Node(rhs_type, rhs_name_offset, 0, rhs_index_offset))
            inner_eq_index, cursor = bind_or_create_statement_token(
                3,
                tokens[3],
                "value",
                planned_token_nodes,
                nodes,
                s4,
                bound_ids,
                cursor,
                line_number=line_number,
                reuse_existing=True,
            )
            final_value_index, cursor = bind_or_create_statement_token(
                4,
                tokens[4],
                "value",
                planned_token_nodes,
                nodes,
                s4,
                bound_ids,
                cursor,
                line_number=line_number,
                reuse_existing=exact_mode or should_reuse_clean_node(tokens[4], "value"),
            )
            first_index, cursor = bind_or_create_statement_token(
                0,
                tokens[0],
                "value",
                planned_token_nodes,
                nodes,
                s4,
                bound_ids,
                cursor,
                line_number=line_number,
                reuse_existing=exact_mode or should_reuse_clean_node(tokens[0], "value"),
            )
            second_index, cursor = bind_or_create_statement_token(
                1,
                tokens[1],
                "assign-op",
                planned_token_nodes,
                nodes,
                s4,
                bound_ids,
                cursor,
                line_number=line_number,
                reuse_existing=True,
            )
            s1.extend([first_index, second_index, 3, rhs_value_index, inner_eq_index, final_value_index])
            return cursor, next_hidden_temp_slot

        if not exact_mode and is_temp_slot_value(tokens[0]):
            arg_indexes: list[int] = []
            for token_pos, token in enumerate(tokens[2:], start=2):
                if is_indexed_family_value(token) and not (planned_token_nodes is not None and token_pos in planned_token_nodes):
                    rhs_name, rhs_index = parse_indexed_value(token)
                    rhs_index_offset = append_dword_offset(s4, rhs_index)
                    rhs_name_offset = append_cstr_offset(s4, rhs_name)
                    append_new_node(nodes, Node(4, 0, rhs_index_offset, 0))
                    rhs_type = 8 if rhs_name in {"Flag", "GlobalFlag"} else 9
                    node_index = append_new_node(nodes, Node(rhs_type, rhs_name_offset, 0, rhs_index_offset))
                else:
                    node_index, cursor = bind_or_create_statement_token(
                        token_pos,
                        token,
                        "value",
                        planned_token_nodes,
                        nodes,
                        s4,
                        bound_ids,
                        cursor,
                        line_number=line_number,
                        reuse_existing=exact_mode or should_reuse_clean_node(token, "value"),
                    )
                arg_indexes.append(node_index)

            first_index, cursor = bind_or_create_statement_token(
                0,
                tokens[0],
                "value",
                planned_token_nodes,
                nodes,
                s4,
                bound_ids,
                cursor,
                line_number=line_number,
                reuse_existing=exact_mode or should_reuse_clean_node(tokens[0], "value"),
            )
            second_index, cursor = bind_or_create_statement_token(
                1,
                tokens[1],
                "assign-op",
                planned_token_nodes,
                nodes,
                s4,
                bound_ids,
                cursor,
                line_number=line_number,
                reuse_existing=True,
            )
            s1.extend([first_index, second_index, len(arg_indexes), *arg_indexes])
            return cursor, next_hidden_temp_slot

        if not exact_mode and len(tokens) == 3 and is_indexed_family_value(tokens[2]):
            lhs_is_fresh_literal = is_fresh_literal_value(tokens[0]) and not (
                planned_token_nodes is not None and 0 in planned_token_nodes
            )
            if not lhs_is_fresh_literal:
                first_index, cursor = bind_or_create_statement_token(
                    0,
                    tokens[0],
                    "value",
                    planned_token_nodes,
                    nodes,
                    s4,
                    bound_ids,
                    cursor,
                    line_number=line_number,
                    reuse_existing=exact_mode or should_reuse_clean_node(tokens[0], "value"),
                )
            rhs_name, rhs_index = parse_indexed_value(tokens[2])
            rhs_index_offset = append_dword_offset(s4, rhs_index)
            rhs_name_offset = append_cstr_offset(s4, rhs_name)
            append_new_node(nodes, Node(4, 0, rhs_index_offset, 0))
            rhs_type = 8 if rhs_name in {"Flag", "GlobalFlag"} else 9
            rhs_value_index = append_new_node(nodes, Node(rhs_type, rhs_name_offset, 0, rhs_index_offset))
            next_hidden_temp_slot = allocate_hidden_temp_slot(nodes, next_hidden_temp_slot)
            if lhs_is_fresh_literal:
                first_index, cursor = bind_or_create_statement_token(
                    0,
                    tokens[0],
                    "value",
                    planned_token_nodes,
                    nodes,
                    s4,
                    bound_ids,
                    cursor,
                    line_number=line_number,
                    reuse_existing=exact_mode or should_reuse_clean_node(tokens[0], "value"),
                )
            second_index, cursor = bind_or_create_statement_token(
                1,
                tokens[1],
                "assign-op",
                planned_token_nodes,
                nodes,
                s4,
                bound_ids,
                cursor,
                line_number=line_number,
                reuse_existing=True,
            )
            s1.extend([first_index, second_index, 1, rhs_value_index])
            return cursor, next_hidden_temp_slot

        if not exact_mode and is_indexed_family_value(tokens[0]) and not (planned_token_nodes is not None and 0 in planned_token_nodes):
            first_index = append_indexed_value_node(
                tokens[0],
                nodes,
                s4,
                index_before_name=False,
                hidden_before_node=False,
            )
        else:
            first_index, cursor = bind_or_create_statement_token(
                0,
                tokens[0],
                "value",
                planned_token_nodes,
                nodes,
                s4,
                bound_ids,
                cursor,
                line_number=line_number,
                reuse_existing=exact_mode or should_reuse_clean_node(tokens[0], "value"),
            )
        arg_indexes: list[int] = []
        rhs_has_indexed_value = False
        for token_pos, token in enumerate(tokens[2:], start=2):
            if not exact_mode and is_indexed_family_value(token) and not (planned_token_nodes is not None and token_pos in planned_token_nodes):
                node_index = append_indexed_value_node(
                    token,
                    nodes,
                    s4,
                    index_before_name=True,
                    hidden_before_node=True,
                )
                rhs_has_indexed_value = True
            else:
                node_index, cursor = bind_or_create_statement_token(
                    token_pos,
                    token,
                    "value",
                    planned_token_nodes,
                    nodes,
                    s4,
                    bound_ids,
                    cursor,
                    line_number=line_number,
                    reuse_existing=exact_mode or should_reuse_clean_node(token, "value"),
                )
            arg_indexes.append(node_index)
        second_index, cursor = bind_or_create_statement_token(
            1,
            tokens[1],
            "assign-op",
            planned_token_nodes,
            nodes,
            s4,
            bound_ids,
            cursor,
            line_number=line_number,
            reuse_existing=exact_mode or should_reuse_clean_node(tokens[1], "assign-op"),
        )
        s1.extend([first_index, second_index, len(arg_indexes), *arg_indexes])
        if not exact_mode and rhs_has_indexed_value:
            next_hidden_temp_slot = allocate_hidden_temp_slot(nodes, next_hidden_temp_slot)
        return cursor, next_hidden_temp_slot

    command_name = tokens[0]
    first_index, cursor = bind_or_create_statement_token(
        0,
        command_name,
        "cmd",
        planned_token_nodes,
        nodes,
        s4,
        bound_ids,
        cursor,
        line_number=line_number,
        reuse_existing=exact_mode or should_reuse_clean_node(command_name, "cmd"),
    )
    arg_indexes = []
    for token_pos, token in enumerate(tokens[1:], start=1):
        role = "label-ref" if command_name == "ScriptJump" else "value"
        node_index, cursor = bind_or_create_statement_token(
            token_pos,
            token,
            role,
            planned_token_nodes,
            nodes,
            s4,
            bound_ids,
            cursor,
            command_name,
            line_number,
            exact_mode or should_reuse_clean_node(token, role),
        )
        arg_indexes.append(node_index)
        if role == "value" and is_indexed_family_value(token):
            hidden_temp_already_planned = (
                planned_hidden_temp_token_positions is not None and token_pos in planned_hidden_temp_token_positions
            )
            if not hidden_temp_already_planned:
                next_hidden_temp_slot = allocate_hidden_temp_slot(nodes, next_hidden_temp_slot)
                if not exact_mode:
                    ensure_assign_op_node(nodes, s4)
    s1.extend([first_index, len(arg_indexes), *arg_indexes])
    if command_name == "ScriptJump":
        for node_index in arg_indexes:
            if nodes[node_index].type == 1 and not is_special_label_text(node_summary_data(s4, nodes[node_index])):
                jump_targets[node_index] = node_summary_data(s4, nodes[node_index])
    return cursor, next_hidden_temp_slot


def finalize_pending_bare_assignment(
    pending_lhs_token: str | None,
    pending_lhs_pos: int | None,
    pending_line_number: int | None,
    pending_eq_pos: int | None,
    nodes: list[Node],
    s4: bytearray,
    s1: list[int],
    next_hidden_temp_slot: int,
    allocate_hidden_temp: bool,
) -> tuple[int, str | None, int | None, int | None, int | None]:
    if pending_line_number is None or pending_eq_pos is None:
        return next_hidden_temp_slot, pending_lhs_token, pending_lhs_pos, pending_line_number, pending_eq_pos

    if allocate_hidden_temp:
        next_hidden_temp_slot = allocate_hidden_temp_slot(nodes, next_hidden_temp_slot)
    eq_index, _ = bind_or_create_visible_node(
        "=",
        "assign-op",
        nodes,
        s4,
        [],
        0,
        line_number=pending_line_number,
        reuse_existing=True,
    )
    s1[pending_eq_pos] = eq_index
    if pending_lhs_token is not None and pending_lhs_pos is not None:
        lhs_index, _ = bind_or_create_visible_node(
            pending_lhs_token,
            "value",
            nodes,
            s4,
            [],
            0,
            line_number=pending_line_number,
            reuse_existing=should_reuse_clean_node(pending_lhs_token, "value"),
        )
        s1[pending_lhs_pos] = lhs_index
    return next_hidden_temp_slot, None, None, None, None


def build_code_section(
    code_lines: list[tuple[int, str, str]],
    nodes: list[Node],
    s4: bytearray,
    exact_mode: bool,
) -> list[int]:
    if exact_mode:
        return _build_code_section_impl(code_lines, nodes, s4, exact_mode)

    previous_cache = current_node_lookup_cache()
    _NODE_LOOKUP_CONTEXT.cache = NodeLookupCache()
    try:
        return _build_code_section_impl(code_lines, nodes, s4, exact_mode)
    finally:
        if previous_cache is None:
            try:
                delattr(_NODE_LOOKUP_CONTEXT, "cache")
            except AttributeError:
                pass
        else:
            _NODE_LOOKUP_CONTEXT.cache = previous_cache


def _build_code_section_impl(
    code_lines: list[tuple[int, str, str]],
    nodes: list[Node],
    s4: bytearray,
    exact_mode: bool,
) -> list[int]:
    s1: list[int] = []
    declared_labels: dict[int, int] = {}
    declared_label_pcs: dict[str, int] = {}
    jump_targets: dict[int, str] = {}
    clean_precreate_actions = {} if exact_mode else build_clean_precreate_actions(code_lines)
    planned_token_nodes: dict[tuple[int, int], int] = {}
    planned_hidden_temp_tokens: set[tuple[int, int]] = set()
    zero_patch_placeholders_by_line: dict[int, list[int]] = {}
    next_hidden_temp_slot = 0
    pending_bare_assign_lhs_token: str | None = None
    pending_bare_assign_lhs_pos: int | None = None
    pending_bare_assign_line_number: int | None = None
    pending_bare_assign_eq_pos: int | None = None
    pending_bare_assign_hidden_slot_start: int | None = None
    previous_statement_tokens: list[str] | None = None

    for line_index, (line_number, raw_line, comment) in enumerate(code_lines):
        stripped = raw_line.strip()
        if not stripped:
            continue

        for action in clean_precreate_actions.get(line_index, []):
            if action.kind == "placeholder":
                node_index = create_indexed_family_placeholder(action.visible, nodes, s4)
            else:
                reuse_existing = (
                    action.reuse_existing
                    if action.reuse_existing is not None
                    else action.role == "cmd" or should_reuse_clean_node(action.visible, action.role)
                )
                node_index, _ = bind_or_create_visible_node(
                    action.visible,
                    action.role,
                    nodes,
                    s4,
                    [],
                    0,
                    command_name=action.command_name,
                    line_number=line_number,
                    reuse_existing=reuse_existing,
                )
            if action.allocate_hidden_temp:
                next_hidden_temp_slot = allocate_hidden_temp_slot(nodes, next_hidden_temp_slot)
                if not exact_mode:
                    ensure_assign_op_node(nodes, s4)
            if action.target_line_index is not None and action.target_token_index is not None:
                planned_token_nodes[(action.target_line_index, action.target_token_index)] = node_index
                if action.allocate_hidden_temp:
                    planned_hidden_temp_tokens.add((action.target_line_index, action.target_token_index))
            if action.patch_zero_line_index is not None:
                zero_patch_placeholders_by_line.setdefault(action.patch_zero_line_index, []).append(node_index)

        tokens = split_code_tokens(stripped)
        if not tokens:
            continue

        use_inline_binding = any(is_inline_bound_token(token) for token in tokens)
        bound_ids = [] if use_inline_binding or not exact_mode else parse_code_metadata(comment)
        logical_statements = split_compound_code_tokens(tokens, line_number, raw_line)
        line_planned_nodes = {
            token_index: node_index
            for (planned_line_index, token_index), node_index in planned_token_nodes.items()
            if planned_line_index == line_index
        }
        line_planned_hidden_temp_positions = {
            token_index for (planned_line_index, token_index) in planned_hidden_temp_tokens if planned_line_index == line_index
        }
        cursor = 0
        for statement_tokens in logical_statements:
            if not exact_mode and len(statement_tokens) == 2 and statement_tokens[1] == "=":
                if pending_bare_assign_eq_pos is not None:
                    raise ParseError(f"dangling bare assignment before line {line_number}: {raw_line}")
                pending_bare_assign_line_number = line_number
                pending_bare_assign_hidden_slot_start = next_hidden_temp_slot
                if is_temp_slot_value(statement_tokens[0]):
                    pending_bare_assign_lhs_pos = len(s1)
                    pending_bare_assign_eq_pos = len(s1) + 1
                    pending_bare_assign_lhs_token = statement_tokens[0]
                    s1.extend([0xFFFFFFFF, 0xFFFFFFFF, 0])
                    continue

                lhs_index, cursor = bind_or_create_visible_node(
                    statement_tokens[0],
                    "value",
                    nodes,
                    s4,
                    bound_ids,
                    cursor,
                    line_number=line_number,
                    reuse_existing=should_reuse_clean_node(statement_tokens[0], "value"),
                )
                pending_bare_assign_lhs_pos = None
                pending_bare_assign_eq_pos = len(s1) + 1
                pending_bare_assign_line_number = line_number
                pending_bare_assign_lhs_token = None
                s1.extend([lhs_index, 0xFFFFFFFF, 0])
                continue

            statement_start = len(s1)
            cursor, next_hidden_temp_slot = append_code_statement(
                statement_tokens,
                line_number,
                raw_line,
                bound_ids,
                cursor,
                nodes,
                s4,
                s1,
                declared_labels,
                declared_label_pcs,
                jump_targets,
                next_hidden_temp_slot,
                exact_mode,
                line_planned_nodes if len(logical_statements) == 1 else None,
                previous_statement_tokens,
                line_planned_hidden_temp_positions if len(logical_statements) == 1 else None,
            )
            if (
                not exact_mode
                and line_index in zero_patch_placeholders_by_line
                and len(statement_tokens) == 3
                and statement_tokens[0] == "0"
                and statement_tokens[1] == "="
                and len(s1) > statement_start
            ):
                lhs_index = s1[statement_start]
                if 0 <= lhs_index < len(nodes) and nodes[lhs_index].type == 4:
                    for placeholder_index in zero_patch_placeholders_by_line.pop(line_index):
                        nodes[placeholder_index].f3 = nodes[lhs_index].f2
            if pending_bare_assign_eq_pos is not None:
                next_hidden_temp_slot, pending_bare_assign_lhs_token, pending_bare_assign_lhs_pos, pending_bare_assign_line_number, pending_bare_assign_eq_pos = finalize_pending_bare_assignment(
                    pending_bare_assign_lhs_token,
                    pending_bare_assign_lhs_pos,
                    pending_bare_assign_line_number,
                    pending_bare_assign_eq_pos,
                    nodes,
                    s4,
                    s1,
                    next_hidden_temp_slot,
                    pending_bare_assign_hidden_slot_start == next_hidden_temp_slot,
                )
                pending_bare_assign_hidden_slot_start = None
            previous_statement_tokens = statement_tokens
        if cursor != len(bound_ids):
            raise ParseError(f"unused code metadata on line {line_number}: {comment}")
        for key in [key for key in planned_token_nodes if key[0] == line_index]:
            planned_token_nodes.pop(key, None)
        for key in [key for key in planned_hidden_temp_tokens if key[0] == line_index]:
            planned_hidden_temp_tokens.discard(key)

    if pending_bare_assign_eq_pos is not None:
        next_hidden_temp_slot, pending_bare_assign_lhs_token, pending_bare_assign_lhs_pos, pending_bare_assign_line_number, pending_bare_assign_eq_pos = finalize_pending_bare_assignment(
            pending_bare_assign_lhs_token,
            pending_bare_assign_lhs_pos,
            pending_bare_assign_line_number,
            pending_bare_assign_eq_pos,
            nodes,
            s4,
            s1,
            next_hidden_temp_slot,
            pending_bare_assign_hidden_slot_start == next_hidden_temp_slot,
        )
        pending_bare_assign_hidden_slot_start = None

    rebuild_control_links(s1, nodes, s4, declared_labels, declared_label_pcs, jump_targets)
    if not exact_mode:
        rebuild_command_node_f2(s1, nodes, s4)
    return s1


def gather_pool_refs(yks: YKSFile) -> dict[int, set[str]]:
    refs: dict[int, set[str]] = {}

    def add(offset: int, kind: str) -> None:
        refs.setdefault(offset, set()).add(kind)

    for node in yks.s2:
        if node.type in {0, 1, 2, 3, 6, 7, 8, 9}:
            add(node.f1, "cstr")
        if node.type in {1, 2, 4}:
            add(node.f2, "dword")
        if node.type in {3, 5}:
            add(node.f2, "cstr")
        if node.type in {8, 9}:
            add(node.f3, "dword")
    return refs


def choose_pool_entry(s4: bytes, offset: int, kinds: set[str]) -> tuple[str, object, int]:
    if kinds == {"dword"}:
        if offset + 4 > len(s4):
            raise ParseError(f"dword offset out of range: 0x{offset:X}")
        value = unpack_dword(s4[offset : offset + 4])
        return "dword", value, 4
    if kinds == {"cstr"}:
        raw = read_cstring(s4, offset)
        text = decode_text_lossless(raw[:-1])
        if text is None:
            return "bytes", list(raw), len(raw)
        return "cstr", text, len(raw)
    cstr_size = 0
    if "cstr" in kinds:
        cstr_size = len(read_cstring(s4, offset))
    dword_size = 4 if "dword" in kinds else 0
    size = max(cstr_size, dword_size)
    if size <= 0:
        raise ParseError(f"cannot choose pool entry size at 0x{offset:X}")
    return "bytes", list(s4[offset : offset + size]), size


def build_pool_entries(yks: YKSFile) -> tuple[list[PoolEntry], list[PoolAlias]]:
    refs = gather_pool_refs(yks)
    entries: list[PoolEntry] = []
    aliases: list[PoolAlias] = []
    spans: list[tuple[int, int, str]] = []
    position = 0
    for offset in sorted(refs):
        if offset < position:
            for start, end, label in reversed(spans):
                if start <= offset < end:
                    aliases.append(PoolAlias(pool_label(offset), offset, label, offset - start))
                    break
            else:
                raise ParseError(f"overlapping pool reference at 0x{offset:X}")
            continue
        if offset > position:
            gap = yks.s4[position:offset]
            if any(gap):
                entries.append(PoolEntry(pool_label(position), position, "bytes", list(gap), len(gap)))
            else:
                entries.append(PoolEntry(pool_label(position), position, "pad", len(gap), len(gap)))
            spans.append((position, offset, pool_label(position)))
            position = offset
        kind, value, size = choose_pool_entry(yks.s4, offset, refs[offset])
        label = pool_label(offset)
        entries.append(PoolEntry(label, offset, kind, value, size))
        spans.append((offset, offset + size, label))
        position = offset + size
    if position < len(yks.s4):
        tail = yks.s4[position:]
        if any(tail):
            entries.append(PoolEntry(pool_label(position), position, "bytes", list(tail), len(tail)))
        else:
            entries.append(PoolEntry(pool_label(position), position, "pad", len(tail), len(tail)))
    aliases.sort(key=lambda item: item.offset)
    return entries, aliases


def pool_entry_lines(entries: list[PoolEntry], aliases: list[PoolAlias]) -> list[str]:
    alias_by_offset: dict[int, list[PoolAlias]] = {}
    for alias in aliases:
        alias_by_offset.setdefault(alias.offset, []).append(alias)
    lines: list[str] = []
    for entry in entries:
        if entry.kind == "cstr":
            body = f"cstr({quote_text(entry.value)})"
        elif entry.kind == "dword":
            body = f"dword({format_dword(entry.value)})"
        elif entry.kind == "pad":
            body = f"pad({entry.value})"
        elif entry.kind == "bytes":
            bytes_text = ", ".join(f"0x{value:02X}" for value in entry.value)
            body = f"bytes([{bytes_text}])"
        else:
            raise ParseError(f"unsupported pool entry kind: {entry.kind}")
        lines.append(f"{entry.label} = {body}")
        for alias in alias_by_offset.get(entry.offset, []):
            lines.append(f"{alias.label} = alias({alias.target}, {alias.delta})")
    for alias in aliases:
        if alias.offset not in {entry.offset for entry in entries}:
            lines.append(f"{alias.label} = alias({alias.target}, {alias.delta})")
    return lines


def node_summary(yks: YKSFile, node: Node) -> str:
    return node_summary_data(yks.s4, node)


def node_directive(node: Node) -> str:
    def maybe(parts: list[str], key: str, value: int, default: int) -> None:
        if value != default:
            parts.append(f"{key}={format_dword(value & 0xFFFFFFFF) if value < 0 else value}")

    if node.type == 0:
        parts = [f"name={pool_label(node.f1)}"]
        maybe(parts, "f2", node.f2, 0)
        maybe(parts, "f3", node.f3, 0)
        return f"cmd({', '.join(parts)})"
    if node.type == 1:
        parts = [f"text={pool_label(node.f1)}", f"aux={pool_label(node.f2)}"]
        maybe(parts, "f3", node.f3, -1)
        return f"tok({', '.join(parts)})"
    if node.type == 2:
        parts = [f"name={pool_label(node.f1)}", f"value={pool_label(node.f2)}"]
        maybe(parts, "f3", node.f3, 0)
        return f"num_symbol({', '.join(parts)})"
    if node.type == 3:
        parts = [f"name={pool_label(node.f1)}", f"value={pool_label(node.f2)}"]
        maybe(parts, "f3", node.f3, 0)
        return f"str_symbol({', '.join(parts)})"
    if node.type == 4:
        parts = [f"value={pool_label(node.f2)}"]
        maybe(parts, "f1", node.f1, 0)
        maybe(parts, "f3", node.f3, 0)
        return f"int({', '.join(parts)})"
    if node.type == 5:
        parts = [f"value={pool_label(node.f2)}"]
        maybe(parts, "f1", node.f1, 0)
        maybe(parts, "f3", node.f3, 0)
        return f"str({', '.join(parts)})"
    if node.type == 6:
        parts = [f"name={pool_label(node.f1)}"]
        maybe(parts, "f2", node.f2, 0)
        maybe(parts, "f3", node.f3, 0)
        return f"num_family({', '.join(parts)})"
    if node.type == 7:
        parts = [f"name={pool_label(node.f1)}"]
        maybe(parts, "f2", node.f2, 0)
        maybe(parts, "f3", node.f3, 0)
        return f"str_family({', '.join(parts)})"
    if node.type == 8:
        parts = [f"name={pool_label(node.f1)}", f"index={pool_label(node.f3)}"]
        maybe(parts, "f2", node.f2, 0)
        return f"num_var({', '.join(parts)})"
    if node.type == 9:
        parts = [f"name={pool_label(node.f1)}", f"index={pool_label(node.f3)}"]
        maybe(parts, "f2", node.f2, 0)
        return f"str_var({', '.join(parts)})"
    if node.type == 10:
        parts = [f"slot={node.f2}"]
        maybe(parts, "f1", node.f1, 0)
        maybe(parts, "f3", node.f3, 0)
        return f"temp({', '.join(parts)})"
    if node.type == 11:
        parts = [f"slot={node.f2}"]
        maybe(parts, "f1", node.f1, 0)
        maybe(parts, "f3", node.f3, 0)
        return f"temp_num({', '.join(parts)})"
    if node.type == 12:
        parts = [f"slot={node.f2}"]
        maybe(parts, "f1", node.f1, 0)
        maybe(parts, "f3", node.f3, 0)
        return f"temp_str({', '.join(parts)})"
    return f"raw(type={node.type}, f1={node.f1}, f2={node.f2}, f3={node.f3})"


def s1_comment(yks: YKSFile, value: int) -> str:
    if 0 <= value < len(yks.s2):
        return f"{node_label(value)} {node_summary(yks, yks.s2[value])}"
    return ""


def build_pretty_code_records(yks: YKSFile) -> list[tuple[int, str, list[tuple[int, str]]]]:
    records: list[tuple[int, str, list[tuple[int, str]]]] = []
    statements = scan_s1_statements(yks.s1, yks.s2, yks.s4)
    depth = 0
    index = 0

    while index < len(statements):
        statement = statements[index]
        if statement["kind"] == "token" and statement["text"] == "}":
            depth = max(depth - 1, 0)

        line_depth = depth
        consume = 1
        next_depth = depth

        if statement["kind"] == "assign":
            lhs = int(statement["lhs"])
            cmd = int(statement["cmd"])
            args = [int(node_index) for node_index in statement["args"]]
            parts = [
                format_pretty_code_token(yks.s4, yks.s2, lhs),
                format_pretty_code_token(yks.s4, yks.s2, cmd),
            ]
            parts.extend(format_pretty_code_token(yks.s4, yks.s2, node_index) for node_index in args)
            bindings = [(lhs, "value"), (cmd, "assign-op")]
            bindings.extend((node_index, "value") for node_index in args)
            if index + 1 < len(statements) and statements[index + 1]["kind"] == "token" and statements[index + 1]["text"] == "{":
                open_node = int(statements[index + 1]["node"])
                parts.append("{")
                bindings.append((open_node, "token"))
                consume = 2
                next_depth = depth + 1
            records.append((line_depth, " ".join(parts), bindings))
        elif statement["kind"] == "command":
            cmd = int(statement["cmd"])
            args = [int(node_index) for node_index in statement["args"]]
            command_name = format_pretty_code_token(yks.s4, yks.s2, cmd)
            parts = [command_name]
            parts.extend(format_pretty_code_token(yks.s4, yks.s2, node_index) for node_index in args)
            bindings = [(cmd, "cmd")]
            arg_role = "label-ref" if command_name == "ScriptJump" else "value"
            bindings.extend((node_index, arg_role) for node_index in args)
            if index + 1 < len(statements) and statements[index + 1]["kind"] == "token" and statements[index + 1]["text"] == "{":
                open_node = int(statements[index + 1]["node"])
                parts.append("{")
                bindings.append((open_node, "token"))
                consume = 2
                next_depth = depth + 1
            records.append((line_depth, " ".join(parts), bindings))
        else:
            token_text = str(statement["text"])
            token_node = int(statement["node"])
            token = format_pretty_code_token(yks.s4, yks.s2, token_node)
            if token_text == "}" and index + 2 < len(statements):
                maybe_else = statements[index + 1]
                maybe_open = statements[index + 2]
                if maybe_else["kind"] == "token" and maybe_else["text"] == "else" and maybe_open["kind"] == "token" and maybe_open["text"] == "{":
                    records.append(
                        (
                            line_depth,
                            "} else {",
                            [
                                (token_node, "token"),
                                (int(maybe_else["node"]), "token"),
                                (int(maybe_open["node"]), "token"),
                            ],
                        )
                    )
                    consume = 3
                    next_depth = depth + 1
                else:
                    records.append((line_depth, token, [(token_node, "token")]))
            elif token_text == "else" and index + 1 < len(statements):
                maybe_open = statements[index + 1]
                if maybe_open["kind"] == "token" and maybe_open["text"] == "{":
                    records.append((line_depth, "else {", [(token_node, "token"), (int(maybe_open["node"]), "token")]))
                    consume = 2
                    next_depth = depth + 1
                else:
                    records.append((line_depth, token, [(token_node, "token")]))
            elif not is_special_label_text(token_text):
                records.append((line_depth, f"{token}:", [(token_node, "label")]))
            else:
                records.append((line_depth, token, [(token_node, "token")]))
                if token_text == "{":
                    next_depth = depth + 1

        depth = next_depth
        index += consume

    return records


def pretty_code_line_needs_metadata(yks: YKSFile, bindings: list[tuple[int, str]]) -> bool:
    lookup_cache = NodeLookupCache()
    for node_index, role in bindings:
        visible = format_pretty_code_token(yks.s4, yks.s2, node_index)
        if len(find_visible_node_candidates(visible, role, yks.s2, yks.s4, lookup_cache)) != 1:
            return True
    return False


def build_code_lines(yks: YKSFile, include_metadata: bool = False) -> list[str]:
    lines: list[str] = []
    for depth, text, bindings in build_pretty_code_records(yks):
        if text.endswith(":") and lines and lines[-1] != "":
            lines.append("")
        suffix = ""
        if include_metadata and pretty_code_line_needs_metadata(yks, bindings):
            suffix = f" #| {format_code_metadata([node_index for node_index, _ in bindings])}"
        lines.append("    " * (depth + 1) + text + suffix)
    return lines


def build_exact_metadata_lines(yks: YKSFile) -> list[str]:
    entries, aliases = build_pool_entries(yks)
    lines = [
        "#@exact machine metadata below keeps byte-identical roundtrip.",
        "#@exact edit .code first; these lines are regenerated automatically.",
    ]
    for line in pool_entry_lines(entries, aliases):
        lines.append(f"#@pool {line}")
    for index, node in enumerate(yks.s2):
        lines.append(f"#@node {node_label(index)} = {node_directive(node)}")
    return lines


def disassemble_to_text(yks: YKSFile, include_exact_metadata: bool = False) -> str:
    lines = [
        "# LoveDere YKS ASM",
        f"# format: {ASM_FORMAT_VERSION}",
        "# encoding: utf-8",
        f"# script-encoding: {SCRIPT_ENCODING}",
        "",
        ".meta",
        f"version = {yks.version}",
        f"unknown_08 = 0x{yks.unknown_08:016X}",
        f"unknown_2c = 0x{yks.unknown_2c:08X}",
        f"temp_count = {yks.temp_count}",
        "",
        ".code",
    ]
    lines.extend(build_code_lines(yks, include_metadata=include_exact_metadata))
    if include_exact_metadata:
        lines.extend([""])
        lines.extend(build_exact_metadata_lines(yks))
    lines.append("")
    return "\n".join(lines)


def build_pool_from_defs(defs: list[tuple[str, str, list[object], dict[str, object]]]) -> tuple[bytes, dict[str, int]]:
    buffer = bytearray()
    labels: dict[str, int] = {}

    def resolve_label(label: str) -> int:
        if label not in labels:
            raise ParseError(f"unknown pool label: {label}")
        return labels[label]

    for label, kind, positional, keyword in defs:
        expected_offset = parse_label_index(label)
        if kind == "alias":
            if len(positional) != 2 or not isinstance(positional[0], str) or not isinstance(positional[1], int):
                raise ParseError(f"invalid alias definition: {label}")
            actual_offset = resolve_label(positional[0]) + positional[1]
            if actual_offset != expected_offset:
                raise ParseError(f"alias offset mismatch for {label}: expected 0x{expected_offset:X}, got 0x{actual_offset:X}")
            labels[label] = actual_offset
            continue
        if len(buffer) != expected_offset:
            raise ParseError(f"pool offset mismatch for {label}: expected 0x{expected_offset:X}, got 0x{len(buffer):X}")
        labels[label] = len(buffer)
        if kind == "cstr":
            if len(positional) != 1 or not isinstance(positional[0], str):
                raise ParseError(f"invalid cstr definition: {label}")
            buffer.extend(positional[0].encode(SCRIPT_ENCODING) + b"\x00")
        elif kind == "dword":
            if len(positional) != 1 or not isinstance(positional[0], int):
                raise ParseError(f"invalid dword definition: {label}")
            buffer.extend(pack_dword(positional[0]))
        elif kind == "pad":
            if len(positional) != 1 or not isinstance(positional[0], int):
                raise ParseError(f"invalid pad definition: {label}")
            buffer.extend(b"\x00" * positional[0])
        elif kind == "bytes":
            if len(positional) != 1 or not isinstance(positional[0], list):
                raise ParseError(f"invalid bytes definition: {label}")
            buffer.extend(int(value) & 0xFF for value in positional[0])
        else:
            raise ParseError(f"unsupported pool definition: {kind}")
    return bytes(buffer), labels


def resolve_pool_arg(value: object, pool_offsets: dict[str, int]) -> int:
    if isinstance(value, str):
        if value not in pool_offsets:
            raise ParseError(f"unknown pool reference: {value}")
        return pool_offsets[value]
    if isinstance(value, int):
        return value
    raise ParseError(f"invalid pool argument: {value}")


def build_nodes(defs: list[tuple[str, str, list[object], dict[str, object]]], pool_offsets: dict[str, int]) -> tuple[list[Node], dict[str, int]]:
    nodes: list[Node] = []
    labels: dict[str, int] = {}
    for label, kind, positional, keyword in defs:
        expected_index = parse_label_index(label)
        if expected_index != len(nodes):
            raise ParseError(f"node index mismatch for {label}: expected {len(nodes)}, got {expected_index}")
        labels[label] = len(nodes)
        if kind == "cmd":
            node = Node(0, resolve_pool_arg(keyword["name"], pool_offsets), int(keyword.get("f2", 0)), int(keyword.get("f3", 0)))
        elif kind == "tok":
            node = Node(1, resolve_pool_arg(keyword["text"], pool_offsets), resolve_pool_arg(keyword["aux"], pool_offsets), int(keyword.get("f3", -1)))
        elif kind == "num_symbol":
            node = Node(2, resolve_pool_arg(keyword["name"], pool_offsets), resolve_pool_arg(keyword["value"], pool_offsets), int(keyword.get("f3", 0)))
        elif kind == "str_symbol":
            node = Node(3, resolve_pool_arg(keyword["name"], pool_offsets), resolve_pool_arg(keyword["value"], pool_offsets), int(keyword.get("f3", 0)))
        elif kind == "int":
            node = Node(4, int(keyword.get("f1", 0)), resolve_pool_arg(keyword["value"], pool_offsets), int(keyword.get("f3", 0)))
        elif kind == "str":
            node = Node(5, int(keyword.get("f1", 0)), resolve_pool_arg(keyword["value"], pool_offsets), int(keyword.get("f3", 0)))
        elif kind == "num_family":
            node = Node(6, resolve_pool_arg(keyword["name"], pool_offsets), int(keyword.get("f2", 0)), int(keyword.get("f3", 0)))
        elif kind == "str_family":
            node = Node(7, resolve_pool_arg(keyword["name"], pool_offsets), int(keyword.get("f2", 0)), int(keyword.get("f3", 0)))
        elif kind == "num_var":
            node = Node(8, resolve_pool_arg(keyword["name"], pool_offsets), int(keyword.get("f2", 0)), resolve_pool_arg(keyword["index"], pool_offsets))
        elif kind == "str_var":
            node = Node(9, resolve_pool_arg(keyword["name"], pool_offsets), int(keyword.get("f2", 0)), resolve_pool_arg(keyword["index"], pool_offsets))
        elif kind == "temp":
            node = Node(10, int(keyword.get("f1", 0)), int(keyword["slot"]), int(keyword.get("f3", 0)))
        elif kind == "temp_num":
            node = Node(11, int(keyword.get("f1", 0)), int(keyword["slot"]), int(keyword.get("f3", 0)))
        elif kind == "temp_str":
            node = Node(12, int(keyword.get("f1", 0)), int(keyword["slot"]), int(keyword.get("f3", 0)))
        elif kind == "raw":
            node = Node(int(keyword["type"]), int(keyword["f1"]), int(keyword["f2"]), int(keyword["f3"]))
        else:
            raise ParseError(f"unsupported node definition: {kind}")
        nodes.append(node)
    return nodes, labels


def build_s1(defs: list[tuple[str, int]]) -> list[int]:
    stream: list[int] = []
    for label, value in defs:
        expected_index = parse_label_index(label)
        if expected_index != len(stream):
            raise ParseError(f"S1 index mismatch for {label}: expected {len(stream)}, got {expected_index}")
        stream.append(value)
    return stream


def assemble_text(text: str) -> YKSFile:
    section: str | None = None
    meta: dict[str, int] = {}
    pool_defs: list[tuple[str, str, list[object], dict[str, object]]] = []
    node_defs: list[tuple[str, str, list[object], dict[str, object]]] = []
    s1_defs: list[tuple[str, int]] = []
    code_lines: list[tuple[int, str, str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("#@pool "):
            payload = stripped[len("#@pool ") :].strip()
            left, eq, right = payload.partition("=")
            if not eq:
                raise ParseError(f"invalid hidden pool line {line_number}: {raw_line}")
            kind, positional, keyword = parse_call(right.strip())
            pool_defs.append((left.strip(), kind, positional, keyword))
            continue
        if stripped.startswith("#@node "):
            payload = stripped[len("#@node ") :].strip()
            left, eq, right = payload.partition("=")
            if not eq:
                raise ParseError(f"invalid hidden node line {line_number}: {raw_line}")
            kind, positional, keyword = parse_call(right.strip())
            node_defs.append((left.strip(), kind, positional, keyword))
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if section == ".code":
            code, comment = split_code_metadata_comment(raw_line)
        else:
            code, comment = split_comment(raw_line)
        stripped = code.strip()
        if not stripped:
            continue
        if stripped.startswith("."):
            section = stripped
            continue
        if section == ".meta":
            key, eq, value = stripped.partition("=")
            if not eq:
                raise ParseError(f"invalid meta line {line_number}: {raw_line}")
            meta[key.strip()] = parse_int(value.strip())
            continue
        if section == ".code":
            code_lines.append((line_number, stripped, comment))
            continue
        left, eq, right = stripped.partition("=")
        if not eq:
            raise ParseError(f"invalid line {line_number}: {raw_line}")
        label = left.strip()
        if section == ".pool":
            kind, positional, keyword = parse_call(right.strip())
            record = (label, kind, positional, keyword)
            pool_defs.append(record)
        elif section == ".nodes":
            kind, positional, keyword = parse_call(right.strip())
            record = (label, kind, positional, keyword)
            node_defs.append(record)
        elif section == ".s1":
            value = parse_int(right.strip())
            s1_defs.append((label, value))
        else:
            raise ParseError(f"line outside section at {line_number}: {raw_line}")
    s4, pool_offsets = build_pool_from_defs(pool_defs)
    nodes, node_indexes = build_nodes(node_defs, pool_offsets)
    del node_indexes
    s4_mut = bytearray(s4)
    if code_lines:
        s1 = build_code_section(code_lines, nodes, s4_mut, exact_mode=bool(node_defs))
    else:
        s1 = build_s1(s1_defs)
    if code_lines and not node_defs:
        ensure_temp_slot_coverage(nodes, meta.get("temp_count", 0))
    return YKSFile(
        version=meta.get("version", 0),
        unknown_08=meta.get("unknown_08", 0),
        unknown_2c=meta.get("unknown_2c", 0),
        temp_count=meta.get("temp_count", 0),
        s1=s1,
        s2=nodes,
        s4=bytes(s4_mut),
    )


def disassemble_path(input_path: Path, output_path: Path | None, include_exact_metadata: bool = False) -> None:
    yks = YKSFile.load(input_path)
    text = disassemble_to_text(yks, include_exact_metadata=include_exact_metadata)
    out_path = output_path or input_path.with_suffix(input_path.suffix + ".asm")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8", newline="\n")


def assemble_path(input_path: Path, output_path: Path | None) -> None:
    text = input_path.read_text(encoding="utf-8")
    yks = assemble_text(text)
    if output_path is None:
        if input_path.suffix.lower() == ".asm" and input_path.name.lower().endswith(".yks.asm"):
            output_path = input_path.with_suffix("")
        else:
            output_path = input_path.with_suffix(".yks")
    yks.save(output_path)


def iter_yks_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".yks")


def default_worker_count() -> int:
    return 16


def default_log_path(target: Path, stem: str) -> Path:
    if target.exists() and target.is_file():
        return target.with_suffix(target.suffix + f".{stem}.log")
    if target.suffix:
        return target.with_suffix(target.suffix + f".{stem}.log")
    return target / f"{stem}.log"


class BatchLogger:
    def __init__(self, log_path: Path | None):
        self.log_path = log_path
        self._lock = Lock()
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("", encoding="utf-8")

    def log(self, message: str) -> None:
        with self._lock:
            print(message)
            if self.log_path is not None:
                with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(message + "\n")


def run_parallel_jobs(paths: list[Path], worker_count: int, worker):
    if worker_count <= 1:
        for path in paths:
            yield worker(path)
        return

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(worker, path): path for path in paths}
        for future in as_completed(futures):
            yield future.result()


def shorten_line(text: str, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def describe_first_text_diff(left: str, right: str) -> str:
    left_lines = left.splitlines()
    right_lines = right.splitlines()
    limit = min(len(left_lines), len(right_lines))
    for index in range(limit):
        if left_lines[index] != right_lines[index]:
            return (
                f"first diff line {index + 1}: "
                f"{shorten_line(left_lines[index])!r} != {shorten_line(right_lines[index])!r}"
            )
    if len(left_lines) != len(right_lines):
        return f"line count mismatch: {len(left_lines)} != {len(right_lines)}"
    return "text mismatch"


def describe_first_byte_diff(original: bytes, rebuilt: bytes) -> str:
    limit = min(len(original), len(rebuilt))
    for index in range(limit):
        if original[index] != rebuilt[index]:
            return f"first diff: 0x{index:X} {original[index]:02X} != {rebuilt[index]:02X}"
    return f"size mismatch: {len(original)} != {len(rebuilt)}"


def batch_disassemble(
    input_dir: Path,
    output_dir: Path,
    include_exact_metadata: bool = False,
    worker_count: int | None = None,
    log_path: Path | None = None,
) -> int:
    files = iter_yks_files(input_dir)
    worker_count = worker_count or default_worker_count()
    logger = BatchLogger(log_path or default_log_path(output_dir, "batch_disasm"))
    logger.log(
        f"START batch_disasm files={len(files)} workers={worker_count} exact={include_exact_metadata} log={logger.log_path}"
    )
    started = time.perf_counter()

    def worker(path: Path) -> BatchResult:
        try:
            relative = path.relative_to(input_dir)
            out_path = output_dir / (str(relative) + ".asm")
            disassemble_path(path, out_path, include_exact_metadata=include_exact_metadata)
            return BatchResult(path, True, f"{relative} -> {out_path.relative_to(output_dir)}")
        except Exception as exc:
            return BatchResult(path, False, str(exc))

    failures = 0
    for result in run_parallel_jobs(files, worker_count, worker):
        if result.ok:
            logger.log(f"OK   {result.path.name} {result.detail}")
        else:
            failures += 1
            logger.log(f"FAIL {result.path} {result.detail}")

    elapsed = time.perf_counter() - started
    logger.log(f"SUMMARY batch_disasm total={len(files)} failed={failures} seconds={elapsed:.2f}")
    return failures


def batch_assemble(
    input_dir: Path,
    output_dir: Path,
    worker_count: int | None = None,
    log_path: Path | None = None,
) -> int:
    files = sorted(input_dir.rglob("*.asm"))
    worker_count = worker_count or default_worker_count()
    logger = BatchLogger(log_path or default_log_path(output_dir, "batch_asm"))
    logger.log(f"START batch_asm files={len(files)} workers={worker_count} log={logger.log_path}")
    started = time.perf_counter()

    def worker(path: Path) -> BatchResult:
        try:
            relative = path.relative_to(input_dir)
            if relative.name.lower().endswith(".yks.asm"):
                target_name = relative.name[:-4]
            else:
                target_name = relative.stem + ".yks"
            out_path = output_dir / relative.parent / target_name
            assemble_path(path, out_path)
            return BatchResult(path, True, f"{relative} -> {out_path.relative_to(output_dir)}")
        except Exception as exc:
            return BatchResult(path, False, str(exc))

    failures = 0
    for result in run_parallel_jobs(files, worker_count, worker):
        if result.ok:
            logger.log(f"OK   {result.path.name} {result.detail}")
        else:
            failures += 1
            logger.log(f"FAIL {result.path} {result.detail}")

    elapsed = time.perf_counter() - started
    logger.log(f"SUMMARY batch_asm total={len(files)} failed={failures} seconds={elapsed:.2f}")
    return failures


def verify_roundtrip(target: Path, worker_count: int | None = None, log_path: Path | None = None) -> int:
    files = [target] if target.is_file() else iter_yks_files(target)
    worker_count = worker_count or default_worker_count()
    logger = BatchLogger(log_path or default_log_path(target, "verify_exact"))
    logger.log(f"START verify_exact files={len(files)} workers={worker_count} log={logger.log_path}")
    started = time.perf_counter()

    def worker(path: Path) -> BatchResult:
        try:
            original = path.read_bytes()
            text = disassemble_to_text(YKSFile.load(path), include_exact_metadata=True)
            rebuilt = assemble_text(text).to_bytes()
            if rebuilt != original:
                return BatchResult(path, False, describe_first_byte_diff(original, rebuilt))
            return BatchResult(path, True, "byte-identical")
        except Exception as exc:
            return BatchResult(path, False, str(exc))

    failures = 0
    for result in run_parallel_jobs(files, worker_count, worker):
        if not result.ok:
            failures += 1
            logger.log(f"FAIL {result.path} {result.detail}")
        else:
            logger.log(f"OK   {result.path} {result.detail}")

    elapsed = time.perf_counter() - started
    logger.log(f"SUMMARY verify_exact total={len(files)} failed={failures} seconds={elapsed:.2f}")
    return failures


def verify_clean_roundtrip_text(target: Path, worker_count: int | None = None, log_path: Path | None = None) -> int:
    files = [target] if target.is_file() else iter_yks_files(target)
    worker_count = worker_count or default_worker_count()
    logger = BatchLogger(log_path or default_log_path(target, "verify_clean"))
    logger.log(f"START verify_clean files={len(files)} workers={worker_count} log={logger.log_path}")
    started = time.perf_counter()

    def worker(path: Path) -> BatchResult:
        try:
            original_text = disassemble_to_text(YKSFile.load(path), include_exact_metadata=False)
            rebuilt = assemble_text(original_text)
            roundtrip_text = disassemble_to_text(rebuilt, include_exact_metadata=False)
            if roundtrip_text != original_text:
                return BatchResult(path, False, describe_first_text_diff(original_text, roundtrip_text))
            return BatchResult(path, True, "text-identical")
        except Exception as exc:
            return BatchResult(path, False, str(exc))

    failures = 0
    for result in run_parallel_jobs(files, worker_count, worker):
        if not result.ok:
            failures += 1
            logger.log(f"FAIL {result.path} {result.detail}")
        else:
            logger.log(f"OK   {result.path} {result.detail}")

    elapsed = time.perf_counter() - started
    logger.log(f"SUMMARY verify_clean total={len(files)} failed={failures} seconds={elapsed:.2f}")
    return failures


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LoveDere YKS disassembler/assembler")
    subparsers = parser.add_subparsers(dest="command", required=True)

    disasm_parser = subparsers.add_parser("disasm", help="disassemble a YKS file to readable text")
    disasm_parser.add_argument("input", type=Path)
    disasm_parser.add_argument("output", type=Path, nargs="?")
    disasm_parser.add_argument("--exact", action="store_true", help="include hidden pool/node metadata for byte-identical roundtrip")

    asm_parser = subparsers.add_parser("asm", help="assemble a text file back into YKS")
    asm_parser.add_argument("input", type=Path)
    asm_parser.add_argument("output", type=Path, nargs="?")

    batch_disasm_parser = subparsers.add_parser("batch_disasm", help="disassemble all YKS files in a directory")
    batch_disasm_parser.add_argument("input_dir", type=Path)
    batch_disasm_parser.add_argument("output_dir", type=Path)
    batch_disasm_parser.add_argument("--exact", action="store_true", help="include hidden pool/node metadata for byte-identical roundtrip")
    batch_disasm_parser.add_argument("--workers", type=int, default=default_worker_count())
    batch_disasm_parser.add_argument("--log", type=Path)

    batch_asm_parser = subparsers.add_parser("batch_asm", help="assemble all ASM files in a directory")
    batch_asm_parser.add_argument("input_dir", type=Path)
    batch_asm_parser.add_argument("output_dir", type=Path)
    batch_asm_parser.add_argument("--workers", type=int, default=default_worker_count())
    batch_asm_parser.add_argument("--log", type=Path)

    verify_parser = subparsers.add_parser("verify", help="disassemble and reassemble, then compare bytes")
    verify_parser.add_argument("target", type=Path)
    verify_parser.add_argument("--workers", type=int, default=default_worker_count())
    verify_parser.add_argument("--log", type=Path)

    verify_clean_parser = subparsers.add_parser("verify_clean", help="clean mode text must match after asm->disasm")
    verify_clean_parser.add_argument("target", type=Path)
    verify_clean_parser.add_argument("--workers", type=int, default=default_worker_count())
    verify_clean_parser.add_argument("--log", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "disasm":
        disassemble_path(args.input, args.output, include_exact_metadata=args.exact)
        return 0
    if args.command == "asm":
        assemble_path(args.input, args.output)
        return 0
    if args.command == "batch_disasm":
        return 1 if batch_disassemble(
            args.input_dir,
            args.output_dir,
            include_exact_metadata=args.exact,
            worker_count=args.workers,
            log_path=args.log,
        ) else 0
    if args.command == "batch_asm":
        return 1 if batch_assemble(args.input_dir, args.output_dir, worker_count=args.workers, log_path=args.log) else 0
    if args.command == "verify":
        return 1 if verify_roundtrip(args.target, worker_count=args.workers, log_path=args.log) else 0
    if args.command == "verify_clean":
        return 1 if verify_clean_roundtrip_text(args.target, worker_count=args.workers, log_path=args.log) else 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())