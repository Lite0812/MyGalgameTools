"""Instruction definitions for the Dual Colors SSB virtual machine.

The VM stores one instruction or immediate value in every little-endian
32-bit word.  A word with bit 31 clear is a signed/unsigned immediate pushed
to the main stack.  A word with bit 31 set is dispatched by its high 16 bits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


DATA_XOR = 0xAA
WORD_SIZE = 4


@dataclass(frozen=True)
class Opcode:
    """Metadata needed by both the disassembler and assembler."""

    value: int
    mnemonic: str
    pops: int = 0
    pushes: int = 0
    data_refs: tuple[int, ...] = ()
    branch_kind: str | None = None
    description: str = ""

    @property
    def group(self) -> int:
        return self.value & 0xFFFF0000

    @property
    def subop(self) -> int:
        return self.value & 0xFFFF

    @property
    def relative(self) -> bool:
        return self.branch_kind in {"jmp_rel", "call_rel", "jnz_rel", "jz_rel"}


OPCODES: dict[int, Opcode] = {}
OPCODES_BY_MNEMONIC: dict[str, Opcode] = {}


def _add(
    group: int,
    subop: int,
    mnemonic: str,
    *,
    pops: int = 0,
    pushes: int = 0,
    data_refs: Iterable[int] = (),
    branch_kind: str | None = None,
    description: str = "",
) -> None:
    value = (group | subop) & 0xFFFFFFFF
    op = Opcode(
        value=value,
        mnemonic=mnemonic,
        pops=pops,
        pushes=pushes,
        data_refs=tuple(data_refs),
        branch_kind=branch_kind,
        description=description,
    )
    OPCODES[value] = op
    OPCODES_BY_MNEMONIC[mnemonic.upper()] = op


# 0x8000: stack, Data and control flow.
_STACK = 0x80000000
_add(_STACK, 0x00, "PUSH_DATA32", pops=1, pushes=1, data_refs=(1,))
_add(_STACK, 0x01, "STORE_DATA32", pops=2, data_refs=(1,))
_add(_STACK, 0x02, "LOAD_DATA8", pops=2, pushes=1, data_refs=(2,))
_add(_STACK, 0x03, "STORE_DATA8", pops=3, data_refs=(2,))
_add(_STACK, 0x04, "DUP", pops=1, pushes=2)
_add(_STACK, 0x05, "REORDER3", pops=2, pushes=3)
_add(_STACK, 0x06, "DROP", pops=1)
_add(_STACK, 0x07, "RET_SECONDARY")
_add(_STACK, 0x08, "SET_VM_ERROR")
_add(_STACK, 0x09, "SET_BREAK")
_add(_STACK, 0x0A, "JMP_ABS", pops=1, branch_kind="jmp_abs")
_add(_STACK, 0x0B, "JMP_REL", pops=1, branch_kind="jmp_rel")
_add(_STACK, 0x0C, "CALL_ABS", pops=1, branch_kind="call_abs")
_add(_STACK, 0x0D, "CALL_REL", pops=1, branch_kind="call_rel")
_add(_STACK, 0x0E, "JNZ_ABS", pops=2, branch_kind="jnz_abs")
_add(_STACK, 0x0F, "JNZ_REL", pops=2, branch_kind="jnz_rel")
_add(_STACK, 0x10, "JZ_ABS", pops=2, branch_kind="jz_abs")
_add(_STACK, 0x11, "JZ_REL", pops=2, branch_kind="jz_rel")
_add(_STACK, 0x12, "SWAP", pops=2, pushes=2)
_add(_STACK, 0x13, "SCRIPT_ABORT")
_add(_STACK, 0x14, "DROP_SECONDARY", pops=1)


# 0x8001: integer operators.
_ARITH = 0x80010000
_ARITHMETIC = [
    (0x00, "ADD", 2, 1),
    (0x01, "SUB", 2, 1),
    (0x02, "MUL", 2, 1),
    (0x03, "DIV", 2, 1),
    (0x04, "MOD", 2, 1),
    (0x05, "OR", 2, 1),
    (0x06, "AND", 2, 1),
    (0x07, "XOR", 2, 1),
    (0x08, "NOT", 1, 1),
    (0x09, "NEG", 1, 1),
    (0x0A, "CMP_EQ", 2, 1),
    (0x0B, "CMP_NE", 2, 1),
    (0x0C, "CMP_GE_0C", 2, 1),
    (0x0D, "CMP_GE_0D", 2, 1),
    (0x0E, "CMP_LT", 2, 1),
    (0x0F, "CMP_LE", 2, 1),
    (0x10, "MUL_ALT", 2, 1),
    (0x11, "DIV_ALT", 2, 1),
    (0x12, "MOD_ALT", 2, 1),
    (0x13, "CMP_GT", 2, 1),
    (0x14, "CMP_GE", 2, 1),
    (0x15, "CMP_LT_ALT", 2, 1),
    (0x16, "CMP_LE_ALT", 2, 1),
    (0x17, "SHR", 2, 1),
    (0x18, "SHL", 2, 1),
    (0x19, "SAR", 2, 1),
]
for _subop, _name, _pops, _pushes in _ARITHMETIC:
    _add(_ARITH, _subop, _name, pops=_pops, pushes=_pushes)


# 0x8002: random, cursor, timer and main-window text.
_UI = 0x80020000
_add(_UI, 0x00, "RAND_MOD", pops=1, pushes=1)
_add(_UI, 0x01, "SHOW_CURSOR", pops=1)
_add(_UI, 0x02, "CURSOR_TIMER", pops=1)
_add(_UI, 0x03, "SET_MAIN_TEXT", pops=1, data_refs=(1,))
_add(_UI, 0x04, "SET_TIMER_VALUE", pops=1)
_add(_UI, 0x05, "READ_TIME")


# 0x8004: debug output.
_DEBUG = 0x80040000
_add(_DEBUG, 0x00, "DEBUG_INT", pops=1)
_add(_DEBUG, 0x01, "DEBUG_DATA", pops=1, data_refs=(1,))
_add(_DEBUG, 0x02, "DEBUG_RESPONSE")


# 0x8005: input and mouse.
_INPUT = 0x80050000
_add(_INPUT, 0x00, "INPUT_SIZE", pushes=1)
_add(_INPUT, 0x01, "INPUT_BITS", pushes=1)
_add(_INPUT, 0x02, "KEY_STATE", pops=1, pushes=1)
_add(_INPUT, 0x03, "SET_CURSOR_POS", pops=2)


# 0x8006: display mode.
_DISPLAY = 0x80060000
_add(_DISPLAY, 0x00, "REBUILD_DISPLAY", pops=3)
_add(_DISPLAY, 0x01, "GET_DISPLAY_MODE", pushes=1)


# 0x8007: saves, resources and scene helpers.
_SAVE = 0x80070000
_add(_SAVE, 0x00, "LOAD_SAVE_VM", pops=1)
_add(_SAVE, 0x01, "STORE_SAVE_VM", pops=1)
_add(_SAVE, 0x06, "QUERY_RESOURCE", pops=1, pushes=1, data_refs=(1,))
_add(_SAVE, 0x07, "WRITE_RESOURCE_STATE", pops=1, data_refs=(1,))
_add(_SAVE, 0x08, "RESOURCE_EXISTS", pops=1, pushes=1, data_refs=(1,))
_add(_SAVE, 0x09, "SET_RESOURCE_INDEX", pops=1)
_add(_SAVE, 0x0A, "SHOW_SAVE_INFO", pops=2, data_refs=(2,))
_add(_SAVE, 0x0B, "GET_SAVE_STATUS", pushes=1)
_add(_SAVE, 0x0C, "SAVE_EXISTS", pops=1, pushes=1, data_refs=(1,))
_add(_SAVE, 0x0D, "GET_SAVE_TIME", pops=1, pushes=6, data_refs=(1,))
_add(_SAVE, 0x0E, "SCENE_RESOURCE_OP", pops=2)
_add(_SAVE, 0x0F, "LOAD_SAVE_PATH", pops=1, data_refs=(1,))
_add(_SAVE, 0x10, "SAVE_PATH_OP", pops=3, data_refs=(3,))


# 0x8008: GRD/image/object host calls.  The names describe only the
# observed call shape; the host object's business semantics are not guessed.
_OBJECT = 0x80080000
_add(_OBJECT, 0x00, "OBJECT_DRAW_BASE", pops=5)
_add(_OBJECT, 0x01, "GRD_LOAD", pops=3, data_refs=(3,))
_add(_OBJECT, 0x02, "PIXEL_BUFFER_DRAW", pops=5)
_add(_OBJECT, 0x03, "AUX_RELEASE", pops=1)
_add(_OBJECT, 0x04, "DRAW_UPDATE_1", pops=5)
_add(_OBJECT, 0x05, "OBJECT_UPDATE", pops=2)
_add(_OBJECT, 0x06, "DRAW_UPDATE_LONG", pops=11)
_add(_OBJECT, 0x07, "DRAW_UPDATE_2", pops=7)
_add(_OBJECT, 0x08, "OBJECT_RELEASE", pops=1)
_add(_OBJECT, 0x09, "OBJECT_CREATE_REPLACE", pops=6)
_add(_OBJECT, 0x0A, "SET_GRD_MODE", pops=1)
_add(_OBJECT, 0x0B, "GET_OBJECT_SIZE", pops=1, pushes=2)


# 0x8009: controls and host objects.
_CONTROL = 0x80090000
_add(_CONTROL, 0x00, "CONTROL_CREATE_DRAW", pops=4)
_add(_CONTROL, 0x01, "CONTROL_RELEASE", pops=1)
_add(_CONTROL, 0x02, "CONTROL_SET_DATA_TEXT", pops=2, data_refs=(2,))
_add(_CONTROL, 0x03, "CONTROL_SET_VALUE_TEXT", pops=2)
_add(_CONTROL, 0x04, "CONTROL_GET_VALUE", pops=1, pushes=1)
_add(_CONTROL, 0x05, "CONTROL_ATTACH", pops=3)
_add(_CONTROL, 0x06, "CONTROL_SET_PARAM_1", pops=2)
_add(_CONTROL, 0x07, "CONTROL_SET_PARAM_2", pops=2)
_add(_CONTROL, 0x08, "CONTROL_SET_FIELD40", pops=2)


# 0x800A: MIDI.
_MIDI = 0x800A0000
_add(_MIDI, 0x00, "MIDI_PLAY_1", pops=1, data_refs=(1,))
_add(_MIDI, 0x01, "MIDI_STOP")
_add(_MIDI, 0x02, "MIDI_PLAY_2", pops=1, data_refs=(1,))
_add(_MIDI, 0x03, "MIDI_GET_STATE", pushes=1)
_add(_MIDI, 0x04, "MIDI_SET_PARAM", pops=1)


# 0x800B: WAV.
_WAV = 0x800B0000
_add(_WAV, 0x00, "WAV_PLAY", pops=2, data_refs=(2,))
_add(_WAV, 0x01, "WAV_STOP", pops=1)
_add(_WAV, 0x02, "WAV_GET_STATE", pops=1, pushes=1)
_add(_WAV, 0x03, "WAV_SET_PARAM_1", pops=1)
_add(_WAV, 0x04, "WAV_SET_PARAM_2", pops=2)
_add(_WAV, 0x05, "WAV_SET_PARAM_3", pops=1)
_add(_WAV, 0x06, "WAV_SET_PARAM_4", pops=2)
_add(_WAV, 0x07, "WAV_PLAY_LOOP", pops=2, data_refs=(2,))


# 0x800C: input/selection state.
_SELECT = 0x800C0000
_add(_SELECT, 0x00, "INPUT_SELECT", pops=1)
_add(_SELECT, 0x01, "INPUT_STATE_3")
_add(_SELECT, 0x02, "INPUT_GET", pushes=1)
_add(_SELECT, 0x03, "INPUT_SET", pops=1)
_add(_SELECT, 0x04, "INPUT_CLEAR")


# 0x800D: audio object.
_AUDIO = 0x800D0000
_add(_AUDIO, 0x00, "AUDIO_FIND", pops=1, pushes=1, data_refs=(1,))
_add(_AUDIO, 0x01, "AUDIO_PLAY_1", pops=1)
_add(_AUDIO, 0x02, "AUDIO_STOP")
_add(_AUDIO, 0x03, "AUDIO_DELAY", pops=1)
_add(_AUDIO, 0x04, "AUDIO_PLAY_0", pops=1)
_add(_AUDIO, 0x05, "AUDIO_GET_STATE", pushes=1)


KNOWN_GROUPS = frozenset(op.group for op in OPCODES.values())
BRANCH_OPCODES = frozenset(value for value, op in OPCODES.items() if op.branch_kind)


def opcode_for_word(word: int) -> Opcode | None:
    """Return an opcode definition for a 32-bit word, or ``None``."""

    return OPCODES.get(word & 0xFFFFFFFF)


def opcode_for_mnemonic(mnemonic: str) -> Opcode | None:
    return OPCODES_BY_MNEMONIC.get(mnemonic.upper())


def is_opcode_word(word: int) -> bool:
    """Whether the word is a known VM instruction rather than an immediate."""

    word &= 0xFFFFFFFF
    return bool(word & 0x80000000) and word in OPCODES


def is_unknown_opcode_word(word: int) -> bool:
    word &= 0xFFFFFFFF
    return bool(word & 0x80000000) and word not in OPCODES


def signed32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def unsigned32(value: int) -> int:
    return value & 0xFFFFFFFF


__all__ = [
    "DATA_XOR",
    "WORD_SIZE",
    "Opcode",
    "OPCODES",
    "OPCODES_BY_MNEMONIC",
    "KNOWN_GROUPS",
    "BRANCH_OPCODES",
    "opcode_for_word",
    "opcode_for_mnemonic",
    "is_opcode_word",
    "is_unknown_opcode_word",
    "signed32",
    "unsigned32",
]
