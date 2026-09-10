#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lambda MBT0/SAM text extract/inject tool.

Workflow:
  python lambda_text_tool.py extract _work\\script_mbt0 _work\\texts
  python lambda_text_tool.py inject _work\\script_mbt0 _work\\texts _work\\script_injected

Translation JSON format follows the project memo:
  [{"id": 0, "name": "...", "pre_jp": "...", "message": "..."}]
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from lambda_mbt0_opcode import (
    DEFAULT_ENCODING,
    MAGIC_MBT0,
    MAGIC_SAM,
    MBT0_HEADER_SIZE,
    MBT0_SPECIAL_TAIL_PTRS,
    SAM_RECORD_OFFSET,
    SAM_RECORD_SIZE,
)


class TextToolError(Exception):
    pass


CONTROL_CHARS = set("/\\_^[]=<>#$%&*@{}|")
JSON_INDENT = 2
VOICE_PREFIX_RE = re.compile(r"^(?P<prefix>\[[^\]]+\])(?P<body>.*)$", re.DOTALL)


@dataclass
class TextEntry:
    id: int
    source: str
    kind: str
    offset: int
    raw_len: int
    text_raw: str
    message: str
    name: str | None = None
    prefix: str = ""
    suffix: str = ""
    pointer_index: int | None = None
    record_index: int | None = None
    record_offset: int | None = None
    slot_size: int | None = None
    lead_ctrl: str = ""
    trail_ctrl: str = ""
    middle_ctrl: list[str] | None = None


def u32(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def p32(data: bytearray, off: int, value: int) -> None:
    struct.pack_into("<I", data, off, value)


def read_zstr(data: bytes, off: int) -> bytes:
    end = data.find(b"\0", off)
    if end < 0:
        raise TextToolError(f"unterminated zstr at 0x{off:X}")
    return data[off:end]


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=JSON_INDENT), encoding="utf-8", newline="\n")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def is_japanese_visible(text: str) -> bool:
    for ch in text:
        if "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff":
            return True
        if ch in "。、！？ー「」『』（）［］【】・：；＿　":
            return True
    return False


def is_noise_token(text: str) -> bool:
    if not text:
        return True
    if text.startswith("#"):
        return True
    if re.fullmatch(r"[A-Za-z0-9_./:\\-]+", text):
        return True
    if len(text) <= 1 and not is_japanese_visible(text):
        return True
    return False


def split_edge_controls(text: str) -> tuple[str, str, str]:
    def is_ctrl(ch: str) -> bool:
        o = ord(ch)
        return o < 0x20 or ch in CONTROL_CHARS

    start = 0
    while start < len(text) and is_ctrl(text[start]):
        start += 1
    end = len(text)
    while end > start and is_ctrl(text[end - 1]):
        end -= 1
    return text[:start], text[start:end], text[end:]


def middle_controls(text: str) -> list[str]:
    found = []
    for i, ch in enumerate(text):
        if ord(ch) < 0x20 or ch in CONTROL_CHARS:
            found.append(f"pos={i} char={repr(ch)}")
    return found


def normalize_b31(text: str) -> str:
    return text.replace("\r\n", "").replace("\n", "").replace("\r", "")


DIALOG_RE = re.compile(r"^(?P<prefix>(?:\[[^\]]+\])?)(?P<name>[^「」\r\n]{1,40})「(?P<msg>.*)」$", re.DOTALL)


def classify_mes_text(text: str) -> tuple[str, str | None, str, str, str]:
    text = normalize_b31(text)
    voice_prefix = ""
    vm = VOICE_PREFIX_RE.match(text)
    if vm:
        voice_prefix = vm.group("prefix")
        text = vm.group("body")
    m = DIALOG_RE.match(text)
    if m:
        prefix = voice_prefix + (m.group("prefix") or "")
        return "dialog", m.group("name"), m.group("msg"), prefix, "」"
    return "narration", None, text, voice_prefix, ""


def make_entry(
    entry_id: int,
    source: str,
    kind: str,
    raw_text: str,
    message: str,
    offset: int,
    raw_len: int,
    name: str | None = None,
    prefix: str = "",
    suffix: str = "",
    pointer_index: int | None = None,
    record_index: int | None = None,
    record_offset: int | None = None,
    slot_size: int | None = None,
) -> TextEntry:
    lead, core, trail = split_edge_controls(message)
    mids = middle_controls(core)
    return TextEntry(
        id=entry_id,
        source=source,
        kind=kind,
        offset=offset,
        raw_len=raw_len,
        text_raw=raw_text,
        message=core,
        name=name,
        prefix=prefix,
        suffix=suffix,
        pointer_index=pointer_index,
        record_index=record_index,
        record_offset=record_offset,
        slot_size=slot_size,
        lead_ctrl=lead,
        trail_ctrl=trail,
        middle_ctrl=mids,
    )


def entry_to_public(e: TextEntry) -> dict:
    obj = {"id": e.id}
    if e.name is not None:
        obj["name"] = e.name
    obj["pre_jp"] = e.message
    obj["message"] = e.message
    return obj


def entry_to_meta(e: TextEntry) -> dict:
    obj = {
        "id": e.id,
        "source": e.source,
        "kind": e.kind,
        "offset": e.offset,
        "raw_len": e.raw_len,
        "text_raw": e.text_raw,
        "prefix": e.prefix,
        "suffix": e.suffix,
        "lead_ctrl": e.lead_ctrl,
        "trail_ctrl": e.trail_ctrl,
        "middle_ctrl": e.middle_ctrl or [],
    }
    if e.name is not None:
        obj["name"] = e.name
    if e.pointer_index is not None:
        obj["pointer_index"] = e.pointer_index
    if e.record_index is not None:
        obj["record_index"] = e.record_index
    if e.record_offset is not None:
        obj["record_offset"] = e.record_offset
    if e.slot_size is not None:
        obj["slot_size"] = e.slot_size
    return obj


def parse_mbt0_entries(path: Path, encoding: str) -> list[TextEntry]:
    data = path.read_bytes()
    if data[:4] != MAGIC_MBT0:
        raise TextToolError(f"not MBT0: {path}")
    count = u32(data, 0x08)
    normal_count = max(0, count - MBT0_SPECIAL_TAIL_PTRS)
    entries: list[TextEntry] = []
    for i in range(normal_count):
        ptr = u32(data, MBT0_HEADER_SIZE + i * 4)
        if ptr <= 0 or ptr >= len(data):
            continue
        raw = read_zstr(data, ptr)
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if is_noise_token(text):
            continue
        if not is_japanese_visible(text):
            continue
        kind, name, msg, prefix, suffix = classify_mes_text(text)
        if not msg:
            continue
        entries.append(
            make_entry(
                len(entries),
                path.name,
                kind,
                text,
                msg,
                ptr,
                len(raw),
                name=name,
                prefix=prefix,
                suffix=suffix,
                pointer_index=i,
            )
        )
    return entries


def iter_sam_zstr_slots(data: bytes, encoding: str):
    record_count = u32(data, 0x10)
    for rec_index in range(record_count):
        rec_start = SAM_RECORD_OFFSET + rec_index * SAM_RECORD_SIZE
        rec_end = rec_start + SAM_RECORD_SIZE
        if rec_end > len(data):
            break
        pos = rec_start
        while pos < rec_end:
            if data[pos] == 0:
                pos += 1
                continue
            end = data.find(b"\0", pos, rec_end)
            if end < 0:
                break
            raw = data[pos:end]
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                pos = end + 1
                continue
            slot_end = end + 1
            while slot_end < rec_end and data[slot_end] == 0:
                slot_end += 1
            yield rec_index, pos - rec_start, pos, slot_end - pos, raw, text
            pos = slot_end


def parse_sam_entries(path: Path, encoding: str) -> list[TextEntry]:
    data = path.read_bytes()
    if data[:4] != MAGIC_SAM:
        raise TextToolError(f"not SAM: {path}")
    entries: list[TextEntry] = []
    for rec_index, rec_off, abs_off, slot_size, raw, text in iter_sam_zstr_slots(data, encoding):
        if is_noise_token(text):
            continue
        if not is_japanese_visible(text):
            continue
        entries.append(
            make_entry(
                len(entries),
                path.name,
                "system",
                text,
                normalize_b31(text),
                abs_off,
                len(raw),
                record_index=rec_index,
                record_offset=rec_off,
                slot_size=slot_size,
            )
        )
    return entries


def parse_entries(path: Path, encoding: str) -> list[TextEntry]:
    data = path.read_bytes()[:4]
    if data == MAGIC_MBT0:
        return parse_mbt0_entries(path, encoding)
    if data == MAGIC_SAM:
        return parse_sam_entries(path, encoding)
    raise TextToolError(f"unknown format: {path}")


def collect_input_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.is_file() and not p.name.endswith((".asm.txt", ".rebuild")))
    return [path]


def cmd_extract(args: argparse.Namespace) -> int:
    src = Path(args.input)
    out_dir = Path(args.out_dir)
    all_warnings = []
    total = 0
    for path in collect_input_files(src):
        try:
            entries = parse_entries(path, args.encoding)
        except TextToolError as e:
            print(f"[SKIP] {path}: {e}")
            continue
        if not entries:
            print(f"[SKIP] {path}: 0 text")
            continue
        public = [entry_to_public(e) for e in entries]
        meta = {
            "file": path.name,
            "encoding": args.encoding,
            "scheme": "B3.1",
            "note": "Internal CR/LF removed for extraction; edge controls stored in meta.",
            "entries": [entry_to_meta(e) for e in entries],
        }
        write_json(out_dir / f"{path.name}.json", public)
        write_json(out_dir / f"{path.name}.json.meta.json", meta)
        warnings = [e for e in entries if e.middle_ctrl]
        total += len(entries)
        print(f"[OK] {path.name}: {len(entries)} entries -> {out_dir / (path.name + '.json')}")
        if warnings:
            print(f"[WARN] {path.name}: {len(warnings)} entries contain middle control chars; see meta middle_ctrl")
            all_warnings.extend((path.name, e.id, e.middle_ctrl) for e in warnings)
    if all_warnings:
        warn_path = out_dir / "_control_warnings.txt"
        with warn_path.open("w", encoding="utf-8", newline="\n") as f:
            for file_name, entry_id, mids in all_warnings:
                f.write(f"{file_name}\tid={entry_id}\t{'; '.join(mids)}\n")
        print(f"[WARN] wrote {warn_path}")
    print(f"[DONE] total entries: {total}")
    return 0


def check_cp932(text: str, encoding: str, context: str) -> bytes:
    try:
        return text.encode(encoding)
    except UnicodeEncodeError as e:
        raise TextToolError(f"{context}: cannot encode {e.object[e.start:e.end]!r} at char {e.start}") from e


def build_mbt0_text(meta_entry: dict, trans_entry: dict) -> str:
    if (
        trans_entry.get("message", trans_entry.get("pre_jp", "")) == trans_entry.get("pre_jp", "")
        and trans_entry.get("name", meta_entry.get("name")) == meta_entry.get("name")
    ):
        return meta_entry.get("text_raw", "")
    name = trans_entry.get("name", meta_entry.get("name"))
    msg = trans_entry.get("message", trans_entry.get("pre_jp", ""))
    msg = meta_entry.get("lead_ctrl", "") + normalize_b31(msg) + meta_entry.get("trail_ctrl", "")
    if meta_entry["kind"] == "dialog" and name:
        return f"{meta_entry.get('prefix', '')}{name}「{msg}」"
    return f"{meta_entry.get('prefix', '')}{msg}"


def inject_mbt0(orig: Path, trans: list[dict], meta: dict, out: Path, encoding: str) -> None:
    data = bytearray(orig.read_bytes())
    count = u32(data, 0x08)
    ptrs = [u32(data, MBT0_HEADER_SIZE + i * 4) for i in range(count)]
    normal_count = max(0, count - MBT0_SPECIAL_TAIL_PTRS)
    meta_by_pid = {e["pointer_index"]: e for e in meta["entries"] if "pointer_index" in e}
    trans_by_id = {e["id"]: e for e in trans}
    first_body = min(p for p in ptrs[:normal_count] if p > 0)
    new_data = bytearray(data[:first_body])
    new_ptrs = ptrs[:]

    for i in range(normal_count):
        old_ptr = ptrs[i]
        if i in meta_by_pid:
            m = meta_by_pid[i]
            t = trans_by_id.get(m["id"])
            if t is None:
                raise TextToolError(f"missing translation id={m['id']}")
            new_ptrs[i] = len(new_data)
            text = build_mbt0_text(m, t)
            raw = check_cp932(text, encoding, f"{orig.name} id={m['id']}")
            new_data.extend(raw)
            new_data.append(0)
        else:
            raw = read_zstr(data, old_ptr)
            new_ptrs[i] = len(new_data)
            new_data.extend(raw)
            new_data.append(0)
        while len(new_data) % 4:
            new_data.append(0)

    # Preserve special tail pointed strings, remapping pointers when they target copied data.
    for i in range(normal_count, count):
        old_ptr = ptrs[i]
        if 0 <= old_ptr < len(data):
            raw = read_zstr(data, old_ptr)
            if old_ptr == 0:
                new_ptrs[i] = 0
            else:
                new_ptrs[i] = len(new_data)
                new_data.extend(raw)
                new_data.append(0)
                while len(new_data) % 4:
                    new_data.append(0)
        else:
            new_ptrs[i] = old_ptr

    p32(new_data, 0x08, count)
    for i, ptr in enumerate(new_ptrs):
        p32(new_data, MBT0_HEADER_SIZE + i * 4, ptr)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(new_data)


def inject_sam(orig: Path, trans: list[dict], meta: dict, out: Path, encoding: str) -> None:
    data = bytearray(orig.read_bytes())
    trans_by_id = {e["id"]: e for e in trans}
    for m in meta["entries"]:
        t = trans_by_id.get(m["id"])
        if t is None:
            raise TextToolError(f"missing translation id={m['id']}")
        if t.get("message", t.get("pre_jp", "")) == t.get("pre_jp", ""):
            msg = m.get("text_raw", "")
        else:
            msg = m.get("lead_ctrl", "") + normalize_b31(t.get("message", t.get("pre_jp", ""))) + m.get("trail_ctrl", "")
        raw = check_cp932(msg, encoding, f"{orig.name} id={m['id']}")
        slot_size = int(m["slot_size"])
        if len(raw) + 1 > slot_size:
            raise TextToolError(
                f"{orig.name} id={m['id']}: encoded text too long for fixed SAM slot "
                f"({len(raw)+1}>{slot_size})"
            )
        off = int(m["offset"])
        data[off : off + slot_size] = raw + b"\0" * (slot_size - len(raw))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)


def cmd_inject(args: argparse.Namespace) -> int:
    orig_path = Path(args.original)
    text_dir = Path(args.text_dir)
    out_dir = Path(args.out_dir)
    files = collect_input_files(orig_path)
    done = 0
    for orig in files:
        json_path = text_dir / f"{orig.name}.json"
        meta_path = text_dir / f"{orig.name}.json.meta.json"
        if not json_path.exists() or not meta_path.exists():
            print(f"[SKIP] missing json/meta for {orig.name}")
            continue
        trans = load_json(json_path)
        meta = load_json(meta_path)
        out = out_dir / orig.name
        magic = orig.read_bytes()[:4]
        if magic == MAGIC_MBT0:
            inject_mbt0(orig, trans, meta, out, args.encoding or meta.get("encoding", DEFAULT_ENCODING))
        elif magic == MAGIC_SAM:
            inject_sam(orig, trans, meta, out, args.encoding or meta.get("encoding", DEFAULT_ENCODING))
        else:
            print(f"[SKIP] unknown format: {orig}")
            continue
        done += 1
        print(f"[OK] injected {orig.name} -> {out}")
    print(f"[DONE] files: {done}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lambda MBT0/SAM text extract/inject")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract", help="extract translatable text to JSON")
    p.add_argument("input", help="input DAT file or folder")
    p.add_argument("out_dir")
    p.add_argument("--encoding", default=DEFAULT_ENCODING)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("inject", help="inject translated JSON back to DAT")
    p.add_argument("original", help="original DAT file or folder")
    p.add_argument("text_dir", help="folder containing *.json and *.json.meta.json")
    p.add_argument("out_dir")
    p.add_argument("--encoding")
    p.set_defaults(func=cmd_inject)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except TextToolError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
