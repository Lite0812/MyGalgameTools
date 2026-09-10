"""Semantic disassembler for the Dual Colors SSB VM.

The VM has no file header: Code.ssb is a stream of little-endian dwords and
Data.ssb is the same-size byte buffer after the game's 0xAA XOR transform.
This module produces a paired Code/Data assembly document.  Branch operands
are represented by labels and expanded back to the VM's stack form by the
assembler.
"""

from __future__ import annotations

import argparse
import codecs
import re
import struct
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from opcodelist import (
    DATA_XOR,
    OPCODES,
    Opcode,
    is_unknown_opcode_word,
    opcode_for_word,
    signed32,
)


WORD_MASK = 0xFFFFFFFF
BRANCH_OPCODES = frozenset(op.value for op in OPCODES.values() if op.branch_kind)
STRING_DATA_OPCODES = frozenset(
    {
        0x80020003,
        0x80040001,
        0x80070006,
        0x80070007,
        0x80070008,
        0x80080001,
        0x80090002,
        0x800A0000,
        0x800A0002,
        0x800B0000,
        0x800B0007,
        0x800D0000,
    }
)


class DisassemblyError(RuntimeError):
    """Raised for malformed or undefined bytecode."""


@dataclass(frozen=True)
class AbstractValue:
    kind: str
    value: int | None = None
    source_pc: int | None = None
    source_pcs: frozenset[int] = frozenset()
    data_loads: frozenset[int] = frozenset()


UNKNOWN = AbstractValue("unknown")


@dataclass(frozen=True)
class BranchReference:
    branch_pc: int
    target_pc: int
    source_pc: int | None


@dataclass(frozen=True)
class TextSpan:
    offset: int
    end: int
    rendered: str


@dataclass(frozen=True)
class DataBlock:
    """One relocatable part of Data.ssb for the unified source view."""

    order: int
    label: str
    body: tuple[str, ...]


def _abstract_value(
    kind: str,
    value: int | None = None,
    source_pcs: Iterable[int] = (),
    data_loads: Iterable[int] = (),
) -> AbstractValue:
    sources = frozenset(source_pcs)
    source_pc = next(iter(sources)) if len(sources) == 1 else None
    return AbstractValue(kind, value, source_pc, sources, frozenset(data_loads))


def _const(
    value: int,
    source_pc: int | None = None,
    source_pcs: Iterable[int] = (),
    data_loads: Iterable[int] = (),
) -> AbstractValue:
    sources = set(source_pcs)
    if source_pc is not None:
        sources.add(source_pc)
    return _abstract_value("const", value & WORD_MASK, sources, data_loads)


def _unknown(
    source_pcs: Iterable[int] = (),
    data_loads: Iterable[int] = (),
) -> AbstractValue:
    sources = frozenset(source_pcs)
    loads = frozenset(data_loads)
    return UNKNOWN if not sources and not loads else _abstract_value(
        "unknown", source_pcs=sources, data_loads=loads
    )


def _value_equal(left: AbstractValue, right: AbstractValue) -> bool:
    return left.kind == right.kind and left.value == right.value


def _merge_value(left: AbstractValue, right: AbstractValue) -> AbstractValue:
    sources = left.source_pcs | right.source_pcs
    data_loads = left.data_loads | right.data_loads
    if _value_equal(left, right):
        if left.kind == "const":
            return _const(left.value or 0, source_pcs=sources, data_loads=data_loads)
        return _unknown(sources, data_loads)
    return _unknown(sources, data_loads)


def _merge_stack(old: tuple[AbstractValue, ...], new: tuple[AbstractValue, ...]) -> tuple[AbstractValue, ...]:
    if len(old) != len(new):
        # A malformed or path-dependent stack is deliberately made opaque.
        # Keeping the larger shape would invent operands and is less safe.
        return ()
    return tuple(_merge_value(a, b) for a, b in zip(old, new))


def _pop(stack: list[AbstractValue], count: int) -> list[AbstractValue]:
    values: list[AbstractValue] = []
    for _ in range(count):
        values.append(stack.pop() if stack else UNKNOWN)
    return values


def _binary_result(op: int, left: AbstractValue, right: AbstractValue) -> AbstractValue:
    sources = left.source_pcs | right.source_pcs
    data_loads = left.data_loads | right.data_loads
    if left.kind != "const" or right.kind != "const":
        return _unknown(sources, data_loads)
    a = signed32(left.value or 0)
    b = signed32(right.value or 0)
    try:
        if op in (0x80010000, 0x80010010):
            result = b + a
        elif op == 0x80010001:
            result = b - a
        elif op in (0x80010002,):
            result = a * b
        elif op in (0x80010003, 0x80010011):
            result = int(b / a)
        elif op in (0x80010004, 0x80010012):
            result = b % a
        elif op == 0x80010005:
            result = a | b
        elif op == 0x80010006:
            result = a & b
        elif op == 0x80010007:
            result = b ^ a
        elif op in (0x8001000A,):
            result = int(a == b)
        elif op in (0x8001000B,):
            result = int(a != b)
        elif op in (0x8001000C, 0x8001000D, 0x80010014):
            result = int(a >= b)
        elif op in (0x8001000E, 0x80010015):
            result = int(a < b)
        elif op in (0x8001000F, 0x80010016):
            result = int(a <= b)
        elif op == 0x80010013:
            result = int(a > b)
        elif op == 0x80010017:
            result = (right.value or 0) >> ((left.value or 0) & 31)
        elif op == 0x80010018:
            result = (b << (a & 31))
        elif op == 0x80010019:
            result = b >> (a & 31)
        else:
            return _unknown(sources, data_loads)
    except (ArithmeticError, ValueError, OverflowError):
        return _unknown(sources, data_loads)
    return _const(result, source_pcs=sources, data_loads=data_loads)


def _execute_abstract(
    op: Opcode,
    popped: list[AbstractValue],
    data: bytes,
) -> list[AbstractValue]:
    """Return abstract pushes for one opcode."""

    value = op.value
    if value == 0x80000004:
        return [popped[0] if popped else UNKNOWN] * 2
    if value == 0x80000005:
        top = popped[0] if len(popped) > 0 else UNKNOWN
        below = popped[1] if len(popped) > 1 else UNKNOWN
        return [below, top, below]
    if value == 0x80000012:
        top = popped[0] if len(popped) > 0 else UNKNOWN
        below = popped[1] if len(popped) > 1 else UNKNOWN
        return [top, below]
    if value == 0x80000000:
        index = popped[0] if popped else UNKNOWN
        if index.kind == "const" and (index.value or 0) < len(data) // 4:
            offset = (index.value or 0) * 4
            return [_const(
                struct.unpack_from("<I", data, offset)[0],
                data_loads=((index.value or 0),),
            )]
        return [UNKNOWN]
    if value == 0x80000002:
        byte_off = popped[0] if popped else UNKNOWN
        word_index = popped[1] if len(popped) > 1 else UNKNOWN
        if byte_off.kind == "const" and word_index.kind == "const":
            offset = (word_index.value or 0) * 4 + (byte_off.value or 0)
            if 0 <= offset < len(data):
                return [_const(data[offset])]
        return [UNKNOWN]
    if 0x80010000 <= value <= 0x80010019:
        if value in (0x80010008, 0x80010009):
            a = popped[0] if popped else UNKNOWN
            if a.kind != "const":
                return [_unknown(a.source_pcs, a.data_loads)]
            result = ~signed32(a.value or 0) if value == 0x80010008 else -signed32(a.value or 0)
            return [_const(result, source_pcs=a.source_pcs, data_loads=a.data_loads)]
        a = popped[0] if popped else UNKNOWN
        b = popped[1] if len(popped) > 1 else UNKNOWN
        return [_binary_result(value, a, b)]
    if value == 0x80020000:
        return [UNKNOWN]
    if value in (0x80050000, 0x80050001, 0x80050002, 0x80060001):
        return [UNKNOWN]
    if value in (0x80070006, 0x80070008, 0x8007000B, 0x8007000C):
        return [UNKNOWN]
    if value == 0x8007000D:
        return [UNKNOWN] * 6
    if value == 0x8008000B:
        return [UNKNOWN, UNKNOWN]
    if value == 0x80090004:
        return [UNKNOWN]
    if value == 0x800A0003:
        return [UNKNOWN]
    if value == 0x800B0002:
        return [UNKNOWN]
    if value == 0x800C0002:
        return [UNKNOWN]
    if value in (0x800D0000, 0x800D0005):
        return [UNKNOWN]
    return [UNKNOWN] * op.pushes


def _data_address_sources(
    value: AbstractValue,
    words: list[int],
    data: bytes,
) -> list[tuple[int, int]]:
    """Return immediate sources that form the relocatable base of a Data address.

    Direct references have one source.  Computed references normally combine a
    large Data base with one or more small indexes/offsets, so the largest valid
    Data immediate is the base that must move when Data.ssb is repacked.
    """

    candidates = [
        (source_pc, words[source_pc] & WORD_MASK)
        for source_pc in value.source_pcs
        if 0 <= source_pc < len(words)
        and not (words[source_pc] & 0x80000000)
        and 0 <= (words[source_pc] & WORD_MASK) < len(data) // 4
    ]
    if not candidates:
        return []
    base = max(index for _, index in candidates)
    return [(source_pc, index) for source_pc, index in candidates if index == base]


def _branch_target(op: Opcode, branch_pc: int, target: AbstractValue) -> int | None:
    if target.kind != "const":
        return None
    value = target.value or 0
    if op.relative:
        return branch_pc + 1 + signed32(value)
    return value


def analyze_code(words: list[int], data: bytes) -> tuple[dict[int, set[int]], dict[int, set[int]], dict[int, BranchReference]]:
    """Collect relocatable Data immediates and branch targets.

    This is a small constant-propagation pass over the stack VM.  It follows
    known branch targets and also keeps a lexical fall-through edge.  A second
    seed is started for disconnected blocks, so a malformed/unreachable block
    cannot hide a relocation.
    """

    states: dict[int, tuple[AbstractValue, ...]] = {}
    queue: deque[int] = deque()
    data_refs: dict[int, set[int]] = {}
    string_refs: dict[int, set[int]] = {}
    branches: dict[int, BranchReference] = {}
    indirect_string_slots: set[int] = set()
    stored_values: dict[int, set[tuple[int, AbstractValue]]] = {}
    visits: dict[int, int] = {}

    def schedule(pc: int, stack: tuple[AbstractValue, ...]) -> None:
        if not 0 <= pc < len(words):
            return
        old = states.get(pc)
        if old is None:
            states[pc] = stack
            queue.append(pc)
            return
        merged = _merge_stack(old, stack)
        if merged != old and visits.get(pc, 0) < 8:
            states[pc] = merged
            queue.append(pc)

    schedule(0, ())
    next_seed = 0
    while True:
        while queue:
            pc = queue.popleft()
            visits[pc] = visits.get(pc, 0) + 1
            if visits[pc] > 8:
                continue
            word = words[pc] & WORD_MASK
            if not (word & 0x80000000):
                schedule(pc + 1, states[pc] + (_const(word, pc),))
                continue
            op = opcode_for_word(word)
            if op is None:
                continue
            stack = list(states[pc])
            popped = _pop(stack, op.pops)

            for pop_index in op.data_refs:
                if pop_index > len(popped):
                    continue
                value = popped[pop_index - 1]
                sources = _data_address_sources(value, words, data)
                for source_pc, index in sources:
                    data_refs.setdefault(source_pc, set()).add(index)
                if (
                    op.value in STRING_DATA_OPCODES
                    and len(sources) == 1
                    and value.kind == "const"
                    and value.value == sources[0][1]
                ):
                    source_pc, index = sources[0]
                    string_refs.setdefault(source_pc, set()).add(index)
                if op.value in STRING_DATA_OPCODES:
                    indirect_string_slots.update(value.data_loads)

            if op.value == 0x80000001 and len(popped) >= 2:
                destination, stored = popped[0], popped[1]
                if (
                    destination.kind == "const"
                    and destination.value is not None
                    and 0 <= destination.value < len(data) // 4
                ):
                    stored_values.setdefault(destination.value, set()).add((pc, stored))

                # Some engine tables store Data indices in dynamically
                # calculated slots without ever passing the loaded value to a
                # string opcode in this CFG.  A non-trivial address calculation
                # followed by a large, empty-string Data index is the compiler's
                # pointer-table initialization pattern; retain its immediate
                # source for Data relocation as well.
                previous = opcode_for_word(words[pc - 1] & WORD_MASK) if pc else None
                if (
                    previous is not None
                    and 0x80010000 <= previous.value <= 0x80010019
                    and stored.kind == "const"
                    and stored.value is not None
                    and 0x1000 <= stored.value < len(data) // 4
                    and data[stored.value * 4] == 0
                ):
                    sources = _data_address_sources(stored, words, data)
                    for source_pc, index in sources:
                        data_refs.setdefault(source_pc, set()).add(index)

            if op.branch_kind:
                target = _branch_target(op, pc, popped[0] if popped else UNKNOWN)
                if target is not None and 0 <= target < len(words):
                    branches[pc] = BranchReference(pc, target, popped[0].source_pc if popped else None)

            pushed = _execute_abstract(op, popped, data)
            after = tuple(stack + pushed)
            fallthrough = pc + 1
            if op.branch_kind and op.branch_kind.startswith("jmp_"):
                target = branches.get(pc)
                if target is not None:
                    schedule(target.target_pc, after)
                continue
            if op.branch_kind and op.branch_kind.startswith("call_"):
                target = branches.get(pc)
                if target is not None:
                    schedule(target.target_pc, after)
                schedule(fallthrough, after)
                continue
            if op.branch_kind and op.branch_kind.startswith(("jnz_", "jz_")):
                target = branches.get(pc)
                if target is not None:
                    schedule(target.target_pc, after)
                schedule(fallthrough, after)
                continue
            schedule(fallthrough, after)

        while next_seed < len(words) and next_seed in states:
            next_seed += 1
        if next_seed >= len(words):
            break
        schedule(next_seed, ())

    # A script may keep a string pointer in a Data dword before passing the
    # loaded value to a host text/resource opcode.  Relocate the values written
    # to such pointer slots as well; an empty string is otherwise invisible to
    # the text-span inference pass.
    call_prefix = [0]
    for word in words:
        opcode = opcode_for_word(word & WORD_MASK)
        is_call = bool(
            opcode is not None
            and opcode.branch_kind
            and opcode.branch_kind.startswith("call_")
        )
        call_prefix.append(call_prefix[-1] + int(is_call))

    for slot in indirect_string_slots:
        for store_pc, value in stored_values.get(slot, ()):
            if value.kind == "const" and value.value == 0:
                continue
            local_sources = {
                source_pc
                for source_pc in value.source_pcs
                if 0 <= source_pc <= store_pc
                and call_prefix[store_pc] == call_prefix[source_pc + 1]
            }
            local_value = _abstract_value(
                value.kind,
                value.value,
                local_sources,
                value.data_loads,
            )
            sources = _data_address_sources(local_value, words, data)
            for source_pc, index in sources:
                data_refs.setdefault(source_pc, set()).add(index)
            if (
                len(sources) == 1
                and local_value.kind == "const"
                and local_value.value == sources[0][1]
            ):
                source_pc, index = sources[0]
                string_refs.setdefault(source_pc, set()).add(index)

    return data_refs, string_refs, branches


def _decode_one(data: bytes, offset: int, encoding: str) -> tuple[str, int]:
    """Decode one character, returning a placeholder for unsafe bytes."""

    max_width = min(4, len(data) - offset)
    for width in range(1, max_width + 1):
        chunk = data[offset : offset + width]
        try:
            text = chunk.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
        if len(text) != 1:
            continue
        char = text[0]
        codepoint = ord(char)
        safe = (
            char != '"'
            and (char.isprintable() or char == "\u3000")
            and not 0xE000 <= codepoint <= 0xF8FF
        )
        if safe:
            return (r"\\" if char == "\\" else char), width
        return "{{" + ":".join(f"{byte:02X}" for byte in chunk) + "}}", width
    return "{{" + f"{data[offset]:02X}" + "}}", 1


def render_text(raw: bytes, encoding: str) -> str:
    parts: list[str] = []
    offset = 0
    while offset < len(raw):
        if raw.startswith(b"\\nn", offset):
            parts.append(r"\n")
            offset += 3
            continue
        part, width = _decode_one(raw, offset, encoding)
        parts.append(part)
        offset += width
    return "".join(parts)


def _looks_like_text(raw: bytes, encoding: str) -> bool:
    if not raw or len(raw) > 8192:
        return False
    try:
        decoded = raw.decode(encoding, errors="strict")
    except UnicodeDecodeError:
        return False
    if not decoded:
        return False
    # CR is a semantic part of a script string even though it is not a
    # printable Unicode character.  Count it here so a short string whose
    # first byte is 0x0D is still recognized from its true boundary.
    printable = sum(
        char.isprintable() or char in ("\u3000", "\r") for char in decoded
    )
    if printable < max(1, int(len(decoded) * 0.80)):
        return False
    return any(char.isalnum() or ord(char) >= 0x80 for char in decoded)


def discover_text_spans(data: bytes, encoding: str, required_offsets: Iterable[int]) -> list[TextSpan]:
    required = {offset for offset in required_offsets if 0 <= offset < len(data) and offset % 4 == 0}
    starts = sorted(required)
    for offset in range(0, len(data), 4):
        if offset in required or data[offset] == 0:
            continue
        end = data.find(b"\0", offset)
        if end < 0:
            end = len(data)
        raw = data[offset:end]
        if _looks_like_text(raw, encoding):
            starts.append(offset)
    spans: list[TextSpan] = []
    occupied_end = -1
    required_set = set(required)
    for offset in sorted(set(starts)):
        if offset < occupied_end or data[offset] == 0:
            continue
        end = data.find(b"\0", offset)
        if end < 0:
            continue
        raw = data[offset:end]
        if not raw:
            continue
        # A required label inside a string would otherwise be lost.  Emit
        # that region as bytes instead and keep both addresses relocatable.
        if any(offset < required_offset < end + 1 for required_offset in required_set):
            continue
        if offset not in required_set and not _looks_like_text(raw, encoding):
            continue
        spans.append(TextSpan(offset, end + 1, render_text(raw, encoding)))
        occupied_end = end + 1
    return spans


def _format_bytes(data: bytes, chunk_size: int = 32) -> Iterable[str]:
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset : offset + chunk_size]
        yield ".byte " + ", ".join(f"0x{byte:02X}" for byte in chunk)


def _format_text_tail(data: bytes) -> Iterable[str]:
    """Render bytes following TEXT without spelling zero runs as .byte."""

    pos = 0
    while pos < len(data):
        if data[pos] == 0:
            end = pos + 1
            while end < len(data) and data[end] == 0:
                end += 1
            # A short terminal run is ordinary alignment after TEXT.  The
            # assembler supplies it when it aligns the next data label.
            if end - pos > 3 or end < len(data):
                yield f".zero {end - pos}"
            pos = end
            continue
        end = pos + 1
        while end < len(data) and data[end] != 0:
            end += 1
        yield from _format_bytes(data[pos:end])
        pos = end


def _label_for_code(pc: int) -> str:
    return f"loc_{pc:08X}"


def _label_for_data(index: int) -> str:
    return f"data_{index:08X}"


def _emit_data_block(block: DataBlock) -> list[str]:
    lines = ["", f"{block.label}:"]
    lines.extend(f"    {line}" for line in block.body)
    return lines


def _simple_inline_body(block: DataBlock) -> str | None:
    """Return a compact suffix for TEXT/zero-only Data blocks."""

    if block.body and all(
        line.startswith("TEXT ") or line.startswith(".zero ") for line in block.body
    ):
        return " ".join(block.body)
    return None


def _recover_implicit_branch_refs(
    words: list[int], branches: dict[int, BranchReference]
) -> None:
    """Recover the compiler's ordinary-dword branch operands."""

    for pc, word in enumerate(words):
        op = opcode_for_word(word & WORD_MASK)
        if op is None or not op.branch_kind or pc == 0:
            continue
        previous = words[pc - 1] & WORD_MASK
        if previous & 0x80000000:
            continue
        target = pc + 1 + signed32(previous) if op.relative else previous
        if 0 <= target < len(words):
            branches[pc] = BranchReference(pc, target, pc - 1)


def _infer_direct_text_refs(
    words: list[int],
    data: bytes,
    encoding: str,
    data_refs: dict[int, set[int]],
    branches: dict[int, BranchReference],
) -> list[TextSpan]:
    """Recognize text pointers passed through script helper functions.

    Many game routines receive Data indices as ordinary stack arguments and
    then call a second routine which consumes them.  A direct immediate that
    exactly names a decoded text span is therefore a relocatable text pointer,
    unless that word is the operand of a branch instruction.
    """

    spans = discover_text_spans(data, encoding, ())
    text_indices = {span.offset // 4 for span in spans}
    branch_sources = {ref.source_pc for ref in branches.values() if ref.source_pc is not None}
    for pc, word in enumerate(words):
        word &= WORD_MASK
        if word & 0x80000000 or pc in branch_sources:
            continue
        if word in text_indices:
            data_refs.setdefault(pc, set()).add(word)

    # A few helper calls receive an empty-string Data index immediately
    # before one or more text pointers.  The VM does not expose those
    # arguments through a typed opcode, so the abstract stack pass cannot
    # classify them.  Recover only the narrow, stable pattern used by the
    # compiler: a contiguous immediate argument run for one call containing
    # at least two known text indices, with a zero dword immediately beside
    # their narrow index range.
    data_words = len(data) // 4
    for call_pc, ref in branches.items():
        if call_pc >= len(words):
            continue
        call_op = opcode_for_word(words[call_pc] & WORD_MASK)
        if call_op is None or not call_op.branch_kind.startswith("call_"):
            continue
        stop_pc = ref.source_pc if ref.source_pc is not None else call_pc
        run_start = stop_pc
        while run_start > 0:
            previous_pc = run_start - 1
            previous_word = words[previous_pc] & WORD_MASK
            if previous_pc in branch_sources or previous_word & 0x80000000:
                break
            run_start = previous_pc
        run = [words[pc] & WORD_MASK for pc in range(run_start, stop_pc)]
        text_args = [value for value in run if value in text_indices]
        if len(text_args) < 2:
            continue
        low = min(text_args) - 1
        high = max(text_args) + 1
        for pc in range(run_start, stop_pc):
            value = words[pc] & WORD_MASK
            if not (low <= value <= high and 0 < value < data_words):
                continue
            if value in text_indices or data[value * 4 : value * 4 + 4] != b"\0" * 4:
                continue
            data_refs.setdefault(pc, set()).add(value)
    return spans


def _emit_code(
    words: list[int],
    data: bytes,
    encoding: str,
    inline_blocks: dict[int, list[DataBlock]] | None = None,
    analysis: tuple[dict[int, set[int]], dict[int, BranchReference]] | None = None,
) -> list[str]:
    if analysis is None:
        data_refs, _, branches = analyze_code(words, data)
        _recover_implicit_branch_refs(words, branches)
        _infer_direct_text_refs(words, data, encoding, data_refs, branches)
    else:
        data_refs, branches = analysis
    # The compiler emits every observed branch target as the immediately
    # preceding ordinary dword.  Recover that form even when a control-flow
    # merge made the abstract stack state opaque.
    _recover_implicit_branch_refs(words, branches)
    targets = {ref.target_pc for ref in branches.values()}
    targets.add(0)
    collapsed_sources: set[int] = set()
    for ref in branches.values():
        if ref.source_pc == ref.branch_pc - 1:
            collapsed_sources.add(ref.source_pc)

    lines: list[str] = [".section code"]
    for pc, word in enumerate(words):
        if pc in targets:
            lines.extend(["", f"{_label_for_code(pc)}:"])
        if pc in collapsed_sources:
            for block in (inline_blocks or {}).get(pc, ()):
                lines.extend(_emit_data_block(block))
            continue
        word &= WORD_MASK
        if not (word & 0x80000000):
            refs = data_refs.get(pc, set())
            if len(refs) == 1:
                index = next(iter(refs))
                operand = _label_for_data(index)
            else:
                operand = f"0x{word:08X}"
            blocks = (inline_blocks or {}).get(pc, ())
            inline_body = _simple_inline_body(blocks[0]) if len(blocks) == 1 else None
            if inline_body is not None:
                lines.append(f"    PUSHI {operand} {inline_body}")
            else:
                lines.append(f"    PUSHI {operand}")
                for block in blocks:
                    lines.extend(_emit_data_block(block))
            continue
        op = opcode_for_word(word)
        if op is None:
            raise DisassemblyError(
                f"unknown opcode 0x{word:08X} at Code word {pc} (byte offset {pc * 4})"
            )
        if op.branch_kind:
            ref = branches.get(pc)
            if ref is None:
                # This is only expected for hand-written/dynamic bytecode.
                # Preserve the raw branch opcode, but make the limitation
                # explicit instead of guessing a target.
                lines.append(f"    {op.mnemonic}")
            else:
                lines.append(f"    {op.mnemonic} {_label_for_code(ref.target_pc)}")
        else:
            lines.append(f"    {op.mnemonic}")
        for block in (inline_blocks or {}).get(pc, ()):
            lines.extend(_emit_data_block(block))
    return lines


def _build_data_blocks(
    data: bytes,
    encoding: str,
    required_indices: Iterable[int],
    spans: list[TextSpan] | None = None,
) -> list[DataBlock]:
    required_offsets = [index * 4 for index in required_indices]
    spans = spans if spans is not None else discover_text_spans(data, encoding, required_offsets)
    span_by_start = {span.offset: span for span in spans}
    boundaries = sorted(
        offset
        for offset in set(required_offsets) | set(span_by_start) | {0}
        if 0 <= offset < len(data)
    )
    blocks: list[DataBlock] = []
    for index, boundary in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(data)
        body: list[str] = []
        pos = boundary
        span = span_by_start.get(boundary)
        if span is not None:
            body.append(f'TEXT "{span.rendered}"')
            pos = span.end
        if pos < end:
            tail = data[pos:end]
            if span is not None:
                # TEXT supplies its terminator.  The assembler aligns the
                # next data label automatically, so ordinary 1-3 byte zero
                # padding does not need to be printed.
                body.extend(_format_text_tail(tail))
            else:
                if tail and not any(tail):
                    body.append(f".zero {len(tail)}")
                else:
                    body.extend(_format_bytes(tail))
        blocks.append(
            DataBlock(
                order=boundary,
                label=_label_for_data(boundary // 4),
                body=tuple(body),
            )
        )
    return blocks


def _emit_data(data: bytes, encoding: str, required_indices: Iterable[int]) -> list[str]:
    required_offsets = [index * 4 for index in required_indices]
    spans = discover_text_spans(data, encoding, required_offsets)
    span_by_start = {span.offset: span for span in spans}
    labels = set(required_offsets)
    labels.update(span_by_start)
    labels.add(0)
    boundaries = sorted(offset for offset in labels if 0 <= offset <= len(data))

    lines: list[str] = [".section data"]
    pos = 0
    for boundary in boundaries:
        if boundary < pos:
            continue
        if boundary > pos:
            lines.extend("    " + line for line in _format_bytes(data[pos:boundary]))
            pos = boundary
        lines.extend(["", "    .align 4", f"{_label_for_data(boundary // 4)}:"])
        span = span_by_start.get(boundary)
        if span is not None and span.end > pos:
            lines.append(f'    TEXT "{span.rendered}"')
            pos = span.end
    if pos < len(data):
        lines.extend("    " + line for line in _format_bytes(data[pos:]))
    return lines


def disassemble_pair(
    code_path: Path,
    data_path: Path,
    encoding: str,
    output_encoding: str = "utf-8",
    inline_data: bool = True,
) -> str:
    code_bytes = code_path.read_bytes()
    data_raw = data_path.read_bytes()
    if len(code_bytes) % 4:
        raise DisassemblyError(f"Code file size is not a multiple of 4: {code_path}")
    words = list(struct.unpack(f"<{len(code_bytes) // 4}I", code_bytes))
    data = bytes(byte ^ DATA_XOR for byte in data_raw)
    data_refs, _, branches = analyze_code(words, data)
    _recover_implicit_branch_refs(words, branches)
    text_spans = _infer_direct_text_refs(words, data, encoding, data_refs, branches)
    required_indices = sorted({index for indexes in data_refs.values() for index in indexes})

    lines = [
        "; Dual Colors SSB VM semantic assembly",
        f"; source code: {code_path.name}",
        f"; source data: {data_path.name}",
        f'.encoding "{encoding}"',
        f'.file_encoding "{output_encoding}"',
        f".data_xor 0x{DATA_XOR:02X}",
        "",
    ]
    if inline_data:
        blocks = _build_data_blocks(data, encoding, required_indices, text_spans)
        first_reference: dict[int, int] = {}
        for pc, indexes in data_refs.items():
            if len(indexes) == 1:
                index = next(iter(indexes))
                first_reference[index] = min(pc, first_reference.get(index, pc))
        blocks_by_pc: dict[int, list[DataBlock]] = {}
        trailing_blocks: list[DataBlock] = []
        for block in blocks:
            pc = first_reference.get(block.order // 4)
            if pc is None:
                trailing_blocks.append(block)
            else:
                blocks_by_pc.setdefault(pc, []).append(block)
        lines.extend(_emit_code(words, data, encoding, blocks_by_pc, (data_refs, branches)))
        if trailing_blocks:
            lines.extend(["", "; Data blocks without a unique static code reference"])
            for block in trailing_blocks:
                lines.extend(_emit_data_block(block))
    else:
        lines.extend(_emit_code(words, data, encoding))
        lines.extend(_emit_data(data, encoding, required_indices))
    return "\n".join(lines) + "\n"


def _find_pair(path: Path, data_override: Path | None) -> tuple[Path, Path]:
    if path.is_dir():
        code = path / "Script" / "Code.ssb"
        data = path / "Script" / "Data.ssb"
        if not code.exists():
            code = path / "Code.ssb"
            data = path / "Data.ssb"
        if not code.exists():
            raise DisassemblyError(f"cannot find Code.ssb below {path}")
        if data_override is not None:
            data = data_override
        if not data.exists():
            raise DisassemblyError(f"cannot find Data.ssb beside {code}")
        return code, data
    if path.name.lower() == "data.ssb":
        data = path
        code = path.with_name("Code.ssb")
    else:
        code = path
        data = path.with_name("Data.ssb")
    if data_override is not None:
        data = data_override
    if not code.exists() or not data.exists():
        raise DisassemblyError(f"Code/Data pair not found for {path}")
    return code, data


def _default_output(code: Path, input_path: Path) -> Path:
    if input_path.is_dir() or code.name.lower() == "code.ssb":
        return input_path / "asm.txt" if input_path.is_dir() else code.parent / "asm.txt"
    return code.with_name(code.stem + ".asm.txt")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Disassemble Dual Colors Code.ssb/Data.ssb")
    parser.add_argument("inputs", nargs="+", type=Path, help="Code.ssb, Data.ssb, or a directory")
    parser.add_argument("-o", "--output", type=Path, help="assembly output path (single input only)")
    parser.add_argument("--data", type=Path, help="explicit Data.ssb path")
    parser.add_argument("--encoding", default="cp932", help="script text codec (default: cp932)")
    parser.add_argument(
        "--output-encoding",
        default="utf-8",
        help="assembly file codec (default: utf-8; .encoding remains the script codec)",
    )
    parser.add_argument(
        "--separate-data",
        action="store_true",
        help="emit the legacy .section data layout instead of inline .data_block definitions",
    )
    args = parser.parse_args(argv)

    try:
        codecs.lookup(args.encoding)
        codecs.lookup(args.output_encoding)
        if args.output is not None and len(args.inputs) != 1:
            parser.error("-o/--output requires exactly one input")
        for input_path in args.inputs:
            code, data = _find_pair(input_path, args.data)
            output = args.output if args.output is not None else _default_output(code, input_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                disassemble_pair(
                    code,
                    data,
                    args.encoding,
                    args.output_encoding,
                    inline_data=not args.separate_data,
                ),
                encoding=args.output_encoding,
                newline="\n",
            )
            print(f"wrote {output} ({code.name} + {data.name})")
    except (LookupError, OSError, DisassemblyError) as exc:
        print(f"disassembler: error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
