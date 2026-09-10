from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from opcodelist import (
    DEFAULT_ENCODING,
    HxbFormatError,
    bytes_to_placeholder,
    crypt_body,
    encode_expr_tokens,
    encode_param_tokens,
    decode_expr_tokens,
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
    prefix: bytes
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
    wide: int
    extended: int
    crypted: int
    prefix: bytes
    instructions: List[CodeItem]
    labels: Dict[str, int]


class AsmParser:
    def __init__(self, path: Path, encoding_override: Optional[str] = None) -> None:
        self.path = path
        self.lines = path.read_text(encoding="utf-8").splitlines()
        self.encoding_override = encoding_override

    def parse(self) -> Program:
        source_name = self.path.name
        encoding = self.encoding_override or DEFAULT_ENCODING
        wide = 0
        extended = 0
        crypted = 1
        magic = "DDWuHXB"
        header_unknown = 0
        prefix: Optional[bytes] = None
        instructions: List[CodeItem] = []
        labels: Dict[str, int] = {}
        in_code = False
        current_index = 0
        self._current_encoding = encoding
        self._current_wide = wide
        self._current_extended = extended

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
                    if "kind=hxb" not in payload:
                        raise AssembleError(f"Unsupported .file directive at line {line_no}: {payload}")
                elif directive == ".source":
                    source_name = json.loads(payload)
                elif directive == ".encoding":
                    if self.encoding_override is None:
                        encoding = json.loads(payload)
                    self._current_encoding = encoding
                elif directive == ".wide":
                    wide = self._parse_int(payload, line_no)
                    self._current_wide = wide
                elif directive == ".extended":
                    extended = self._parse_int(payload, line_no)
                    self._current_extended = extended
                elif directive == ".crypted":
                    crypted = self._parse_int(payload, line_no)
                elif directive == ".magic":
                    magic = json.loads(payload)
                elif directive == ".header_unknown":
                    header_unknown = self._parse_int(payload, line_no)
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
            instruction = self._parse_instruction(line, line_no)
            if isinstance(instruction, list):
                instructions.extend(instruction)
                current_index += len(instruction)
            else:
                instructions.append(instruction)
                current_index += 1

        if prefix is None:
            prefix = self._build_prefix(magic, header_unknown, line_no if self.lines else 1)
        if not in_code:
            raise AssembleError("Missing .code directive")
        return Program(source_name, encoding, wide, extended, crypted, prefix, instructions, labels)

    def _build_prefix(self, magic: str, header_unknown: int, line_no: int) -> bytes:
        magic_bytes = magic.encode("ascii")
        if len(magic_bytes) > 7:
            raise AssembleError(f"HXB magic too long at line {line_no}: {magic}")
        if not 0 <= header_unknown <= 0xFFFFFFFF:
            raise AssembleError(f"header_unknown out of range: 0x{header_unknown:X}")
        header = bytearray(16)
        header[: len(magic_bytes)] = magic_bytes
        header[7] = 0
        header[11] = self._current_extended
        header[12:16] = header_unknown.to_bytes(4, "big")
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

    def _parse_instruction(self, line: str, line_no: int) -> CodeItem | List[CodeItem]:
        pieces = line.split(None, 1)
        mnemonic = pieces[0].upper()
        operand_text = pieces[1].strip() if len(pieces) > 1 else ""
        if mnemonic == "DATA":
            return DataBlock(line_no, placeholder_to_bytes(operand_text))
        if mnemonic == "TEXT":
            return self._parse_text_macro(operand_text, line_no)
        if mnemonic in {"BG", "SPRITE", "SFX", "SOUNDCTRL", "SPRITEFACE", "SPRITECLEAR", "NEXTSCRIPT", "ENTERCMD", "CHAPTER", "SCENEMODE", "SELECTMODE", "FADEWAIT", "FADETIME", "INITSCENE"}:
            return self._parse_enter_command_macro(mnemonic, operand_text, line_no)
        opcode = get_opcode_by_mnemonic(mnemonic)
        if opcode is None:
            raise AssembleError(f"Unknown mnemonic at line {line_no}: {mnemonic}")
        args = self._split_args(operand_text) if operand_text else []
        prefix = b""
        operands: List[OperandText] = []
        for arg in args:
            if arg.startswith(".pre "):
                if prefix:
                    raise AssembleError(f"Duplicate .pre at line {line_no}")
                prefix = placeholder_to_bytes(arg[len(".pre ") :].strip())
            elif arg.startswith(".expr "):
                operands.append(OperandText("expr", placeholder_to_bytes(arg[len(".expr ") :].strip())))
            elif arg.startswith(".param "):
                operands.append(OperandText("param", placeholder_to_bytes(arg[len(".param ") :].strip())))
            elif arg.startswith(".u8 "):
                operands.append(OperandText("u8", self._parse_int(arg[len(".u8 ") :].strip(), line_no)))
            elif arg.startswith("["):
                if self._looks_like_param(arg):
                    operands.append(OperandText("param", self._parse_param(arg, line_no)))
                else:
                    operands.append(OperandText("expr", self._parse_expr(arg, line_no)))
            elif self._looks_like_u8_operand(opcode, len(operands)):
                operands.append(OperandText("u8", self._parse_int(arg, line_no)))
            elif self._looks_like_expr_operand(opcode, len(operands)):
                operands.append(OperandText("expr", self._parse_expr(arg, line_no)))
            else:
                operands.append(OperandText("target", arg))
        return CodeInstruction(line_no, mnemonic, prefix, operands)

    def _parse_enter_command_macro(self, mnemonic: str, operand_text: str, line_no: int) -> CodeInstruction:
        args = self._split_args(operand_text) if operand_text else []
        if mnemonic == "SOUNDCTRL":
            if len(args) < 2:
                raise AssembleError(f"SOUNDCTRL requires two numeric args at line {line_no}")
            values = {"a": self._parse_int(args[0], line_no), "b": self._parse_int(args[1], line_no), "duration": 0}
            encodings = {"cmd": None, "a": None, "b": None, "duration": None}
            self._parse_macro_key_values(args[2:], values, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(1, encodings["cmd"]), "END"]),
                ("NUM", [self._macro_const_token(int(values["a"]), encodings["a"]), "END"]),
                ("NUM", [self._macro_const_token(int(values["b"]), encodings["b"]), "END"]),
                ("NUM", [self._macro_const_token(int(values["duration"]), encodings["duration"]), "END"]),
            ]
        elif mnemonic == "SFX":
            if not args:
                raise AssembleError(f"SFX requires resource name at line {line_no}")
            name = json.loads(args[0])
            values = {"channel": 0}
            encodings = {"cmd": None, "channel": None}
            self._parse_macro_key_values(args[1:], values, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(1, encodings["cmd"]), "END"]),
                ("STR", [f"TEXT {json.dumps(name, ensure_ascii=False)}", "END"]),
                ("NUM", [self._macro_const_token(int(values["channel"]), encodings["channel"]), "END"]),
            ]
        elif mnemonic == "BG":
            if not args:
                raise AssembleError(f"BG requires resource name at line {line_no}")
            name = json.loads(args[0])
            values = {"duration": 0}
            encodings = {"cmd": None, "duration": None}
            self._parse_macro_key_values(args[1:], values, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(5, encodings["cmd"]), "END"]),
                ("STR", [f"TEXT {json.dumps(name, ensure_ascii=False)}", "END"]),
                ("NUM", [self._macro_const_token(int(values["duration"]), encodings["duration"]), "END"]),
            ]
        elif mnemonic == "SPRITE":
            if not args:
                raise AssembleError(f"SPRITE requires resource name at line {line_no}")
            name = json.loads(args[0])
            values = {"layer": 0}
            encodings = {"cmd": None, "layer": None}
            self._parse_macro_key_values(args[1:], values, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(7, encodings["cmd"]), "END"]),
                ("STR", [f"TEXT {json.dumps(name, ensure_ascii=False)}", "END"]),
                ("NUM", [self._macro_const_token(int(values["layer"]), encodings["layer"]), "END"]),
            ]
        elif mnemonic == "SPRITEFACE":
            if not args:
                raise AssembleError(f"SPRITEFACE requires resource name at line {line_no}")
            name = json.loads(args[0])
            encodings = {"cmd": None}
            self._parse_macro_key_values(args[1:], {}, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(6, encodings["cmd"]), "END"]),
                ("STR", [f"TEXT {json.dumps(name, ensure_ascii=False)}", "END"]),
            ]
        elif mnemonic == "SPRITECLEAR":
            values = {"layer": 0}
            encodings = {"cmd": None, "layer": None}
            self._parse_macro_key_values(args, values, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(7, encodings["cmd"]), "END"]),
                ("NUM", [self._macro_const_token(int(values["layer"]), encodings["layer"]), "END"]),
            ]
        elif mnemonic == "NEXTSCRIPT":
            if not args:
                raise AssembleError(f"NEXTSCRIPT requires script name at line {line_no}")
            name = json.loads(args[0])
            encodings = {"cmd": None}
            self._parse_macro_key_values(args[1:], {}, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(28, encodings["cmd"]), "END"]),
                ("STR", [f"TEXT {json.dumps(name, ensure_ascii=False)}", "END"]),
            ]
        elif mnemonic == "CHAPTER":
            if not args:
                raise AssembleError(f"CHAPTER requires title at line {line_no}")
            title = json.loads(args[0])
            encodings = {"cmd": None}
            self._parse_macro_key_values(args[1:], {}, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(0, encodings["cmd"]), "END"]),
                ("STR", [f"TEXT {json.dumps(title, ensure_ascii=False)}", "END"]),
            ]
        elif mnemonic == "SCENEMODE":
            if len(args) < 2:
                raise AssembleError(f"SCENEMODE requires two numeric args at line {line_no}")
            values = {"a": self._parse_int(args[0], line_no), "b": self._parse_int(args[1], line_no)}
            encodings = {"cmd": None, "a": None, "b": None}
            self._parse_macro_key_values(args[2:], values, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(4, encodings["cmd"]), "END"]),
                ("NUM", [self._macro_const_token(int(values["a"]), encodings["a"]), "END"]),
                ("NUM", [self._macro_const_token(int(values["b"]), encodings["b"]), "END"]),
            ]
        elif mnemonic == "SELECTMODE":
            command_map = {"SELECTMODE": 3}
            values = {"arg": self._parse_int(args[0], line_no) if args else 0, "duration": None}
            encodings = {"cmd": None, "arg": None, "duration": None}
            self._parse_macro_key_values(args[1:], values, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(command_map[mnemonic], encodings["cmd"]), "END"]),
                ("NUM", [self._macro_const_token(int(values["arg"]), encodings["arg"]), "END"]),
            ]
            if values["duration"] is not None:
                params.append(("NUM", [self._macro_const_token(int(values["duration"]), encodings["duration"]), "END"]))
        elif mnemonic in {"FADEWAIT", "INITSCENE"}:
            command_map = {"FADEWAIT": 8, "INITSCENE": 11}
            encodings = {"cmd": None}
            self._parse_macro_key_values(args, {}, encodings, line_no)
            params = [("NUM", [self._macro_const_token(command_map[mnemonic], encodings["cmd"]), "END"])]
        elif mnemonic == "FADETIME":
            if not args:
                raise AssembleError(f"FADETIME requires value at line {line_no}")
            values = {"arg": self._parse_int(args[0], line_no)}
            encodings = {"cmd": None, "arg": None}
            self._parse_macro_key_values(args[1:], values, encodings, line_no)
            params = [
                ("NUM", [self._macro_const_token(10, encodings["cmd"]), "END"]),
                ("NUM", [self._macro_const_token(int(values["arg"]), encodings["arg"]), "END"]),
            ]
        else:
            if not args:
                raise AssembleError(f"ENTERCMD requires command id at line {line_no}")
            command = self._parse_int(args[0], line_no)
            encodings: Dict[str, Optional[str]] = {"cmd": None}
            command_params = []
            for arg in args[1:]:
                if "=" not in arg:
                    raise AssembleError(f"Invalid ENTERCMD argument at line {line_no}: {arg}")
                key, value = arg.split("=", 1)
                key = key.strip().lower()
                value = value.strip()
                if key == "enc":
                    self._parse_enc_value(value, encodings, line_no)
                elif key in {"num", "str"}:
                    if not (value.startswith("[") and value.endswith("]")):
                        raise AssembleError(f"ENTERCMD {key} requires bracketed expression at line {line_no}: {value}")
                    tokens = [token.strip() for token in value[1:-1].split(";") if token.strip()]
                    command_params.append((key.upper(), tokens))
                else:
                    raise AssembleError(f"Unknown ENTERCMD argument at line {line_no}: {key}")
            params = [("NUM", [self._macro_const_token(command, encodings["cmd"]), "END"])] + command_params
        command_expr = encode_expr_tokens(["IMM8 53", "END"], self._wide_enabled(), self._encoding())
        param_bytes = encode_param_tokens(params, self._wide_enabled(), self._encoding())
        return CodeInstruction(line_no, "ENTER", b"", [OperandText("expr", command_expr), OperandText("param", param_bytes)])

    def _entercmd_value_tokens(self, value: str, encoding_kind: Optional[str], line_no: int) -> List[str]:
        if encoding_kind:
            return [self._macro_const_token(self._parse_int(value, line_no), encoding_kind), "END"]
        return decode_expr_tokens(self._encode_semantic_expr(value, line_no), self._wide_enabled(), self._encoding())

    def _parse_entercmd_enc_value(
        self,
        value: str,
        encodings: Dict[str, Optional[str]],
        param_encodings: Dict[int, str],
        line_no: int,
    ) -> None:
        try:
            enc_payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AssembleError(f"Invalid enc string at line {line_no}: {exc}") from exc
        for part in enc_payload.split(","):
            if not part.strip():
                continue
            if ":" not in part:
                raise AssembleError(f"Invalid enc fragment at line {line_no}: {part}")
            enc_key, enc = part.split(":", 1)
            enc_key = enc_key.strip().lower()
            enc = enc.strip().upper()
            if enc_key == "cmd":
                encodings["cmd"] = enc
            elif enc_key.startswith("p") and enc_key[1:].isdigit():
                param_encodings[int(enc_key[1:])] = enc
            else:
                raise AssembleError(f"Unknown enc key at line {line_no}: {enc_key}")

    def _parse_raw_enter_params(self, raw_params: str, line_no: int) -> List[tuple[str, List[str]]]:
        pieces = self._split_args(raw_params)
        params: List[tuple[str, List[str]]] = []
        for piece in pieces:
            item = piece.strip()
            if not item:
                continue
            kind, expr_text = item.split(None, 1)
            if kind.upper() not in {"STR", "NUM"}:
                raise AssembleError(f"Invalid raw ENTERCMD param kind at line {line_no}: {kind}")
            expr = expr_text.strip()
            if not (expr.startswith("[") and expr.endswith("]")):
                raise AssembleError(f"Invalid raw ENTERCMD expression at line {line_no}: {expr_text}")
            tokens = [token.strip() for token in expr[1:-1].split(";") if token.strip()]
            params.append((kind.upper(), tokens))
        return params

    def _parse_macro_key_values(self, args: List[str], values: Dict[str, object], encodings: Dict[str, Optional[str]], line_no: int) -> None:
        for arg in args:
            if "=" not in arg:
                raise AssembleError(f"Invalid macro argument at line {line_no}: {arg}")
            key, value = arg.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if key == "enc":
                self._parse_enc_value(value, encodings, line_no)
            elif key in values:
                values[key] = self._parse_int(value, line_no)
            else:
                raise AssembleError(f"Unknown macro argument at line {line_no}: {key}")

    def _parse_enc_value(self, value: str, encodings: Dict[str, Optional[str]], line_no: int) -> None:
        try:
            enc_payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AssembleError(f"Invalid enc string at line {line_no}: {exc}") from exc
        for part in enc_payload.split(","):
            if not part.strip():
                continue
            if ":" not in part:
                raise AssembleError(f"Invalid enc fragment at line {line_no}: {part}")
            enc_key, enc = part.split(":", 1)
            enc_key = enc_key.strip().lower()
            if enc_key not in encodings:
                raise AssembleError(f"Unknown enc key at line {line_no}: {enc_key}")
            encodings[enc_key] = enc.strip().upper()

    def _parse_text_macro(self, operand_text: str, line_no: int) -> CodeInstruction:
        args = self._split_args(operand_text)
        if not args:
            raise AssembleError(f"TEXT requires a string at line {line_no}")
        try:
            text = json.loads(args[0])
        except json.JSONDecodeError as exc:
            raise AssembleError(f"Invalid TEXT string at line {line_no}: {exc}") from exc
        values = {"voice": "", "page": 0, "id": 0, "next": 0}
        encodings = {"page": None, "id": None, "next": None}
        for arg in args[1:]:
            if "=" not in arg:
                raise AssembleError(f"Invalid TEXT named argument at line {line_no}: {arg}")
            key, value = arg.split("=", 1)
            key = key.strip().lower()
            if key == "enc":
                self._parse_enc_value(value, encodings, line_no)
                continue
            if key == "name":
                key = "voice"
            value = value.strip()
            if key not in values:
                raise AssembleError(f"Unknown TEXT argument at line {line_no}: {key}")
            if key == "voice":
                try:
                    values[key] = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise AssembleError(f"Invalid TEXT voice at line {line_no}: {exc}") from exc
            else:
                values[key] = self._parse_int(value, line_no)
        command_expr = encode_expr_tokens(["IMM8 54", "END"], self._wide_enabled(), self._encoding())
        params = encode_param_tokens(
            [
                ("STR", [f"TEXT {json.dumps(text, ensure_ascii=False)}", "END"]),
                ("STR", [f"TEXT {json.dumps(values['voice'], ensure_ascii=False)}", "END"]),
                ("NUM", [self._macro_const_token(int(values["page"]), encodings["page"]), "END"]),
                ("NUM", [self._macro_const_token(int(values["id"]), encodings["id"]), "END"]),
                ("NUM", [self._macro_const_token(int(values["next"]), encodings["next"]), "END"]),
            ],
            self._wide_enabled(),
            self._encoding(),
        )
        return CodeInstruction(line_no, "ENTER", b"", [OperandText("expr", command_expr), OperandText("param", params)])

    def _macro_const_token(self, value: int, encoding_kind: Optional[str]) -> str:
        if encoding_kind:
            kind = encoding_kind.upper()
            if kind == "PUSHN" and -5 <= value <= 7:
                return f"PUSHN {value}"
            if kind == "IMM8" and 0 <= value <= 0xFF:
                return f"IMM8 {value}"
            if kind == "IMM16" and 0 <= value <= 0xFFFF:
                return f"IMM16 {value}"
            if kind == "IMM32" and 0 <= value <= 0xFFFFFFFF:
                return f"IMM32 {value}"
            raise AssembleError(f"Cannot encode {value} as {encoding_kind}")
        return self._const_token(value)

    def _const_token(self, value: int) -> str:
        if -5 <= value <= 7:
            return f"PUSHN {value}"
        if 0 <= value <= 0xFF:
            return f"IMM8 {value}"
        if 0 <= value <= 0xFFFF:
            return f"IMM16 {value}"
        if 0 <= value <= 0xFFFFFFFF:
            return f"IMM32 {value}"
        raise AssembleError(f"Constant out of supported range: {value}")

    def _looks_like_expr_operand(self, opcode: int, operand_index: int) -> bool:
        spec = get_opcode_spec(opcode)
        if spec is None or operand_index >= len(spec.operands):
            return False
        return spec.operands[operand_index] == "expr"

    def _looks_like_u8_operand(self, opcode: int, operand_index: int) -> bool:
        spec = get_opcode_spec(opcode)
        if spec is None or operand_index >= len(spec.operands):
            return False
        return spec.operands[operand_index] == "u8"

    def _looks_like_param(self, text: str) -> bool:
        inner = text.strip()[1:-1].strip()
        return not inner or inner.startswith("STR ") or inner.startswith("NUM ") or inner.startswith("END")

    def _parse_expr(self, text: str, line_no: int) -> bytes:
        value = text.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            tokens = [part.strip() for part in inner.split(";") if part.strip()]
            try:
                return encode_expr_tokens(tokens, self._wide_enabled(), self._encoding())
            except HxbFormatError as exc:
                raise AssembleError(f"Invalid expression at line {line_no}: {exc}") from exc
        try:
            return self._encode_semantic_expr(value, line_no)
        except HxbFormatError as exc:
            raise AssembleError(f"Invalid expression at line {line_no}: {exc}") from exc

    def _encode_semantic_expr(self, value: str, line_no: int) -> bytes:
        import re

        if value.startswith('"'):
            try:
                text = json.loads(value)
            except json.JSONDecodeError as exc:
                raise HxbFormatError(f"Invalid string expression at line {line_no}: {exc}") from exc
            return encode_expr_tokens([f"TEXT {json.dumps(text, ensure_ascii=False)}", "END"], self._wide_enabled(), self._encoding())
        match = re.fullmatch(r"([A-Z]+)\[(\d+)\]", value)
        if match:
            return encode_expr_tokens([f"{match.group(1)} {match.group(2)}", "END"], self._wide_enabled(), self._encoding())
        if re.fullmatch(r"-?\d+", value):
            return encode_expr_tokens([self._const_token(int(value)), "END"], self._wide_enabled(), self._encoding())
        for symbol, token in ((">=", "GE"), ("<=", "LE"), ("!=", "NE"), ("==", "EQ"), (">", "GT"), ("<", "LT")):
            if symbol in value:
                left, right = value.split(symbol, 1)
                left_bytes = self._encode_semantic_expr(left.strip(), line_no)
                right_bytes = self._encode_semantic_expr(right.strip(), line_no)
                return left_bytes[:-1] + right_bytes[:-1] + encode_expr_tokens([token, "END"], self._wide_enabled(), self._encoding())
        raise HxbFormatError(f"Cannot parse semantic expression: {value}")

    def _parse_param(self, text: str, line_no: int) -> bytes:
        value = text.strip()
        if not (value.startswith("[") and value.endswith("]")):
            raise AssembleError(f"Invalid param operand at line {line_no}: {text}")
        inner = value[1:-1].strip()
        if not inner:
            return b"\x00"
        params = []
        for part in self._split_args(inner):
            piece = part.strip()
            if not piece:
                continue
            if piece.upper() == "END":
                params.append(("END", []))
                continue
            kind, expr_text = piece.split(None, 1)
            if kind.upper() not in {"STR", "NUM"}:
                raise AssembleError(f"Invalid param kind at line {line_no}: {kind}")
            expr_value = expr_text.strip()
            if expr_value.startswith("[") and expr_value.endswith("]"):
                tokens = [token.strip() for token in expr_value[1:-1].split(";") if token.strip()]
                params.append((kind.upper(), tokens))
            else:
                expr_bytes = self._encode_semantic_expr(expr_value, line_no)
                tokens = decode_expr_tokens(expr_bytes, self._wide_enabled(), self._encoding())
                params.append((kind.upper(), tokens))
        try:
            return encode_param_tokens(params, self._wide_enabled(), self._encoding())
        except HxbFormatError as exc:
            raise AssembleError(f"Invalid param block at line {line_no}: {exc}") from exc

    def _wide_enabled(self) -> bool:
        return bool(getattr(self, "_current_wide", 0))

    def _encoding(self) -> str:
        return getattr(self, "_current_encoding", DEFAULT_ENCODING)

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
        for instruction in self.program.instructions:
            if isinstance(instruction, DataBlock):
                body.extend(instruction.data)
            else:
                body.extend(self._encode_instruction(instruction, label_offsets))
        self._update_size(body)
        return crypt_body(body) if self.program.crypted else bytes(body)

    def _layout_labels(self) -> Dict[str, int]:
        index_offsets: List[int] = []
        current = len(self.program.prefix)
        for instruction in self.program.instructions:
            index_offsets.append(current)
            if isinstance(instruction, DataBlock):
                current += len(instruction.data)
            else:
                current += self._instruction_size(instruction)
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
        size = len(instruction.prefix) + 1
        if opcode == 0x2A:
            expr, switch_targets = self._expect_switch_operands(instruction)
            return size + len(expr.value) + 2 + 3 * len(switch_targets)
        expected = list(spec.operands)
        actual = [operand for operand in instruction.operands]
        if len(actual) != len(expected):
            raise AssembleError(
                f"{instruction.mnemonic} at line {instruction.source_line} expects {len(expected)} operands, got {len(actual)}"
            )
        for kind, operand in zip(expected, actual):
            if kind == "expr":
                self._expect_kind(instruction, operand, "expr")
                size += len(operand.value)
            elif kind == "paramblock":
                self._expect_kind(instruction, operand, "param")
                size += len(operand.value)
            elif kind == "target":
                self._expect_kind(instruction, operand, "target")
                size += 3
            elif kind == "u8":
                self._expect_kind(instruction, operand, "u8")
                size += 1
            else:
                raise AssembleError(f"Unhandled operand kind {kind!r} for {instruction.mnemonic}")
        return size

    def _encode_instruction(self, instruction: CodeInstruction, label_offsets: Dict[str, int]) -> bytes:
        opcode = get_opcode_by_mnemonic(instruction.mnemonic)
        if opcode is None:
            raise AssembleError(f"Unknown mnemonic: {instruction.mnemonic}")
        spec = get_opcode_spec(opcode)
        if spec is None:
            raise AssembleError(f"Unknown opcode: 0x{opcode:02X}")
        out = bytearray(instruction.prefix)
        out.append(opcode)
        if opcode == 0x2A:
            expr, switch_targets = self._expect_switch_operands(instruction)
            out.extend(expr.value)
            out.extend(write_u16be(len(switch_targets)))
            for target in switch_targets:
                out.extend(write_u24be(self._resolve_target(target, label_offsets)))
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
            elif kind == "paramblock":
                self._expect_kind(instruction, operand, "param")
                out.extend(operand.value)
            elif kind == "target":
                self._expect_kind(instruction, operand, "target")
                out.extend(write_u24be(self._resolve_target(operand, label_offsets)))
            elif kind == "u8":
                self._expect_kind(instruction, operand, "u8")
                value = int(operand.value)
                if not 0 <= value <= 0xFF:
                    raise AssembleError(f"u8 out of range at line {instruction.source_line}: {value}")
                out.append(value)
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
            raise AssembleError(f"Undefined target at line with operand {text!r}") from exc

    def _update_size(self, data: bytearray) -> None:
        size = len(data)
        if size > 0xFFFFFF:
            raise AssembleError(f"HXB too large for size24: 0x{size:X}")
        data[8] = (size >> 16) & 0xFF
        data[9] = (size >> 8) & 0xFF
        data[10] = size & 0xFF


def default_output_path(input_path: Path) -> Path:
    name = input_path.name
    if name.endswith(".asm.txt"):
        base = name[:-8]
    else:
        base = input_path.stem
    return input_path.with_name(f"{base}.rebuild.hxb")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reassemble HXB semantic asm.txt into binary")
    parser.add_argument("inputs", nargs="+", help="Input asm.txt file(s)")
    parser.add_argument("-o", "--output", help="Output .hxb path; only valid with one input")
    parser.add_argument("--encoding", help="Override encoding metadata")
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
