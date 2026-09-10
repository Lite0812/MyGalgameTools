"""两种 DAT 格式的共享定义与无损文本转义。

文件名沿用项目规范，但分析结果表明目标文件没有二进制 Opcode。
"""

from __future__ import annotations

import codecs
import unicodedata
from dataclasses import dataclass


class FormatError(ValueError):
    """输入二进制或汇编文本不符合已确认格式。"""


MBT0_MAGIC = b"MBT0"
SAM_MAGIC = b"SAM\x00"

# 这两个格式没有二进制指令集。保留空字典供工具显式检查。
OPCODES: dict[int, object] = {}


@dataclass(frozen=True)
class SamSection:
    """SAM 文件头中的固定记录区定义。"""

    name: str
    source_name: str
    count_offset: int
    data_offset_offset: int
    record_size: int


SAM_SECTIONS = {
    "A": SamSection("A", "SKILLS", 0x10, 0x14, 0x884C),
    "B": SamSection("B", "ITEMS", 0x18, 0x1C, 0x1E4),
    "C": SamSection("C", "PROFILES", 0x08, 0x0C, 0x510),
}

SAM_SECTION_CODES = {
    definition.source_name: code for code, definition in SAM_SECTIONS.items()
}

# A 区技能记录。等级索引由 EXE 直接参与 0x848 乘法，合法范围为 0..15。
SAM_A_LEVEL_COUNT = 16
SAM_A_LEVEL_STRIDE = 0x848
SAM_A_LEVEL_HEADER = 0x860
SAM_A_RECORD_NAME = (0x000, 0x398)
SAM_A_LEVEL_TEXTS = {
    "DESCRIPTION_1": (0x398, 0xCC),
    "DESCRIPTION_2": (0x464, 0xCC),
    "DESCRIPTION_3": (0x530, 0xCC),
    "FORMULA_1": (0x5FC, 0xCC),
    "FORMULA_2": (0x6C8, 0xCC),
    "FORMULA_3": (0x794, 0xCC),
}
SAM_A_LEVEL_FIELDS = {
    "LEVEL_PARAMETER": 0x03C,
    "WAIT_TURNS": 0x040,
    "SECONDARY_EFFECT_TYPE": 0x04C,
    "PRIMARY_EFFECT_TYPE": 0x050,
    "EFFECT_PARAMETER": 0x054,
}
SAM_A_EFFECT_OFFSET = 0x058
SAM_A_EFFECT_COUNT = 8
SAM_A_EFFECT_SIZE = 0x18
SAM_A_SPECIAL_OFFSET = 0x118
SAM_A_SPECIAL_NAMES = (
    "TSUMO",
    "RIICHI",
    "URA_DORA",
    "DORA_YAMA",
    "TENPAI",
)
SAM_A_BUFF_OFFSET = 0x198
SAM_A_DEBUFF_OFFSET = 0x1C0
SAM_A_STATUS_EFFECT_COUNT = 4
SAM_A_TAIL_U32_FIELDS = {
    "INITIAL_CONTEXT_VALUE": 0x8688,
    "UI_ICON_ID": 0x869C,
    "PROFILE_ID": 0x86A4,
    "UNLOCK_LEVEL": 0x87F8,
    "CONTEXT_DEFAULT": 0x8800,
    "CONTEXT_LEVEL_6": 0x8804,
    "BATTLE_PARAMETER_1": 0x8808,
    "BATTLE_PARAMETER_2": 0x880C,
    "BATTLE_PARAMETER_3": 0x8810,
    "BATTLE_PARAMETER_4": 0x8814,
}
SAM_A_TAIL_U8_FIELDS = {
    "ENABLED": 0x86A8,
    "PRIMARY_EFFECT_TYPE": 0x86AB,
    "SECONDARY_EFFECT_TYPE": 0x86AC,
    "EFFECT_PARAMETER": 0x86AD,
    "SKILL_CATEGORY": 0x86AF,
}

# sub_57A270 使用的文本公式占位符。前六项直接对应等级字段，
# 后续项对应三组普通效果和增益/减益表。
SAM_A_FORMULA_PLACEHOLDERS = (
    "＃ＷＴ＃",
    "＃ツモ＃",
    "＃リーチ＃",
    "＃裏ドラ＃",
    "＃ドラ山＃",
    "＃テンパイ＃",
    "＃麻効数１＃",
    "＃麻雀Ｔ１＃",
    "＃麻雀数１＃",
    "＃麻雀Ｐ１＃",
    "＃麻効数２＃",
    "＃麻雀Ｔ２＃",
    "＃麻雀数２＃",
    "＃麻雀Ｐ２＃",
    "＃麻効数３＃",
    "＃麻雀Ｔ３＃",
    "＃麻雀数３＃",
    "＃麻雀Ｐ３＃",
    "＃バフＴ＃",
    "＃バフ数１＃",
    "＃バフ数２＃",
    "＃バフ数３＃",
    "＃バフ数４＃",
    "＃デバＴ＃",
    "＃デバ数１＃",
    "＃デバ数２＃",
    "＃デバ数３＃",
)

# B 区礼装/装备记录。
SAM_B_TEXTS = {
    "NAME": (0x000, 0x09C),
    "DESCRIPTION": (0x09C, 0x0A0),
}
SAM_B_U32_FIELDS = {
    "RARITY": 0x13C,
    "TYPE": 0x140,
    "PRICE_LEVEL": 0x144,
    "CATEGORY": 0x148,
    "PURCHASE_COST": 0x14C,
    "SELL_PRICE": 0x150,
}
SAM_B_PASSIVE_OFFSET = 0x154
SAM_B_PASSIVE_COUNT = 18
SAM_B_SPECIAL_TYPE = 0x19C
SAM_B_SPECIAL_VALUE = 0x1A0
SAM_B_MATERIAL_OFFSET = 0x1D4
SAM_B_MATERIAL_COUNT = 4

# C 区角色/战斗配置记录。
SAM_C_NAME = (0x000, 0x410)
SAM_C_U32_FIELDS = {
    "RESOURCE_ID": 0x410,
    "CHARACTER_ID": 0x414,
}
SAM_C_BASE_STAT_OFFSET = 0x44C
SAM_C_GROWTH_STAT_OFFSET = 0x468
SAM_C_STAT_COUNT = 5
SAM_C_ATTACK_TYPE = 0x4C8
SAM_C_EFFECT_RATE_OFFSET = 0x4CC
SAM_C_EFFECT_RATE_FIRST = 18
SAM_C_EFFECT_RATE_COUNT = 16


# 值为“EXE 内部命令 ID、当前样本出现次数”。ID 不存储在 DAT 中。
TEXT_COMMANDS = {
    "XCG": (0x08, 125),
    "STM": (0x10, 7),
    "STL": (0x11, 12),
    "STR": (0x12, 12),
    "STM_OFF": (0x13, 1),
    "CG": (0x28, 111),
    "CG_CLR": (0x30, 111),
    "FADE_SET": (0x39, 123),
    "FADE_IN": (0x3A, 123),
    "FADE_OUT_END": (0x3D, 111),
    "BGM": (0x42, 111),
    "TYPE_H": (0x4E, 111),
    "MES_OFF": (0x51, 123),
    "SE": (0x53, 322),
    "MES_CLR": (0x6B, 123),
    "VS_STAND_CLR": (0x71, 12),
    "UE_CG_ON": (0x8C, 6),
    "MWCG": (0xDA, 38),
    "MWFL": (0xDC, 211),
    "MWAIT": (0xDF, 37),
}


FORMAT_DEFINITIONS = {
    "MBT0": {
        "magic": MBT0_MAGIC,
        "header_size": 0x10,
        "group_record_size": 12,
        "default_encoding": "cp932",
    },
    "SAM": {
        "magic": SAM_MAGIC,
        "header_size": 0x28,
        "sections": SAM_SECTIONS,
        "default_encoding": "cp932",
    },
}


def codec_name(encoding: str) -> str:
    """校验编码名称并返回 Python 的规范名称。"""

    try:
        return codecs.lookup(encoding).name
    except LookupError as exc:
        raise FormatError(f"不支持的文本编码：{encoding}") from exc


def _is_cp932(name: str) -> bool:
    return name in {"cp932", "shift_jis", "shift_jis_2004", "shift_jisx0213"}


def _decode_one(data: bytes, position: int, encoding: str) -> tuple[bytes, str | None]:
    """读取一个编码字符；失败时仍返回需要原样保留的字节单元。"""

    name = codec_name(encoding)
    first = data[position]

    if _is_cp932(name):
        width = 2 if 0x81 <= first <= 0x9F or 0xE0 <= first <= 0xFC else 1
    elif name == "utf-8":
        if first < 0x80:
            width = 1
        elif 0xC2 <= first <= 0xDF:
            width = 2
        elif 0xE0 <= first <= 0xEF:
            width = 3
        elif 0xF0 <= first <= 0xF4:
            width = 4
        else:
            width = 1
    else:
        # 其他编码采用最短可独立解码单元，最多尝试 8 字节。
        for width in range(1, min(8, len(data) - position) + 1):
            unit = data[position : position + width]
            try:
                text = unit.decode(encoding, errors="strict")
            except UnicodeDecodeError:
                continue
            if text:
                return unit, text
        return data[position : position + 1], None

    unit = data[position : min(position + width, len(data))]
    if len(unit) != width:
        return unit, None
    try:
        return unit, unit.decode(encoding, errors="strict")
    except UnicodeDecodeError:
        return unit, None


def _safe_text(text: str) -> bool:
    """判断解码字符能否安全放进双引号 TEXT。"""

    if not text or '"' in text or "\x00" in text:
        return False
    for character in text:
        category = unicodedata.category(character)
        if category in {"Cc", "Cs", "Co", "Cn"}:
            return False
    return True


def _placeholder(unit: bytes) -> str:
    return "{{" + ":".join(f"{value:02X}" for value in unit) + "}}"


def escape_bytes(data: bytes, encoding: str = "cp932") -> str:
    """把原始字符串字节转换为可逆、无乱码的语义文本。"""

    codec_name(encoding)
    result: list[str] = []
    position = 0
    while position < len(data):
        if data.startswith(b"\\nn", position):
            result.append("\\n")
            position += 3
            continue

        value = data[position]
        if value == 0x5C:
            result.append("\\\\")
            position += 1
            continue
        if data.startswith(b"{{", position):
            result.append("{{7B}}{{7B}}")
            position += 2
            continue
        if value in {0x0A, 0x0D} or value < 0x20 or value == 0x7F:
            result.append(_placeholder(bytes([value])))
            position += 1
            continue

        unit, text = _decode_one(data, position, encoding)
        stable = False
        if text is not None:
            try:
                stable = text.encode(encoding, errors="strict") == unit
            except UnicodeEncodeError:
                stable = False
        if text is None or not _safe_text(text) or not stable:
            result.append(_placeholder(unit))
        else:
            result.append(text)
        position += len(unit)
    return "".join(result)


def unescape_text(text: str, encoding: str = "cp932") -> bytes:
    """把 TEXT 内的语义文本恢复为原始字节。"""

    codec_name(encoding)
    result = bytearray()
    ordinary: list[str] = []

    def flush_ordinary() -> None:
        if not ordinary:
            return
        source = "".join(ordinary)
        try:
            result.extend(source.encode(encoding, errors="strict"))
        except UnicodeEncodeError as exc:
            raise FormatError(f"文本无法使用 {encoding} 编码：{source!r}") from exc
        ordinary.clear()

    position = 0
    while position < len(text):
        if text.startswith("{{", position):
            flush_ordinary()
            end = text.find("}}", position + 2)
            if end < 0:
                raise FormatError("字节占位符缺少结束符号 }}")
            body = text[position + 2 : end]
            parts = body.split(":")
            if not parts or any(len(part) != 2 for part in parts):
                raise FormatError(f"非法字节占位符：{{{{{body}}}}}")
            try:
                result.extend(int(part, 16) for part in parts)
            except ValueError as exc:
                raise FormatError(f"非法字节占位符：{{{{{body}}}}}") from exc
            position = end + 2
            continue

        if text[position] == "\\":
            flush_ordinary()
            if position + 1 >= len(text):
                raise FormatError("TEXT 末尾存在孤立反斜杠")
            marker = text[position + 1]
            if marker == "n":
                result.extend(b"\\nn")
            elif marker == "\\":
                result.append(0x5C)
            else:
                raise FormatError(f"不支持的反斜杠转义：\\{marker}")
            position += 2
            continue

        ordinary.append(text[position])
        position += 1

    flush_ordinary()
    return bytes(result)


def quoted_bytes(data: bytes, encoding: str = "cp932") -> str:
    """生成汇编文本使用的双引号字节字符串。"""

    return f'"{escape_bytes(data, encoding)}"'
