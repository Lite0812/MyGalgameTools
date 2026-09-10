"""Opcode definitions for the ADV.COM scenario virtual machine.

The byte patterns and operand schemas in this module are derived from the
dispatcher at ADV 0C00:0166 and the expression evaluator at ADV 0C00:0A81.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Opcode:
    bytecode: int
    mnemonic: str
    operands: tuple[str, ...] = ()
    description: str = ""
    control_flow: str | None = None
    sub_opcodes: Mapping[int, str] = field(default_factory=dict)
    variants: tuple[str, ...] = ()


EXPR_OPERATORS = {
    0x77: "MUL",
    0x78: "DIV",
    0x79: "ADD",
    0x7A: "SUB",
    0x7B: "EQ",
    0x7C: "LT",
    0x7D: "GT",
    0x7E: "NE",
}

EXPR_OPERATOR_BYTES = {name: byte for byte, name in EXPR_OPERATORS.items()}


B_SUB_OPCODES = {
    0: "NOOP",
    1: "DEFINE_PRIMARY",
    2: "ACTIVATE_PRIMARY",
    3: "DEFINE_SECONDARY",
    4: "ACTIVATE_SECONDARY",
}

K_SUB_OPCODES = {
    0: "TEXT_WAIT_AND_ADVANCE",
    1: "POLL_INPUT_MODE_1",
    2: "POLL_INPUT_MODE_2",
    3: "POLL_INPUT_MODE_3",
    4: "WAIT_INPUT",
    5: "WAIT_INPUT_TO_VAR0",
    6: "TIMED_WAIT_TO_VAR0",
}

N_SUB_OPCODES = {
    1: "ADD_MAPPING",
    2: "REMOVE_MAPPING",
    3: "SET_MAPPING_MODE",
}

Z_SUB_OPCODES = {
    0: "DISPLAY_BANK",
    1: "TEXT_COLOR",
    2: "TEXT_COLOR_ALT",
    3: "CONFIG_3",
    4: "CONFIG_4",
    5: "CONFIG_5",
    6: "CONFIG_6",
}

# Y's selector is an expression result rather than a literal extension byte.
Y_SELECTORS = frozenset(
    {
        0x00,
        0x01,
        0x02,
        0x03,
        0x04,
        0x05,
        0x06,
        0x07,
        0x08,
        0x0A,
        0x0D,
        0x0F,
        0x10,
        0x15,
        0x16,
        0x17,
        0x18,
        0x19,
        0x1A,
        0x1B,
        0x1C,
        0x1E,
        0x1F,
        0x28,
        0x29,
        0x2A,
        0x2B,
        0x2D,
        0x32,
        0x33,
        0x3C,
        0x3D,
        0x46,
        0x47,
        0x48,
        0x49,
        0xFA,
        0xFB,
        0xFC,
        0xFD,
        0xFE,
        0xFF,
    }
    | set(range(0xDD, 0xE4))
)


OPCODES = {
    0x21: Opcode(0x21, "SET_VAR", ("varref", "expr"), "Assign an expression to a global variable."),
    0x24: Opcode(
        0x24,
        "CHOICE_DELIM",
        ("stateful",),
        "Alternate between a u16le choice target and a zero-operand text terminator.",
        variants=("CHOICE_BEGIN", "CHOICE_END"),
    ),
    0x25: Opcode(0x25, "PAGE_CALL", ("expr",), "Call another page, or return when the value is zero.", "call"),
    0x26: Opcode(0x26, "PAGE_GOTO", ("expr",), "Transfer to another page.", "jump"),
    0x40: Opcode(0x40, "JMP", ("abs16le",), "Absolute intra-member jump.", "jump"),
    0x41: Opcode(0x41, "TEXT_BREAK", (), "Commit/wait for text and reset display state."),
    0x42: Opcode(0x42, "WINDOW_CONTROL", ("sub8", "expr", "expr", "expr", "expr", "expr", "expr"), sub_opcodes=B_SUB_OPCODES),
    0x45: Opcode(0x45, "WINDOW_SLOT", ("expr", "expr", "expr", "expr", "expr", "expr")),
    0x46: Opcode(0x46, "RESTART_PAGE", (), "Reset the instruction pointer to offset 2.", "jump"),
    0x47: Opcode(0x47, "LOAD_GRAPHIC", ("expr",)),
    0x48: Opcode(0x48, "PRINT_VALUE", ("imm8", "expr")),
    0x49: Opcode(0x49, "BLIT_RECT", ("expr", "expr", "expr", "expr", "expr", "expr")),
    0x4A: Opcode(0x4A, "GRAPHIC_0E", ("expr", "expr")),
    0x4B: Opcode(0x4B, "INPUT_WAIT", ("sub8",), sub_opcodes=K_SUB_OPCODES),
    0x4C: Opcode(0x4C, "LOAD_STATE", ("expr",)),
    0x4D: Opcode(0x4D, "DEFINE_NAME", ("colon_text",)),
    0x4E: Opcode(0x4E, "MAP_CONTROL", ("sub8", "expr", "expr"), sub_opcodes=N_SUB_OPCODES),
    0x4F: Opcode(
        0x4F,
        "BIT_VECTOR",
        ("expr", "expr"),
        variants=("UNPACK_VALUE", "PACK_TO_BARE_VARREF"),
    ),
    0x50: Opcode(0x50, "SET_PALETTE", ("expr", "expr", "expr", "expr")),
    0x51: Opcode(0x51, "SAVE_STATE", ("expr",)),
    0x52: Opcode(0x52, "ADVANCE_LINE"),
    0x53: Opcode(0x53, "LOAD_AUDIO", ("imm8",)),
    0x54: Opcode(0x54, "SET_TEXT_POS", ("expr", "expr")),
    0x55: Opcode(0x55, "LOAD_GRAPHIC_EX", ("expr", "expr")),
    0x56: Opcode(0x56, "NAME_BUFFER_COPY", ("expr", "expr")),
    0x57: Opcode(0x57, "GRAPHIC_39", ("expr", "expr", "expr")),
    0x58: Opcode(0x58, "INSERT_NAME", ("imm8",)),
    0x59: Opcode(0x59, "SYSTEM", ("expr", "expr")),
    0x5A: Opcode(0x5A, "CONFIG", ("expr", "expr"), sub_opcodes=Z_SUB_OPCODES),
    0x5C: Opcode(0x5C, "LOCAL_CALL", ("abs16le_or_zero",), "Call a local address, or return for target zero.", "call"),
    0x5D: Opcode(0x5D, "END_BLOCK", (), "Return from the interpreter to the host loop.", "return"),
    0x7B: Opcode(0x7B, "IF_FALSE_JMP", ("expr", "abs16le"), "Jump when the expression is zero.", "conditional_jump"),
    0x7D: Opcode(0x7D, "IF_FALSE_SKIP", ("expr", "rel8"), "Forward relative jump when the expression is zero.", "conditional_jump"),
}

MNEMONIC_TO_OPCODE = {definition.mnemonic: definition for definition in OPCODES.values()}

PSEUDO_MNEMONICS = frozenset(
    {
        "TEXT",
        "CHOICE_BEGIN",
        "CHOICE_END",
        "TAIL_ENTRY",
        "TAIL_IF",
        "TAIL_END",
    }
)


def is_text_lead(byte: int) -> bool:
    """Return True for a two-byte text atom's first byte."""

    return 0x80 <= byte < 0xA0 or 0xE0 <= byte <= 0xFF


def is_single_text_byte(byte: int) -> bool:
    return byte == 0x20 or 0xA0 <= byte < 0xE0


def is_top_level_text_start(byte: int) -> bool:
    return is_single_text_byte(byte) or is_text_lead(byte)


__all__ = [
    "B_SUB_OPCODES",
    "EXPR_OPERATORS",
    "EXPR_OPERATOR_BYTES",
    "K_SUB_OPCODES",
    "MNEMONIC_TO_OPCODE",
    "N_SUB_OPCODES",
    "OPCODES",
    "Opcode",
    "PSEUDO_MNEMONICS",
    "Y_SELECTORS",
    "Z_SUB_OPCODES",
    "is_single_text_byte",
    "is_text_lead",
    "is_top_level_text_start",
]
