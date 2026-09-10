"""Kogado .kgo 语义反汇编器。

输出是可被 assembler.py 重新读取的结构化文本：代码区使用官方 opcode
名称和标签，头部、记录尾部以及非代码段使用 .byte 数据定义保留原字节。
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

from opcodelist import OPCODES, instruction_length


class DisassemblyError(ValueError):
    """输入文件不符合已确认的 .kgo 布局。"""


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _decode_engine_text(raw: bytes, encoding: str) -> str:
    """解码文本并将引擎的 5C 6E 6E 换行标记显示为 \\n。"""
    marker = b"\\nn"
    pieces: list[str] = []
    cursor = 0
    while True:
        position = raw.find(marker, cursor)
        if position < 0:
            pieces.append(raw[cursor:].decode(encoding))
            break
        pieces.append(raw[cursor:position].decode(encoding))
        pieces.append("\\n")
        cursor = position + len(marker)
    return "".join(pieces)


def _encode_engine_text(text: str, encoding: str) -> bytes:
    """将显示用的 \\n 还原为引擎三字节标记。"""
    marker = "\\n"
    parts = text.split(marker)
    return b"\\nn".join(part.encode(encoding) for part in parts)


def _emit_bytes(out: list[str], data: bytes, indent: str = "    ") -> None:
    """以数据定义写出字节，避免把数据混入语义注释。"""
    if not data:
        out.append(f"{indent}; 空数据")
        return
    for start in range(0, len(data), 16):
        chunk = data[start:start + 16]
        out.append(indent + ".byte " + ", ".join(f"0x{byte:02X}" for byte in chunk))


def _parse_records(main: bytes, count: int) -> list[tuple[int, bytes]]:
    records: list[tuple[int, bytes]] = []
    cursor = 0
    for index in range(count):
        if cursor + 4 > len(main):
            raise DisassemblyError(f"主段记录 {index} 的长度字段越界（偏移 0x{cursor:X}）")
        size = _u32(main, cursor)
        if size < 0x0A or cursor + size > len(main):
            raise DisassemblyError(f"主段记录 {index} 尺寸无效：0x{size:X}")
        records.append((cursor, main[cursor:cursor + size]))
        cursor += size
    if cursor != len(main):
        raise DisassemblyError(f"主段记录尺寸总和 0x{cursor:X} 不等于主段长度 0x{len(main):X}")
    return records


def _record_name(record: bytes, name_offset: int) -> str:
    name_start = 0x0A
    name_end = name_start + name_offset
    if name_end > len(record):
        raise DisassemblyError("记录名称偏移超出记录范围")
    raw = record[name_start:name_end]
    nul = raw.find(b"\0")
    if nul >= 0:
        raw = raw[:nul]
    return raw.decode("cp932", errors="replace")


def _decode_code(code: bytes, code_offset: int) -> tuple[list[tuple[int, int, int | None]], set[int], int]:
    """按入口可达控制流切分代码，并恢复连续的死 JMP 链。"""
    instructions_by_pc: dict[int, tuple[int, int, int | None]] = {}
    pending = [code_offset]
    targets: set[int] = set()
    while pending:
        absolute = pending.pop()
        rel = absolute - code_offset
        if rel in instructions_by_pc:
            continue
        if rel < 0 or rel + 2 > len(code):
            raise DisassemblyError(f"代码入口 0x{absolute:X} 越过记录边界")
        opcode = _u16(code, rel)
        length = instruction_length(opcode)
        if rel + length > len(code):
            raise DisassemblyError(f"opcode 0x{opcode:04X} 在 0x{absolute:X} 处越过记录边界")
        operand: int | None = _i32(code, rel + 2) if opcode in (0x01, 0x03, 0x04, 0x05) else None
        instructions_by_pc[rel] = (rel, opcode, operand)
        fallthrough = absolute + length
        if opcode in (0x03, 0x0E, 0x0F):
            successors: list[int] = []
            if opcode == 0x03:
                assert operand is not None
                target = absolute + operand; targets.add(target); successors.append(target)
        elif opcode in (0x04, 0x05):
            assert operand is not None
            target = absolute + operand; targets.add(target)
            successors = [target, fallthrough]
        else:
            successors = [fallthrough]
        pending.extend(successors)
    # 无条件跳转不会把顺序后继加入待解析队列。编译器在条件分支
    # 后经常保留一条或多条连续的死 JMP，这些字节仍属于代码而不是
    # 记录数据。仅识别完整、两字节对齐且目标可归入现有代码（或同类
    # 跳转链）的 0x0003，避免对尾部元数据进行盲目线性反汇编。
    starts = {code_offset + rel for rel in instructions_by_pc}
    initial_code_end = max(
        (rel + instruction_length(opcode) for rel, opcode, _ in instructions_by_pc.values()),
        default=0,
    )
    instruction_ranges = [
        (rel, rel + instruction_length(opcode))
        for rel, opcode, _ in instructions_by_pc.values()
    ]
    orphan_candidates: dict[int, tuple[int, int, int]] = {}
    for rel in range(0, len(code) - 5, 2):
        if rel >= initial_code_end or _u16(code, rel) != 0x03:
            continue
        if any(start < rel + 6 and rel < end for start, end in instruction_ranges):
            continue
        operand = _i32(code, rel + 2)
        target = code_offset + rel + operand
        if code_offset <= target < code_offset + len(code) and (target - code_offset) % 2 == 0:
            orphan_candidates[rel] = (rel, 0x03, operand)

    # 先收录指向已确认代码、或被已确认跳转直接引用的候选；再迭代
    # 收录指向这些候选的跳转，从而覆盖多级死 JMP 链。
    accepted: dict[int, tuple[int, int, int]] = {}
    accepted_starts: set[int] = set()
    changed = True
    while changed:
        changed = False
        for rel, item in sorted(orphan_candidates.items()):
            if rel in accepted:
                continue
            absolute = code_offset + rel
            if any(start < rel + 6 and rel < end for start, end in instruction_ranges):
                continue
            if any(other_rel < rel + 6 and rel < other_rel + 6
                   for other_rel in accepted):
                continue
            target = absolute + item[2]
            if target in starts or target in accepted_starts:
                accepted[rel] = item
                accepted_starts.add(absolute)
                instruction_ranges.append((rel, rel + 6))
                targets.add(target)
                changed = True
    instructions_by_pc.update(accepted)
    starts.update(accepted_starts)
    invalid_targets = targets - starts
    if invalid_targets:
        text = ", ".join(f"0x{target:X}" for target in sorted(invalid_targets))
        raise DisassemblyError(f"跳转目标不是合法指令边界：{text}")
    instructions = [instructions_by_pc[key] for key in sorted(instructions_by_pc)]
    code_end = max((code_offset + rel + instruction_length(opcode) for rel, opcode, _ in instructions), default=code_offset)
    return instructions, targets, code_end


def _decode_aux_record_code(record: bytes, code_offset: int, aux_hint: int):
    """识别边界提示落在代码起点前、但实际仍含 RET/EXIT 代码的记录。

    +0x06 字段并不是运行时取指边界。这里仅针对入口可达、以 RET/EXIT
    终止且其后全为零填充的特殊记录恢复代码；其余记录仍按原始元数据保留。
    """
    if aux_hint >= code_offset:
        return None
    try:
        instructions, targets, code_end = _decode_code(record[code_offset:], code_offset)
    except DisassemblyError:
        return None
    if not instructions or instructions[-1][1] not in (0x0E, 0x0F):
        return None
    # 已确认的此类记录在终止指令后只有零填充；非零尾部仍按原始数据处理。
    if any(record[code_end:]):
        return None
    return instructions, targets, code_end


def _decode_third_strings(data: bytes, count: int, encoding: str) -> list[dict]:
    """解析第三段的 1-based 字符串记录，并保留每条原始记录。"""
    records: list[dict] = []
    cursor = 0
    for index in range(1, count + 1):
        if cursor + 2 > len(data):
            raise DisassemblyError(f"第三段字符串 {index} 的尺寸字段越界")
        size = _u16(data, cursor)
        if size < 2 or cursor + size > len(data):
            raise DisassemblyError(f"第三段字符串 {index} 尺寸无效：0x{size:X}")
        record = data[cursor:cursor + size]
        payload = record[2:]
        nul = payload.find(b"\0")
        text_bytes = payload if nul < 0 else payload[:nul]
        try:
            text = _decode_engine_text(text_bytes, encoding)
            reversible = _encode_engine_text(text, encoding) == text_bytes
        except (UnicodeDecodeError, UnicodeEncodeError):
            text = ""
            reversible = False
        records.append({"index": index, "size": size, "raw": record,
                        "text": text, "reversible": reversible,
                        "has_nul": nul >= 0})
        cursor += size
    if cursor != len(data):
        raise DisassemblyError(f"第三段记录尺寸总和 0x{cursor:X} 不等于第三段长度 0x{len(data):X}")
    return records


def _decode_second_calls(data: bytes, count: int, encoding: str,
                         main_names: dict[int, str] | None = None) -> dict[int, str]:
    """将第二段记录转换为 CALL 内联语法可用的目标映射。"""
    result: dict[int, str] = {}
    cursor = 0
    for index in range(count):
        if cursor + 4 > len(data):
            raise DisassemblyError(f"第二段记录 {index} 的头部越界")
        size = _u16(data, cursor)
        kind = _u16(data, cursor + 2)
        if size < 4 or cursor + size > len(data):
            raise DisassemblyError(f"第二段记录 {index} 尺寸无效：0x{size:X}")
        payload = data[cursor + 4:cursor + size]
        if kind == 1 and len(payload) >= 4:
            main_offset = _u32(payload, 0)
            target_name = (main_names or {}).get(main_offset)
            target = f"@main::{target_name}" if target_name else f"@main+0x{main_offset:X}"
        else:
            nul = payload.find(b"\0")
            raw = payload if nul < 0 else payload[:nul]
            try:
                target = json.dumps(raw.decode(encoding), ensure_ascii=False)
            except UnicodeDecodeError:
                target = "\"\""
        result[cursor] = f"call_{cursor:08X} {target}"
        cursor += size
    if cursor != len(data):
        raise DisassemblyError(f"第二段记录尺寸总和 0x{cursor:X} 不等于第二段长度 0x{len(data):X}")
    return result


def _decode_second_scenes(data: bytes, count: int, encoding: str) -> dict[int, str]:
    """返回 kind=3 场景切换记录，供 SCNCHG 内联标签使用。"""
    result: dict[int, str] = {}
    for record in _decode_second_records(data, count, encoding):
        if int(record["kind"]) == 3:
            offset = int(record["offset"])
            result[offset] = f'call_{offset:08X} {json.dumps(record["target"], ensure_ascii=False)}'
    return result


def _decode_second_records(data: bytes, count: int, encoding: str,
                           main_names: dict[int, str] | None = None) -> list[dict]:
    records: list[dict] = []
    cursor = 0
    for index in range(count):
        if cursor + 4 > len(data):
            raise DisassemblyError(f"第二段记录 {index} 的头部越界")
        size = _u16(data, cursor); kind = _u16(data, cursor + 2)
        if size < 4 or cursor + size > len(data):
            raise DisassemblyError(f"第二段记录 {index} 尺寸无效：0x{size:X}")
        payload = data[cursor + 4:cursor + size]
        if kind == 1 and len(payload) >= 4:
            main_offset = _u32(payload, 0)
            target_name = (main_names or {}).get(main_offset)
            target = f"@main::{target_name}" if target_name else f"@main+0x{main_offset:X}"
        else:
            nul = payload.find(b"\0"); raw = payload if nul < 0 else payload[:nul]
            try:
                target = raw.decode(encoding)
            except UnicodeDecodeError:
                target = ""
        records.append({"index": index, "offset": cursor, "size": size, "kind": kind,
                        "target": target, "raw": data[cursor:cursor + size]})
        cursor += size
    if cursor != len(data):
        raise DisassemblyError(f"第二段记录尺寸总和 0x{cursor:X} 不等于第二段长度 0x{len(data):X}")
    return records


def _decode_scene_records(data: bytes, count: int, encoding: str) -> list[dict]:
    """解析 Scene.tbl 的场景索引记录。"""
    records: list[dict] = []
    cursor = 0
    for index in range(count):
        if cursor + 2 > len(data):
            raise DisassemblyError(f"Scene.tbl 记录 {index} 的长度字段越界")
        size = _u16(data, cursor)
        if size < 0x17 or cursor + size > len(data):
            raise DisassemblyError(f"Scene.tbl 记录 {index} 尺寸无效：0x{size:X}")
        record = data[cursor:cursor + size]
        name_payload = record[0x16:]
        nul = name_payload.find(b"\0")
        if nul < 0:
            raise DisassemblyError(f"Scene.tbl 记录 {index} 缺少 NUL 结尾名称")
        name_raw = name_payload[:nul]
        try:
            name = name_raw.decode(encoding)
            reversible = True
        except UnicodeDecodeError:
            name = ""
            reversible = False
        tail = name_payload[nul + 1:]
        records.append({
            "index": index,
            "offset": cursor,
            "size": size,
            "script_id": _u32(record, 0x02),
            "type": _u16(record, 0x06),
            "event": _u16(record, 0x08),
            "entry_offset": _u32(record, 0x0A),
            "param_a": _u16(record, 0x0E),
            "param_b": _u16(record, 0x10),
            "param_c": _u16(record, 0x12),
            "param_d": _u16(record, 0x14),
            "name": name,
            "name_raw": name_raw,
            "reversible": reversible,
            "tail": tail,
        })
        cursor += size
    if cursor != len(data):
        raise DisassemblyError(
            f"Scene.tbl 记录尺寸总和 0x{cursor:X} 不等于记录区长度 0x{len(data):X}"
        )
    return records


def disassemble_scene_table(data: bytes, source_name: str = "Scene.tbl",
                            encoding: str = "cp932") -> str:
    """反汇编 Scene.tbl 场景名称到 .scene_tbl 结构化文本。"""
    if len(data) < 12:
        raise DisassemblyError("Scene.tbl 小于 12 字节，缺少头部")
    if data[:4] != b"IN10":
        raise DisassemblyError("Scene.tbl 魔数不是 IN10")
    declared_size = _u32(data, 4)
    count = _u32(data, 8)
    if declared_size != len(data) - 12:
        raise DisassemblyError(
            f"Scene.tbl 记录区长度 0x{declared_size:X} 与实际长度 0x{len(data) - 12:X} 不符"
        )
    record_data = data[12:]
    records = _decode_scene_records(record_data, count, encoding)
    out: list[str] = [
        "; Kogado Scene.tbl 语义反汇编，由 disassembler.py 生成",
        f"; 源文件：{source_name}",
        f'.encoding "{encoding}"',
        ".scene_tbl 1",
        f".scene_header record_bytes=0x{declared_size:X} record_count={count}",
    ]
    for record in records:
        out.append("")
        out.append(
            f".scene_record index={record['index']} offset=0x{record['offset']:X} "
            f"size=0x{record['size']:X}"
        )
        out.append(
            f".scene_fields script_id={record['script_id']} type={record['type']} "
            f"event={record['event']} entry_offset=0x{record['entry_offset']:X}"
        )
        out.append(
            f".scene_params param_a={record['param_a']} param_b={record['param_b']} "
            f"param_c={record['param_c']} param_d={record['param_d']}"
        )
        if record["reversible"]:
            out.append(f".scene_name text={json.dumps(record['name'], ensure_ascii=False)}")
        else:
            out.append(f".scene_name_raw size=0x{len(record['name_raw']):X}")
            _emit_bytes(out, record["name_raw"])
            out.append(".end_scene_name_raw")
        tail = record["tail"]
        if tail and not any(tail):
            out.append(f".scene_align alignment=0x2 size=0x{len(tail):X}")
        elif tail:
            out.append(f".scene_tail size=0x{len(tail):X}")
            _emit_bytes(out, tail)
            out.append(".end_scene_tail")
        out.append(".end_scene_record")
    out.append("")
    return "\n".join(out) + "\n"


def _split_inline_operands(text: str) -> list[str]:
    """按逗号拆分内联参数，同时保留引号中的逗号。"""
    values: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for char in text:
        if char == '"' and not escaped:
            quoted = not quoted
        if char == "," and not quoted:
            values.append("".join(current).strip()); current = []; continue
        current.append(char)
        escaped = (char == "\\" and not escaped)
        if char != "\\":
            escaped = False
    if current:
        values.append("".join(current).strip())
    return values


def _emit_code(out: list[str], code: bytes, code_offset: int,
               strings: dict[int, str] | None = None,
               calls: dict[int, str] | None = None,
               scenes: dict[int, str] | None = None,
               orphan_strings: list[dict] | None = None) -> int:
    instructions, targets, code_end = _decode_code(code, code_offset)
    strings = strings or {}
    calls = calls or {}
    scenes = scenes or {}
    if orphan_strings:
        for record in orphan_strings:
            if record["reversible"] and record["has_nul"]:
                out.append(f"    .orphan_string index={record['index']} text={json.dumps(record['text'], ensure_ascii=False)}")
            else:
                out.append(f"    .orphan_string_raw index={record['index']} size=0x{record['size']:X}")
                _emit_bytes(out, record["raw"])
                out.append("    .end_string_raw")
        out.append("")
    # Select 会一次性从值栈取四个字符串参数，但后面没有 Text/Ruby
    # 指令可供常规折叠。记录连续的四条 PUSH 以便内联显示。
    select_text_refs: dict[int, str] = {}
    for select_index, (_, select_opcode, _) in enumerate(instructions):
        if select_opcode != 0x51 or select_index < 4:
            continue
        preceding = instructions[select_index - 4:select_index]
        if not all(push_opcode == 0x01 for _, push_opcode, _ in preceding):
            continue
        for push_index, (_, _, push_operand) in zip(range(select_index - 4, select_index), preceding):
            if push_operand == 0:
                select_text_refs[push_index] = ""
            elif push_operand in strings:
                select_text_refs[push_index] = strings[push_operand]
    skipped: set[int] = set()
    cursor_abs = code_offset
    for index, (pc, opcode, operand) in enumerate(instructions):
        absolute = code_offset + pc
        if absolute > cursor_abs:
            _emit_bytes(out, code[cursor_abs - code_offset:absolute - code_offset])
        original_end = absolute + instruction_length(opcode)
        if index in skipped:
            cursor_abs = max(cursor_abs, original_end)
            continue
        if absolute in targets:
            out.append("")
            out.append(f"loc_{absolute:08X}:")
        definition = OPCODES.get(opcode)
        if definition is None:
            out.append(f"    .opcode 0x{opcode:04X}")
            cursor_abs = max(cursor_abs, original_end)
            continue
        mnemonic = definition["mnemonic"]
        if not definition["valid"]:
            out.append(f"    .opcode 0x{opcode:04X}    ; {mnemonic} 为空 dispatch 槽")
        elif opcode == 0x01:
            if index in select_text_refs and operand is not None:
                out.append(f"    PUSH TextRef {operand} {json.dumps(select_text_refs[index], ensure_ascii=False)}")
                cursor_abs = max(cursor_abs, original_end)
                continue
            # 将常见的 PUSH 字符串/回调 + 主 opcode 折叠成内联语法。
            if index + 1 < len(instructions) and operand is not None:
                next_pc, next_opcode, _ = instructions[index + 1]
                next_absolute = code_offset + next_pc
                if next_absolute not in targets and next_opcode == 0x40 and operand in strings:
                    out.append(f"    PUSH Text {operand} {json.dumps(strings[operand], ensure_ascii=False)}")
                    skipped.add(index + 1)
                    cursor_abs = max(cursor_abs, original_end)
                    continue
                if next_absolute not in targets and next_opcode == 0x06 and operand in calls:
                    out.append(f"    CALL         {calls[operand]}")
                    skipped.add(index + 1)
                    cursor_abs = max(cursor_abs, original_end)
                    continue
                if next_absolute not in targets and next_opcode == 0x30 and operand in scenes:
                    out.append(f"    SCNCHG       {scenes[operand]}")
                    skipped.add(index + 1)
                    cursor_abs = max(cursor_abs, original_end)
                    continue
                if (index + 2 < len(instructions) and next_absolute not in targets
                        and instructions[index + 2][1] == 0x48
                        and instructions[index + 1][2] in strings
                        and code_offset + instructions[index + 2][0] not in targets):
                    out.append("    PUSH Ruby " + str(operand) + " " + json.dumps(strings[operand], ensure_ascii=False)
                               + " " + str(instructions[index + 1][2]) + " "
                               + json.dumps(strings[instructions[index + 1][2]], ensure_ascii=False))
                    skipped.update((index + 1, index + 2))
                    cursor_abs = max(cursor_abs, original_end)
                    continue
            note = ""
            next_opcode = instructions[index + 1][1] if index + 1 < len(instructions) else None
            if operand is not None and next_opcode in (0x40, 0x48) and operand in strings:
                note = f"    ; 文本引用：{json.dumps(strings[operand], ensure_ascii=False)}"
            out.append(f"    {mnemonic:<12} {operand}{note}")
        elif opcode in (0x03, 0x04, 0x05):
            assert operand is not None
            target = absolute + operand
            out.append(f"    {mnemonic:<12} loc_{target:08X}")
        elif opcode == 0x06 and operand is None:
            out.append(f"    {mnemonic}")
        else:
            note = ""
            if opcode == 0x40:
                note = "    ; 使用栈顶字符串引用显示文本"
            out.append(f"    {mnemonic}{note}")
        cursor_abs = max(cursor_abs, original_end)
    return code_end


def _header_lines(out: list[str], header: bytes) -> None:
    out.append(
        f".header file_length=0x{_u32(header, 4):X} script_id={_u32(header, 8)} "
        f"checksum=0x{_u32(header, 0x0C):08X}"
    )
    # 每段单独一行，避免把十二个头部字段挤成难以阅读的长行。
    for name, offset_field, size_field, count_field in (
        ("seg0", 0x10, 0x14, 0x18),
        ("main", 0x1C, 0x20, 0x24),
        ("second", 0x28, 0x2C, 0x30),
        ("third", 0x34, 0x38, 0x3C),
    ):
        out.append(
            f".header_segment name={name} offset=0x{_u32(header, offset_field):X} "
            f"size=0x{_u32(header, size_field):X} count={_u32(header, count_field)}"
        )


def _segment_ranges(header: bytes, file_size: int) -> list[dict[str, int | str]]:
    fields = [
        ("seg0", 0x10, 0x14, 0x18),
        ("main", 0x1C, 0x20, 0x24),
        ("second", 0x28, 0x2C, 0x30),
        ("third", 0x34, 0x38, 0x3C),
    ]
    result = []
    for name, offset_field, size_field, count_field in fields:
        offset = _u32(header, offset_field)
        size = _u32(header, size_field)
        count = _u32(header, count_field)
        if size and (offset < 64 or offset + size > file_size):
            raise DisassemblyError(f"{name} 段范围越界：offset=0x{offset:X}, size=0x{size:X}")
        result.append({"name": name, "offset": offset, "size": size, "count": count,
                       "offset_field": offset_field, "size_field": size_field,
                       "count_field": count_field})
    nonempty = sorted((item for item in result if item["size"]), key=lambda item: int(item["offset"]))
    for previous, current in zip(nonempty, nonempty[1:]):
        if int(previous["offset"]) + int(previous["size"]) > int(current["offset"]):
            raise DisassemblyError(f"段范围重叠：{previous['name']} 与 {current['name']}")
    return result


def disassemble_bytes(data: bytes, source_name: str = "input.kgo", encoding: str = "cp932") -> str:
    if Path(source_name).name.lower() == "scene.tbl":
        return disassemble_scene_table(data, source_name, encoding)
    if len(data) < 64:
        raise DisassemblyError("文件小于 64 字节，缺少 IN10 头")
    if data[:4] != b"IN10":
        raise DisassemblyError("文件魔数不是 IN10")
    declared_size = _u32(data, 4)
    if declared_size not in (0, len(data)):
        raise DisassemblyError(f"头部文件长度 0x{declared_size:X} 与实际长度 0x{len(data):X} 不符")
    header = data[:64]
    ranges = _segment_ranges(header, len(data))
    out: list[str] = [
        "; Kogado .kgo 语义汇编，由 disassembler.py 生成",
        f"; 源文件：{source_name}",
        f'.encoding "{encoding}"',
        ".kgo_asm 1",
    ]
    _header_lines(out, header)

    segments = {str(item["name"]): item for item in ranges}
    second_item = segments["second"]
    second_calls: dict[int, str] = {}
    second_scenes: dict[int, str] = {}
    third_item = segments["third"]
    third_strings: dict[int, str] = {}
    third_records: list[dict] = []
    if int(third_item["size"]):
        third_bytes = data[int(third_item["offset"]):int(third_item["offset"]) + int(third_item["size"])]
        third_records = _decode_third_strings(third_bytes, int(third_item["count"]), encoding)
        third_strings = {int(record["index"]): str(record["text"]) for record in third_records if record["reversible"]}
    main = segments["main"]
    main_data = data[int(main["offset"]):int(main["offset"]) + int(main["size"])]
    records = _parse_records(main_data, int(main["count"]))
    main_names = {
        record_offset: _record_name(record, _u16(record, 4))
        for record_offset, record in records
    }
    # 头部之后到各段之间的填充单独保留，长度变化时汇编器会按原顺序搬移。
    occupied = [(0, 64, "header")]
    for item in ranges:
        if int(item["size"]):
            occupied.append((int(item["offset"]), int(item["offset"]) + int(item["size"]), str(item["name"])))
    occupied.sort()
    previous_end = 64
    gap_index = 0
    for start, end, _ in occupied:
        if start > previous_end:
            gap = data[previous_end:start]
            if not any(gap):
                out.append(f".padding offset=0x{previous_end:X} size=0x{start - previous_end:X} fill=0x00")
            else:
                out.append(f".gap offset=0x{previous_end:X} size=0x{start - previous_end:X} index={gap_index}")
                _emit_bytes(out, gap)
                out.append(".end_gap")
            gap_index += 1
        previous_end = max(previous_end, end)
    if previous_end < len(data):
        gap = data[previous_end:]
        if not any(gap):
            out.append(f".padding offset=0x{previous_end:X} size=0x{len(data) - previous_end:X} fill=0x00")
        else:
            out.append(f".gap offset=0x{previous_end:X} size=0x{len(data) - previous_end:X} index={gap_index}")
            _emit_bytes(out, gap)
            out.append(".end_gap")

    seg0 = segments["seg0"]
    if int(seg0["size"]):
        seg0_bytes = data[int(seg0["offset"]):int(seg0["offset"]) + int(seg0["size"])]
        if len(seg0_bytes) < 0x16:
            raise DisassemblyError("段0 记录短于已确认字段区")
        fields = (
            f"record_size=0x{_u16(seg0_bytes, 0):X} "
            f"script_id={_u32(seg0_bytes, 2)} type={_u16(seg0_bytes, 6)} event={_u16(seg0_bytes, 8)} "
            f"entry_offset=0x{_u32(seg0_bytes, 0x0A):X} "
            f"param_a={_u16(seg0_bytes, 0x0E)} param_b={_u16(seg0_bytes, 0x10)} "
            f"param_c={_u16(seg0_bytes, 0x12)} param_d={_u16(seg0_bytes, 0x14)}"
        )
        name_raw = seg0_bytes[0x16:].split(b"\0", 1)[0]
        try:
            meta_name = name_raw.decode(encoding)
        except UnicodeDecodeError:
            meta_name = ""
        out.append(f".segment_meta name=seg0 offset=0x{int(seg0['offset']):X} size=0x{int(seg0['size']):X} count={int(seg0['count'])}")
        out.append(
            f".segment_meta_fields record_size=0x{_u16(seg0_bytes, 0):X} "
            f"script_id={_u32(seg0_bytes, 2)}"
        )
        out.append(
            f".segment_meta_fields type={_u16(seg0_bytes, 6)} event={_u16(seg0_bytes, 8)} "
            + (
                f"entry_label={json.dumps(main_names[_u32(seg0_bytes, 0x0A)], ensure_ascii=False)}"
                if _u32(seg0_bytes, 0x0A) in main_names
                else f"entry_offset=0x{_u32(seg0_bytes, 0x0A):X}"
            )
        )
        out.append(
            f".segment_meta_fields param_a={_u16(seg0_bytes, 0x0E)} "
            f"param_b={_u16(seg0_bytes, 0x10)} param_c={_u16(seg0_bytes, 0x12)} "
            f"param_d={_u16(seg0_bytes, 0x14)}"
        )
        out.append(f".segment_meta_name text={json.dumps(meta_name, ensure_ascii=False)}")

    if int(second_item["size"]):
        second_bytes = data[int(second_item["offset"]):int(second_item["offset"]) + int(second_item["size"])]
        second_calls = _decode_second_calls(second_bytes, int(second_item["count"]), encoding, main_names)
        second_scenes = _decode_second_scenes(second_bytes, int(second_item["count"]), encoding)
    out.append(f".segment_main name=main offset=0x{int(main['offset']):X} size=0x{int(main['size']):X} count={int(main['count'])}")
    used_string_indices: set[int] = set()
    for _, record in records:
        name_offset = _u16(record, 4); aux_hint = _u32(record, 6); code_offset = 0x0A + name_offset
        if aux_hint < code_offset and _decode_aux_record_code(record, code_offset, aux_hint) is None:
            continue
        decoded = _decode_aux_record_code(record, code_offset, aux_hint)
        instructions = decoded[0] if decoded is not None else _decode_code(record[code_offset:], code_offset)[0]
        for i, (_, opcode, operand) in enumerate(instructions):
            if opcode == 0x01 and operand is not None and i + 1 < len(instructions) and instructions[i + 1][1] in (0x40, 0x48):
                used_string_indices.add(operand)
            if opcode == 0x01 and operand is not None and i + 2 < len(instructions) and instructions[i + 2][1] == 0x48:
                used_string_indices.add(operand)
                if instructions[i + 1][2] is not None:
                    used_string_indices.add(instructions[i + 1][2])
            if opcode == 0x51:
                if i >= 4:
                    preceding = instructions[i - 4:i]
                    if all(previous_opcode == 0x01 for _, previous_opcode, _ in preceding):
                        for _, _, previous_operand in preceding:
                            if previous_operand is not None and previous_operand > 0:
                                used_string_indices.add(previous_operand)
    orphan_records = [record for record in third_records if int(record["index"]) not in used_string_indices]
    for index, (record_offset, record) in enumerate(records):
        name_offset = _u16(record, 4)
        aux_hint = _u32(record, 6)
        code_offset = 0x0A + name_offset
        # aux_hint 是未由运行时取指消费的 uint32 辅助字段；即使它不落在
        # 当前记录内也应原样保留。只有经过反编译确认的代码入口需要校验。
        if code_offset > len(record):
            raise DisassemblyError(f"主段记录 {index} 的代码入口越过记录边界")
        name = _record_name(record, name_offset)
        out.append(f".record index={index} name={json.dumps(name, ensure_ascii=False)}")
        out.append(
            f".record_layout offset=0x{record_offset:X} size=0x{len(record):X} "
            f"name_offset=0x{name_offset:X} aux_hint=0x{aux_hint:X} code_offset=0x{code_offset:X}"
        )
        aux_code = _decode_aux_record_code(record, code_offset, aux_hint)
        if aux_hint < code_offset and aux_code is None:
            out.append("; 无入口可达代码：以下为事件参数元数据（uint16）")
            values = [str(_u16(record, p)) for p in range(0, len(record) - 1, 2)]
            # 事件/参数记录不是 VM 指令；按固定小组换行，既保留全部字节又便于人工核对。
            for start in range(0, len(values), 8):
                out.append(
                    f".record_data offset=0x{start * 2:X} values="
                    + ",".join(values[start:start + 8])
                )
            out.append(".end_record")
            continue
        out.append(".code")
        code_end = _emit_code(out, record[code_offset:], code_offset, third_strings, second_calls,
                              second_scenes,
                              orphan_records if index == 0 else None)
        out.append(".end_code")
        tail = record[code_end:]
        if tail and not any(tail) and len(record) % 0x10 == 0 and len(tail) == ((-code_end) % 0x10):
            # 记录尾部仅用于 16 字节对齐时省略零字节；汇编器按 size/align 自动补齐。
            out.append(f".tail_align alignment=0x10 size=0x{len(tail):X}")
        elif tail:
            out.append(".tail")
            if len(tail) % 2 == 0:
                out.append("    .tail_meta values=" + ",".join(str(_u16(tail, p)) for p in range(0, len(tail), 2)))
            else:
                _emit_bytes(out, tail)
            out.append(".end_tail")
        out.append(".end_record")
    out.append(".end_segment_main")

    if int(second_item["size"]):
        second_bytes = data[int(second_item["offset"]):int(second_item["offset"]) + int(second_item["size"])]
        out.append(f".segment_calls name=second offset=0x{int(second_item['offset']):X} size=0x{int(second_item['size']):X} count={int(second_item['count'])}")
        for record in _decode_second_records(second_bytes, int(second_item["count"]), encoding, main_names):
            label = f"call_{int(record['offset']):08X}"
            out.append(f"{label}:")
            out.append(
                f".call index={record['index']} offset=0x{record['offset']:X} "
                f"size=0x{record['size']:X} kind={record['kind']} "
                f"target={json.dumps(record['target'], ensure_ascii=False)}"
            )
        out.append(".end_segment_calls")
    out.append("")
    return "\n".join(out) + "\n"


def disassemble_file(path: Path, output: Path | None = None, encoding: str = "cp932") -> Path:
    text = disassemble_bytes(path.read_bytes(), path.name, encoding)
    target = output or path.with_suffix(".asm.txt")
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def disassemble_scene_file(path: Path, output: Path | None = None,
                           encoding: str = "cp932") -> Path:
    text = disassemble_scene_table(path.read_bytes(), path.name, encoding)
    target = output or path.with_name(path.name + ".asm.txt")
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="反汇编 Kogado .kgo 脚本和 Scene.tbl")
    parser.add_argument("inputs", nargs="+", help=".kgo/.tbl 文件或目录")
    parser.add_argument("-o", "--output", help="单文件输出路径")
    parser.add_argument("--encoding", default="cp932", help="脚本文本编码，默认 cp932")
    args = parser.parse_args(argv)
    paths: list[Path] = []
    for value in args.inputs:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.kgo")))
            paths.extend(sorted(path.glob("*.tbl")))
        else:
            paths.append(path)
    if not paths:
        parser.error("没有找到 .kgo 或 .tbl 输入文件")
    if args.output and len(paths) != 1:
        parser.error("批处理时不能只指定一个 --output")
    for path in paths:
        try:
            output = Path(args.output) if args.output else None
            if path.suffix.lower() == ".tbl":
                target = disassemble_scene_file(path, output, args.encoding)
            else:
                target = disassemble_file(path, output, args.encoding)
            print(f"已生成：{target}")
        except (OSError, DisassemblyError, UnicodeError) as exc:
            print(f"反汇编失败 {path}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
