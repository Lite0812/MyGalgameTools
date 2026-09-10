#!/usr/bin/env python3
"""
KyouPri/VITAMIN FPK extractor and repacker.

The repacker is designed for byte-identical round trips:
    python tools/fpk_tool.py verify KP1N_Bgm.fpk

Extraction writes decoded files plus a manifest that preserves every byte of
the archive header/table metadata needed to rebuild the same binary.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import numpy as np
    from numba import njit
except Exception:  # pragma: no cover - optional speed path
    np = None
    njit = None


MAGIC = b"FPK\x00"
VERSION = b"0100"
ENTRY_SIZE = 24
DEFAULT_SEED = 0
MASK32 = 0xFFFFFFFF
HAVE_JIT = np is not None and njit is not None


if HAVE_JIT:

    @njit(cache=True)
    def _decode_words_jit(words: Any, seed: int) -> None:
        v2 = seed & 0xFFFFFFFF
        i = (seed + words.size * 4 - 4) & 0xFFFFFFFF
        for pos in range(words.size - 1, -1, -1):
            v6 = (v2 + i) & 0xFFFFFFFF
            value = int(words[pos])
            v7 = (value - i) & 0xFFFFFFFF
            v8 = (v2 ^ v7) & 0xFFFFFFFF
            words[pos] = v8
            i = (i - 3) & 0xFFFFFFFF
            v2 = (((v6 << 7) & 0xFFFFFFFF) ^ (((v2 - v8) & 0xFFFFFFFF) >> 7)) & 0xFFFFFFFF

    @njit(cache=True)
    def _encode_words_jit(words: Any, seed: int) -> None:
        v7 = seed & 0xFFFFFFFF
        v9 = (seed + words.size * 4 - 4) & 0xFFFFFFFF
        for pos in range(words.size - 1, -1, -1):
            plain_word = int(words[pos])
            v10 = v7
            v7 = ((((v7 + v9) & 0xFFFFFFFF) << 7) ^ (((v7 - plain_word) & 0xFFFFFFFF) >> 7)) & 0xFFFFFFFF
            words[pos] = (v9 + (plain_word ^ v10)) & 0xFFFFFFFF
            v9 = (v9 - 3) & 0xFFFFFFFF


class FpkError(Exception):
    pass


def u32(value: int) -> int:
    return value & MASK32


def read_u32(buf: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", buf, offset)[0]


def write_u32(buf: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", buf, offset, u32(value))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def decode_text(raw: bytes) -> str:
    raw = raw.split(b"\x00", 1)[0]
    return raw.decode("cp932", errors="replace")


def parse_prefix(header: bytes) -> str:
    if len(header) <= 20:
        return ""
    return decode_text(header[20:])


def safe_relpath(prefix: str, name: str, used: set[str], index: int) -> str:
    merged = (prefix + name).replace("\\", "/")
    parts: list[str] = []
    for part in merged.split("/"):
        if not part or part in (".", "..") or ":" in part:
            continue
        parts.append(part)
    if not parts:
        parts = [f"entry_{index:04d}.bin"]
    rel = "/".join(parts)
    if rel not in used:
        used.add(rel)
        return rel
    stem = Path(rel).stem
    suffix = Path(rel).suffix
    parent = Path(rel).parent.as_posix()
    while True:
        candidate_name = f"{stem}__dup_{index:04d}{suffix}"
        candidate = candidate_name if parent == "." else f"{parent}/{candidate_name}"
        if candidate not in used:
            used.add(candidate)
            return candidate


def ensure_under(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise FpkError(f"unsafe output path in manifest: {rel}") from exc
    return target


def decode_payload(stored: bytes, seed: int = DEFAULT_SEED) -> tuple[bytes, int]:
    """Decode an entry with flag bit 0 set. Returns (plain, trailer_dword)."""
    size = len(stored)
    if size < 8 or size % 4:
        raise FpkError(f"encoded payload has invalid size {size}")

    buf = bytearray(stored)
    if HAVE_JIT:
        words = np.frombuffer(buf, dtype=np.uint32)
        _decode_words_jit(words, u32(seed))
    else:
        v2 = u32(seed)
        i = u32(seed + size - 4)
        pos = size - 4
        while pos >= 0:
            v6 = u32(v2 + i)
            value = read_u32(buf, pos)
            v7 = u32(value - i)
            v8 = u32(v2 ^ v7)
            write_u32(buf, pos, v8)
            i = u32(i - 3)
            v2 = u32((v6 << 7) ^ (u32(v2 - v8) >> 7))
            pos -= 4

    plain_size = read_u32(buf, size - 8)
    trailer = read_u32(buf, size - 4)
    expected_size = 4 * ((plain_size + 3) >> 2) + 8
    if expected_size != size:
        raise FpkError(
            f"decoded payload length check failed: stored={size}, plain={plain_size}, expected={expected_size}"
        )
    return bytes(buf[:plain_size]), trailer


def encode_payload(plain: bytes, trailer: int, seed: int = DEFAULT_SEED) -> bytes:
    """Encode bytes exactly like sub_44C950 when a3 == 0."""
    plain_size = len(plain)
    stored_size = 4 * ((plain_size + 3) >> 2) + 8
    buf = bytearray(stored_size)
    buf[:plain_size] = plain
    write_u32(buf, stored_size - 8, plain_size)
    write_u32(buf, stored_size - 4, trailer)

    if HAVE_JIT:
        words = np.frombuffer(buf, dtype=np.uint32)
        _encode_words_jit(words, u32(seed))
    else:
        v7 = u32(seed)
        v9 = u32(seed + stored_size - 4)
        pos = stored_size - 4
        while pos >= 0:
            plain_word = read_u32(buf, pos)
            v10 = v7
            v7 = u32(((v7 + v9) << 7) ^ (u32(v7 - plain_word) >> 7))
            encoded_word = u32(v9 + (plain_word ^ v10))
            write_u32(buf, pos, encoded_word)
            v9 = u32(v9 - 3)
            pos -= 4
    return bytes(buf)


def parse_archive(data: bytes) -> dict[str, Any]:
    if len(data) < 12:
        raise FpkError("file is too small for an FPK header")
    if data[:4] != MAGIC or data[4:8] != VERSION:
        raise FpkError("not an FPK 0100 archive")

    header_size = read_u32(data, 8)
    if header_size < 20 or header_size > len(data):
        raise FpkError(f"invalid header size {header_size}")

    count = read_u32(data, 12)
    unknown = read_u32(data, 16)
    table_offset = header_size
    table_size = count * ENTRY_SIZE
    if table_offset + table_size > len(data):
        raise FpkError("entry table extends past end of archive")

    header = data[:header_size]
    table = data[table_offset : table_offset + table_size]
    entries: list[dict[str, Any]] = []
    for index in range(count):
        item = table[index * ENTRY_SIZE : (index + 1) * ENTRY_SIZE]
        flags, offset, size = struct.unpack_from("<III", item, 0)
        name_raw = item[12:24]
        if offset + size > len(data):
            raise FpkError(f"entry {index} extends past end of archive")
        entries.append(
            {
                "index": index,
                "flags": flags,
                "offset": offset,
                "size": size,
                "name_raw_b64": b64(name_raw),
                "name": decode_text(name_raw),
                "table_raw_b64": b64(item),
            }
        )

    return {
        "header": header,
        "table": table,
        "header_size": header_size,
        "count": count,
        "unknown": unknown,
        "prefix": parse_prefix(header),
        "entries": entries,
    }


def extract_archive_bytes(
    data: bytes,
    out_dir: Path,
    source_name: str,
    *,
    recursive: bool,
    seed: int,
) -> dict[str, Any]:
    parsed = parse_archive(data)
    files_dir = out_dir / "files"
    nested_dir = out_dir / "nested"
    files_dir.mkdir(parents=True, exist_ok=True)
    if recursive:
        nested_dir.mkdir(parents=True, exist_ok=True)

    used_paths: set[str] = set()
    manifest_entries: list[dict[str, Any]] = []
    prefix = parsed["prefix"]

    for entry in parsed["entries"]:
        index = entry["index"]
        raw_payload = data[entry["offset"] : entry["offset"] + entry["size"]]
        rel = safe_relpath(prefix, entry["name"], used_paths, index)
        output_path = ensure_under(files_dir, rel)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        stored_sha = sha256_bytes(raw_payload)
        decoded = False
        trailer: int | None = None
        plain_size: int | None = None

        if entry["flags"] & 1:
            plain, trailer = decode_payload(raw_payload, seed)
            output_path.write_bytes(plain)
            decoded = True
            plain_size = len(plain)
        else:
            output_path.write_bytes(raw_payload)

        nested_manifest: str | None = None
        if recursive and not decoded and entry["name"].lower().endswith(".fpk") and raw_payload.startswith(MAGIC):
            child_name = f"{index:04d}_{entry['name']}"
            child_dir = nested_dir / child_name
            extract_archive_bytes(
                raw_payload,
                child_dir,
                entry["name"],
                recursive=True,
                seed=seed,
            )
            nested_manifest = f"nested/{child_name}/manifest.json"

        item = {
            **entry,
            "extract_path": rel,
            "decoded": decoded,
            "plain_size": plain_size,
            "decode_trailer": trailer,
            "stored_sha256": stored_sha,
            "file_sha256": sha256_file(output_path),
            "nested_manifest": nested_manifest,
        }
        manifest_entries.append(item)

    manifest = {
        "format": "kyoupri-fpk-v1",
        "source_name": source_name,
        "seed": seed,
        "original_size": len(data),
        "original_sha256": sha256_bytes(data),
        "header_b64": b64(parsed["header"]),
        "header_size": parsed["header_size"],
        "entry_size": ENTRY_SIZE,
        "entry_count": parsed["count"],
        "unknown": parsed["unknown"],
        "prefix": prefix,
        "entries": manifest_entries,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def extract_archive(path: Path, out_dir: Path, *, recursive: bool, seed: int) -> None:
    if out_dir.exists():
        if any(out_dir.iterdir()):
            raise FpkError(f"output directory is not empty: {out_dir}")
    else:
        out_dir.mkdir(parents=True)
    data = path.read_bytes()
    extract_archive_bytes(data, out_dir, path.name, recursive=recursive, seed=seed)


def load_manifest(out_dir: Path) -> dict[str, Any]:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        raise FpkError(f"manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def pack_archive_bytes(out_dir: Path, *, prefer_nested: bool = True) -> bytes:
    manifest = load_manifest(out_dir)
    if manifest.get("format") != "kyoupri-fpk-v1":
        raise FpkError("unsupported manifest format")

    seed = int(manifest.get("seed", DEFAULT_SEED))
    header = unb64(manifest["header_b64"])
    entries = manifest["entries"]
    files_dir = out_dir / "files"

    payloads: list[bytes] = []
    for entry in entries:
        payload: bytes
        nested_manifest = entry.get("nested_manifest")
        if prefer_nested and nested_manifest:
            nested_path = ensure_under(out_dir, nested_manifest)
            nested_root = nested_path.parent
            if nested_path.exists():
                payload = pack_archive_bytes(nested_root, prefer_nested=prefer_nested)
            else:
                payload = ensure_under(files_dir, entry["extract_path"]).read_bytes()
        else:
            payload = ensure_under(files_dir, entry["extract_path"]).read_bytes()

        if entry.get("decoded"):
            trailer = entry.get("decode_trailer")
            if trailer is None:
                raise FpkError(f"missing decode trailer for entry {entry['index']}")
            payload = encode_payload(payload, int(trailer), seed)
        payloads.append(payload)

    offset = len(header) + len(entries) * ENTRY_SIZE
    table = bytearray()
    for entry, payload in zip(entries, payloads):
        name_raw = unb64(entry["name_raw_b64"])
        if len(name_raw) != 12:
            raise FpkError(f"entry {entry['index']} has invalid raw name length")
        table.extend(struct.pack("<III", int(entry["flags"]), offset, len(payload)))
        table.extend(name_raw)
        offset += len(payload)

    return header + bytes(table) + b"".join(payloads)


def pack_archive(out_dir: Path, output: Path, *, prefer_nested: bool) -> None:
    data = pack_archive_bytes(out_dir, prefer_nested=prefer_nested)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)


def verify_archive(path: Path, *, recursive: bool, seed: int, keep: bool) -> bool:
    temp_root = Path(tempfile.mkdtemp(prefix="fpk_verify_"))
    try:
        extract_dir = temp_root / "extract"
        rebuilt = temp_root / "rebuilt.fpk"
        extract_archive(path, extract_dir, recursive=recursive, seed=seed)
        pack_archive(extract_dir, rebuilt, prefer_nested=True)

        original_hash = sha256_file(path)
        rebuilt_hash = sha256_file(rebuilt)
        same_size = path.stat().st_size == rebuilt.stat().st_size
        ok = same_size and original_hash == rebuilt_hash
        print(f"original size : {path.stat().st_size}")
        print(f"rebuilt size  : {rebuilt.stat().st_size}")
        print(f"original sha256: {original_hash}")
        print(f"rebuilt  sha256: {rebuilt_hash}")
        print(f"roundtrip: {'OK' if ok else 'MISMATCH'}")
        if keep:
            kept = path.parent / f"{path.stem}_verify_work"
            if kept.exists():
                shutil.rmtree(kept)
            shutil.move(str(temp_root), str(kept))
            print(f"kept work dir: {kept}")
            temp_root = kept
        return ok
    finally:
        if not keep and temp_root.exists():
            shutil.rmtree(temp_root)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract/repack KyouPri FPK archives.")
    parser.add_argument("--seed", type=lambda x: int(x, 0), default=DEFAULT_SEED, help="decode seed, default: 0")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="extract an FPK archive")
    p_extract.add_argument("archive", type=Path)
    p_extract.add_argument("out_dir", type=Path)
    p_extract.add_argument("--recursive", action="store_true", help="also unpack nested .fpk entries")

    p_pack = sub.add_parser("pack", help="rebuild an FPK archive from an extracted directory")
    p_pack.add_argument("out_dir", type=Path)
    p_pack.add_argument("archive", type=Path)
    p_pack.add_argument("--raw-nested", action="store_true", help="use raw nested .fpk files instead of nested manifests")

    p_verify = sub.add_parser("verify", help="extract and repack, then compare with the original")
    p_verify.add_argument("archive", type=Path)
    p_verify.add_argument("--recursive", action="store_true", help="recurse into nested .fpk entries")
    p_verify.add_argument("--keep", action="store_true", help="keep temporary verification work directory")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "extract":
            extract_archive(args.archive, args.out_dir, recursive=args.recursive, seed=args.seed)
            print(f"extracted: {args.archive} -> {args.out_dir}")
            return 0
        if args.command == "pack":
            pack_archive(args.out_dir, args.archive, prefer_nested=not args.raw_nested)
            print(f"packed: {args.out_dir} -> {args.archive}")
            return 0
        if args.command == "verify":
            ok = verify_archive(args.archive, recursive=args.recursive, seed=args.seed, keep=args.keep)
            return 0 if ok else 1
    except FpkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"io error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
