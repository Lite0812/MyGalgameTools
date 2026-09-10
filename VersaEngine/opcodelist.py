"""asm 文本格式与 CBOR 载荷之间的共享定义（反汇编器 / 汇编器共用）。

本模块提供四件东西：

  * ``OPCODES``      指令表（助记符、参数名），与 ``vetool/ve_opcodes.py`` 同源
  * 字符串编解码      ``quote`` / ``unquote``，语义化文本 <-> 原字符串的双向规范
  * 记号器与值解析    asm 里的字面量（整数、字符串、列表、映射）的词法与语法
  * 载荷类型识别      按文件名决定用哪种输出布局

零突变的依据（实测于本作 151 个 CBOR 载荷）：

  * 值树里只有 int / str / list / map / bytes 五种类型，最大嵌套 7 层；
  * ``ve_cbor.dumps(ve_cbor.loads(x)) == x`` 对全部载荷成立；
  * 因此"asm 文本 <-> 值树"互逆 <=> "asm 文本 -> 二进制"逐字节一致。

节点数组内部只有整数和嵌套列表，没有字符串；字符串只出现在头部标量字段
以及 strings / assets / project 这三类表里。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:                          # 拖放运行时 cwd 不一定在这
    sys.path.insert(0, _HERE)

import ve_cbor                                    # noqa: E402
from ve_opcodes import (                          # noqa: E402
    OPCODES, CONTROL_FLOW, COMPARE_OPS, VALUE_KINDS, arg_names, name_of,
)

__all__ = [
    "OPCODES", "CONTROL_FLOW", "COMPARE_OPS", "VALUE_KINDS",
    "arg_names", "name_of", "MNEMONICS", "TAIL_OPS", "ROLE_BY_NAME",
    "AsmError", "quote", "unquote", "tokenize", "parse_value",
    "Placeholder", "Token", "detect_payload", "DEFAULT_ENCODING",
    "HASH_ARGS", "COLOR_ARGS", "ENUM_ARGS", "CMP_NAMES", "CMP_VALUES",
    "KIND_NAMES", "KIND_VALUES", "SPRITE_KINDS", "SPRITE_KIND_VALUES",
    "sprite_decode", "sprite_encode", "fill_args", "strip_args",
    "ARG_DEFAULTS", "SCRIPT_DEFAULTS", "SPEAKER_COLS", "SPEAKER_WIDE",
    "SPEAKER_PER", "speakers_derive", "speakers_are_default",
]


class AsmError(Exception):
    """asm 文本不合法。"""


# asm.txt 默认编码。CLAUDE.md 的缺省是 cp932，但本作的 CBOR 字符串是 UTF-8，
# 且实测有 26532 条字符串无法用 cp932 表示（另有 3960 条无法用 gbk 表示），
# 所以这里默认 utf-8；仍可用 --encoding 覆盖，占位符机制保证任何编码都不丢字节。
DEFAULT_ENCODING = "utf-8"

# 助记符 -> opcode（汇编方向的反查表）
MNEMONICS: dict[str, int] = {name: op for op, (name, _) in OPCODES.items()}

# 带尾部嵌套结构的指令：Vm::decode 收整数参数时遇到 array/map 就停，
# 尾部那一项不计入 argc，由 handler 自己从 Command+0x30 取整个节点。
TAIL_OPS = frozenset({4, 7})

# 参数名 -> 语义角色，只用于生成 `;` 注释里的可读还原，不参与重建
ROLE_BY_NAME: dict[str, str] = {
    "asset": "asset", "voice": "asset", "font": "asset",
    "text": "text", "speaker": "text",
    "layer": "name", "channel": "name", "flag": "name", "screen": "name",
    "key": "name", "var": "name", "rule": "word",
}

# 这些参数是 32 位标识符（FNV-1a32 哈希），写成 #XXXXXXXX
HASH_ARGS = frozenset({
    "layer", "asset", "voice", "text", "speaker", "flag", "channel",
    "screen", "key", "var", "rule", "font", "group", "line_id",
})

# 颜色参数，写成 0xRRGGBB
COLOR_ARGS = frozenset({"color"})

# 枚举参数：写成助记词而不是数字。值不在表里时退回数字，仍可往返。
CMP_NAMES = {0: "eq", 1: "ne", 2: "lt", 3: "le", 4: "gt", 5: "ge"}
CMP_VALUES = {v: k for k, v in CMP_NAMES.items()}
KIND_NAMES = dict(VALUE_KINDS)
KIND_VALUES = {v: k for k, v in KIND_NAMES.items()}

# 参数名 -> 枚举表
ENUM_ARGS: dict[str, tuple[dict, dict]] = {
    "op": (CMP_NAMES, CMP_VALUES),
    "kind": (KIND_NAMES, KIND_VALUES),
}


# --------------------------------------------------------------------------
# op4（show）尾部精灵布局数组的槽位模型（依据见 vm_analysis.md §4.3）
#
# 总长只有 14 / 15 / 16 三种：槽 0..13 固定，之后跟可选的 anim、可选的 style。
# 14 个固定槽里有 8 个在全部 10172 条 show 里恒为同一值，槽 1 由槽 0 唯一
# 决定，都不含信息，具名输出时省略；剩下 kind / flags / parts / sheet / clip
# 和 style 里的 RGB 才是真正会变的东西。
#
# sprite_decode 与 sprite_encode 互为逆运算，反汇编器会当场用 encode 复核
# decode 的结果，对不上就退回原始 `.layout` 数组，因此永不丢字节。
# --------------------------------------------------------------------------

SPRITE_FIXED = 14                                  # 固定槽位数
SPRITE_KINDS = {0: "bg", 1: "frames", 3: "layered"}
SPRITE_KIND_VALUES = {v: k for k, v in SPRITE_KINDS.items()}

SPRITE_HOLD = {0: 12000, 1: 12000, 3: 0}           # 槽 1，由 kind 决定
SPRITE_PARTS = 3                                   # 槽 3：部件表
SPRITE_FLAGS, SPRITE_SHEET, SPRITE_CLIP = 2, 5, 11  # 具名可变槽

# 这些槽恒为固定值，任一处不符就放弃具名输出
SPRITE_CONST: dict[int, object] = {
    4: [], 6: 0, 7: 0, 8: 0, 9: 0, 10: 1000, 12: 1000, 13: 0,
}
# 具名槽的缺省值：等于缺省时不写这个字段
SPRITE_DEFAULT: dict[int, object] = {SPRITE_FLAGS: 17, SPRITE_SHEET: [],
                                     SPRITE_CLIP: []}
SPRITE_TINT = 0xFFFFFF                             # style 里的 RGB 缺省（白 = 不着色）


def _anim() -> list:
    """槽 14。存在时全语料恒为这一个值，每次新建以免节点间共享同一列表。"""
    return [0, [[1, 0]]]


def _parts_ok(parts) -> bool:
    """部件表：每项是一个资源哈希，或 ``[资源哈希, x, y]``。"""
    if not isinstance(parts, list):
        return False
    for item in parts:
        if isinstance(item, int):
            continue
        if not (isinstance(item, list) and len(item) == 3
                and all(isinstance(x, int) for x in item)):
            return False
    return True


def sprite_decode(layout, asset: int = -1) -> dict | None:
    """布局数组 -> 具名字段字典；不符合槽位模型时返回 None。

    ``asset`` 是 show 的资源参数。部件表首项恒等于它（5865/5865 条），
    是底图自身，故拆出来的 ``extra`` 不含首项，由 ``sprite_encode`` 补回。
    """
    if not isinstance(layout, list) or len(layout) < SPRITE_FIXED:
        return None
    tail = layout[SPRITE_FIXED:]
    if len(tail) > 2:
        return None
    has_anim = len(tail) == 2
    if has_anim and tail[0] != _anim():
        return None
    style = tail[-1] if tail else None
    tint = SPRITE_TINT
    if style is not None:
        if not (isinstance(style, list) and len(style) == 6
                and all(isinstance(x, int) for x in style)
                and style[0] == style[1] == style[2] == style[4] == style[5] == 0):
            return None
        tint = style[3]

    kind = layout[0]
    if kind not in SPRITE_KINDS or layout[1] != SPRITE_HOLD[kind]:
        return None
    for slot, want in SPRITE_CONST.items():
        if layout[slot] != want:
            return None
    if not _parts_ok(layout[SPRITE_PARTS]):
        return None
    for slot in (SPRITE_SHEET, SPRITE_CLIP):
        if not (isinstance(layout[slot], list)
                and all(isinstance(x, int) for x in layout[slot])):
            return None
    if not isinstance(layout[SPRITE_FLAGS], int):
        return None

    parts = layout[SPRITE_PARTS]
    # 首项是底图自身，等于 asset 参数就不必再写一遍
    base = bool(parts) and parts[0] == asset
    return {
        "kind": kind,
        "flags": layout[SPRITE_FLAGS],
        "extra": parts[1:] if base else parts,
        "base": base,
        "sheet": layout[SPRITE_SHEET],
        "clip": layout[SPRITE_CLIP],
        "tint": tint,
        "anim": has_anim,
        "style": style is not None,
    }


def sprite_encode(f: dict, asset: int = -1) -> list:
    """具名字段字典 -> 布局数组（``sprite_decode`` 的逆）。"""
    kind = f["kind"]
    if kind not in SPRITE_HOLD:
        raise AsmError(f"未知的精灵类型 {kind}")
    extra = list(f.get("extra") or [])
    parts = ([asset] + extra) if f.get("base") else extra
    layout: list = [0] * SPRITE_FIXED
    layout[0] = kind
    layout[1] = SPRITE_HOLD[kind]
    layout[SPRITE_FLAGS] = f.get("flags", SPRITE_DEFAULT[SPRITE_FLAGS])
    layout[SPRITE_PARTS] = parts
    layout[SPRITE_SHEET] = list(f.get("sheet") or [])
    layout[SPRITE_CLIP] = list(f.get("clip") or [])
    for slot, want in SPRITE_CONST.items():
        layout[slot] = list(want) if isinstance(want, list) else want
    if f.get("anim", True):
        layout.append(_anim())
    if f.get("style", True):
        layout.append([0, 0, 0, f.get("tint", SPRITE_TINT), 0, 0])
    return layout


# --------------------------------------------------------------------------
# 参数缺省值
#
# 指令参数写成 ``名字=值``，等于缺省值的就不写。实测很多参数在整部作品里恒为
# 一个值（show 的 alpha 恒 255，play_sound 的音量/淡入/音高恒 1000），全印出来
# 只是噪音。
#
# 缺省值同时用于两个方向：
#   * 反汇编：值等于缺省就省掉这个字段
#   * 汇编：没写的字段按表补回，参数个数按"末尾还有缺省值就继续补"确定
#
# 参数个数因此也能精确还原：``jmp`` 的第二参没有缺省值，写了就是 2 个参数，
# 没写就是 1 个。反汇编器每条指令都会用 fill_args 复核一遍，对不上就退回全量
# 输出，所以表写错了也只是啰嗦，不会丢字节。
# --------------------------------------------------------------------------

_MISS = object()                                   # "这个下标没有缺省值"

ARG_DEFAULTS: dict[int, dict[int, int]] = {
    4:  {2: 0, 3: 0, 4: 0, 5: 255, 6: 0, 7: 0},    # show: x y z alpha flip variant
    5:  {1: 0},                                    # hide: fade_ms
    6:  {0: 0, 2: 0},                              # say: speaker（旁白）voice（无配音）
    # play_sound: channel（全作只有这一个音频频道）flags 音量 淡入 音高 循环
    8:  {0: 0x9F9C4FD4, 1: 0, 3: 1000, 4: 1000, 5: 1000, 6: 0},
    15: {2: 0, 3: 0, 4: 4, 5: 0, 6: 0},            # transition: 保留槽与 mode
    27: {1: 0},                                    # jmp_if_not: op（默认 eq）
}


def fill_args(op: int, present: dict[int, object], line: int = 0) -> list:
    """``{下标: 值}`` -> 完整参数列表，空位按缺省值补。"""
    table = ARG_DEFAULTS.get(op, {})
    n = max(present) + 1 if present else 0
    while n in table:                              # 末尾还有缺省值就继续补
        n += 1
    out: list = []
    for i in range(n):
        if i in present:
            out.append(present[i])
        elif i in table:
            out.append(table[i])
        else:
            name = arg_names(op)[i] if i < len(arg_names(op)) else f"#{i}"
            raise AsmError(f"第 {line} 行：{OPCODES[op][0]} 缺少参数 {name}，"
                           f"它没有缺省值")
    return out


def strip_args(op: int, args: list) -> dict[int, object]:
    """完整参数列表 -> ``{下标: 值}``，去掉等于缺省值的项。"""
    table = ARG_DEFAULTS.get(op, {})
    present = {i: v for i, v in enumerate(args) if table.get(i, _MISS) != v}
    return present if fill_args(op, present) == list(args) else dict(enumerate(args))


# --------------------------------------------------------------------------
# 脚本载荷的可推导字段
#
# 140 个脚本载荷的顶层键集合完全相同（22 个键）。其中：
#
#   * 7 个数组恒为空（本作没用到常量池、玩家变量、变体）
#   * version / language / entry 三个标量各自只有一个取值
#   * 角色表的 8 个并行数组：140 个脚本存的是**同一张 84 行表**，且除
#     speaker_keys 外全部可推导 —— 组和语音组恒等于键，颜色恒 0xFFFFFFFF，
#     字体/屏/立绘全为 0。整张表折成一行 `.speakers <角色数>`。
#
# 这些不写进 asm，汇编时按 SCRIPT_DEFAULTS 补回。反汇编器逐字段比对，
# 只要有一处不等于缺省就照常写出来，所以别的作品拿来用也不会丢字节。
# --------------------------------------------------------------------------

SPEAKER_COLS = ("speaker_keys", "speaker_groups", "voice_group_keys",
                "speaker_colors", "speaker_fonts", "speaker_screens")
SPEAKER_WIDE = ("speaker_portraits", "speaker_voice_portraits")
SPEAKER_PER = 5                                    # 后两个数组每角色几项

# 角色表各列的推导规则：列名 -> 由 speaker_keys 第 i 项算出该列第 i 项
SPEAKER_RULES = {
    "speaker_groups": lambda key: key,
    "voice_group_keys": lambda key: key,
    "speaker_colors": lambda key: 0xFFFFFFFF,
    "speaker_fonts": lambda key: 0,
    "speaker_screens": lambda key: 0,
}

SCRIPT_DEFAULTS: dict[str, object] = {
    "version": 1,
    "language": "ja",
    "entry": 0,
    "const_keys": [], "const_kinds": [], "const_values": [],
    "var_keys": [], "var_kinds": [], "var_values": [],
    "variants": [],
}


def speakers_derive(keys: list) -> dict[str, list]:
    """角色键列表 -> 其余 7 个并行数组。"""
    out = {name: [rule(k) for k in keys]
           for name, rule in SPEAKER_RULES.items()}
    for name in SPEAKER_WIDE:
        out[name] = [0] * (len(keys) * SPEAKER_PER)
    return out


def speakers_are_default(d: dict) -> bool:
    """角色表的 7 个附属数组是否全部可由 speaker_keys 推出。"""
    keys = d.get("speaker_keys")
    if not isinstance(keys, list) or not keys:
        return False
    want = speakers_derive(keys)
    return all(d.get(name) == col for name, col in want.items())


def detect_payload(filename: str) -> str:
    """按文件名判断载荷类型，决定 asm 的输出布局。"""
    stem = os.path.basename(filename)
    for suffix in (".cbor", ".asm.txt"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    if stem.endswith(".script"):
        return "script"
    if stem.endswith(".strings"):
        return "strings"
    if stem.endswith(".font"):
        return "font"
    if stem in ("assets", "project", "ui"):
        return stem
    return "generic"


# --------------------------------------------------------------------------
# 字符串编解码
#
# 规则（CLAUDE.md 第 2.3 / 3.2 节）：
#   \\        真实反斜杠
#   \n        引擎换行标记。本引擎的换行就是裸 0x0A，故 \n <-> 0x0A
#   {{XX}}    一个原始字节
#   {{XX:XX}} 一个字符的多个原始字节（按 UTF-8 分组）
# 需要占位符的情况：控制字符、双引号、`{{` 序列的首个 `{`、
# 私用区字符、以及在目标编码下无法表示的字符。全角空格不转义。
# --------------------------------------------------------------------------

_PUA = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))


def _needs_placeholder(ch: str, encoding: str) -> bool:
    o = ord(ch)
    if o < 0x20 or o == 0x7F:
        return True                      # 控制字符（0x0A 已在调用处单独处理）
    if ch == '"':
        return True                      # 会提前结束字面量
    if 0xD800 <= o <= 0xDFFF:
        return True                      # surrogateescape 逃逸出的裸字节
    if any(lo <= o <= hi for lo, hi in _PUA):
        return True                      # 私用区，字体外显示为乱码
    try:
        ch.encode(encoding)
    except (UnicodeEncodeError, UnicodeError):
        return True                      # 目标编码写不出来
    return False


def _ph(ch: str) -> str:
    """把一个字符按 UTF-8 字节铺成占位符。"""
    o = ord(ch)
    if 0xDC80 <= o <= 0xDCFF:            # surrogateescape：原样还原单字节
        return "{{%02X}}" % (o & 0xFF)
    data = ch.encode("utf-8", errors="surrogateescape")
    return "{{" + ":".join("%02X" % b for b in data) + "}}"


def quote(text: str, encoding: str = DEFAULT_ENCODING) -> str:
    """字符串 -> asm 字面量（含两侧双引号）。"""
    out = ['"']
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "{" and i + 1 < n and text[i + 1] == "{":
            out.append("{{7B}}")         # 避免与占位符起始混淆
        elif _needs_placeholder(ch, encoding):
            out.append(_ph(ch))
        else:
            out.append(ch)
        i += 1
    out.append('"')
    return "".join(out)


def unquote(body: str) -> str:
    """asm 字面量内容（不含双引号）-> 原字符串。"""
    buf = bytearray()
    n = len(body)
    i = 0
    while i < n:
        ch = body[i]
        if ch == "\\":
            if i + 1 >= n:
                raise AsmError("字符串以孤立的反斜杠结尾")
            nxt = body[i + 1]
            if nxt == "\\":
                buf += b"\\"
            elif nxt == "n":
                buf += b"\n"
            else:
                raise AsmError(f"未定义的转义 \\{nxt}（只允许 \\\\ 和 \\n）")
            i += 2
            continue
        if ch == "{" and body.startswith("{{", i):
            end = body.find("}}", i + 2)
            if end < 0:
                raise AsmError("占位符缺少结束的 }}")
            field = body[i + 2 : end]
            if not field:
                raise AsmError("空占位符 {{}}")
            for part in field.split(":"):
                if len(part) != 2:
                    raise AsmError(f"占位符 {{{{{field}}}}} 的字节段必须是两位十六进制")
                try:
                    buf.append(int(part, 16))
                except ValueError:
                    raise AsmError(f"占位符 {{{{{field}}}}} 含非十六进制字符") from None
            i = end + 2
            continue
        buf += ch.encode("utf-8", errors="surrogateescape")
        i += 1
    return buf.decode("utf-8", errors="surrogateescape")


# --------------------------------------------------------------------------
# 记号器
#
# asm 的词法很小：整数、字符串、标识符、以及 ``[ ] { } , : =`` 六个符号。
# ``;`` 起注释到行尾。字符串必须整体扫过，因为里面可以出现任何符号。
# --------------------------------------------------------------------------

_PUNCT = set("[]{},:=*|")


class Token:
    """一个记号。``kind`` 取 int / str / id / punct 之一。"""

    __slots__ = ("kind", "value", "line")

    def __init__(self, kind: str, value, line: int):
        self.kind = kind
        self.value = value
        self.line = line

    def __repr__(self) -> str:                     # 仅用于报错
        return f"Token({self.kind}, {self.value!r}, line {self.line})"


def _scan_string(text: str, i: int, line: int) -> tuple[str, int]:
    """从 text[i] == '"' 开始扫一个字符串字面量，返回 (原字符串, 结束位置)。"""
    j = i + 1
    n = len(text)
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "{" and text.startswith("{{", j):
            end = text.find("}}", j + 2)
            if end < 0:
                raise AsmError(f"第 {line} 行：占位符缺少结束的 }}")
            j = end + 2
            continue
        if ch == '"':
            return unquote(text[i + 1 : j]), j + 1
        if ch == "\n":
            break
        j += 1
    raise AsmError(f"第 {line} 行：字符串没有闭合的双引号")


def _scan_hash(text: str, i: int, line: int) -> tuple[int, int]:
    """``#XXXXXXXX``：32 位标识符（FNV-1a32 哈希），当整数用。"""
    j = i + 1
    n = len(text)
    while j < n and text[j] in "0123456789abcdefABCDEF":
        j += 1
    if j == i + 1:
        raise AsmError(f"第 {line} 行：# 后面没有十六进制数字")
    val = int(text[i + 1 : j], 16)
    if val > 0xFFFFFFFF:
        raise AsmError(f"第 {line} 行：标识符 {text[i:j]} 超出 32 位")
    return val, j


def _scan_int(text: str, i: int, line: int) -> tuple[int, int]:
    n = len(text)
    j = i
    if text[j] in "+-":
        j += 1
    if text.startswith(("0x", "0X"), j):
        k = j + 2
        while k < n and text[k] in "0123456789abcdefABCDEF_":
            k += 1
        digits = text[j + 2 : k].replace("_", "")
        if not digits:
            raise AsmError(f"第 {line} 行：0x 后面没有十六进制数字")
        val = int(digits, 16)
    else:
        k = j
        while k < n and (text[k].isdigit() or text[k] == "_"):
            k += 1
        digits = text[j:k].replace("_", "")
        if not digits:
            raise AsmError(f"第 {line} 行：无法解析的数字")
        val = int(digits, 10)
    return (-val if text[i] == "-" else val), k


def tokenize(text: str) -> list[list[Token]]:
    """按行切分记号；空行与纯注释行返回空列表，行号从 1 开始。"""
    lines: list[list[Token]] = []
    for lineno, raw in enumerate(text.split("\n"), 1):
        toks: list[Token] = []
        i = 0
        n = len(raw)
        while i < n:
            ch = raw[i]
            if ch in " \t\r":
                i += 1
                continue
            if ch == ";":
                break                              # 注释到行尾
            if ch == '"':
                val, i = _scan_string(raw, i, lineno)
                toks.append(Token("str", val, lineno))
                continue
            if raw.startswith("->", i):
                toks.append(Token("punct", "->", lineno))
                i += 2
                continue
            if ch in _PUNCT:
                toks.append(Token("punct", ch, lineno))
                i += 1
                continue
            if ch == "#":
                val, i = _scan_hash(raw, i, lineno)
                toks.append(Token("int", val, lineno))
                continue
            if ch.isdigit() or (
                ch in "+-" and i + 1 < n and raw[i + 1].isdigit()
            ):
                val, i = _scan_int(raw, i, lineno)
                toks.append(Token("int", val, lineno))
                continue
            j = i
            while j < n and (raw[j].isalnum() or raw[j] in "._-/"):
                j += 1
            if j == i:
                raise AsmError(f"第 {lineno} 行：无法识别的字符 {ch!r}")
            toks.append(Token("id", raw[i:j], lineno))
            i = j
        lines.append(toks)
    return lines


# --------------------------------------------------------------------------
# 值语法
#
#   value := int | string | list | map | blob | null | true | false
#   list  := '[' [ value { ',' value } ] ']'
#   map   := '{' [ key ':' value { ',' key ':' value } ] '}'
#   blob  := 'blob' string          外置字节块，string 是相对本 asm 的文件名
#
# 逗号可省（列表里连着写也行），这样 op4 的布局数组能压成一行。
# --------------------------------------------------------------------------

class Placeholder:
    """占位：一个待载入的外置字节块。汇编器把它换成真实 bytes。"""

    __slots__ = ("path",)

    def __init__(self, path: str):
        self.path = path

    def __repr__(self) -> str:
        return f"blob({self.path!r})"


_WORDS = {"null": None, "true": True, "false": False}


def parse_value(toks: list[Token], pos: int) -> tuple[object, int]:
    """从 toks[pos] 开始解析一个值，返回 (值, 下一个位置)。"""
    if pos >= len(toks):
        raise AsmError("需要一个值，但行已结束")
    tok = toks[pos]

    if tok.kind in ("int", "str"):
        return tok.value, pos + 1

    if tok.kind == "id":
        if tok.value == "blob":
            if pos + 1 >= len(toks) or toks[pos + 1].kind != "str":
                raise AsmError(f"第 {tok.line} 行：blob 后面要跟文件名字符串")
            return Placeholder(toks[pos + 1].value), pos + 2
        if tok.value in _WORDS:
            return _WORDS[tok.value], pos + 1
        raise AsmError(f"第 {tok.line} 行：无法识别的记号 {tok.value!r}")

    if tok.value == "[":
        items: list = []
        pos += 1
        while True:
            if pos >= len(toks):
                raise AsmError(f"第 {tok.line} 行：列表缺少 ]")
            cur = toks[pos]
            if cur.kind == "punct" and cur.value == "]":
                return items, pos + 1
            if cur.kind == "punct" and cur.value == ",":
                pos += 1
                continue
            item, pos = parse_value(toks, pos)
            # ``N*值``：重复 N 次。长并行数组里大片相同值靠它压缩。
            if (pos < len(toks) and toks[pos].kind == "punct"
                    and toks[pos].value == "*"):
                if not isinstance(item, int) or item < 0:
                    raise AsmError(f"第 {cur.line} 行：* 前面要是非负整数重复次数")
                repeated, pos = parse_value(toks, pos + 1)
                items.extend([repeated] * item)
                continue
            items.append(item)

    if tok.value == "{":
        pairs: list[tuple[object, object]] = []
        pos += 1
        while True:
            if pos >= len(toks):
                raise AsmError(f"第 {tok.line} 行：映射缺少 }}")
            cur = toks[pos]
            if cur.kind == "punct" and cur.value == "}":
                return ve_cbor.CborMap(pairs), pos + 1
            if cur.kind == "punct" and cur.value == ",":
                pos += 1
                continue
            key, pos = parse_value(toks, pos)
            if pos >= len(toks) or toks[pos].kind != "punct" or toks[pos].value != ":":
                raise AsmError(f"第 {cur.line} 行：映射的键后面要跟 :")
            val, pos = parse_value(toks, pos + 1)
            pairs.append((key, val))

    raise AsmError(f"第 {tok.line} 行：值的位置不该出现 {tok.value!r}")
