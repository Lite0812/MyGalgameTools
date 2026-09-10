from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from opcodelist import (
    DEFAULT_ENCODING,
    OpcodeSpec,
    bytes_to_placeholder,
    calc_code_start,
    decode_semantic_string,
    decode_expr_tokens,
    decode_paramblock,
    decode_strarg,
    expr_end,
    get_opcode_spec,
    is_line_mode,
    quote_string,
    read_u16be,
    read_u24be,
    strarg_end,
    validate_shsyssc_header,
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
    marker: bytes
    opcode: int
    mnemonic: str
    operands: List[Operand]
    size: int


@dataclass
class ShSysScDocument:
    source_path: Path
    data: bytes
    prefix: bytes
    code_start: int
    line_mode: bool
    instructions: List[Instruction]
    encoding: str


class ScriptReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def eof(self) -> bool:
        return self.pos >= len(self.data)

    def tell(self) -> int:
        return self.pos

    def seek(self, pos: int) -> None:
        if not 0 <= pos <= len(self.data):
            raise DecodeError(f"Seek out of range: 0x{pos:06X}")
        self.pos = pos

    def read_u8(self) -> int:
        if self.pos >= len(self.data):
            raise DecodeError(f"Unexpected EOF at 0x{self.pos:06X}")
        value = self.data[self.pos]
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
        self.pos = expr_end(self.data, self.pos)
        return self.data[start : self.pos]

    def read_strarg(self) -> bytes:
        start = self.pos
        self.pos = strarg_end(self.data, self.pos)
        return self.data[start : self.pos]

    def read_paramblock(self) -> bytes:
        start = self.pos
        while True:
            desc = self.read_u8()
            if desc == 0:
                return self.data[start : self.pos]
            if desc == 1:
                self.pos = strarg_end(self.data, self.pos)
            else:
                self.pos = expr_end(self.data, self.pos)


class Disassembler:
    def __init__(self, path: Path, encoding: str) -> None:
        self.path = path
        self.encoding = encoding
        self.data = path.read_bytes()
        validate_shsyssc_header(self.data)
        self.code_start = calc_code_start(self.data)
        self.line_mode = is_line_mode(self.data)
        self.reader = ScriptReader(self.data)

    def parse(self) -> ShSysScDocument:
        instructions: List[Instruction] = []
        self.reader.seek(self.code_start)
        while not self.reader.eof():
            instruction = self._decode_instruction()
            instructions.append(instruction)
        return ShSysScDocument(
            source_path=self.path,
            data=self.data,
            prefix=self.data[: self.code_start],
            code_start=self.code_start,
            line_mode=self.line_mode,
            instructions=instructions,
            encoding=self.encoding,
        )

    def _decode_instruction(self) -> Instruction:
        start = self.reader.tell()
        marker = self.reader.read_bytes(2) if self.line_mode else b""
        opcode = self.reader.read_u8()
        spec = get_opcode_spec(opcode)
        if spec is None:
            raise DecodeError(f"Unknown opcode 0x{opcode:02X} at PC 0x{start:06X}")
        operands = self._decode_operands(spec)
        size = self.reader.tell() - start
        return Instruction(start, marker, opcode, spec.mnemonic, operands, size)

    def _decode_operands(self, spec: OpcodeSpec) -> List[Operand]:
        if spec.opcode == 0x2A:
            expr = self.reader.read_expr()
            count = read_u16be(self.reader.data, self.reader.tell())
            self.reader.read_bytes(2)
            targets = [self.reader.read_target24() for _ in range(count)]
            return [Operand("expr", expr), Operand("switch", targets)]
        if spec.opcode == 0x36:
            expr = self.reader.read_expr()
            selector = self.reader.read_u8()
            if selector == 0:
                key = Operand("expr", self.reader.read_expr())
            else:
                key = Operand("strarg", self.reader.read_strarg())
            return [Operand("expr", expr), Operand("u8", selector), key]

        operands: List[Operand] = []
        for kind in spec.operands:
            if kind == "expr":
                operands.append(Operand("expr", self.reader.read_expr()))
            elif kind == "strarg":
                operands.append(Operand("strarg", self.reader.read_strarg()))
            elif kind == "paramblock":
                operands.append(Operand("param", self.reader.read_paramblock()))
            elif kind == "target":
                operands.append(Operand("target", self.reader.read_target24()))
            else:
                raise DecodeError(f"Unhandled operand kind {kind!r} for {spec.mnemonic}")
        return operands


def label_name(pc: int) -> str:
    return f"loc_{pc:06X}"


def build_label_map(document: ShSysScDocument) -> Dict[int, str]:
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


def render_expr(data: bytes) -> str:
    return "[" + "; ".join(decode_expr_tokens(data)) + "]"


def expr_const_value_and_kind(data: bytes) -> Optional[tuple[int, str]]:
    tokens = decode_expr_tokens(data)
    if len(tokens) != 2 or tokens[1] != "END":
        return None
    head = tokens[0]
    if head.startswith("PUSHN ") or head.startswith("IMM8 ") or head.startswith("IMM16 ") or head.startswith("IMM32 "):
        kind, value = head.split(None, 1)
        return int(value, 0), kind
    return None


def param_entries(data: bytes) -> Optional[List[tuple[str, int, bytes]]]:
    entries: List[tuple[str, int, bytes]] = []
    pos = 0
    while pos < len(data):
        desc = data[pos]
        pos += 1
        if desc == 0:
            return entries if pos == len(data) else None
        if desc == 1:
            end = strarg_end(data, pos)
            entries.append(("STR", desc, data[pos:end]))
            pos = end
        else:
            end = expr_end(data, pos)
            entries.append(("NUM", desc, data[pos:end]))
            pos = end
    return None


def strarg_text_value(data: bytes, encoding: str) -> Optional[str]:
    if data == b"\x00":
        return ""
    if not data or data[0] < 0x20 or data[-1] != 0:
        return None
    return decode_semantic_string(data[:-1], encoding)


DecodedParam = tuple[str, object, Optional[str]]


def decoded_param_entries(entries: List[tuple[str, int, bytes]], encoding: str) -> Optional[List[DecodedParam]]:
    decoded: List[DecodedParam] = []
    for kind, desc, payload in entries:
        if kind == "NUM":
            if desc != 2:
                return None
            parsed = expr_const_value_and_kind(payload)
            if parsed is None:
                return None
            value, token_kind = parsed
            decoded.append(("NUM", value, token_kind))
        else:
            text = strarg_text_value(payload, encoding)
            if text is None:
                return None
            decoded.append(("STR", text, None))
    return decoded


def param_enc(command_kind: str, params: List[DecodedParam]) -> str:
    parts = [f"cmd:{command_kind}"]
    for index, (kind, _value, token_kind) in enumerate(params):
        if kind == "NUM":
            parts.append(f"p{index}:{token_kind}")
    return ",".join(parts)


def render_with_enc(mnemonic: str, args: List[str], enc: str) -> str:
    payload = ", ".join([*args, f"enc={json.dumps(enc)}"])
    return f"    {mnemonic} {payload}" if payload else f"    {mnemonic}"


def is_num(param: DecodedParam, value: Optional[int] = None) -> bool:
    return param[0] == "NUM" and (value is None or int(param[1]) == value)


def is_str(param: DecodedParam) -> bool:
    return param[0] == "STR"


def render_named_command_macro(command_value: int, command_kind: str, params: List[DecodedParam]) -> Optional[str]:
    if not params or not is_num(params[0]):
        return None
    subcmd = int(params[0][1])
    enc = param_enc(command_kind, params)

    if command_value == 0x34:
        if subcmd == 0 and len(params) == 2 and is_str(params[1]):
            return render_with_enc("TITLE", [json.dumps(params[1][1], ensure_ascii=False)], enc)
        if subcmd == 1 and len(params) == 2 and is_str(params[1]):
            return render_with_enc("BG", [json.dumps(params[1][1], ensure_ascii=False)], enc)
        if subcmd == 2 and len(params) == 3 and is_str(params[1]) and is_num(params[2]):
            return render_with_enc("CHAR", [json.dumps(params[1][1], ensure_ascii=False), f"slot={params[2][1]}"], enc)
        if subcmd == 3 and len(params) == 1:
            return render_with_enc("CHAR_CLEAR", [], enc)
        if subcmd == 4 and len(params) == 3 and is_num(params[1]) and is_num(params[2]):
            return render_with_enc("TRANSITION", [str(params[1][1]), str(params[2][1])], enc)
        if subcmd == 5 and len(params) == 1:
            return render_with_enc("FADE_WAIT", [], enc)
        if subcmd == 6 and len(params) == 2 and is_num(params[1]):
            return render_with_enc("BGM", [str(params[1][1])], enc)
        if subcmd == 7 and 2 <= len(params) <= 3 and all(is_num(param) for param in params[1:]):
            return render_with_enc("SOUND", [str(param[1]) for param in params[1:]], enc)
        if subcmd == 9 and len(params) == 2 and is_str(params[1]):
            return render_with_enc("NEXT_SCRIPT", [json.dumps(params[1][1], ensure_ascii=False)], enc)
        if subcmd == 10 and len(params) == 2 and is_str(params[1]):
            return render_with_enc("SCRIPT_NAME", [json.dumps(params[1][1], ensure_ascii=False)], enc)
        if subcmd == 12 and len(params) == 2 and is_str(params[1]):
            return render_with_enc("FACE", [json.dumps(params[1][1], ensure_ascii=False)], enc)

    if command_value == 0x37:
        if subcmd == 0 and len(params) == 2 and is_num(params[1]):
            return render_with_enc("CHOICE_BEGIN", [str(params[1][1])], enc)
        if subcmd == 1 and len(params) == 3 and is_str(params[1]) and is_num(params[2]):
            return render_with_enc(
                "CHOICE_ITEM",
                [json.dumps(params[1][1], ensure_ascii=False), f"enabled={params[2][1]}"],
                enc,
            )
        if subcmd == 2 and len(params) == 1:
            return render_with_enc("CHOICE_WAIT", [], enc)

    return None


def render_enter_slot_macro(instruction: Instruction, document: ShSysScDocument) -> Optional[str]:
    if instruction.mnemonic != "ENTER_SLOT" or instruction.marker or len(instruction.operands) != 2:
        return None
    if instruction.operands[0].kind != "expr" or instruction.operands[1].kind != "param":
        return None
    command = expr_const_value_and_kind(bytes(instruction.operands[0].value))
    if command is None:
        return None
    command_value, command_kind = command
    entries = param_entries(bytes(instruction.operands[1].value))
    if entries is None:
        return None
    decoded = decoded_param_entries(entries, document.encoding)
    if decoded is None:
        return None

    if command_value == 0x36 and len(decoded) == 6:
        if not all(is_num(param) for param in decoded[:4]) or not is_str(decoded[4]) or not is_str(decoded[5]):
            return None
        nums = [(int(param[1]), str(param[2])) for param in decoded[:4]]
        voice = str(decoded[4][1])
        text = str(decoded[5][1])
        enc = param_enc(command_kind, decoded)
        args = [json.dumps(text, ensure_ascii=False)]
        if voice:
            args.append(f"voice={json.dumps(voice, ensure_ascii=False)}")
        args.extend(
            [
                f"p0={nums[0][0]}",
                f"p1={nums[1][0]}",
                f"p2={nums[2][0]}",
                f"p3={nums[3][0]}",
                f"enc={json.dumps(enc)}",
            ]
        )
        return f"    TEXT {', '.join(args)}"

    named = render_named_command_macro(command_value, command_kind, decoded)
    if named is not None:
        return named

    rendered: List[str] = []
    for _index, (kind, value, _token_kind) in enumerate(decoded):
        if kind == "NUM":
            rendered.append(f"NUM {value}")
        else:
            rendered.append(f"STR {json.dumps(value, ensure_ascii=False)}")
    payload = ", ".join([f"0x{command_value:02X}", *rendered, f"enc={json.dumps(param_enc(command_kind, decoded))}"])
    return f"    CMD {payload}"


def render_operand(operand: Operand, label_map: Dict[int, str], document: ShSysScDocument) -> List[str]:
    if operand.kind == "expr":
        return [render_expr(bytes(operand.value))]
    if operand.kind == "strarg":
        return [decode_strarg(bytes(operand.value), document.encoding)]
    if operand.kind == "param":
        return [decode_paramblock(bytes(operand.value), document.encoding)]
    if operand.kind == "target":
        return [render_target(int(operand.value), label_map)]
    if operand.kind == "switch":
        return [render_target(int(target), label_map) for target in operand.value]
    if operand.kind == "u8":
        return [f"0x{int(operand.value):02X}"]
    raise DecodeError(f"Unhandled render operand kind: {operand.kind}")


def render_instruction(instruction: Instruction, label_map: Dict[int, str], document: ShSysScDocument) -> str:
    specialized = render_enter_slot_macro(instruction, document)
    if specialized is not None:
        return specialized
    parts: List[str] = []
    if instruction.marker:
        parts.append(f".pre {bytes_to_placeholder(instruction.marker)}")
    for operand in instruction.operands:
        parts.extend(render_operand(operand, label_map, document))
    if parts:
        return f"    {instruction.mnemonic} " + ", ".join(parts)
    return f"    {instruction.mnemonic}"


def emit_asm(document: ShSysScDocument) -> str:
    label_map = build_label_map(document)
    lines: List[str] = []
    lines.append(".file kind=shsyssc")
    lines.append(f".source {quote_string(document.source_path.name)}")
    lines.append(f".encoding {quote_string(document.encoding)}")
    lines.append(f".line_mode {1 if document.line_mode else 0}")
    lines.append(f".code_start 0x{document.code_start:06X}")
    lines.append(f".magic {quote_string('SHSysSC')}")
    lines.append(f".header_unknown {bytes_to_placeholder(document.data[0x0C:0x10])}")
    if document.line_mode:
        title = document.data[0x10 : document.code_start - 1]
        lines.append(f".title {quote_string(decode_semantic_string(title, document.encoding))}")
    lines.append("")
    lines.append(".code")
    for instruction in document.instructions:
        if instruction.pc in label_map:
            lines.append("")
            lines.append(f"{label_map[instruction.pc]}:")
        lines.append(render_instruction(instruction, label_map, document))
    lines.append("")
    return "\n".join(lines)


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}.asm.txt")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Disassemble SHSysSC bytecode into semantic asm.txt")
    parser.add_argument("inputs", nargs="+", help="Input SHSysSC script file(s)")
    parser.add_argument("-o", "--output", help="Output asm path; only valid with one input")
    parser.add_argument(
        "--encoding",
        default=DEFAULT_ENCODING,
        help=f"String encoding for inline strarg text (default: {DEFAULT_ENCODING})",
    )
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
