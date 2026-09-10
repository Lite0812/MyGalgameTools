#!/usr/bin/env python3
"""Unpack and repack SHS6/SHS7 HXP archives.

Unpack writes editable files under entries/ and a minimal manifest.json that
only records the archive type. Pack rebuilds a game-readable archive using raw
uncompressed blocks (packed_size == 0), so it does not need the original binary
or per-file compression metadata.
"""

from __future__ import annotations

import argparse
import ctypes
import functools
import hashlib
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any


MANIFEST_NAME = "manifest.json"
ENTRY_DIR = "entries"
LCID_JAPANESE = 0x0411
SORT_STRINGSORT = 0x1000
AUTO_EXTENSIONS = {".bmp", ".tga"}

if sys.platform == "win32":
    _COMPARE_STRING_W = ctypes.windll.kernel32.CompareStringW
    _COMPARE_STRING_W.argtypes = [
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    _COMPARE_STRING_W.restype = ctypes.c_int
else:
    _COMPARE_STRING_W = None


class ShsError(RuntimeError):
    pass


def u32le(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def p32le(value: int) -> bytes:
    return struct.pack("<I", value)


def u16be(data: bytes, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def u32be(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def p16be(value: int) -> bytes:
    return struct.pack(">H", value)


def p32be(value: int) -> bytes:
    return struct.pack(">I", value)


def read_exact_block(archive: bytes, off: int) -> tuple[dict[str, Any], bytes, int]:
    if off < 0 or off + 8 > len(archive):
        raise ShsError(f"bad block offset 0x{off:x}")
    packed_size = u32le(archive, off)
    unpacked_size = u32le(archive, off + 4)
    if packed_size:
        end = off + 8 + packed_size
        if end > len(archive):
            raise ShsError(f"compressed block at 0x{off:x} extends past EOF")
        payload, _ = decode_compressed(archive[off + 8 : end], unpacked_size)
        block = {
            "packed": True,
            "unpacked_size": unpacked_size,
            "packed_size": packed_size,
        }
        return block, payload, end

    end = off + 8 + unpacked_size
    if end > len(archive):
        raise ShsError(f"raw block at 0x{off:x} extends past EOF")
    block = {"packed": False, "unpacked_size": unpacked_size}
    return block, archive[off + 8 : end], end


def decode_compressed(
    src: bytes, out_size: int, collect_tokens: bool = False
) -> tuple[bytes, list[list[Any]]]:
    out = bytearray()
    tokens: list[list[Any]] = []
    pos = 0

    def need(n: int) -> None:
        if pos + n > len(src):
            raise ShsError("compressed stream is truncated")

    while len(out) < out_size:
        need(1)
        code = src[pos]
        pos += 1

        if code < 0x20:
            if code < 0x1D:
                length = code + 1
                kind = "l1"
            elif code == 0x1D:
                need(1)
                length = src[pos] + 30
                pos += 1
                kind = "l2"
            elif code == 0x1E:
                need(2)
                length = u16be(src, pos) + 286
                pos += 2
                kind = "l3"
            else:
                need(4)
                length = u32be(src, pos)
                pos += 4
                kind = "l4"

            if length > out_size - len(out):
                raise ShsError("literal token overruns decompressed size")
            need(length)
            out.extend(src[pos : pos + length])
            pos += length
            if collect_tokens:
                tokens.append([kind, length])
            continue

        if code & 0x80:
            need(1)
            dist = (((code & 0x1F) << 8) | src[pos]) + 1
            pos += 1
            length = ((code >> 5) & 3) + 3
            kind = "m1"
        elif (code & 0x60) == 0x20:
            dist = ((code >> 2) & 7) + 1
            length = (code & 3) + 3
            kind = "m2"
        elif (code & 0x60) == 0x40:
            need(1)
            dist = src[pos] + 1
            pos += 1
            length = (code & 0x1F) + 7
            kind = "m3"
        else:
            need(2)
            dist = (((code & 0x1F) << 8) | src[pos]) + 1
            pos += 1
            ext = src[pos]
            pos += 1
            if ext == 0xFE:
                need(2)
                length = u16be(src, pos) + 261
                pos += 2
                kind = "m4"
            elif ext == 0xFF:
                need(4)
                length = u32be(src, pos) + 3
                pos += 4
                kind = "m5"
            else:
                length = ext + 7
                kind = "m6"

        if length > out_size - len(out):
            raise ShsError("match token overruns decompressed size")
        copy_match(out, dist, length)
        if collect_tokens:
            tokens.append([kind, dist, length])

    if pos != len(src):
        raise ShsError(
            f"compressed stream has {len(src) - pos} trailing bytes; "
            "round-trip without raw-byte metadata is not supported"
        )
    return bytes(out), tokens


def copy_match(out: bytearray, dist: int, length: int) -> None:
    if dist <= 0:
        raise ShsError("bad match distance")
    start = len(out) - dist
    if start < 0:
        raise ShsError("match references bytes before block start")
    for _ in range(length):
        out.append(out[start])
        start += 1


def encode_compressed(data: bytes, tokens: list[list[Any]]) -> bytes:
    out = bytearray()
    pos = 0

    for token in tokens:
        kind = token[0]
        if kind.startswith("l"):
            length = int(token[1])
            if pos + length > len(data):
                raise ShsError("literal token overruns payload")
            out.extend(encode_literal_header(kind, length))
            out.extend(data[pos : pos + length])
            pos += length
            continue

        dist = int(token[1])
        length = int(token[2])
        verify_match(data, pos, dist, length)
        out.extend(encode_match_header(kind, dist, length))
        pos += length

    if pos != len(data):
        raise ShsError("compression token recipe does not cover payload")
    return bytes(out)


def encode_literal_header(kind: str, length: int) -> bytes:
    if kind == "l1":
        if not 1 <= length <= 29:
            raise ShsError("l1 literal length out of range")
        return bytes([length - 1])
    if kind == "l2":
        if not 30 <= length <= 285:
            raise ShsError("l2 literal length out of range")
        return bytes([0x1D, length - 30])
    if kind == "l3":
        if not 286 <= length <= 65821:
            raise ShsError("l3 literal length out of range")
        return bytes([0x1E]) + p16be(length - 286)
    if kind == "l4":
        if not 0 <= length <= 0xFFFFFFFF:
            raise ShsError("l4 literal length out of range")
        return bytes([0x1F]) + p32be(length)
    raise ShsError(f"unknown literal token {kind!r}")


def encode_match_header(kind: str, dist: int, length: int) -> bytes:
    if kind == "m1":
        if not 1 <= dist <= 8192 or not 3 <= length <= 6:
            raise ShsError("m1 match out of range")
        d = dist - 1
        return bytes([0x80 | ((length - 3) << 5) | (d >> 8), d & 0xFF])
    if kind == "m2":
        if not 1 <= dist <= 8 or not 3 <= length <= 6:
            raise ShsError("m2 match out of range")
        return bytes([0x20 | ((dist - 1) << 2) | (length - 3)])
    if kind == "m3":
        if not 1 <= dist <= 256 or not 7 <= length <= 38:
            raise ShsError("m3 match out of range")
        return bytes([0x40 | (length - 7), dist - 1])
    if kind in {"m4", "m5", "m6"}:
        if not 1 <= dist <= 8192:
            raise ShsError(f"{kind} match distance out of range")
        d = dist - 1
        head = bytearray([0x60 | (d >> 8), d & 0xFF])
        if kind == "m4":
            if not 261 <= length <= 65796:
                raise ShsError("m4 match length out of range")
            head.extend([0xFE])
            head.extend(p16be(length - 261))
        elif kind == "m5":
            if not 3 <= length <= 0x100000002:
                raise ShsError("m5 match length out of range")
            head.extend([0xFF])
            head.extend(p32be(length - 3))
        else:
            if not 7 <= length <= 260:
                raise ShsError("m6 match length out of range")
            head.extend([length - 7])
        return bytes(head)
    raise ShsError(f"unknown match token {kind!r}")


def verify_match(data: bytes, pos: int, dist: int, length: int) -> None:
    if dist <= 0 or pos - dist < 0:
        raise ShsError("match token cannot be applied to current payload")
    if pos + length > len(data):
        raise ShsError("match token overruns payload")
    for i in range(length):
        if data[pos + i] != data[pos - dist + i]:
            raise ShsError("payload no longer matches stored compression recipe")


def build_block(block: dict[str, Any], data: bytes, changed_policy: str) -> bytes:
    expected = int(block["unpacked_size"])
    changed = len(data) != expected or sha1_hex(data) != block.get("sha1")

    if not block.get("packed"):
        return p32le(0) + p32le(len(data)) + data

    if changed and changed_policy == "store":
        return p32le(0) + p32le(len(data)) + data
    if changed and changed_policy == "error":
        raise ShsError("payload changed; exact compression recipe may no longer fit")

    packed = encode_compressed(data, block["tokens"])
    if len(packed) != int(block["packed_size"]):
        raise ShsError("rebuilt compressed stream length differs from original")
    return p32le(len(packed)) + p32le(len(data)) + packed


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def detect_extension(data: bytes) -> str:
    if data.startswith(b"BM"):
        return ".bmp"
    if len(data) >= 4 and data[:4] in (b"\x00\x00\x02\x00", b"\x00\x00\x0A\x00"):
        return ".tga"
    return ""


def add_detected_extension(file_name: str, data: bytes) -> str:
    if Path(file_name).suffix:
        return file_name
    return file_name + detect_extension(data)


def lower_ascii_bytes(value: bytes) -> bytes:
    return bytes(b | 0x20 if 0x41 <= b <= 0x5A else b for b in value)


def shs_hash(name: bytes) -> int:
    h = 0
    factor = 1
    for b in name:
        signed = b if b < 0x80 else b - 0x100
        h ^= (factor * signed) & 0xFFFFFFFF
        factor = (factor + 499) & 0xFFFFFFFF
    return (h ^ (h >> 11)) & 0xFFFFFFFF


def compare_archive_names(left: bytes, right: bytes) -> int:
    if _COMPARE_STRING_W is None:
        return (left > right) - (left < right)
    left_text = left.decode("cp932")
    right_text = right.decode("cp932")
    result = _COMPARE_STRING_W(
        LCID_JAPANESE, SORT_STRINGSORT, left_text, -1, right_text, -1
    )
    if result == 1:
        return -1
    if result == 3:
        return 1
    if result == 2:
        return 0
    raise ShsError("CompareStringW failed")


def decode_name_for_display(name: bytes) -> str:
    for enc in ("cp932", "latin1"):
        try:
            return name.decode(enc)
        except UnicodeDecodeError:
            pass
    return name.hex()


def make_safe_file_name(name: bytes, used: set[str], fallback: str) -> str:
    text = decode_name_for_display(name) if name else fallback
    invalid = '<>:"/\\|?*'
    safe = "".join("_" if ch in invalid or ord(ch) < 32 else ch for ch in text)
    safe = safe.strip(" .")
    if not safe:
        safe = fallback

    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
    if safe.upper() in reserved:
        safe = f"_{safe}"

    base = safe
    suffix = 1
    key = safe.casefold()
    while key in used:
        suffix += 1
        safe = f"{base}__{suffix:02d}"
        key = safe.casefold()
    used.add(key)
    return safe


def unpack_archive(src: Path, out_dir: Path) -> None:
    archive = src.read_bytes()
    if len(archive) < 8:
        raise ShsError("file is too small")
    magic = archive[:4]
    if magic == b"SHS6":
        manifest = unpack_shs6(archive, out_dir)
    elif magic == b"SHS7":
        manifest = unpack_shs7(archive, out_dir)
    else:
        raise ShsError(f"unsupported magic {magic!r}")

    write_manifest(out_dir, manifest)


def unpack_shs6(archive: bytes, out_dir: Path) -> dict[str, Any]:
    count = u32le(archive, 4)
    table_end = 8 + count * 4
    if table_end > len(archive):
        raise ShsError("SHS6 offset table extends past EOF")

    ensure_clean_output(out_dir)
    entries_dir = out_dir / ENTRY_DIR
    entries_dir.mkdir(parents=True)

    entries = []
    offsets = [u32le(archive, 8 + i * 4) for i in range(count)]
    for i, off in enumerate(offsets, 1):
        block, payload, end = read_exact_block(archive, off)
        file_name = add_detected_extension(f"{i:06d}", payload)
        (entries_dir / file_name).write_bytes(payload)
        entries.append(
            {
                "index": i,
                "file": f"{ENTRY_DIR}/{file_name}",
                "offset": off,
                "end": end,
                "block": block,
            }
        )

    data_end = verify_contiguous("SHS6 data", table_end, [(e["offset"], e["end"]) for e in entries])
    parse_size_footer(archive, data_end, "SHS6")
    return {"format": "SHS6"}


def unpack_shs7(archive: bytes, out_dir: Path) -> dict[str, Any]:
    bucket_count = u32le(archive, 4)
    table_end = 8 + bucket_count * 8
    if table_end > len(archive):
        raise ShsError("SHS7 bucket table extends past EOF")

    ensure_clean_output(out_dir)
    entries_dir = out_dir / ENTRY_DIR
    entries_dir.mkdir(parents=True)

    buckets = []
    entries = []
    seen_offsets: set[int] = set()

    for bucket_index in range(bucket_count):
        size = u32le(archive, 8 + bucket_index * 8)
        off = u32le(archive, 12 + bucket_index * 8)
        buckets.append({"size": size, "offset": off})
        if size == 0:
            continue
        if off < table_end or off + size > len(archive):
            raise ShsError(f"bad SHS7 bucket {bucket_index}")
        bucket_data = archive[off : off + size]
        pos = 0
        order = 0
        while pos < size and bucket_data[pos] != 0:
            rec_len = bucket_data[pos]
            if rec_len < 6 or pos + rec_len > size:
                raise ShsError(f"bad SHS7 record in bucket {bucket_index}")
            data_off = u32be(bucket_data, pos + 1)
            name_end = bucket_data.find(b"\0", pos + 5, pos + rec_len)
            if name_end < 0:
                raise ShsError(f"unterminated SHS7 name in bucket {bucket_index}")
            name = bytes(bucket_data[pos + 5 : name_end])
            record_zero_padding = bytes(bucket_data[name_end + 1 : pos + rec_len])
            if record_zero_padding != b"\0" * len(record_zero_padding):
                raise ShsError(
                    f"non-zero SHS7 record padding in bucket {bucket_index}; "
                    "round-trip without raw record bytes is not supported"
                )
            if data_off in seen_offsets:
                raise ShsError(f"duplicate SHS7 data offset 0x{data_off:x}")
            seen_offsets.add(data_off)
            entries.append(
                {
                    "bucket": bucket_index,
                    "bucket_order": order,
                    "record_len": rec_len,
                    "data_offset": data_off,
                    "name_hex": name.hex(),
                    "name_text": decode_name_for_display(name),
                    "record_zero_padding": len(record_zero_padding),
                }
            )
            pos += rec_len
            order += 1
        if pos < size:
            padding = bucket_data[pos:size]
            if padding != b"\0" * len(padding):
                raise ShsError(f"non-zero SHS7 bucket padding in bucket {bucket_index}")
            buckets[bucket_index]["zero_padding"] = len(padding)

    data_entries = sorted(entries, key=lambda e: e["data_offset"])
    used_names: set[str] = set()
    for data_order, entry in enumerate(data_entries):
        block, payload, end = read_exact_block(archive, entry["data_offset"])
        file_name = make_safe_file_name(
            bytes.fromhex(entry["name_hex"]), used_names, f"{data_order + 1:06d}"
        )
        file_name = add_detected_extension(file_name, payload)
        (entries_dir / file_name).write_bytes(payload)
        entry["data_order"] = data_order
        entry["file"] = f"{ENTRY_DIR}/{file_name}"
        entry["end"] = end
        entry["block"] = block

    bucket_ranges = [(b["offset"], b["offset"] + b["size"]) for b in buckets if b["size"]]
    bucket_end = verify_contiguous("SHS7 bucket data", table_end, bucket_ranges)
    data_ranges = [(e["data_offset"], e["end"]) for e in entries]
    first_data = min((off for off, _ in data_ranges), default=bucket_end)
    if first_data < bucket_end:
        raise ShsError("SHS7 data area overlaps bucket area")
    data_padding = archive[bucket_end:first_data]
    if data_padding != b"\0" * len(data_padding):
        raise ShsError(
            "SHS7 index/data gap contains non-zero bytes; "
            "round-trip without raw gap bytes is not supported"
        )
    data_end = verify_contiguous("SHS7 file data", first_data, data_ranges)
    parse_size_footer(archive, data_end, "SHS7")
    return {"format": "SHS7"}


def verify_contiguous(kind: str, start: int, ranges: list[tuple[int, int]]) -> int:
    ranges = sorted(ranges)
    cursor = start
    for off, end in ranges:
        if off != cursor:
            raise ShsError(
                f"{kind} has a gap or overlap at 0x{cursor:x}->0x{off:x}; "
                "round-trip without gap bytes is not supported"
            )
        cursor = end
    return cursor


def parse_size_footer(archive: bytes, data_end: int, kind: str) -> dict[str, Any]:
    tail = archive[data_end:]
    if len(tail) == 4 and u32le(tail, 0) == len(archive):
        return {"present": True}
    if tail == b"":
        return {"present": False}
    if tail == b"\0" * len(tail):
        return {"present": False, "zero_padding": len(tail)}
    raise ShsError(
        f"{kind} has unsupported trailing bytes at 0x{data_end:x}; "
        "round-trip without raw trailing bytes is not supported"
    )


def append_archive_footer(out: bytearray, manifest: dict[str, Any]) -> None:
    footer = manifest.get("size_footer", {"present": False})
    zero_padding = int(footer.get("zero_padding", 0))
    if zero_padding:
        out.extend(b"\0" * zero_padding)
    if footer.get("present"):
        out.extend(p32le(len(out) + 4))


def ensure_clean_output(out_dir: Path) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ShsError(f"output directory is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)


def write_manifest(out_dir: Path, manifest: dict[str, Any]) -> None:
    with (out_dir / MANIFEST_NAME).open("w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def read_manifest(in_dir: Path) -> dict[str, Any]:
    path = in_dir / MANIFEST_NAME
    if not path.is_file():
        raise ShsError(f"missing {MANIFEST_NAME}: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def entry_file_paths(in_dir: Path) -> list[Path]:
    entries_dir = in_dir / ENTRY_DIR
    if not entries_dir.is_dir():
        raise ShsError(f"missing entries directory: {entries_dir}")
    files = [p for p in entries_dir.iterdir() if p.is_file()]
    if not files:
        raise ShsError(f"no files found in {entries_dir}")
    return files


def natural_sort_key(path: Path) -> list[tuple[int, Any]]:
    parts: list[tuple[int, Any]] = []
    text = path.name
    pos = 0
    while pos < len(text):
        if text[pos].isdigit():
            end = pos + 1
            while end < len(text) and text[end].isdigit():
                end += 1
            parts.append((0, int(text[pos:end])))
            pos = end
        else:
            end = pos + 1
            while end < len(text) and not text[end].isdigit():
                end += 1
            parts.append((1, text[pos:end].casefold()))
            pos = end
    return parts


def raw_block(data: bytes) -> bytes:
    return p32le(0) + p32le(len(data)) + data


def append_size_footer(out: bytearray) -> None:
    out.extend(p32le(len(out) + 4))


def archive_entry_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in AUTO_EXTENSIONS and detect_extension(path.read_bytes()) == suffix:
        return path.stem
    return path.name


def encode_archive_name(path: Path) -> bytes:
    entry_name = archive_entry_text(path)
    try:
        name = entry_name.encode("cp932")
    except UnicodeEncodeError as exc:
        raise ShsError(f"file name is not encodable as CP932: {entry_name}") from exc
    if not name:
        raise ShsError("empty SHS7 entry name")
    if len(name) + 6 > 0xFF:
        raise ShsError(f"SHS7 entry name is too long: {entry_name}")
    return name


def pack_archive(in_dir: Path, dst: Path, changed_policy: str) -> None:
    manifest = read_manifest(in_dir)
    fmt = manifest.get("format")
    if fmt == "SHS6":
        data = pack_shs6(in_dir, manifest, changed_policy)
    elif fmt == "SHS7":
        data = pack_shs7(in_dir, manifest, changed_policy)
    else:
        raise ShsError(f"unsupported manifest format {fmt!r}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)


def pack_shs6(in_dir: Path, manifest: dict[str, Any], changed_policy: str) -> bytes:
    files = sorted(entry_file_paths(in_dir), key=natural_sort_key)
    count = len(files)
    header_size = 8 + count * 4
    blocks = []
    offsets = []
    cursor = header_size
    for path in files:
        block = raw_block(path.read_bytes())
        offsets.append(cursor)
        blocks.append(block)
        cursor += len(block)

    out = bytearray(b"SHS6" + p32le(count))
    for off in offsets:
        out.extend(p32le(off))
    for block in blocks:
        out.extend(block)
    append_size_footer(out)
    return bytes(out)


def pack_shs7(in_dir: Path, manifest: dict[str, Any], changed_policy: str) -> bytes:
    files = sorted(entry_file_paths(in_dir), key=natural_sort_key)
    entries = []
    for path in files:
        name = encode_archive_name(path)
        entries.append({"path": path, "name": name, "block": raw_block(path.read_bytes())})

    bucket_count = max(1, min(512, len(entries)))
    bucket_entries: list[list[int]] = [[] for _ in range(bucket_count)]
    for i, entry in enumerate(entries):
        bucket_entries[shs_hash(entry["name"]) % bucket_count].append(i)
    for bucket in bucket_entries:
        bucket.sort(
            key=functools.cmp_to_key(
                lambda left, right: compare_archive_names(
                    entries[left]["name"], entries[right]["name"]
                )
            )
        )

    header_size = 8 + bucket_count * 8
    bucket_cursor = header_size
    bucket_offsets = []
    bucket_sizes = []

    for bucket in bucket_entries:
        if not bucket:
            bucket_offsets.append(bucket_cursor)
            bucket_sizes.append(0)
            continue
        size = sum(1 + 4 + len(entries[i]["name"]) + 1 for i in bucket) + 1
        bucket_offsets.append(bucket_cursor)
        bucket_sizes.append(size)
        bucket_cursor += size

    data_offsets: dict[int, int] = {}
    data_cursor = bucket_cursor
    for i in range(len(entries)):
        data_offsets[i] = data_cursor
        data_cursor += len(entries[i]["block"])

    bucket_payloads = []
    for bucket in bucket_entries:
        if not bucket:
            bucket_payloads.append(b"")
            continue
        payload = bytearray()
        for i in bucket:
            name = entries[i]["name"]
            rec_len = 1 + 4 + len(name) + 1
            payload.extend(bytes([rec_len]))
            payload.extend(p32be(data_offsets[i]))
            payload.extend(name)
            payload.extend(b"\0")
        payload.extend(b"\0")
        bucket_payloads.append(bytes(payload))

    out = bytearray(b"SHS7" + p32le(bucket_count))
    for size, off in zip(bucket_sizes, bucket_offsets):
        out.extend(p32le(size))
        out.extend(p32le(off))
    for payload in bucket_payloads:
        out.extend(payload)
    for entry in entries:
        out.extend(entry["block"])
    append_size_footer(out)
    return bytes(out)


def roundtrip(src: Path, work_dir: Path | None, keep: bool) -> bool:
    if work_dir is None:
        temp = tempfile.TemporaryDirectory(prefix="shs_roundtrip_")
        base = Path(temp.name)
    else:
        temp = None
        base = work_dir
        if base.exists():
            shutil.rmtree(base)
        base.mkdir(parents=True)

    try:
        unpack_dir = base / "unpacked"
        rebuilt = base / "rebuilt.hxp"
        verify_dir = base / "verify"
        unpack_archive(src, unpack_dir)
        pack_archive(unpack_dir, rebuilt, changed_policy="error")
        unpack_archive(rebuilt, verify_dir)
        return True
    finally:
        if temp is not None and not keep:
            temp.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_unpack = sub.add_parser("unpack", help="unpack SHS6/SHS7 archive")
    p_unpack.add_argument("archive", type=Path)
    p_unpack.add_argument("out_dir", type=Path)

    p_pack = sub.add_parser("pack", help="repack an unpacked directory")
    p_pack.add_argument("in_dir", type=Path)
    p_pack.add_argument("archive", type=Path)
    p_pack.add_argument(
        "--changed-policy",
        choices=("error", "store"),
        default="error",
        help="what to do when edited data no longer fits the saved token recipe",
    )

    p_round = sub.add_parser("roundtrip", help="verify unpack+pack readability")
    p_round.add_argument("archive", type=Path)
    p_round.add_argument("--work-dir", type=Path)
    p_round.add_argument("--keep", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "unpack":
            unpack_archive(args.archive, args.out_dir)
            return 0
        if args.cmd == "pack":
            pack_archive(args.in_dir, args.archive, args.changed_policy)
            return 0
        if args.cmd == "roundtrip":
            return 0 if roundtrip(args.archive, args.work_dir, args.keep) else 1
    except ShsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
