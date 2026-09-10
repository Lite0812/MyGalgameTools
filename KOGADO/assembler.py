"""Kogado .kgo 语义汇编器。

该汇编器读取 disassembler.py 生成的结构化文本，解析官方 opcode、标签和
数据定义，并重建 IN10 头、主段记录以及第二/第三段。无语义修改时，原段
偏移、记录尾部和填充字节会按原位置写回。
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

from opcodelist import NAME_TO_OPCODE, OPCODES, instruction_length


class AssemblyError(ValueError):
    """汇编文本或布局不合法。"""


_KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\"(?:\\.|[^\"])*\"|\S+)")


def _parse_int(value: str | int) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(value, 0)
    except ValueError as exc:
        raise AssemblyError(f"无法解析整数：{value}") from exc


def _kv(line: str) -> dict[str, str]:
    return {key: value for key, value in _KV_RE.findall(line)}


def _unquote(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise AssemblyError(f"字符串引号格式错误：{value}") from exc
    return value


def _strip_comment(line: str) -> str:
    quoted = False
    escaped = False
    for index, char in enumerate(line):
        if char == '"' and not escaped:
            quoted = not quoted
        if char == ';' and not quoted:
            return line[:index].rstrip()
        escaped = (char == '\\' and not escaped)
        if char != '\\':
            escaped = False
    return line.rstrip()


def _parse_bytes(line: str) -> bytes:
    body = line.split(None, 1)[1] if len(line.split(None, 1)) == 2 else ""
    values = [value.strip() for value in body.split(",") if value.strip()]
    result = bytearray()
    for value in values:
        number = _parse_int(value)
        if not 0 <= number <= 0xFF:
            raise AssemblyError(f"字节超出范围：{value}")
        result.append(number)
    return bytes(result)


def _parse_words(line: str) -> bytes:
    body = line.split(None, 1)[1] if len(line.split(None, 1)) == 2 else ""
    result = bytearray()
    for value in (part.strip() for part in body.split(",") if part.strip()):
        number = _parse_int(value)
        if not 0 <= number <= 0xFFFF:
            raise AssemblyError(f"uint16 超出范围：{value}")
        result += struct.pack("<H", number)
    return bytes(result)


def _parse_text(text: str, encoding: str) -> bytes:
    """将 .string 文本编码并补上第三段记录的 NUL。

    反汇编文本使用 ``\\n`` 表示引擎实际存储的 ``5C 6E 6E`` 换行标记，
    这里在编码前还原该三字节序列。
    """
    try:
        payload = b"\\nn".join(part.encode(encoding) for part in text.split("\\n"))
    except UnicodeEncodeError as exc:
        raise AssemblyError(f"字符串无法用 {encoding} 编码：{text!r}") from exc
    return payload + b"\0"


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
            values.append("".join(current).strip())
            current = []
            continue
        current.append(char)
        escaped = (char == "\\" and not escaped)
        if char != "\\":
            escaped = False
    if current:
        values.append("".join(current).strip())
    return values


def _split_inline_tokens(text: str) -> list[str]:
    """按空白拆分内联参数，同时保留完整的双引号字符串。"""
    return re.findall(r'"(?:\\.|[^"\\])*"|\S+', text)


def _instruction_bytes(mnemonic: str, operand: str | None, labels: dict[str, int], offset: int,
                       string_indices: dict[str, int] | None = None,
                       call_indices: dict[str, int] | None = None) -> bytes:
    # 语义折叠形式：PUSH Text <序号> "文本"、PUSH TextRef <序号> "文本"、
    # PUSH Ruby ...、PUSH CALL ...。TextRef 只压入索引，不附带 Text 指令。
    if mnemonic == "PUSH" and operand:
        folded = operand.split(None, 1)
        if folded and folded[0] in ("Text", "TextRef", "Ruby", "CALL"):
            sub = folded[0]; rest = folded[1] if len(folded) > 1 else ""
            if sub == "TextRef":
                values = _split_inline_tokens(rest)
                if len(values) != 2:
                    raise AssemblyError("PUSH TextRef 需要序号和文本")
                index = _parse_int(values[0])
                return struct.pack("<Hi", 0x01, index)
            if sub == "Text":
                values = _split_inline_tokens(rest)
                if len(values) != 2:
                    raise AssemblyError("PUSH Text 需要序号和文本")
                index = _parse_int(values[0]); text = _unquote(" ".join(values[1:]))
                return struct.pack("<HiH", 0x01, index, 0x40)
            if sub == "Ruby":
                values = _split_inline_tokens(rest)
                if len(values) != 4:
                    raise AssemblyError("PUSH Ruby 需要两个序号和两个文本")
                first_index = _parse_int(values[0]); second_index = _parse_int(values[2])
                return struct.pack("<HiHiH", 0x01, first_index, 0x01, second_index, 0x48)
            values = _split_inline_tokens(rest)
            if len(values) != 2:
                raise AssemblyError("PUSH CALL 需要第二段偏移和目标")
            return struct.pack("<HiH", 0x01, _parse_int(values[0]), 0x06)
    if mnemonic not in NAME_TO_OPCODE:
        raise AssemblyError(f"未找到官方 opcode 名称：{mnemonic}")
    opcode = NAME_TO_OPCODE[mnemonic]
    definition = OPCODES[opcode]
    if not definition["valid"]:
        raise AssemblyError(f"{mnemonic} 是空 dispatch 槽，请使用 .opcode 保留原字节")
    if opcode == 0x01:
        if operand is None:
            raise AssemblyError("PUSH 缺少 imm32 操作数")
        value = _parse_int(operand)
        if not -(1 << 31) <= value < (1 << 31):
            raise AssemblyError("PUSH 的 imm32 超出有符号范围")
        return struct.pack("<Hi", opcode, value)
    if opcode == 0x40 and operand is not None:
        if string_indices is None:
            raise AssemblyError("Text 内联字符串缺少第三段字符串表")
        text = _unquote(operand)
        if text not in string_indices:
            raise AssemblyError(f"Text 内联字符串未出现在字符串表：{text!r}")
        return struct.pack("<HiH", 0x01, string_indices[text], 0x40)
    if opcode == 0x48 and operand is not None:
        if string_indices is None:
            raise AssemblyError("Ruby 内联字符串缺少第三段字符串表")
        values = _split_inline_operands(operand)
        if len(values) != 2:
            raise AssemblyError("Ruby 内联语法需要两个字符串参数")
        try:
            first, second = (_unquote(value) for value in values)
        except AssemblyError:
            raise
        if first not in string_indices or second not in string_indices:
            raise AssemblyError("Ruby 内联字符串未出现在第三段字符串表")
        return struct.pack("<HiH i H", 0x01, string_indices[first], 0x01, string_indices[second], 0x48)
    if opcode == 0x06 and operand is not None:
        if call_indices is None:
            raise AssemblyError("CALL 内联目标缺少第二段回调表")
        call_tokens = _split_inline_tokens(operand)
        target = call_tokens[0] if call_tokens else operand
        if target.startswith("@second+"):
            value = _parse_int(target[len("@second+"):])
        elif target.startswith("call_"):
            value = call_indices.get(target)
            if value is None:
                raise AssemblyError(f"CALL 标签未出现在第二段：{target}")
        elif target.startswith("@main+") or target.startswith("@main::"):
            # 兼容旧的 @main+0x...，新的 @main::函数名 通过第二段标签表解析。
            value = call_indices.get(target)
            if value is None:
                raise AssemblyError(f"CALL 目标未出现在第二段：{target}")
        else:
            target = _unquote(target)
            value = call_indices.get(target)
        if value is None:
            raise AssemblyError(f"CALL 目标未出现在第二段：{target}")
        return struct.pack("<HiH", 0x01, value, 0x06)
    if opcode == 0x30 and operand is not None:
        if call_indices is None:
            raise AssemblyError("SCNCHG 内联目标缺少第二段记录表")
        scene_tokens = _split_inline_tokens(operand)
        target = scene_tokens[0] if scene_tokens else operand
        if target.startswith("@second+"):
            value = _parse_int(target[len("@second+"):])
        elif target.startswith("call_"):
            value = call_indices.get(target)
            if value is None:
                raise AssemblyError(f"SCNCHG 标签未出现在第二段：{target}")
        else:
            target = _unquote(target)
            value = call_indices.get(target)
        if value is None:
            raise AssemblyError(f"SCNCHG 目标未出现在第二段：{target}")
        return struct.pack("<HiH", 0x01, value, 0x30)
    if opcode in (0x03, 0x04, 0x05):
        if operand is None:
            raise AssemblyError(f"{mnemonic} 缺少标签操作数")
        if operand in labels:
            relative = labels[operand] - offset
        else:
            relative = _parse_int(operand)
        if not -(1 << 31) <= relative < (1 << 31):
            raise AssemblyError(f"{mnemonic} 相对偏移超出 int32 范围")
        return struct.pack("<Hi", opcode, relative)
    if operand is not None:
        raise AssemblyError(f"{mnemonic} 不接受内联操作数；参数应先通过值栈传入")
    return struct.pack("<H", opcode)


def _assemble_code(items: list[tuple], base_offset: int,
                   string_indices: dict[str, int] | None = None,
                   call_indices: dict[str, int] | None = None) -> bytes:
    labels: dict[str, int] = {}
    offset = base_offset
    for item in items:
        kind = item[0]
        if kind == "label":
            name = item[1]
            if name in labels:
                raise AssemblyError(f"标签重复定义：{name}")
            labels[name] = offset
        elif kind == "data":
            offset += len(item[1])
        elif kind == "instruction":
            mnemonic = item[1]
            opcode = NAME_TO_OPCODE.get(mnemonic)
            if opcode is None:
                raise AssemblyError(f"未找到官方 opcode 名称：{mnemonic}")
            if mnemonic == "PUSH" and item[2] and item[2].split(None, 1)[0] in ("Text", "TextRef", "Ruby", "CALL"):
                folded_name = item[2].split(None, 1)[0]
                offset += {"Text": 8, "TextRef": 6, "Ruby": 14, "CALL": 8}[folded_name]
            elif opcode == 0x40 and item[2] is not None:
                offset += 8
            elif opcode == 0x48 and item[2] is not None:
                offset += 14
            elif opcode == 0x06 and item[2] is not None:
                offset += 8
            elif opcode == 0x30 and item[2] is not None:
                offset += 8
            else:
                offset += instruction_length(opcode)
        else:
            raise AssemblyError(f"未知代码项：{kind}")
    result = bytearray()
    offset = base_offset
    for item in items:
        kind = item[0]
        if kind == "label":
            continue
        if kind == "data":
            result += item[1]
            offset += len(item[1])
        else:
            encoded = _instruction_bytes(item[1], item[2], labels, offset, string_indices, call_indices)
            result += encoded
            offset += len(encoded)
    return bytes(result)


def _new_context(kind: str, **kwargs):
    context = {"kind": kind, "bytes": bytearray()}
    context.update(kwargs)
    return context


def _set_inline_string(model: dict, index: int, value: str | bytes, line_number: int) -> None:
    """登记内联字符串；同一序号出现不同文本时立即报错，避免静默覆盖。"""
    existing = model["inline_strings"].get(index)
    if index in model["inline_strings"] and existing != value:
        raise AssemblyError(
            f"第 {line_number} 行字符串序号 {index} 与此前定义不一致；请同步修改该序号的所有引用"
        )
    model["inline_strings"][index] = value


def parse_asm(text: str) -> dict:
    model = {"header_magic": "IN10", "header_words": {}, "gaps": [], "segments": {},
             "encoding": "cp932", "inline_strings": {}}
    mode: dict | None = None
    current_record: dict | None = None
    current_segment: dict | None = None
    lines = text.splitlines()
    for line_number, raw_line in enumerate(lines, 1):
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith(";"):
            continue
        parts = line.split(None, 1)
        directive = parts[0]
        if directive == ".kgo_asm":
            if len(parts) != 2 or _parse_int(parts[1]) != 1:
                raise AssemblyError(f"第 {line_number} 行 .kgo_asm 版本不受支持")
        elif directive == ".encoding":
            model["encoding"] = _unquote(parts[1].strip())
        elif directive == ".header":
            attrs = _kv(parts[1])
            if "file_length" in attrs:
                model["header_words"][4] = _parse_int(attrs["file_length"])
            if "script_id" in attrs:
                model["header_words"][8] = _parse_int(attrs["script_id"])
            if "checksum" in attrs:
                model["header_words"][0x0C] = _parse_int(attrs["checksum"])
        elif directive == ".header_segments":
            attrs = _kv(parts[1])
            names = {
                "seg0_offset": 0x10, "seg0_size": 0x14, "seg0_count": 0x18,
                "main_offset": 0x1C, "main_size": 0x20, "main_count": 0x24,
                "second_offset": 0x28, "second_size": 0x2C, "second_count": 0x30,
                "third_offset": 0x34, "third_size": 0x38, "third_count": 0x3C,
            }
            for name, offset in names.items():
                if name in attrs:
                    model["header_words"][offset] = _parse_int(attrs[name])
        elif directive == ".header_segment":
            attrs = _kv(parts[1])
            name = attrs.get("name", "")
            fields = {
                "seg0": (0x10, 0x14, 0x18), "main": (0x1C, 0x20, 0x24),
                "second": (0x28, 0x2C, 0x30), "third": (0x34, 0x38, 0x3C),
            }
            if name not in fields:
                raise AssemblyError(f"第 {line_number} 行未知段名称：{name}")
            for key, offset in zip(("offset", "size", "count"), fields[name]):
                if key in attrs:
                    model["header_words"][offset] = _parse_int(attrs[key])
        elif directive == ".header_magic":
            model["header_magic"] = _unquote(parts[1].strip())
        elif directive == ".header_u32":
            values = parts[1].split()
            if len(values) != 2:
                raise AssemblyError(f"第 {line_number} 行 .header_u32 格式错误")
            offset, value = _parse_int(values[0]), _parse_int(values[1])
            if offset < 4 or offset > 60 or offset % 4:
                raise AssemblyError(f"第 {line_number} 行头部偏移错误")
            if not 0 <= value <= 0xFFFFFFFF:
                raise AssemblyError(f"第 {line_number} 行头部值越界")
            model["header_words"][offset] = value
        elif directive == ".gap":
            mode = _new_context("gap", **_kv(parts[1]))
            model["gaps"].append(mode)
        elif directive == ".padding":
            attrs = _kv(parts[1])
            size = _parse_int(attrs.get("size", "0")); fill = _parse_int(attrs.get("fill", "0"))
            if not 0 <= fill <= 0xFF:
                raise AssemblyError(f"第 {line_number} 行 padding 填充值越界")
            model["gaps"].append({"kind": "gap", "offset": attrs.get("offset", "0"),
                                  "size": attrs.get("size", "0"), "bytes": bytearray([fill] * size)})
        elif directive == ".end_gap":
            if mode is None or mode["kind"] != "gap":
                raise AssemblyError(f"第 {line_number} 行 .end_gap 没有对应 .gap")
            mode = None
        elif directive == ".segment_raw":
            attrs = _kv(parts[1]); name = attrs.get("name")
            if not name:
                raise AssemblyError(f"第 {line_number} 行原始段缺少名称")
            mode = _new_context("segment_raw", **attrs)
            model["segments"][name] = mode
            current_segment = mode
        elif directive == ".segment_calls":
            attrs = _kv(parts[1]); name = attrs.get("name")
            if not name:
                raise AssemblyError(f"第 {line_number} 行调用段缺少名称")
            mode = _new_context("segment_calls", **attrs, records=[])
            model["segments"][name] = mode; current_segment = mode
        elif directive == ".end_segment_calls":
            if mode is None or mode["kind"] != "segment_calls":
                raise AssemblyError(f"第 {line_number} 行调用段结束错误")
            mode = None; current_segment = None
        elif directive == ".segment_meta":
            attrs = _kv(parts[1]); name = attrs.get("name")
            if not name:
                raise AssemblyError(f"第 {line_number} 行元数据段缺少名称")
            model["segments"][name] = {"kind": "segment_meta", **attrs}
        elif directive == ".segment_meta_fields":
            segment = model["segments"].get("seg0")
            if segment is None or segment.get("kind") != "segment_meta":
                raise AssemblyError(f"第 {line_number} 行段 0 字段没有对应元数据段")
            segment.update(_kv(parts[1]))
        elif directive == ".segment_meta_name":
            segment = model["segments"].get("seg0")
            if segment is None or segment.get("kind") != "segment_meta":
                raise AssemblyError(f"第 {line_number} 行段 0 名称没有对应元数据段")
            attrs = _kv(parts[1])
            if "text" not in attrs:
                raise AssemblyError(f"第 {line_number} 行段 0 名称缺少 text")
            segment["name_text"] = attrs["text"]
        elif directive == ".end_segment_raw":
            if mode is None or mode["kind"] != "segment_raw":
                raise AssemblyError(f"第 {line_number} 行 .end_segment_raw 没有对应段")
            mode = None; current_segment = None
        elif directive == ".segment_strings":
            attrs = _kv(parts[1]); mode = _new_context("segment_strings", **attrs, records=[])
            model["segments"][attrs["name"]] = mode; current_segment = mode
        elif directive == ".end_segment_strings":
            if mode is None or mode["kind"] != "segment_strings":
                raise AssemblyError(f"第 {line_number} 行字符串段结束错误")
            mode = None; current_segment = None
        elif directive == ".segment_main":
            attrs = _kv(parts[1]); mode = _new_context("segment_main", **attrs, records=[])
            model["segments"][attrs["name"]] = mode; current_segment = mode
        elif directive == ".end_segment_main":
            if mode is None or mode["kind"] != "segment_main":
                raise AssemblyError(f"第 {line_number} 行主段结束错误")
            mode = None; current_segment = None
        elif directive == ".record":
            if current_segment is None or current_segment["kind"] != "segment_main":
                raise AssemblyError(f"第 {line_number} 行记录不在主段中")
            attrs = _kv(parts[1])
            current_record = _new_context("record", **attrs, prefix=bytearray(), tail=bytearray(), code=[])
            current_segment["records"].append(current_record); mode = current_record
        elif directive == ".record_layout":
            if current_record is None:
                raise AssemblyError(f"第 {line_number} 行记录布局不在记录中")
            current_record.update(_kv(parts[1]))
        elif directive == ".record_data":
            if current_record is None:
                raise AssemblyError(f"第 {line_number} 行记录数据不在记录中")
            attrs = _kv(parts[1]); values = attrs.get("values", "")
            blob = bytearray()
            for value in values.split(","):
                if value.strip():
                    number = _parse_int(value.strip())
                    if not 0 <= number <= 0xFFFF:
                        raise AssemblyError(f"第 {line_number} 行记录字段超出 uint16 范围")
                    blob.extend(struct.pack("<H", number))
            # 允许反汇编器将长数据记录拆成多行；多行按原顺序拼接。
            current_record.setdefault("data_only", bytearray()).extend(blob)
        elif directive == ".call":
            if mode is None or mode["kind"] != "segment_calls":
                raise AssemblyError(f"第 {line_number} 行 .call 不在调用段中")
            record = {"kind": "call", **_kv(parts[1])}
            if "pending_label" in mode:
                record.setdefault("label", mode.pop("pending_label"))
            mode["records"].append(record)
        elif directive in (".end_record",):
            if current_record is None:
                raise AssemblyError(f"第 {line_number} 行没有活动记录")
            current_record = None; mode = current_segment
        elif directive in (".prefix", ".tail"):
            if current_record is None:
                raise AssemblyError(f"第 {line_number} 行不在记录中")
            mode = _new_context(directive[1:], parent=current_record)
        elif directive in (".end_prefix", ".end_tail"):
            expected = "prefix" if directive == ".end_prefix" else "tail"
            if mode is None or mode["kind"] != expected:
                raise AssemblyError(f"第 {line_number} 行 {directive} 没有对应区块")
            if mode["kind"] == "prefix":
                current_record["prefix"].extend(mode["bytes"])
            else:
                current_record["tail"].extend(mode["bytes"])
            mode = current_record
        elif directive == ".tail_words":
            if mode is None or mode["kind"] != "tail":
                raise AssemblyError(f"第 {line_number} 行 .tail_words 不在尾部区块中")
            mode["parent"]["tail"].extend(_parse_words(line))
        elif directive == ".tail_meta":
            if mode is None or mode["kind"] != "tail":
                raise AssemblyError(f"第 {line_number} 行 .tail_meta 不在尾部区块中")
            attrs = _kv(parts[1])
            for value in attrs.get("values", "").split(","):
                if value.strip():
                    number = _parse_int(value.strip())
                    if not 0 <= number <= 0xFFFF:
                        raise AssemblyError(f"第 {line_number} 行尾部字段超出 uint16 范围")
                    mode["parent"]["tail"].extend(struct.pack("<H", number))
        elif directive == ".tail_align":
            if current_record is None or mode is not current_record:
                raise AssemblyError(f"第 {line_number} 行 .tail_align 不在记录尾部位置")
            attrs = _kv(parts[1])
            alignment = _parse_int(attrs.get("alignment", "0x10"))
            size = _parse_int(attrs.get("size", "0"))
            if alignment < 1 or alignment & (alignment - 1):
                raise AssemblyError(f"第 {line_number} 行尾部对齐值必须是正的 2 次幂")
            if size < 0:
                raise AssemblyError(f"第 {line_number} 行尾部对齐尺寸不能为负数")
            current_record["tail_align"] = alignment
            current_record["tail_align_size"] = size
        elif directive == ".opcode":
            if mode is None or mode["kind"] != "code":
                raise AssemblyError(f"第 {line_number} 行 .opcode 不在代码区中")
            value = _parse_int(parts[1].strip())
            if not 0 <= value <= 0xFFFF:
                raise AssemblyError(f"第 {line_number} 行 opcode 超出 uint16 范围")
            mode["parent"]["code"].append(("data", struct.pack("<H", value)))
        elif directive == ".code":
            if current_record is None:
                raise AssemblyError(f"第 {line_number} 行代码区不在记录中")
            mode = _new_context("code", parent=current_record)
        elif directive == ".end_code":
            if mode is None or mode["kind"] != "code":
                raise AssemblyError(f"第 {line_number} 行 .end_code 没有对应代码区")
            mode = current_record
        elif directive in (".string", ".orphan_string"):
            attrs = _kv(parts[1])
            if mode is not None and mode["kind"] == "segment_strings":
                mode["records"].append({"kind": "string", **attrs})
            elif mode is not None and mode["kind"] == "code":
                if "index" not in attrs or "text" not in attrs:
                    raise AssemblyError(f"第 {line_number} 行内联 .string 缺少 index/text")
                _set_inline_string(model, _parse_int(attrs["index"]), _unquote(attrs["text"]), line_number)
            else:
                raise AssemblyError(f"第 {line_number} 行 .string 不在字符串段中")
        elif directive in (".string_raw", ".orphan_string_raw"):
            attrs = _kv(parts[1])
            if mode is not None and mode["kind"] == "segment_strings":
                raw = _new_context("string_raw", **attrs); mode["records"].append(raw); mode = raw
            elif mode is not None and mode["kind"] == "code":
                raw = _new_context("inline_string_raw", **attrs, parent=mode); mode = raw
            else:
                raise AssemblyError(f"第 {line_number} 行 .string_raw 不在字符串段中")
        elif directive == ".end_string_raw":
            if mode is None or mode["kind"] not in ("string_raw", "inline_string_raw"):
                raise AssemblyError(f"第 {line_number} 行 .end_string_raw 没有对应区块")
            if mode["kind"] == "inline_string_raw":
                _set_inline_string(model, _parse_int(mode["index"]), bytes(mode["bytes"]), line_number)
                mode = mode["parent"]
            else:
                mode = current_segment
        elif directive in (".byte", ".word"):
            payload = _parse_bytes(line) if directive == ".byte" else _parse_words(line)
            if mode is None:
                raise AssemblyError(f"第 {line_number} 行数据定义没有目标区块")
            if mode["kind"] in ("gap", "segment_raw", "prefix", "tail", "string_raw", "inline_string_raw"):
                mode["bytes"].extend(payload)
            elif mode["kind"] == "code":
                mode["parent"]["code"].append(("data", payload))
            else:
                raise AssemblyError(f"第 {line_number} 行不能在 {mode['kind']} 中写数据")
        elif mode is not None and mode["kind"] == "code":
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):$", line)
            if match:
                mode["parent"]["code"].append(("label", match.group(1)))
            else:
                tokens = line.split(None, 1)
                mnemonic = tokens[0]
                operand = tokens[1].strip() if len(tokens) == 2 else None
                if mnemonic == "PUSH" and operand:
                    folded = operand.split(None, 1)
                    if folded and folded[0] in ("Text", "TextRef") and len(folded) > 1:
                        values = _split_inline_tokens(folded[1])
                        if len(values) >= 2:
                            index = _parse_int(values[0])
                            if index > 0:
                                _set_inline_string(model, index, _unquote(" ".join(values[1:])), line_number)
                    elif folded and folded[0] == "Ruby" and len(folded) > 1:
                        values = _split_inline_tokens(folded[1])
                        if len(values) == 4:
                            _set_inline_string(model, _parse_int(values[0]), _unquote(values[1]), line_number)
                            _set_inline_string(model, _parse_int(values[2]), _unquote(values[3]), line_number)
                mode["parent"]["code"].append(("instruction", mnemonic, operand))
        elif mode is not None and mode["kind"] == "segment_calls":
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):$", line)
            if match:
                mode["pending_label"] = match.group(1)
            else:
                raise AssemblyError(f"第 {line_number} 行调用段无法识别：{line}")
        else:
            raise AssemblyError(f"第 {line_number} 行无法识别：{line}")
    if mode is not None:
        raise AssemblyError(f"文件末尾仍在区块 {mode['kind']} 中")
    return model


def _build_segment(model: dict, name: str, encoding: str) -> bytes:
    segment = model["segments"].get(name)
    if segment is None:
        return b""
    kind = segment["kind"]
    if kind in ("segment_raw", "gap"):
        return bytes(segment["bytes"])
    if kind == "segment_calls":
        result = bytearray()
        main_record_offsets = model.get("_main_record_offsets", {})
        for record in segment["records"]:
            kind_value = _parse_int(record.get("kind", "2"))
            target = _unquote(record.get("target", '""'))
            if kind_value == 1 or target.startswith("@main+") or target.startswith("@main::"):
                if target.startswith("@main::"):
                    main_name = target[len("@main::"):]
                    main_offset = model.get("_main_record_names", {}).get(main_name)
                    if main_offset is None:
                        raise AssemblyError(f"同脚本 CALL 目标标签不存在：{main_name}")
                    payload = struct.pack("<I", main_offset)
                elif target.startswith("@main+"):
                    main_offset = _parse_int(target[len("@main+"):])
                    # kind=1 保存的是主段记录偏移，而不是固定 PC。
                    # 前置记录长度变化后，按旧记录偏移映射到新偏移。
                    new_offset = main_record_offsets.get(main_offset, main_offset)
                    payload = struct.pack("<I", new_offset)
                else:
                    payload = struct.pack("<I", _parse_int(target))
            else:
                payload = target.encode(encoding) + b"\0"
            size = 4 + len(payload)
            result += struct.pack("<HH", size, kind_value) + payload
        return bytes(result)
    if kind == "segment_meta":
        size = _parse_int(segment.get("size", "0"))
        result = bytearray(size)
        fields = (("record_size", 0, "H"), ("script_id", 2, "I"),
                  ("type", 6, "H"), ("event", 8, "H"), ("entry_offset", 0x0A, "I"),
                  ("param_a", 0x0E, "H"), ("param_b", 0x10, "H"),
                  ("param_c", 0x12, "H"), ("param_d", 0x14, "H"))
        for key, offset, fmt in fields:
            if key in segment:
                value = _parse_int(segment[key]); limit = 0xFFFFFFFF if fmt == "I" else 0xFFFF
                if not 0 <= value <= limit:
                    raise AssemblyError(f"段0 字段 {key} 超出范围")
                if key == "entry_offset":
                    # 段 0 的入口在已确认样本中指向主段记录起点；主段
                    # 重排后按旧记录偏移映射到新位置。若无法匹配则保留
                    # 原值，避免误改尚未确认的字段语义。
                    value = model.get("_main_record_offsets", {}).get(value, value)
                struct.pack_into("<" + fmt, result, offset, value)
        if "entry_label" in segment:
            entry_label = _unquote(segment["entry_label"])
            entry_value = model.get("_main_record_names", {}).get(entry_label)
            if entry_value is None:
                raise AssemblyError(f"段0入口标签不存在：{entry_label}")
            struct.pack_into("<I", result, 0x0A, entry_value)
        name = _unquote(segment.get("name_text", '""'))
        name_bytes = name.encode(encoding)
        if size < 0x16 + len(name_bytes) + 1:
            raise AssemblyError("段0 名称超出声明尺寸")
        result[0x16:0x16 + len(name_bytes)] = name_bytes
        result[0x16 + len(name_bytes)] = 0
        return bytes(result)
    if kind == "segment_strings":
        result = bytearray()
        for record in segment["records"]:
            if record["kind"] == "string":
                text = _unquote(record["text"])
                payload = _parse_text(text, encoding)
                expected = 2 + len(payload)
                expected = (expected + 1) & ~1
                if "size" in record:
                    size = _parse_int(record["size"])
                    if size < expected:
                        raise AssemblyError(f"字符串 {record['index']} 编码后尺寸超过声明尺寸")
                else:
                    size = expected
                result += struct.pack("<H", size) + payload + b"\0" * (size - (2 + len(payload)))
            else:
                raw = bytes(record["bytes"])
                expected_size = _parse_int(record.get("size", str(len(raw))))
                if len(raw) != expected_size:
                    raise AssemblyError(f"原始字符串 {record['index']} 尺寸不符")
                result += raw
        return bytes(result)
    if kind == "segment_main":
        string_indices: dict[str, int] = {}
        string_segment = model["segments"].get("third")
        if string_segment and string_segment["kind"] == "segment_strings":
            for entry in string_segment["records"]:
                if entry.get("kind") == "string":
                    string_indices[_unquote(entry["text"])] = _parse_int(entry["index"])
        for index, value in model.get("inline_strings", {}).items():
            if isinstance(value, str):
                string_indices.setdefault(value, int(index))
        call_indices: dict[str, int] = {}
        second_segment = model["segments"].get("second")
        if second_segment and second_segment["kind"] == "segment_raw":
            raw_second = bytes(second_segment["bytes"]); cursor = 0
            while cursor + 4 <= len(raw_second):
                size = struct.unpack_from("<H", raw_second, cursor)[0]
                kind_value = struct.unpack_from("<H", raw_second, cursor + 2)[0]
                if size < 4 or cursor + size > len(raw_second):
                    break
                payload = raw_second[cursor + 4:cursor + size]
                if kind_value == 1 and len(payload) >= 4:
                    call_indices[f"@main+0x{struct.unpack_from('<I', payload)[0]:X}"] = cursor
                else:
                    nul = payload.find(b"\0"); raw_name = payload if nul < 0 else payload[:nul]
                    try:
                        call_indices[raw_name.decode(encoding)] = cursor
                    except UnicodeDecodeError:
                        pass
                cursor += size
        elif second_segment and second_segment["kind"] == "segment_calls":
            cursor = 0
            for entry in second_segment["records"]:
                entry_kind = _parse_int(entry.get("kind", "2")); target = _unquote(entry.get("target", '""'))
                call_indices[target] = cursor
                label = entry.get("label")
                if label:
                    call_indices[label] = cursor
                call_indices[f"call_{cursor:08X}"] = cursor
                if entry_kind == 1 or target.startswith("@main+") or target.startswith("@main::"):
                    cursor += 8
                else:
                    cursor += 4 + len(target.encode(encoding)) + 1
        result = bytearray()
        main_record_offsets: dict[int, int] = {}
        main_record_names: dict[str, int] = {}
        old_record_cursor = 0
        for record in segment["records"]:
            old_record_offset = _parse_int(record.get("offset", str(old_record_cursor)))
            main_record_offsets[old_record_offset] = len(result)
            if "name" in record:
                main_record_names[_unquote(record["name"])] = len(result)
            if "data_only" in record:
                result += record["data_only"]
                old_record_cursor = old_record_offset + _parse_int(
                    record.get("size", str(len(record["data_only"]))))
                continue
            prefix = bytearray(record["prefix"])
            tail = bytes(record["tail"])
            if not prefix:
                if "name" not in record:
                    raise AssemblyError("记录缺少 name，无法生成记录前缀")
                name = _unquote(record["name"])
                try:
                    name_bytes = name.encode(encoding)
                except UnicodeEncodeError as exc:
                    raise AssemblyError(f"记录名称无法用 {encoding} 编码：{name!r}") from exc
                name_offset = _parse_int(record.get("name_offset", str(len(name_bytes) + 1)))
                if name_offset < len(name_bytes) + 1:
                    raise AssemblyError("记录 name_offset 小于名称所需长度")
                prefix = bytearray(0x0A + name_offset)
                prefix[4:6] = struct.pack("<H", name_offset)
                prefix[0x0A:0x0A + len(name_bytes)] = name_bytes
                prefix[0x0A + len(name_bytes)] = 0
            code_offset = len(prefix)
            code = _assemble_code(record["code"], code_offset, string_indices, call_indices)
            code_end = code_offset + len(code)
            original_tail_len = len(tail)
            original_size = _parse_int(record.get("size", str(code_end + original_tail_len)))
            align_value = record.get("tail_align")
            if align_value is not None:
                alignment = _parse_int(align_value)
                original_tail_len = _parse_int(record.get("tail_align_size", "0"))
                if alignment < 1 or alignment & (alignment - 1):
                    raise AssemblyError("记录尾部对齐值必须是正的 2 次幂")
                expected_padding = (-code_end) % alignment
                if ("size" in record and original_tail_len != expected_padding
                        and code_end == original_size - original_tail_len):
                    # 原始文本与记录尺寸矛盾时优先报错，避免静默改变布局。
                    raise AssemblyError("记录尾部对齐尺寸与代码边界不一致")
                tail = b"\0" * expected_padding
            else:
                original_tail_len = len(tail)
            # +0x06 的真实用途尚未在运行时消费者中确认，文本中以
            # aux_hint 命名；兼容旧版 asm 中的 aux_offset 写法。
            original_aux_hint = _parse_int(record.get("aux_hint", record.get("aux_offset", str(code_end))))
            original_code_end = original_size - original_tail_len
            # +0x06 是编译器辅助边界提示，部分脚本会把它写在最后 PUSH 的
            # 操作数中；零修改时必须原样保留，代码长度变化时按代码增量平移。
            aux_hint = original_aux_hint + (code_end - original_code_end)
            if len(prefix) < 10:
                raise AssemblyError("主段记录前缀小于 0x0A 字节")
            prefix[0:4] = struct.pack("<I", len(prefix) + len(code) + len(tail))
            prefix[6:10] = struct.pack("<I", aux_hint)
            result += prefix + code + tail
            old_record_cursor = old_record_offset + _parse_int(
                record.get("size", str(len(prefix) + len(code) + len(tail))))
        # 供后续第二段 kind=1 CALL 重定位使用。
        model["_main_record_offsets"] = main_record_offsets
        model["_main_record_names"] = main_record_names
        return bytes(result)
    raise AssemblyError(f"未知段类型：{kind}")


def assemble_model(model: dict) -> bytes:
    encoding = model.get("encoding", "cp932")
    # 新版反汇编器把字符串写在代码区；汇编前按序号自动生成第三段记录。
    if "third" not in model["segments"] and model.get("inline_strings"):
        entries = []
        for index in sorted(model["inline_strings"]):
            value = model["inline_strings"][index]
            if isinstance(value, bytes):
                entries.append({"kind": "string_raw", "index": str(index), "size": str(len(value)), "bytes": value})
            else:
                entries.append({"kind": "string", "index": str(index), "text": json.dumps(value, ensure_ascii=False)})
        model["segments"]["third"] = {"kind": "segment_strings", "name": "third",
                                        "offset": str(model["header_words"].get(0x34, 0)),
                                        "size": str(model["header_words"].get(0x38, 0)),
                                        "count": str(model["header_words"].get(0x3C, len(entries))),
                                        "records": entries}
    header = bytearray(64)
    magic = model.get("header_magic", "IN10").encode("ascii", errors="strict")
    if len(magic) != 4:
        raise AssemblyError("头部魔数必须正好 4 字节")
    header[:4] = magic
    for offset, value in model["header_words"].items():
        header[offset:offset + 4] = struct.pack("<I", value)

    # 先构建主段，建立记录偏移映射，供段 0 entry_offset 和第二段
    # kind=1 CALL 在代码/记录长度变化后进行重定位。
    segment_names = ("main", "seg0", "second", "third")
    segments = []
    field_map = {"seg0": (0x10, 0x14), "main": (0x1C, 0x20), "second": (0x28, 0x2C), "third": (0x34, 0x38)}
    for name in segment_names:
        segment = model["segments"].get(name)
        if segment is None:
            continue
        data = _build_segment(model, name, encoding)
        old_offset = _parse_int(segment.get("offset", "0"))
        old_size = _parse_int(segment.get("size", "0"))
        count = _parse_int(segment.get("count", "0"))
        segments.append({"name": name, "data": data, "old_offset": old_offset, "old_size": old_size, "count": count})

    gaps = []
    for gap in model.get("gaps", []):
        gaps.append({"old_offset": _parse_int(gap.get("offset", "0")), "data": bytes(gap["bytes"])})
    all_same = all(len(item["data"]) == item["old_size"] for item in segments)
    same_offsets = all_same and all(item["old_offset"] >= 64 for item in segments)
    if same_offsets:
        file_length = model["header_words"].get(4, 0)
        if not file_length:
            file_length = max([64] + [item["old_offset"] + len(item["data"]) for item in segments] + [g["old_offset"] + len(g["data"]) for g in gaps])
        output = bytearray(file_length)
        output[:64] = header
        placements = [(item["old_offset"], item["data"], item["name"]) for item in segments]
        placements += [(gap["old_offset"], gap["data"], "gap") for gap in gaps]
        new_offsets = {item["name"]: item["old_offset"] for item in segments}
        for offset, blob, _ in placements:
            if offset + len(blob) > len(output):
                raise AssemblyError("原始布局中的数据超出文件长度")
            output[offset:offset + len(blob)] = blob
    else:
        # 只有长度改变时才重新布局；段之间沿用原始 gap 字节和顺序。
        blocks = [(item["old_offset"], "segment", item) for item in segments]
        blocks += [(gap["old_offset"], "gap", gap) for gap in gaps]
        blocks.sort(key=lambda value: value[0])
        body = bytearray(); cursor_old = 64; new_offsets = {}
        for old_offset, kind, block in blocks:
            if old_offset < cursor_old:
                continue
            # 未被显式记录的空洞按零填充；正常反汇编输出会有对应 .gap。
            if old_offset > cursor_old:
                body.extend(b"\0" * (old_offset - cursor_old))
            if kind == "segment":
                new_offsets[block["name"]] = 64 + len(body)
                body.extend(block["data"]); cursor_old = old_offset + block["old_size"]
            else:
                body.extend(block["data"]); cursor_old = old_offset + len(block["data"])
        output = bytearray(64) + body
        output[:64] = header
        file_length = len(output)

    # 将新的段偏移/尺寸/文件总长回写头部；记录数等其他字段保持原值。
    struct.pack_into("<I", output, 4, len(output))
    for item in segments:
        name = item["name"]
        offset_field, size_field = field_map[name]
        struct.pack_into("<I", output, offset_field, new_offsets.get(name, item["old_offset"]))
        struct.pack_into("<I", output, size_field, len(item["data"]))
    return bytes(output)


def parse_scene_asm(text: str) -> dict:
    """解析 disassembler.py 生成的 Scene.tbl 语义汇编。"""
    model = {"encoding": "cp932", "record_bytes": 0, "record_count": 0,
             "records": []}
    current: dict | None = None
    mode: dict | None = None
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        parts = line.split(None, 1)
        directive = parts[0]
        attrs = _kv(parts[1]) if len(parts) > 1 else {}
        if directive == ".encoding":
            if len(parts) != 2:
                raise AssemblyError(f"第 {line_number} 行 .encoding 缺少编码名")
            model["encoding"] = _unquote(parts[1].strip())
        elif directive == ".scene_tbl":
            if len(parts) != 2 or _parse_int(parts[1]) != 1:
                raise AssemblyError(f"第 {line_number} 行 .scene_tbl 版本不受支持")
        elif directive == ".scene_header":
            if "record_bytes" in attrs:
                model["record_bytes"] = _parse_int(attrs["record_bytes"])
            if "record_count" in attrs:
                model["record_count"] = _parse_int(attrs["record_count"])
        elif directive == ".scene_record":
            if current is not None:
                raise AssemblyError(f"第 {line_number} 行上一个场景记录尚未结束")
            current = {"attrs": attrs, "name": None, "name_raw": None,
                       "align": None, "tail": bytearray()}
            model["records"].append(current)
            mode = current
        elif directive == ".scene_fields":
            if current is None:
                raise AssemblyError(f"第 {line_number} 行场景字段不在记录中")
            current["fields"] = attrs
        elif directive == ".scene_params":
            if current is None:
                raise AssemblyError(f"第 {line_number} 行场景参数不在记录中")
            current["params"] = attrs
        elif directive == ".scene_name":
            if current is None or mode is not current:
                raise AssemblyError(f"第 {line_number} 行场景名称不在记录中")
            if "text" not in attrs:
                raise AssemblyError(f"第 {line_number} 行场景名称缺少 text")
            current["name"] = _unquote(attrs["text"])
            current["name_raw"] = None
        elif directive == ".scene_name_raw":
            if current is None or mode is not current:
                raise AssemblyError(f"第 {line_number} 行原始场景名称不在记录中")
            current["name_raw"] = _new_context("scene_name_raw", **attrs, parent=current)
            mode = current["name_raw"]
        elif directive == ".end_scene_name_raw":
            if mode is None or mode["kind"] != "scene_name_raw":
                raise AssemblyError(f"第 {line_number} 行原始场景名称结束错误")
            raw = bytes(mode["bytes"])
            declared = _parse_int(mode.get("size", str(len(raw))))
            if len(raw) != declared:
                raise AssemblyError(f"第 {line_number} 行原始场景名称尺寸不符")
            mode["parent"]["name_raw"] = raw
            mode = current
        elif directive == ".scene_align":
            if current is None or mode is not current:
                raise AssemblyError(f"第 {line_number} 行场景对齐不在记录尾部位置")
            current["align"] = attrs
        elif directive == ".scene_tail":
            if current is None or mode is not current:
                raise AssemblyError(f"第 {line_number} 行场景尾部不在记录中")
            current["tail_ctx"] = _new_context("scene_tail", **attrs, parent=current)
            mode = current["tail_ctx"]
        elif directive == ".end_scene_tail":
            if mode is None or mode["kind"] != "scene_tail":
                raise AssemblyError(f"第 {line_number} 行场景尾部结束错误")
            raw = bytes(mode["bytes"])
            declared = _parse_int(mode.get("size", str(len(raw))))
            if len(raw) != declared:
                raise AssemblyError(f"第 {line_number} 行场景尾部尺寸不符")
            mode["parent"]["tail"] = bytearray(raw)
            mode = current
        elif directive == ".end_scene_record":
            if current is None or mode is not current:
                raise AssemblyError(f"第 {line_number} 行场景记录结束错误")
            if current.get("name") is None and current.get("name_raw") is None:
                raise AssemblyError(f"第 {line_number} 行场景记录缺少名称")
            current = None
            mode = None
        elif directive == ".byte":
            payload = _parse_bytes(line)
            if mode is None or mode.get("kind") not in ("scene_name_raw", "scene_tail"):
                raise AssemblyError(f"第 {line_number} 行 .byte 不在原始场景数据中")
            mode["bytes"].extend(payload)
        else:
            raise AssemblyError(f"第 {line_number} 行无法识别 Scene.tbl 指令：{line}")
    if current is not None or mode is not None:
        raise AssemblyError("Scene.tbl 汇编文件末尾仍在记录中")
    if not model["records"]:
        raise AssemblyError("Scene.tbl 汇编文件没有场景记录")
    return model


def assemble_scene_model(model: dict) -> bytes:
    """根据场景记录重建 Scene.tbl；记录偏移、长度和头部自动计算。"""
    encoding = model.get("encoding", "cp932")
    records = bytearray()
    for index, item in enumerate(model["records"]):
        fields = item.get("fields", {})
        params = item.get("params", {})
        values = {
            "script_id": _parse_int(fields.get("script_id", "0")),
            "type": _parse_int(fields.get("type", "0")),
            "event": _parse_int(fields.get("event", "0")),
            "entry_offset": _parse_int(fields.get("entry_offset", "0")),
            "param_a": _parse_int(params.get("param_a", "0")),
            "param_b": _parse_int(params.get("param_b", "0")),
            "param_c": _parse_int(params.get("param_c", "0")),
            "param_d": _parse_int(params.get("param_d", "0")),
        }
        if not 0 <= values["script_id"] <= 0xFFFFFFFF:
            raise AssemblyError(f"Scene.tbl 记录 {index} script_id 超出范围")
        record = bytearray(0x16)
        struct.pack_into("<I", record, 2, values["script_id"])
        struct.pack_into("<HHI", record, 6, values["type"], values["event"], values["entry_offset"])
        struct.pack_into("<HHHH", record, 0x0E, values["param_a"], values["param_b"],
                         values["param_c"], values["param_d"])
        if item.get("name") is not None:
            try:
                name_bytes = str(item["name"]).encode(encoding)
            except UnicodeEncodeError as exc:
                raise AssemblyError(f"Scene.tbl 场景名称无法用 {encoding} 编码") from exc
        else:
            name_bytes = bytes(item["name_raw"])
        record.extend(name_bytes)
        record.append(0)
        align_attrs = item.get("align")
        alignment = _parse_int(align_attrs.get("alignment", "0x2")) if align_attrs else 2
        if alignment < 1 or alignment & (alignment - 1):
            raise AssemblyError(f"Scene.tbl 记录 {index} 对齐值必须是 2 的幂")
        # Scene.tbl 记录按偶数边界串联；即使原记录没有显式
        # .scene_align，名称长度变化后也自动补齐。
        record.extend(b"\0" * ((-len(record)) % alignment))
        record.extend(bytes(item.get("tail", b"")))
        if len(record) > 0xFFFF:
            raise AssemblyError(f"Scene.tbl 记录 {index} 超过 uint16 尺寸范围")
        struct.pack_into("<H", record, 0, len(record))
        records.extend(record)
    return struct.pack("<4sII", b"IN10", len(records), len(model["records"])) + records


def _kgo_record_names(data: bytes, encoding: str) -> tuple[int, dict[int, str], dict[str, int]]:
    """提取 .kgo 脚本 ID 以及主段记录的旧/新偏移映射。"""
    if len(data) < 64 or data[:4] != b"IN10":
        raise AssemblyError("无法从 Scene.tbl 同步目标：文件不是 IN10 .kgo")
    script_id = struct.unpack_from("<I", data, 8)[0]
    main_offset, main_size, count = struct.unpack_from("<III", data, 0x1C)
    if main_offset + main_size > len(data):
        raise AssemblyError(".kgo 主段范围越界，无法同步 Scene.tbl")
    main = data[main_offset:main_offset + main_size]
    cursor = 0
    old_by_offset: dict[int, str] = {}
    new_by_name: dict[str, int] = {}
    for index in range(count):
        if cursor + 10 > len(main):
            raise AssemblyError(f".kgo 主段记录 {index} 越界，无法同步 Scene.tbl")
        size = struct.unpack_from("<I", main, cursor)[0]
        name_offset = struct.unpack_from("<H", main, cursor + 4)[0]
        if size < 0x0A + name_offset or cursor + size > len(main):
            raise AssemblyError(f".kgo 主段记录 {index} 尺寸无效，无法同步 Scene.tbl")
        raw_name = main[cursor + 0x0A:cursor + 0x0A + name_offset]
        raw_name = raw_name.split(b"\0", 1)[0]
        try:
            name = raw_name.decode(encoding)
        except UnicodeDecodeError:
            name = raw_name.decode(encoding, errors="replace")
        old_by_offset[cursor] = name
        new_by_name.setdefault(name, cursor)
        cursor += size
    if cursor != len(main):
        raise AssemblyError(".kgo 主段记录尺寸总和不匹配，无法同步 Scene.tbl")
    return script_id, old_by_offset, new_by_name


def sync_scene_entries(scene_model: dict, kgo_maps: dict[int, dict]) -> int:
    """按原 .kgo 记录名称把 Scene.tbl 入口偏移重定位到新主段。"""
    updated = 0
    for index, item in enumerate(scene_model["records"]):
        fields = item.setdefault("fields", {})
        script_id = _parse_int(fields.get("script_id", "0"))
        mapping = kgo_maps.get(script_id)
        if mapping is None:
            continue
        old_offset = _parse_int(fields.get("entry_offset", "0"))
        target_name = mapping["old_by_offset"].get(old_offset)
        if target_name is None:
            continue
        new_offset = mapping["new_by_name"].get(target_name)
        if new_offset is None:
            # 仅对名称大小写变化做宽松匹配；真正重命名仍需人工同步。
            lowered = target_name.casefold()
            new_offset = next((offset for name, offset in mapping["new_by_name"].items()
                               if name.casefold() == lowered), None)
        if new_offset is None:
            # 入口偏移为 0 的场景通常对应脚本首条记录；场景名与函数名
            # 可能只差 Func_ 前缀，改名后优先按这两个语义名称寻找。
            scene_name = item.get("name")
            candidates = []
            if scene_name:
                candidates.extend((scene_name, f"Func_{scene_name}"))
            for candidate in candidates:
                new_offset = mapping["new_by_name"].get(candidate)
                if new_offset is not None:
                    break
        if new_offset is None:
            continue
        if new_offset != old_offset:
            fields["entry_offset"] = f"0x{new_offset:X}"
            updated += 1
    return updated


def _is_scene_asm(text: str) -> bool:
    return any(line.strip().startswith(".scene_tbl") for line in text.splitlines())


def _sync_scene_path(scene_path: Path, built_kgo: dict[Path, bytes] | None = None,
                     encoding: str | None = None) -> tuple[Path, int]:
    """用同目录 .kgo（及本次刚生成的字节）同步一个 Scene.tbl 汇编文件。"""
    scene_text = scene_path.read_text(encoding="utf-8")
    scene_model = parse_scene_asm(scene_text)
    if encoding:
        scene_model["encoding"] = encoding
    maps: dict[int, dict] = {}
    built_kgo = built_kgo or {}
    for kgo_path in sorted(scene_path.parent.glob("scr*.kgo")):
        old_data = kgo_path.read_bytes()
        sid, old_by_offset, _ = _kgo_record_names(old_data, scene_model["encoding"])
        new_data = next((data for asm_path, data in built_kgo.items()
                         if asm_path.parent == scene_path.parent
                         and asm_path.name.endswith(".asm.txt")
                         and asm_path.name[:-8] == kgo_path.stem), old_data)
        new_sid, _, new_by_name = _kgo_record_names(new_data, scene_model["encoding"])
        if sid != new_sid:
            raise AssemblyError(f"脚本 {kgo_path.name} 的 script_id 被修改，无法同步 Scene.tbl")
        maps[sid] = {"old_by_offset": old_by_offset, "new_by_name": new_by_name}
    changed = sync_scene_entries(scene_model, maps)
    result = assemble_scene_model(scene_model)
    target = scene_path.with_name(scene_path.name[:-8] + ".rebuild") \
        if scene_path.name.endswith(".asm.txt") else scene_path.with_suffix(".rebuild")
    target.write_bytes(result)
    return target, changed


def assemble_file(path: Path, output: Path | None = None) -> Path:
    text = path.read_text(encoding="utf-8")
    is_scene = _is_scene_asm(text)
    if is_scene:
        result = assemble_scene_model(parse_scene_asm(text))
    else:
        model = parse_asm(text)
        result = assemble_model(model)
    if output is not None:
        target = output
    elif path.name.endswith(".asm.txt"):
        target = path.with_name(path.name[:-8] + ".rebuild")
    else:
        target = path.with_suffix(".rebuild")
    target.write_bytes(result)
    if not is_scene and path.name.endswith(".asm.txt"):
        companion = path.parent / "Scene.tbl.asm.txt"
        if companion.exists():
            _sync_scene_path(companion, {path: result}, model.get("encoding", "cp932"))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="汇编 Kogado 语义汇编文件")
    parser.add_argument("inputs", nargs="+", help="asm.txt 文件或目录")
    parser.add_argument("-o", "--output", help="单文件输出路径")
    parser.add_argument("--encoding", help="覆盖 .encoding 指令")
    args = parser.parse_args(argv)
    paths: list[Path] = []
    for value in args.inputs:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.asm.txt")))
        else:
            paths.append(path)
    if not paths:
        parser.error("没有找到汇编输入文件")
    if args.output and len(paths) != 1:
        parser.error("批处理时不能只指定一个 --output")
    scene_paths: list[Path] = []
    built_kgo: dict[Path, bytes] = {}
    try:
        # 先汇编所有 .kgo，建立新的主段记录偏移；Scene.tbl 在最后统一同步。
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if _is_scene_asm(text):
                scene_paths.append(path)
                continue
            model = parse_asm(text)
            if args.encoding:
                model["encoding"] = args.encoding
            result = assemble_model(model)
            if args.output:
                target = Path(args.output)
            elif path.name.endswith(".asm.txt"):
                target = path.with_name(path.name[:-8] + ".rebuild")
            else:
                target = path.with_suffix(".rebuild")
            target.write_bytes(result)
            built_kgo[path] = result
            print(f"已生成：{target}")

        # 单独汇编 .kgo 时，若同目录存在已反汇编的 Scene.tbl，也自动生成同步后的表。
        if not scene_paths:
            for path in built_kgo:
                companion = path.parent / "Scene.tbl.asm.txt"
                if companion.exists() and companion not in scene_paths:
                    scene_paths.append(companion)
                    break

        if scene_paths:
            for scene_path in scene_paths:
                if args.output and len(paths) == 1 and paths[0] == scene_path:
                    # 显式输出路径仅适用于单独汇编 Scene.tbl；目录批处理
                    # 会固定生成同目录的 Scene.tbl.rebuild。
                    scene_text = scene_path.read_text(encoding="utf-8")
                    scene_model = parse_scene_asm(scene_text)
                    if args.encoding:
                        scene_model["encoding"] = args.encoding
                    target = Path(args.output)
                    target.write_bytes(assemble_scene_model(scene_model))
                    changed = 0
                elif scene_path.name.endswith(".asm.txt"):
                    target, changed = _sync_scene_path(scene_path, built_kgo, args.encoding)
                else:
                    target, changed = _sync_scene_path(scene_path, built_kgo, args.encoding)
                print(f"已生成：{target}（同步 {changed} 条入口）")
    except (OSError, AssemblyError, UnicodeError) as exc:
        print(f"汇编失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
