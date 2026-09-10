from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Dict, Iterable, List, Tuple

DEFAULT_ENCODING = "utf-8"
HXB_MAGIC_SUFFIX = b"HXB\x00"


class HxbFormatError(Exception):
    pass


@dataclass(frozen=True)
class OpcodeSpec:
    opcode: int
    mnemonic: str
    operands: Tuple[str, ...]


OPCODE_SPECS: Dict[int, OpcodeSpec] = {
    0x00: OpcodeSpec(0x00, "EVAL", ("expr",)),
    0x01: OpcodeSpec(0x01, "SETPARAM", ("expr", "expr")),
    0x02: OpcodeSpec(0x02, "CALL", ("paramblock", "target")),
    0x03: OpcodeSpec(0x03, "ENTER", ("expr", "paramblock")),
    0x04: OpcodeSpec(0x04, "BINDIMG", ("expr", "expr")),
    0x05: OpcodeSpec(0x05, "SETMODE", ("expr",)),
    0x06: OpcodeSpec(0x06, "LOADHXB", ("expr", "expr", "expr", "paramblock")),
    0x07: OpcodeSpec(0x07, "RESINFO", ("expr",)),
    0x08: OpcodeSpec(0x08, "CLIPSET", ("expr",)),
    0x09: OpcodeSpec(0x09, "MEDSTAT", ("expr",)),
    0x0A: OpcodeSpec(0x0A, "MEDPLAY", ()),
    0x0B: OpcodeSpec(0x0B, "IMEOPEN", ("expr",)),
    0x0C: OpcodeSpec(0x0C, "TEXTINDEX", ("expr", "expr")),
    0x0D: OpcodeSpec(0x0D, "BINCOPY", ("expr", "expr", "expr")),
    0x0E: OpcodeSpec(0x0E, "SETSLOT", ("expr",) * 11),
    0x0F: OpcodeSpec(0x0F, "COLOR", ("expr",) * 4),
    0x10: OpcodeSpec(0x10, "FONT", ("expr",) * 7),
    0x11: OpcodeSpec(0x11, "ICON", ("expr",) * 5),
    0x12: OpcodeSpec(0x12, "SURFACE", ("expr",) * 5),
    0x13: OpcodeSpec(0x13, "DIALOG", ("expr", "expr")),
    0x14: OpcodeSpec(0x14, "COLORKEY", ("expr", "expr", "expr")),
    0x15: OpcodeSpec(0x15, "DRAWIMG", ("expr",) * 8),
    0x16: OpcodeSpec(0x16, "BLIT", ("expr",) * 12),
    0x17: OpcodeSpec(0x17, "DRAWTEXT", ("expr",) * 8),
    0x18: OpcodeSpec(0x18, "SLOTENABLE", ("expr", "expr", "expr")),
    0x19: OpcodeSpec(0x19, "EXIT", ("expr",)),
    0x1A: OpcodeSpec(0x1A, "ENUMFONT", ("expr",)),
    0x1B: OpcodeSpec(0x1B, "FREERES", ("expr",)),
    0x1C: OpcodeSpec(0x1C, "READBYTE", ("expr", "expr")),
    0x1D: OpcodeSpec(0x1D, "IMPORT", ("expr",)),
    0x1E: OpcodeSpec(0x1E, "READSTR", ()),
    0x1F: OpcodeSpec(0x1F, "READINTS", ("expr", "expr")),
    0x20: OpcodeSpec(0x20, "GETKEY", ()),
    0x21: OpcodeSpec(0x21, "INPUT", ()),
    0x22: OpcodeSpec(0x22, "TIME", ()),
    0x23: OpcodeSpec(0x23, "SYSINFO", ("expr",)),
    0x24: OpcodeSpec(0x24, "SYSPATH", ("expr",)),
    0x25: OpcodeSpec(0x25, "TIMER", ("expr",)),
    0x26: OpcodeSpec(0x26, "JZTRUE", ("expr", "target")),
    0x27: OpcodeSpec(0x27, "JZFALSE", ("expr", "target")),
    0x28: OpcodeSpec(0x28, "BRANCH", ("expr", "target", "target")),
    0x29: OpcodeSpec(0x29, "JUMP", ("target",)),
    0x2A: OpcodeSpec(0x2A, "SWITCH", ("expr", "switch")),
    0x2B: OpcodeSpec(0x2B, "FILETIME", ("expr",)),
    0x2C: OpcodeSpec(0x2C, "LOADRES", ("expr",) * 4),
    0x2D: OpcodeSpec(0x2D, "PREPRES", ("expr",) * 4),
    0x2E: OpcodeSpec(0x2E, "LOADMASK", ("expr", "expr", "expr")),
    0x2F: OpcodeSpec(0x2F, "MOVE", ("expr", "expr", "expr")),
    0x30: OpcodeSpec(0x30, "NOOP", ()),
    0x31: OpcodeSpec(0x31, "SOUNDPLAY", ("expr",) * 4),
    0x32: OpcodeSpec(0x32, "MOVIE", ("expr",) * 6),
    0x33: OpcodeSpec(0x33, "EXPORT", ("expr",)),
    0x34: OpcodeSpec(0x34, "WRITESTR", ("expr",)),
    0x35: OpcodeSpec(0x35, "WRITEINTS", ("expr", "expr")),
    0x36: OpcodeSpec(0x36, "TEXTGET", ("expr", "u8", "expr")),
    0x37: OpcodeSpec(0x37, "REGGET", ("expr",)),
    0x38: OpcodeSpec(0x38, "SLOTPOS", ("expr", "expr")),
    0x39: OpcodeSpec(0x39, "SETSRC", ()),
    0x3A: OpcodeSpec(0x3A, "DOWNSCALE", ("expr", "expr", "expr")),
    0x3B: OpcodeSpec(0x3B, "RESIZE", ("expr", "expr", "expr")),
    0x3C: OpcodeSpec(0x3C, "RETURN", ("expr",)),
    0x3D: OpcodeSpec(0x3D, "EXISTS", ("expr",)),
    0x3E: OpcodeSpec(0x3E, "SAVEIMG", ("expr", "expr", "paramblock")),
    0x3F: OpcodeSpec(0x3F, "KEYSTATE", ()),
    0x40: OpcodeSpec(0x40, "FULLSCR", ("expr",)),
    0x41: OpcodeSpec(0x41, "FINDDRV", ("expr", "expr")),
    0x42: OpcodeSpec(0x42, "SETAPPKEY", ("expr", "expr")),
    0x43: OpcodeSpec(0x43, "ALPHA", ("expr", "expr")),
    0x44: OpcodeSpec(0x44, "WRITEBYTE", ("expr", "expr", "expr")),
    0x45: OpcodeSpec(0x45, "WRITEWSTR", ("expr", "expr", "expr")),
    0x46: OpcodeSpec(0x46, "OPTION", ("expr", "expr")),
    0x47: OpcodeSpec(0x47, "IMEPOS", ("expr", "expr")),
    0x48: OpcodeSpec(0x48, "BLEND", ("expr",) * 4),
    0x49: OpcodeSpec(0x49, "MOUSEMOVE", ("expr", "expr")),
    0x4A: OpcodeSpec(0x4A, "SNDSEEK", ("expr", "expr")),
    0x4B: OpcodeSpec(0x4B, "SNDVOL", ("expr", "expr", "expr")),
    0x4C: OpcodeSpec(0x4C, "FILLGLOBAL", ("expr", "expr", "expr")),
    0x4D: OpcodeSpec(0x4D, "SHELL", ("expr", "expr", "expr")),
    0x4E: OpcodeSpec(0x4E, "SHOW", ("expr", "expr")),
    0x4F: OpcodeSpec(0x4F, "SLEEP", ("expr",)),
    0x50: OpcodeSpec(0x50, "SNDCTRL", ("expr", "expr", "expr")),
    0x51: OpcodeSpec(0x51, "MOVIESTOP", ()),
    0x52: OpcodeSpec(0x52, "STRCAT", ("expr", "expr")),
    0x53: OpcodeSpec(0x53, "STRCMP", ("expr", "expr")),
    0x54: OpcodeSpec(0x54, "STRSET", ("expr", "expr")),
    0x55: OpcodeSpec(0x55, "STRFIND", ("expr",) * 4),
    0x56: OpcodeSpec(0x56, "STRESCAPE", ("expr", "expr")),
    0x57: OpcodeSpec(0x57, "STRLEN", ("expr",)),
    0x58: OpcodeSpec(0x58, "STRSPLIT", ("expr", "expr")),
    0x59: OpcodeSpec(0x59, "SUBSTR", ("expr",) * 4),
    0x5A: OpcodeSpec(0x5A, "TEXTSIZE", ("expr", "expr")),
    0x5B: OpcodeSpec(0x5B, "TRANS", ("expr",) * 6),
    0x5C: OpcodeSpec(0x5C, "BREAK", ("expr",)),
    0x5D: OpcodeSpec(0x5D, "WAIT", ()),
    0x5E: OpcodeSpec(0x5E, "REGSET", ("expr", "expr")),
    0x5F: OpcodeSpec(0x5F, "READDATA", ("expr", "expr", "expr")),
    0xFF: OpcodeSpec(0xFF, "END", ()),
}

MNEMONIC_TO_OPCODE = {spec.mnemonic: opcode for opcode, spec in OPCODE_SPECS.items()}


def get_opcode_spec(opcode: int) -> OpcodeSpec | None:
    return OPCODE_SPECS.get(opcode)


def get_opcode_by_mnemonic(mnemonic: str) -> int | None:
    return MNEMONIC_TO_OPCODE.get(mnemonic.upper())


def calc_key(size: int) -> int:
    return (((size + 455497) * (((32 * size) & 0xFFFFFFFF) ^ 0xA5)) ^ 0x34A9B129) & 0xFFFFFFFF


def crypt_body(data: bytes | bytearray) -> bytes:
    out = bytearray(data)
    key = calc_key(len(out))
    for offset in range(0x10, len(out) - 3, 4):
        value = int.from_bytes(out[offset : offset + 4], "little")
        out[offset : offset + 4] = (value ^ key).to_bytes(4, "little")
    return bytes(out)


def read_u24be(data: bytes | bytearray, offset: int) -> int:
    if offset + 3 > len(data):
        raise HxbFormatError(f"Unexpected EOF while reading target24 at 0x{offset:06X}")
    return (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]


def write_u24be(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFF:
        raise HxbFormatError(f"target24 out of range: 0x{value:X}")
    return bytes(((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))


def read_u16be(data: bytes | bytearray, offset: int) -> int:
    if offset + 2 > len(data):
        raise HxbFormatError(f"Unexpected EOF while reading u16 at 0x{offset:06X}")
    return (data[offset] << 8) | data[offset + 1]


def write_u16be(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise HxbFormatError(f"u16 out of range: 0x{value:X}")
    return bytes(((value >> 8) & 0xFF, value & 0xFF))


def validate_hxb_header(data: bytes) -> None:
    if len(data) < 16:
        raise HxbFormatError("File too small for HXB header")
    if data[0:2] != b"DD" or data[4:8] != HXB_MAGIC_SUFFIX:
        raise HxbFormatError(f"Unsupported HXB magic: {data[:8]!r}")
    size = read_u24be(data, 8)
    if size != len(data):
        raise HxbFormatError(f"HXB size mismatch: header=0x{size:X}, actual=0x{len(data):X}")


def is_wide_hxb(data: bytes | bytearray) -> bool:
    return data[2:4] == b"Wu"


def is_extended_hxb(data: bytes | bytearray) -> bool:
    return data[0x0B] != 0


def find_narrow_z(data: bytes | bytearray, offset: int) -> int:
    end = bytes(data).find(b"\x00", offset)
    if end < 0:
        raise HxbFormatError(f"Unterminated narrow string at 0x{offset:06X}")
    return end + 1


def find_wide_z(data: bytes | bytearray, offset: int) -> int:
    pos = offset
    while pos + 1 < len(data):
        if data[pos] == 0 and data[pos + 1] == 0:
            return pos + 2
        pos += 2
    raise HxbFormatError(f"Unterminated wide string at 0x{offset:06X}")


def calc_data_start(plain: bytes | bytearray) -> int:
    if not is_extended_hxb(plain):
        return 0x10
    pos = 0x10
    if is_wide_hxb(plain):
        pos = find_wide_z(plain, pos)
        pos = find_wide_z(plain, pos)
    else:
        pos = find_narrow_z(plain, pos)
        pos = find_narrow_z(plain, pos)
    return pos


def bytes_to_placeholder(data: bytes | bytearray) -> str:
    if not data:
        return "{{}}"
    return "{{" + ":".join(f"{byte:02X}" for byte in data) + "}}"


def quote_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _byte_to_placeholder(byte: int) -> str:
    return "{{" + f"{byte:02X}" + "}}"


def decode_semantic_string(data: bytes, wide: bool, encoding: str) -> str:
    if wide:
        chars: List[str] = []
        for offset in range(0, len(data), 2):
            pair = data[offset : offset + 2]
            if len(pair) != 2:
                chars.append(_byte_to_placeholder(pair[0]))
                continue
            code = pair[0] | (pair[1] << 8)
            if code == 0:
                chars.append("{{0000}}")
            elif 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD:
                chars.append(chr(code))
            else:
                chars.append("{{" + pair.hex(":").upper() + "}}")
        return "".join(chars)
    chars = []
    for byte in data:
        if byte == 0:
            chars.append("{{00}}")
            continue
        if byte < 0x20 or byte == 0x7F:
            chars.append(_byte_to_placeholder(byte))
            continue
        try:
            chars.append(bytes((byte,)).decode(encoding))
        except UnicodeDecodeError:
            chars.append(_byte_to_placeholder(byte))
    return "".join(chars)


def encode_semantic_string(text: str, wide: bool, encoding: str) -> bytes:
    out = bytearray()
    index = 0
    while index < len(text):
        if text.startswith("{{", index):
            end = text.find("}}", index + 2)
            if end < 0:
                raise HxbFormatError("Unterminated string placeholder")
            inner = text[index + 2 : end]
            if inner == "0000":
                out.extend(b"\x00\x00")
            else:
                for part in inner.split(":"):
                    if not part:
                        continue
                    out.append(int(part, 16))
            index = end + 2
            continue
        ch = text[index]
        if wide:
            out.extend(ord(ch).to_bytes(2, "little"))
        else:
            out.extend(ch.encode(encoding))
        index += 1
    return bytes(out)


def read_index(data: bytes, offset: int, lo: int) -> Tuple[int, int]:
    if lo < 0x0E:
        return lo, offset
    if lo == 0x0E:
        if offset >= len(data):
            raise HxbFormatError("Unexpected EOF while reading 1-byte index")
        return data[offset], offset + 1
    if offset + 2 > len(data):
        raise HxbFormatError("Unexpected EOF while reading 2-byte index")
    return (data[offset] << 8) | data[offset + 1], offset + 2


def encode_index(base: int, index: int) -> bytes:
    if 0 <= index < 0x0E:
        return bytes((base | index,))
    if 0 <= index <= 0xFF:
        return bytes((base | 0x0E, index))
    if 0 <= index <= 0xFFFF:
        return bytes((base | 0x0F, (index >> 8) & 0xFF, index & 0xFF))
    raise HxbFormatError(f"Index out of range: {index}")


def decode_expr_tokens(data: bytes, wide: bool, encoding: str) -> List[str]:
    tokens: List[str] = []
    pos = 0
    while pos < len(data):
        token = data[pos]
        pos += 1
        if token == 0xFF:
            tokens.append("END")
            break
        hi = token & 0xF0
        lo = token & 0x0F
        if token < 0x40:
            if hi == 0:
                if lo <= 0x07:
                    tokens.append(f"PUSHN {lo}")
                elif lo <= 0x0C:
                    tokens.append(f"PUSHN {7 - lo}")
                elif lo == 0x0D:
                    tokens.append(f"IMM8 {data[pos]}")
                    pos += 1
                elif lo == 0x0E:
                    value = (data[pos] << 8) | data[pos + 1]
                    tokens.append(f"IMM16 {value}")
                    pos += 2
                else:
                    value = (data[pos] << 24) | (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3]
                    tokens.append(f"IMM32 {value}")
                    pos += 4
            else:
                index, pos = read_index(data, pos, lo)
                tokens.append(f"{NUM_VAR_NAMES.get(hi, f'NVAR{hi:02X}')} {index}")
            continue
        if hi >= 0x80:
            if hi == 0x80:
                start = pos
                if wide:
                    while pos + 1 < len(data):
                        pair = data[pos : pos + 2]
                        pos += 2
                        if pair == b"\x00\x00":
                            break
                    string_data = data[start : max(start, pos - 2)]
                else:
                    while pos < len(data):
                        byte = data[pos]
                        pos += 1
                        if byte == 0:
                            break
                    string_data = data[start : max(start, pos - 1)]
                tokens.append(f"TEXT {quote_string(decode_semantic_string(string_data, wide, encoding))}")
            else:
                index, pos = read_index(data, pos, lo)
                tokens.append(f"{STR_VAR_NAMES.get(hi, f'SVAR{hi:02X}')} {index}")
            continue
        if hi == 0x70:
            tokens.append(UNARY_NAMES.get(token, f"OP{token:02X}"))
        elif hi == 0x50:
            tokens.append(CMP_NAMES.get(token, f"OP{token:02X}"))
        elif hi == 0x60:
            tokens.append(BINARY_NAMES.get(token, f"OP{token:02X}"))
        elif token & 0x40:
            tokens.append(ASSIGN_NAMES.get(token, f"OP{token:02X}"))
        else:
            tokens.append(f"OP{token:02X}")
    if pos != len(data):
        tokens.append(f"RAWTAIL {bytes_to_placeholder(data[pos:])}")
    return tokens


def encode_expr_tokens(tokens: List[str], wide: bool, encoding: str) -> bytes:
    out = bytearray()
    for token_text in tokens:
        parts = token_text.split(None, 1)
        name = parts[0].upper()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if name == "END":
            out.append(0xFF)
        elif name == "PUSHN":
            value = int(arg, 0)
            if 0 <= value <= 7:
                out.append(value)
            elif -5 <= value <= -1:
                out.append(7 - value)
            else:
                raise HxbFormatError(f"PUSHN value out of compact range: {value}")
        elif name == "IMM8":
            value = int(arg, 0)
            if not 0 <= value <= 0xFF:
                raise HxbFormatError(f"IMM8 value out of range: {value}")
            out.extend((0x0D, value))
        elif name == "IMM16":
            value = int(arg, 0)
            if not 0 <= value <= 0xFFFF:
                raise HxbFormatError(f"IMM16 value out of range: {value}")
            out.extend((0x0E, (value >> 8) & 0xFF, value & 0xFF))
        elif name == "IMM32":
            value = int(arg, 0)
            if not 0 <= value <= 0xFFFFFFFF:
                raise HxbFormatError(f"IMM32 value out of range: {value}")
            out.extend((0x0F, (value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))
        elif name in NUM_VAR_BASES:
            out.extend(encode_index(NUM_VAR_BASES[name], int(arg, 0)))
        elif name in STR_VAR_BASES:
            out.extend(encode_index(STR_VAR_BASES[name], int(arg, 0)))
        elif name == "TEXT":
            out.append(0x80)
            out.extend(encode_semantic_string(json.loads(arg), wide, encoding))
            out.extend(b"\x00\x00" if wide else b"\x00")
        elif name in TOKEN_NAME_TO_BYTE:
            out.append(TOKEN_NAME_TO_BYTE[name])
        elif name == "RAWTAIL":
            out.extend(placeholder_to_bytes(arg))
        else:
            raise HxbFormatError(f"Unknown expression token: {token_text}")
    return bytes(out)


def decode_param_tokens(data: bytes, wide: bool, encoding: str) -> List[Tuple[str, List[str]]]:
    params: List[Tuple[str, List[str]]] = []
    pos = 0
    while pos < len(data):
        kind = data[pos]
        pos += 1
        if kind == 0:
            params.append(("END", []))
            break
        start = pos
        depth_reader_end = data.find(b"\xFF", pos)
        if depth_reader_end < 0:
            raise HxbFormatError("Unterminated param expression")
        # Reuse the expression decoder by walking exactly once; this handles
        # inline strings whose payload may contain 0xFF bytes.
        reader_pos = pos
        while True:
            token = data[reader_pos]
            reader_pos += 1
            if token == 0xFF:
                break
            hi = token & 0xF0
            lo = token & 0x0F
            if token < 0x40:
                if hi == 0 and lo == 0x0D:
                    reader_pos += 1
                elif hi == 0 and lo == 0x0E:
                    reader_pos += 2
                elif hi == 0 and lo == 0x0F:
                    reader_pos += 4
                elif hi != 0 and lo == 0x0E:
                    reader_pos += 1
                elif hi != 0 and lo == 0x0F:
                    reader_pos += 2
            elif hi >= 0x80:
                if hi == 0x80:
                    if wide:
                        while data[reader_pos : reader_pos + 2] != b"\x00\x00":
                            reader_pos += 2
                        reader_pos += 2
                    else:
                        while data[reader_pos] != 0:
                            reader_pos += 1
                        reader_pos += 1
                elif lo == 0x0E:
                    reader_pos += 1
                elif lo == 0x0F:
                    reader_pos += 2
        pos = reader_pos
        params.append(("STR" if kind == 1 else "NUM", decode_expr_tokens(data[start:pos], wide, encoding)))
    return params


def encode_param_tokens(params: List[Tuple[str, List[str]]], wide: bool, encoding: str) -> bytes:
    out = bytearray()
    for kind, expr_tokens in params:
        kind_upper = kind.upper()
        if kind_upper == "END":
            out.append(0)
            continue
        out.append(1 if kind_upper == "STR" else 2)
        out.extend(encode_expr_tokens(expr_tokens, wide, encoding))
    if not out or out[-1] != 0:
        out.append(0)
    return bytes(out)


NUM_VAR_NAMES = {0x10: "REG", 0x20: "LOCAL", 0x30: "GLOBAL"}
NUM_VAR_BASES = {name: base for base, name in NUM_VAR_NAMES.items()}
STR_VAR_NAMES = {0x90: "SARG", 0xA0: "LSTR", 0xB0: "GSTR"}
STR_VAR_BASES = {name: base for base, name in STR_VAR_NAMES.items()}

UNARY_NAMES = {0x70: "NEG", 0x71: "NOT", 0x72: "RAND", 0x73: "SIN", 0x74: "COS", 0x75: "ATAN", 0x76: "SQRT"}
CMP_NAMES = {0x50: "EQ", 0x51: "NE", 0x52: "LT", 0x53: "LE", 0x54: "GT", 0x55: "GE"}
BINARY_NAMES = {0x60: "ADD", 0x61: "SUB", 0x68: "MUL", 0x69: "DIV", 0x6A: "MOD", 0x6B: "BAND", 0x6C: "BOR", 0x6D: "AND", 0x6E: "OR"}
ASSIGN_NAMES = {0x40: "SET", 0x41: "ADDEQ", 0x42: "SUBEQ", 0x43: "MULEQ", 0x44: "DIVEQ", 0x45: "MODEQ", 0x46: "BANDEQ", 0x47: "BOREQ"}
TOKEN_NAME_TO_BYTE = {name: token for token, name in {**UNARY_NAMES, **CMP_NAMES, **BINARY_NAMES, **ASSIGN_NAMES}.items()}


def placeholder_to_bytes(text: str) -> bytes:
    value = text.strip()
    if not (value.startswith("{{") and value.endswith("}}")):
        raise HxbFormatError(f"Expected byte placeholder, got: {text}")
    inner = value[2:-2].strip()
    if not inner:
        return b""
    parts = inner.split(":")
    out = bytearray()
    for part in parts:
        part = part.strip()
        if len(part) != 2:
            raise HxbFormatError(f"Invalid placeholder byte: {part!r}")
        out.append(int(part, 16))
    return bytes(out)


def iter_jump_operand_indexes(spec: OpcodeSpec) -> Iterable[int]:
    for index, kind in enumerate(spec.operands):
        if kind == "target":
            yield index
