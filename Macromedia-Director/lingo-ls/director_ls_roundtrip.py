"""Director CST/LS 辅助工具。

当前主要针对 D11_5（ProjectorRays 版本号 1150）样本验证。
二进制结构解释尽量直接对齐 ProjectorRays 源码中的实现：

- ProjectorRays-master/src/lingodec/script.h
- ProjectorRays-master/src/lingodec/script.cpp
- ProjectorRays-master/src/director/chunk.h
- ProjectorRays-master/src/director/chunk.cpp

已实现的能力：

- export：直接解析 CASt 脚本成员导出 .ls
- fix-names：修复 ProjectorRays 导出目录里的常见乱码文件名
- patch-script：只支持等长字符串字面量修改
- patch-literals：支持变长字符串字面量修改，但字面量数量和非字面量结构必须保持不变
"""

from __future__ import annotations

import argparse
import ast
import csv
import re
import sys
import struct
from pathlib import Path
from typing import TypedDict


# Lscr 头字段偏移，均相对 chunk payload 起点（已经跳过 8 字节 chunk 头）。
# 对应 ProjectorRays-master/src/lingodec/script.h 里的 Script 结构。
LSCR_TOTAL_LENGTH_OFFSET = 8
LSCR_TOTAL_LENGTH2_OFFSET = 12
LSCR_HANDLER_VECTORS_OFFSET_OFFSET = 52
LSCR_LITERALS_OFFSET_OFFSET = 80
LSCR_LITERALS_DATA_COUNT_OFFSET = 84
LSCR_LITERALS_DATA_OFFSET_OFFSET = 88
LSCR_LITERAL_ALIGNMENT = 2

# Director 5 及以上版本的 CASt 成员布局。
# 对应 ProjectorRays-master/src/director/chunk.cpp 里的 CastMemberChunk::read/write。
CAST_MEMBER_TYPE_OFFSET = 0
CAST_MEMBER_INFO_LEN_OFFSET = 4
CAST_MEMBER_SPECIFIC_DATA_LEN_OFFSET = 8
CAST_MEMBER_INFO_OFFSET = 12

# CastInfoChunk 是一个 ListChunk，header 固定为 20 字节。
# item 0 是 scriptSrcText，item 1 是 Pascal name。
CAST_INFO_DATA_OFFSET_OFFSET = 0
CAST_INFO_UNK1_OFFSET = 4
CAST_INFO_UNK2_OFFSET = 8
CAST_INFO_FLAGS_OFFSET = 12
CAST_INFO_SCRIPT_ID_OFFSET = 16
CAST_INFO_HEADER_SIZE = 20
CAST_INFO_SCRIPT_TEXT_INDEX = 0

# mmap 布局来自 ProjectorRays-master/src/director/chunk.cpp::MemoryMapChunk::read。
MMAP_DEFAULT_OFFSET = 44
MMAP_HEADER_SIZE = 24
MMAP_ENTRY_SIZE = 20
MMAP_USED_COUNT_OFFSET = 12
CAST_MEMBER_CHUNK_TYPES = (b"CASt", b"tSAC")
CAST_MAP_CHUNK_TYPES = (b"CAS*", b"*SAC")
CONFIG_CHUNK_TYPES = (b"DRCF", b"FCRD", b"VWCF", b"FCWV")
SCRIPT_MEMBER_TYPE = 11
LITERAL_TYPE_STRING = 1
LITERAL_TYPE_INT = 4
WINDOWS_FILENAME_TRANSLATION = str.maketrans({char: "_" for char in '<>:"/\\|?*'})


class CastInfoChunkData(TypedDict):
    dataOffset: int
    unk1: int
    unk2: int
    flags: int
    scriptId: int
    items: list[bytes]


class ScriptExportEntry(TypedDict):
    entryIndex: int
    memberId: int
    scriptId: int
    name: str
    scriptText: str


class ResolvedLsImportEntry(TypedDict):
    lsPath: Path
    editedText: str
    scriptExport: ScriptExportEntry
    lscrEntryIndex: int


def read_be_u16(blob: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from(">H", blob, offset)[0]


def read_be_u32(blob: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from(">I", blob, offset)[0]


def write_be_u32(blob: bytearray, offset: int, value: int) -> None:
    struct.pack_into(">I", blob, offset, value)


def normalize_director_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r")


def normalize_export_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_utf8_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def ascii_debug(text: str) -> str:
    return ascii(text)


def has_japanese_text(text: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff"
        or "\u4e00" <= char <= "\u9fff"
        or "\uff01" <= char <= "\uff60"
        for char in text
    )


def decode_director_text(raw: bytes) -> str:
    """尽量按 Director 常见编码把字节串解成文本。"""

    if not raw:
        return ""

    best_candidate = raw.decode("latin1", errors="replace")
    best_score = -10_000
    for encoding in ("utf-8", "cp932", "shift_jis", "latin1"):
        try:
            candidate = raw.decode(encoding)
        except UnicodeDecodeError:
            continue

        score = 0
        if encoding == "utf-8":
            score += 10
        if has_japanese_text(candidate):
            score += 100
        if "\ufffd" in candidate:
            score -= 100
        if any(ord(char) < 32 for char in candidate):
            score -= 100
        if score > best_score:
            best_candidate = candidate
            best_score = score

    return best_candidate


def decode_pascal_name(raw_item: bytes) -> str:
    """把 CastInfoChunk item 1 里的 Pascal 名称解成 Python 字符串。"""

    if not raw_item:
        return ""
    name_len = min(raw_item[0], len(raw_item) - 1)
    return decode_director_text(raw_item[1:1 + name_len]).strip()


def read_members_csv_entry_index(ls_path: Path) -> int | None:
    """从同目录 Members.csv 里读取精确的 entryIndex 映射。"""

    members_csv = ls_path.parent / "Members.csv"
    if not members_csv.is_file():
        return None

    with members_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("file_name") != ls_path.name:
                continue

            entry_index_text = (row.get("entry_index") or "").strip()
            if not entry_index_text:
                return None

            try:
                return int(entry_index_text)
            except ValueError:
                return None

    return None


def sanitize_filename_component(name: str) -> str:
    """清理 Windows 不允许的文件名字符。"""

    cleaned = name.translate(WINDOWS_FILENAME_TRANSLATION).strip().strip(".")
    return cleaned or "unnamed"


def csv_escape(value: str | int) -> str:
    text = str(value).replace('"', '""')
    return f'"{text}"'


def repair_mojibake_name(name: str) -> str:
    """修复常见的 UTF-8/Shift-JIS 被按 Latin-1 误解码后的文件名。"""

    try:
        raw = name.encode("latin1")
    except UnicodeEncodeError:
        return name

    if all(byte < 0x80 for byte in raw):
        return name

    for encoding in ("utf-8", "cp932", "shift_jis"):
        try:
            candidate = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if candidate == name:
            continue

        score = 0
        if has_japanese_text(candidate):
            score += 100
        if any(ord(char) < 32 for char in candidate):
            score -= 100
        if score > 0:
            return candidate

    return name


def fix_exported_names(root_dir: Path, dry_run: bool = False) -> int:
    if not root_dir.is_dir():
        raise FileNotFoundError(f"Directory does not exist: {root_dir}")

    renamed_count = 0
    paths = sorted(root_dir.rglob("*"), key=lambda path: len(path.parts), reverse=True)
    for path in paths:
        repaired_name = repair_mojibake_name(path.name)
        if repaired_name == path.name:
            continue

        target_path = path.with_name(repaired_name)
        if target_path.exists() and target_path != path:
            raise FileExistsError(f"Cannot rename {path} to {target_path}: target already exists.")

        print(f"RENAME {ascii_debug(str(path))} -> {ascii_debug(str(target_path))}")
        renamed_count += 1
        if not dry_run:
            path.rename(target_path)

    print(f"Renamed {renamed_count} paths under {root_dir}")
    return renamed_count


def extract_string_literals(text: str) -> list[str]:
    """提取 Lingo 源码里的双引号字符串字面量。"""

    literals: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != '"':
            index += 1
            continue

        index += 1
        current: list[str] = []
        while index < len(text):
            char = text[index]
            if char == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    current.append('"')
                    index += 2
                    continue
                literals.append("".join(current))
                index += 1
                break
            current.append(char)
            index += 1
        else:
            raise ValueError("Unterminated string literal in LS script.")
    return literals


def strip_string_literal_contents(text: str) -> str:
    """移除字面量内容，只保留脚本结构，用于比较非字面量部分是否变化。"""

    pieces: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != '"':
            pieces.append(text[index])
            index += 1
            continue

        pieces.append('"')
        index += 1
        while index < len(text):
            char = text[index]
            if char == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 2
                    continue
                pieces.append('"')
                index += 1
                break
            index += 1
        else:
            raise ValueError("Unterminated string literal in LS script.")
    return "".join(pieces)


def read_projectorrays_literal_dump(path: Path) -> dict:
    """读取 ProjectorRays 导出的 Lscr dump。

    这个 dump 不是严格 JSON，而是 Python 可以直接 literal_eval 的对象字面量。
    """

    return ast.literal_eval(path.read_text(encoding="utf-8"))


def parse_cast_info_chunk(info_bytes: bytes) -> CastInfoChunkData:
    """按 ProjectorRays 的 CastInfoChunk/ListChunk 规则解析 info 区。"""

    if len(info_bytes) < CAST_INFO_HEADER_SIZE:
        raise ValueError("CASt info 区长度不足，无法按 CastInfoChunk 解析。")

    data_offset = read_be_u32(info_bytes, CAST_INFO_DATA_OFFSET_OFFSET)
    if data_offset > len(info_bytes):
        raise ValueError("CASt info 的 dataOffset 超出范围。")

    offset_table_len = read_be_u16(info_bytes, data_offset)
    offset_table_base = data_offset + 2
    items_len_offset = offset_table_base + offset_table_len * 4
    if items_len_offset + 4 > len(info_bytes):
        raise ValueError("CASt info 的 offset table 超出范围。")

    offset_table = [
        read_be_u32(info_bytes, offset_table_base + index * 4)
        for index in range(offset_table_len)
    ]
    items_len = read_be_u32(info_bytes, items_len_offset)
    items_base = items_len_offset + 4
    if items_base + items_len > len(info_bytes):
        raise ValueError("CASt info 的 items 区超出范围。")

    items: list[bytes] = []
    for index, item_offset in enumerate(offset_table):
        next_offset = items_len if index == offset_table_len - 1 else offset_table[index + 1]
        items.append(info_bytes[items_base + item_offset: items_base + next_offset])

    return {
        "dataOffset": data_offset,
        "unk1": read_be_u32(info_bytes, CAST_INFO_UNK1_OFFSET),
        "unk2": read_be_u32(info_bytes, CAST_INFO_UNK2_OFFSET),
        "flags": read_be_u32(info_bytes, CAST_INFO_FLAGS_OFFSET),
        "scriptId": read_be_u32(info_bytes, CAST_INFO_SCRIPT_ID_OFFSET),
        "items": items,
    }


def build_cast_info_chunk(parsed_info: CastInfoChunkData, script_text_bytes: bytes) -> bytes:
    """按 ProjectorRays 的 ListChunk 写回规则重建 CastInfoChunk。"""

    items = list(parsed_info["items"])
    if not items:
        items = [b""]
    items[CAST_INFO_SCRIPT_TEXT_INDEX] = script_text_bytes

    offsets: list[int] = []
    current_offset = 0
    for item in items:
        offsets.append(current_offset)
        current_offset += len(item)

    info_bytes = bytearray()
    info_bytes += struct.pack(
        ">IIIII",
        int(parsed_info["dataOffset"]),
        int(parsed_info["unk1"]),
        int(parsed_info["unk2"]),
        int(parsed_info["flags"]),
        int(parsed_info["scriptId"]),
    )
    info_bytes += struct.pack(">H", len(items))
    for offset in offsets:
        info_bytes += struct.pack(">I", offset)
    info_bytes += struct.pack(">I", current_offset)
    for item in items:
        info_bytes += item
    return bytes(info_bytes)


def decode_lscr_string_bytes(value_bytes: bytes) -> str:
    """优先按 UTF-8 读取 Lscr 字符串，失败时再做兜底解码。"""

    try:
        return value_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return decode_director_text(value_bytes)


def extract_lscr_string_literals(lscr_dump: dict) -> list[str]:
    """从 Lscr dump 中提取字符串字面量，忽略 int 等非字符串条目。"""

    string_literals: list[str] = []
    for literal in lscr_dump["literals"]:
        literal_type = int(literal["type"])
        if literal_type != LITERAL_TYPE_STRING:
            continue

        value = literal.get("value", "")
        if not isinstance(value, str):
            raise ValueError("Lscr dump 中存在无法解码的字符串字面量。")

        try:
            string_literals.append(value.encode("latin1").decode("utf-8"))
        except UnicodeError:
            string_literals.append(value)

    return string_literals


def ordered_unique_literals(values: list[str]) -> list[str]:
    """按首次出现顺序去重，和当前样本的 Lscr literal 排列保持一致。"""

    unique_values: list[str] = []
    for value in values:
        if value not in unique_values:
            unique_values.append(value)
    return unique_values


def find_source_literal_subsequence_positions(
    source_literals: list[str],
    compiled_literals: list[str],
) -> list[int]:
    """按顺序把源码字面量映射到 Lscr literal 表中的对应位置。

    D11_5 样本里编译态有时会带额外的辅助字面量（例如 EMPTY 对应的空串），
    因此这里允许 source 是 compiled 的有序子序列，而不是强制两边完全相等。
    """

    positions: list[int] = []
    compiled_index = 0
    for source_literal in source_literals:
        while compiled_index < len(compiled_literals) and compiled_literals[compiled_index] != source_literal:
            compiled_index += 1
        if compiled_index >= len(compiled_literals):
            raise ValueError("当前脚本的源码字面量无法在 Lscr literal 表里按顺序对齐。")
        positions.append(compiled_index)
        compiled_index += 1

    return positions


def project_source_literals_to_compiled_literals(
    original_source_literals: list[str],
    edited_source_literals: list[str],
    compiled_literals: list[str],
) -> list[str]:
    """把源码字面量变化投影回去重后的 Lscr 字面量表。

    约束：如果源码里同一个原始字符串在多个位置复用，编辑后这些位置必须仍然保持一致，
    否则就需要改 bytecode 对 literal 索引的引用，当前工具不支持。
    """

    if len(original_source_literals) != len(edited_source_literals):
        raise ValueError("编辑后的源码字面量数量与原始脚本不一致。")

    compiled_by_source = ordered_unique_literals(original_source_literals)
    compiled_positions = find_source_literal_subsequence_positions(compiled_by_source, compiled_literals)

    edited_by_original: dict[str, str] = {}
    for original_literal, edited_literal in zip(original_source_literals, edited_source_literals):
        existing = edited_by_original.get(original_literal)
        if existing is None:
            edited_by_original[original_literal] = edited_literal
            continue
        if existing != edited_literal:
            raise ValueError(
                "检测到同一个原始字符串在不同位置被改成了不同内容。"
                f" 当前工具无法在不改 bytecode 引用的情况下处理这种修改: {original_literal!r}"
            )

    projected_literals = list(compiled_literals)
    for original_literal, compiled_index in zip(compiled_by_source, compiled_positions):
        projected_literals[compiled_index] = edited_by_original[original_literal]

    return projected_literals


def parse_script_identifier_from_ls_path(ls_path: Path) -> int:
    """从导出的 ls 文件名中提取前缀数字。"""

    match = re.match(r"^(\d+)", ls_path.stem)
    if not match:
        raise ValueError(
            "无法从 LS 文件名中提取脚本编号。请使用 export 导出的文件名格式，例如 1019_選択.ls。"
        )
    return int(match.group(1))


def find_script_export_for_ls_path(
    exports: list[ScriptExportEntry],
    ls_path: Path,
) -> ScriptExportEntry:
    """优先按 Members.csv 的 entryIndex，再按文件名前缀回查脚本。"""

    entry_index = read_members_csv_entry_index(ls_path)
    if entry_index is not None:
        entry_matches = [item for item in exports if item["entryIndex"] == entry_index]
        if len(entry_matches) == 1:
            return entry_matches[0]
        if len(entry_matches) > 1:
            raise ValueError(f"Members.csv 中的 entryIndex {entry_index} 对应多个记录，无法唯一定位。")

    return find_script_export_in_exports(exports, parse_script_identifier_from_ls_path(ls_path))


def find_script_export_by_identifier(blob: bytes, script_identifier: int) -> ScriptExportEntry:
    """按 memberId、scriptId 或 entryIndex 回查脚本成员。"""

    exports = collect_script_exports(blob)
    return find_script_export_in_exports(exports, script_identifier)


def find_script_export_in_exports(
    exports: list[ScriptExportEntry],
    script_identifier: int,
) -> ScriptExportEntry:
    """在已收集的脚本列表里按 memberId、scriptId 或 entryIndex 查找目标脚本。"""

    member_id_matches = [item for item in exports if item["memberId"] == script_identifier]
    if len(member_id_matches) == 1:
        return member_id_matches[0]
    if len(member_id_matches) > 1:
        raise ValueError(f"脚本编号 {script_identifier} 对应多个 memberId 记录，无法唯一定位。")

    script_id_matches = [item for item in exports if item["scriptId"] == script_identifier]
    if len(script_id_matches) == 1:
        return script_id_matches[0]
    if len(script_id_matches) > 1:
        raise ValueError(f"脚本编号 {script_identifier} 对应多个 scriptId 记录，无法唯一定位。")

    entry_matches = [item for item in exports if item["entryIndex"] == script_identifier]
    if len(entry_matches) == 1:
        return entry_matches[0]
    if len(entry_matches) > 1:
        raise ValueError(f"脚本编号 {script_identifier} 对应多个 entryIndex 记录，无法唯一定位。")

    raise ValueError(f"在 CST 中找不到编号为 {script_identifier} 的脚本成员。")


def find_lscr_entries_by_script_number(
    entries: list[dict[str, int | bytes]],
    blob: bytes,
    script_number: int,
) -> list[int]:
    """按 Lscr header 的 scriptNumber 收集候选块。"""

    matches: list[int] = []
    for index, entry in enumerate(entries):
        if entry["fourcc"] not in (b"Lscr", b"rcsL"):
            continue

        chunk_offset = int(entry["offset"])
        chunk_len = int(entry["len"])
        payload = blob[chunk_offset + 8:chunk_offset + 8 + chunk_len]
        if len(payload) < LSCR_LITERALS_DATA_OFFSET_OFFSET + 4:
            continue

        if read_be_u16(payload, 18) == script_number:
            matches.append(index)

    return matches


def find_lscr_entry_index_for_script(entries: list[dict[str, int | bytes]], blob: bytes, script_id: int) -> int:
    """按 scriptId 自动定位对应 Lscr。当前 D11_5 样本里 Lscr scriptNumber = scriptId - 1。"""

    candidate_numbers: list[int] = []
    if script_id > 0:
        candidate_numbers.append(script_id - 1)
        candidate_numbers.append(script_id)

    seen_numbers: set[int] = set()
    for script_number in candidate_numbers:
        if script_number in seen_numbers or script_number < 0:
            continue
        seen_numbers.add(script_number)

        matches = find_lscr_entries_by_script_number(entries, blob, script_number)
        if not matches:
            continue
        if len(matches) > 1:
            raise ValueError(
                f"scriptId={script_id} 对应的 scriptNumber={script_number} 命中多个 Lscr chunk，无法唯一定位。"
            )
        return matches[0]

    raise ValueError(f"找不到 scriptId={script_id} 对应的 Lscr chunk。")


def read_lscr_literal_dump_from_chunk(
    blob: bytes,
    entries: list[dict[str, int | bytes]],
    lscr_entry_index: int,
) -> dict:
    """直接从 CST 的 Lscr chunk 读取 literal table/data。"""

    entry = entries[lscr_entry_index]
    if entry["fourcc"] not in (b"Lscr", b"rcsL"):
        raise ValueError("指定的 chunk 不是 Lscr。")

    chunk_offset = int(entry["offset"])
    chunk_len = int(entry["len"])
    payload = blob[chunk_offset + 8:chunk_offset + 8 + chunk_len]
    if len(payload) < LSCR_LITERALS_DATA_OFFSET_OFFSET + 4:
        raise ValueError("Lscr payload 长度不足，无法读取 literal 信息。")

    literals_count = read_be_u16(payload, 78)
    literals_offset = read_be_u32(payload, LSCR_LITERALS_OFFSET_OFFSET)
    literals_data_count = read_be_u32(payload, LSCR_LITERALS_DATA_COUNT_OFFSET)
    literals_data_offset = read_be_u32(payload, LSCR_LITERALS_DATA_OFFSET_OFFSET)
    handler_vectors_offset = read_be_u32(payload, LSCR_HANDLER_VECTORS_OFFSET_OFFSET)

    if literals_data_offset > handler_vectors_offset or handler_vectors_offset > len(payload):
        raise ValueError("Lscr literals data 区范围异常。")

    literals: list[dict[str, object]] = []
    data_entries: list[tuple[int, int, int]] = []
    for index in range(literals_count):
        record_offset = literals_offset + index * 8
        literal_type = read_be_u32(payload, record_offset)
        literal_offset = read_be_u32(payload, record_offset + 4)
        literal_record: dict[str, object] = {
            "type": literal_type,
            "offset": literal_offset,
        }
        literals.append(literal_record)
        if literal_type != LITERAL_TYPE_INT:
            data_entries.append((index, literal_offset, literal_type))

    data_entries.sort(key=lambda item: item[1])
    data_end = handler_vectors_offset - literals_data_offset
    for item_index, (literal_index, literal_offset, literal_type) in enumerate(data_entries):
        next_offset = data_end
        if item_index + 1 < len(data_entries):
            next_offset = data_entries[item_index + 1][1]

        start = literals_data_offset + literal_offset
        end = literals_data_offset + next_offset
        raw_data = bytes(payload[start:end])
        literals[literal_index]["rawData"] = raw_data

        if literal_type == LITERAL_TYPE_STRING:
            string_length = read_be_u32(payload, start)
            value_start = start + 4
            value_end = value_start + max(string_length - 1, 0)
            value_bytes = bytes(payload[value_start:value_end])
            literals[literal_index]["value"] = decode_lscr_string_bytes(value_bytes)

    return {
        "entryIndex": lscr_entry_index,
        "literalsCount": literals_count,
        "literalsOffset": literals_offset,
        "literalsDataCount": literals_data_count,
        "literalsDataOffset": literals_data_offset,
        "handlerVectorsOffset": handler_vectors_offset,
        "literals": literals,
    }


def find_matching_lscr_entry_index(
    blob: bytes,
    entries: list[dict[str, int | bytes]],
    script_id: int,
    original_text: str,
) -> int:
    """优先按 scriptId，再按字符串字面量内容回查真正的 Lscr。"""

    original_literals = ordered_unique_literals(extract_string_literals(original_text))
    matches: list[int] = []
    checked: set[int] = set()

    try:
        preferred_index = find_lscr_entry_index_for_script(entries, blob, script_id)
        preferred_dump = read_lscr_literal_dump_from_chunk(blob, entries, preferred_index)
        checked.add(preferred_index)
        find_source_literal_subsequence_positions(
            original_literals,
            extract_lscr_string_literals(preferred_dump),
        )
        return preferred_index
    except ValueError:
        pass

    for index, entry in enumerate(entries):
        if index in checked:
            continue
        if entry["fourcc"] not in (b"Lscr", b"rcsL"):
            continue

        lscr_dump = read_lscr_literal_dump_from_chunk(blob, entries, index)
        try:
            find_source_literal_subsequence_positions(
                original_literals,
                extract_lscr_string_literals(lscr_dump),
            )
            matches.append(index)
        except ValueError:
            continue

    if not matches:
        raise ValueError("找不到与原始 LS 字符串字面量一致的 Lscr chunk。")
    if len(matches) > 1:
        raise ValueError(f"找到多个与原始 LS 匹配的 Lscr chunk: {matches[:10]}")
    return matches[0]


def resolve_ls_import_entries(blob: bytes, ls_files: list[Path]) -> list[ResolvedLsImportEntry]:
    """在原始 CST 上一次性解析出需要导回的脚本和对应 Lscr。"""

    exports = collect_script_exports(blob)
    mmap_offset, _mmap_len, chunk_count_used = find_mmap_chunk(blob)
    entries = parse_mmap_entries(blob, mmap_offset, chunk_count_used)

    resolved_entries: list[ResolvedLsImportEntry] = []
    for ls_file in ls_files:
        script_export = find_script_export_for_ls_path(exports, ls_file)
        edited_export_text = normalize_export_newlines(read_utf8_text(ls_file))
        if edited_export_text == script_export["scriptText"]:
            continue
        if script_export["scriptId"] <= 0:
            raise ValueError("目标脚本缺少有效的 scriptId，当前无法自动定位对应的 Lscr。")

        lscr_entry_index = find_matching_lscr_entry_index(
            blob,
            entries,
            script_export["scriptId"],
            normalize_director_newlines(script_export["scriptText"]),
        )
        resolved_entries.append(
            {
                "lsPath": ls_file,
                "editedText": edited_export_text,
                "scriptExport": script_export,
                "lscrEntryIndex": lscr_entry_index,
            }
        )

    return resolved_entries


def build_lscr_literals_region(lscr_dump: dict, edited_literals: list[str]) -> tuple[bytes, bytes]:
    """重建 Lscr 的 literals table 和 literals data 区。

    对应 ProjectorRays 的 LiteralStore::readRecord/readData：
    - record: type + data offset
    - data: length(uint32) + 字节串 + 结尾 0
    - 字面量数据区按 2 字节对齐
    """

    original_string_literals = extract_lscr_string_literals(lscr_dump)
    if len(edited_literals) != len(original_string_literals):
        raise ValueError(
            "Edited literal count does not match the Lscr dump. "
            f"Edited={len(edited_literals)}, dump={len(original_string_literals)}"
        )

    rebuilt_table = bytearray()
    rebuilt_data = bytearray()
    original_literals = lscr_dump["literals"]
    edited_index = 0
    for original_literal in original_literals:
        literal_type = int(original_literal["type"])

        if literal_type == LITERAL_TYPE_INT:
            rebuilt_table += struct.pack(">I", literal_type)
            rebuilt_table += struct.pack(">I", int(original_literal["offset"]))
            continue

        rebuilt_table += struct.pack(">I", literal_type)
        rebuilt_table += struct.pack(">I", len(rebuilt_data))

        if literal_type == LITERAL_TYPE_STRING:
            edited_literal = edited_literals[edited_index]
            edited_index += 1
            value_bytes = edited_literal.encode("utf-8")
            rebuilt_data += struct.pack(">I", len(value_bytes) + 1)
            rebuilt_data += value_bytes + b"\x00"
            if len(rebuilt_data) % LSCR_LITERAL_ALIGNMENT:
                rebuilt_data += b"\x00"
            continue

        raw_data = original_literal.get("rawData")
        if not isinstance(raw_data, bytes):
            raise ValueError(f"Unsupported literal type for patching: {literal_type}")
        rebuilt_data += raw_data

    return bytes(rebuilt_table), bytes(rebuilt_data)


def collect_script_exports(blob: bytes) -> list[ScriptExportEntry]:
    """从 CST 的 CASt 成员中提取全部 LS 源码槽位，包含空脚本。"""

    mmap_offset, _mmap_len, chunk_count_used = find_mmap_chunk(blob)
    entries = parse_mmap_entries(blob, mmap_offset, chunk_count_used)
    member_id_by_entry = build_member_id_map(blob, entries)

    exports: list[ScriptExportEntry] = []
    for entry_index, entry in enumerate(entries):
        if entry["fourcc"] not in CAST_MEMBER_CHUNK_TYPES:
            continue

        chunk_offset = int(entry["offset"])
        chunk_len = int(entry["len"])
        payload = blob[chunk_offset + 8:chunk_offset + 8 + chunk_len]
        if len(payload) < CAST_MEMBER_INFO_OFFSET:
            continue

        info_len = read_be_u32(payload, CAST_MEMBER_INFO_LEN_OFFSET)
        specific_len = read_be_u32(payload, CAST_MEMBER_SPECIFIC_DATA_LEN_OFFSET)
        if CAST_MEMBER_INFO_OFFSET + info_len + specific_len != len(payload):
            raise ValueError(f"脚本 CASt chunk 布局异常，entry={entry_index}")
        if info_len == 0:
            continue

        info_bytes = payload[CAST_MEMBER_INFO_OFFSET:CAST_MEMBER_INFO_OFFSET + info_len]
        parsed_info = parse_cast_info_chunk(info_bytes)
        if not parsed_info["items"]:
            continue

        script_text_bytes = parsed_info["items"][CAST_INFO_SCRIPT_TEXT_INDEX]
        if script_text_bytes:
            try:
                script_text = script_text_bytes.decode("utf-8")
            except UnicodeDecodeError:
                script_text = decode_director_text(script_text_bytes)
        else:
            script_text = ""

        name = decode_pascal_name(parsed_info["items"][1]) if len(parsed_info["items"]) > 1 else ""
        exports.append(
            {
                "entryIndex": entry_index,
                "memberId": member_id_by_entry.get(entry_index, 0),
                "scriptId": parsed_info["scriptId"],
                "name": name,
                "scriptText": normalize_export_newlines(script_text),
            }
        )

    exports.sort(
        key=lambda item: (
            0 if item["memberId"] > 0 else 1,
            item["memberId"] or item["scriptId"] or item["entryIndex"],
            item["entryIndex"],
        )
    )
    return exports


def read_config_min_member(blob: bytes, entries: list[dict[str, int | bytes]]) -> int | None:
    """读取配置块中的 minMember，用来还原 DCR 使用的 member id。"""

    for entry in entries:
        if entry["fourcc"] not in CONFIG_CHUNK_TYPES:
            continue

        chunk_offset = int(entry["offset"])
        chunk_len = int(entry["len"])
        payload = blob[chunk_offset + 8:chunk_offset + 8 + chunk_len]
        if len(payload) < 14:
            continue
        return struct.unpack_from(">h", payload, 12)[0]

    return None


def build_member_id_map(blob: bytes, entries: list[dict[str, int | bytes]]) -> dict[int, int]:
    """按 CAS* 成员表把 CASt entryIndex 映射回 member id。"""

    min_member = read_config_min_member(blob, entries)
    if min_member is None:
        return {}

    member_id_by_entry: dict[int, int] = {}
    for entry in entries:
        if entry["fourcc"] not in CAST_MAP_CHUNK_TYPES:
            continue

        chunk_offset = int(entry["offset"])
        chunk_len = int(entry["len"])
        payload = blob[chunk_offset + 8:chunk_offset + 8 + chunk_len]
        usable_size = len(payload) - (len(payload) % 4)
        for offset in range(0, usable_size, 4):
            section_id = struct.unpack_from(">i", payload, offset)[0]
            if section_id <= 0:
                continue
            member_id_by_entry.setdefault(section_id, offset // 4 + min_member)

    return member_id_by_entry


def parse_mmap_entries(blob: bytes, mmap_offset: int, chunk_count_used: int) -> list[dict[str, int | bytes]]:
    entries: list[dict[str, int | bytes]] = []
    entries_offset = mmap_offset + 8 + 24
    for index in range(chunk_count_used):
        entry_offset = entries_offset + index * 20
        fourcc = blob[entry_offset:entry_offset + 4]
        length = struct.unpack_from("<I", blob, entry_offset + 4)[0]
        offset = struct.unpack_from("<I", blob, entry_offset + 8)[0]
        flags = struct.unpack_from("<H", blob, entry_offset + 12)[0]
        unknown0 = struct.unpack_from("<H", blob, entry_offset + 14)[0]
        next_index = struct.unpack_from("<I", blob, entry_offset + 16)[0]
        entries.append(
            {
                "entry_offset": entry_offset,
                "fourcc": fourcc,
                "len": length,
                "offset": offset,
                "flags": flags,
                "unknown0": unknown0,
                "next": next_index,
            }
        )
    return entries


def find_mmap_chunk(blob: bytes) -> tuple[int, int, int]:
    if blob[:4] != b"XFIR":
        raise ValueError("Unsupported CST header: expected XFIR.")

    mmap_offset = 44
    if blob[mmap_offset:mmap_offset + 4] != b"pamm":
        raise ValueError("Unsupported CST layout: expected pamm chunk at offset 44.")

    mmap_len = struct.unpack_from("<I", blob, mmap_offset + 4)[0]
    chunk_count_used = struct.unpack_from("<I", blob, mmap_offset + 12)[0]
    return mmap_offset, mmap_len, chunk_count_used


def find_chunk_entry(entries: list[dict[str, int | bytes]], fourcc: bytes, offset: int) -> int:
    for index, entry in enumerate(entries):
        if entry["fourcc"] in (fourcc, fourcc[::-1]) and entry["offset"] == offset:
            return index
    raise ValueError(f"Chunk entry not found for {fourcc!r} at offset {offset}.")


def find_smallest_containing_chunk_entry(
    entries: list[dict[str, int | bytes]],
    fourcc: bytes,
    absolute_offset: int,
) -> int:
    candidates: list[tuple[int, int]] = []
    for index, entry in enumerate(entries):
        if entry["fourcc"] not in (fourcc, fourcc[::-1]):
            continue
        start = int(entry["offset"])
        end = start + 8 + int(entry["len"])
        if start <= absolute_offset < end:
            candidates.append((int(entry["len"]), index))

    if not candidates:
        raise ValueError(f"No {fourcc!r} chunk contains file offset {absolute_offset}.")

    candidates.sort()
    return candidates[0][1]


def replace_range(blob: bytes, start: int, old_size: int, replacement: bytes) -> bytes:
    return blob[:start] + replacement + blob[start + old_size:]


def update_entries_after_chunk_patch(
    entries: list[dict[str, int | bytes]],
    cast_entry_index: int,
    source_chunk_offset: int,
    new_cast_chunk_len: int,
    cast_delta: int,
    lscr_entry_index: int,
    lscr_chunk_offset: int,
    new_lscr_len: int,
    literals_delta: int,
) -> None:
    """把一次 CASt/Lscr 替换后的长度和偏移变化同步到内存里的 mmap entries。"""

    for index, entry in enumerate(entries):
        original_entry_chunk_offset = int(entry["offset"])
        if index == cast_entry_index:
            entry["len"] = new_cast_chunk_len
        elif index == lscr_entry_index:
            entry["len"] = new_lscr_len

        new_entry_chunk_offset = original_entry_chunk_offset
        if original_entry_chunk_offset > source_chunk_offset:
            new_entry_chunk_offset += cast_delta
        if original_entry_chunk_offset > lscr_chunk_offset:
            new_entry_chunk_offset += literals_delta
        entry["offset"] = new_entry_chunk_offset


def write_mmap_entries_to_blob(blob: bytearray, entries: list[dict[str, int | bytes]]) -> None:
    """把内存里的 mmap entry 长度/偏移一次性写回 blob。"""

    file_length = len(blob) - 8
    struct.pack_into("<I", blob, 4, file_length)
    for index, entry in enumerate(entries):
        entry_offset = int(entry["entry_offset"])
        entry_length = file_length if index == 0 else int(entry["len"])
        struct.pack_into("<I", blob, entry_offset + 4, entry_length)
        struct.pack_into("<I", blob, entry_offset + 8, int(entry["offset"]))


def patch_variable_length_literals_in_blob(
    blob: bytes,
    entries: list[dict[str, int | bytes]],
    cast_entry_index: int,
    lscr_entry_index: int,
    original_text: str,
    edited_text: str,
    lscr_dump: dict,
    write_updated_mmap: bool = True,
) -> bytes:
    """按字符串字面量差异重建 CASt/Lscr，并返回新的 CST 字节串。"""

    original_literals = extract_string_literals(original_text)
    edited_literals = extract_string_literals(edited_text)
    if len(original_literals) != len(edited_literals):
        raise ValueError(
            "Edited script changed the number of string literals. "
            "This tool supports variable-length literal content, but not adding or removing literals."
        )
    if strip_string_literal_contents(original_text) != strip_string_literal_contents(edited_text):
        raise ValueError(
            "This tool only supports string literal edits. Non-literal script text changed as well."
        )

    dump_literals = extract_lscr_string_literals(lscr_dump)
    edited_compiled_literals = project_source_literals_to_compiled_literals(
        original_literals,
        edited_literals,
        dump_literals,
    )

    original_script_bytes = original_text.encode("utf-8")
    edited_script_bytes = edited_text.encode("utf-8")

    source_chunk_offset = int(entries[cast_entry_index]["offset"])
    old_cast_chunk_len = int(entries[cast_entry_index]["len"])
    old_cast_total_size = 8 + old_cast_chunk_len
    old_cast_chunk = blob[source_chunk_offset:source_chunk_offset + old_cast_total_size]
    old_cast_payload = old_cast_chunk[8:]
    old_cast_info_len = read_be_u32(old_cast_payload, CAST_MEMBER_INFO_LEN_OFFSET)
    old_cast_specific_len = read_be_u32(old_cast_payload, CAST_MEMBER_SPECIFIC_DATA_LEN_OFFSET)
    if CAST_MEMBER_INFO_OFFSET + old_cast_info_len + old_cast_specific_len != len(old_cast_payload):
        raise ValueError("CASt 成员布局与 ProjectorRays 的 CastMemberChunk 结构不一致。")

    old_cast_info_bytes = old_cast_payload[
        CAST_MEMBER_INFO_OFFSET:CAST_MEMBER_INFO_OFFSET + old_cast_info_len
    ]
    old_cast_specific_bytes = old_cast_payload[
        CAST_MEMBER_INFO_OFFSET + old_cast_info_len:CAST_MEMBER_INFO_OFFSET + old_cast_info_len + old_cast_specific_len
    ]
    parsed_cast_info = parse_cast_info_chunk(old_cast_info_bytes)
    if (
        not parsed_cast_info["items"]
        or parsed_cast_info["items"][CAST_INFO_SCRIPT_TEXT_INDEX] != original_script_bytes
    ):
        raise ValueError("CASt 的 scriptSrcText 与原始 LS 内容不匹配。")

    rebuilt_cast_info = build_cast_info_chunk(parsed_cast_info, edited_script_bytes)
    new_cast_payload = bytearray(old_cast_payload[:CAST_MEMBER_INFO_OFFSET])
    struct.pack_into(">I", new_cast_payload, CAST_MEMBER_INFO_LEN_OFFSET, len(rebuilt_cast_info))
    struct.pack_into(">I", new_cast_payload, CAST_MEMBER_SPECIFIC_DATA_LEN_OFFSET, old_cast_specific_len)
    new_cast_payload += rebuilt_cast_info
    new_cast_payload += old_cast_specific_bytes

    new_cast_chunk_len = len(new_cast_payload)
    cast_delta = new_cast_chunk_len - old_cast_chunk_len
    new_cast_chunk = bytearray(old_cast_chunk[:8])
    new_cast_chunk += new_cast_payload
    struct.pack_into("<I", new_cast_chunk, 4, new_cast_chunk_len)

    lscr_chunk_offset = int(entries[lscr_entry_index]["offset"])
    old_lscr_chunk_len = int(entries[lscr_entry_index]["len"])
    old_lscr_total_size = 8 + old_lscr_chunk_len
    old_lscr_chunk = bytearray(blob[lscr_chunk_offset:lscr_chunk_offset + old_lscr_total_size])
    lscr_payload = old_lscr_chunk[8:]

    literals_offset = int(lscr_dump["literalsOffset"])
    literals_data_offset = int(lscr_dump["literalsDataOffset"])
    handler_vectors_offset = int(lscr_dump["handlerVectorsOffset"])
    original_table_len = literals_data_offset - literals_offset
    original_data_len = handler_vectors_offset - literals_data_offset
    rebuilt_table, rebuilt_data = build_lscr_literals_region(lscr_dump, edited_compiled_literals)
    new_literals_data_offset = literals_offset + len(rebuilt_table)
    literals_delta = (len(rebuilt_table) + len(rebuilt_data)) - (original_table_len + original_data_len)

    old_total_length = read_be_u32(lscr_payload, LSCR_TOTAL_LENGTH_OFFSET)
    old_total_length2 = read_be_u32(lscr_payload, LSCR_TOTAL_LENGTH2_OFFSET)
    old_handler_vectors_offset = read_be_u32(lscr_payload, LSCR_HANDLER_VECTORS_OFFSET_OFFSET)
    old_literals_offset = read_be_u32(lscr_payload, LSCR_LITERALS_OFFSET_OFFSET)
    old_literals_data_count = read_be_u32(lscr_payload, LSCR_LITERALS_DATA_COUNT_OFFSET)
    old_literals_data_offset = read_be_u32(lscr_payload, LSCR_LITERALS_DATA_OFFSET_OFFSET)
    if old_handler_vectors_offset != handler_vectors_offset:
        raise ValueError("Unexpected Lscr handlerVectorsOffset field position.")
    if old_literals_offset != literals_offset:
        raise ValueError("Unexpected Lscr literalsOffset field position.")
    if old_literals_data_count != int(lscr_dump["literalsDataCount"]):
        raise ValueError("Unexpected Lscr literalsDataCount field position.")
    if old_literals_data_offset != literals_data_offset:
        raise ValueError("Unexpected Lscr literalsDataOffset field position.")

    new_lscr_payload = bytearray(lscr_payload[:literals_offset])
    new_lscr_payload += rebuilt_table
    new_lscr_payload += rebuilt_data
    new_lscr_payload += lscr_payload[handler_vectors_offset:]

    new_lscr_len = old_lscr_chunk_len + literals_delta
    write_be_u32(new_lscr_payload, LSCR_TOTAL_LENGTH_OFFSET, old_total_length + literals_delta)
    write_be_u32(new_lscr_payload, LSCR_TOTAL_LENGTH2_OFFSET, old_total_length2 + literals_delta)
    write_be_u32(new_lscr_payload, LSCR_LITERALS_DATA_COUNT_OFFSET, len(rebuilt_data))
    write_be_u32(new_lscr_payload, LSCR_LITERALS_DATA_OFFSET_OFFSET, new_literals_data_offset)
    write_be_u32(
        new_lscr_payload,
        LSCR_HANDLER_VECTORS_OFFSET_OFFSET,
        old_handler_vectors_offset + literals_delta,
    )

    if len(new_lscr_payload) != new_lscr_len:
        raise ValueError(
            "Rebuilt Lscr payload length mismatch. "
            f"Expected {new_lscr_len}, got {len(new_lscr_payload)}"
        )

    new_lscr_chunk = old_lscr_chunk[:8] + new_lscr_payload
    struct.pack_into("<I", new_lscr_chunk, 4, new_lscr_len)

    updated_blob = blob
    modifications = [
        (source_chunk_offset, old_cast_total_size, bytes(new_cast_chunk)),
        (lscr_chunk_offset, old_lscr_total_size, bytes(new_lscr_chunk)),
    ]
    for chunk_offset, old_size, replacement in sorted(modifications, key=lambda item: item[0], reverse=True):
        updated_blob = replace_range(updated_blob, chunk_offset, old_size, replacement)

    updated_blob = bytearray(updated_blob)
    update_entries_after_chunk_patch(
        entries,
        cast_entry_index,
        source_chunk_offset,
        new_cast_chunk_len,
        cast_delta,
        lscr_entry_index,
        lscr_chunk_offset,
        new_lscr_len,
        literals_delta,
    )
    if write_updated_mmap:
        write_mmap_entries_to_blob(updated_blob, entries)
    else:
        struct.pack_into("<I", updated_blob, 4, len(updated_blob) - 8)

    return bytes(updated_blob)


def patch_variable_length_literals(
    input_cst: Path,
    original_ls: Path,
    edited_ls: Path,
    lscr_dump_path: Path,
    output_cst: Path,
) -> None:
    if not input_cst.is_file():
        raise FileNotFoundError(f"Input CST does not exist: {input_cst}")
    if not original_ls.is_file():
        raise FileNotFoundError(f"Original LS does not exist: {original_ls}")
    if not edited_ls.is_file():
        raise FileNotFoundError(f"Edited LS does not exist: {edited_ls}")
    if not lscr_dump_path.is_file():
        raise FileNotFoundError(f"Lscr dump does not exist: {lscr_dump_path}")

    blob = input_cst.read_bytes()
    original_text = normalize_director_newlines(read_utf8_text(original_ls))
    edited_text = normalize_director_newlines(read_utf8_text(edited_ls))
    lscr_dump = read_projectorrays_literal_dump(lscr_dump_path)
    if original_text == edited_text:
        raise ValueError("No literal changes were found between the original and edited LS files.")

    original_script_bytes = original_text.encode("utf-8")
    edited_script_bytes = edited_text.encode("utf-8")
    source_offset = blob.find(original_script_bytes)
    if source_offset < 0:
        raise ValueError("Original script source bytes were not found in the CST.")
    if blob.count(original_script_bytes) != 1:
        raise ValueError("Original script source bytes were found more than once in the CST.")

    mmap_offset, _old_mmap_len, chunk_count_used = find_mmap_chunk(blob)
    entries = parse_mmap_entries(blob, mmap_offset, chunk_count_used)
    cast_entry_index = find_smallest_containing_chunk_entry(entries, b"CASt", source_offset)

    lscr_entry_index = int(lscr_dump_path.stem.split("-", 1)[1])
    if not (0 <= lscr_entry_index < len(entries)) or entries[lscr_entry_index]["fourcc"] not in (b"Lscr", b"Lscr"[::-1]):
        raise ValueError("The provided Lscr dump path does not map to a valid Lscr entry index.")
    updated_blob = patch_variable_length_literals_in_blob(
        blob,
        entries,
        cast_entry_index,
        lscr_entry_index,
        original_text,
        edited_text,
        lscr_dump,
    )
    output_cst.parent.mkdir(parents=True, exist_ok=True)
    output_cst.write_bytes(updated_blob)


def import_ls_script(
    input_cst: Path,
    edited_ls: Path,
    output_cst: Path,
) -> None:
    """根据原始 CST 和修改后的单个 LS 文件导回新的 CST。"""

    if not input_cst.is_file():
        raise FileNotFoundError(f"Input CST does not exist: {input_cst}")
    if not edited_ls.is_file():
        raise FileNotFoundError(f"Edited LS does not exist: {edited_ls}")

    blob = input_cst.read_bytes()
    resolved_entries = resolve_ls_import_entries(blob, [edited_ls])
    if not resolved_entries:
        updated_blob = blob
        changed = False
        message = ""
    else:
        resolved_entry = resolved_entries[0]
        updated_blob, changed, message = apply_ls_file_to_blob(
            blob,
            edited_ls,
            script_export=resolved_entry["scriptExport"],
            lscr_entry_index=resolved_entry["lscrEntryIndex"],
            edited_export_text=resolved_entry["editedText"],
        )

    output_cst.parent.mkdir(parents=True, exist_ok=True)
    output_cst.write_bytes(updated_blob)
    if changed:
        print(message)
    else:
        print("LS 内容未变化，已直接复制原始 CST，保证二进制完全一致。")


def apply_ls_file_to_blob(
    blob: bytes,
    edited_ls: Path,
    script_export: ScriptExportEntry | None = None,
    lscr_entry_index: int | None = None,
    edited_export_text: str | None = None,
    entries: list[dict[str, int | bytes]] | None = None,
    write_updated_mmap: bool = True,
) -> tuple[bytes, bool, str]:
    """把单个 LS 文件的修改应用到现有 CST 字节串。"""

    if script_export is None:
        exports = collect_script_exports(blob)
        script_export = find_script_export_for_ls_path(exports, edited_ls)

    original_export_text = script_export["scriptText"]
    if edited_export_text is None:
        edited_export_text = normalize_export_newlines(read_utf8_text(edited_ls))
    if edited_export_text == original_export_text:
        return blob, False, ""

    if entries is None:
        mmap_offset, _mmap_len, chunk_count_used = find_mmap_chunk(blob)
        entries = parse_mmap_entries(blob, mmap_offset, chunk_count_used)
    if lscr_entry_index is None:
        if script_export["scriptId"] <= 0:
            raise ValueError("目标脚本缺少有效的 scriptId，当前无法自动定位对应的 Lscr。")
        lscr_entry_index = find_matching_lscr_entry_index(
            blob,
            entries,
            script_export["scriptId"],
            normalize_director_newlines(original_export_text),
        )
    lscr_dump = read_lscr_literal_dump_from_chunk(blob, entries, lscr_entry_index)

    updated_blob = patch_variable_length_literals_in_blob(
        blob,
        entries,
        script_export["entryIndex"],
        lscr_entry_index,
        normalize_director_newlines(original_export_text),
        normalize_director_newlines(edited_export_text),
        lscr_dump,
        write_updated_mmap=write_updated_mmap,
    )

    message = (
        f"已将 {edited_ls.name} 导回到输出 CST。"
        f" memberId={script_export['memberId']}"
        f" scriptId={script_export['scriptId']} LscrEntry={lscr_entry_index}"
    )
    return updated_blob, True, message


def import_ls_folder(
    input_cst: Path,
    edited_ls_dir: Path,
    output_cst: Path,
) -> None:
    """根据原始 CST 和一个修改后的 LS 目录导回新的 CST。"""

    if not input_cst.is_file():
        raise FileNotFoundError(f"Input CST does not exist: {input_cst}")
    if not edited_ls_dir.is_dir():
        raise FileNotFoundError(f"Edited LS directory does not exist: {edited_ls_dir}")

    ls_files = sorted(edited_ls_dir.rglob("*.ls"), key=lambda path: path.as_posix().lower())
    if not ls_files:
        raise ValueError(f"目录里没有找到任何 .ls 文件: {edited_ls_dir}")

    blob = input_cst.read_bytes()
    resolved_entries = resolve_ls_import_entries(blob, ls_files)

    if not resolved_entries:
        output_cst.parent.mkdir(parents=True, exist_ok=True)
        output_cst.write_bytes(blob)
        print("目录中的 LS 内容都未变化，已直接输出与原始 CST 二进制完全一致的文件。")
        return

    mmap_offset, _mmap_len, chunk_count_used = find_mmap_chunk(blob)
    current_entries = parse_mmap_entries(blob, mmap_offset, chunk_count_used)
    changed_count = 0
    for resolved_entry in resolved_entries:
        ls_file = resolved_entry["lsPath"]
        blob, changed, message = apply_ls_file_to_blob(
            blob,
            ls_file,
            script_export=resolved_entry["scriptExport"],
            lscr_entry_index=resolved_entry["lscrEntryIndex"],
            edited_export_text=resolved_entry["editedText"],
            entries=current_entries,
            write_updated_mmap=False,
        )
        if not changed:
            continue
        changed_count += 1
        print(message)

    final_blob = bytearray(blob)
    write_mmap_entries_to_blob(final_blob, current_entries)
    output_cst.parent.mkdir(parents=True, exist_ok=True)
    output_cst.write_bytes(final_blob)
    print(f"已完成目录导回，共应用 {changed_count} 个脚本修改 -> {output_cst}")


def export_scripts(input_cst: Path, output_dir: Path) -> None:
    if not input_cst.is_file():
        raise FileNotFoundError(f"Input CST does not exist: {input_cst}")

    cast_dir = output_dir / input_cst.stem
    cast_dir.mkdir(parents=True, exist_ok=True)

    exports = collect_script_exports(input_cst.read_bytes())
    if not exports:
        raise ValueError("没有在 CST 中找到可导出的脚本成员。")

    used_stems: set[str] = set()
    members_csv_lines = ["member_id,script_id,entry_index,name,file_name"]
    for export in exports:
        numeric_prefix = export["memberId"] or export["scriptId"] or export["entryIndex"]
        file_stem = str(numeric_prefix)
        if export["name"]:
            file_stem = f"{file_stem}_{export['name']}"
        file_stem = sanitize_filename_component(file_stem)
        if file_stem in used_stems:
            file_stem = f"{file_stem}__{export['entryIndex']}"
        used_stems.add(file_stem)

        file_name = f"{file_stem}.ls"
        (cast_dir / file_name).write_text(export["scriptText"], encoding="utf-8", newline="\n")
        members_csv_lines.append(
            ",".join(
                [
                    csv_escape(export["memberId"]),
                    csv_escape(export["scriptId"]),
                    csv_escape(export["entryIndex"]),
                    csv_escape(export["name"]),
                    csv_escape(file_name),
                ]
            )
        )

    (cast_dir / "Members.csv").write_text(
        "\n".join(members_csv_lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"已导出 {len(exports)} 个脚本到 {cast_dir}")


def replace_exactly_once(blob: bytes, old: bytes, new: bytes, label: str) -> bytes:
    count = blob.count(old)
    if count != 1:
        raise ValueError(f"Expected exactly one {label} occurrence, found {count}.")
    return blob.replace(old, new, 1)


def patch_same_length_script(
    input_cst: Path,
    original_ls: Path,
    edited_ls: Path,
    output_cst: Path,
) -> None:
    if not input_cst.is_file():
        raise FileNotFoundError(f"Input CST does not exist: {input_cst}")
    if not original_ls.is_file():
        raise FileNotFoundError(f"Original LS does not exist: {original_ls}")
    if not edited_ls.is_file():
        raise FileNotFoundError(f"Edited LS does not exist: {edited_ls}")

    original_text = normalize_director_newlines(read_utf8_text(original_ls))
    edited_text = normalize_director_newlines(read_utf8_text(edited_ls))

    original_bytes = original_text.encode("utf-8")
    edited_bytes = edited_text.encode("utf-8")
    if len(original_bytes) != len(edited_bytes):
        raise ValueError(
            "Edited script must keep the same UTF-8 byte length as the original script. "
            f"Original={len(original_bytes)}, edited={len(edited_bytes)}"
        )

    original_literals = extract_string_literals(original_text)
    edited_literals = extract_string_literals(edited_text)
    if len(original_literals) != len(edited_literals):
        raise ValueError(
            "Edited script changed the number of string literals. "
            "Without a Lingo compiler, this tool only supports same-structure edits."
        )

    replacements: list[tuple[str, str]] = []
    for old_literal, new_literal in zip(original_literals, edited_literals):
        if old_literal == new_literal:
            continue
        old_bytes = old_literal.encode("utf-8")
        new_bytes = new_literal.encode("utf-8")
        if len(old_bytes) != len(new_bytes):
            raise ValueError(
                "Edited string literals must keep the same UTF-8 byte length. "
                f"Old={len(old_bytes)}, new={len(new_bytes)}, old={old_literal!r}, new={new_literal!r}"
            )
        replacements.append((old_literal, new_literal))

    if not replacements:
        raise ValueError("No string literal changes were found between the original and edited LS files.")

    blob = input_cst.read_bytes()
    blob = replace_exactly_once(blob, original_bytes, edited_bytes, "script source")

    for old_literal, new_literal in replacements:
        blob = replace_exactly_once(
            blob,
            old_literal.encode("utf-8"),
            new_literal.encode("utf-8"),
            f"compiled literal {old_literal!r}",
        )

    output_cst.parent.mkdir(parents=True, exist_ok=True)
    output_cst.write_bytes(blob)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="导出 Director LS 脚本（包含空脚本）、修复导出目录文件名，并补丁脚本字面量。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="直接从 CASt 成员导出全部 .ls 文件，包含空脚本")
    export_parser.add_argument("input_cst", type=Path)
    export_parser.add_argument("output_dir", type=Path)

    patch_parser = subparsers.add_parser(
        "patch-script",
        help="按等长字符串字面量修改回写一个脚本",
    )
    patch_parser.add_argument("input_cst", type=Path)
    patch_parser.add_argument("original_ls", type=Path)
    patch_parser.add_argument("edited_ls", type=Path)
    patch_parser.add_argument("output_cst", type=Path)

    patch_literals_parser = subparsers.add_parser(
        "patch-literals",
        help="按变长字符串字面量修改回写一个脚本，脚本结构必须保持不变",
    )
    patch_literals_parser.add_argument("input_cst", type=Path)
    patch_literals_parser.add_argument("original_ls", type=Path)
    patch_literals_parser.add_argument("edited_ls", type=Path)
    patch_literals_parser.add_argument("lscr_dump", type=Path)
    patch_literals_parser.add_argument("output_cst", type=Path)

    import_ls_parser = subparsers.add_parser(
        "import-ls",
        help="用原始 CST 和修改后的单个 LS 文件导回新的 CST",
    )
    import_ls_parser.add_argument("input_cst", type=Path)
    import_ls_parser.add_argument("edited_ls", type=Path)
    import_ls_parser.add_argument("output_cst", type=Path)

    import_folder_parser = subparsers.add_parser(
        "import-folder",
        help="用原始 CST 和修改后的 LS 文件夹导回新的 CST",
    )
    import_folder_parser.add_argument("input_cst", type=Path)
    import_folder_parser.add_argument("edited_ls_dir", type=Path)
    import_folder_parser.add_argument("output_cst", type=Path)

    fix_names_parser = subparsers.add_parser(
        "fix-names",
        help="修复导出目录中的乱码文件名",
    )
    fix_names_parser.add_argument("root_dir", type=Path)
    fix_names_parser.add_argument("--dry-run", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "export":
            export_scripts(args.input_cst, args.output_dir)
        elif args.command == "patch-script":
            patch_same_length_script(
                args.input_cst,
                args.original_ls,
                args.edited_ls,
                args.output_cst,
            )
        elif args.command == "patch-literals":
            patch_variable_length_literals(
                args.input_cst,
                args.original_ls,
                args.edited_ls,
                args.lscr_dump,
                args.output_cst,
            )
        elif args.command == "import-ls":
            import_ls_script(
                args.input_cst,
                args.edited_ls,
                args.output_cst,
            )
        elif args.command == "import-folder":
            import_ls_folder(
                args.input_cst,
                args.edited_ls_dir,
                args.output_cst,
            )
        elif args.command == "fix-names":
            fix_exported_names(args.root_dir, dry_run=args.dry_run)
        else:
            parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())