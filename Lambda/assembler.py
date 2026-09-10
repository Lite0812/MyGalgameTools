"""MBT0 与 SAM DAT 文件汇编器。"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from opcodelist import (
    MBT0_MAGIC,
    SAM_A_BUFF_OFFSET,
    SAM_A_DEBUFF_OFFSET,
    SAM_A_EFFECT_COUNT,
    SAM_A_EFFECT_OFFSET,
    SAM_A_EFFECT_SIZE,
    SAM_A_LEVEL_COUNT,
    SAM_A_LEVEL_FIELDS,
    SAM_A_LEVEL_HEADER,
    SAM_A_LEVEL_STRIDE,
    SAM_A_LEVEL_TEXTS,
    SAM_A_RECORD_NAME,
    SAM_A_SPECIAL_NAMES,
    SAM_A_SPECIAL_OFFSET,
    SAM_A_STATUS_EFFECT_COUNT,
    SAM_A_TAIL_U32_FIELDS,
    SAM_A_TAIL_U8_FIELDS,
    SAM_B_MATERIAL_COUNT,
    SAM_B_MATERIAL_OFFSET,
    SAM_B_PASSIVE_COUNT,
    SAM_B_PASSIVE_OFFSET,
    SAM_B_SPECIAL_TYPE,
    SAM_B_SPECIAL_VALUE,
    SAM_B_TEXTS,
    SAM_B_U32_FIELDS,
    SAM_C_ATTACK_TYPE,
    SAM_C_BASE_STAT_OFFSET,
    SAM_C_EFFECT_RATE_COUNT,
    SAM_C_EFFECT_RATE_FIRST,
    SAM_C_EFFECT_RATE_OFFSET,
    SAM_C_GROWTH_STAT_OFFSET,
    SAM_C_NAME,
    SAM_C_STAT_COUNT,
    SAM_C_U32_FIELDS,
    SAM_MAGIC,
    SAM_SECTION_CODES,
    SAM_SECTIONS,
    FormatError,
    codec_name,
    unescape_text,
)


@dataclass
class MbtGroupSource:
    """汇编文本中的一个消息分组。"""

    name: bytes
    strings: list[bytes] = field(default_factory=list)


@dataclass
class SamSectionSource:
    """汇编文本中的一个语义记录区。"""

    name: str
    records: list[bytes] = field(default_factory=list)


@dataclass
class SamRecordSource:
    """正在解析的 SAM 记录及其重复字段状态。"""

    section: str
    data: bytearray
    seen: set[str] = field(default_factory=set)
    record_name: bytes = b""
    levels: set[int] = field(default_factory=set)
    explicit_level_names: set[int] = field(default_factory=set)


def strip_comment(line: str) -> str:
    """删除双引号外以分号开头的语义注释。"""

    quoted = False
    for index, character in enumerate(line):
        if character == '"':
            quoted = not quoted
        elif character == ";" and not quoted:
            return line[:index].strip()
    return line.strip()


def source_lines(text: str) -> list[tuple[int, str]]:
    """生成去除空行和注释后的带行号记录。"""

    result: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = strip_comment(raw)
        if line:
            result.append((number, line))
    return result


def quoted_value(line: str, prefix: str, number: int) -> str:
    """解析形如“指令 \"内容\"”的单一双引号参数。"""

    if not line.startswith(prefix):
        raise FormatError(f"第 {number} 行缺少 {prefix}")
    rest = line[len(prefix) :].strip()
    if len(rest) < 2 or rest[0] != '"' or rest[-1] != '"':
        raise FormatError(f"第 {number} 行需要一个完整双引号参数")
    value = rest[1:-1]
    if '"' in value:
        raise FormatError(f"第 {number} 行的双引号字节必须写作 {{22}} 占位符")
    return value


def text_value(line: str, number: int) -> tuple[str, int | None]:
    """解析 TEXT；兼容读取旧文本中的显式 .zero。"""

    rest = line[len("TEXT ") :].strip()
    if not rest.startswith('"'):
        raise FormatError(f"第 {number} 行的 TEXT 缺少起始双引号")
    end = rest.find('"', 1)
    if end < 0:
        raise FormatError(f"第 {number} 行的 TEXT 缺少结束双引号")
    value = rest[1:end]
    tail = rest[end + 1 :].strip()
    if not tail:
        return value, None
    parts = tail.split()
    if len(parts) != 2 or parts[0] != ".zero":
        raise FormatError(f"第 {number} 行的 TEXT 尾部只能使用 .zero N")
    return value, parse_integer(parts[1], number, "TEXT 尾部零填充")


def parse_integer(value: str, number: int, field_name: str) -> int:
    """解析十进制或 0x 十六进制非负整数。"""

    try:
        result = int(value, 0)
    except ValueError as exc:
        raise FormatError(f"第 {number} 行的{field_name}不是整数：{value}") from exc
    if result < 0:
        raise FormatError(f"第 {number} 行的{field_name}不能为负数")
    return result


def find_directive(lines: list[tuple[int, str]], prefix: str) -> tuple[int, str] | None:
    """查找只允许出现一次的全局指令。"""

    matches = [(number, line) for number, line in lines if line.startswith(prefix)]
    if len(matches) > 1:
        raise FormatError(f"全局指令 {prefix.strip()} 重复出现")
    return matches[0] if matches else None


def assembly_format(lines: list[tuple[int, str]]) -> str:
    """读取 .format，并拒绝未知格式。"""

    item = find_directive(lines, ".format ")
    if item is None:
        raise FormatError("汇编文本缺少 .format")
    number, line = item
    value = line[len(".format ") :].strip().upper()
    if value not in {"MBT0", "SAM"}:
        raise FormatError(f"第 {number} 行使用了未知格式：{value}")
    return value


def assembly_encoding(lines: list[tuple[int, str]], override: str | None) -> str:
    """命令行编码优先，否则读取 .encoding，最后使用 cp932。"""

    if override:
        return codec_name(override)
    item = find_directive(lines, ".encoding ")
    if item is None:
        return "cp932"
    number, line = item
    return codec_name(quoted_value(line, ".encoding ", number))


def parse_mbt0(lines: list[tuple[int, str]], encoding: str) -> tuple[int, int, list[MbtGroupSource]]:
    """解析 MBT0 分组语义文本。"""

    group_offset: int | None = None
    slot_count: int | None = None
    groups: list[MbtGroupSource] = []
    current: MbtGroupSource | None = None

    for number, line in lines:
        if line.startswith(".format ") or line.startswith(".encoding "):
            continue
        if line.startswith(".group_table_offset "):
            if group_offset is not None:
                raise FormatError(f"第 {number} 行重复定义 .group_table_offset")
            value = line[len(".group_table_offset ") :].strip()
            group_offset = parse_integer(value, number, "分组表偏移")
            continue
        if line.startswith(".string_slot_count "):
            if slot_count is not None:
                raise FormatError(f"第 {number} 行重复定义 .string_slot_count")
            value = line[len(".string_slot_count ") :].strip()
            slot_count = parse_integer(value, number, "字符串偏移槽数量")
            continue
        if line.startswith(".group "):
            if current is not None:
                raise FormatError(f"第 {number} 行在前一分组结束前开始了新分组")
            name_text = quoted_value(line, ".group ", number)
            name = unescape_text(name_text, "ascii")
            if not name or b"\x00" in name:
                raise FormatError(f"第 {number} 行的分组名为空或含 NUL")
            current = MbtGroupSource(name)
            continue
        if line.startswith("TEXT "):
            if current is None:
                raise FormatError(f"第 {number} 行的 TEXT 不在分组内")
            value_text, declared_padding = text_value(line, number)
            value = unescape_text(value_text, encoding)
            if b"\x00" in value:
                raise FormatError(f"第 {number} 行的 MBT0 字符串不能含内嵌 NUL")
            automatic_padding = (-(len(value) + 1)) % 4
            if declared_padding is not None and declared_padding != automatic_padding:
                raise FormatError(
                    f"第 {number} 行的 .zero 应为 {automatic_padding}，实际为 {declared_padding}"
                )
            current.strings.append(value)
            continue
        if line == ".endgroup":
            if current is None:
                raise FormatError(f"第 {number} 行存在多余 .endgroup")
            groups.append(current)
            current = None
            continue
        raise FormatError(f"第 {number} 行存在未知 MBT0 指令：{line[:80]}")

    if current is not None:
        raise FormatError("最后一个分组缺少 .endgroup")
    if group_offset is None:
        raise FormatError("MBT0 汇编缺少 .group_table_offset")
    if slot_count is None:
        raise FormatError("MBT0 汇编缺少 .string_slot_count")
    if not groups:
        raise FormatError("MBT0 汇编不包含任何分组")
    return group_offset, slot_count, groups


def build_mbt0(group_table_offset: int, slot_count: int, groups: list[MbtGroupSource]) -> bytes:
    """重新计算全部 MBT0 表项和文件相对偏移。"""

    actual_slot_count = 1 + sum(len(group.strings) for group in groups)
    if actual_slot_count != slot_count:
        raise FormatError(
            f"TEXT 数量要求 {actual_slot_count} 个偏移槽，但 .string_slot_count 声明为 {slot_count}"
        )
    offset_table_end = 0x10 + slot_count * 4
    if group_table_offset < offset_table_end:
        raise FormatError(".group_table_offset 与字符串偏移表重叠")

    group_table_end = group_table_offset + len(groups) * 12
    name_offsets: list[int] = []
    cursor = group_table_end
    for group in groups:
        name_offsets.append(cursor)
        cursor += len(group.name) + 1

    cursor = (cursor + 3) & ~3
    string_offsets = [0]
    for group in groups:
        for value in group.strings:
            string_offsets.append(cursor)
            cursor = (cursor + len(value) + 1 + 3) & ~3

    output = bytearray(cursor)
    struct.pack_into("<4sIII", output, 0, MBT0_MAGIC, len(groups), slot_count, group_table_offset)
    for index, offset in enumerate(string_offsets):
        struct.pack_into("<I", output, 0x10 + index * 4, offset)

    first_index = 1
    for index, group in enumerate(groups):
        record = group_table_offset + index * 12
        struct.pack_into("<III", output, record, first_index, len(group.strings), name_offsets[index])
        first_index += len(group.strings)

    cursor = group_table_end
    for group in groups:
        output[cursor : cursor + len(group.name)] = group.name
        cursor += len(group.name) + 1
    cursor = (cursor + 3) & ~3
    for group in groups:
        for value in group.strings:
            output[cursor : cursor + len(value)] = value
            cursor = (cursor + len(value) + 1 + 3) & ~3
    return bytes(output)


def claim_field(record: SamRecordSource, key: str, number: int) -> None:
    """登记一个语义字段，避免后写值静默覆盖前写值。"""

    if key in record.seen:
        raise FormatError(f"第 {number} 行重复定义字段 {key}")
    record.seen.add(key)


def write_fixed_string(
    record: SamRecordSource,
    key: str,
    offset: int,
    size: int,
    value: bytes,
    number: int,
) -> None:
    """写入固定字符串槽；NUL 和剩余零填充均自动生成。"""

    claim_field(record, key, number)
    if b"\x00" in value:
        raise FormatError(f"第 {number} 行的 {key} 不能含内嵌 NUL")
    if len(value) + 1 > size:
        raise FormatError(
            f"第 {number} 行的 {key} 编码后为 {len(value)} 字节，槽容量最多为 {size - 1} 字节"
        )
    record.data[offset : offset + len(value)] = value


def write_u32(record: SamRecordSource, key: str, offset: int, value: int, number: int) -> None:
    claim_field(record, key, number)
    if value > 0xFFFFFFFF:
        raise FormatError(f"第 {number} 行的 {key} 超出 u32 范围")
    struct.pack_into("<I", record.data, offset, value)


def write_u8(record: SamRecordSource, key: str, offset: int, value: int, number: int) -> None:
    claim_field(record, key, number)
    if value > 0xFF:
        raise FormatError(f"第 {number} 行的 {key} 超出 u8 范围")
    record.data[offset] = value


def parse_assignments(
    line: str,
    mnemonic: str,
    allowed: set[str],
    required: set[str],
    number: int,
) -> dict[str, int]:
    """解析“指令 key=value”形式的无符号整数参数。"""

    parts = line.split()
    if not parts or parts[0] != mnemonic:
        raise FormatError(f"第 {number} 行不是 {mnemonic} 指令")
    values: dict[str, int] = {}
    for part in parts[1:]:
        if "=" not in part:
            raise FormatError(f"第 {number} 行的 {mnemonic} 参数缺少等号：{part}")
        key, raw = part.split("=", 1)
        key = key.lower()
        if key not in allowed or key in values:
            raise FormatError(f"第 {number} 行存在重复或未知的 {mnemonic} 参数：{key}")
        value = parse_integer(raw, number, f"{mnemonic} 参数 {key}")
        if value > 0xFFFFFFFF:
            raise FormatError(f"第 {number} 行的 {mnemonic} 参数 {key} 超出 u32 范围")
        values[key] = value
    missing = required - set(values)
    if missing:
        raise FormatError(f"第 {number} 行的 {mnemonic} 缺少参数：{', '.join(sorted(missing))}")
    return values


def parse_named_text(line: str, field_name: str, encoding: str, number: int) -> bytes:
    text = quoted_value(line, field_name + " ", number)
    return unescape_text(text, encoding)


def parse_scalar(line: str, field_name: str, number: int) -> int:
    value = line[len(field_name) :].strip()
    if not value or len(value.split()) != 1:
        raise FormatError(f"第 {number} 行的 {field_name} 需要一个整数")
    return parse_integer(value, number, field_name)


def parse_skill_line(
    record: SamRecordSource,
    level: int | None,
    line: str,
    encoding: str,
    number: int,
) -> None:
    """解析 A 区记录或当前等级中的一条语义指令。"""

    mnemonic = line.split(maxsplit=1)[0]
    if level is None:
        if mnemonic == "NAME":
            value = parse_named_text(line, "NAME", encoding, number)
            offset, size = SAM_A_RECORD_NAME
            write_fixed_string(record, "record:NAME", offset, size, value, number)
            record.record_name = value
            return
        if mnemonic in SAM_A_TAIL_U32_FIELDS:
            value = parse_scalar(line, mnemonic, number)
            write_u32(record, f"record:{mnemonic}", SAM_A_TAIL_U32_FIELDS[mnemonic], value, number)
            return
        if mnemonic in SAM_A_TAIL_U8_FIELDS:
            value = parse_scalar(line, mnemonic, number)
            write_u8(record, f"record:{mnemonic}", SAM_A_TAIL_U8_FIELDS[mnemonic], value, number)
            return
        raise FormatError(f"第 {number} 行存在未知技能记录指令：{line[:80]}")

    block = level * SAM_A_LEVEL_STRIDE
    header = block + SAM_A_LEVEL_HEADER
    prefix = f"level:{level}:"
    if mnemonic == "NAME":
        value = parse_named_text(line, "NAME", encoding, number)
        write_fixed_string(record, prefix + "NAME", header, 0x3C, value, number)
        record.explicit_level_names.add(level)
        return
    if mnemonic in SAM_A_LEVEL_TEXTS:
        value = parse_named_text(line, mnemonic, encoding, number)
        relative, size = SAM_A_LEVEL_TEXTS[mnemonic]
        write_fixed_string(record, prefix + mnemonic, block + relative, size, value, number)
        return
    if mnemonic in SAM_A_LEVEL_FIELDS:
        value = parse_scalar(line, mnemonic, number)
        write_u32(record, prefix + mnemonic, header + SAM_A_LEVEL_FIELDS[mnemonic], value, number)
        return
    if mnemonic == "EFFECT":
        values = parse_assignments(
            line,
            mnemonic,
            {"slot", "id", "type", "value", "probability", "target"},
            {"slot"},
            number,
        )
        slot = values["slot"]
        if slot >= SAM_A_EFFECT_COUNT:
            raise FormatError(f"第 {number} 行的 EFFECT slot 必须小于 {SAM_A_EFFECT_COUNT}")
        key = prefix + f"EFFECT:{slot}"
        claim_field(record, key, number)
        offset = header + SAM_A_EFFECT_OFFSET + slot * SAM_A_EFFECT_SIZE
        struct.pack_into(
            "<5I",
            record.data,
            offset,
            *(values.get(name, 0) for name in ("id", "type", "value", "probability", "target")),
        )
        return
    if mnemonic == "SPECIAL":
        parts = line.split()
        if len(parts) != 3 or parts[1] not in SAM_A_SPECIAL_NAMES:
            names = ", ".join(SAM_A_SPECIAL_NAMES)
            raise FormatError(f"第 {number} 行的 SPECIAL 应使用已知名称：{names}")
        index = SAM_A_SPECIAL_NAMES.index(parts[1])
        value = parse_integer(parts[2], number, f"SPECIAL {parts[1]}")
        write_u32(
            record,
            prefix + f"SPECIAL:{parts[1]}",
            header + SAM_A_SPECIAL_OFFSET + index * 4,
            value,
            number,
        )
        return
    if mnemonic in {"BUFF", "DEBUFF"}:
        values = parse_assignments(line, mnemonic, {"category", "turns"}, set(), number)
        if not values:
            raise FormatError(f"第 {number} 行的 {mnemonic} 至少需要 category 或 turns")
        key = prefix + mnemonic
        claim_field(record, key, number)
        relative = SAM_A_BUFF_OFFSET if mnemonic == "BUFF" else SAM_A_DEBUFF_OFFSET
        struct.pack_into(
            "<2I",
            record.data,
            header + relative,
            values.get("category", 0),
            values.get("turns", 0),
        )
        return
    if mnemonic in {"BUFF_EFFECT", "DEBUFF_EFFECT"}:
        values = parse_assignments(line, mnemonic, {"slot", "id", "rank"}, {"slot"}, number)
        slot = values["slot"]
        if slot >= SAM_A_STATUS_EFFECT_COUNT:
            raise FormatError(
                f"第 {number} 行的 {mnemonic} slot 必须小于 {SAM_A_STATUS_EFFECT_COUNT}"
            )
        key = prefix + f"{mnemonic}:{slot}"
        claim_field(record, key, number)
        relative = SAM_A_BUFF_OFFSET if mnemonic == "BUFF_EFFECT" else SAM_A_DEBUFF_OFFSET
        offset = header + relative + 8 + slot * 8
        struct.pack_into("<2I", record.data, offset, values.get("id", 0), values.get("rank", 0))
        return
    raise FormatError(f"第 {number} 行存在未知技能等级指令：{line[:80]}")


def parse_item_line(record: SamRecordSource, line: str, encoding: str, number: int) -> None:
    """解析 B 区记录中的一条语义指令。"""

    mnemonic = line.split(maxsplit=1)[0]
    if mnemonic in SAM_B_TEXTS:
        value = parse_named_text(line, mnemonic, encoding, number)
        offset, size = SAM_B_TEXTS[mnemonic]
        write_fixed_string(record, mnemonic, offset, size, value, number)
        return
    if mnemonic in SAM_B_U32_FIELDS:
        value = parse_scalar(line, mnemonic, number)
        write_u32(record, mnemonic, SAM_B_U32_FIELDS[mnemonic], value, number)
        return
    if mnemonic == "PASSIVE_ATTRIBUTE":
        values = parse_assignments(line, mnemonic, {"index", "value"}, {"index", "value"}, number)
        index = values["index"]
        if index >= SAM_B_PASSIVE_COUNT:
            raise FormatError(f"第 {number} 行的被动属性 index 必须小于 {SAM_B_PASSIVE_COUNT}")
        write_u32(
            record,
            f"PASSIVE_ATTRIBUTE:{index}",
            SAM_B_PASSIVE_OFFSET + index * 4,
            values["value"],
            number,
        )
        return
    if mnemonic == "SPECIAL_ATTRIBUTE":
        values = parse_assignments(line, mnemonic, {"type", "value"}, set(), number)
        if not values:
            raise FormatError(f"第 {number} 行的 SPECIAL_ATTRIBUTE 至少需要 type 或 value")
        claim_field(record, mnemonic, number)
        struct.pack_into(
            "<2I",
            record.data,
            SAM_B_SPECIAL_TYPE,
            values.get("type", 0),
            values.get("value", 0),
        )
        return
    if mnemonic == "MATERIAL":
        values = parse_assignments(line, mnemonic, {"index", "id"}, {"index", "id"}, number)
        index = values["index"]
        if index >= SAM_B_MATERIAL_COUNT:
            raise FormatError(f"第 {number} 行的 MATERIAL index 必须小于 {SAM_B_MATERIAL_COUNT}")
        write_u32(
            record,
            f"MATERIAL:{index}",
            SAM_B_MATERIAL_OFFSET + index * 4,
            values["id"],
            number,
        )
        return
    raise FormatError(f"第 {number} 行存在未知装备记录指令：{line[:80]}")


def parse_profile_line(record: SamRecordSource, line: str, encoding: str, number: int) -> None:
    """解析 C 区记录中的一条语义指令。"""

    mnemonic = line.split(maxsplit=1)[0]
    if mnemonic == "NAME":
        value = parse_named_text(line, mnemonic, encoding, number)
        offset, size = SAM_C_NAME
        write_fixed_string(record, mnemonic, offset, size, value, number)
        return
    if mnemonic in SAM_C_U32_FIELDS:
        value = parse_scalar(line, mnemonic, number)
        write_u32(record, mnemonic, SAM_C_U32_FIELDS[mnemonic], value, number)
        return
    if mnemonic == "STAT":
        values = parse_assignments(line, mnemonic, {"index", "base", "growth"}, {"index"}, number)
        index = values["index"]
        if index >= SAM_C_STAT_COUNT:
            raise FormatError(f"第 {number} 行的 STAT index 必须小于 {SAM_C_STAT_COUNT}")
        if "base" not in values and "growth" not in values:
            raise FormatError(f"第 {number} 行的 STAT 至少需要 base 或 growth")
        claim_field(record, f"STAT:{index}", number)
        struct.pack_into("<I", record.data, SAM_C_BASE_STAT_OFFSET + index * 4, values.get("base", 0))
        struct.pack_into(
            "<I", record.data, SAM_C_GROWTH_STAT_OFFSET + index * 4, values.get("growth", 0)
        )
        return
    if mnemonic == "ATTACK_TYPE":
        raw = line[len(mnemonic) :].strip().upper()
        symbolic = {"PHYSICAL": 1, "MAGICAL": 2}
        value = symbolic.get(raw)
        if value is None:
            value = parse_integer(raw, number, mnemonic)
        write_u32(record, mnemonic, SAM_C_ATTACK_TYPE, value, number)
        return
    if mnemonic == "EFFECT_RATE":
        values = parse_assignments(line, mnemonic, {"type", "value"}, {"type", "value"}, number)
        effect_type = values["type"]
        index = effect_type - SAM_C_EFFECT_RATE_FIRST
        if index < 0 or index >= SAM_C_EFFECT_RATE_COUNT:
            last = SAM_C_EFFECT_RATE_FIRST + SAM_C_EFFECT_RATE_COUNT - 1
            raise FormatError(
                f"第 {number} 行的 EFFECT_RATE type 必须为 {SAM_C_EFFECT_RATE_FIRST}..{last}"
            )
        write_u32(
            record,
            f"EFFECT_RATE:{effect_type}",
            SAM_C_EFFECT_RATE_OFFSET + index * 4,
            values["value"],
            number,
        )
        return
    raise FormatError(f"第 {number} 行存在未知配置记录指令：{line[:80]}")


def parse_sam(lines: list[tuple[int, str]], encoding: str) -> dict[str, SamSectionSource]:
    """解析 SAM 的技能、装备和角色配置语义源码。"""

    sections: dict[str, SamSectionSource] = {}
    section: SamSectionSource | None = None
    record: SamRecordSource | None = None
    level: int | None = None

    for number, line in lines:
        if line.startswith(".format ") or line.startswith(".encoding "):
            continue
        if line.startswith(".section "):
            if section is not None:
                raise FormatError(f"第 {number} 行在前一区段结束前开始了新区段")
            parts = line.split()
            if len(parts) != 2:
                raise FormatError(f"第 {number} 行的 .section 只接受区段名称")
            source_name = parts[1].upper()
            code = SAM_SECTION_CODES.get(source_name, source_name if source_name in SAM_SECTIONS else None)
            if code is None:
                raise FormatError(f"第 {number} 行存在未知 SAM 区段：{source_name}")
            if code in sections:
                raise FormatError(f"第 {number} 行重复定义 {source_name} 区")
            section = SamSectionSource(code)
            continue
        if line == ".record":
            if section is None:
                raise FormatError(f"第 {number} 行的 .record 不在区段内")
            if record is not None:
                raise FormatError(f"第 {number} 行在前一记录结束前开始了新记录")
            size = SAM_SECTIONS[section.name].record_size
            record = SamRecordSource(section.name, bytearray(size))
            continue
        if line.startswith(".level "):
            if record is None or record.section != "A":
                raise FormatError(f"第 {number} 行的 .level 只能位于技能记录内")
            if level is not None:
                raise FormatError(f"第 {number} 行在前一等级结束前开始了新等级")
            value = line[len(".level ") :].strip()
            level = parse_integer(value, number, "等级索引")
            if level >= SAM_A_LEVEL_COUNT:
                raise FormatError(f"第 {number} 行的等级索引必须小于 {SAM_A_LEVEL_COUNT}")
            if level in record.levels:
                raise FormatError(f"第 {number} 行重复定义等级 {level}")
            record.levels.add(level)
            continue
        if line == ".endlevel":
            if record is None or level is None:
                raise FormatError(f"第 {number} 行存在多余 .endlevel")
            level = None
            continue
        if line == ".endrecord":
            if section is None or record is None:
                raise FormatError(f"第 {number} 行存在多余 .endrecord")
            if level is not None:
                raise FormatError(f"第 {number} 行在等级结束前结束了记录")
            if record.section == "A" and record.record_name:
                for level_index in record.levels - record.explicit_level_names:
                    offset = level_index * SAM_A_LEVEL_STRIDE + SAM_A_LEVEL_HEADER
                    if len(record.record_name) + 1 > 0x3C:
                        raise FormatError(
                            f"第 {number} 行的技能名称过长，不能自动写入等级 {level_index} 名称槽"
                        )
                    record.data[offset : offset + len(record.record_name)] = record.record_name
            section.records.append(bytes(record.data))
            record = None
            continue
        if line == ".endsection":
            if section is None:
                raise FormatError(f"第 {number} 行存在多余 .endsection")
            if record is not None:
                raise FormatError(f"第 {number} 行在记录结束前结束了区段")
            sections[section.name] = section
            section = None
            continue

        if record is None:
            raise FormatError(f"第 {number} 行的指令不在记录内：{line[:80]}")
        if record.section == "A":
            parse_skill_line(record, level, line, encoding, number)
        elif record.section == "B":
            if level is not None:
                raise FormatError(f"第 {number} 行的装备记录不能包含等级")
            parse_item_line(record, line, encoding, number)
        else:
            if level is not None:
                raise FormatError(f"第 {number} 行的配置记录不能包含等级")
            parse_profile_line(record, line, encoding, number)

    if level is not None:
        raise FormatError("最后一个等级缺少 .endlevel")
    if record is not None:
        raise FormatError("最后一条记录缺少 .endrecord")
    if section is not None:
        raise FormatError(f"{section.name} 区缺少 .endsection")
    missing = set(SAM_SECTIONS) - set(sections)
    if missing:
        raise FormatError(f"SAM 汇编缺少区段：{', '.join(sorted(missing))}")
    return sections


def align16(value: int) -> int:
    return (value + 0xF) & ~0xF


def build_sam(sections: dict[str, SamSectionSource]) -> bytes:
    """根据记录数量自动计算区段偏移、间隙和文件头。"""

    offsets: dict[str, int] = {"A": 0x28}
    a_end = offsets["A"] + len(sections["A"].records) * SAM_SECTIONS["A"].record_size
    offsets["B"] = align16(a_end)
    b_end = offsets["B"] + len(sections["B"].records) * SAM_SECTIONS["B"].record_size
    offsets["C"] = align16(b_end)
    c_end = offsets["C"] + len(sections["C"].records) * SAM_SECTIONS["C"].record_size

    output = bytearray(c_end)
    output[:4] = SAM_MAGIC
    for name, definition in SAM_SECTIONS.items():
        section = sections[name]
        struct.pack_into("<I", output, definition.count_offset, len(section.records))
        struct.pack_into("<I", output, definition.data_offset_offset, offsets[name])
        cursor = offsets[name]
        for record_data in section.records:
            if len(record_data) != definition.record_size:
                raise FormatError(f"{name} 区存在长度错误的记录")
            output[cursor : cursor + definition.record_size] = record_data
            cursor += definition.record_size
    return bytes(output)


def assemble_text(text: str, encoding_override: str | None = None) -> bytes:
    """解析汇编文本并返回完整二进制文件。"""

    lines = source_lines(text)
    format_name = assembly_format(lines)
    encoding = assembly_encoding(lines, encoding_override)
    if format_name == "MBT0":
        group_offset, slot_count, groups = parse_mbt0(lines, encoding)
        return build_mbt0(group_offset, slot_count, groups)
    sections = parse_sam(lines, encoding)
    return build_sam(sections)


def collect_inputs(arguments: list[str]) -> list[Path]:
    """展开汇编文件和目录参数。"""

    result: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if path.is_file():
            result.append(path)
        elif path.is_dir():
            result.extend(sorted(path.rglob("*.asm.txt")))
        else:
            raise FormatError(f"输入路径不存在：{path}")
    if not result:
        raise FormatError("没有找到 .asm.txt 输入文件")
    return result


def default_output_path(source: Path) -> Path:
    """把 name.asm.txt 转换为 name.rebuild。"""

    suffix = ".asm.txt"
    if source.name.lower().endswith(suffix):
        name = source.name[: -len(suffix)] + ".rebuild"
    else:
        name = source.stem + ".rebuild"
    return source.with_name(name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="汇编 MBT0/SAM DAT 数据库", add_help=False)
    parser._positionals.title = "位置参数"
    parser._optionals.title = "选项"
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出")
    parser.add_argument("inputs", nargs="+", help="输入 .asm.txt 文件或目录，可一次指定多个")
    parser.add_argument("-o", "--output", help="单文件模式的输出路径")
    parser.add_argument("--encoding", help="覆盖汇编头部的 DAT 字符串编码")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        sources = collect_inputs(args.inputs)
        if args.output and len(sources) != 1:
            raise FormatError("-o/--output 只能与单个输入文件一起使用")
        if args.encoding:
            codec_name(args.encoding)

        for source in sources:
            output = Path(args.output) if args.output else default_output_path(source)
            text = source.read_text(encoding="utf-8")
            data = assemble_text(text, args.encoding)
            output.write_bytes(data)
            print(f"已汇编：{source} -> {output}")
        return 0
    except (FormatError, OSError, UnicodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
