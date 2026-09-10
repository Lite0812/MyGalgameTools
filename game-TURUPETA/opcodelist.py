"""WS2 VM opcode and container definitions.

Definitions are derived from ws2_vm_analysis.md.  The opcode mnemonics intentionally
avoid a WS2_/ws_ prefix so they can be used directly in asm text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpcodeDef:
    code: tuple[int, ...]
    mnemonic: str
    operands: tuple[dict[str, Any], ...] = ()
    handler: str = ""
    description: str = ""
    invalid: bool = False

    @property
    def byte_pattern(self) -> str:
        return " ".join(f"{b:02X}" for b in self.code)


def op(code: int | tuple[int, ...], mnemonic: str, operands=(), handler="", description="", invalid=False) -> OpcodeDef:
    if isinstance(code, int):
        code_tuple = (code,)
    else:
        code_tuple = tuple(code)
    return OpcodeDef(code_tuple, mnemonic, tuple(operands), handler, description, invalid)


def u8(name: str = "value") -> dict[str, Any]:
    return {"type": "u8", "name": name}


def i8(name: str = "value") -> dict[str, Any]:
    return {"type": "i8", "name": name}


def u16(name: str = "value") -> dict[str, Any]:
    return {"type": "u16", "name": name}


def u32(name: str = "value") -> dict[str, Any]:
    return {"type": "u32", "name": name}


def script_id(name: str = "script_id") -> dict[str, Any]:
    return {"type": "script_id", "name": name}


def label_id(name: str = "label_id") -> dict[str, Any]:
    return {"type": "u32", "name": name}


def ws2_string(name: str = "text") -> dict[str, Any]:
    return {"type": "ws2_string", "name": name}


def string_list(name: str = "strings") -> dict[str, Any]:
    return {"type": "string_list_u8", "name": name}


# Container/package constants.
COMPRESSION_HEADER_SIZE = 0x20
FORMAT_COPY = 0
FORMAT_XG_ZLIB = 1
FORMAT_XB_BZIP2 = 2
FORMAT_COPY_ALIAS = 3
VALID_FORMATS = {FORMAT_COPY, FORMAT_XG_ZLIB, FORMAT_XB_BZIP2, FORMAT_COPY_ALIAS}

# Direct runtime opcodes.
OPCODES: dict[tuple[int, ...], OpcodeDef] = {
    (0x00,): op(0x00, "RESET_EXEC_GATE", handler="sub_42C1B0"),
    (0x01,): op(0x01, "CLEAR_TEXT_QUEUE", handler="sub_42D540"),
    (0x02,): op(0x02, "QUEUE_TEXT_RECORD", [u8(), ws2_string()], handler="sub_42D9D0"),
    (0x03,): op(0x03, "START_TEXT_OUTPUT", handler="sub_42C1D0"),
    (0x04,): op(0x04, "SET_VISUAL_SLOT", [u8("slot"), u8("value")], handler="sub_42D5A0"),
    (0x05,): op(0x05, "SET_VISUAL_RESOURCE", [u8("mode"), string_list()], handler="sub_42EF80"),
    (0x06,): op(0x06, "RUN_DRAW_TRANSITION", handler="sub_42C220"),
    (0x07,): op(0x07, "SET_DRAW_EFFECT", [u8("mode"), string_list()], handler="sub_42F480"),
    (0x08,): op(0x08, "SET_TRANSITION_STEP", [u8()], handler="sub_42C260"),
    (0x09,): op(0x09, "SET_TRANSITION_MODE", [i8("mode")], handler="sub_42C2A0"),
    (0x0A,): op(0x0A, "SHOW_INLINE_TEXT", [ws2_string()], handler="sub_42CA80"),
    (0x0B,): op(0x0B, "PLAY_TABLE_SOUND", [string_list()], handler="sub_42F6C0"),
    (0x0C,): op(0x0C, "STOP_TABLE_SOUND", handler="sub_42D670"),
    (0x0D,): op(0x0D, "LOAD_AUDIO_FILE", [string_list()], handler="sub_42F850"),
    (0x0E,): op(0x0E, "PLAY_VOICE", [string_list()], handler="sub_42F960"),
    (0x0F,): op(0x0F, "CLEAR_WAIT_TIMER", handler="sub_42C380"),
    (0x10,): op(0x10, "WAIT_TIME", [u32("ticks")], handler="sub_42C3B0"),
    (0x11,): op(0x11, "WAIT_TEXT_ADVANCE", handler="sub_42C400"),
    (0x12,): op(0x12, "PUSH_CONST", [u32()], handler="sub_42DAD0"),
    (0x13,): op(0x13, "POP_STACK", handler="sub_42CDB0"),
    (0x14,): op(0x14, "PUSH_FLAG", [u32("flag_index")], handler="sub_42DB60"),
    (0x15,): op(0x15, "POP_FLAG", [u32("flag_index")], handler="sub_42CEC0"),
    (0x16,): op(0x16, "PUSH_PERSIST_FLAG", [u32("flag_index")], handler="sub_42DC30"),
    (0x17,): op(0x17, "POP_PERSIST_FLAG", [u32("flag_index")], handler="sub_42D030"),
    (0x18,): op(0x18, "ADD", handler="sub_42DD00"),
    (0x19,): op(0x19, "SUB", handler="sub_42DE40"),
    (0x1A,): op(0x1A, "EQ", handler="sub_42DF80"),
    (0x1B,): op(0x1B, "GT", handler="sub_42E0D0"),
    (0x1C,): op(0x1C, "LT", handler="sub_42E220"),
    (0x1D,): op(0x1D, "AND", handler="sub_42E370"),
    (0x1E,): op(0x1E, "OR", handler="sub_42E4E0"),
    (0x1F,): op(0x1F, "BOOL_NOT", handler="sub_42C840"),
    (0x20,): op(0x20, "IF_TRUE", handler="sub_42D190"),
    (0x21,): op(0x21, "IF_FALSE", handler="sub_42D2C0"),
    (0x22,): op(0x22, "GOTO_LABEL", [script_id()], handler="sub_42C460"),
    (0x23,): op(0x23, "CALL_LABEL", [script_id()], handler="sub_42C570"),
    (0x24,): op(0x24, "RETURN_LABEL", handler="sub_42D3F0"),
    (0x25,): op(0x25, "RUN_SYSTEM_COMMAND", [string_list()], handler="sub_42FAC0"),
    (0x26,): op(0x26, "MAX_VALUE", [u8("count")], handler="sub_42E650"),
    (0x27,): op(0x27, "MIN_VALUE", [u8("count")], handler="sub_42E7A0"),
    (0x28,): op(0x28, "ABS_VALUE", handler="sub_42C930"),
    (0x29,): op(0x29, "PUSH_LABEL_FLAG", [label_id()], handler="sub_42E8F0"),
    (0x2A,): op(0x2A, "ENTER_SCRIPT", [script_id(), ws2_string("name")], handler="sub_42CB80"),
    (0x2B,): op(0x2B, "ENTER_SUBENTRY", [u8("state"), u16("sub_index"), u16("line_or_state")], handler="sub_42C630"),
    (0x2C,): op(0x2C, "SET_LINE_MARK", [u16("line")], handler="sub_42C6E0"),
    (0x2D,): op(0x2D, "MAX_INDEX", [u8("count")], handler="sub_42E990"),
    (0x2E,): op(0x2E, "MIN_INDEX", [u8("count")], handler="sub_42EAF0"),
    (0x2F,): op(0x2F, "CALL_BRANCH_DIALOG", [string_list()], handler="sub_430100"),
    (0x30,): op(0x30, "CALL_BRANCH", [string_list()], handler="sub_430390"),
    (0x31,): op(0x31, "RUN_SUBCOMMAND_10", [string_list()], handler="sub_4305A0"),
    (0x32,): op(0x32, "REPORT_SCRIPT_ERROR", handler="sub_42C730"),
    (0x33,): op(0x33, "CLEAR_UI_STATE", [string_list()], handler="sub_4306B0"),
    (0x34,): op(0x34, "LOAD_UI_RECORDS", [string_list()], handler="sub_4307D0"),
    (0x35,): op(0x35, "CONTROL_UI_MODE", [string_list()], handler="sub_430C90"),
    (0x36,): op(0x36, "LOAD_RESOURCE_SET", [string_list()], handler="sub_431350"),
    (0x37,): op(0x37, "STACK_DUP", [u8("depth")], handler="sub_42EC50"),
    (0x38,): op(0x38, "STACK_SWAP", [u8("depth")], handler="sub_42C9D0"),
    (0x39,): op(0x39, "MUL", handler="sub_42ED00"),
    (0x3A,): op(0x3A, "DIV", handler="sub_42EE40"),
    (0x3B,): op(0x3B, "SET_DISPLAY_MODE", [u8("mode")], handler="sub_42C780"),
    (0x3C,): op(0x3C, "SET_DISPLAY_ENABLE", [u8("enabled")], handler="sub_42C7D0"),
    (0x3D,): op(0x3D, "RUN_UI_ACTION", [u8("mode"), string_list()], handler="sub_431540"),
    (0x3E,): op(0x3E, "CLEAR_VISUAL_SLOTS", handler="sub_42D6E0"),
    (0x3F,): op(0x3F, "NOP", handler="sub_431750"),
    (0x40,): op(0x40, "INVALID_RESERVED", invalid=True),
    (0x41,): op(0x41, "SET_EXT_PARAMS", [u8(), u8()], handler="sub_4316F0"),
    (0x42,): op(0x42, "RUN_EXT_ACTION", [u8("mode"), string_list()], handler="sub_431840"),
    (0x43,): op(0x43, "RANDOM", handler="sub_431760"),
    (0x44,): op(0x44, "NOP_ALIAS", handler="sub_431750"),
    (0x80, 0x00): op((0x80, 0x00), "SET_EXT_PARAMS_ALIAS", [u8(), u8()], handler="sub_4316F0"),
    (0x80, 0x01): op((0x80, 0x01), "RUN_EXT_ACTION_ALIAS", [u8("mode"), string_list()], handler="sub_431840"),
    (0x80, 0x02): op((0x80, 0x02), "RANDOM_ALIAS", handler="sub_431760"),
    (0x80, 0x03): op((0x80, 0x03), "NOP_ALIAS_EXT", handler="sub_431750"),
}

MNEMONIC_TO_OPCODE: dict[str, OpcodeDef] = {v.mnemonic: v for v in OPCODES.values() if not v.invalid}


def opcode_from_bytes(data: bytes, offset: int) -> OpcodeDef | None:
    """Return the opcode definition at offset, or None if it is not executable."""
    if offset >= len(data):
        return None
    first = data[offset]
    if first < 0x80:
        definition = OPCODES.get((first,))
        if definition is None or definition.invalid:
            return None
        return definition
    if offset + 1 >= len(data):
        return None
    definition = OPCODES.get((first, data[offset + 1]))
    if definition is None or definition.invalid:
        return None
    return definition
