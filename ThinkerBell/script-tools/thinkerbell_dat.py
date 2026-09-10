#!/usr/bin/env python3
"""
ThinkerBell ArcXX.dat unpack/repack helper.

The game uses small LZSS-compressed index DATs (Arc00/01/04) that point into
large payload DATs (Arc02/03/05).  This tool extracts payload files under the
payload DAT name (for example out/Arc02.dat/entries/...), then repacks by
rebuilding payload DATs and rewriting/recompressing the paired index DATs.
Unchanged round trips preserve the original index bytes; changed entries get a
fresh all-literal LZSS stream that the game loader accepts.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import shutil
import struct
from pathlib import Path

MAGIC = "ThinkerBell-dat-v1"
COMPATIBLE_MANIFEST_MAGICS = {MAGIC, "hidamari-dat-v1"}
INDEX_TO_ARCHIVE = {
    "Arc00.dat": "Arc02.dat",
    "Arc01.dat": "Arc03.dat",
    "Arc04.dat": "Arc05.dat",
}
SPECIAL_DAT = "Arc06.dat"
RECORD = struct.Struct("<IIIIIc")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_file(path: Path) -> bytes:
    with path.open("rb") as f:
        return f.read()


def write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(data)


def decode_len4(data: bytes, off: int) -> int:
    value = 0
    for i, weight in enumerate((1000, 100, 10, 1)):
        c = data[off + i]
        if c != 0xFF:
            value += (c ^ 0x7F) * weight
    return value


def encode_len4(value: int) -> bytes:
    """Encode the game's four-byte weighted length field.

    Despite looking decimal-ish, the loader multiplies arbitrary decoded byte
    values by 1000/100/10/1.  Values above 9999 therefore use a large first
    component, e.g. 104727 -> [104, 7, 2, 7] ^ 0x7F.
    """
    if not 0 <= value <= 254 * 1111:
        raise ValueError(f"DAT length header only supports 0..{254 * 1111}, got {value}")
    parts = []
    rest = value
    for weight in (1000, 100, 10, 1):
        part, rest = divmod(rest, weight)
        if part == 0x80:
            raise ValueError("cannot encode length component 0x80 because it maps to the 0xFF skip marker")
        if part > 0xFE:
            raise ValueError(f"length component too large: {part}")
        parts.append(part)
    return bytes(part ^ 0x7F for part in parts)


def lzss_compress_literals(data: bytes) -> bytes:
    """Emit a valid LZSS stream using only literal tokens.

    This is larger than the original compressed stream, but it exactly matches
    sub_406E40's decoder and keeps repacking simple/reliable for edited files.
    """
    out = bytearray()
    for i in range(0, len(data), 8):
        chunk = data[i:i + 8]
        out.append((1 << len(chunk)) - 1)  # one flag bit per literal, LSB first
        out.extend(chunk)
    return bytes(out)


def lzss_decompress(comp: bytes, expected_size: int | None = None) -> bytes:
    """Decompress the game's Okumura-style 4 KiB LZSS stream."""
    window = bytearray(4096)
    r = 4078
    flags = 0
    i = 0
    out = bytearray()

    while i < len(comp):
        flags >>= 1
        if (flags & 0x100) == 0:
            if i >= len(comp):
                break
            flags = comp[i] | 0xFF00
            i += 1

        if flags & 1:
            if i >= len(comp):
                break
            c = comp[i]
            i += 1
            out.append(c)
            window[r] = c
            r = (r + 1) & 0xFFF
        else:
            if i >= len(comp):
                break
            b1 = comp[i]
            i += 1
            if i >= len(comp):
                break
            b2 = comp[i]
            i += 1
            pos = b1 | ((b2 & 0xF0) << 4)
            length = (b2 & 0x0F) + 2
            for k in range(length + 1):
                c = window[(pos + k) & 0xFFF]
                out.append(c)
                window[r] = c
                r = (r + 1) & 0xFFF

    result = bytes(out)
    if expected_size is not None and len(result) != expected_size:
        raise ValueError(f"LZSS size mismatch: expected {expected_size}, got {len(result)}")
    return result


def parse_index_dat(data: bytes) -> dict:
    unpacked_size = decode_len4(data, 0)
    packed_size = decode_len4(data, 4)
    if 8 + packed_size > len(data):
        raise ValueError("packed stream extends beyond file")
    packed = data[8:8 + packed_size]
    decoded = lzss_decompress(packed, unpacked_size)
    if len(decoded) % RECORD.size:
        raise ValueError(f"decoded index size is not a multiple of {RECORD.size}: {len(decoded)}")
    records = []
    for i in range(0, len(decoded), RECORD.size):
        a, entry_id, stored_size, file_size, offset, typ = RECORD.unpack_from(decoded, i)
        records.append({
            "record_index": i // RECORD.size,
            "const": a,
            "entry_id": entry_id,
            "stored_size": stored_size,
            "file_size": file_size,
            "offset": offset,
            "type": typ.decode("ascii", "replace"),
        })
    return {
        "kind": "index",
        "unpacked_size": unpacked_size,
        "packed_size": packed_size,
        "decoded": decoded,
        "packed": packed,
        "tail": data[8 + packed_size:],
        "records": records,
    }


def parse_arc06(data: bytes) -> dict:
    unpacked_size = decode_len4(data, 0)
    packed_size = decode_len4(data, 4)
    packed = data[8:8 + packed_size]
    decoded = lzss_decompress(packed, unpacked_size)
    off = 8 + packed_size
    blocks = []
    for name in ("block2", "block3"):
        size = decode_len4(data, off)
        header = data[off:off + 4]
        off += 4
        block = data[off:off + size]
        off += size
        blocks.append({"name": name, "size": size, "header_b64": base64.b64encode(header).decode(), "data": block})
    return {
        "kind": "special",
        "unpacked_size": unpacked_size,
        "packed_size": packed_size,
        "decoded": decoded,
        "packed": packed,
        "blocks": blocks,
        "tail": data[off:],
    }


def classify(root: Path) -> dict:
    items = []
    for name in ["Arc00.dat", "Arc01.dat", "Arc02.dat", "Arc03.dat", "Arc04.dat", "Arc05.dat", "Arc06.dat"]:
        path = root / name
        if not path.exists():
            continue
        data = read_file(path)
        role = "unknown"
        archive = None
        note = ""
        if name in INDEX_TO_ARCHIVE:
            info = parse_index_dat(data)
            role = "index"
            archive = INDEX_TO_ARCHIVE[name]
            note = f"{len(info['records'])} records, decoded {len(info['decoded'])} bytes"
        elif name in INDEX_TO_ARCHIVE.values():
            role = "archive"
            note = "payload bytes referenced by an index DAT"
        elif name == SPECIAL_DAT:
            info = parse_arc06(data)
            role = "special-index/config"
            note = f"lzss {len(info['decoded'])} bytes + {len(info['blocks'])} raw blocks"
        items.append({"file": name, "size": len(data), "role": role, "archive": archive, "note": note})
    return {"items": items}


def safe_name(record: dict) -> str:
    # Use the engine entry id as the filename and the record type letter as the
    # extension, e.g. 00000.a, 05422.c, 00434.h.
    return f"{record['entry_id']:05d}.{record['type']}"


def pack_record(row: dict) -> bytes:
    return RECORD.pack(
        int(row["const"]),
        int(row["entry_id"]),
        int(row["stored_size"]),
        int(row["file_size"]),
        int(row["offset"]),
        str(row["type"]).encode("ascii")[:1],
    )


def unpack(root: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"magic": MAGIC, "source": str(root), "files": []}

    for item in classify(root)["items"]:
        name = item["file"]
        path = root / name
        data = read_file(path)
        file_entry = {"name": name, "size": len(data), "sha256": sha256(data), "role": item["role"]}

        if item["role"] == "index":
            info = parse_index_dat(data)
            archive_name = INDEX_TO_ARCHIVE[name]
            base = out_dir / archive_name
            write_file(base / f"{name}.decoded_index.bin", info["decoded"])
            write_file(base / f"{name}.packed_stream.bin", info["packed"])
            with (base / "records.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["record_index", "const", "entry_id", "type", "offset", "stored_size", "file_size", "file_name"])
                w.writeheader()
                for r in info["records"]:
                    row = {k: r[k] for k in ["record_index", "const", "entry_id", "type", "offset", "stored_size", "file_size"]}
                    row["file_name"] = safe_name(r)
                    w.writerow(row)

            archive_data = read_file(root / archive_name)
            entries_dir = base / "entries"
            for r in info["records"]:
                start = r["offset"]
                end = start + r["file_size"]
                if start < 0 or end > len(archive_data):
                    raise ValueError(f"{name} record {r['record_index']} points outside {archive_name}: {start}+{r['file_size']}")
                write_file(entries_dir / safe_name(r), archive_data[start:end])

            file_entry.update({
                "archive": archive_name,
                "unpacked_size": info["unpacked_size"],
                "packed_size": info["packed_size"],
                "original_header_b64": base64.b64encode(data[:8]).decode(),
                "records_sha256": sha256(info["decoded"]),
                "original_index_sha256": sha256(info["decoded"]),
                "tail_b64": base64.b64encode(info["tail"]).decode(),
                "record_count": len(info["records"]),
            })
        elif item["role"] == "archive":
            # Archive bytes are reconstructed from extracted entries during repack.
            # Do not embed them in manifest.json; Arc03/Arc05 are hundreds of MB.
            file_entry["source_file"] = name
        elif item["role"] == "special-index/config":
            info = parse_arc06(data)
            base = out_dir / name
            write_file(base / "decoded_block1.bin", info["decoded"])
            write_file(base / "packed_stream.bin", info["packed"])
            for block in info["blocks"]:
                write_file(base / f"{block['name']}.bin", block["data"])
            file_entry.update({
                "unpacked_size": info["unpacked_size"],
                "packed_size": info["packed_size"],
                "blocks": [{"name": b["name"], "size": b["size"], "header_b64": b["header_b64"]} for b in info["blocks"]],
                "tail_b64": base64.b64encode(info["tail"]).decode(),
            })
        else:
            file_entry["raw_b64"] = base64.b64encode(data).decode()

        manifest["files"].append(file_entry)

    write_file(out_dir / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))



def repack(unpack_dir: Path, out_dir: Path) -> None:
    manifest = json.loads((unpack_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("magic") not in COMPATIBLE_MANIFEST_MAGICS:
        raise ValueError("not a compatible ThinkerBell/HIDAMARI DAT manifest")
    src = Path(manifest["source"])
    out_dir.mkdir(parents=True, exist_ok=True)

    index_entries: dict[str, dict] = {}
    archive_to_index: dict[str, str] = {}
    for f in manifest["files"]:
        if f["role"] == "index":
            index_entries[f["name"]] = f
            archive_to_index[f["archive"]] = f["name"]

    rebuilt_indexes: dict[str, bytes] = {}
    rebuilt_archives: dict[str, bytes] = {}

    for archive_name, index_name in archive_to_index.items():
        rows = []
        records_path = unpack_dir / archive_name / "records.csv"
        with records_path.open("r", newline="", encoding="utf-8") as csvf:
            for row in csv.DictReader(csvf):
                row["record_index"] = int(row["record_index"])
                row["const"] = int(row["const"])
                row["entry_id"] = int(row["entry_id"])
                row["offset"] = int(row["offset"])
                row["stored_size"] = int(row["stored_size"])
                row["file_size"] = int(row["file_size"])
                rows.append(row)

        chunks = []
        pos = 0
        for row in sorted(rows, key=lambda x: x["record_index"]):
            entry_path = unpack_dir / archive_name / "entries" / row["file_name"]
            data = read_file(entry_path)
            old_file_size = row["file_size"]
            row["offset"] = pos
            row["file_size"] = len(data)
            # field 2 is sometimes a logical/original size and sometimes equals
            # file_size.  If the original record used equal sizes, keep it equal
            # after editing; otherwise leave the logical size untouched.
            if row["stored_size"] == old_file_size or row["stored_size"] == 0:
                row["stored_size"] = len(data)
            chunks.append(data)
            pos += len(data)

        archive_data = b"".join(chunks)
        rebuilt_archives[archive_name] = archive_data
        decoded_index = b"".join(pack_record(row) for row in sorted(rows, key=lambda x: x["record_index"]))
        index_entry = index_entries[index_name]
        old_decoded_sha = index_entry.get("original_index_sha256")
        old_archive_sha = next((f.get("sha256") for f in manifest["files"] if f["name"] == archive_name), None)
        unchanged = sha256(decoded_index) == old_decoded_sha and sha256(archive_data) == old_archive_sha
        if unchanged:
            rebuilt_indexes[index_name] = read_file(src / index_name)
        else:
            packed = lzss_compress_literals(decoded_index)
            rebuilt_indexes[index_name] = encode_len4(len(decoded_index)) + encode_len4(len(packed)) + packed + base64.b64decode(index_entry.get("tail_b64", ""))

    for f in manifest["files"]:
        name = f["name"]
        role = f["role"]
        if role == "index":
            write_file(out_dir / name, rebuilt_indexes[name])
        elif role == "archive" and name in rebuilt_archives:
            write_file(out_dir / name, rebuilt_archives[name])
        elif role == "special-index/config":
            shutil.copyfile(src / name, out_dir / name)
        elif "raw_b64" in f:
            write_file(out_dir / name, base64.b64decode(f["raw_b64"]))
        else:
            shutil.copyfile(src / name, out_dir / name)


def verify(root: Path, rebuilt: Path) -> bool:
    ok = True
    for p in sorted(root.glob("Arc*.dat")):
        q = rebuilt / p.name
        if not q.exists():
            print(f"MISS {p.name}")
            ok = False
            continue
        a = read_file(p)
        b = read_file(q)
        same = a == b
        print(f"{'OK  ' if same else 'FAIL'} {p.name} size {len(a)} -> {len(b)} sha256 {sha256(b)}")
        ok = ok and same
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Unpack/repack ThinkerBell ArcXX.dat files")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("classify")
    p.add_argument("root", type=Path)

    p = sub.add_parser("unpack")
    p.add_argument("root", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("repack")
    p.add_argument("unpack_dir", type=Path)
    p.add_argument("out", type=Path)

    p = sub.add_parser("verify")
    p.add_argument("root", type=Path)
    p.add_argument("rebuilt", type=Path)

    args = ap.parse_args()
    if args.cmd == "classify":
        for item in classify(args.root)["items"]:
            arc = f" -> {item['archive']}" if item.get("archive") else ""
            print(f"{item['file']:9} {item['role']:20} size={item['size']:10}{arc}  {item['note']}")
    elif args.cmd == "unpack":
        unpack(args.root, args.out)
    elif args.cmd == "repack":
        repack(args.unpack_dir, args.out)
    elif args.cmd == "verify":
        return 0 if verify(args.root, args.rebuilt) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
