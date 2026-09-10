"""MBT0 与 SAM DAT 文件反汇编器。"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from opcodelist import (
    FORMAT_DEFINITIONS,
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
    SAM_SECTIONS,
    FormatError,
    codec_name,
    quoted_bytes,
)


@dataclass(frozen=True)
class MessageGroup:
    """已经验证闭合关系的消息分组。"""

    name: bytes
    first_index: int
    strings: tuple[bytes, ...]


def read_u32(data: bytes, offset: int, field: str) -> int:
    """读取小端 u32，并提供可定位的越界错误。"""

    if offset < 0 or offset + 4 > len(data):
        raise FormatError(f"{field} 位于文件范围之外：0x{offset:X}")
    return struct.unpack_from("<I", data, offset)[0]


def read_zstring(data: bytes, offset: int, field: str) -> tuple[bytes, int]:
    """读取 NUL 结尾字节串，返回内容和结束 NUL 的位置。"""

    if offset < 0 or offset >= len(data):
        raise FormatError(f"{field} 偏移越界：0x{offset:X}")
    end = data.find(b"\x00", offset)
    if end < 0:
        raise FormatError(f"{field} 缺少 NUL 结束符：0x{offset:X}")
    return data[offset:end], end


def require_zero(data: bytes, start: int, end: int, field: str) -> None:
    """确认可由汇编器隐式重建的区域全部为零。"""

    if start < 0 or end < start or end > len(data):
        raise FormatError(f"{field} 范围非法：0x{start:X}..0x{end:X}")
    position = next((index for index, value in enumerate(data[start:end]) if value), None)
    if position is not None:
        raise FormatError(f"{field} 含非零字节：0x{start + position:X}")


def disassemble_mbt0(data: bytes, encoding: str) -> str:
    """把完整闭合的 MBT0 数据库转换为分组和 TEXT。"""

    if len(data) < FORMAT_DEFINITIONS["MBT0"]["header_size"]:
        raise FormatError("MBT0 文件短于 0x10 字节头部")
    if data[:4] != MBT0_MAGIC:
        raise FormatError("文件魔数不是 MBT0")

    group_count = read_u32(data, 0x04, "分组数量")
    slot_count = read_u32(data, 0x08, "字符串偏移槽数量")
    group_table_offset = read_u32(data, 0x0C, "分组表偏移")
    if slot_count < 1:
        raise FormatError("MBT0 至少需要索引 0 空槽")

    offset_table_end = 0x10 + slot_count * 4
    group_table_end = group_table_offset + group_count * 12
    if offset_table_end > group_table_offset:
        raise FormatError("字符串偏移表与分组表重叠")
    if group_table_end > len(data):
        raise FormatError("分组表超出文件范围")
    require_zero(data, offset_table_end, group_table_offset, "偏移表尾部填充")

    offsets = [read_u32(data, 0x10 + index * 4, f"字符串偏移[{index}]") for index in range(slot_count)]
    if offsets[0] != 0:
        raise FormatError("字符串索引 0 的偏移必须为零")

    raw_groups: list[tuple[int, int, int]] = []
    expected_string_index = 1
    name_cursor = group_table_end
    group_names: list[bytes] = []
    for index in range(group_count):
        record = group_table_offset + index * 12
        first_index = read_u32(data, record, f"分组[{index}] 首字符串索引")
        count = read_u32(data, record + 4, f"分组[{index}] 字符串数量")
        name_offset = read_u32(data, record + 8, f"分组[{index}] 名称偏移")
        if first_index != expected_string_index:
            raise FormatError(
                f"分组[{index}] 字符串索引不连续：期望 {expected_string_index}，实际 {first_index}"
            )
        if first_index + count > slot_count:
            raise FormatError(f"分组[{index}] 引用了偏移表范围之外的字符串")
        if name_offset != name_cursor:
            raise FormatError(
                f"分组[{index}] 名称池不连续：期望 0x{name_cursor:X}，实际 0x{name_offset:X}"
            )
        name, name_end = read_zstring(data, name_offset, f"分组[{index}] 名称")
        try:
            name.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise FormatError(f"分组[{index}] 名称不是 ASCII") from exc
        raw_groups.append((first_index, count, name_offset))
        group_names.append(name)
        expected_string_index += count
        name_cursor = name_end + 1

    if expected_string_index != slot_count:
        raise FormatError(
            f"分组只覆盖到字符串索引 {expected_string_index - 1}，偏移槽数量为 {slot_count}"
        )

    string_cursor = (name_cursor + 3) & ~3
    require_zero(data, name_cursor, string_cursor, "消息字符串池之前的对齐填充")
    strings: list[bytes] = [b""]
    for index in range(1, slot_count):
        if offsets[index] != string_cursor:
            raise FormatError(
                f"消息字符串池在索引 {index} 不连续：期望 0x{string_cursor:X}，实际 0x{offsets[index]:X}"
            )
        value, value_end = read_zstring(data, offsets[index], f"消息字符串[{index}]")
        next_offset = offsets[index + 1] if index + 1 < slot_count else len(data)
        content_end = value_end + 1
        if next_offset < content_end:
            raise FormatError(f"消息字符串[{index}] 与下一字符串重叠")
        require_zero(data, content_end, next_offset, f"消息字符串[{index}] 尾部填充")
        expected_next = (content_end + 3) & ~3
        if next_offset != expected_next:
            raise FormatError(
                f"消息字符串[{index}] 未按 4 字节对齐：期望下一偏移 0x{expected_next:X}，实际 0x{next_offset:X}"
            )
        strings.append(value)
        string_cursor = next_offset

    groups: list[MessageGroup] = []
    for name, (first_index, count, _name_offset) in zip(group_names, raw_groups, strict=True):
        groups.append(MessageGroup(name, first_index, tuple(strings[first_index : first_index + count])))

    lines = [
        "; MBT0 消息数据库",
        ".format MBT0",
        f'.encoding "{encoding}"',
        f".group_table_offset 0x{group_table_offset:X}",
        f".string_slot_count {slot_count}",
    ]
    for group in groups:
        lines.extend(
            [
                "",
                f".group {quoted_bytes(group.name, 'ascii')}",
                *(f"TEXT {quoted_bytes(value, encoding)}" for value in group.strings),
                ".endgroup",
            ]
        )
    return "\n".join(lines) + "\n"


def read_fixed_string(record: bytes, offset: int, size: int, field: str) -> bytes:
    """读取固定槽中的 NUL 字符串，并确认未使用尾部全为零。"""

    end = offset + size
    if offset < 0 or end > len(record):
        raise FormatError(f"{field} 字符串槽越界：0x{offset:X}..0x{end:X}")
    slot = record[offset:end]
    nul = slot.find(b"\x00")
    if nul < 0:
        raise FormatError(f"{field} 缺少 NUL 结束符")
    require_zero(slot, nul + 1, len(slot), f"{field} 字符串槽尾部")
    return slot[:nul]


def mark_range(covered: bytearray, offset: int, size: int) -> None:
    """标记已经由语义字段解释的记录范围。"""

    covered[offset : offset + size] = b"\x01" * size


def require_covered(record: bytes, covered: bytearray, field: str) -> None:
    """拒绝语义模型之外的非零字节，防止静默丢失数据。"""

    for offset, value in enumerate(record):
        if value and not covered[offset]:
            raise FormatError(f"{field} 在未定义偏移 0x{offset:X} 含非零数据")


def append_text(lines: list[str], name: str, value: bytes, encoding: str) -> None:
    """只输出非空字符串；空槽由汇编器自动补零。"""

    if value:
        lines.append(f"{name} {quoted_bytes(value, encoding)}")


def append_u32(lines: list[str], name: str, record: bytes, offset: int) -> None:
    value = read_u32(record, offset, name)
    if value:
        lines.append(f"{name} {value}")


def disassemble_skill_record(record: bytes, encoding: str, record_index: int) -> list[str]:
    """反汇编一条 A 区技能记录。"""

    covered = bytearray(len(record))
    lines = [".record"]
    name_offset, name_size = SAM_A_RECORD_NAME
    record_name = read_fixed_string(record, name_offset, name_size, f"技能[{record_index}] 名称")
    mark_range(covered, name_offset, name_size)
    append_text(lines, "NAME", record_name, encoding)

    for level_index in range(SAM_A_LEVEL_COUNT):
        block = level_index * SAM_A_LEVEL_STRIDE
        header = block + SAM_A_LEVEL_HEADER
        level_lines: list[str] = []

        for field_name, (relative, size) in SAM_A_LEVEL_TEXTS.items():
            offset = block + relative
            value = read_fixed_string(
                record,
                offset,
                size,
                f"技能[{record_index}] 等级[{level_index}] {field_name}",
            )
            mark_range(covered, offset, size)
            append_text(level_lines, field_name, value, encoding)

        level_name = read_fixed_string(
            record,
            header,
            0x3C,
            f"技能[{record_index}] 等级[{level_index}] 名称",
        )
        mark_range(covered, header, 0x3C)

        for field_name, relative in SAM_A_LEVEL_FIELDS.items():
            offset = header + relative
            mark_range(covered, offset, 4)
            value = read_u32(record, offset, field_name)
            if value:
                level_lines.append(f"{field_name} {value}")

        for slot in range(SAM_A_EFFECT_COUNT):
            offset = header + SAM_A_EFFECT_OFFSET + slot * SAM_A_EFFECT_SIZE
            values = struct.unpack_from("<5I", record, offset)
            mark_range(covered, offset, 20)
            if any(values):
                keys = ("id", "type", "value", "probability", "target")
                operands = [f"slot={slot}"]
                operands.extend(f"{key}={value}" for key, value in zip(keys, values) if value)
                level_lines.append("EFFECT " + " ".join(operands))

        for special_index, field_name in enumerate(SAM_A_SPECIAL_NAMES):
            offset = header + SAM_A_SPECIAL_OFFSET + special_index * 4
            mark_range(covered, offset, 4)
            value = read_u32(record, offset, field_name)
            if value:
                level_lines.append(f"SPECIAL {field_name} {value}")

        for kind, relative in (("BUFF", SAM_A_BUFF_OFFSET), ("DEBUFF", SAM_A_DEBUFF_OFFSET)):
            offset = header + relative
            category, turns = struct.unpack_from("<2I", record, offset)
            mark_range(covered, offset, 8)
            if category or turns:
                operands = []
                if category:
                    operands.append(f"category={category}")
                if turns:
                    operands.append(f"turns={turns}")
                level_lines.append(f"{kind} " + " ".join(operands))
            for slot in range(SAM_A_STATUS_EFFECT_COUNT):
                item_offset = offset + 8 + slot * 8
                effect_id, rank = struct.unpack_from("<2I", record, item_offset)
                mark_range(covered, item_offset, 8)
                if effect_id or rank:
                    operands = [f"slot={slot}"]
                    if effect_id:
                        operands.append(f"id={effect_id}")
                    if rank:
                        operands.append(f"rank={rank}")
                    level_lines.append(f"{kind}_EFFECT " + " ".join(operands))

        if level_name or level_lines:
            if level_name != record_name:
                level_lines.insert(0, f"NAME {quoted_bytes(level_name, encoding)}")
            lines.append(f".level {level_index}")
            lines.extend(level_lines)
            lines.append(".endlevel")

    for field_name, offset in SAM_A_TAIL_U32_FIELDS.items():
        mark_range(covered, offset, 4)
        append_u32(lines, field_name, record, offset)
    for field_name, offset in SAM_A_TAIL_U8_FIELDS.items():
        mark_range(covered, offset, 1)
        value = record[offset]
        if value:
            lines.append(f"{field_name} {value}")

    require_covered(record, covered, f"技能记录[{record_index}]")
    lines.append(".endrecord")
    return lines


def disassemble_item_record(record: bytes, encoding: str, record_index: int) -> list[str]:
    """反汇编一条 B 区礼装/装备记录。"""

    covered = bytearray(len(record))
    lines = [".record"]
    for field_name, (offset, size) in SAM_B_TEXTS.items():
        value = read_fixed_string(record, offset, size, f"装备[{record_index}] {field_name}")
        mark_range(covered, offset, size)
        append_text(lines, field_name, value, encoding)
    for field_name, offset in SAM_B_U32_FIELDS.items():
        mark_range(covered, offset, 4)
        append_u32(lines, field_name, record, offset)

    for index in range(SAM_B_PASSIVE_COUNT):
        offset = SAM_B_PASSIVE_OFFSET + index * 4
        mark_range(covered, offset, 4)
        value = read_u32(record, offset, f"被动属性[{index}]")
        if value:
            lines.append(f"PASSIVE_ATTRIBUTE index={index} value={value}")

    special_type = read_u32(record, SAM_B_SPECIAL_TYPE, "特殊属性类型")
    special_value = read_u32(record, SAM_B_SPECIAL_VALUE, "特殊属性值")
    mark_range(covered, SAM_B_SPECIAL_TYPE, 8)
    if special_type or special_value:
        operands = []
        if special_type:
            operands.append(f"type={special_type}")
        if special_value:
            operands.append(f"value={special_value}")
        lines.append("SPECIAL_ATTRIBUTE " + " ".join(operands))

    for index in range(SAM_B_MATERIAL_COUNT):
        offset = SAM_B_MATERIAL_OFFSET + index * 4
        mark_range(covered, offset, 4)
        value = read_u32(record, offset, f"材料[{index}]")
        if value:
            lines.append(f"MATERIAL index={index} id={value}")

    require_covered(record, covered, f"装备记录[{record_index}]")
    lines.append(".endrecord")
    return lines


def disassemble_profile_record(record: bytes, encoding: str, record_index: int) -> list[str]:
    """反汇编一条 C 区角色/战斗配置记录。"""

    covered = bytearray(len(record))
    lines = [".record"]
    name_offset, name_size = SAM_C_NAME
    name = read_fixed_string(record, name_offset, name_size, f"配置[{record_index}] 名称")
    mark_range(covered, name_offset, name_size)
    append_text(lines, "NAME", name, encoding)
    for field_name, offset in SAM_C_U32_FIELDS.items():
        mark_range(covered, offset, 4)
        append_u32(lines, field_name, record, offset)

    for index in range(SAM_C_STAT_COUNT):
        base_offset = SAM_C_BASE_STAT_OFFSET + index * 4
        growth_offset = SAM_C_GROWTH_STAT_OFFSET + index * 4
        base_value = read_u32(record, base_offset, f"基础参数[{index}]")
        growth_value = read_u32(record, growth_offset, f"成长参数[{index}]")
        mark_range(covered, base_offset, 4)
        mark_range(covered, growth_offset, 4)
        if base_value or growth_value:
            operands = [f"index={index}"]
            if base_value:
                operands.append(f"base={base_value}")
            if growth_value:
                operands.append(f"growth={growth_value}")
            lines.append("STAT " + " ".join(operands))

    mark_range(covered, SAM_C_ATTACK_TYPE, 4)
    attack_type = read_u32(record, SAM_C_ATTACK_TYPE, "攻击类型")
    if attack_type:
        names = {1: "PHYSICAL", 2: "MAGICAL"}
        lines.append(f"ATTACK_TYPE {names.get(attack_type, attack_type)}")

    for index in range(SAM_C_EFFECT_RATE_COUNT):
        offset = SAM_C_EFFECT_RATE_OFFSET + index * 4
        mark_range(covered, offset, 4)
        value = read_u32(record, offset, f"效果倍率[{index}]")
        if value:
            effect_type = SAM_C_EFFECT_RATE_FIRST + index
            lines.append(f"EFFECT_RATE type={effect_type} value={value}")

    require_covered(record, covered, f"配置记录[{record_index}]")
    lines.append(".endrecord")
    return lines


def disassemble_sam(data: bytes, encoding: str) -> str:
    """把 SAM 固定记录数据库转换为不含原始数据块的语义源码。"""

    if len(data) < FORMAT_DEFINITIONS["SAM"]["header_size"]:
        raise FormatError("SAM 文件短于 0x28 字节头部")
    if data[:4] != SAM_MAGIC:
        raise FormatError("文件魔数不是 SAM\\0")
    require_zero(data, 0x04, 0x08, "SAM 头部保留字段")
    require_zero(data, 0x20, 0x28, "SAM 头部尾部保留字段")

    sections: list[tuple[str, int, int, bytes]] = []
    for name in ("A", "B", "C"):
        definition = SAM_SECTIONS[name]
        count = read_u32(data, definition.count_offset, f"{name} 区记录数")
        offset = read_u32(data, definition.data_offset_offset, f"{name} 区偏移")
        end = offset + count * definition.record_size
        if offset < 0x28 or end > len(data):
            raise FormatError(f"{name} 区范围越界：0x{offset:X}..0x{end:X}")
        sections.append((name, offset, definition.record_size, data[offset:end]))

    cursor = 0x28
    for name, offset, _record_size, raw in sorted(sections, key=lambda item: item[1]):
        if offset < cursor:
            raise FormatError(f"{name} 区与前一区段重叠")
        require_zero(data, cursor, offset, f"{name} 区之前的自动对齐")
        cursor = offset + len(raw)
    if cursor != len(data):
        raise FormatError(f"最后一个 SAM 区段之后仍有 {len(data) - cursor} 字节数据")

    decoders = {
        "A": disassemble_skill_record,
        "B": disassemble_item_record,
        "C": disassemble_profile_record,
    }
    lines = ["; SAM 参数数据库", ".format SAM", f'.encoding "{encoding}"']
    for name, _offset, record_size, raw in sections:
        lines.extend(["", f".section {SAM_SECTIONS[name].source_name}"])
        for index in range(len(raw) // record_size):
            start = index * record_size
            record = raw[start : start + record_size]
            lines.append("")
            lines.extend(decoders[name](record, encoding, index))
        lines.append(".endsection")
    return "\n".join(lines) + "\n"


def disassemble_bytes(data: bytes, encoding: str) -> str:
    """按魔数选择结构解析器。"""

    if data.startswith(MBT0_MAGIC):
        return disassemble_mbt0(data, encoding)
    if data.startswith(SAM_MAGIC):
        return disassemble_sam(data, encoding)
    magic = data[:4].hex(" ").upper()
    raise FormatError(f"不支持的文件魔数：{magic or '空文件'}")


def supported_file(path: Path) -> bool:
    """目录批处理时只选择已知魔数文件。"""

    if not path.is_file():
        return False
    try:
        with path.open("rb") as stream:
            magic = stream.read(4)
    except OSError:
        return False
    return magic in {MBT0_MAGIC, SAM_MAGIC}


def collect_inputs(arguments: list[str]) -> list[Path]:
    """展开文件和目录参数，目录采用递归批处理。"""

    result: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if path.is_file():
            result.append(path)
        elif path.is_dir():
            result.extend(candidate for candidate in sorted(path.rglob("*")) if supported_file(candidate))
        else:
            raise FormatError(f"输入路径不存在：{path}")
    if not result:
        raise FormatError("没有找到 MBT0 或 SAM 输入文件")
    return result


def default_output_path(source: Path) -> Path:
    return source.with_name(source.name + ".asm.txt")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="反汇编 MBT0/SAM DAT 数据库", add_help=False)
    parser._positionals.title = "位置参数"
    parser._optionals.title = "选项"
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出")
    parser.add_argument("inputs", nargs="+", help="输入文件或目录，可一次指定多个")
    parser.add_argument("-o", "--output", help="单文件模式的输出路径")
    parser.add_argument("--encoding", default="cp932", help="DAT 字符串编码，默认 cp932")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        encoding = codec_name(args.encoding)
        sources = collect_inputs(args.inputs)
        if args.output and len(sources) != 1:
            raise FormatError("-o/--output 只能与单个输入文件一起使用")

        for source in sources:
            output = Path(args.output) if args.output else default_output_path(source)
            data = source.read_bytes()
            assembly = disassemble_bytes(data, encoding)
            with output.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(assembly)
            print(f"已反汇编：{source} -> {output}")
        return 0
    except (FormatError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
