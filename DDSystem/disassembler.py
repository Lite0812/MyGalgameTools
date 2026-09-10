from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from opcodelist import (
    DEFAULT_ENCODING,
    HxbFormatError,
    OpcodeSpec,
    bytes_to_placeholder,
    calc_data_start,
    crypt_body,
    decode_expr_tokens,
    decode_param_tokens,
    get_opcode_spec,
    is_extended_hxb,
    is_wide_hxb,
    quote_string,
    read_u16be,
    read_u24be,
    validate_hxb_header,
)


class DecodeError(Exception):
    pass


@dataclass
class Operand:
    kind: str
    value: object


@dataclass
class Instruction:
    pc: int
    prefix: bytes
    opcode: int
    mnemonic: str
    operands: List[Operand]
    size: int


@dataclass
class HxbDocument:
    source_path: Path
    encrypted: bytes
    plain: bytes
    prefix: bytes
    data_start: int
    instructions: List[Instruction]
    trailing_data: bytes
    encoding: str
    crypted: bool


class HxbReader:
    def __init__(self, data: bytes, wide: bool) -> None:
        self.data = data
        self.pos = 0
        self.wide = wide

    def eof(self) -> bool:
        return self.pos >= len(self.data)

    def tell(self) -> int:
        return self.pos

    def seek(self, pos: int) -> None:
        if not 0 <= pos <= len(self.data):
            raise DecodeError(f"Seek out of range: 0x{pos:06X}")
        self.pos = pos

    def peek_u8(self) -> int:
        if self.pos >= len(self.data):
            raise DecodeError(f"Unexpected EOF at 0x{self.pos:06X}")
        return self.data[self.pos]

    def read_u8(self) -> int:
        value = self.peek_u8()
        self.pos += 1
        return value

    def read_bytes(self, count: int) -> bytes:
        if self.pos + count > len(self.data):
            raise DecodeError(f"Unexpected EOF while reading {count} bytes at 0x{self.pos:06X}")
        value = self.data[self.pos : self.pos + count]
        self.pos += count
        return value

    def read_target24(self) -> int:
        value = read_u24be(self.data, self.pos)
        self.pos += 3
        return value

    def read_expr(self) -> bytes:
        start = self.pos
        while True:
            token = self.read_u8()
            if token == 0xFF:
                return self.data[start : self.pos]
            self._skip_expr_token_payload(token)

    def _skip_expr_token_payload(self, token: int) -> None:
        hi = token & 0xF0
        lo = token & 0x0F
        if token < 0x40:
            if hi == 0:
                if lo == 0x0D:
                    self.read_bytes(1)
                elif lo == 0x0E:
                    self.read_bytes(2)
                elif lo == 0x0F:
                    self.read_bytes(4)
            elif lo == 0x0E:
                self.read_bytes(1)
            elif lo == 0x0F:
                self.read_bytes(2)
            return
        if hi >= 0x80:
            if hi == 0x80:
                self._skip_inline_string()
            elif lo == 0x0E:
                self.read_bytes(1)
            elif lo == 0x0F:
                self.read_bytes(2)
            return
        # Comparison, arithmetic, unary and assignment/update tokens carry no
        # immediate bytes in the recovered VM format.

    def _skip_inline_string(self) -> None:
        if self.wide:
            while True:
                pair = self.read_bytes(2)
                if pair == b"\x00\x00":
                    return
        while True:
            if self.read_u8() == 0:
                return

    def read_paramblock(self) -> bytes:
        start = self.pos
        while True:
            kind = self.read_u8()
            if kind == 0:
                return self.data[start : self.pos]
            self.read_expr()


class Disassembler:
    def __init__(self, path: Path, encoding: str) -> None:
        self.path = path
        self.encoding = encoding
        self.encrypted = path.read_bytes()
        validate_hxb_header(self.encrypted)
        candidate_plain = crypt_body(self.encrypted)
        encrypted_start = calc_data_start(self.encrypted)
        plain_start = calc_data_start(candidate_plain)
        if self._looks_like_instruction_stream(self.encrypted, encrypted_start):
            self.plain = self.encrypted
            self.crypted = False
        else:
            self.plain = candidate_plain
            self.crypted = True
        self.wide = is_wide_hxb(self.plain)
        self.extended = is_extended_hxb(self.plain)
        self.data_start = calc_data_start(self.plain)
        self.reader = HxbReader(self.plain, self.wide)

    def _looks_like_instruction_stream(self, data: bytes, start: int) -> bool:
        if start >= len(data):
            return False
        opcode = data[start]
        if get_opcode_spec(opcode) is None:
            return False
        # Most shipped HXB scripts begin with a branch/call/setup opcode in
        # plaintext. This probe prevents double-decrypting already-plain files
        # while still allowing encrypted inputs to fall through to XOR decoding.
        return opcode in {0x00, 0x02, 0x03, 0x06, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x30, 0xFF}

    def parse(self) -> HxbDocument:
        instructions: List[Instruction] = []
        trailing_data = b""
        self.reader.seek(self.data_start)
        while not self.reader.eof():
            pc = self.reader.tell()
            try:
                instruction = self._decode_instruction()
            except DecodeError:
                trailing_data = self.plain[pc:]
                self.reader.seek(len(self.plain))
                break
            instructions.append(instruction)
        return HxbDocument(
            source_path=self.path,
            encrypted=self.encrypted,
            plain=self.plain,
            prefix=self.plain[: self.data_start],
            data_start=self.data_start,
            instructions=instructions,
            trailing_data=trailing_data,
            encoding=self.encoding,
            crypted=self.crypted,
        )

    def _decode_instruction(self) -> Instruction:
        start = self.reader.tell()
        prefix = self.reader.read_bytes(2) if self.extended else b""
        opcode = self.reader.read_u8()
        spec = get_opcode_spec(opcode)
        if spec is None:
            raise DecodeError(f"Unknown opcode 0x{opcode:02X} at PC 0x{start:06X}")
        operands = self._decode_operands(spec)
        size = self.reader.tell() - start
        return Instruction(start, prefix, opcode, spec.mnemonic, operands, size)

    def _decode_operands(self, spec: OpcodeSpec) -> List[Operand]:
        if spec.opcode == 0x2A:
            expr = self.reader.read_expr()
            count = read_u16be(self.reader.data, self.reader.tell())
            self.reader.read_bytes(2)
            targets = [self.reader.read_target24() for _ in range(count)]
            return [Operand("expr", expr), Operand("switch", targets)]

        operands: List[Operand] = []
        for kind in spec.operands:
            if kind == "expr":
                operands.append(Operand("expr", self.reader.read_expr()))
            elif kind == "paramblock":
                operands.append(Operand("param", self.reader.read_paramblock()))
            elif kind == "target":
                operands.append(Operand("target", self.reader.read_target24()))
            elif kind == "u8":
                operands.append(Operand("u8", self.reader.read_u8()))
            else:
                raise DecodeError(f"Unhandled operand kind {kind!r} for {spec.mnemonic}")
        return operands


def quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def label_name(pc: int) -> str:
    return f"loc_{pc:06X}"


def build_label_map(document: HxbDocument) -> Dict[int, str]:
    targets = set()
    for instruction in document.instructions:
        for operand in instruction.operands:
            if operand.kind == "target":
                targets.add(int(operand.value))
            elif operand.kind == "switch":
                targets.update(int(target) for target in operand.value)
    instruction_pcs = {instruction.pc for instruction in document.instructions}
    return {target: label_name(target) for target in sorted(targets) if target in instruction_pcs}


def render_target(value: int, label_map: Dict[int, str]) -> str:
    return label_map.get(value, f"0x{value:06X}")


def render_expr(data: bytes, document: HxbDocument) -> str:
    tokens = decode_expr_tokens(data, is_wide_hxb(document.plain), document.encoding)
    simple = render_expr_tokens_semantic(tokens)
    if simple is not None:
        return simple
    return "[" + "; ".join(tokens) + "]"


def render_expr_tokens_semantic(tokens: List[str]) -> Optional[str]:
    if len(tokens) == 2 and tokens[1] == "END":
        return render_expr_atom(tokens[0])
    if len(tokens) == 4 and tokens[3] == "END" and tokens[2] in CMP_SYMBOLS:
        left = render_expr_atom(tokens[0])
        right = render_expr_atom(tokens[1])
        if left is not None and right is not None:
            return f"{left} {CMP_SYMBOLS[tokens[2]]} {right}"
    if len(tokens) == 4 and tokens[3] == "END" and tokens[2] in BINARY_SYMBOLS:
        left = render_expr_atom(tokens[0])
        right = render_expr_atom(tokens[1])
        if left is not None and right is not None:
            return f"{left} {BINARY_SYMBOLS[tokens[2]]} {right}"
    return None


def render_expr_atom(token: str) -> Optional[str]:
    parts = token.split(None, 1)
    name = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    if name in {"PUSHN", "IMM8", "IMM16", "IMM32"}:
        return arg
    if name in {"REG", "LOCAL", "GLOBAL", "SARG", "LSTR", "GSTR"}:
        return f"{name}[{arg}]"
    if name == "TEXT":
        return arg
    return None


CMP_SYMBOLS = {"EQ": "==", "NE": "!=", "LT": "<", "LE": "<=", "GT": ">", "GE": ">="}
BINARY_SYMBOLS = {"ADD": "+", "SUB": "-", "MUL": "*", "DIV": "/", "MOD": "%", "BAND": "&", "BOR": "|", "AND": "&&", "OR": "||"}


def render_param(data: bytes, document: HxbDocument) -> str:
    parts: List[str] = []
    for kind, tokens in decode_param_tokens(data, is_wide_hxb(document.plain), document.encoding):
        if kind == "END":
            continue
        parts.append(f"{kind} [" + "; ".join(tokens) + "]")
    return "[" + ", ".join(parts) + "]"


def expr_const(data: bytes, document: HxbDocument) -> Optional[int]:
    tokens = decode_expr_tokens(data, is_wide_hxb(document.plain), document.encoding)
    if len(tokens) != 2 or tokens[1] != "END":
        return None
    head = tokens[0]
    if head.startswith("PUSHN ") or head.startswith("IMM8 ") or head.startswith("IMM16 ") or head.startswith("IMM32 "):
        return int(head.split(None, 1)[1], 0)
    return None


def token_text_value(tokens: List[str]) -> Optional[str]:
    if len(tokens) != 2 or tokens[1] != "END" or not tokens[0].startswith("TEXT "):
        return None
    try:
        return json.loads(tokens[0].split(None, 1)[1])
    except json.JSONDecodeError:
        return None


def token_const_value(tokens: List[str]) -> Optional[int]:
    if len(tokens) != 2 or tokens[1] != "END":
        return None
    head = tokens[0]
    if head.startswith("PUSHN ") or head.startswith("IMM8 ") or head.startswith("IMM16 ") or head.startswith("IMM32 "):
        return int(head.split(None, 1)[1], 0)
    return None


def const_token_text(tokens: List[str]) -> Optional[str]:
    if len(tokens) != 2 or tokens[1] != "END":
        return None
    head = tokens[0]
    if head.startswith("PUSHN ") or head.startswith("IMM8 ") or head.startswith("IMM16 ") or head.startswith("IMM32 "):
        return head
    return None


def const_token_value_and_kind(tokens: List[str]) -> Optional[tuple[int, str]]:
    token = const_token_text(tokens)
    if token is None:
        return None
    kind, value = token.split(None, 1)
    return int(value, 0), kind


def parse_enter_command(instruction: Instruction, document: HxbDocument) -> Optional[tuple[int, List[tuple[str, List[str]]]]]:
    if instruction.mnemonic != "ENTER" or instruction.prefix or len(instruction.operands) != 2:
        return None
    command = expr_const(instruction.operands[0].value, document)
    if command is None:
        return None
    params = decode_param_tokens(instruction.operands[1].value, is_wide_hxb(document.plain), document.encoding)
    return command, [(kind, tokens) for kind, tokens in params if kind != "END"]


def param_const(params: List[tuple[str, List[str]]], index: int) -> Optional[tuple[int, str]]:
    if index >= len(params) or params[index][0] != "NUM":
        return None
    return const_token_value_and_kind(params[index][1])


def param_text(params: List[tuple[str, List[str]]], index: int) -> Optional[str]:
    if index >= len(params) or params[index][0] != "STR":
        return None
    return token_text_value(params[index][1])


def render_enter_macro(instruction: Instruction, document: HxbDocument) -> Optional[str]:
    parsed = parse_enter_command(instruction, document)
    if parsed is None:
        return None
    command, params = parsed
    if command == 54:
        return render_text_macro(params)
    if command == 53:
        return render_resource_macro(params)
    return None


def render_text_macro(params: List[tuple[str, List[str]]]) -> Optional[str]:
    if len(params) != 5:
        return None
    text = param_text(params, 0)
    voice = param_text(params, 1)
    page = param_const(params, 2)
    text_id = param_const(params, 3)
    next_id = param_const(params, 4)
    if text is None or voice is None or page is None or text_id is None or next_id is None:
        return None
    page_value, page_kind = page
    id_value, id_kind = text_id
    next_value, next_kind = next_id
    return (
        f"    TEXT {quote(text)}, voice={quote(voice)}, page={page_value}, id={id_value}, next={next_value}, "
        f"enc={quote(f'page:{page_kind},id:{id_kind},next:{next_kind}')}"
    )


def render_resource_macro(params: List[tuple[str, List[str]]]) -> Optional[str]:
    if not params:
        return None
    command = param_const(params, 0)
    if command is None:
        return None
    command_value, command_kind = command
    if command_value == 0 and len(params) == 2:
        title = param_text(params, 1)
        if title is not None:
            return f"    CHAPTER {quote(title)}, enc={quote(f'cmd:{command_kind}')}"
    if command_value == 1 and len(params) == 3:
        name = param_text(params, 1)
        channel = param_const(params, 2)
        if name is not None and channel is not None:
            return f"    SFX {quote(name)}, channel={channel[0]}, enc={quote(f'cmd:{command_kind},channel:{channel[1]}')}"
    if command_value == 1 and len(params) == 4:
        a = param_const(params, 1)
        b = param_const(params, 2)
        duration = param_const(params, 3)
        if a is not None and b is not None and duration is not None:
            return f"    SOUNDCTRL {a[0]}, {b[0]}, duration={duration[0]}, enc={quote(f'cmd:{command_kind},a:{a[1]},b:{b[1]},duration:{duration[1]}')}"
    if command_value == 3 and len(params) == 3:
        mode = param_const(params, 1)
        duration = param_const(params, 2)
        if mode is not None and duration is not None:
            return f"    SELECTMODE {mode[0]}, duration={duration[0]}, enc={quote(f'cmd:{command_kind},arg:{mode[1]},duration:{duration[1]}')}"
    if command_value == 4 and len(params) == 3:
        left = param_const(params, 1)
        right = param_const(params, 2)
        if left is not None and right is not None:
            return f"    SCENEMODE {left[0]}, {right[0]}, enc={quote(f'cmd:{command_kind},a:{left[1]},b:{right[1]}')}"
    if command_value == 5 and len(params) == 3:
        name = param_text(params, 1)
        duration = param_const(params, 2)
        if name is not None and duration is not None:
            return f"    BG {quote(name)}, duration={duration[0]}, enc={quote(f'cmd:{command_kind},duration:{duration[1]}')}"
    if command_value == 6 and len(params) == 2:
        name = param_text(params, 1)
        if name is not None:
            return f"    SPRITEFACE {quote(name)}, enc={quote(f'cmd:{command_kind}')}"
    if command_value == 7 and len(params) == 2:
        layer = param_const(params, 1)
        if layer is not None:
            return f"    SPRITECLEAR layer={layer[0]}, enc={quote(f'cmd:{command_kind},layer:{layer[1]}')}"
    if command_value == 7 and len(params) == 3:
        name = param_text(params, 1)
        layer = param_const(params, 2)
        if name is not None and layer is not None:
            return f"    SPRITE {quote(name)}, layer={layer[0]}, enc={quote(f'cmd:{command_kind},layer:{layer[1]}')}"
    if command_value == 28 and len(params) == 2:
        script = param_text(params, 1)
        if script is not None:
            return f"    NEXTSCRIPT {quote(script)}, enc={quote(f'cmd:{command_kind}')}"
    if len(params) == 1:
        macro = ENTER_COMMAND_NAMES.get(command_value)
        if macro is not None:
            return f"    {macro} enc={quote(f'cmd:{command_kind}')}"
        return f"    ENTERCMD {command_value}, enc={quote(f'cmd:{command_kind}')}"
    if len(params) == 2:
        value = param_const(params, 1)
        if value is not None:
            macro = ENTER_COMMAND_NAMES.get(command_value)
            if macro is not None:
                return f"    {macro} {value[0]}, enc={quote(f'cmd:{command_kind},arg:{value[1]}')}"
    params_text = render_enter_params(params[1:])
    return f"    ENTERCMD {command_value}, {params_text}, enc={quote(f'cmd:{command_kind}')}"


def render_enter_params(params: List[tuple[str, List[str]]]) -> str:
    return ", ".join(f"{kind.lower()}=[" + "; ".join(tokens) + "]" for kind, tokens in params)


ENTER_COMMAND_NAMES = {
    3: "SELECTMODE",
    8: "FADEWAIT",
    10: "FADETIME",
    11: "INITSCENE",
}


def try_render_text_instruction(instruction: Instruction, document: HxbDocument) -> Optional[str]:
    return render_enter_macro(instruction, document)


def render_operand(operand: Operand, label_map: Dict[int, str], document: HxbDocument) -> List[str]:
    if operand.kind == "expr":
        return [render_expr(operand.value, document)]
    if operand.kind == "param":
        return [render_param(operand.value, document)]
    if operand.kind == "target":
        return [render_target(int(operand.value), label_map)]
    if operand.kind == "switch":
        return [render_target(int(target), label_map) for target in operand.value]
    if operand.kind == "u8":
        return [f"0x{int(operand.value):02X}"]
    raise DecodeError(f"Unhandled render operand kind: {operand.kind}")


def render_instruction(instruction: Instruction, label_map: Dict[int, str], document: HxbDocument) -> str:
    specialized = try_render_text_instruction(instruction, document)
    if specialized is not None:
        return specialized
    parts: List[str] = []
    if instruction.prefix:
        parts.append(f".pre {bytes_to_placeholder(instruction.prefix)}")
    for operand in instruction.operands:
        parts.extend(render_operand(operand, label_map, document))
    if parts:
        return f"    {instruction.mnemonic} " + ", ".join(parts)
    return f"    {instruction.mnemonic}"


def emit_data_block(data: bytes, lines: List[str], indent: str = "    ", chunk_size: int = 32) -> None:
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset : offset + chunk_size]
        lines.append(f"{indent}DATA {bytes_to_placeholder(chunk)}")


def emit_asm(document: HxbDocument) -> str:
    label_map = build_label_map(document)
    lines: List[str] = []
    lines.append(".file kind=hxb")
    lines.append(f".source {quote(document.source_path.name)}")
    lines.append(f".encoding {quote(document.encoding)}")
    lines.append(f".wide {1 if is_wide_hxb(document.plain) else 0}")
    lines.append(f".extended {1 if is_extended_hxb(document.plain) else 0}")
    lines.append(f".crypted {1 if document.crypted else 0}")
    if len(document.prefix) == 16:
        magic = document.prefix[:8].rstrip(b"\x00").decode("ascii", errors="replace")
        header_unknown = int.from_bytes(document.prefix[12:16], "big")
        lines.append(f".magic {quote(magic)}")
        lines.append(f".header_unknown 0x{header_unknown:08X}")
    else:
        lines.append(f".prefix {bytes_to_placeholder(document.prefix)}")
    lines.append("")
    lines.append(".code")
    for instruction in document.instructions:
        if instruction.pc in label_map:
            lines.append("")
            lines.append(f"{label_map[instruction.pc]}:")
        lines.append(render_instruction(instruction, label_map, document))
    if document.trailing_data:
        lines.append("")
        emit_data_block(document.trailing_data, lines)
    lines.append("")
    return "\n".join(lines)


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}.asm.txt")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Disassemble HXB bytecode into semantic asm.txt")
    parser.add_argument("inputs", nargs="+", help="Input .hxb file(s)")
    parser.add_argument("-o", "--output", help="Output asm path; only valid with one input")
    parser.add_argument("--encoding", default=DEFAULT_ENCODING, help="Text encoding note for asm metadata")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    input_paths = [Path(value) for value in args.inputs]
    if args.output and len(input_paths) != 1:
        raise SystemExit("-o/--output can only be used with one input file")
    for input_path in input_paths:
        output_path = Path(args.output) if args.output else default_output_path(input_path)
        document = Disassembler(input_path, args.encoding).parse()
        output_path.write_text(emit_asm(document), encoding="utf-8", newline="\n")
        print(f"{input_path} -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
