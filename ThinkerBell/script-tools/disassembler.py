from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from opcodelist import COMMAND_PREFIX, CMP_TOKENS, END_MARKER, TEXT_PREFIX, get_opcode_spec, opcode_mnemonic


class DecodeError(Exception):
    pass


@dataclass
class Operand:
    kind: str
    value: object


@dataclass
class ARecord:
    index: int
    file_offset: int
    payload: bytes


@dataclass
class DecodedRecord:
    record: ARecord
    mnemonic: str
    operands: List[Operand]
    raw_tail: bytes = b""


def read_u32le(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise DecodeError(f"Unexpected EOF while reading u32 at 0x{offset:08X}")
    return int.from_bytes(data[offset:offset + 4], "little")


def is_safe_char(ch: str) -> bool:
    if ch in "{}":
        return False
    code = ord(ch)
    if code < 0x20 or code == 0x7F:
        return False
    # CP932 private-use/control glyphs such as U+F8F2 are engine markers, not
    # readable text.  Keep them as byte placeholders instead of rendering odd
    # characters like "" in asm.txt.
    if 0xE000 <= code <= 0xF8FF:
        return False
    return True


def bytes_to_escaped_text(data: bytes, encoding: str, hide_text_end: bool = False) -> str:
    if hide_text_end and data.endswith(b"\xFE"):
        data = data[:-1]
    text = data.decode(encoding, errors="surrogateescape")
    out: List[str] = []
    for ch in text:
        code = ord(ch)
        if 0xDC80 <= code <= 0xDCFF:
            out.append(f"{{{{{code - 0xDC00:02X}}}}}")
        elif is_safe_char(ch):
            out.append(ch)
        else:
            raw = ch.encode(encoding, errors="surrogateescape")
            out.extend(f"{{{{{b:02X}}}}}" for b in raw)
    return "".join(out)


def quote_bytes(data: bytes, encoding: str, hide_text_end: bool = False) -> str:
    return json.dumps(bytes_to_escaped_text(data, encoding, hide_text_end=hide_text_end), ensure_ascii=False)


def decode_text_payload(payload: bytes) -> bytes:
    if len(payload) < 5:
        raise DecodeError("TEXT record is missing its encrypted length field")
    text_length = read_u32le(payload, 1)
    encrypted = payload[5:]
    if len(encrypted) != text_length:
        raise DecodeError(
            f"TEXT encrypted length mismatch: header={text_length}, actual={len(encrypted)}"
        )
    key = text_length & 0xFF
    return bytes(byte ^ key for byte in encrypted)


def decode_string_operand(payload: bytes, offset: int) -> Tuple[bytes, int]:
    if offset + 5 > len(payload) or payload[offset:offset + 1] != TEXT_PREFIX:
        raise DecodeError(f"Expected encrypted string operand at 0x{offset:08X}")
    text_length = read_u32le(payload, offset + 1)
    end = offset + 5 + text_length
    if end > len(payload):
        raise DecodeError(
            f"Encrypted string operand extends beyond command: offset=0x{offset:08X}, length={text_length}"
        )
    key = text_length & 0xFF
    return bytes(byte ^ key for byte in payload[offset + 5:end]), end


def label_name(index: int) -> str:
    return f"rec_{index:06d}"


class ThinkerBellDisassembler:
    def __init__(
        self,
        data: bytes,
        encoding: str,
        keep_record_len: bool = False,
        text_format: str = "auto",
    ) -> None:
        self.data = data
        self.encoding = encoding
        self.keep_record_len = keep_record_len
        self.text_format = text_format

    def parse_records(self) -> List[ARecord]:
        records: List[ARecord] = []
        pos = 0
        index = 0
        while pos < len(self.data):
            file_offset = pos
            length = read_u32le(self.data, pos)
            pos += 4
            if pos + length > len(self.data):
                raise DecodeError(
                    f"Record {index} payload extends beyond file: offset=0x{file_offset:08X}, length={length}"
                )
            payload = self.data[pos:pos + length]
            pos += length
            records.append(ARecord(index=index, file_offset=file_offset, payload=payload))
            index += 1
        return records

    def decode_record(self, record: ARecord) -> DecodedRecord:
        payload = record.payload
        if not payload:
            return DecodedRecord(record, "EMPTY_RECORD", [])
        if payload.startswith(END_MARKER):
            return DecodedRecord(record, "END_MARKER", [], payload[2:])
        if payload.startswith(COMMAND_PREFIX) and len(payload) >= 7:
            return self.decode_command(record)
        if payload.startswith(b"S"):
            text = payload[1:] if self.text_format == "plain" else decode_text_payload(payload)
            mnemonic = "TEXT" if text.endswith(b"\xFE") else "TEXT_UNTERMINATED"
            return DecodedRecord(record, mnemonic, [Operand("TEXT", text)])
        return DecodedRecord(record, "RAW_RECORD", [Operand("RAW", payload)])

    def decode_command(self, record: ARecord) -> DecodedRecord:
        payload = record.payload
        opcode = read_u32le(payload, 3)
        spec = get_opcode_spec(opcode)
        mnemonic = opcode_mnemonic(opcode)
        if opcode == 60 and payload[7:8] == b"S" and self.text_format == "xor_length":
            return DecodedRecord(record, mnemonic, [Operand("TEXT", decode_text_payload(payload[7:]))])
        if opcode == 60 and self.text_format == "plain":
            return DecodedRecord(record, "NOOP_60_TEXT_PAYLOAD", [], payload[7:])
        operands: List[Operand] = []
        pos = 7
        schema = spec.operands if spec is not None else ()
        for operand_kind in schema:
            if operand_kind == "RAW":
                break
            if operand_kind == "STR":
                try:
                    value, pos = decode_string_operand(payload, pos)
                except DecodeError:
                    break
                operands.append(Operand("STR", value))
                continue
            if operand_kind == "CMP":
                if pos >= len(payload) or payload[pos] not in CMP_TOKENS:
                    break
                operands.append(Operand("CMP", chr(payload[pos])))
                pos += 1
                continue
            parsed = self.read_token(payload, pos, operand_kind)
            if parsed is None:
                break
            token_kind, value, pos = parsed
            operands.append(Operand(token_kind, value))
        raw_tail = payload[pos:]
        return DecodedRecord(record, mnemonic, operands, raw_tail)

    def read_token(self, payload: bytes, pos: int, expected: str) -> Optional[Tuple[str, int, int]]:
        if pos + 5 > len(payload):
            return None
        token = chr(payload[pos])
        if expected == "N/A":
            if token not in {"N", "A"}:
                return None
        elif token != expected:
            return None
        value = int.from_bytes(payload[pos + 1:pos + 5], "little")
        return token, value, pos + 5

    def emit(self) -> str:
        records = self.parse_records()
        if self.text_format == "auto":
            self.text_format = self.detect_text_format(records)
        decoded = [self.decode_record(record) for record in records]
        referenced = self.collect_referenced_records(decoded)
        self.referenced_records = referenced
        lines: List[str] = []
        lines.append("; ThinkerBell .a asm")
        lines.append(f"; encoding: {self.encoding}")
        lines.append(".file kind=ThinkerBell_a")
        lines.append(f".text_format {self.text_format}")
        lines.append("")
        for item in decoded:
            if item.record.index in referenced:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(f"{label_name(item.record.index)}:")
            if self.keep_record_len:
                lines.append(f"    .record_len {len(item.record.payload)}")
            lines.append(self.render_record(item))
        lines.append("")
        return "\n".join(lines)

    def detect_text_format(self, records: List[ARecord]) -> str:
        text_payloads = [record.payload for record in records if record.payload.startswith(b"S")]
        for record in records:
            payload = record.payload
            if (
                payload.startswith(COMMAND_PREFIX)
                and len(payload) >= 8
                and read_u32le(payload, 3) == 60
                and payload[7:8] == b"S"
            ):
                text_payloads.append(payload[7:])
        for payload in text_payloads:
            if len(payload) < 5:
                return "plain"
            if read_u32le(payload, 1) != len(payload) - 5:
                return "plain"
        return "xor_length"

    def collect_referenced_records(self, decoded: List[DecodedRecord]) -> set[int]:
        max_index = len(decoded) - 1
        referenced: set[int] = set()
        for item in decoded:
            spec = get_opcode_spec(read_u32le(item.record.payload, 3)) if item.record.payload.startswith(COMMAND_PREFIX) and len(item.record.payload) >= 7 else None
            if spec is None:
                continue
            for operand_index in spec.target_operands:
                if operand_index >= len(item.operands):
                    continue
                operand = item.operands[operand_index]
                if operand.kind in {"N", "A"} and isinstance(operand.value, int) and 0 <= operand.value <= max_index:
                    referenced.add(operand.value)
        return referenced

    def render_record(self, item: DecodedRecord) -> str:
        mnemonic = item.mnemonic
        if mnemonic == "EMPTY_RECORD":
            return "    EMPTY_RECORD"
        if mnemonic == "END_MARKER":
            if item.raw_tail:
                return f"    GRAPHICGROUP_END {self.render_raw_payload(item.raw_tail)}"
            return "    GRAPHICGROUP_END"
        if mnemonic in {"TEXT", "TEXT_UNTERMINATED"}:
            return (
                f"    {mnemonic} "
                f"{quote_bytes(item.operands[0].value, self.encoding, hide_text_end=mnemonic == 'TEXT')}"
            )
        if mnemonic == "STATE_TEXT_SET":
            return f"    {mnemonic} {quote_bytes(item.operands[0].value, self.encoding)}"
        if mnemonic == "RAW_RECORD":
            return f"    RAW_RECORD {self.render_raw_payload(item.operands[0].value)}"

        parts = [mnemonic]
        spec = get_opcode_spec(read_u32le(item.record.payload, 3)) if item.record.payload.startswith(COMMAND_PREFIX) and len(item.record.payload) >= 7 else None
        rendered_operands = [
            self.render_operand(op, as_target=spec is not None and idx in spec.target_operands)
            for idx, op in enumerate(item.operands)
        ]
        if item.raw_tail:
            rendered_operands.append(f"RAW({self.render_raw_payload(item.raw_tail)})")
        if rendered_operands:
            parts.append(", ".join(rendered_operands))
        return "    " + " ".join(parts)

    def render_raw_payload(self, payload: bytes) -> str:
        tokens: List[str] = []
        pos = 0
        while pos < len(payload):
            byte = payload[pos]
            if byte in (ord("N"), ord("A")) and pos + 5 <= len(payload):
                value = int.from_bytes(payload[pos + 1:pos + 5], "little")
                tokens.append(f"{chr(byte)}({value})")
                pos += 5
                continue
            start = pos
            pos += 1
            while pos < len(payload):
                next_byte = payload[pos]
                if next_byte in (ord("N"), ord("A")) and pos + 5 <= len(payload):
                    break
                pos += 1
            raw = payload[start:pos]
            tokens.append(f"BYTES({quote_bytes(raw, self.encoding)})")
        return ", ".join(tokens)

    def render_operand(self, operand: Operand, as_target: bool = False) -> str:
        if operand.kind in {"N", "A"}:
            value = (
                label_name(int(operand.value))
                if as_target
                and isinstance(operand.value, int)
                and hasattr(self, "referenced_records")
                and operand.value in self.referenced_records
                else operand.value
            )
            return f"{operand.kind}({value})"
        if operand.kind == "CMP":
            return f"CMP({json.dumps(operand.value)})"
        if operand.kind == "TEXT":
            return quote_bytes(operand.value, self.encoding)
        if operand.kind == "STR":
            return f"STR({quote_bytes(operand.value, self.encoding)})"
        raise DecodeError(f"Unsupported operand kind: {operand.kind}")


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}.asm.txt")


def output_path_for(input_path: Path, output_arg: Optional[str], multi_input: bool) -> Path:
    if output_arg is None:
        return default_output_path(input_path)
    output_path = Path(output_arg)
    if multi_input:
        if output_path.suffix:
            raise DecodeError("-o/--output must be a directory when disassembling multiple input files")
        return output_path / f"{input_path.stem}.asm.txt"
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Disassemble ThinkerBell .a bytecode into semantic asm.txt")
    parser.add_argument("inputs", nargs="+", help="Input .a script(s)")
    parser.add_argument("-o", "--output", help="Output asm path, or output directory for multiple inputs")
    parser.add_argument("--encoding", default="cp932", help="Encoding after decrypting S records (default: cp932)")
    parser.add_argument(
        "--text-format",
        choices=("auto", "plain", "xor_length"),
        default="auto",
        help="TEXT storage format (default: auto-detect)",
    )
    parser.add_argument("--keep-record-len", action="store_true", help="Emit .record_len checks for strict debugging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_paths = [Path(value) for value in args.inputs]
    multi_input = len(input_paths) > 1
    for input_path in input_paths:
        output_path = output_path_for(input_path, args.output, multi_input)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        asm = ThinkerBellDisassembler(
            input_path.read_bytes(),
            args.encoding,
            keep_record_len=args.keep_record_len,
            text_format=args.text_format,
        ).emit()
        output_path.write_text(asm, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
