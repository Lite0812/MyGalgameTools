from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from opcodelist import (
    DEFAULT_ENCODING,
    ShSysScFormatError,
    encode_expr_tokens,
    encode_semantic_string,
    encode_strarg_text,
    get_opcode_by_mnemonic,
    get_opcode_spec,
    placeholder_to_bytes,
    write_u16be,
    write_u24be,
)


class AssembleError(Exception):
    pass


@dataclass
class OperandText:
    kind: str
    value: object


@dataclass
class CodeInstruction:
    source_line: int
    mnemonic: str
    marker: bytes
    operands: List[OperandText]


@dataclass
class DataBlock:
    source_line: int
    data: bytes


CodeItem = CodeInstruction | DataBlock


@dataclass
class Program:
    source_name: str
    encoding: str
    prefix: bytes
    instructions: List[CodeItem]
    labels: Dict[str, int]


class AsmParser:
    def __init__(self, path: Path, encoding_override: Optional[str] = None) -> None:
        self.path = path
        self.lines = path.read_text(encoding="utf-8").splitlines()
        self.encoding_override = encoding_override
        self._current_encoding = encoding_override or DEFAULT_ENCODING

    def parse(self) -> Program:
        source_name = self.path.name
        encoding = self.encoding_override or DEFAULT_ENCODING
        prefix: Optional[bytes] = None
        line_mode = 0
        magic = "SHSysSC"
        header_unknown = b"\x00\x00\x00\x00"
        title: Optional[str] = None
        instructions: List[CodeItem] = []
        labels: Dict[str, int] = {}
        in_code = False
        current_index = 0

        for line_no, raw_line in enumerate(self.lines, 1):
            line = self._strip_comment(raw_line).strip()
            if not line:
                continue
            if line.endswith(":") and in_code:
                name = line[:-1].strip()
                if not name:
                    raise AssembleError(f"Empty label at line {line_no}")
                if name in labels:
                    raise AssembleError(f"Duplicate label {name!r} at line {line_no}")
                labels[name] = current_index
                continue
            if line.startswith(".") and not in_code:
                directive, payload = self._split_directive(line)
                if directive == ".file":
                    if "kind=shsyssc" not in payload.lower():
                        raise AssembleError(f"Unsupported .file directive at line {line_no}: {payload}")
                elif directive == ".source":
                    source_name = json.loads(payload)
                elif directive == ".encoding":
                    if self.encoding_override is None:
                        encoding = json.loads(payload)
                    self._current_encoding = encoding
                elif directive == ".line_mode":
                    line_mode = self._parse_int(payload, line_no)
                elif directive == ".code_start":
                    continue
                elif directive == ".magic":
                    magic = json.loads(payload)
                elif directive == ".header_unknown":
                    header_unknown = placeholder_to_bytes(payload)
                    if len(header_unknown) != 4:
                        raise AssembleError(f".header_unknown must contain 4 bytes at line {line_no}")
                elif directive == ".title":
                    title = json.loads(payload)
                elif directive == ".prefix":
                    prefix = placeholder_to_bytes(payload)
                elif directive == ".code":
                    in_code = True
                else:
                    raise AssembleError(f"Unsupported directive at line {line_no}: {directive}")
                continue
            if line == ".code":
                in_code = True
                continue
            if not in_code:
                raise AssembleError(f"Expected directive before .code at line {line_no}: {line}")
            item = self._parse_instruction(line, line_no)
            instructions.append(item)
            current_index += 1

        if prefix is None:
            prefix = self._build_prefix(magic, line_mode, header_unknown, title)
        if not in_code:
            raise AssembleError("Missing .code directive")
        return Program(source_name, encoding, prefix, instructions, labels)

    def _build_prefix(self, magic: str, line_mode: int, header_unknown: bytes, title: Optional[str]) -> bytes:
        if magic != "SHSysSC":
            raise AssembleError(f"Unsupported SHSysSC magic: {magic!r}")
        if line_mode not in {0, 1}:
            raise AssembleError(f".line_mode must be 0 or 1, got {line_mode}")
        header = bytearray(0x10)
        header[:8] = b"SHSysSC\x00"
        header[0x0B] = line_mode
        header[0x0C:0x10] = header_unknown
        if line_mode:
            title_text = title or ""
            header.extend(encode_semantic_string(title_text, self._current_encoding))
            header.append(0)
        return bytes(header)

    def _split_directive(self, line: str) -> tuple[str, str]:
        parts = line.split(None, 1)
        return parts[0], parts[1].strip() if len(parts) > 1 else ""

    def _strip_comment(self, line: str) -> str:
        in_placeholder = False
        in_string = False
        escaped = False
        bracket_depth = 0
        idx = 0
        while idx < len(line):
            ch = line[idx]
            if in_placeholder:
                if ch == "}" and idx + 1 < len(line) and line[idx + 1] == "}":
                    in_placeholder = False
                    idx += 2
                    continue
                idx += 1
                continue
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                idx += 1
                continue
            if ch == "{" and idx + 1 < len(line) and line[idx + 1] == "{":
                in_placeholder = True
                idx += 2
                continue
            if ch == '"':
                in_string = True
            elif ch == "[":
                bracket_depth += 1
            elif ch == "]":
                bracket_depth -= 1
            elif ch == ";" and bracket_depth == 0:
                return line[:idx]
            idx += 1
        return line

    def _split_args(self, text: str) -> List[str]:
        args: List[str] = []
        current: List[str] = []
        in_placeholder = False
        in_string = False
        escaped = False
        bracket_depth = 0
        idx = 0
        while idx < len(text):
            ch = text[idx]
            if in_placeholder:
                current.append(ch)
                if ch == "}" and idx + 1 < len(text) and text[idx + 1] == "}":
                    current.append("}")
                    in_placeholder = False
                    idx += 2
                    continue
                idx += 1
                continue
            if in_string:
                current.append(ch)
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                idx += 1
                continue
            if ch == "{" and idx + 1 < len(text) and text[idx + 1] == "{":
                current.append("{{")
                in_placeholder = True
                idx += 2
                continue
            if ch == '"':
                in_string = True
                current.append(ch)
            elif ch == "[":
                bracket_depth += 1
                current.append(ch)
            elif ch == "]":
                bracket_depth -= 1
                current.append(ch)
            elif ch == "," and bracket_depth == 0:
                args.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
            idx += 1
        tail = "".join(current).strip()
        if tail:
            args.append(tail)
        return args

    def _parse_instruction(self, line: str, line_no: int) -> CodeItem:
        pieces = line.split(None, 1)
        mnemonic = pieces[0].upper()
        operand_text = pieces[1].strip() if len(pieces) > 1 else ""
        if mnemonic == "DATA":
            return DataBlock(line_no, placeholder_to_bytes(operand_text))
        if mnemonic == "TEXT":
            return self._parse_text_macro(operand_text, line_no)
        if mnemonic == "CMD":
            return self._parse_cmd_macro(operand_text, line_no)
        if mnemonic in {
            "TITLE",
            "BG",
            "CHAR",
            "CHAR_CLEAR",
            "TRANSITION",
            "FADE_WAIT",
            "BGM",
            "SOUND",
            "NEXT_SCRIPT",
            "SCRIPT_NAME",
            "FACE",
            "CHOICE_BEGIN",
            "CHOICE_ITEM",
            "CHOICE_WAIT",
        }:
            return self._parse_named_cmd_macro(mnemonic, operand_text, line_no)

        opcode = get_opcode_by_mnemonic(mnemonic)
        if opcode is None:
            raise AssembleError(f"Unknown mnemonic at line {line_no}: {mnemonic}")
        spec = get_opcode_spec(opcode)
        if spec is None:
            raise AssembleError(f"Unknown opcode for mnemonic at line {line_no}: {mnemonic}")

        args = self._split_args(operand_text) if operand_text else []
        marker = b""
        if args and args[0].startswith(".pre "):
            marker = placeholder_to_bytes(args[0][len(".pre ") :].strip())
            args = args[1:]

        if opcode == 0x2A:
            if not args:
                raise AssembleError(f"SWITCH requires operands at line {line_no}")
            operands = [OperandText("expr", self._parse_expr(args[0], line_no))]
            operands.extend(OperandText("target", arg) for arg in args[1:])
            return CodeInstruction(line_no, mnemonic, marker, operands)

        if opcode == 0x36:
            if len(args) != 3:
                raise AssembleError(f"TEXTGET expects 3 operands at line {line_no}, got {len(args)}")
            selector = self._parse_int(args[1], line_no)
            if not 0 <= selector <= 0xFF:
                raise AssembleError(f"TEXTGET selector out of range at line {line_no}: {selector}")
            third = (
                OperandText("expr", self._parse_expr(args[2], line_no))
                if selector == 0
                else OperandText("strarg", self._parse_strarg(args[2], line_no))
            )
            return CodeInstruction(
                line_no,
                mnemonic,
                marker,
                [OperandText("expr", self._parse_expr(args[0], line_no)), OperandText("u8", selector), third],
            )

        expected = list(spec.operands)
        if len(args) != len(expected):
            raise AssembleError(f"{mnemonic} expects {len(expected)} operands at line {line_no}, got {len(args)}")
        operands: List[OperandText] = []
        for kind, arg in zip(expected, args):
            if kind == "expr":
                operands.append(OperandText("expr", self._parse_expr(arg, line_no)))
            elif kind == "strarg":
                operands.append(OperandText("strarg", self._parse_strarg(arg, line_no)))
            elif kind == "paramblock":
                operands.append(OperandText("param", self._parse_paramblock(arg, line_no)))
            elif kind == "target":
                operands.append(OperandText("target", arg))
            else:
                raise AssembleError(f"Unhandled operand kind {kind!r} for {mnemonic}")
        return CodeInstruction(line_no, mnemonic, marker, operands)

    def _parse_text_macro(self, operand_text: str, line_no: int) -> CodeInstruction:
        args = self._split_args(operand_text)
        if not args:
            raise AssembleError(f"TEXT requires text at line {line_no}")
        try:
            text = json.loads(args[0])
        except json.JSONDecodeError as exc:
            raise AssembleError(f"Invalid TEXT string at line {line_no}: {exc}") from exc
        values: Dict[str, object] = {"voice": "", "p0": 0, "p1": 0, "p2": 0, "p3": 0}
        encodings: Dict[str, Optional[str]] = {"cmd": None, "p0": None, "p1": None, "p2": None, "p3": None}
        self._parse_macro_key_values(args[1:], values, encodings, line_no)
        command_expr = encode_expr_tokens([self._macro_const_token(0x36, encodings["cmd"]), "END"])
        param_bytes = bytearray()
        for key in ("p0", "p1", "p2", "p3"):
            param_bytes.append(2)
            param_bytes.extend(encode_expr_tokens([self._macro_const_token(int(values[key]), encodings[key]), "END"]))
        param_bytes.append(1)
        param_bytes.extend(self._encode_text_strarg(str(values["voice"]), line_no))
        param_bytes.append(1)
        param_bytes.extend(self._encode_text_strarg(str(text), line_no))
        param_bytes.append(0)
        return CodeInstruction(
            line_no,
            "ENTER_SLOT",
            b"",
            [OperandText("expr", command_expr), OperandText("param", bytes(param_bytes))],
        )

    def _parse_cmd_macro(self, operand_text: str, line_no: int) -> CodeInstruction:
        args = self._split_args(operand_text)
        if not args:
            raise AssembleError(f"CMD requires command id at line {line_no}")
        command = self._parse_int(args[0], line_no)
        encodings: Dict[str, Optional[str]] = {"cmd": None}
        param_items = []
        for index, arg in enumerate(args[1:]):
            if arg.lower().startswith("enc="):
                self._parse_enc_value(arg.split("=", 1)[1].strip(), encodings, line_no)
                continue
            upper = arg.upper()
            if upper.startswith("NUM "):
                key = f"p{len(param_items)}"
                encodings.setdefault(key, None)
                param_items.append(("NUM", self._parse_int(arg[4:].strip(), line_no), key))
            elif upper.startswith("STR "):
                param_items.append(("STR", json.loads(arg[4:].strip()), None))
            else:
                raise AssembleError(f"Invalid CMD argument at line {line_no}: {arg}")
        return self._build_cmd_instruction(command, param_items, encodings, line_no)

    def _parse_named_cmd_macro(self, mnemonic: str, operand_text: str, line_no: int) -> CodeInstruction:
        args = self._split_args(operand_text) if operand_text else []
        encodings: Dict[str, Optional[str]] = {"cmd": None, "p0": None}
        values: List[str] = []
        for arg in args:
            if arg.lower().startswith("enc="):
                self._parse_enc_value(arg.split("=", 1)[1].strip(), encodings, line_no)
            else:
                values.append(arg)

        def expect_count(count: int) -> None:
            if len(values) != count:
                raise AssembleError(f"{mnemonic} expects {count} arguments at line {line_no}, got {len(values)}")

        def parse_string(index: int) -> str:
            try:
                return str(json.loads(values[index]))
            except json.JSONDecodeError as exc:
                raise AssembleError(f"Invalid {mnemonic} string at line {line_no}: {exc}") from exc

        def parse_positional_or_key(index: int, key: str) -> int:
            value = values[index]
            if "=" in value:
                got_key, got_value = value.split("=", 1)
                if got_key.strip().lower() != key:
                    raise AssembleError(f"Expected {key}= for {mnemonic} at line {line_no}, got {got_key.strip()}")
                value = got_value.strip()
            return self._parse_int(value, line_no)

        def build(command: int, subcmd: int, rest: List[tuple[str, object, Optional[str]]]) -> CodeInstruction:
            param_items: List[tuple[str, object, Optional[str]]] = [("NUM", subcmd, "p0")]
            param_items.extend(rest)
            for _kind, _value, key in param_items:
                if key is not None:
                    encodings.setdefault(str(key), None)
            return self._build_cmd_instruction(command, param_items, encodings, line_no)

        if mnemonic == "TITLE":
            expect_count(1)
            return build(0x34, 0, [("STR", parse_string(0), None)])
        if mnemonic == "BG":
            expect_count(1)
            return build(0x34, 1, [("STR", parse_string(0), None)])
        if mnemonic == "CHAR":
            expect_count(2)
            return build(0x34, 2, [("STR", parse_string(0), None), ("NUM", parse_positional_or_key(1, "slot"), "p2")])
        if mnemonic == "CHAR_CLEAR":
            expect_count(0)
            return build(0x34, 3, [])
        if mnemonic == "TRANSITION":
            expect_count(2)
            return build(0x34, 4, [("NUM", self._parse_int(values[0], line_no), "p1"), ("NUM", self._parse_int(values[1], line_no), "p2")])
        if mnemonic == "FADE_WAIT":
            expect_count(0)
            return build(0x34, 5, [])
        if mnemonic == "BGM":
            expect_count(1)
            return build(0x34, 6, [("NUM", self._parse_int(values[0], line_no), "p1")])
        if mnemonic == "SOUND":
            if len(values) not in {1, 2}:
                raise AssembleError(f"SOUND expects 1 or 2 arguments at line {line_no}, got {len(values)}")
            rest = [("NUM", self._parse_int(values[0], line_no), "p1")]
            if len(values) == 2:
                rest.append(("NUM", self._parse_int(values[1], line_no), "p2"))
            return build(0x34, 7, rest)
        if mnemonic == "NEXT_SCRIPT":
            expect_count(1)
            return build(0x34, 9, [("STR", parse_string(0), None)])
        if mnemonic == "SCRIPT_NAME":
            expect_count(1)
            return build(0x34, 10, [("STR", parse_string(0), None)])
        if mnemonic == "FACE":
            expect_count(1)
            return build(0x34, 12, [("STR", parse_string(0), None)])
        if mnemonic == "CHOICE_BEGIN":
            expect_count(1)
            return build(0x37, 0, [("NUM", self._parse_int(values[0], line_no), "p1")])
        if mnemonic == "CHOICE_ITEM":
            expect_count(2)
            return build(0x37, 1, [("STR", parse_string(0), None), ("NUM", parse_positional_or_key(1, "enabled"), "p2")])
        if mnemonic == "CHOICE_WAIT":
            expect_count(0)
            return build(0x37, 2, [])

        raise AssembleError(f"Unhandled named macro at line {line_no}: {mnemonic}")

    def _build_cmd_instruction(
        self,
        command: int,
        param_items: List[tuple[str, object, Optional[str]]],
        encodings: Dict[str, Optional[str]],
        line_no: int,
    ) -> CodeInstruction:
        command_expr = encode_expr_tokens([self._macro_const_token(command, encodings["cmd"]), "END"])
        param_bytes = bytearray()
        for kind, value, key in param_items:
            if kind == "NUM":
                param_bytes.append(2)
                param_bytes.extend(encode_expr_tokens([self._macro_const_token(int(value), encodings.get(str(key))), "END"]))
            else:
                param_bytes.append(1)
                param_bytes.extend(self._encode_text_strarg(str(value), line_no))
        param_bytes.append(0)
        return CodeInstruction(
            line_no,
            "ENTER_SLOT",
            b"",
            [OperandText("expr", command_expr), OperandText("param", bytes(param_bytes))],
        )

    def _encode_text_strarg(self, text: str, line_no: int) -> bytes:
        try:
            return encode_strarg_text("TEXT " + json.dumps(text, ensure_ascii=False), self._current_encoding)
        except (ShSysScFormatError, ValueError, json.JSONDecodeError) as exc:
            raise AssembleError(f"Invalid macro string at line {line_no}: {exc}") from exc

    def _parse_macro_key_values(
        self,
        args: List[str],
        values: Dict[str, object],
        encodings: Dict[str, Optional[str]],
        line_no: int,
    ) -> None:
        for arg in args:
            if "=" not in arg:
                raise AssembleError(f"Invalid macro argument at line {line_no}: {arg}")
            key, value = arg.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if key == "enc":
                self._parse_enc_value(value, encodings, line_no)
            elif key == "voice":
                values[key] = json.loads(value)
            elif key in values:
                values[key] = self._parse_int(value, line_no)
            else:
                raise AssembleError(f"Unknown macro argument at line {line_no}: {key}")

    def _parse_enc_value(self, value: str, encodings: Dict[str, Optional[str]], line_no: int) -> None:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AssembleError(f"Invalid enc string at line {line_no}: {exc}") from exc
        for part in payload.split(","):
            if not part.strip():
                continue
            if ":" not in part:
                raise AssembleError(f"Invalid enc fragment at line {line_no}: {part}")
            key, enc = part.split(":", 1)
            key = key.strip().lower()
            if key not in encodings:
                encodings[key] = None
            encodings[key] = enc.strip().upper()

    def _macro_const_token(self, value: int, encoding_kind: Optional[str]) -> str:
        if encoding_kind:
            kind = encoding_kind.upper()
            if kind == "PUSHN" and -5 <= value <= 7:
                return f"PUSHN {value}"
            if kind == "IMM8" and 0 <= value <= 0xFF:
                return f"IMM8 0x{value:02X}"
            if kind == "IMM16" and 0 <= value <= 0xFFFF:
                return f"IMM16 0x{value:04X}"
            if kind == "IMM32" and 0 <= value <= 0xFFFFFFFF:
                return f"IMM32 0x{value:08X}"
            raise AssembleError(f"Cannot encode {value} as {encoding_kind}")
        return self._const_token(value)

    def _parse_expr(self, text: str, line_no: int) -> bytes:
        value = text.strip()
        if value.startswith(".expr "):
            return placeholder_to_bytes(value[len(".expr ") :].strip())
        if value.startswith("[") and value.endswith("]"):
            tokens = [part.strip() for part in value[1:-1].split(";") if part.strip()]
            try:
                return encode_expr_tokens(tokens)
            except ShSysScFormatError as exc:
                raise AssembleError(f"Invalid expression at line {line_no}: {exc}") from exc
        try:
            intval = int(value, 0)
        except ValueError as exc:
            raise AssembleError(f"Expression must be bracketed at line {line_no}: {text}") from exc
        return encode_expr_tokens([self._const_token(intval), "END"])

    def _parse_strarg(self, text: str, line_no: int) -> bytes:
        try:
            return encode_strarg_text(text, self._current_encoding)
        except (ShSysScFormatError, ValueError, json.JSONDecodeError) as exc:
            raise AssembleError(f"Invalid strarg at line {line_no}: {exc}") from exc

    def _parse_paramblock(self, text: str, line_no: int) -> bytes:
        value = text.strip()
        if value.startswith(".param "):
            return placeholder_to_bytes(value[len(".param ") :].strip())
        if not (value.startswith("[") and value.endswith("]")):
            raise AssembleError(f"Invalid paramblock at line {line_no}: {text}")
        inner = value[1:-1].strip()
        if not inner:
            return b"\x00"
        out = bytearray()
        for part in self._split_args(inner):
            piece = part.strip()
            if not piece:
                continue
            upper = piece.upper()
            if upper.startswith("STR "):
                out.append(1)
                out.extend(self._parse_strarg(piece[4:].strip(), line_no))
            elif upper.startswith("NUM "):
                rest = piece[4:].strip()
                desc_text, expr_text = rest.split(None, 1)
                desc = self._parse_int(desc_text, line_no)
                if desc == 0 or desc == 1 or not 0 <= desc <= 0xFF:
                    raise AssembleError(f"NUM descriptor must be 2..255 at line {line_no}: {desc}")
                out.append(desc)
                out.extend(self._parse_expr(expr_text, line_no))
            elif upper.startswith("RAWTAIL "):
                out.extend(placeholder_to_bytes(piece[8:].strip()))
            else:
                raise AssembleError(f"Invalid paramblock item at line {line_no}: {piece}")
        out.append(0)
        return bytes(out)

    def _const_token(self, value: int) -> str:
        if -5 <= value <= 7:
            return f"PUSHN {value}"
        if 0 <= value <= 0xFF:
            return f"IMM8 0x{value:02X}"
        if 0 <= value <= 0xFFFF:
            return f"IMM16 0x{value:04X}"
        if 0 <= value <= 0xFFFFFFFF:
            return f"IMM32 0x{value:08X}"
        raise AssembleError(f"Constant out of supported range: {value}")

    def _parse_int(self, text: str, line_no: int) -> int:
        try:
            return int(text, 0)
        except ValueError as exc:
            raise AssembleError(f"Invalid integer at line {line_no}: {text}") from exc


class Assembler:
    def __init__(self, program: Program) -> None:
        self.program = program

    def assemble(self) -> bytes:
        label_offsets = self._layout_labels()
        body = bytearray(self.program.prefix)
        for item in self.program.instructions:
            if isinstance(item, DataBlock):
                body.extend(item.data)
            else:
                body.extend(self._encode_instruction(item, label_offsets))
        self._update_size(body)
        return bytes(body)

    def _layout_labels(self) -> Dict[str, int]:
        index_offsets: List[int] = []
        current = len(self.program.prefix)
        for item in self.program.instructions:
            index_offsets.append(current)
            if isinstance(item, DataBlock):
                current += len(item.data)
            else:
                current += self._instruction_size(item)
        label_offsets: Dict[str, int] = {}
        for name, index in self.program.labels.items():
            if index == len(self.program.instructions):
                label_offsets[name] = current
            elif 0 <= index < len(index_offsets):
                label_offsets[name] = index_offsets[index]
            else:
                raise AssembleError(f"Label {name!r} points outside instruction list")
        return label_offsets

    def _instruction_size(self, instruction: CodeInstruction) -> int:
        opcode = get_opcode_by_mnemonic(instruction.mnemonic)
        if opcode is None:
            raise AssembleError(f"Unknown mnemonic: {instruction.mnemonic}")
        spec = get_opcode_spec(opcode)
        if spec is None:
            raise AssembleError(f"Unknown opcode for mnemonic: {instruction.mnemonic}")
        size = len(instruction.marker) + 1
        if opcode == 0x2A:
            expr, switch_targets = self._expect_switch_operands(instruction)
            return size + len(expr.value) + 2 + 3 * len(switch_targets)
        if opcode == 0x36:
            if len(instruction.operands) != 3:
                raise AssembleError(f"TEXTGET at line {instruction.source_line} expects 3 operands")
            size += len(instruction.operands[0].value) + 1 + len(instruction.operands[2].value)
            return size
        expected = list(spec.operands)
        actual = list(instruction.operands)
        if len(actual) != len(expected):
            raise AssembleError(
                f"{instruction.mnemonic} at line {instruction.source_line} expects {len(expected)} operands, got {len(actual)}"
            )
        for kind, operand in zip(expected, actual):
            if kind == "expr":
                self._expect_kind(instruction, operand, "expr")
                size += len(operand.value)
            elif kind == "strarg":
                self._expect_kind(instruction, operand, "strarg")
                size += len(operand.value)
            elif kind == "paramblock":
                self._expect_kind(instruction, operand, "param")
                size += len(operand.value)
            elif kind == "target":
                self._expect_kind(instruction, operand, "target")
                size += 3
            else:
                raise AssembleError(f"Unhandled operand kind {kind!r} for {instruction.mnemonic}")
        return size

    def _encode_instruction(self, instruction: CodeInstruction, label_offsets: Dict[str, int]) -> bytes:
        opcode = get_opcode_by_mnemonic(instruction.mnemonic)
        if opcode is None:
            raise AssembleError(f"Unknown mnemonic: {instruction.mnemonic}")
        spec = get_opcode_spec(opcode)
        if spec is None:
            raise AssembleError(f"Unknown opcode for mnemonic: {instruction.mnemonic}")
        out = bytearray(instruction.marker)
        out.append(opcode)
        if opcode == 0x2A:
            expr, switch_targets = self._expect_switch_operands(instruction)
            out.extend(expr.value)
            out.extend(write_u16be(len(switch_targets)))
            for target in switch_targets:
                out.extend(write_u24be(self._resolve_target(target, label_offsets)))
            return bytes(out)
        if opcode == 0x36:
            if len(instruction.operands) != 3:
                raise AssembleError(f"TEXTGET at line {instruction.source_line} expects 3 operands")
            self._expect_kind(instruction, instruction.operands[0], "expr")
            self._expect_kind(instruction, instruction.operands[1], "u8")
            selector = int(instruction.operands[1].value)
            if selector == 0:
                self._expect_kind(instruction, instruction.operands[2], "expr")
            else:
                self._expect_kind(instruction, instruction.operands[2], "strarg")
            out.extend(instruction.operands[0].value)
            out.append(selector)
            out.extend(instruction.operands[2].value)
            return bytes(out)
        expected = list(spec.operands)
        actual = list(instruction.operands)
        if len(actual) != len(expected):
            raise AssembleError(
                f"{instruction.mnemonic} at line {instruction.source_line} expects {len(expected)} operands, got {len(actual)}"
            )
        for kind, operand in zip(expected, actual):
            if kind == "expr":
                self._expect_kind(instruction, operand, "expr")
                out.extend(operand.value)
            elif kind == "strarg":
                self._expect_kind(instruction, operand, "strarg")
                out.extend(operand.value)
            elif kind == "paramblock":
                self._expect_kind(instruction, operand, "param")
                out.extend(operand.value)
            elif kind == "target":
                self._expect_kind(instruction, operand, "target")
                out.extend(write_u24be(self._resolve_target(operand, label_offsets)))
            else:
                raise AssembleError(f"Unhandled operand kind {kind!r} for {instruction.mnemonic}")
        return bytes(out)

    def _expect_switch_operands(self, instruction: CodeInstruction) -> tuple[OperandText, List[OperandText]]:
        if not instruction.operands:
            raise AssembleError(f"SWITCH requires operands at line {instruction.source_line}")
        expr = instruction.operands[0]
        self._expect_kind(instruction, expr, "expr")
        targets = instruction.operands[1:]
        for target in targets:
            self._expect_kind(instruction, target, "target")
        return expr, targets

    def _expect_kind(self, instruction: CodeInstruction, operand: OperandText, expected: str) -> None:
        if operand.kind != expected:
            raise AssembleError(
                f"{instruction.mnemonic} at line {instruction.source_line} expected {expected}, got {operand.kind}"
            )

    def _resolve_target(self, operand: OperandText, label_offsets: Dict[str, int]) -> int:
        text = str(operand.value)
        if text in label_offsets:
            return label_offsets[text]
        try:
            return int(text, 0)
        except ValueError as exc:
            raise AssembleError(f"Undefined target operand {text!r}") from exc

    def _update_size(self, data: bytearray) -> None:
        size = len(data)
        if size > 0xFFFFFF:
            raise AssembleError(f"SHSysSC too large for size24: 0x{size:X}")
        if len(data) < 0x10:
            raise AssembleError("Output too small for SHSysSC header")
        data[8] = (size >> 16) & 0xFF
        data[9] = (size >> 8) & 0xFF
        data[10] = size & 0xFF


def default_output_path(input_path: Path) -> Path:
    name = input_path.name
    base = name[:-8] if name.endswith(".asm.txt") else input_path.stem
    return input_path.with_name(f"{base}.rebuild")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reassemble SHSysSC semantic asm.txt into binary")
    parser.add_argument("inputs", nargs="+", help="Input asm.txt file(s)")
    parser.add_argument("-o", "--output", help="Output binary path; only valid with one input")
    parser.add_argument(
        "--encoding",
        help=f"Override encoding metadata (default: asm .encoding, or {DEFAULT_ENCODING} if absent)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    input_paths = [Path(value) for value in args.inputs]
    if args.output and len(input_paths) != 1:
        raise SystemExit("-o/--output can only be used with one input file")
    for input_path in input_paths:
        output_path = Path(args.output) if args.output else default_output_path(input_path)
        program = AsmParser(input_path, args.encoding).parse()
        output_path.write_bytes(Assembler(program).assemble())
        print(f"{input_path} -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
