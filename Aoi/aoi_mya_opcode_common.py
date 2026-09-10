from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path


SCRIPT_ENCODING = "cp932"
MYA_ASM_FORMAT_VERSION = "AOI-MYA-ASM-2"
MYU_ASM_FORMAT_VERSION = "AOI-MYU-ASM-2"
# Kept as a compatibility alias for callers that imported the old constant.
ASM_FORMAT_VERSION = MYA_ASM_FORMAT_VERSION


OPCODE_ARG_COUNTS = [
    0, 0, 1, 2, 0, 0, 0, 4, 0, 0, 2, 3, 3, 3, 3, 3,
    0, 1, 0, 0, 0, 1, 1, 0, 1, 0, 2, 0, 2, 0, 0, 0,
    5, 0, 2, 0, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0, 3, 0,
    4, 0, 2, 1, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0,
    0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 2, 2, 0, 1,
    0, 0, 4, 4, 0, 0, 2, 2, 0, 0, 6, 6, 0, 0, 2, 2,
    0, 0, 4, 4, 0, 0, 4, 4, 0, 0, 0, 1, 0, 0, 2, 0,
    1, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 0, 0, 0, 0, 0, 0,
    0, 2, 5, 0, 2, 2, 2, 0, 0, 1, 1, 1, 1, 1, 1, 1,
    1, 2, 1, 1, 0, 2, 2, 1, 0, 0, 0, 0, 0, 0, 0, 2,
    2, 2, 2, 0, 1, 1, 1, 0, 0, 1, 1, 1, 2, 3, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 2, 0, 2, 0, 2, 0, 0, 0, 0, 0, 0, 2,
    1, 2, 2, 0, 1, 1, 1, 1, 2, 0, 1, 0, 1, 2, 2, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
]


MYU_OPCODE_ARG_COUNTS = [
    0, 0, 1, 3, 0, 0, 0, 4, 0, 0, 2, 3, 3, 3, 3, 3,
    0, 1, 0, 0, 0, 1, 1, 0, 1, 0, 2, 0, 2, 0, 5, 0,
    5, 0, 2, 0, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0, 3, 0,
    4, 0, 2, 1, 1, 0, 0, 0, 0, 0, 0, 0, 1, 2, 0, 0,
    0, 1, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 2, 2, 0, 1,
    3, 3, 6, 6, 11, 11, 4, 4, 9, 9, 8, 8, 0, 0, 2, 2,
    0, 0, 4, 4, 0, 0, 4, 4, 8, 2, 0, 1, 1, 0, 2, 0,
    1, 0, 2, 0, 0, 0, 5, 5, 0, 0, 0, 0, 0, 2, 0, 0,
    3, 0, 4, 0, 2, 0, 3, 1, 0, 0, 0, 3, 2, 2, 2, 2,
    3, 2, 4, 5, 1, 1, 2, 1, 3, 2, 3, 3, 0, 0, 0, 2,
    2, 0, 0, 0, 0, 0, 0, 0, 2, 3, 0, 0, 0, 0, 0, 0,
    0, 0, 2, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 1, 2, 1, 1, 0, 0, 0, 0, 0, 0,
    2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 3, 5, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
]


OPCODE_NAMES = {
    0x00: "@NON",
    0x02: "@TEXT",
    0x03: "@NAME",
    0x05: "@CR",
    0x07: "@SELECT",
    0x0A: "@SET",
    0x0B: "@ADD",
    0x0C: "@SUB",
    0x0D: "@MUL",
    0x0E: "@DIV",
    0x0F: "@MOD",
    0x11: "@CALC",
    0x15: "@GOTO",
    0x16: "@GOSUB",
    0x17: "@RETURN",
    0x18: "@SIL_GOTO",
    0x1A: "@IF",
    0x1C: "@ELSEIF",
    0x20: "@LOOP",
    0x22: "@WHILE",
    0x24: "@NEXT",
    0x25: "@BREAK",
    0x26: "@CONTINUE",
    0x2A: "@WAIT",
    0x2B: "@RUN",
    0x2E: "@RANDOM",
    0x30: "@LIMIT",
    0x32: "@NUMBER",
    0x33: "@NUMRAND",
    0x34: "@NUMORDER",
    0x3C: "@POPUP",
    0x41: "@BGMP",
    0x42: "@BGMS",
    0x45: "@SE",
    0x46: "@SES",
    0x49: "@MOVIE",
    0x4C: "@BGSET",
    0x4D: "@BGSETO",
    0x4F: "@BGBACK",
    0x52: "@CHSET",
    0x53: "@CHSETO",
    0x56: "@CHCLS",
    0x57: "@CHCLSO",
    0x5A: "@SPSET",
    0x5B: "@SPSETO",
    0x5E: "@SPCLS",
    0x5F: "@SPCLSO",
    0x62: "@SPPOS",
    0x63: "@SPPOSO",
    0x66: "@SPMOVE",
    0x67: "@SPMOVEO",
    0x6B: "@SCREFF",
    0x6E: "@EVNT",
    0x70: "@EVNTRND",
    0x72: "@EVNTSET",
    0x78: "@EXIT",
    0x7A: "@GOTITLE",
    0x8A: "@DUNNEXTBG",
    0x8B: "@DUNTAKE",
    0x8C: "@DUNBATTLE",
    0x8D: "@DUNRETREAT",
    0x94: "@DUNSORTIEADD",
    0x95: "@DUNUNITAPPEAR",
    0x96: "@DUNSORTIECANCEL",
    0x97: "@DUNFSEVNTSET",
    0x98: "@DUNCELESADD",
    0x99: "@DUNCELESRATE",
    0x9A: "@DUNORNAMENTCANCEL",
    0x9C: "@CELESLIFERATE",
    0x9D: "@CELESMAGICRATE",
    0x9E: "@CELESLIFEMAX",
    0x9F: "@CELESLIFENOW",
    0xA0: "@CELESLIFEADD",
    0xA1: "@CELESMAGICMAX",
    0xA2: "@CELESMAGICNOW",
    0xA3: "@CELESMAGICADD",
    0xA4: "@CELESSTATUS",
    0xA5: "@CELESFETTLE",
    0xA6: "@CELESFETTLEADD",
    0xA8: "@SKILLLEVEL",
    0xA9: "@SKILLPROF",
    0xAA: "@SKILLGET",
    0xB2: "@ITEMGET",
    0xB3: "@ITEMIS",
    0xB4: "@FURNIGET",
    0xB5: "@FURNIIS",
    0xB6: "@ITEMBUY",
    0xB7: "@FURNICOUNT",
    0xB8: "@FURNIPOINT",
    0xB9: "@FURNIARTICLE",
    0xBC: "@DUNPRODUCESET",
    0xBD: "@DUNPRODUCEALL",
    0xBE: "@DUNPRODUCEAUTO",
    0xBF: "@DUNPRODUCETO",
    0xC0: "@DUNPRODUCEFROM",
    0xDA: "@SKILLNEWRESET",
    0xDB: "@SKILLNEW",
    0xDC: "@ITEMNEWRESET",
    0xDD: "@ITEMNEW",
    0xDE: "@FURNINEWRESET",
    0xDF: "@FURNINEW",
    0xE4: "@OFFICEINFOON",
    0xE5: "@OFFICEINFOOFF",
    0xE6: "@TRAININGEFFSET",
    0xE7: "@TRAININGBGSET",
    0xE8: "@SDULECOUNTNAME",
    0xE9: "@SDULECOUNTNUM",
    0xEA: "@CALENDAR",
    0xEB: "@CELESVOICEA",
    0xEC: "@CELESVOICEB",
    0xED: "@CELESVOICEC",
    0xEE: "@MASTERMAGICRATE",
    0xEF: "@DEATHCOUNT",
    0xF0: "@SUCCEEDSAVE",
    0xF1: "@SUCCEEDLOAD",
    0xF2: "@HEROICSAVE",
    0xF3: "@CELESDIRECT",
    0xF4: "@TRAININGEFFCHANGE",
    0xF5: "@TRAININGJUDGE",
    0xF6: "@HEROICTAKERESET",
}


EXPR_OPERATOR_NAMES = {
    0x02: "SET",
    0x04: "ADD",
    0x05: "SUB",
    0x06: "MUL",
    0x07: "DIV",
    0x08: "MOD",
    0x0A: "AND",
    0x0B: "OR",
    0x0C: "XOR",
    0x0E: "EQ",
    0x0F: "NE",
    0x11: "LT",
    0x12: "GT",
    0x14: "LE",
    0x15: "GE",
    0x17: "BOOL_AND",
    0x18: "BOOL_OR",
    0x1A: "ADD_EQ",
    0x1B: "SUB_EQ",
    0x1C: "MUL_EQ",
    0x1D: "DIV_EQ",
    0x1E: "MOD_EQ",
    0x20: "AND_EQ",
    0x21: "OR_EQ",
    0x22: "XOR_EQ",
    0x24: "INDEX",
    0x80: "POS",
    0x81: "NEG",
    0x83: "NOT",
    0x84: "BIT_NOT",
}


EXPR_OPERATOR_CODES = {name.upper(): code for code, name in EXPR_OPERATOR_NAMES.items()}
DISPLAY_NAME_LOOKUP: dict[str, int] = {}
for _opcode, _name in OPCODE_NAMES.items():
    DISPLAY_NAME_LOOKUP[_name.upper()] = _opcode
    DISPLAY_NAME_LOOKUP[_name.lstrip("@").upper()] = _opcode
    DISPLAY_NAME_LOOKUP[f"OP_0X{_opcode:02X}"] = _opcode
    DISPLAY_NAME_LOOKUP[f"OP_{_opcode:02X}"] = _opcode

CONTROL_TARGET_ARG_INDEXES = {
    0x15: [0],  # GOTO
    0x16: [0],  # GOSUB
    0x1A: [1],  # IF
    0x1C: [1],  # ELSEIF
    0x20: [4],  # LOOP
    0x24: [0],  # NEXT
}

EXPR_BINARY_SYMBOLS = {
    0x04: "+",
    0x05: "-",
    0x06: "*",
    0x07: "/",
    0x08: "%",
    0x0A: "&",
    0x0B: "|",
    0x0C: "^",
    0x0E: "==",
    0x0F: "!=",
    0x11: "<",
    0x12: ">",
    0x14: "<=",
    0x15: ">=",
    0x17: "&&",
    0x18: "||",
}

EXPR_BINARY_CODES = {symbol: code for code, symbol in EXPR_BINARY_SYMBOLS.items()}
EXPR_UNARY_SYMBOLS = {
    0x80: "+",
    0x81: "-",
    0x83: "!",
    0x84: "~",
}
EXPR_UNARY_CODES = {symbol: code for code, symbol in EXPR_UNARY_SYMBOLS.items()}
FLAG_SUFFIX_PATTERN = re.compile(r"^(.*)\{(0x[0-9A-Fa-f]+|-?\d+)\}$")


class ParseError(RuntimeError):
    pass


@dataclass
class ParsedArg:
    flags: int
    kind: str
    mode: str
    value: object | None
    extra: dict[str, object]


@dataclass
class ParsedInstruction:
    offset: int
    opcode: int
    name: str
    display_name: str
    arg_count: int
    args: list[ParsedArg]
    size: int


@dataclass(frozen=True)
class LabelRef:
    name: str


@dataclass
class LabelLine:
    name: str


@dataclass
class InstructionLine:
    instruction: ParsedInstruction


def opcode_name(opcode: int) -> str:
    return OPCODE_NAMES.get(opcode, f"OP_0x{opcode:02X}")


def opcode_display_name(opcode: int) -> str:
    return opcode_name(opcode).lstrip("@")


def parse_opcode_name(name: str) -> int:
    normalized = name.strip().upper()
    if normalized in DISPLAY_NAME_LOOKUP:
        return DISPLAY_NAME_LOOKUP[normalized]
    match = re.fullmatch(r"OP_0X([0-9A-F]{2})", normalized)
    if match:
        return int(match.group(1), 16)
    raise ParseError(f"unknown opcode name: {name}")


def read_u16_be(buffer: bytes, pos: int) -> tuple[int, int]:
    if pos + 2 > len(buffer):
        raise ParseError("unexpected EOF while reading u16")
    return (buffer[pos] << 8) | buffer[pos + 1], pos + 2


def read_i32_be(buffer: bytes, pos: int) -> tuple[int, int]:
    if pos + 4 > len(buffer):
        raise ParseError("unexpected EOF while reading i32")
    value = int.from_bytes(buffer[pos : pos + 4], "big", signed=True)
    return value, pos + 4


def script_character_width(script_encoding: str) -> int:
    normalized = script_encoding.lower().replace("_", "-")
    return 2 if normalized in {"utf-16", "utf-16le", "utf-16-le", "utf-16be", "utf-16-be"} else 1


def read_length_prefixed_bytes(buffer: bytes, pos: int, character_width: int = 1) -> tuple[bytes, int]:
    if pos >= len(buffer):
        raise ParseError("unexpected EOF while reading string length")
    length = buffer[pos] * character_width
    pos += 1
    if pos + length > len(buffer):
        raise ParseError("unexpected EOF while reading string bytes")
    return buffer[pos : pos + length], pos + length


def encode_u16_be(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ParseError(f"u16 out of range: {value}")
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


def encode_i32_be(value: int) -> bytes:
    return int(value).to_bytes(4, "big", signed=True)


def encode_length_prefixed_bytes(raw: bytes, character_width: int = 1) -> bytes:
    if len(raw) % character_width != 0:
        raise ParseError(f"string byte length is not aligned to character width {character_width}: {len(raw)}")
    length = len(raw) // character_width
    if length > 0xFF:
        raise ParseError(f"string too long for MYA immediate: {length} characters")
    return bytes([length]) + raw


def decode_script_text_lossless(raw: bytes, script_encoding: str = SCRIPT_ENCODING) -> str | None:
    try:
        text = raw.decode(script_encoding)
    except UnicodeDecodeError:
        return None
    try:
        if text.encode(script_encoding) != raw:
            return None
    except UnicodeEncodeError:
        return None
    return text


def default_flags(kind: str, mode: str, has_chain: bool = False) -> int:
    chain_bit = 0x01 if has_chain else 0x00
    if kind == "number" and mode == "immediate":
        return 0x82 | chain_bit
    if kind == "number" and mode == "reference":
        return 0x02 | chain_bit
    if kind == "bool" and mode == "immediate":
        return 0x84
    if kind == "bool" and mode == "reference":
        return 0x04
    if kind == "string" and mode == "immediate":
        return 0x88
    if kind == "string" and mode == "reference":
        return 0x08
    if kind == "expr":
        return 0x01
    if kind == "null":
        return 0x00
    raise ParseError(f"unsupported arg kind/mode: {kind}/{mode}")


def quote_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def format_flag_kw(flags: int, expected: int) -> str:
    if flags == expected:
        return ""
    return f", flags=0x{flags:02X}"


def format_flag_suffix(flags: int, expected: int) -> str:
    if flags == expected:
        return ""
    return f"{{0x{flags:02X}}}"


def format_chain_items(chain: list[dict[str, int]]) -> str:
    items = [f"chain({item['value']}, flags=0x{item['flags']:02X})" for item in chain]
    return "[" + ", ".join(items) + "]"


def format_expr_token(token: dict[str, object], script_encoding: str = SCRIPT_ENCODING) -> str:
    if token["type"] == "operator":
        op = int(token["op"])
        name = EXPR_OPERATOR_NAMES.get(op)
        if name is None:
            return f"0x{op:02X}"
        return name
    return format_arg(token["arg"], script_encoding=script_encoding)


def label_name_for_offset(offset: int) -> str:
    return f"@L{offset:08X}"


def collect_control_labels(insns: list[ParsedInstruction]) -> dict[int, str]:
    labels: dict[int, str] = {}
    for insn in insns:
        for arg_index in CONTROL_TARGET_ARG_INDEXES.get(insn.opcode, []):
            arg = insn.args[arg_index]
            if arg.kind != "number" or arg.mode != "immediate" or not isinstance(arg.value, int):
                continue
            labels.setdefault(arg.value, label_name_for_offset(arg.value))
    return labels


def format_target_arg(arg: ParsedArg, labels: dict[int, str] | None = None) -> str:
    if arg.kind != "number" or arg.mode != "immediate":
        raise ParseError("target arg must be an immediate numeric arg")
    if isinstance(arg.value, LabelRef):
        text = arg.value.name
    elif isinstance(arg.value, int):
        if labels is not None and arg.value in labels:
            text = labels[arg.value]
        else:
            text = label_name_for_offset(arg.value)
    else:
        raise ParseError("unsupported target value")
    return text + format_flag_suffix(arg.flags, 0x92)


def format_expr(arg: ParsedArg, script_encoding: str = SCRIPT_ENCODING) -> str:
    raw_payload = arg.extra.get("raw_payload")
    if isinstance(raw_payload, bytes):
        text = decode_script_text_lossless(raw_payload, script_encoding=script_encoding)
        if text is not None:
            return f"exprtext({quote_string(text)})"
        return f'exprhex"{raw_payload.hex().upper()}"'
    tokens = arg.extra.get("tokens", [])
    flags_suffix = format_flag_suffix(arg.flags, default_flags("expr", "special"))
    if len(tokens) == 3 and tokens[0]["type"] == "arg" and tokens[1]["type"] == "arg" and tokens[2]["type"] == "operator":
        op = int(tokens[2]["op"])
        symbol = EXPR_BINARY_SYMBOLS.get(op)
        if symbol is not None:
            left = format_arg(tokens[0]["arg"], script_encoding=script_encoding)
            right = format_arg(tokens[1]["arg"], script_encoding=script_encoding)
            return f"{left} {symbol} {right}{flags_suffix}"
    if len(tokens) == 2 and tokens[0]["type"] == "arg" and tokens[1]["type"] == "operator":
        op = int(tokens[1]["op"])
        symbol = EXPR_UNARY_SYMBOLS.get(op)
        if symbol is not None:
            value = format_arg(tokens[0]["arg"], script_encoding=script_encoding)
            return f"{symbol}{value}{flags_suffix}"
    token_text = ", ".join(format_expr_token(token, script_encoding=script_encoding) for token in tokens)
    return f"expr[{token_text}]{flags_suffix}"


def format_arg(arg: ParsedArg, script_encoding: str = SCRIPT_ENCODING) -> str:
    flags = arg.flags
    if arg.kind == "number":
        chain = arg.extra.get("chain", [])
        if chain:
            chain_suffix = f", chain={format_chain_items(chain)}"
            if arg.mode == "immediate":
                return f"num({arg.value}{chain_suffix}{format_flag_kw(flags, default_flags('number', 'immediate', True))})"
            return f"numref({arg.value}{chain_suffix}{format_flag_kw(flags, default_flags('number', 'reference', True))})"
        if arg.mode == "immediate":
            if isinstance(arg.value, LabelRef):
                return format_target_arg(arg)
            return f"{arg.value}{format_flag_suffix(flags, default_flags('number', 'immediate'))}"
        return f"num[{arg.value}]{format_flag_suffix(flags, default_flags('number', 'reference'))}"
    if arg.kind == "bool":
        if arg.mode == "immediate":
            value = int(arg.value)
            if value == 0:
                return f"false{format_flag_suffix(flags, default_flags('bool', 'immediate'))}"
            if value == 1:
                return f"true{format_flag_suffix(flags, default_flags('bool', 'immediate'))}"
            return f"!{value}{format_flag_suffix(flags, default_flags('bool', 'immediate'))}"
        return f"bool[{arg.value}]{format_flag_suffix(flags, default_flags('bool', 'reference'))}"
    if arg.kind == "string":
        if arg.mode == "reference":
            return f"str[{arg.value}]{format_flag_suffix(flags, default_flags('string', 'reference'))}"
        raw = arg.value
        if not isinstance(raw, bytes):
            raise ParseError("string immediate arg missing raw bytes")
        text = decode_script_text_lossless(raw, script_encoding=script_encoding)
        if text is not None:
            return f"{quote_string(text)}{format_flag_suffix(flags, default_flags('string', 'immediate'))}"
        return f'hex"{raw.hex().upper()}"{format_flag_suffix(flags, default_flags("string", "immediate"))}'
    if arg.kind == "expr":
        return format_expr(arg, script_encoding=script_encoding)
    if arg.kind == "null":
        return f"null{format_flag_suffix(flags, default_flags('null', 'special'))}"
    raise ParseError(f"unsupported arg kind: {arg.kind}")


def format_instruction(
    insn: ParsedInstruction,
    labels: dict[int, str] | None = None,
    script_encoding: str = SCRIPT_ENCODING,
) -> str:
    if insn.opcode == 0x15:
        return f"GOTO {format_target_arg(insn.args[0], labels)}"
    if insn.opcode == 0x16:
        return f"GOSUB {format_target_arg(insn.args[0], labels)}"
    if insn.opcode in {0x1A, 0x1C}:
        return f"{insn.display_name} ({format_expr(insn.args[0], script_encoding=script_encoding)}) {format_target_arg(insn.args[1], labels)}"
    if insn.opcode == 0x20:
        args = ", ".join(format_arg(arg, script_encoding=script_encoding) for arg in insn.args[:-1])
        return f"LOOP ({args}) {format_target_arg(insn.args[-1], labels)}"
    if insn.opcode == 0x24:
        return f"NEXT {format_target_arg(insn.args[0], labels)}"
    if not insn.args:
        return insn.display_name
    args = ", ".join(format_arg(arg, script_encoding=script_encoding) for arg in insn.args)
    return f"{insn.display_name} ({args})"


def disassemble_to_text(
    buffer: bytes,
    script_encoding: str = SCRIPT_ENCODING,
    opcode_counts: list[int] = OPCODE_ARG_COUNTS,
) -> str:
    insns = disassemble_buffer(buffer, script_encoding=script_encoding, opcode_counts=opcode_counts)
    labels = collect_control_labels(insns)
    is_myu = script_character_width(script_encoding) == 2
    script_format = "MYU" if is_myu else "MYA"
    format_version = MYU_ASM_FORMAT_VERSION if is_myu else MYA_ASM_FORMAT_VERSION
    lines = [
        f"# AOI {script_format} ASM",
        f"# format: {format_version}",
        f"# encoding: utf-8",
        f"# script-encoding: {script_encoding}",
        "",
    ]
    for insn in insns:
        label = labels.get(insn.offset)
        if label is not None:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(label)
        lines.append(f"    {format_instruction(insn, labels, script_encoding=script_encoding)}")
    return "\n".join(lines) + "\n"


def parse_numeric_chain(buffer: bytes, pos: int) -> tuple[list[dict[str, int]], int]:
    chain: list[dict[str, int]] = []
    while True:
        if pos >= len(buffer):
            raise ParseError("unexpected EOF while reading numeric chain flag")
        chain_flags = buffer[pos]
        pos += 1
        chain_value, pos = read_u16_be(buffer, pos)
        chain.append({"flags": chain_flags, "value": chain_value})
        if (chain_flags & 1) == 0:
            return chain, pos


def encoded_arg_length(arg: ParsedArg, character_width: int = 1) -> int:
    if arg.kind == "number":
        base = 5 if arg.mode == "immediate" else 3
        return base + 3 * len(arg.extra.get("chain", []))
    if arg.kind == "bool":
        return 2 if arg.mode == "immediate" else 3
    if arg.kind == "string":
        if arg.mode == "reference":
            return 3
        raw = arg.value
        if not isinstance(raw, bytes) or len(raw) % character_width != 0:
            raise ParseError("string immediate arg has invalid byte length")
        return 2 + len(raw) // character_width
    if arg.kind == "expr":
        raw_payload = arg.extra.get("raw_payload")
        if isinstance(raw_payload, bytes):
            if len(raw_payload) % character_width != 0:
                raise ParseError("expression payload has invalid byte length")
            return 2 + len(raw_payload) // character_width
        return 2 + sum(
            2 if token["type"] == "operator" else encoded_arg_length(token["arg"], character_width)
            for token in arg.extra.get("tokens", [])
        )
    if arg.kind == "null":
        return 1
    raise ParseError(f"unsupported arg kind: {arg.kind}")


def parse_expression_payload(buffer: bytes, pos: int, character_width: int = 1) -> tuple[dict[str, object], int]:
    if pos >= len(buffer):
        raise ParseError("unexpected EOF while reading expression payload length")
    remaining = buffer[pos]
    pos += 1
    if character_width > 1:
        byte_length = remaining * character_width
        if pos + byte_length > len(buffer):
            raise ParseError("unexpected EOF in expression payload")
        return {
            "raw_payload": buffer[pos : pos + byte_length],
            "character_width": character_width,
        }, pos + byte_length
    tokens: list[dict[str, object]] = []
    while remaining > 0:
        if pos >= len(buffer):
            raise ParseError("unexpected EOF in expression payload")
        token_flag = buffer[pos]
        pos += 1
        remaining -= 1
        if token_flag == 0x01:
            if remaining <= 0 or pos >= len(buffer):
                raise ParseError("unexpected EOF while reading expression operator")
            operator = buffer[pos]
            pos += 1
            remaining -= 1
            tokens.append({"type": "operator", "op": operator})
            continue
        arg, pos = parse_arg(buffer, pos, token_flag, character_width)
        remaining -= encoded_arg_length(arg, character_width) - 1
        if remaining < 0:
            raise ParseError("expression payload length underflow")
        tokens.append({"type": "arg", "arg": arg})
    return {"tokens": tokens}, pos


def parse_arg(
    buffer: bytes,
    pos: int,
    flags: int | None = None,
    character_width: int = 1,
) -> tuple[ParsedArg, int]:
    if flags is None:
        if pos >= len(buffer):
            raise ParseError("unexpected EOF while reading arg flags")
        flags = buffer[pos]
        pos += 1
    signed_flags = flags if flags < 0x80 else flags - 0x100
    mode = "immediate" if signed_flags < 0 else "reference"

    if flags & 0x02:
        if signed_flags < 0:
            value, pos = read_i32_be(buffer, pos)
        else:
            value, pos = read_u16_be(buffer, pos)
        extra: dict[str, object] = {}
        if flags & 0x01:
            chain, pos = parse_numeric_chain(buffer, pos)
            extra["chain"] = chain
        return ParsedArg(flags=flags, kind="number", mode=mode, value=value, extra=extra), pos

    if flags & 0x04:
        if signed_flags < 0:
            if pos >= len(buffer):
                raise ParseError("unexpected EOF while reading bool immediate")
            value = buffer[pos]
            pos += 1
        else:
            value, pos = read_u16_be(buffer, pos)
        return ParsedArg(flags=flags, kind="bool", mode=mode, value=value, extra={}), pos

    if flags & 0x08:
        if signed_flags < 0:
            raw_bytes, pos = read_length_prefixed_bytes(buffer, pos, character_width)
            return ParsedArg(
                flags=flags,
                kind="string",
                mode=mode,
                value=raw_bytes,
                extra={"character_width": character_width},
            ), pos
        value, pos = read_u16_be(buffer, pos)
        return ParsedArg(flags=flags, kind="string", mode=mode, value=value, extra={}), pos

    if flags & 0x01:
        payload, pos = parse_expression_payload(buffer, pos, character_width)
        return ParsedArg(flags=flags, kind="expr", mode="special", value=None, extra=payload), pos

    return ParsedArg(flags=flags, kind="null", mode="special", value=None, extra={}), pos


def parse_instruction(
    buffer: bytes,
    offset: int,
    character_width: int = 1,
    opcode_counts: list[int] = OPCODE_ARG_COUNTS,
) -> tuple[ParsedInstruction, int]:
    if offset >= len(buffer):
        raise ParseError("offset out of range")
    opcode = buffer[offset]
    pos = offset + 1
    arg_count = opcode_counts[opcode]
    args: list[ParsedArg] = []
    for _ in range(arg_count):
        arg, pos = parse_arg(buffer, pos, character_width=character_width)
        args.append(arg)
    return ParsedInstruction(
        offset=offset,
        opcode=opcode,
        name=opcode_name(opcode),
        display_name=opcode_display_name(opcode),
        arg_count=arg_count,
        args=args,
        size=pos - offset,
    ), pos


def disassemble_buffer(
    buffer: bytes,
    script_encoding: str = SCRIPT_ENCODING,
    opcode_counts: list[int] = OPCODE_ARG_COUNTS,
) -> list[ParsedInstruction]:
    insns: list[ParsedInstruction] = []
    pos = 0
    character_width = script_character_width(script_encoding)
    while pos < len(buffer):
        insn, pos = parse_instruction(buffer, pos, character_width, opcode_counts)
        insns.append(insn)
    return insns


def encode_chain_items(chain: list[dict[str, int]]) -> bytes:
    out = bytearray()
    for item in chain:
        flags = int(item["flags"])
        value = int(item["value"])
        out.append(flags)
        out.extend(encode_u16_be(value))
    return bytes(out)


def encode_expr_tokens(tokens: list[dict[str, object]]) -> bytes:
    payload = bytearray()
    for token in tokens:
        if token["type"] == "operator":
            payload.append(0x01)
            payload.append(int(token["op"]))
            continue
        payload.extend(encode_arg(token["arg"]))
    if len(payload) > 0xFF:
        raise ParseError(f"expression payload too large: {len(payload)} bytes")
    return bytes([len(payload)]) + payload


def resolve_numeric_value(value: object, labels: dict[str, int] | None) -> int:
    if isinstance(value, LabelRef):
        if labels is None:
            return 0
        if value.name not in labels:
            raise ParseError(f"undefined label: {value.name}")
        return labels[value.name]
    if isinstance(value, int):
        return value
    raise ParseError(f"unsupported numeric value: {value!r}")


def encode_arg(arg: ParsedArg, labels: dict[str, int] | None = None) -> bytes:
    out = bytearray([arg.flags])

    if arg.kind == "number":
        if arg.mode == "immediate":
            out.extend(encode_i32_be(resolve_numeric_value(arg.value, labels)))
        else:
            out.extend(encode_u16_be(resolve_numeric_value(arg.value, labels)))
        chain = arg.extra.get("chain")
        if chain:
            out.extend(encode_chain_items(chain))
        return bytes(out)

    if arg.kind == "bool":
        if arg.mode == "immediate":
            value = int(arg.value)
            if not 0 <= value <= 0xFF:
                raise ParseError(f"bool immediate out of range: {value}")
            out.append(value)
        else:
            out.extend(encode_u16_be(int(arg.value)))
        return bytes(out)

    if arg.kind == "string":
        if arg.mode == "immediate":
            raw = arg.value
            if not isinstance(raw, bytes):
                raise ParseError("string immediate arg must contain bytes")
            out.extend(encode_length_prefixed_bytes(raw, int(arg.extra.get("character_width", 1))))
        else:
            out.extend(encode_u16_be(int(arg.value)))
        return bytes(out)

    if arg.kind == "expr":
        raw_payload = arg.extra.get("raw_payload")
        if isinstance(raw_payload, bytes):
            character_width = int(arg.extra.get("character_width", 1))
            out.extend(encode_length_prefixed_bytes(raw_payload, character_width))
            return bytes(out)
        payload = bytearray()
        payload_length = 0
        character_width = 1
        for token in arg.extra.get("tokens", []):
            if token["type"] == "operator":
                payload.append(0x01)
                payload.append(int(token["op"]))
                payload_length += 2
            else:
                token_arg = token["arg"]
                character_width = max(character_width, int(token_arg.extra.get("character_width", 1)))
                payload.extend(encode_arg(token_arg, labels))
                payload_length += encoded_arg_length(token_arg, character_width)
        if payload_length > 0xFF:
            raise ParseError(f"expression payload too large: {payload_length} units")
        out.extend(bytes([payload_length]))
        out.extend(payload)
        return bytes(out)

    if arg.kind == "null":
        return bytes(out)

    raise ParseError(f"unsupported arg kind for encoding: {arg.kind}")


def assemble_instruction(insn: ParsedInstruction, labels: dict[str, int] | None = None) -> bytes:
    out = bytearray([insn.opcode])
    for arg in insn.args:
        out.extend(encode_arg(arg, labels))
    return bytes(out)


def _parse_int_node(node: ast.AST) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return int(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_parse_int_node(node.operand)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _parse_int_node(node.operand)
    raise ParseError("expected integer literal")


def _parse_str_node(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return str(node.value)
    raise ParseError("expected string literal")


def _parse_call(node: ast.AST, expected_names: set[str]) -> ast.Call:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        raise ParseError("expected function-style argument expression")
    if node.func.id not in expected_names:
        raise ParseError(f"unexpected expression form: {node.func.id}")
    return node


def _parse_chain_item_node(node: ast.AST) -> dict[str, int]:
    call = _parse_call(node, {"chain"})
    if len(call.args) != 1:
        raise ParseError("chain() requires exactly one positional argument")
    value = _parse_int_node(call.args[0])
    flags = 0x02
    for keyword in call.keywords:
        if keyword.arg != "flags":
            raise ParseError(f"unsupported chain() keyword: {keyword.arg}")
        flags = _parse_int_node(keyword.value)
    return {"flags": flags, "value": value}


def _parse_expr_operator_node(node: ast.AST) -> dict[str, object]:
    call = _parse_call(node, {"op"})
    if len(call.args) != 1:
        raise ParseError("op() requires exactly one positional argument")
    operand = call.args[0]
    if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
        op_name = str(operand.value).upper()
        if op_name not in EXPR_OPERATOR_CODES:
            raise ParseError(f"unknown expression operator name: {operand.value}")
        return {"type": "operator", "op": EXPR_OPERATOR_CODES[op_name]}
    return {"type": "operator", "op": _parse_int_node(operand)}


def _parse_int_text(text: str) -> int:
    try:
        return int(text, 0)
    except ValueError as exc:
        raise ParseError(f"invalid integer literal: {text}") from exc


def split_flag_suffix(text: str) -> tuple[str, int | None]:
    match = FLAG_SUFFIX_PATTERN.match(text.strip())
    if not match:
        return text.strip(), None
    return match.group(1).strip(), _parse_int_text(match.group(2))


def strip_outer_parens(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == "(" and text[-1] == ")" and _paren_balance_delta(text) == 0:
        return text[1:-1].strip()
    return text


def parse_string_literal_text(text: str) -> str:
    try:
        value = ast.literal_eval(text)
    except (SyntaxError, ValueError) as exc:
        raise ParseError(f"invalid string literal: {text}") from exc
    if not isinstance(value, str):
        raise ParseError(f"invalid string literal: {text}")
    return value


def find_top_level_binary_operator(text: str) -> tuple[str, int] | None:
    candidates = ["&&", "||", "==", "!=", "<=", ">=", "<", ">", "&", "|", "^", "+", "-", "*", "/", "%"]
    in_string = False
    escape = False
    quote_char = ""
    depth = 0
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote_char:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote_char = char
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}":
            depth -= 1
            continue
        if depth != 0:
            continue
        for candidate in candidates:
            if text.startswith(candidate, index):
                if candidate in {"+", "-"}:
                    if index == 0:
                        continue
                    prev = text[index - 1]
                    if prev in "([{:,+-*/%&|^!<>= ":
                        continue
                return candidate, index
    return None


def parse_expr_text(text: str, script_encoding: str = SCRIPT_ENCODING) -> ParsedArg:
    core = strip_outer_parens(text.strip())
    explicit_flags = None
    match = re.fullmatch(r"exprtext\((.*)\)", core, re.IGNORECASE)
    if match:
        value = parse_string_literal_text(match.group(1).strip())
        try:
            raw_payload = value.encode(script_encoding)
        except UnicodeEncodeError as exc:
            raise ParseError(f"expression is not encodable as {script_encoding}: {value!r}") from exc
        return ParsedArg(
            flags=default_flags("expr", "special"),
            kind="expr",
            mode="special",
            value=None,
            extra={
                "raw_payload": raw_payload,
                "character_width": script_character_width(script_encoding),
            },
        )
    match = re.fullmatch(r'exprhex"([0-9A-Fa-f]*)"', core, re.IGNORECASE)
    if match:
        return ParsedArg(
            flags=default_flags("expr", "special"),
            kind="expr",
            mode="special",
            value=None,
            extra={
                "raw_payload": bytes.fromhex(match.group(1)),
                "character_width": script_character_width(script_encoding),
            },
        )
    binary = find_top_level_binary_operator(core)
    if binary is None:
        if core.startswith("expr[") and core.endswith("]"):
            inner = core[5:-1].strip()
            items = split_top_level_commas(inner) if inner else []
            tokens: list[dict[str, object]] = []
            for item in items:
                upper_item = item.upper()
                if upper_item in EXPR_OPERATOR_CODES:
                    tokens.append({"type": "operator", "op": EXPR_OPERATOR_CODES[upper_item]})
                elif re.fullmatch(r"0x[0-9A-Fa-f]+|-?\d+", item):
                    tokens.append({"type": "operator", "op": _parse_int_text(item)})
                else:
                    tokens.append({"type": "arg", "arg": parse_arg_text(item, script_encoding=script_encoding)})
            return ParsedArg(
                flags=explicit_flags if explicit_flags is not None else default_flags("expr", "special"),
                kind="expr",
                mode="special",
                value=None,
                extra={"tokens": tokens},
            )
        return parse_arg_text(text, script_encoding=script_encoding)
    symbol, index = binary
    left_text = core[:index].strip()
    right_text = core[index + len(symbol) :].strip()
    if not left_text or not right_text:
        raise ParseError(f"invalid expression: {text}")
    tokens = [
        {"type": "arg", "arg": parse_arg_text(left_text, script_encoding=script_encoding)},
        {"type": "arg", "arg": parse_arg_text(right_text, script_encoding=script_encoding)},
        {"type": "operator", "op": EXPR_BINARY_CODES[symbol]},
    ]
    return ParsedArg(
        flags=explicit_flags if explicit_flags is not None else default_flags("expr", "special"),
        kind="expr",
        mode="special",
        value=None,
        extra={"tokens": tokens},
    )


def parse_compact_arg_text(text: str, script_encoding: str = SCRIPT_ENCODING) -> ParsedArg:
    stripped = text.strip()
    if (
        stripped.startswith("expr[")
        or stripped.lower().startswith("exprtext(")
        or stripped.lower().startswith('exprhex"')
        or find_top_level_binary_operator(strip_outer_parens(stripped)) is not None
    ):
        return parse_expr_text(stripped, script_encoding=script_encoding)

    core, explicit_flags = split_flag_suffix(stripped)
    lower = core.lower()
    if core.startswith("@"):
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else 0x92,
            kind="number",
            mode="immediate",
            value=LabelRef(core),
            extra={},
        )
    if lower == "null":
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("null", "special"),
            kind="null",
            mode="special",
            value=None,
            extra={},
        )
    if lower == "false":
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("bool", "immediate"),
            kind="bool",
            mode="immediate",
            value=0,
            extra={},
        )
    if lower == "true":
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("bool", "immediate"),
            kind="bool",
            mode="immediate",
            value=1,
            extra={},
        )
    if core.startswith("!"):
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("bool", "immediate"),
            kind="bool",
            mode="immediate",
            value=_parse_int_text(core[1:]),
            extra={},
        )
    if core.startswith("$"):
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("number", "reference"),
            kind="number",
            mode="reference",
            value=_parse_int_text(core[1:]),
            extra={},
        )
    match = re.fullmatch(r"num\[(.+)\]", core, re.IGNORECASE)
    if match:
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("number", "reference"),
            kind="number",
            mode="reference",
            value=_parse_int_text(match.group(1).strip()),
            extra={},
        )
    if core.startswith("?"):
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("bool", "reference"),
            kind="bool",
            mode="reference",
            value=_parse_int_text(core[1:]),
            extra={},
        )
    match = re.fullmatch(r"bool\[(.+)\]", core, re.IGNORECASE)
    if match:
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("bool", "reference"),
            kind="bool",
            mode="reference",
            value=_parse_int_text(match.group(1).strip()),
            extra={},
        )
    if core.startswith("%"):
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("string", "reference"),
            kind="string",
            mode="reference",
            value=_parse_int_text(core[1:]),
            extra={},
        )
    match = re.fullmatch(r"str\[(.+)\]", core, re.IGNORECASE)
    if match:
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("string", "reference"),
            kind="string",
            mode="reference",
            value=_parse_int_text(match.group(1).strip()),
            extra={},
        )
    if core.startswith('hex"') and core.endswith('"'):
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("string", "immediate"),
            kind="string",
            mode="immediate",
            value=bytes.fromhex(core[4:-1]),
            extra={"character_width": script_character_width(script_encoding)},
        )
    if core[:1] in {'"', "'"}:
        value = parse_string_literal_text(core)
        try:
            raw = value.encode(script_encoding)
        except UnicodeEncodeError as exc:
            raise ParseError(f"string is not encodable as {script_encoding}: {value!r}") from exc
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("string", "immediate"),
            kind="string",
            mode="immediate",
            value=raw,
            extra={"character_width": script_character_width(script_encoding)},
        )
    if re.fullmatch(r"-?\d+|0x[0-9A-Fa-f]+|-0x[0-9A-Fa-f]+", core):
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else default_flags("number", "immediate"),
            kind="number",
            mode="immediate",
            value=_parse_int_text(core),
            extra={},
        )
    raise ParseError(f"unsupported compact argument syntax: {text}")


def parse_arg_text(text: str, script_encoding: str = SCRIPT_ENCODING) -> ParsedArg:
    stripped = text.strip()
    try:
        return parse_compact_arg_text(stripped, script_encoding=script_encoding)
    except ParseError:
        pass
    try:
        node = ast.parse(stripped, mode="eval").body
    except SyntaxError as exc:
        raise ParseError(f"invalid argument syntax: {text}") from exc
    return _parse_arg_node(node, script_encoding=script_encoding)


def _parse_arg_node(node: ast.AST, script_encoding: str = SCRIPT_ENCODING) -> ParsedArg:
    call = _parse_call(
        node,
        {"num", "numref", "bool", "boolref", "str", "strref", "expr", "null"},
    )
    name = call.func.id

    if name in {"num", "numref"}:
        if len(call.args) != 1:
            raise ParseError(f"{name}() requires exactly one positional argument")
        value = _parse_int_node(call.args[0])
        mode = "immediate" if name == "num" else "reference"
        chain: list[dict[str, int]] = []
        flags = default_flags("number", mode, False)
        for keyword in call.keywords:
            if keyword.arg == "flags":
                flags = _parse_int_node(keyword.value)
            elif keyword.arg == "chain":
                if not isinstance(keyword.value, ast.List):
                    raise ParseError("chain= must be a list")
                chain = [_parse_chain_item_node(item) for item in keyword.value.elts]
            else:
                raise ParseError(f"unsupported {name}() keyword: {keyword.arg}")
        if flags == default_flags("number", mode, False) and chain:
            flags = default_flags("number", mode, True)
        extra = {"chain": chain} if chain else {}
        return ParsedArg(flags=flags, kind="number", mode=mode, value=value, extra=extra)

    if name in {"bool", "boolref"}:
        if len(call.args) != 1:
            raise ParseError(f"{name}() requires exactly one positional argument")
        value = _parse_int_node(call.args[0])
        mode = "immediate" if name == "bool" else "reference"
        flags = default_flags("bool", mode)
        for keyword in call.keywords:
            if keyword.arg != "flags":
                raise ParseError(f"unsupported {name}() keyword: {keyword.arg}")
            flags = _parse_int_node(keyword.value)
        return ParsedArg(flags=flags, kind="bool", mode=mode, value=value, extra={})

    if name == "str":
        raw_bytes: bytes | None = None
        flags = default_flags("string", "immediate")
        if len(call.args) > 1:
            raise ParseError("str() accepts at most one positional argument")
        if call.args:
            text = _parse_str_node(call.args[0])
            try:
                raw_bytes = text.encode(script_encoding)
            except UnicodeEncodeError as exc:
                raise ParseError(f"string is not encodable as {script_encoding}: {text!r}") from exc
        for keyword in call.keywords:
            if keyword.arg == "flags":
                flags = _parse_int_node(keyword.value)
            elif keyword.arg == "hex":
                raw_bytes = bytes.fromhex(_parse_str_node(keyword.value))
            else:
                raise ParseError(f"unsupported str() keyword: {keyword.arg}")
        if raw_bytes is None:
            raise ParseError("str() requires a text literal or hex=...")
        return ParsedArg(
            flags=flags,
            kind="string",
            mode="immediate",
            value=raw_bytes,
            extra={"character_width": script_character_width(script_encoding)},
        )

    if name == "strref":
        if len(call.args) != 1:
            raise ParseError("strref() requires exactly one positional argument")
        value = _parse_int_node(call.args[0])
        flags = default_flags("string", "reference")
        for keyword in call.keywords:
            if keyword.arg != "flags":
                raise ParseError(f"unsupported strref() keyword: {keyword.arg}")
            flags = _parse_int_node(keyword.value)
        return ParsedArg(flags=flags, kind="string", mode="reference", value=value, extra={})

    if name == "expr":
        flags = default_flags("expr", "special")
        tokens: list[dict[str, object]] = []
        for arg_node in call.args:
            if isinstance(arg_node, ast.Call) and isinstance(arg_node.func, ast.Name) and arg_node.func.id == "op":
                tokens.append(_parse_expr_operator_node(arg_node))
            else:
                tokens.append({"type": "arg", "arg": _parse_arg_node(arg_node, script_encoding=script_encoding)})
        for keyword in call.keywords:
            if keyword.arg != "flags":
                raise ParseError(f"unsupported expr() keyword: {keyword.arg}")
            flags = _parse_int_node(keyword.value)
        return ParsedArg(flags=flags, kind="expr", mode="special", value=None, extra={"tokens": tokens})

    if name == "null":
        if call.args:
            raise ParseError("null() does not accept positional arguments")
        flags = default_flags("null", "special")
        for keyword in call.keywords:
            if keyword.arg != "flags":
                raise ParseError(f"unsupported null() keyword: {keyword.arg}")
            flags = _parse_int_node(keyword.value)
        return ParsedArg(flags=flags, kind="null", mode="special", value=None, extra={})

    raise ParseError(f"unsupported argument form: {name}")


def split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_string = False
    escape = False
    quote_char = ""
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote_char:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote_char = char
            continue
        if char in "([{" :
            depth += 1
            continue
        if char in ")]}":
            depth -= 1
            continue
        if char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _paren_balance_delta(text: str) -> int:
    depth = 0
    in_string = False
    escape = False
    quote_char = ""
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote_char:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote_char = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
    return depth


def iter_logical_asm_lines(text: str) -> list[str]:
    logical_lines: list[str] = []
    current: list[str] = []
    balance = 0
    in_block_comment = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if in_block_comment:
            if line == "*/":
                in_block_comment = False
            continue
        if not line:
            continue
        if line == "/*":
            in_block_comment = True
            continue
        if line.startswith("#"):
            continue
        if line.startswith("@") and "(" not in line and balance == 0 and not current:
            logical_lines.append(line.rstrip(";"))
            continue
        current.append(line.rstrip(";"))
        balance += _paren_balance_delta(line)
        if balance == 0:
            logical_lines.append(" ".join(current))
            current = []
    if current:
        raise ParseError("unterminated command in asm text")
    return logical_lines


def parse_target_text(text: str) -> ParsedArg:
    core, explicit_flags = split_flag_suffix(text)
    if core.startswith("@"):
        return ParsedArg(
            flags=explicit_flags if explicit_flags is not None else 0x92,
            kind="number",
            mode="immediate",
            value=LabelRef(core),
            extra={},
        )
    return ParsedArg(
        flags=explicit_flags if explicit_flags is not None else 0x92,
        kind="number",
        mode="immediate",
        value=_parse_int_text(core),
        extra={},
    )


def build_instruction(
    opcode: int,
    args: list[ParsedArg],
    opcode_counts: list[int] = OPCODE_ARG_COUNTS,
) -> ParsedInstruction:
    expected = opcode_counts[opcode]
    if len(args) != expected:
        raise ParseError(f"opcode {opcode_display_name(opcode)} expects {expected} args, got {len(args)}")
    return ParsedInstruction(
        offset=0,
        opcode=opcode,
        name=opcode_name(opcode),
        display_name=opcode_display_name(opcode),
        arg_count=expected,
        args=args,
        size=0,
    )


def parse_instruction_text(
    line: str,
    script_encoding: str = SCRIPT_ENCODING,
    opcode_counts: list[int] = OPCODE_ARG_COUNTS,
) -> ParsedInstruction:
    match = re.match(r"^(GOTO|GOSUB|NEXT)\s+(.+)$", line)
    if match:
        opcode = parse_opcode_name(match.group(1))
        return build_instruction(opcode, [parse_target_text(match.group(2).strip())], opcode_counts)

    match = re.match(r"^(IF|ELSEIF)\s*\((.*)\)\s+(.+)$", line)
    if match:
        opcode = parse_opcode_name(match.group(1))
        return build_instruction(
            opcode,
            [
                parse_expr_text(match.group(2).strip(), script_encoding=script_encoding),
                parse_target_text(match.group(3).strip()),
            ],
            opcode_counts,
        )

    match = re.match(r"^(LOOP)\s*\((.*)\)\s+(.+)$", line)
    if match:
        opcode = parse_opcode_name(match.group(1))
        arg_text = match.group(2).strip()
        args = [parse_arg_text(part, script_encoding=script_encoding) for part in split_top_level_commas(arg_text)] if arg_text else []
        args.append(parse_target_text(match.group(3).strip()))
        return build_instruction(opcode, args, opcode_counts)

    if "(" not in line and ")" not in line and not line.startswith("@"):
        opcode = parse_opcode_name(line.strip())
        return build_instruction(opcode, [], opcode_counts)

    open_paren = line.find("(")
    close_paren = line.rfind(")")
    if open_paren <= 0 or close_paren < open_paren:
        raise ParseError(f"invalid instruction line: {line}")
    name = line[:open_paren].strip()
    opcode = parse_opcode_name(name)
    arg_text = line[open_paren + 1 : close_paren].strip()
    args = [parse_arg_text(part, script_encoding=script_encoding) for part in split_top_level_commas(arg_text)] if arg_text else []
    return build_instruction(opcode, args, opcode_counts)


def parse_asm_line(
    line: str,
    script_encoding: str = SCRIPT_ENCODING,
    opcode_counts: list[int] = OPCODE_ARG_COUNTS,
) -> LabelLine | InstructionLine:
    if line.startswith("@") and "(" not in line and " " not in line and "\t" not in line:
        return LabelLine(line)
    return InstructionLine(
        parse_instruction_text(line, script_encoding=script_encoding, opcode_counts=opcode_counts)
    )


def assemble_text(
    script_text: str,
    script_encoding: str = SCRIPT_ENCODING,
    opcode_counts: list[int] = OPCODE_ARG_COUNTS,
) -> bytes:
    entries = [
        parse_asm_line(line, script_encoding=script_encoding, opcode_counts=opcode_counts)
        for line in iter_logical_asm_lines(script_text)
    ]
    labels: dict[str, int] = {}
    offset = 0
    for entry in entries:
        if isinstance(entry, LabelLine):
            labels[entry.name] = offset
            continue
        offset += len(assemble_instruction(entry.instruction, None))

    out = bytearray()
    for entry in entries:
        if isinstance(entry, LabelLine):
            continue
        out.extend(assemble_instruction(entry.instruction, labels))
    return bytes(out)


def iter_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    files = [path for path in root.rglob("*") if path.is_file()]
    files.sort(key=lambda item: str(item.relative_to(root)).lower())
    return files


def is_script_binary_path(path: Path) -> bool:
    return path.suffix.lower() == ".txt"


def is_disasm_path(path: Path) -> bool:
    return path.name.lower().endswith(".asm.txt")


def disasm_output_path(input_path: Path, input_root: Path, output_root: Path) -> Path:
    relative = input_path.relative_to(input_root) if input_root.is_dir() else Path(input_path.name)
    return output_root / relative.with_name(relative.name + ".asm.txt")


def asm_input_bin_path(input_path: Path, input_root: Path, output_root: Path) -> Path:
    relative = input_path.relative_to(input_root) if input_root.is_dir() else Path(input_path.name)
    name = relative.name
    if name.lower().endswith(".asm.txt"):
        name = name[:-8]
    return output_root / relative.with_name(name)


def _load_counts_from_memory_dump() -> list[int] | None:
    root = Path(__file__).resolve().parent
    dump_path = root / "export-for-ai" / "memory" / "00451514--0045D000.txt"
    if not dump_path.is_file():
        return None
    memory: dict[int, int] = {}
    for line in dump_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([0-9A-F]{16}) \| ([0-9A-F ]+)\|", line)
        if not match:
            continue
        address = int(match.group(1), 16)
        byte_values = [int(item, 16) for item in match.group(2).split()]
        for index, value in enumerate(byte_values):
            memory[address + index] = value
    start = 0x454AA0
    counts = [memory.get(start + index, 0) for index in range(256)]
    if any(counts):
        return counts
    return None


_LOADED_COUNTS = _load_counts_from_memory_dump()
if _LOADED_COUNTS is not None:
    OPCODE_ARG_COUNTS[:] = _LOADED_COUNTS
