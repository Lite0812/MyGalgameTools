from __future__ import annotations

import argparse
import json
import logging
import mmap
import os
import struct
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


XP3_SIGNATURE = b"XP3\r\n \n\x1a\x8bg\x01"
HEADER_SIZE = len(XP3_SIGNATURE) + 8
META_FILENAME = ".xp3meta.json"
CHUNK_HEADER = struct.Struct("<4sQ")
SEGMENT_STRUCT = struct.Struct("<IQQQ")
FILTER_STREAM_LEN = 61
FILTER_KEY_STREAM_LEN = 31
BUILTIN_EXTRANS_TOTALS = {
    "ballet-style": 0x8A9E3AAEED2BD8B4,
}
COMMON_BINARY_MAGIC = (
    b"\x89PNG\r\n\x1a\n",
    b"OggS",
    b"RIFF",
    b"BM",
    b"\xFF\xD8\xFF",
    b"PK\x03\x04",
)
LOGGER = logging.getLogger("xp3_tool")


class ProgressBar:
    def __init__(self, label: str, total: int, stream=None) -> None:
        self.label = label
        self.total = max(0, total)
        self.stream = stream or sys.stdout
        self.current = 0
        self.width = 28
        self.interactive = bool(getattr(self.stream, "isatty", lambda: False)())
        self.last_render = 0.0
        self.last_percent = -1
        self._lock = threading.Lock()

    def advance(self, step: int = 1) -> None:
        with self._lock:
            self.current = min(self.total, self.current + step)
            percent = 100 if self.total == 0 else int(self.current * 100 / self.total)
            now = time.monotonic()
            if self.interactive:
                if self.current == self.total or now - self.last_render >= 0.1:
                    self._render_interactive()
                    self.last_render = now
                return
            if self.current == self.total or percent >= self.last_percent + 10:
                self._render_line(percent)
                self.last_percent = percent

    def finish(self) -> None:
        with self._lock:
            self.current = self.total
            if self.interactive:
                self._render_interactive(final=True)
            else:
                self._render_line(100)

    def _render_interactive(self, final: bool = False) -> None:
        percent = 100.0 if self.total == 0 else (self.current / self.total) * 100.0
        filled = self.width if self.total == 0 else int(self.width * self.current / self.total)
        bar = "#" * filled + "-" * (self.width - filled)
        self.stream.write(
            f"\r{self.label} [{bar}] {self.current}/{self.total} {percent:6.2f}%"
        )
        if final:
            self.stream.write("\n")
        self.stream.flush()

    def _render_line(self, percent: int) -> None:
        self.stream.write(f"[{self.label}] {self.current}/{self.total} ({percent}%)\n")
        self.stream.flush()


@dataclass(slots=True)
class ExtransContext:
    profile: str
    total: int
    stream: bytes


@dataclass(slots=True)
class PackedFile:
    name: str
    info_flags: int
    original_data: bytes
    stored_data: bytes
    compressed: bool


@dataclass(slots=True)
class UnpackedFile:
    entry: XP3Entry
    data: bytes


@dataclass(slots=True)
class XP3Segment:
    offset: int
    original_size: int
    archived_size: int
    compressed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "offset": self.offset,
            "original_size": self.original_size,
            "archived_size": self.archived_size,
            "compressed": self.compressed,
        }


@dataclass(slots=True)
class XP3Entry:
    name: str
    info_flags: int
    original_size: int
    archived_size: int
    adler32: int | None
    segments: list[XP3Segment]

    @property
    def compressed(self) -> bool:
        return bool(self.segments) and all(segment.compressed for segment in self.segments)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "info_flags": self.info_flags,
            "original_size": self.original_size,
            "archived_size": self.archived_size,
            "adler32": self.adler32,
            "compressed": self.compressed,
            "segment_count": len(self.segments),
            "segments": [segment.to_dict() for segment in self.segments],
        }


@dataclass(slots=True)
class XP3Archive:
    path: Path
    xp3_offset: int
    index_offset: int
    index_compressed: bool
    entries: list[XP3Entry]

    def read_entry(self, fp, entry: XP3Entry) -> bytes:
        parts: list[bytes] = []
        for segment in entry.segments:
            fp.seek(self.xp3_offset + segment.offset)
            data = read_exact(fp, segment.archived_size)
            if segment.compressed:
                data = zlib.decompress(data)
            if len(data) != segment.original_size:
                raise ValueError(
                    f"segment size mismatch for {entry.name}: "
                    f"expected {segment.original_size}, got {len(data)}"
                )
            parts.append(data)

        merged = b"".join(parts)
        if len(merged) != entry.original_size:
            raise ValueError(
                f"file size mismatch for {entry.name}: "
                f"expected {entry.original_size}, got {len(merged)}"
            )
        return merged


def u32(value: int) -> int:
    return value & 0xFFFFFFFF


def shrd32(low: int, high: int, shift: int) -> int:
    return u32((low >> shift) | ((high << (32 - shift)) & 0xFFFFFFFF))


def permute32(value: int) -> int:
    value = u32(value)
    return u32(
        (8 * ((value & 0xF) | ((value & 0xFFFFFFF0) << 14)))
        | (
            (
                value & 0x18000
                | ((value & 0x1F00000 | ((value & 0xE0000 | ((value >> 1) & 0x7F000000)) >> 13)) >> 3)
            )
            >> 1
        )
    )


def build_total_stream(total: int) -> bytes:
    low = total & 0xFFFFFFFF
    high = (total >> 32) & 0x1FFFFFFF
    stream = bytearray(FILTER_STREAM_LEN)
    for index in range(FILTER_STREAM_LEN):
        byte = low & 0xFF
        stream[index] = byte
        low = shrd32(low, high, 8)
        high = u32((high >> 8) | (byte << 21))
    return bytes(stream)


def build_extrans_context(profile: str) -> ExtransContext:
    total = BUILTIN_EXTRANS_TOTALS[profile]
    return ExtransContext(profile=profile, total=total, stream=build_total_stream(total))


def resolve_extrans_context(profile: str | None) -> ExtransContext | None:
    if profile is None:
        return None
    return build_extrans_context(profile)


def default_filter_profile() -> str | None:
    return "ballet-style" if "ballet-style" in BUILTIN_EXTRANS_TOTALS else None


def build_key_stream(key: int) -> bytes:
    state = key & 0x7FFFFFFF
    stream = bytearray(FILTER_KEY_STREAM_LEN)
    for index in range(FILTER_KEY_STREAM_LEN):
        stream[index] = state & 0xFF
        state = u32((state >> 8) | ((state & 0xFF) << 23))
    return bytes(stream)


def apply_extrans_filter(data: bytes, key: int, context: ExtransContext, offset: int = 0) -> bytes:
    key_stream = build_key_stream(key)
    output = bytearray(data)
    for index in range(len(output)):
        absolute = offset + index
        output[index] ^= key_stream[absolute % FILTER_KEY_STREAM_LEN]
        output[index] = (output[index] + context.stream[absolute % FILTER_STREAM_LEN]) & 0xFF
    return bytes(output)


def reverse_extrans_filter(data: bytes, key: int, context: ExtransContext, offset: int = 0) -> bytes:
    key_stream = build_key_stream(key)
    output = bytearray(data)
    for index in range(len(output)):
        absolute = offset + index
        output[index] = (output[index] - context.stream[absolute % FILTER_STREAM_LEN]) & 0xFF
        output[index] ^= key_stream[absolute % FILTER_KEY_STREAM_LEN]
    return bytes(output)


def read_exact(fp, size: int) -> bytes:
    data = fp.read(size)
    if len(data) != size:
        raise EOFError(f"expected {size} bytes, got {len(data)}")
    return data


def read_u64(fp) -> int:
    return struct.unpack("<Q", read_exact(fp, 8))[0]


def write_u64(fp, value: int) -> None:
    fp.write(struct.pack("<Q", value))


def iter_chunks(blob: bytes):
    offset = 0
    total = len(blob)
    while offset < total:
        if total - offset < CHUNK_HEADER.size:
            raise ValueError("truncated XP3 chunk header")
        tag, size = CHUNK_HEADER.unpack_from(blob, offset)
        offset += CHUNK_HEADER.size
        end = offset + size
        if end > total:
            raise ValueError(f"truncated XP3 chunk payload for {tag!r}")
        yield tag, blob[offset:end]
        offset = end


def parse_info_chunk(payload: bytes) -> tuple[int, int, int, str]:
    if len(payload) < 22:
        raise ValueError("info chunk is too short")
    info_flags, original_size, archived_size = struct.unpack_from("<IQQ", payload, 0)
    name_units = struct.unpack_from("<H", payload, 20)[0]
    name_size = name_units * 2
    name_data = payload[22 : 22 + name_size]
    if len(name_data) != name_size:
        raise ValueError("truncated XP3 file name")
    name = name_data.decode("utf-16le")
    return info_flags, original_size, archived_size, name


def parse_segments_chunk(payload: bytes) -> list[XP3Segment]:
    if len(payload) % SEGMENT_STRUCT.size:
        raise ValueError("segm chunk size is not aligned")
    segments: list[XP3Segment] = []
    for offset in range(0, len(payload), SEGMENT_STRUCT.size):
        flags, seg_offset, original_size, archived_size = SEGMENT_STRUCT.unpack_from(payload, offset)
        compression_mode = flags & 0x7
        if compression_mode not in (0, 1):
            raise ValueError(f"unsupported segment compression mode: {compression_mode}")
        segments.append(
            XP3Segment(
                offset=seg_offset,
                original_size=original_size,
                archived_size=archived_size,
                compressed=bool(compression_mode),
            )
        )
    return segments


def parse_file_record(payload: bytes) -> XP3Entry:
    info_payload: bytes | None = None
    segm_payload: bytes | None = None
    adlr_payload: bytes | None = None

    for tag, chunk_payload in iter_chunks(payload):
        if tag == b"info":
            info_payload = chunk_payload
        elif tag == b"segm":
            segm_payload = chunk_payload
        elif tag == b"adlr":
            adlr_payload = chunk_payload

    if info_payload is None or segm_payload is None:
        raise ValueError("XP3 file record is missing info or segm chunk")

    info_flags, original_size, archived_size, name = parse_info_chunk(info_payload)
    segments = parse_segments_chunk(segm_payload)
    adler32 = None
    if adlr_payload is not None:
        if len(adlr_payload) < 4:
            raise ValueError("adlr chunk is too short")
        adler32 = struct.unpack_from("<I", adlr_payload, 0)[0]

    return XP3Entry(
        name=name,
        info_flags=info_flags,
        original_size=original_size,
        archived_size=archived_size,
        adler32=adler32,
        segments=segments,
    )


def parse_index_blob(blob: bytes) -> list[XP3Entry]:
    entries: list[XP3Entry] = []
    for tag, payload in iter_chunks(blob):
        if tag != b"File":
            continue
        entries.append(parse_file_record(payload))
    return entries


def find_xp3_offset(fp) -> int:
    fp.seek(0)
    header = read_exact(fp, len(XP3_SIGNATURE))
    if header == XP3_SIGNATURE:
        return 0
    if header[:2] != b"MZ":
        raise ValueError("file is neither an XP3 archive nor an MZ-wrapped XP3")

    scan_start = 16
    step = 0x40000
    fp.seek(scan_start)
    base = scan_start
    signature_length = len(XP3_SIGNATURE)
    while True:
        block = fp.read(step)
        if not block:
            break
        limit = len(block) - signature_length + 1
        for offset in range(0, max(limit, 0), 16):
            if block[offset : offset + signature_length] == XP3_SIGNATURE:
                return base + offset
        base += len(block)
    raise ValueError("XP3 signature not found")


def open_xp3(path: Path) -> XP3Archive:
    with path.open("rb") as fp:
        xp3_offset = find_xp3_offset(fp)
        fp.seek(xp3_offset + len(XP3_SIGNATURE))
        index_offset = read_u64(fp)
        index_compressed = False
        entries: list[XP3Entry] = []

        current_offset = index_offset
        while True:
            fp.seek(xp3_offset + current_offset)
            flag = read_exact(fp, 1)[0]
            compression_mode = flag & 0x7
            has_next = bool(flag & 0x80)

            if compression_mode == 0:
                raw_size = read_u64(fp)
                index_blob = read_exact(fp, raw_size)
            elif compression_mode == 1:
                compressed_size = read_u64(fp)
                raw_size = read_u64(fp)
                index_blob = zlib.decompress(read_exact(fp, compressed_size))
                index_compressed = True
                if len(index_blob) != raw_size:
                    raise ValueError(
                        f"index size mismatch: expected {raw_size}, got {len(index_blob)}"
                    )
            else:
                raise ValueError(f"unsupported XP3 index compression mode: {compression_mode}")

            entries.extend(parse_index_blob(index_blob))

            if not has_next:
                break
            current_offset = read_u64(fp)

    return XP3Archive(
        path=path,
        xp3_offset=xp3_offset,
        index_offset=index_offset,
        index_compressed=index_compressed,
        entries=entries,
    )


def normalize_internal_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe archive path: {name}")
    return path.as_posix()


def output_path(root: Path, name: str) -> Path:
    normalized = normalize_internal_name(name)
    return root.joinpath(*PurePosixPath(normalized).parts)


def default_jobs() -> int:
    process_cpu_count = getattr(os, "process_cpu_count", None)
    if callable(process_cpu_count):
        cpu_count = process_cpu_count()
    else:
        cpu_count = os.cpu_count()
    if not isinstance(cpu_count, int) or cpu_count < 1:
        return 1
    return cpu_count


def configure_logging(verbose: bool, auto_mode: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO if auto_mode else logging.WARNING
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )


def is_likely_binary_magic(data: bytes) -> bool:
    return any(data.startswith(magic) for magic in COMMON_BINARY_MAGIC)


def looks_like_filtered_script(data: bytes) -> bool:
    if data.startswith(b"\xFE\xFE"):
        return True
    if is_likely_binary_magic(data):
        return True
    if not data or data[:1] not in b"*;[@":
        return False
    try:
        head = data[:256].decode("cp932")
    except UnicodeDecodeError:
        return False
    return "\n" in head or "\r" in head or "|" in head


def read_entry_from_blob(blob: mmap.mmap, archive: XP3Archive, entry: XP3Entry) -> bytes:
    parts: list[bytes] = []
    for segment in entry.segments:
        start = archive.xp3_offset + segment.offset
        end = start + segment.archived_size
        data = blob[start:end]
        if len(data) != segment.archived_size:
            raise ValueError(
                f"segment size mismatch for {entry.name}: "
                f"expected archived {segment.archived_size}, got {len(data)}"
            )
        if segment.compressed:
            data = zlib.decompress(data)
        if len(data) != segment.original_size:
            raise ValueError(
                f"segment size mismatch for {entry.name}: "
                f"expected {segment.original_size}, got {len(data)}"
            )
        parts.append(data)
    return b"".join(parts)


def manifest_path(root: Path) -> Path:
    return root / META_FILENAME


def write_manifest(root: Path, archive: XP3Archive, filter_profile: str | None = None) -> None:
    data = {
        "source": archive.path.name,
        "xp3_offset": archive.xp3_offset,
        "index_offset": archive.index_offset,
        "index_compressed": archive.index_compressed,
        "file_count": len(archive.entries),
        "files": [entry.to_dict() for entry in archive.entries],
    }
    if filter_profile is not None:
        data["filter_profile"] = filter_profile
    manifest_path(root).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_manifest(root: Path) -> dict[str, object] | None:
    path = manifest_path(root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_files(manifest: dict[str, object] | None) -> list[dict[str, object]]:
    if manifest is None:
        return []
    files = manifest.get("files")
    if not isinstance(files, list):
        return []
    return [entry for entry in files if isinstance(entry, dict)]


def manifest_filter_profile(manifest: dict[str, object] | None) -> str | None:
    if manifest is None:
        return None
    profile = manifest.get("filter_profile")
    if isinstance(profile, str) and profile in BUILTIN_EXTRANS_TOTALS:
        return profile
    return None


def resolve_unpack_filter_profile(mode: str, archive: XP3Archive, blob: mmap.mmap) -> str | None:
    if mode == "none":
        return None
    if mode == "auto":
        return default_filter_profile()
    return mode


def resolve_pack_filter_profile(mode: str, manifest: dict[str, object] | None, files: list[Path]) -> str | None:
    if mode == "none":
        return None
    if mode == "auto":
        return default_filter_profile()
    return mode


def read_and_filter_entry(
    blob: mmap.mmap,
    archive: XP3Archive,
    entry: XP3Entry,
    context: ExtransContext | None,
) -> UnpackedFile:
    data = read_entry_from_blob(blob, archive, entry)
    if context is not None and entry.adler32 is not None:
        data = apply_extrans_filter(data, entry.adler32, context)
    return UnpackedFile(entry=entry, data=data)


def prepare_packed_file(
    path: Path,
    input_dir: Path,
    manifest_entries: dict[str, dict[str, object]],
    file_mode_default: str,
    extrans_context: ExtransContext | None,
) -> PackedFile:
    relative_name = path.relative_to(input_dir).as_posix()
    manifest_entry = manifest_entries.get(relative_name)
    file_mode = choose_file_mode(file_mode_default, manifest_entry)
    info_flags_value = manifest_entry.get("info_flags", 0) if manifest_entry else 0
    info_flags = info_flags_value if isinstance(info_flags_value, int) else 0
    original_data = path.read_bytes()
    stored_source = original_data
    if extrans_context is not None:
        filter_key = zlib.adler32(original_data) & 0xFFFFFFFF
        stored_source = reverse_extrans_filter(original_data, filter_key, extrans_context)
    compressed, stored_data = maybe_compress(stored_source, file_mode)
    return PackedFile(
        name=relative_name,
        info_flags=info_flags,
        original_data=original_data,
        stored_data=stored_data,
        compressed=compressed,
    )


def choose_index_mode(mode: str, manifest: dict[str, object] | None) -> str:
    if mode == "metadata" and manifest is not None:
        return "compressed" if manifest.get("index_compressed") else "raw"
    if mode == "metadata":
        return "auto"
    return mode


def choose_file_mode(
    mode: str,
    manifest_entry: dict[str, object] | None,
) -> str:
    if mode == "metadata" and manifest_entry is not None:
        return "compressed" if manifest_entry.get("compressed") else "raw"
    if mode == "metadata":
        return "auto"
    return mode


def collect_input_files(root: Path) -> list[Path]:
    files = [path for path in root.rglob("*") if path.is_file() and path.name != META_FILENAME]
    return files


def sort_input_files(files: list[Path], root: Path, manifest: dict[str, object] | None) -> list[Path]:
    order_map: dict[str, int] = {}
    for index, entry in enumerate(manifest_files(manifest)):
        name = entry.get("name")
        if isinstance(name, str):
            order_map[name] = index

    def sort_key(path: Path):
        name = path.relative_to(root).as_posix()
        return (order_map.get(name, len(order_map)), name)

    return sorted(files, key=sort_key)


def entry_map_from_manifest(manifest: dict[str, object] | None) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for entry in manifest_files(manifest):
        name = entry.get("name")
        if isinstance(name, str):
            result[name] = entry
    return result


def maybe_compress(data: bytes, mode: str) -> tuple[bool, bytes]:
    if mode == "raw":
        return False, data
    compressed = zlib.compress(data, level=9)
    if mode == "compressed":
        return True, compressed
    if len(compressed) < len(data):
        return True, compressed
    return False, data


def make_chunk(tag: bytes, payload: bytes) -> bytes:
    return CHUNK_HEADER.pack(tag, len(payload)) + payload


def build_file_record(
    name: str,
    info_flags: int,
    segment_offset: int,
    original_data: bytes,
    stored_data: bytes,
    compressed: bool,
) -> bytes:
    encoded_name = name.encode("utf-16le")
    info_payload = struct.pack(
        "<IQQH",
        info_flags,
        len(original_data),
        len(stored_data),
        len(encoded_name) // 2,
    ) + encoded_name
    segm_payload = SEGMENT_STRUCT.pack(
        1 if compressed else 0,
        segment_offset,
        len(original_data),
        len(stored_data),
    )
    adlr_payload = struct.pack("<I", zlib.adler32(original_data) & 0xFFFFFFFF)

    file_payload = b"".join(
        (
            make_chunk(b"info", info_payload),
            make_chunk(b"segm", segm_payload),
            make_chunk(b"adlr", adlr_payload),
        )
    )
    return make_chunk(b"File", file_payload)


def print_listing(archive: XP3Archive) -> None:
    print(
        f"archive={archive.path.name} entries={len(archive.entries)} "
        f"xp3_offset={archive.xp3_offset} index_offset={archive.index_offset}"
    )
    for entry in archive.entries:
        print(
            f"{entry.name}\t{entry.original_size}\t{entry.archived_size}\t"
            f"segments={len(entry.segments)}\tcompressed={entry.compressed}"
        )


def command_list(args: argparse.Namespace) -> int:
    archive = open_xp3(Path(args.archive))
    print_listing(archive)
    return 0


def command_unpack(args: argparse.Namespace) -> int:
    archive_path = Path(args.archive)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    archive = open_xp3(archive_path)
    LOGGER.info("opening archive %s", archive_path)
    with archive_path.open("rb") as archive_fp, mmap.mmap(
        archive_fp.fileno(),
        0,
        access=mmap.ACCESS_READ,
    ) as archive_blob:
        filter_profile = resolve_unpack_filter_profile(args.filter_profile, archive, archive_blob)
        extrans_context = resolve_extrans_context(filter_profile)
        if extrans_context is not None:
            LOGGER.info("using filter profile %s", extrans_context.profile)
        LOGGER.info("unpacking %d files with %d worker(s)", len(archive.entries), args.jobs)
        progress = ProgressBar("unpacking", len(archive.entries))
        try:
            with ThreadPoolExecutor(max_workers=args.jobs) as executor:
                for unpacked in executor.map(
                    lambda entry: read_and_filter_entry(archive_blob, archive, entry, extrans_context),
                    archive.entries,
                ):
                    entry = unpacked.entry
                    target = output_path(output_dir, entry.name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(unpacked.data)
                    LOGGER.debug("unpacked %s (%d bytes)", entry.name, len(unpacked.data))
                    progress.advance()
        finally:
            progress.finish()

    if args.write_manifest:
        write_manifest(output_dir, archive, filter_profile=filter_profile)
        LOGGER.info("wrote manifest %s", manifest_path(output_dir))
    if extrans_context is not None:
        print(f"applied extrans filter profile {extrans_context.profile}")
    print(f"unpacked {len(archive.entries)} files to {output_dir}")
    return 0


def command_pack(args: argparse.Namespace) -> int:
    input_dir = Path(args.input)
    output_path_value = Path(args.output)
    manifest = load_manifest(input_dir)
    manifest_entries = entry_map_from_manifest(manifest)
    files = sort_input_files(collect_input_files(input_dir), input_dir, manifest)
    filter_profile = resolve_pack_filter_profile(args.filter_profile, manifest, files)
    extrans_context = resolve_extrans_context(filter_profile)

    index_mode = choose_index_mode(args.index_mode, manifest)
    index_records: list[bytes] = []
    LOGGER.info("packing %d files with %d worker(s)", len(files), args.jobs)
    if extrans_context is not None:
        LOGGER.info("using filter profile %s", extrans_context.profile)

    prepare_progress = ProgressBar("preparing", len(files))
    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            prepared_files = []
            for prepared in executor.map(
                lambda path: prepare_packed_file(
                    path,
                    input_dir,
                    manifest_entries,
                    args.file_mode,
                    extrans_context,
                ),
                files,
            ):
                prepared_files.append(prepared)
                LOGGER.debug(
                    "prepared %s (%d -> %d bytes, compressed=%s)",
                    prepared.name,
                    len(prepared.original_data),
                    len(prepared.stored_data),
                    prepared.compressed,
                )
                prepare_progress.advance()
    finally:
        prepare_progress.finish()

    with output_path_value.open("wb") as fp:
        fp.write(XP3_SIGNATURE)
        write_u64(fp, 0)

        write_progress = ProgressBar("packing", len(prepared_files))
        for prepared in prepared_files:
            segment_offset = fp.tell()
            fp.write(prepared.stored_data)
            index_records.append(
                build_file_record(
                    name=prepared.name,
                    info_flags=prepared.info_flags,
                    segment_offset=segment_offset,
                    original_data=prepared.original_data,
                    stored_data=prepared.stored_data,
                    compressed=prepared.compressed,
                )
            )
            LOGGER.debug(
                "packed %s (%d -> %d bytes, compressed=%s)",
                prepared.name,
                len(prepared.original_data),
                len(prepared.stored_data),
                prepared.compressed,
            )
            write_progress.advance()

        write_progress.finish()

        index_offset = fp.tell()
        index_blob = b"".join(index_records)
        compressed_index, stored_index = maybe_compress(index_blob, index_mode)
        flag = 1 if compressed_index else 0
        fp.write(bytes((flag,)))
        if compressed_index:
            write_u64(fp, len(stored_index))
            write_u64(fp, len(index_blob))
            fp.write(stored_index)
        else:
            write_u64(fp, len(index_blob))
            fp.write(index_blob)

        fp.seek(len(XP3_SIGNATURE))
        write_u64(fp, index_offset)

    print(f"packed {len(files)} files into {output_path_value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal XP3 packer and unpacker")
    parser.add_argument(
        "--jobs",
        type=int,
        default=default_jobs(),
        help="Worker thread count for pack/unpack operations",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show per-file logs during pack/unpack",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List archive entries")
    list_parser.add_argument("archive", help="Path to the XP3 archive")
    list_parser.set_defaults(func=command_list)

    unpack_parser = subparsers.add_parser("unpack", help="Extract an XP3 archive")
    unpack_parser.add_argument("archive", help="Path to the XP3 archive")
    unpack_parser.add_argument("output", help="Directory to write extracted files")
    unpack_parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Write .xp3meta.json alongside extracted files",
    )
    unpack_parser.add_argument(
        "--filter-profile",
        choices=("auto", "none", "ballet-style"),
        default="ballet-style",
        help="Built-in extraction filter profile to apply; defaults to ballet-style",
    )
    unpack_parser.set_defaults(func=command_unpack)

    pack_parser = subparsers.add_parser("pack", help="Build an XP3 archive from a directory")
    pack_parser.add_argument("input", help="Directory containing files to pack")
    pack_parser.add_argument("output", help="Archive path to write")
    pack_parser.add_argument(
        "--file-mode",
        choices=("metadata", "auto", "raw", "compressed"),
        default="metadata",
        help="How to choose per-file content compression",
    )
    pack_parser.add_argument(
        "--index-mode",
        choices=("metadata", "auto", "raw", "compressed"),
        default="metadata",
        help="How to choose index compression",
    )
    pack_parser.add_argument(
        "--filter-profile",
        choices=("auto", "none", "ballet-style"),
        default="ballet-style",
        help="Built-in repack filter profile to apply; defaults to ballet-style",
    )
    pack_parser.set_defaults(func=command_pack)

    return parser


def run_auto_mode(source: Path, jobs: int, verbose: bool) -> int:
    configure_logging(verbose, auto_mode=True)
    if source.is_dir():
        output = source.with_suffix(".xp3")
        LOGGER.info("auto mode: packing %s -> %s", source, output)
        args = argparse.Namespace(
            command="pack",
            input=str(source),
            output=str(output),
            file_mode="metadata",
            index_mode="metadata",
            filter_profile="ballet-style",
            jobs=jobs,
            verbose=verbose,
        )
        return command_pack(args)

    output = source.with_suffix("")
    LOGGER.info("auto mode: unpacking %s -> %s", source, output)
    args = argparse.Namespace(
        command="unpack",
        archive=str(source),
        output=str(output),
        write_manifest=False,
        filter_profile="ballet-style",
        jobs=jobs,
        verbose=verbose,
    )
    return command_unpack(args)


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    if raw_argv and raw_argv[0] not in {"list", "unpack", "pack", "-h", "--help"}:
        parser = argparse.ArgumentParser(description="Minimal XP3 packer and unpacker")
        parser.add_argument("source", help="Archive file or folder path")
        parser.add_argument("--jobs", type=int, default=default_jobs(), help="Worker thread count")
        parser.add_argument("--verbose", action="store_true", help="Show per-file logs")
        auto_args = parser.parse_args(raw_argv)
        if auto_args.jobs < 1:
            print("error: --jobs must be >= 1", file=sys.stderr)
            return 1
        source = Path(auto_args.source)
        if not source.exists():
            print(f"error: {source} does not exist", file=sys.stderr)
            return 1
        try:
            return run_auto_mode(source, auto_args.jobs, auto_args.verbose)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    if args.jobs < 1:
        print("error: --jobs must be >= 1", file=sys.stderr)
        return 1
    configure_logging(args.verbose)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())