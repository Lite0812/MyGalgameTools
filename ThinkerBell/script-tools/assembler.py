from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from opcodelist import COMMAND_PREFIX, END_MARKER, opcode_by_mnemonic


class AssembleError(Exception):
    pass


@dataclass
class AsmRecord:
    label: str
    expected_len: Optional[int]
    mnemonic: str
    operands: List[str]
    source_line: int


class AsmParser:
    def __init__(self, path: Path, encoding: Optional[str], text_format: Optional[str] = None) -> None:
        self.path = path
        self.lines = path.read_text(encoding="utf-8").splitlines()
        self.encoding = encoding
        self.text_format = text_format

    def parse(self) -> Tuple[str, str, List[AsmRecord]]:
        records: List[AsmRecord] = []
        current_labels: List[str] = []
        current_len: Optional[int] = None
        detected_encoding = self.encoding
        detected_text_format = self.text_format

        for line_no, raw_line in enumerate(self.lines, 1):
            if raw_line.startswith("; encoding:") and detected_encoding is None:
                detected_encoding = raw_line.split(":", 1)[1].strip()
                continue
            line = self.strip_comment(raw_line).strip()
            if not line:
                continue
            if line.startswith(".file"):
                if line not in {".file kind=ThinkerBell_a", ".file kind=hidamari_a"}:
                    raise AssembleError(f"Unsupported .file directive at line {line_no}: {line}")
                continue
            if line.startswith(".text_format"):
                parts = line.split(None, 1)
                if len(parts) != 2 or parts[1] not in {"plain", "xor_length"}:
                    raise AssembleError(f"Invalid .text_format at line {line_no}: {line}")
                if detected_text_format is None:
                    detected_text_format = parts[1]
                continue
            if line.endswith(":"):
                label = line[:-1].strip()
                if not label:
                    raise AssembleError(f"Empty label at line {line_no}")
                current_labels.append(label)
                continue
            if line.startswith(".record_len"):
                parts = line.split(None, 1)
                if len(parts) != 2:
                    raise AssembleError(f"Missing .record_len value at line {line_no}")
                current_len = self.parse_int(parts[1], line_no)
                continue
            mnemonic, operands = self.parse_instruction(line)
            label = current_labels[-1] if current_labels else f"__record_{len(records):06d}"
            records.append(AsmRecord(label, current_len, mnemonic, operands, line_no))
            current_labels = []
            current_len = None

        if current_labels:
            raise AssembleError(f"Dangling label(s) without record: {', '.join(current_labels)}")
        return detected_encoding or "cp932", detected_text_format or "xor_length", records

    def strip_comment(self, line: str) -> str:
        in_string = False
        escaped = False
        for idx, ch in enumerate(line):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == ";":
                return line[:idx]
        return line

    def parse_instruction(self, line: str) -> Tuple[str, List[str]]:
        parts = line.split(None, 1)
        mnemonic = parts[0].upper()
        if len(parts) == 1:
            return mnemonic, []
        return mnemonic, self.split_args(parts[1].strip())

    def split_args(self, text: str) -> List[str]:
        args: List[str] = []
        current: List[str] = []
        in_string = False
        escaped = False
        paren_depth = 0
        for ch in text:
            if in_string:
                current.append(ch)
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                current.append(ch)
            elif ch == "(":
                paren_depth += 1
                current.append(ch)
            elif ch == ")":
                if paren_depth > 0:
                    paren_depth -= 1
                current.append(ch)
            elif ch == "," and paren_depth == 0:
                args.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        tail = "".join(current).strip()
        if tail:
            args.append(tail)
        return args

    def parse_int(self, text: str, line_no: int) -> int:
        try:
            return int(text.strip(), 0)
        except ValueError as exc:
            raise AssembleError(f"Invalid integer at line {line_no}: {text}") from exc


class Assembler:
    def __init__(self, records: List[AsmRecord], encoding: str, text_format: str = "xor_length") -> None:
        self.records = records
        self.encoding = encoding
        self.text_format = text_format
        self.label_to_index = {}
        real_index = 0
        for record in records:
            self.label_to_index[record.label] = real_index
            real_index += 1

    def assemble(self) -> bytes:
        out = bytearray()
        for index, record in enumerate(self.records):
            payload = self.encode_record(record)
            if len(payload) > 0x400:
                raise AssembleError(
                    f"Record {record.label} at line {record.source_line} exceeds 0x400-byte VM buffer: {len(payload)}"
                )
            if record.expected_len is not None and len(payload) != record.expected_len:
                raise AssembleError(
                    f"Record {record.label} at line {record.source_line} length mismatch: "
                    f"expected {record.expected_len}, got {len(payload)}"
                )
            out.extend(len(payload).to_bytes(4, "little"))
            out.extend(payload)
        return bytes(out)

    def encode_record(self, record: AsmRecord) -> bytes:
        mnemonic = record.mnemonic
        if mnemonic == "EMPTY_RECORD":
            self.require_operand_count(record, 0)
            return b""
        if mnemonic in {"END_MARKER", "GRAPHICGROUP_END"}:
            if mnemonic == "END_MARKER":
                self.require_operand_count(record, 0)
                return END_MARKER
            return END_MARKER + self.encode_raw_operands(record.operands, record.source_line)
        if mnemonic in {"TEXT", "TEXT_UNTERMINATED"}:
            self.require_operand_count(record, 1)
            text_bytes = self.parse_quoted_bytes(record.operands[0], record.source_line)
            if mnemonic == "TEXT" and not text_bytes.endswith(b"\xFE"):
                text_bytes += b"\xFE"
            if self.text_format == "plain":
                return b"S" + text_bytes
            text_length = len(text_bytes)
            key = text_length & 0xFF
            encrypted = bytes(byte ^ key for byte in text_bytes)
            return b"S" + text_length.to_bytes(4, "little") + encrypted
        if mnemonic == "RAW_RECORD":
            return self.encode_raw_operands(record.operands, record.source_line)
        if mnemonic == "STATE_TEXT_SET":
            self.require_operand_count(record, 1)
            text_bytes = self.parse_quoted_bytes(record.operands[0], record.source_line)
            text_length = len(text_bytes)
            key = text_length & 0xFF
            encrypted = bytes(byte ^ key for byte in text_bytes)
            return b"M#N" + (60).to_bytes(4, "little") + b"S" + text_length.to_bytes(4, "little") + encrypted

        opcode = opcode_by_mnemonic(mnemonic)
        if opcode is None or opcode == 9999:
            raise AssembleError(f"Unknown mnemonic at line {record.source_line}: {mnemonic}")
        out = bytearray(COMMAND_PREFIX)
        out.extend(opcode.to_bytes(4, "little"))
        for operand in record.operands:
            if operand.startswith("RAW(") and operand.endswith(")"):
                out.extend(self.encode_raw_operands(self.split_raw_operands(operand[4:-1].strip()), record.source_line))
            elif operand.startswith("BYTES(") and operand.endswith(")"):
                out.extend(self.parse_quoted_bytes(operand[6:-1].strip(), record.source_line))
            elif operand.startswith("CMP(") and operand.endswith(")"):
                value = json.loads(operand[4:-1].strip())
                if not isinstance(value, str) or len(value) != 1:
                    raise AssembleError(f"Invalid CMP operand at line {record.source_line}: {operand}")
                out.extend(value.encode("ascii"))
            elif operand.startswith("STR(") and operand.endswith(")"):
                text_bytes = self.parse_quoted_bytes(operand[4:-1].strip(), record.source_line)
                text_length = len(text_bytes)
                key = text_length & 0xFF
                encrypted = bytes(byte ^ key for byte in text_bytes)
                out.extend(b"S" + text_length.to_bytes(4, "little") + encrypted)
            elif operand.startswith("N(") and operand.endswith(")"):
                out.extend(self.encode_token("N", operand[2:-1].strip(), record.source_line))
            elif operand.startswith("A(") and operand.endswith(")"):
                out.extend(self.encode_token("A", operand[2:-1].strip(), record.source_line))
            else:
                raise AssembleError(f"Invalid operand at line {record.source_line}: {operand}")
        return bytes(out)

    def encode_raw_operands(self, operands: List[str], line_no: int) -> bytes:
        out = bytearray()
        for operand in operands:
            if operand.startswith("BYTES(") and operand.endswith(")"):
                out.extend(self.parse_quoted_bytes(operand[6:-1].strip(), line_no))
            elif operand.startswith("N(") and operand.endswith(")"):
                out.extend(self.encode_token("N", operand[2:-1].strip(), line_no))
            elif operand.startswith("A(") and operand.endswith(")"):
                out.extend(self.encode_token("A", operand[2:-1].strip(), line_no))
            else:
                raise AssembleError(f"Invalid raw operand at line {line_no}: {operand}")
        return bytes(out)

    def split_raw_operands(self, text: str) -> List[str]:
        if not text:
            return []
        args: List[str] = []
        current: List[str] = []
        in_string = False
        escaped = False
        paren_depth = 0
        for ch in text:
            if in_string:
                current.append(ch)
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                current.append(ch)
            elif ch == "(":
                paren_depth += 1
                current.append(ch)
            elif ch == ")":
                if paren_depth > 0:
                    paren_depth -= 1
                current.append(ch)
            elif ch == "," and paren_depth == 0:
                args.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        tail = "".join(current).strip()
        if tail:
            args.append(tail)
        return args

    def require_operand_count(self, record: AsmRecord, count: int) -> None:
        if len(record.operands) != count:
            raise AssembleError(
                f"{record.mnemonic} at line {record.source_line} requires {count} operands, got {len(record.operands)}"
            )

    def encode_token(self, token: str, text: str, line_no: int) -> bytes:
        if text in self.label_to_index:
            value = self.label_to_index[text]
        else:
            try:
                value = int(text, 0)
            except ValueError as exc:
                raise AssembleError(f"Invalid {token} value at line {line_no}: {text}") from exc
        if not 0 <= value <= 0xFFFFFFFF:
            raise AssembleError(f"{token} value out of range at line {line_no}: {value}")
        return token.encode("ascii") + value.to_bytes(4, "little")

    def parse_quoted_bytes(self, text: str, line_no: int) -> bytes:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AssembleError(f"Invalid quoted string at line {line_no}: {exc}") from exc
        if not isinstance(value, str):
            raise AssembleError(f"Expected quoted string at line {line_no}: {text}")
        if "\\x" in text or "\\X" in text:
            raise AssembleError(f"\\x escapes are not allowed at line {line_no}")
        return self.escaped_text_to_bytes(value, line_no)

    def escaped_text_to_bytes(self, value: str, line_no: int) -> bytes:
        out = bytearray()
        buf: List[str] = []
        i = 0
        while i < len(value):
            if value.startswith("{{", i):
                end = value.find("}}", i + 2)
                if end < 0:
                    raise AssembleError(f"Unterminated byte placeholder at line {line_no}")
                if buf:
                    out.extend("".join(buf).encode(self.encoding))
                    buf = []
                body = value[i + 2:end]
                parts = body.split(":")
                if not parts or any(len(part) != 2 for part in parts):
                    raise AssembleError(f"Invalid byte placeholder at line {line_no}: {{{{{body}}}}}")
                try:
                    out.extend(int(part, 16) for part in parts)
                except ValueError as exc:
                    raise AssembleError(f"Invalid byte placeholder at line {line_no}: {{{{{body}}}}}") from exc
                i = end + 2
            else:
                buf.append(value[i])
                i += 1
        if buf:
            out.extend("".join(buf).encode(self.encoding))
        return bytes(out)


def default_output_path(input_path: Path) -> Path:
    if input_path.name.endswith(".asm.txt"):
        return input_path.with_name(input_path.name[:-8] + ".rebuild")
    return input_path.with_name(input_path.stem + ".rebuild")


def output_path_for(input_path: Path, output_arg: Optional[str], multi_input: bool) -> Path:
    if output_arg is None:
        return default_output_path(input_path)
    output_path = Path(output_arg)
    if multi_input:
        if output_path.suffix:
            raise AssembleError("-o/--output must be a directory when assembling multiple input files")
        return output_path / default_output_path(input_path).name
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reassemble semantic ThinkerBell .a asm.txt into binary")
    parser.add_argument("inputs", nargs="+", help="Input asm.txt path(s)")
    parser.add_argument("-o", "--output", help="Output .a path, or output directory for multiple inputs")
    parser.add_argument("--encoding", help="Text encoding; defaults to asm header or cp932")
    parser.add_argument(
        "--text-format",
        choices=("plain", "xor_length"),
        help="Override TEXT storage format; use plain for asm made by the old backup tool",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_paths = [Path(value) for value in args.inputs]
    multi_input = len(input_paths) > 1
    for input_path in input_paths:
        output_path = output_path_for(input_path, args.output, multi_input)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        encoding, text_format, records = AsmParser(input_path, args.encoding, args.text_format).parse()
        output_path.write_bytes(Assembler(records, encoding, text_format).assemble())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
