#!/usr/bin/env python3
"""Unpack TanukiSoft/TLib TArc1.00 and TArc1.10 archives."""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


INDEX_KEY = b"TLibArchiveData"
HASH_MULTIPLIER = 104729
UINT64_MASK = (1 << 64) - 1
BUCKET_STRUCT = struct.Struct("<HHI")
ENTRY_STRUCT = struct.Struct("<QIIII")


@dataclass(frozen=True)
class TacHeader:
    version: str
    filetime_low: int
    filetime_high: int
    file_count: int
    bucket_count: int
    index_size: int
    hash_seed: int
    header_size: int
    unknown: int

    @property
    def data_base(self) -> int:
        return self.header_size + self.index_size


@dataclass(frozen=True)
class TacEntry:
    index: int
    full_hash: int
    mode: int
    size_field: int
    data_offset: int
    stored_size: int

    @property
    def decoded_size(self) -> int:
        return self.size_field if self.mode == 1 else self.stored_size

    @property
    def secure_size(self) -> int:
        return min(self.size_field, self.stored_size) if self.mode == 0 else 0


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"unexpected EOF: wanted {size} bytes, got {len(data)}")
    return data


def _swap_u32_bytes(data: bytes) -> bytes:
    result = bytearray(len(data))
    for offset in range(0, len(data), 4):
        result[offset : offset + 4] = data[offset : offset + 4][::-1]
    return bytes(result)


def tlib_blowfish(data: bytes, key: bytes, *, decrypt: bool) -> bytes:
    """Apply TLib's Blowfish ECB layout, leaving a partial final block unchanged."""
    if not key:
        raise ValueError("Blowfish key cannot be empty")

    try:
        from cryptography.hazmat.decrepit.ciphers import algorithms
    except ImportError:  # cryptography < 43
        from cryptography.hazmat.primitives.ciphers import algorithms
    from cryptography.hazmat.primitives.ciphers import Cipher, modes

    block_size = len(data) & ~7
    if block_size == 0:
        return data

    # The executable passes little-endian uint32 halves to Blowfish. Standard
    # crypto APIs treat each half as big-endian, hence the two word swaps.
    source = _swap_u32_bytes(data[:block_size])
    cipher = Cipher(algorithms.Blowfish(key), modes.ECB())
    context = cipher.decryptor() if decrypt else cipher.encryptor()
    transformed = context.update(source) + context.finalize()
    return _swap_u32_bytes(transformed) + data[block_size:]


def engine_decompress(data: bytes, expected_size: int | None = None) -> bytes:
    """Reimplementation of sub_5F8670: inflate a standard zlib stream."""
    result = zlib.decompress(data)
    if expected_size is not None and len(result) != expected_size:
        raise ValueError(
            f"zlib size mismatch: expected {expected_size}, got {len(result)}"
        )
    return result


def engine_compress(data: bytes) -> bytes:
    """Reimplementation of sub_5F8850: zlib's default compression level."""
    return zlib.compress(data, level=-1)


def read_header(stream: BinaryIO, archive_size: int) -> TacHeader:
    stream.seek(0)
    raw = _read_exact(stream, min(44, archive_size))
    if len(raw) < 36:
        raise ValueError("file is too small to contain a TAC header")

    magic = raw[:12].rstrip(b"\0")
    if magic == b"TArc1.00":
        (
            _,
            filetime_low,
            filetime_high,
            file_count,
            bucket_count,
            index_size,
            hash_seed,
        ) = struct.unpack_from("<12s6I", raw)
        header = TacHeader(
            "1.00",
            filetime_low,
            filetime_high,
            file_count,
            bucket_count,
            index_size,
            hash_seed,
            36,
            0,
        )
    elif magic == b"TArc1.10":
        if len(raw) < 44:
            raise ValueError("truncated TArc1.10 header")
        (
            _,
            filetime_low,
            filetime_high,
            file_count,
            bucket_count,
            index_size,
            hash_seed,
            header_size,
            unknown,
        ) = struct.unpack_from("<12s8I", raw)
        if header_size != 44:
            raise ValueError(f"unsupported TArc1.10 header size: {header_size}")
        header = TacHeader(
            "1.10",
            filetime_low,
            filetime_high,
            file_count,
            bucket_count,
            index_size,
            hash_seed,
            header_size,
            unknown,
        )
    else:
        raise ValueError(f"unsupported TAC signature: {raw[:12]!r}")

    if header.file_count == 0 or header.bucket_count == 0:
        raise ValueError("archive contains no index entries")
    if header.index_size == 0 or header.data_base > archive_size:
        raise ValueError("index extends beyond the archive")
    return header


def read_index(stream: BinaryIO, header: TacHeader) -> list[TacEntry]:
    stream.seek(header.header_size)
    encrypted = _read_exact(stream, header.index_size)
    compressed = tlib_blowfish(encrypted, INDEX_KEY, decrypt=True)
    expected_size = (
        BUCKET_STRUCT.size * header.bucket_count
        + ENTRY_STRUCT.size * header.file_count
    )
    plain = engine_decompress(compressed, expected_size)

    buckets = [
        BUCKET_STRUCT.unpack_from(plain, index * BUCKET_STRUCT.size)
        for index in range(header.bucket_count)
    ]
    entry_base = header.bucket_count * BUCKET_STRUCT.size
    raw_entries = [
        ENTRY_STRUCT.unpack_from(plain, entry_base + index * ENTRY_STRUCT.size)
        for index in range(header.file_count)
    ]

    low_hashes: list[int | None] = [None] * header.file_count
    for hash_low, count, first_entry in buckets:
        if first_entry + count > header.file_count:
            raise ValueError(
                f"bucket {hash_low:04x} points outside the entry table"
            )
        for entry_index in range(first_entry, first_entry + count):
            if low_hashes[entry_index] is not None:
                raise ValueError(f"entry {entry_index} is referenced by two buckets")
            low_hashes[entry_index] = hash_low

    if any(value is None for value in low_hashes):
        raise ValueError("one or more entries are missing from the bucket table")

    entries: list[TacEntry] = []
    for index, (hash_upper, mode, size_field, data_offset, stored_size) in enumerate(
        raw_entries
    ):
        if hash_upper >> 48:
            raise ValueError(f"entry {index} has an invalid 48-bit hash suffix")
        full_hash = (hash_upper << 16) | int(low_hashes[index])
        entries.append(
            TacEntry(
                index,
                full_hash,
                mode,
                size_field,
                data_offset,
                stored_size,
            )
        )
    return entries


def _is_cp932_lead(value: int) -> bool:
    return 0x81 <= value <= 0x9F or 0xE0 <= value <= 0xFC


def normalize_archive_path(path: str) -> bytes:
    source = path.encode("cp932")
    result = bytearray(source)
    index = 0
    while index < len(result):
        value = result[index]
        if _is_cp932_lead(value) and index + 1 < len(result):
            index += 2
        else:
            if 0x61 <= value <= 0x7A:
                result[index] = value - 0x20
            elif value == 0x5C:
                result[index] = 0x2F
            index += 1
    return bytes(result)


def archive_path_hash(path: str, seed: int) -> int:
    value = 0
    for byte in normalize_archive_path(path):
        signed_byte = byte if byte < 0x80 else byte - 0x100
        value = (value * HASH_MULTIPLIER + signed_byte + seed) & UINT64_MASK
    return value


def _load_known_names(path: Path | None, seed: int) -> dict[int, str]:
    if path is None:
        return {}
    candidates: dict[int, list[str]] = {}
    with path.open("r", encoding="utf-8-sig") as stream:
        for raw_line in stream:
            name = raw_line.strip()
            if not name or name.startswith("#"):
                continue
            candidates.setdefault(archive_path_hash(name, seed), []).append(name)
    return {
        hash_value: names[0]
        for hash_value, names in candidates.items()
        if len(names) == 1
    }


def _guess_extension(data: bytes) -> str:
    signatures = (
        (b"\x89PNG\r\n\x1a\n", ".png"),
        (b"\xff\xd8\xff", ".jpg"),
        (b"BM", ".bmp"),
        (b"GIF87a", ".gif"),
        (b"GIF89a", ".gif"),
        (b"OggS", ".ogg"),
        (b"fLaC", ".flac"),
        (b"DDS ", ".dds"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".cfb"),
        (b"PK\x03\x04", ".zip"),
        (b"7z\xbc\xaf\x27\x1c", ".7z"),
    )
    for signature, extension in signatures:
        if data.startswith(signature):
            return extension
    if data.startswith(b"RIFF") and len(data) >= 12:
        if data[8:12] == b"WEBP":
            return ".webp"
        if data[8:12] == b"WAVE":
            return ".wav"
    if data.startswith(b"#") and b"\0" not in data:
        return ".txt"
    return ".bin"


_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def _safe_known_path(name: str) -> Path | None:
    parts: list[str] = []
    for part in name.replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            return None
        cleaned = _INVALID_WINDOWS_CHARS.sub("_", part).rstrip(" .")
        if not cleaned:
            return None
        parts.append(cleaned)
    return Path(*parts) if parts else None


def _output_path(
    output_dir: Path,
    entry: TacEntry,
    sniff_data: bytes,
    known_name: str | None,
) -> Path:
    if known_name:
        relative = _safe_known_path(known_name)
        if relative is not None:
            return output_dir / relative
    return output_dir / f"{entry.full_hash:016x}{_guess_extension(sniff_data)}"


def _copy_exact(source: BinaryIO, target: BinaryIO, size: int) -> None:
    remaining = size
    while remaining:
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ValueError(f"unexpected EOF with {remaining} bytes left")
        target.write(chunk)
        remaining -= len(chunk)


def _write_atomic(target: Path, writer, overwrite: bool) -> str:
    if target.exists() and not overwrite:
        return "skipped"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".part")
    try:
        with temporary.open("wb") as output:
            writer(output)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return "ok"


def extract_entry(
    stream: BinaryIO,
    archive_size: int,
    header: TacHeader,
    entry: TacEntry,
    output_dir: Path,
    known_name: str | None,
    overwrite: bool,
) -> tuple[Path, str]:
    absolute_offset = header.data_base + entry.data_offset
    end_offset = absolute_offset + entry.stored_size
    if absolute_offset < header.data_base or end_offset > archive_size:
        raise ValueError("entry payload extends beyond the archive")

    stream.seek(absolute_offset)
    if entry.mode == 0:
        encrypted_prefix = _read_exact(stream, entry.secure_size)
        key = f"{entry.full_hash:d}_tlib_secure_".encode("ascii")
        prefix = tlib_blowfish(encrypted_prefix, key, decrypt=True)
        remaining = entry.stored_size - entry.secure_size
        peek = _read_exact(stream, min(64 - min(64, len(prefix)), remaining))
        remaining -= len(peek)
        target = _output_path(output_dir, entry, prefix + peek, known_name)

        def write_mode0(output: BinaryIO) -> None:
            output.write(prefix)
            output.write(peek)
            _copy_exact(stream, output, remaining)

        return target, _write_atomic(target, write_mode0, overwrite)

    if entry.mode == 1:
        compressed = _read_exact(stream, entry.stored_size)
        decoded = engine_decompress(compressed, entry.size_field)
        target = _output_path(output_dir, entry, decoded[:64], known_name)
        return target, _write_atomic(target, lambda output: output.write(decoded), overwrite)

    raise ValueError(f"unsupported entry mode {entry.mode}")


def unpack_archive(args: argparse.Namespace) -> int:
    archive_path: Path = args.archive.resolve()
    archive_size = archive_path.stat().st_size
    with archive_path.open("rb") as stream:
        header = read_header(stream, archive_size)
        entries = read_index(stream, header)

        mode_counts: dict[int, int] = {}
        for entry in entries:
            mode_counts[entry.mode] = mode_counts.get(entry.mode, 0) + 1
        print(
            f"TArc{header.version}: files={header.file_count}, "
            f"buckets={header.bucket_count}, index={header.index_size}, "
            f"seed={header.hash_seed}, data_base={header.data_base}"
        )
        print(
            "modes: "
            + ", ".join(f"{mode}={count}" for mode, count in sorted(mode_counts.items()))
        )
        if args.list_only:
            return 0

        output_dir: Path = args.output.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        known_names = _load_known_names(args.name_list, header.hash_seed)
        matched_names = sum(entry.full_hash in known_names for entry in entries)
        if args.name_list:
            print(f"known names matched: {matched_names}/{len(entries)}")

        manifest_path = output_dir / "manifest.csv"
        ok_count = skipped_count = error_count = 0
        with manifest_path.open("w", encoding="utf-8-sig", newline="") as manifest:
            writer = csv.writer(manifest)
            writer.writerow(
                (
                    "index",
                    "hash",
                    "mode",
                    "size_field",
                    "data_offset",
                    "stored_size",
                    "decoded_size",
                    "known_name",
                    "output",
                    "status",
                    "error",
                )
            )
            for position, entry in enumerate(entries, 1):
                known_name = known_names.get(entry.full_hash)
                target: Path | None = None
                status = "error"
                error = ""
                try:
                    target, status = extract_entry(
                        stream,
                        archive_size,
                        header,
                        entry,
                        output_dir,
                        known_name,
                        args.overwrite,
                    )
                    if status == "ok":
                        ok_count += 1
                    else:
                        skipped_count += 1
                except Exception as exc:
                    error_count += 1
                    error = str(exc)
                    if args.fail_fast:
                        raise
                writer.writerow(
                    (
                        entry.index,
                        f"{entry.full_hash:016x}",
                        entry.mode,
                        entry.size_field,
                        entry.data_offset,
                        entry.stored_size,
                        entry.decoded_size,
                        known_name or "",
                        str(target.relative_to(output_dir)) if target else "",
                        status,
                        error,
                    )
                )
                if position % 100 == 0 or position == len(entries):
                    print(
                        f"[{position}/{len(entries)}] ok={ok_count} "
                        f"skipped={skipped_count} errors={error_count}"
                    )

    print(f"manifest: {manifest_path}")
    return 1 if error_count else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unpack TLib TArc1.00/TArc1.10 .tac archives"
    )
    parser.add_argument("archive", type=Path, help="input .tac file")
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("unpacked"), help="output directory"
    )
    parser.add_argument(
        "--name-list",
        type=Path,
        help="optional UTF-8 file containing one original archive path per line",
    )
    parser.add_argument("--list-only", action="store_true", help="only validate and list metadata")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing output files")
    parser.add_argument("--fail-fast", action="store_true", help="stop on the first bad entry")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return unpack_archive(args)
    except (OSError, ValueError, zlib.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
