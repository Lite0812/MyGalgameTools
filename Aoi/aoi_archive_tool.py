from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


def u32(value: int) -> int:
    return value & 0xFFFFFFFF


def read_c_string(data: bytes, encoding: str) -> str:
    raw = data.split(b"\x00", 1)[0]
    return raw.decode(encoding)


def read_utf16le_zstring(data: bytes) -> str:
    chars = []
    for i in range(0, len(data), 2):
        code_unit = data[i : i + 2]
        if len(code_unit) < 2 or code_unit == b"\x00\x00":
            break
        chars.append(code_unit)
    return b"".join(chars).decode("utf-16le")


def key_from_offset(offset: int) -> int:
    base = 0x5CC8E9D7
    shift0 = offset & 0xF
    v1 = u32(
        offset
        - base
        + (0xA3371629 >> (shift0 + 1))
        - u32(base << (31 - shift0))
    )

    shift1 = (offset >> 4) & 0xF
    v3 = u32(v1 << (31 - shift1))
    v4 = v1 >> (shift1 + 1)
    temp = u32(offset - base + v3 + v4)

    shift2 = (offset >> 8) & 0xF
    v5 = u32(offset - base + u32(temp << (31 - shift2)) + (temp >> (shift2 + 1)))

    shift3 = (offset >> 12) & 0xF
    v6 = u32(offset - base + u32(v5 << (31 - shift3)) + (v5 >> (shift3 + 1)))

    shift4 = (offset >> 16) & 0xF
    v7 = u32(offset - base + u32(v6 << (31 - shift4)) + (v6 >> (shift4 + 1)))

    shift5 = (offset >> 20) & 0xF
    temp2 = u32(offset - base + u32(v7 << (31 - shift5)) + (v7 >> (shift5 + 1)))
    shift6 = (offset >> 24) & 0xF
    v9 = u32(offset - base + u32(temp2 << (31 - shift6)) + (temp2 >> (shift6 + 1)))

    shift7 = offset >> 28
    key = u32(offset - base + u32(v9 << (31 - shift7)) + (v9 >> (shift7 + 1))) >> shift0
    return key & 0xFF


def crypt_with_offset(data: bytes, start_offset: int) -> bytes:
    out = bytearray(data)
    offset = start_offset
    for i in range(len(out)):
        out[i] ^= key_from_offset(offset)
        offset += 1
    return bytes(out)


def xor_5f(data: bytes) -> bytes:
    return bytes(b ^ 0x5F for b in data)


VFS_SIGNATURES = {0x4656, 0x4C56}
VFS_V1_ENTRY_NAME_SIZE = 0x13
VFS_V1_ENTRY_SIZE = 0x20
VFS_V1_VERSION = 0x0101
VFS_V2_ENTRY_SIZE = 0x17
VFS_V2_VERSION = 0x0200
VFS_META_NAME = "__aoi_vfs__.json"
AOIMY01_META_NAME = "__aoi_aoimy01__.json"
AOIBX9_META_NAME = "__aoi_aoibx9__.json"


def iter_files(input_dir: Path):
    files = [path for path in input_dir.rglob("*") if path.is_file()]
    files.sort(key=lambda p: str(p.relative_to(input_dir)).lower())
    return files


def normalize_archive_name(path: Path, input_dir: Path) -> str:
    return str(path.relative_to(input_dir)).replace("/", "\\")


def move_pro_txt_first(entries: list[dict]) -> None:
    for index, entry in enumerate(entries):
        if entry["name"] == "pro.txt":
            entries[0], entries[index] = entries[index], entries[0]
            return


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_vfs_meta(input_dir: Path):
    meta_path = input_dir / VFS_META_NAME
    if not meta_path.is_file():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def load_aoimy01_meta(input_dir: Path):
    meta_path = input_dir / AOIMY01_META_NAME
    if not meta_path.is_file():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def load_aoibx9_meta(input_dir: Path):
    meta_path = input_dir / AOIBX9_META_NAME
    if not meta_path.is_file():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def read_vfs_header(vfs_path: Path) -> tuple[int, int, int, int, int, int]:
    with vfs_path.open("rb") as f:
        header = f.read(0x10)
    if len(header) < 0x10:
        raise RuntimeError(f"VFS 文件头过短: {vfs_path}")
    signature, version, count, entry_size = struct.unpack_from("<4H", header, 0)
    index_size, total_size = struct.unpack_from("<2I", header, 8)
    if signature not in VFS_SIGNATURES:
        raise RuntimeError(f"不是有效的 VFS 文件: {vfs_path}")
    if total_size != vfs_path.stat().st_size:
        raise RuntimeError(f"VFS 文件大小字段异常: {vfs_path}")
    return signature, version, count, entry_size, index_size, total_size


def extract_vfs(vfs_path: Path, output_dir: Path) -> int:
    signature, version, count, entry_size, _, _ = read_vfs_header(vfs_path)
    with vfs_path.open("rb") as f:
        data = f.read()

    index_offset = 0x10
    entries: list[dict[str, object]] = []
    if version >= 0x0200:
        filenames_offset = index_offset + entry_size * count
        if filenames_offset + 8 > len(data):
            raise RuntimeError(f"VFS 文件索引越界: {vfs_path}")
        filenames_length = struct.unpack_from("<I", data, filenames_offset)[0]
        filenames_reserved = data[filenames_offset + 4 : filenames_offset + 8]
        text_start = filenames_offset + 8
        text_end = text_start + filenames_length * 2
        if text_end > len(data):
            raise RuntimeError(f"VFS 文件名表越界: {vfs_path}")
        filenames = data[text_start:text_end].decode("utf-16le")
        for _ in range(count):
            if index_offset + entry_size > len(data):
                raise RuntimeError(f"VFS 目录项越界: {vfs_path}")
            name_offset = struct.unpack_from("<I", data, index_offset)[0]
            if not 0 <= name_offset < len(filenames):
                raise RuntimeError(f"VFS 文件名偏移无效: {vfs_path}")
            name_end = filenames.find("\x00", name_offset)
            if name_end == -1:
                name_end = len(filenames)
            name = filenames[name_offset:name_end]
            if not name:
                raise RuntimeError(f"VFS 存在空文件名: {vfs_path}")
            offset = struct.unpack_from("<I", data, index_offset + 0x0A)[0]
            size = struct.unpack_from("<I", data, index_offset + 0x0E)[0]
            unpacked_size = struct.unpack_from("<I", data, index_offset + 0x12)[0]
            is_packed = data[index_offset + 0x16]
            if is_packed:
                raise RuntimeError(f"暂不支持解包压缩的 VFS 文件: {vfs_path}")
            if size != unpacked_size:
                raise RuntimeError(f"VFS 大小字段不一致: {name}")
            if offset + size > len(data):
                raise RuntimeError(f"VFS 文件数据越界: {name}")
            entries.append(
                {
                    "name": name,
                    "offset": offset,
                    "size": size,
                    "raw_entry_hex": data[index_offset : index_offset + entry_size].hex(),
                }
            )
            index_offset += entry_size
    else:
        for _ in range(count):
            if index_offset + entry_size > len(data):
                raise RuntimeError(f"VFS 目录项越界: {vfs_path}")
            raw_name = data[index_offset : index_offset + VFS_V1_ENTRY_NAME_SIZE]
            name = raw_name.split(b"\x00", 1)[0].decode("cp932")
            offset = struct.unpack_from("<I", data, index_offset + 0x13)[0]
            size = struct.unpack_from("<I", data, index_offset + 0x17)[0]
            unpacked_size = struct.unpack_from("<I", data, index_offset + 0x1B)[0]
            is_packed = data[index_offset + 0x1F]
            if is_packed:
                raise RuntimeError(f"暂不支持解包压缩的 VFS 文件: {vfs_path}")
            if size != unpacked_size:
                raise RuntimeError(f"VFS 大小字段不一致: {name}")
            if offset + size > len(data):
                raise RuntimeError(f"VFS 文件数据越界: {name}")
            entries.append(
                {
                    "name": name,
                    "offset": offset,
                    "size": size,
                    "raw_name_hex": raw_name.hex(),
                }
            )
            index_offset += entry_size

    for entry in entries:
        name = entry["name"]
        offset = entry["offset"]
        size = entry["size"]
        print(f"正在解包文件: {name}")
        out_path = output_dir / Path(name)
        ensure_parent(out_path)
        out_path.write_bytes(data[offset : offset + size])

    meta = {
        "format": "VFS",
        "signature": signature,
        "version": version,
        "entry_size": entry_size,
        "entries": entries,
    }
    if version >= VFS_V2_VERSION:
        meta["filenames_reserved_hex"] = filenames_reserved.hex()
    (output_dir / VFS_META_NAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    version_text = f"VFS 0x{version:04X}"
    print(f"解包完成，版本 {version_text}，共输出 {len(entries)} 个文件。")
    return len(entries)


def create_vfs(input_dir: Path, vfs_path: Path) -> int:
    meta = load_vfs_meta(input_dir)
    signature = int(meta.get("signature", 0x4656)) if meta else 0x4656
    version = int(meta.get("version", VFS_V1_VERSION)) if meta else VFS_V1_VERSION
    default_entry_size = VFS_V2_ENTRY_SIZE if version >= VFS_V2_VERSION else VFS_V1_ENTRY_SIZE
    entry_size = int(meta.get("entry_size", default_entry_size)) if meta else default_entry_size
    is_v2 = version >= VFS_V2_VERSION
    if is_v2 and entry_size != VFS_V2_ENTRY_SIZE:
        raise RuntimeError(f"暂不支持的 VFS V2 目录项大小: {entry_size}")
    if not is_v2 and entry_size != VFS_V1_ENTRY_SIZE:
        raise RuntimeError(f"暂不支持的 VFS V1 目录项大小: {entry_size}")

    entries = []
    for file_path in iter_files(input_dir):
        if file_path.name == VFS_META_NAME:
            continue
        relative_name = normalize_archive_name(file_path, input_dir)
        if "\\" in relative_name or "/" in relative_name:
            raise RuntimeError(f"VFS 不支持子目录: {relative_name}")
        name_bytes = relative_name.encode("utf-16le" if is_v2 else "cp932")
        if not is_v2 and len(name_bytes) > VFS_V1_ENTRY_NAME_SIZE:
            raise RuntimeError(f"文件名过长: {relative_name}")
        size = file_path.stat().st_size
        entries.append(
            {
                "name": relative_name,
                "name_bytes": name_bytes,
                "path": file_path,
                "size": size,
            }
        )

    if meta:
        meta_entries = meta.get("entries", [])
        file_map = {entry["name"]: entry for entry in entries}
        meta_name_set = {entry["name"] for entry in meta_entries}
        file_name_set = set(file_map)
        if meta_name_set != file_name_set:
            missing = sorted(meta_name_set - file_name_set)
            extra = sorted(file_name_set - meta_name_set)
            detail = []
            if missing:
                detail.append(f"缺少文件: {', '.join(missing)}")
            if extra:
                detail.append(f"多出文件: {', '.join(extra)}")
            raise RuntimeError("VFS 元数据与目录内容不一致，无法无损封包。 " + "；".join(detail))
        merged_entries = []
        for meta_entry in meta_entries:
            merged = dict(file_map[meta_entry["name"]])
            if "raw_name_hex" in meta_entry:
                merged["raw_name_hex"] = meta_entry["raw_name_hex"]
            if "raw_entry_hex" in meta_entry:
                merged["raw_entry_hex"] = meta_entry["raw_entry_hex"]
            merged_entries.append(merged)
        entries = merged_entries

    if not entries:
        raise RuntimeError("没有找到文件.")

    filenames_data = bytearray()
    if is_v2:
        for entry in entries:
            entry["name_offset"] = len(filenames_data) // 2
            filenames_data.extend(entry["name_bytes"])
            filenames_data.extend(b"\x00\x00")
        # VFS V2 stores the filename list as a double-NUL-terminated UTF-16 table.
        filenames_data.extend(b"\x00\x00")
        current_offset = 0x10 + len(entries) * entry_size + 8 + len(filenames_data)
    else:
        current_offset = 0x10 + len(entries) * entry_size
    for entry in entries:
        entry["offset"] = current_offset
        current_offset += entry["size"]

    with vfs_path.open("wb") as f:
        f.write(struct.pack("<H", signature))
        f.write(struct.pack("<H", version))
        f.write(struct.pack("<H", len(entries)))
        f.write(struct.pack("<H", entry_size))
        f.write(struct.pack("<I", len(entries) * entry_size))
        f.write(struct.pack("<I", current_offset))

        if is_v2:
            for entry in entries:
                raw_entry_hex = entry.get("raw_entry_hex") if meta else None
                if raw_entry_hex:
                    raw_entry = bytearray.fromhex(raw_entry_hex)
                    if len(raw_entry) != entry_size:
                        raise RuntimeError(f"VFS V2 元数据中的目录项长度异常: {entry['name']}")
                else:
                    raw_entry = bytearray(entry_size)
                struct.pack_into("<I", raw_entry, 0x00, entry["name_offset"])
                struct.pack_into("<I", raw_entry, 0x0A, entry["offset"])
                struct.pack_into("<I", raw_entry, 0x0E, entry["size"])
                struct.pack_into("<I", raw_entry, 0x12, entry["size"])
                raw_entry[0x16] = 0
                f.write(raw_entry)

            filenames_reserved_hex = meta.get("filenames_reserved_hex") if meta else None
            filenames_reserved = bytes.fromhex(filenames_reserved_hex) if filenames_reserved_hex else b"\x00" * 4
            if len(filenames_reserved) != 4:
                raise RuntimeError("VFS V2 元数据中的文件名表保留字段长度异常。")
            f.write(struct.pack("<I", len(filenames_data) // 2))
            f.write(filenames_reserved)
            f.write(filenames_data)
        else:
            for entry in entries:
                raw_name_hex = entry.get("raw_name_hex") if meta else None
                if raw_name_hex:
                    raw_name = bytes.fromhex(raw_name_hex)
                    if len(raw_name) != VFS_V1_ENTRY_NAME_SIZE:
                        raise RuntimeError(f"VFS 元数据中的文件名字段长度异常: {entry['name']}")
                    encoded_name = raw_name.split(b"\x00", 1)[0]
                    if encoded_name != entry["name_bytes"]:
                        raise RuntimeError(f"VFS 元数据中的文件名与实际文件不一致: {entry['name']}")
                else:
                    raw_name = entry["name_bytes"].ljust(VFS_V1_ENTRY_NAME_SIZE, b"\x00")
                f.write(raw_name)
                f.write(struct.pack("<I", entry["offset"]))
                f.write(struct.pack("<I", entry["size"]))
                f.write(struct.pack("<I", entry["size"]))
                f.write(b"\x00")

        for entry in entries:
            print(f"正在封包文件: {entry['name']}")
            f.write(entry["path"].read_bytes())

    print(f"封包完成，共写入 {len(entries)} 个文件。")
    return len(entries)


def extract_aoimy01u(box_path: Path, output_dir: Path) -> int:
    with box_path.open("rb") as f:
        f.seek(16)
        file_count = struct.unpack(">I", f.read(4))[0]
        f.seek(4, 1)

        entries = []
        for _ in range(file_count):
            raw_name = f.read(32)
            name = read_utf16le_zstring(raw_name)
            offset = struct.unpack(">I", f.read(4))[0]
            size = struct.unpack(">I", f.read(4))[0]
            entries.append(
                {
                    "name": name,
                    "offset": offset,
                    "size": size,
                    "raw_name_hex": raw_name.hex(),
                }
            )

        for entry in entries:
            name = entry["name"]
            offset = entry["offset"]
            size = entry["size"]
            print(f"正在解包文件: {name}")
            f.seek(offset)
            data = crypt_with_offset(f.read(size), offset)
            out_path = output_dir / Path(name)
            ensure_parent(out_path)
            out_path.write_bytes(data)

    meta = {
        "format": "AOIMY01U",
        "entries": entries,
    }
    (output_dir / AOIMY01_META_NAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"解包完成，共输出 {len(entries)} 个文件。")
    return len(entries)


def create_aoimy01u(input_dir: Path, box_path: Path) -> int:
    meta = load_aoimy01_meta(input_dir)
    entries = []
    current_offset = 24

    for file_path in iter_files(input_dir):
        if file_path.name in {AOIMY01_META_NAME, VFS_META_NAME}:
            continue
        name = normalize_archive_name(file_path, input_dir)
        size = file_path.stat().st_size
        entries.append({"name": name, "name_bytes": name.encode("utf-16le"), "path": file_path, "size": size})

    if not entries:
        raise RuntimeError("没有找到文件.")

    if meta and meta.get("format") == "AOIMY01U":
        meta_entries = meta.get("entries", [])
        file_map = {entry["name"]: entry for entry in entries}
        meta_name_set = {entry["name"] for entry in meta_entries}
        file_name_set = set(file_map)
        if meta_name_set != file_name_set:
            missing = sorted(meta_name_set - file_name_set)
            extra = sorted(file_name_set - meta_name_set)
            detail = []
            if missing:
                detail.append(f"缺少文件: {', '.join(missing)}")
            if extra:
                detail.append(f"多出文件: {', '.join(extra)}")
            raise RuntimeError("AOIMY01 元数据与目录内容不一致，无法无损封包。 " + "；".join(detail))
        merged_entries = []
        for meta_entry in meta_entries:
            merged = dict(file_map[meta_entry["name"]])
            if "raw_name_hex" in meta_entry:
                merged["raw_name_hex"] = meta_entry["raw_name_hex"]
            merged_entries.append(merged)
        entries = merged_entries
    else:
        move_pro_txt_first(entries)
    current_offset += len(entries) * 0x28

    for entry in entries:
        entry["offset"] = current_offset
        current_offset += entry["size"]

    with box_path.open("wb") as f:
        # AOIMY01U reserves a UTF-16LE NUL terminator before the BE header fields.
        f.write("AOIMY01\0".encode("utf-16le"))
        f.write(struct.pack(">I", len(entries)))
        f.write(struct.pack("<I", 0))

        for entry in entries:
            raw_name_hex = entry.get("raw_name_hex") if meta else None
            name_bytes = entry["name_bytes"]
            if len(name_bytes) > 32:
                raise RuntimeError(f"文件名过长: {entry['name']}")
            if raw_name_hex:
                raw_name = bytes.fromhex(raw_name_hex)
                if len(raw_name) != 32:
                    raise RuntimeError(f"AOIMY01 元数据中的文件名字段长度异常: {entry['name']}")
                encoded_name = raw_name[: len(name_bytes)]
                if encoded_name != name_bytes:
                    raise RuntimeError(f"AOIMY01 元数据中的文件名与实际文件不一致: {entry['name']}")
            else:
                raw_name = name_bytes.ljust(32, b"\x00")
            f.write(raw_name)
            f.write(struct.pack(">I", entry["offset"]))
            f.write(struct.pack(">I", entry["size"]))

        for entry in entries:
            print(f"正在封包文件: {entry['name']}")
            data = entry["path"].read_bytes()
            f.write(crypt_with_offset(data, entry["offset"]))

    print(f"封包完成，共写入 {len(entries)} 个文件。")
    return len(entries)


def extract_aoimy01a(box_path: Path, output_dir: Path) -> int:
    with box_path.open("rb") as f:
        f.seek(8)
        file_count = struct.unpack(">I", f.read(4))[0]
        f.seek(4, 1)

        entries = []
        for _ in range(file_count):
            raw_name = f.read(12)
            name = read_c_string(raw_name, "cp932")
            raw_extra = f.read(4)
            offset = struct.unpack(">I", f.read(4))[0]
            size = struct.unpack(">I", f.read(4))[0]
            entries.append(
                {
                    "name": name,
                    "offset": offset,
                    "size": size,
                    "raw_name_hex": raw_name.hex(),
                    "raw_extra_hex": raw_extra.hex(),
                }
            )

        for entry in entries:
            name = entry["name"]
            offset = entry["offset"]
            size = entry["size"]
            print(f"正在解包文件: {name}")
            f.seek(offset)
            data = crypt_with_offset(f.read(size), offset)
            out_path = output_dir / Path(name)
            ensure_parent(out_path)
            out_path.write_bytes(data)

    meta = {
        "format": "AOIMY01A",
        "entries": entries,
    }
    (output_dir / AOIMY01_META_NAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"解包完成，共输出 {len(entries)} 个文件。")
    return len(entries)


def create_aoimy01a(input_dir: Path, box_path: Path) -> int:
    meta = load_aoimy01_meta(input_dir)
    entries = []
    current_offset = 16

    for file_path in iter_files(input_dir):
        if file_path.name in {AOIMY01_META_NAME, VFS_META_NAME}:
            continue
        name = normalize_archive_name(file_path, input_dir)
        size = file_path.stat().st_size
        entries.append({"name": name, "name_bytes": name.encode("cp932"), "path": file_path, "size": size})

    if not entries:
        raise RuntimeError("没有找到文件.")

    if meta and meta.get("format") == "AOIMY01A":
        meta_entries = meta.get("entries", [])
        file_map = {entry["name"]: entry for entry in entries}
        meta_name_set = {entry["name"] for entry in meta_entries}
        file_name_set = set(file_map)
        if meta_name_set != file_name_set:
            missing = sorted(meta_name_set - file_name_set)
            extra = sorted(file_name_set - meta_name_set)
            detail = []
            if missing:
                detail.append(f"缺少文件: {', '.join(missing)}")
            if extra:
                detail.append(f"多出文件: {', '.join(extra)}")
            raise RuntimeError("AOIMY01 元数据与目录内容不一致，无法无损封包。 " + "；".join(detail))
        merged_entries = []
        for meta_entry in meta_entries:
            merged = dict(file_map[meta_entry["name"]])
            if "raw_name_hex" in meta_entry:
                merged["raw_name_hex"] = meta_entry["raw_name_hex"]
            if "raw_extra_hex" in meta_entry:
                merged["raw_extra_hex"] = meta_entry["raw_extra_hex"]
            merged_entries.append(merged)
        entries = merged_entries
    else:
        move_pro_txt_first(entries)
    current_offset += len(entries) * 0x18

    for entry in entries:
        entry["offset"] = current_offset
        current_offset += entry["size"]

    with box_path.open("wb") as f:
        f.write(b"AOIMY01\x00")
        f.write(struct.pack(">I", len(entries)))
        f.write(struct.pack("<I", 0))

        for entry in entries:
            raw_name_hex = entry.get("raw_name_hex") if meta else None
            raw_extra_hex = entry.get("raw_extra_hex") if meta else None
            name_bytes = entry["name_bytes"]
            if len(name_bytes) > 12:
                raise RuntimeError(f"文件名过长: {entry['name']}")
            if raw_name_hex:
                raw_name = bytes.fromhex(raw_name_hex)
                if len(raw_name) != 12:
                    raise RuntimeError(f"AOIMY01 元数据中的文件名字段长度异常: {entry['name']}")
                encoded_name = raw_name.split(b"\x00", 1)[0]
                if encoded_name != name_bytes:
                    raise RuntimeError(f"AOIMY01 元数据中的文件名与实际文件不一致: {entry['name']}")
            else:
                raw_name = name_bytes.ljust(12, b"\x00")
            if raw_extra_hex:
                raw_extra = bytes.fromhex(raw_extra_hex)
                if len(raw_extra) != 4:
                    raise RuntimeError(f"AOIMY01 元数据中的保留字段长度异常: {entry['name']}")
            else:
                raw_extra = struct.pack("<I", 1)
            f.write(raw_name)
            f.write(raw_extra)
            f.write(struct.pack(">I", entry["offset"]))
            f.write(struct.pack(">I", entry["size"]))

        for entry in entries:
            print(f"正在封包文件: {entry['name']}")
            data = entry["path"].read_bytes()
            f.write(crypt_with_offset(data, entry["offset"]))

    print(f"封包完成，共写入 {len(entries)} 个文件。")
    return len(entries)


def extract_aoibx9a(box_path: Path, output_dir: Path) -> int:
    with box_path.open("rb") as f:
        f.seek(8)
        file_count = struct.unpack("<I", f.read(4))[0]
        reserved = f.read(4)

        entries = []
        for _ in range(file_count):
            raw_name = f.read(16)
            name = read_c_string(raw_name, "cp932")
            offset = struct.unpack("<I", f.read(4))[0]
            size = struct.unpack("<I", f.read(4))[0]
            entries.append(
                {
                    "name": name,
                    "offset": offset,
                    "size": size,
                    "raw_name_hex": raw_name.hex(),
                }
            )

        for entry in entries:
            name = entry["name"]
            offset = entry["offset"]
            size = entry["size"]
            print(f"正在解包文件: {name}")
            f.seek(offset)
            data = xor_5f(f.read(size))
            out_path = output_dir / Path(name)
            ensure_parent(out_path)
            out_path.write_bytes(data)

    meta = {
        "format": "AOIBX9A",
        "reserved_hex": reserved.hex(),
        "entries": entries,
    }
    (output_dir / AOIBX9_META_NAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"解包完成，共输出 {len(entries)} 个文件。")
    return len(entries)


def create_aoibx9a(input_dir: Path, box_path: Path) -> int:
    meta = load_aoibx9_meta(input_dir)
    entries = []
    current_offset = 16

    for file_path in iter_files(input_dir):
        if file_path.name in {AOIBX9_META_NAME, AOIMY01_META_NAME, VFS_META_NAME}:
            continue
        name = normalize_archive_name(file_path, input_dir)
        size = file_path.stat().st_size
        entries.append({"name": name, "name_bytes": name.encode("cp932"), "path": file_path, "size": size})

    if not entries:
        raise RuntimeError("没有找到文件.")

    if meta and meta.get("format") == "AOIBX9A":
        meta_entries = meta.get("entries", [])
        file_map = {entry["name"]: entry for entry in entries}
        meta_name_set = {entry["name"] for entry in meta_entries}
        file_name_set = set(file_map)
        if meta_name_set != file_name_set:
            missing = sorted(meta_name_set - file_name_set)
            extra = sorted(file_name_set - meta_name_set)
            detail = []
            if missing:
                detail.append(f"缺少文件: {', '.join(missing)}")
            if extra:
                detail.append(f"多出文件: {', '.join(extra)}")
            raise RuntimeError("AOIBX9 元数据与目录内容不一致，无法无损封包。 " + "；".join(detail))
        merged_entries = []
        for meta_entry in meta_entries:
            merged = dict(file_map[meta_entry["name"]])
            if "raw_name_hex" in meta_entry:
                merged["raw_name_hex"] = meta_entry["raw_name_hex"]
            merged_entries.append(merged)
        entries = merged_entries
    else:
        move_pro_txt_first(entries)
    current_offset += len(entries) * 0x18

    for entry in entries:
        entry["offset"] = current_offset
        current_offset += entry["size"]

    with box_path.open("wb") as f:
        f.write(b"AOIBX9\x00\x00")
        f.write(struct.pack("<I", len(entries)))
        if meta and meta.get("format") == "AOIBX9A":
            reserved_hex = meta.get("reserved_hex", "00000000")
            reserved = bytes.fromhex(reserved_hex)
            if len(reserved) != 4:
                raise RuntimeError("AOIBX9 元数据中的保留字段长度异常。")
        else:
            reserved = struct.pack("<I", 0)
        f.write(reserved)

        for entry in entries:
            raw_name_hex = entry.get("raw_name_hex") if meta else None
            name_bytes = entry["name_bytes"]
            if len(name_bytes) > 16:
                raise RuntimeError(f"文件名过长: {entry['name']}")
            if raw_name_hex:
                raw_name = bytes.fromhex(raw_name_hex)
                if len(raw_name) != 16:
                    raise RuntimeError(f"AOIBX9 元数据中的文件名字段长度异常: {entry['name']}")
                encoded_name = raw_name.split(b"\x00", 1)[0]
                if encoded_name != name_bytes:
                    raise RuntimeError(f"AOIBX9 元数据中的文件名与实际文件不一致: {entry['name']}")
            else:
                raw_name = name_bytes.ljust(16, b"\x00")
            f.write(raw_name)
            f.write(struct.pack("<I", entry["offset"]))
            f.write(struct.pack("<I", entry["size"]))

        for entry in entries:
            print(f"正在封包文件: {entry['name']}")
            data = entry["path"].read_bytes()
            f.write(xor_5f(data))

    print(f"封包完成，共写入 {len(entries)} 个文件。")
    return len(entries)


def choose_extract_mode(box_path: Path, output_dir: Path) -> tuple[str, int]:
    with box_path.open("rb") as f:
        header = f.read(16)
    if len(header) >= 4:
        signature = struct.unpack_from("<H", header, 0)[0]
        if signature in VFS_SIGNATURES:
            return "VFS", extract_vfs(box_path, output_dir)
    if header.startswith(b"AOIMY01\x00"):
        return "AOIMY01/ANSI", extract_aoimy01a(box_path, output_dir)
    if header.startswith(b"AOIBX9\x00\x00"):
        return "AOIBX9/ANSI", extract_aoibx9a(box_path, output_dir)
    if read_utf16le_zstring(header) == "AOIMY01":
        return "AOIMY01/Unicode", extract_aoimy01u(box_path, output_dir)
    raise RuntimeError(f"不支持的封包格式: {box_path}")


def choose_pack_mode(mode: str, input_dir: Path, output_box_path: Path) -> tuple[str, int]:
    if mode == "1":
        return "AOIMY01/Unicode", create_aoimy01u(input_dir, output_box_path)
    if mode == "2":
        return "AOIMY01/ANSI", create_aoimy01a(input_dir, output_box_path)
    if mode == "3":
        return "AOIBX9/ANSI", create_aoibx9a(input_dir, output_box_path)
    if mode == "4":
        return "VFS", create_vfs(input_dir, output_box_path)
    raise RuntimeError(f"未知封包模式: {mode}")


def print_usage(program_name: str) -> None:
    print(
        "归档工具命令行用法:\n"
        f"  解包: {program_name} -e <封包文件> <输出目录>\n"
        f"  封包: {program_name} -p <模式> <输入目录> <输出封包文件>\n"
        "  模式: 1 - AOIMY01/Unicode, 2 - AOIMY01/ANSI, 3 - AOIBX9/ANSI, 4 - VFS"
    )


def main(argv: list[str]) -> int:
    try:
        if len(argv) < 2:
            print_usage(Path(argv[0]).name)
            return 1

        mode = argv[1]
        if mode == "-e":
            if len(argv) < 4:
                print_usage(Path(argv[0]).name)
                return 1
            box_path = Path(argv[2])
            output_dir = Path(argv[3])
            output_dir.mkdir(parents=True, exist_ok=True)
            archive_type, file_count = choose_extract_mode(box_path, output_dir)
            print(f"已识别封包类型: {archive_type}，共解出 {file_count} 个文件。")
            return 0

        if mode == "-p":
            if len(argv) < 5:
                print_usage(Path(argv[0]).name)
                return 1
            pack_mode = argv[2]
            input_dir = Path(argv[3])
            output_box_path = Path(argv[4])
            ensure_parent(output_box_path)
            archive_type, file_count = choose_pack_mode(pack_mode, input_dir, output_box_path)
            print(f"封包格式: {archive_type}，共写入 {file_count} 个文件。")
            return 0

        print(f"错误: 无效模式: {mode}")
        print_usage(Path(argv[0]).name)
        return 1
    except Exception as exc:
        print(f"错误: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
