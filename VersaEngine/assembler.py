"""VersaEngine 脚本汇编器：asm 文本 -> CBOR 载荷。

用法::

    python assembler.py <输入...> [-o 输出] [--encoding utf-8]

输入可以是单个 ``.asm.txt``、多个文件，或者一个目录（自动批处理目录下所有
``.asm.txt``）。也可以把文件直接拖到本脚本图标上。

  * 单文件且未指定 ``-o``：在输入同目录生成 ``<basename>.rebuild``
  * 多文件或目录：各生成一份 ``<basename>.rebuild``；``-o`` 当作输出目录
  * ``--as-cbor``：输出名用 ``.cbor``，方便直接覆盖回 ``_packages`` 再封包

编码优先取命令行 ``--encoding``，否则读 asm 头部的 ``.encoding``，都没有就
用 utf-8。占位符 ``{{XX}}`` 绕过编码直接落原始字节，所以编码选错也不会丢字节。

与 ``disassembler.py`` 配对，往返逐字节一致。
"""

from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import opcodelist as OL
import ve_cbor
import ve_opcodes as OP
from opcodelist import AsmError, Token

SHOW_OP = 4                                        # 带精灵布局的指令
CHOICE_OP = 7                                      # 带选项表的指令

# show 主行上的精灵字段（`as=` `clip=` 之类），不是 Command 的整数参数
_SPRITE_KEYS = frozenset({"as", "flags", "sheet", "clip", "tint",
                          "anim", "style", "base"})


def _depth(toks: list[Token]) -> int:
    """一行结束时未闭合的括号层数。"""
    n = 0
    for t in toks:
        if t.kind == "punct":
            if t.value in "[{":
                n += 1
            elif t.value in "]}":
                n -= 1
    return n


class Cursor:
    """按逻辑行游走的记号流。

    括号没闭合的行会自动把后续行拼进来，所以长数组可以折成多行写。
    """

    def __init__(self, lines: list[list[Token]]):
        self.lines = lines
        self.i = 0
        self._cache: list[Token] | None = None
        self._end = 0

    def _load(self) -> list[Token] | None:
        while self.i < len(self.lines) and not self.lines[self.i]:
            self.i += 1
        if self.i >= len(self.lines):
            return None
        toks = list(self.lines[self.i])
        j = self.i + 1
        depth = _depth(toks)
        while depth > 0:
            if j >= len(self.lines):
                raise AsmError(f"第 {toks[0].line} 行：括号没有闭合")
            toks.extend(self.lines[j])
            depth += _depth(self.lines[j])
            j += 1
        self._end = j
        return toks

    def peek(self) -> list[Token] | None:
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    def next(self) -> list[Token] | None:
        toks = self.peek()
        if toks is not None:
            self.i = self._end
            self._cache = None
        return toks


def _directive(toks: list[Token]) -> str | None:
    """一行是不是 ``.name ...`` 形式的伪指令；是则返回 name。"""
    if toks and toks[0].kind == "id" and toks[0].value.startswith("."):
        return toks[0].value[1:]
    return None


def _label(toks: list[Token]) -> str | None:
    """一行是不是 ``name:`` 标签定义。"""
    if len(toks) == 2 and toks[0].kind == "id" and \
            toks[1].kind == "punct" and toks[1].value == ":":
        return toks[0].value
    return None


def _one_value(toks: list[Token], start: int, what: str):
    """解析从 start 起的唯一一个值，要求正好用完这一行。"""
    value, pos = OL.parse_value(toks, start)
    if pos != len(toks):
        raise AsmError(f"第 {toks[pos].line} 行：{what} 后面有多余的记号 "
                       f"{toks[pos].value!r}")
    return value


def _ints(toks: list[Token], start: int, end: int, line: int) -> list[int]:
    out = []
    for t in toks[start:end]:
        if t.kind != "int":
            raise AsmError(f"第 {line} 行：这里只能是整数，遇到 {t.value!r}")
        out.append(t.value)
    return out


# ---------------------------------------------------------------------------
# 指令流
# ---------------------------------------------------------------------------


class NodeBuilder:
    """两遍汇编：先收指令与标签，再把标签引用换成节点下标。

    节点下标随指令增删而变化，所以引用一律走标签，不硬编码下标。
    """

    def __init__(self):
        self.nodes: list[list] = []
        # (节点序号, 路径, 标签名, 行号)；路径是从节点内层层下标
        self.fixups: list[tuple[int, tuple[int, ...], str, int]] = []
        self.labels: dict[str, int] = {}

    def mark(self, name: str, line: int) -> None:
        if name in self.labels:
            raise AsmError(f"第 {line} 行：标签 {name} 重复定义")
        self.labels[name] = len(self.nodes)

    def emit(self, node: list) -> None:
        self.nodes.append(node)

    def ref(self, path: tuple[int, ...], label: str, line: int) -> int:
        """记一个待回填的标签引用，占位先填 0。"""
        self.fixups.append((len(self.nodes), path, label, line))
        return 0

    def resolve(self) -> None:
        for node_idx, path, label, line in self.fixups:
            if label not in self.labels:
                raise AsmError(f"第 {line} 行：引用了未定义的标签 {label}")
            slot = self.nodes[node_idx]
            for step in path[:-1]:
                slot = slot[step]
            slot[path[-1]] = self.labels[label]


def _is_label_ref(tok: Token) -> bool:
    """本地跳转引用：``loc_`` 是节点下标，``label_`` 是本 scene 的标签名。"""
    return tok.kind == "id" and (tok.value.startswith("loc_")
                                 or tok.value.startswith("label_"))


# 各指令哪个参数槽是本地跳转目标（与 disassembler._JUMP_ARGS 一致）
_JUMP_SLOTS = {1: (0,), 27: (4,), 41: (0,)}


class _LabelRef:
    """参数位上的标签引用。参数按名字给，槽位要等补全缺省值后才定，
    所以先占位，emit 之前再登记回填路径。"""

    __slots__ = ("name", "line")

    def __init__(self, name: str, line: int):
        self.name = name
        self.line = line


def _far_key(tok: Token) -> int | None:
    """``far_XXXXXXXX``：跨 scene 的全局标签键，是哈希不是下标，直接取值。"""
    if tok.kind == "id" and tok.value.startswith("far_"):
        try:
            return int(tok.value[4:], 16)
        except ValueError:
            raise AsmError(f"第 {tok.line} 行：{tok.value} 的十六进制后缀非法") from None
    return None


def _int_list(toks: list[Token], pos: int) -> tuple[list[int], int]:
    """``1,2,3`` 形式的整数列表（用于 sheet= / clip=）。"""
    out = [toks[pos].value]
    pos += 1
    while (pos + 1 < len(toks) and toks[pos].kind == "punct"
           and toks[pos].value == "," and toks[pos + 1].kind == "int"):
        out.append(toks[pos + 1].value)
        pos += 2
    return out, pos


def _parse_instr(toks: list[Token], nb: NodeBuilder, cur: Cursor) -> None:
    """解析一条指令行（含缩进续行），追加到 nb。

    参数写成 ``名字=值``，顺序随意，没写的按 ``ARG_DEFAULTS`` 补。
    """
    head = toks[0]
    op = OL.MNEMONICS.get(head.value) if head.kind == "id" else None
    if op is None:
        raise AsmError(
            f"第 {head.line} 行：未定义的助记符 {head.value!r}。"
            f"合法助记符见 vetool/ve_opcodes.py 的 OPCODES"
        )
    argn = OP.arg_names(op)
    slot_of = {name: i for i, name in enumerate(argn)}

    args: dict[int, object] = {}
    sprite: dict = {}
    pos = 1
    while pos < len(toks):
        name, pos = _field_name(toks, pos, head)
        if name in _SPRITE_KEYS:
            if op != SHOW_OP:
                raise AsmError(f"第 {head.line} 行：{head.value} 没有 {name}= 字段")
            pos = _sprite_field(name, toks, pos, sprite)
            continue
        if name not in slot_of:
            allowed = ", ".join(argn) or "无"
            raise AsmError(f"第 {toks[pos - 2].line} 行：{head.value} 没有参数 "
                           f"{name}（可用：{allowed}）")
        slot = slot_of[name]
        if slot in args:
            raise AsmError(f"第 {toks[pos - 2].line} 行：参数 {name} 重复赋值")
        args[slot], pos = _arg_value(op, slot, toks, pos, nb)

    # 缩进续行：.part / .option / .layout 属于上一条指令
    parts: list = []
    options: list = []
    layout = None
    while True:
        nxt = cur.peek()
        name = _directive(nxt) if nxt else None
        if name not in ("part", "option", "options", "layout"):
            break
        cur.next()
        if name == "part":
            _need_op(op, SHOW_OP, name, nxt[0].line, head.value)
            parts.append(_read_part(nxt))
        elif name == "layout":
            _need_op(op, SHOW_OP, name, nxt[0].line, head.value)
            layout = _one_value(nxt, 1, ".layout")
        elif name == "options":
            _need_op(op, CHOICE_OP, name, nxt[0].line, head.value)
            layout = _one_value(nxt, 1, ".options")
        else:
            _need_op(op, CHOICE_OP, name, nxt[0].line, head.value)
            options.append(_read_option(nxt, nb, len(options)))

    node = [op] + OL.fill_args(op, args, head.line)
    for i, arg in enumerate(node):
        if isinstance(arg, _LabelRef):
            node[i] = nb.ref((i,), arg.name, arg.line)
    _check_argc(op, head, node[1:])

    if op == SHOW_OP:
        if layout is not None:
            if sprite or parts:
                raise AsmError(f"第 {head.line} 行：.layout 是原始数组，"
                               f"不能同时写具名精灵字段")
            node.append(layout)
        else:
            sprite["extra"] = parts
            # 部件表首项是底图自身，等于 asset 参数，除非显式 base=none
            sprite.setdefault("base", bool(parts))
            asset = node[2] if len(node) > 2 else -1
            node.append(OL.sprite_encode(sprite, asset))
    elif op == CHOICE_OP:
        node.append(layout if layout is not None else options)
    elif layout is not None or sprite or parts or options:
        raise AsmError(f"第 {head.line} 行：{head.value} 不带尾部结构")
    nb.emit(node)


def _need_op(op: int, want: int, name: str, line: int, mnemonic: str) -> None:
    if op != want:
        raise AsmError(f"第 {line} 行：{mnemonic} 不带 .{name} 续行，放错了位置")


def _field_name(toks: list[Token], pos: int, head: Token) -> tuple[str, int]:
    """读一个 ``名字=`` 前缀，返回名字与 ``=`` 之后的位置。"""
    tok = toks[pos]
    if tok.kind != "id" or pos + 1 >= len(toks) or \
            toks[pos + 1].kind != "punct" or toks[pos + 1].value != "=":
        raise AsmError(f"第 {tok.line} 行：{head.value} 的参数要写成 `名字=值`，"
                       f"遇到 {tok.value!r}")
    return tok.value, pos + 2


def _arg_value(op: int, slot: int, toks: list[Token], pos: int,
               nb: NodeBuilder):
    """读一个参数的值：标签引用、枚举词、或普通字面量。"""
    tok = toks[pos]
    if slot in _JUMP_SLOTS.get(op, ()) and _is_label_ref(tok):
        # 节点里参数从下标 1 起；下标此刻还不确定，先记名字，emit 前统一回填
        return _LabelRef(tok.value, tok.line), pos + 1
    key = _far_key(tok)
    if key is not None:
        return key, pos + 1
    enum = OL.ENUM_ARGS.get(OP.arg_names(op)[slot])
    if enum and tok.kind == "id" and tok.value in enum[1]:
        return enum[1][tok.value], pos + 1
    return OL.parse_value(toks, pos)


def _sprite_field(name: str, toks: list[Token], pos: int, out: dict) -> int:
    """读一个精灵字段的值。"""
    tok = toks[pos]
    if name == "as":
        if tok.kind != "id" or tok.value not in OL.SPRITE_KIND_VALUES:
            kinds = ", ".join(OL.SPRITE_KIND_VALUES)
            raise AsmError(f"第 {tok.line} 行：as= 只能是 {kinds}")
        out["kind"] = OL.SPRITE_KIND_VALUES[tok.value]
        return pos + 1
    if name in ("anim", "style", "base"):
        if tok.kind != "id" or tok.value != "none":
            raise AsmError(f"第 {tok.line} 行：{name}= 只能写 none")
        out[name] = False
        return pos + 1
    if tok.kind != "int":
        raise AsmError(f"第 {tok.line} 行：{name}= 后面要跟整数")
    if name in ("sheet", "clip"):
        out[name], pos = _int_list(toks, pos)
        return pos
    out[name] = tok.value                          # flags / tint
    return pos + 1


def _read_part(toks: list[Token]) -> object:
    """``.part #XXXXXXXX [at x,y]`` -> 部件表的一项。"""
    line = toks[0].line
    if len(toks) < 2 or toks[1].kind != "int":
        raise AsmError(f"第 {line} 行：.part 要写成 `.part #资源哈希 [at x,y]`")
    if len(toks) == 2:
        return toks[1].value
    if len(toks) != 6 or toks[2].kind != "id" or toks[2].value != "at" or \
            toks[3].kind != "int" or toks[5].kind != "int":
        raise AsmError(f"第 {line} 行：.part 的位置要写成 `at x,y`")
    return [toks[1].value, toks[3].value, toks[5].value]


def _read_option(toks: list[Token], nb: NodeBuilder, k: int) -> list:
    """``.option #XXXXXXXX -> 标签`` -> 选项表的一项。"""
    line = toks[0].line
    if len(toks) != 4 or toks[1].kind != "int" or \
            toks[2].value != "->" or not _is_label_ref(toks[3]):
        raise AsmError(f"第 {line} 行：.option 要写成 "
                       f"`.option #文本哈希 -> 标签`")
    # 选项目标在节点里的路径是 (1, k, 1)：尾部选项表是节点的第 1 项
    return [toks[1].value, nb.ref((1, k, 1), toks[3].value, toks[3].line)]


def _check_argc(op: int, head: Token, args: list) -> None:
    """参数个数越界检查。

    Vm::decode 最多收 8 个整数参数（Command+0x0C..+0x2C），超了会写坏相邻字段；
    再按 opcodelist 里各指令声明的参数名个数卡上限。
    """
    ints = sum(1 for a in args if isinstance(a, int))
    if ints > 8:
        raise AsmError(f"第 {head.line} 行：{head.value} 有 {ints} 个整数参数，"
                       f"引擎的 Command 只有 8 个槽")
    limit = len(OP.arg_names(op))
    if ints > limit:
        names = ", ".join(OP.arg_names(op)) or "无"
        raise AsmError(f"第 {head.line} 行：{head.value} 最多 {limit} 个参数"
                       f"（{names}），这里给了 {ints} 个")


# ---------------------------------------------------------------------------
# 块伪指令
# ---------------------------------------------------------------------------


def _read_nodes(cur: Cursor, out: dict, labels: dict) -> None:
    """``.nodes`` … ``.end``：指令流。"""
    nb = NodeBuilder()
    seen: list[int] = []                           # label_ 标签的出现顺序
    while True:
        toks = cur.next()
        if toks is None:
            raise AsmError(".nodes 缺少 .end")
        if _directive(toks) == "end":
            break
        name = _label(toks)
        if name is not None:
            nb.mark(name, toks[0].line)
            if name.startswith("label_"):
                try:
                    seen.append(int(name[6:], 16))
                except ValueError:
                    raise AsmError(f"第 {toks[0].line} 行：{name} 的十六进制"
                                   f"后缀非法") from None
            continue
        if _directive(toks) == "node":
            nb.emit(_one_value(toks, 1, ".node"))
            continue
        _parse_instr(toks, nb, cur)
    nb.resolve()
    out["nodes"] = nb.nodes
    labels.update(nb.labels)
    out.setdefault("label_keys", seen)              # 没显式写 .label_keys 时用


def _read_map(cur: Cursor, key: str, out: dict) -> None:
    """``.table`` / ``.pieces`` … ``.end``：一行一对 ``键: 值``。"""
    pairs: list[tuple[object, object]] = []
    while True:
        toks = cur.next()
        if toks is None:
            raise AsmError(f".{key} 缺少 .end")
        if _directive(toks) == "end":
            break
        k, pos = OL.parse_value(toks, 0)
        if pos >= len(toks) or toks[pos].kind != "punct" or toks[pos].value != ":":
            raise AsmError(f"第 {toks[0].line} 行：键后面要跟 :")
        pairs.append((k, _one_value(toks, pos + 1, "值")))
    out[key] = ve_cbor.CborMap(pairs)


def _read_paths(cur: Cursor, out: dict) -> None:
    """``.paths`` … ``.end``：路径 哈希 采样数 循环起点 循环长度。"""
    paths: list[str] = []
    cols: list[list[int]] = [[], [], [], []]
    while True:
        toks = cur.next()
        if toks is None:
            raise AsmError(".paths 缺少 .end")
        if _directive(toks) == "end":
            break
        if len(toks) != 5 or toks[0].kind != "str":
            raise AsmError(f"第 {toks[0].line} 行：.paths 每行要写成 "
                           f"`\"路径\" 哈希 采样数 循环起点 循环长度`")
        paths.append(toks[0].value)
        for c, v in zip(cols, _ints(toks, 1, 5, toks[0].line)):
            c.append(v)
    out["paths"] = paths
    for name, col in zip(("path_ids", "sample_counts",
                          "loop_starts", "loop_lengths"), cols):
        out[name] = col


# 角色表的列顺序，与 disassembler._SPK_1 / _SPK_5 一致
_SPK_1 = OL.SPEAKER_COLS
_SPK_5 = OL.SPEAKER_WIDE


def _read_speakers(cur: Cursor, out: dict) -> None:
    """``.speakers`` … ``.end``。

    两种写法：每行只有一个 ``#角色键``（其余 15 列按规则推导），或者每行
    完整的 6 列 ``|`` 5 项 ``|`` 5 项。同一张表里不能混用。
    """
    rows: list[list[int]] = []
    per = OL.SPEAKER_PER
    while True:
        toks = cur.next()
        if toks is None:
            raise AsmError(".speakers 缺少 .end")
        if _directive(toks) == "end":
            break
        line = toks[0].line
        bars = [i for i, t in enumerate(toks)
                if t.kind == "punct" and t.value == "|"]
        if not bars:
            if len(toks) != 1 or toks[0].kind != "int":
                raise AsmError(f"第 {line} 行：.speakers 的名册行只能是一个 "
                               f"#角色键；要写完整列请用两个 | 分隔")
            rows.append([toks[0].value])
            continue
        if len(bars) != 2:
            raise AsmError(f"第 {line} 行：.speakers 每行要有两个 | 分隔符")
        first = _ints(toks, 0, bars[0], line)
        p1 = _ints(toks, bars[0] + 1, bars[1], line)
        p2 = _ints(toks, bars[1] + 1, len(toks), line)
        if len(first) != len(_SPK_1) or len(p1) != per or len(p2) != per:
            raise AsmError(f"第 {line} 行：.speakers 每行要是 "
                           f"{len(_SPK_1)} | {per} | {per} 个整数")
        rows.append(first + p1 + p2)

    widths = {len(r) for r in rows}
    if len(widths) > 1:
        raise AsmError(".speakers 里名册行和完整列行不能混用")
    keys = [r[0] for r in rows]
    if not rows or widths == {1}:
        out["speaker_keys"] = keys
        out.update(OL.speakers_derive(keys))
        return
    out["speaker_keys"] = keys
    for i, name in enumerate(_SPK_1[1:], 1):
        out[name] = [r[i] for r in rows]
    base = len(_SPK_1)
    for j, name in enumerate(_SPK_5):
        start = base + j * per
        out[name] = [x for r in rows for x in r[start : start + per]]


def _is_include(toks: list[Token]) -> bool:
    return (len(toks) == 3 and toks[1].kind == "id"
            and toks[1].value == "include" and toks[2].kind == "str")


def _read_roster(toks: list[Token], out: dict, base_dir: str) -> None:
    """``.speakers include "speakers.txt"``：从共享文件读角色名册。

    共享文件一行一个 ``#角色键``，其余 15 列按 speakers_derive 推导。
    """
    fname = toks[2].value
    line = toks[0].line
    path = os.path.join(base_dir, fname)
    if not os.path.isfile(path):
        raise AsmError(f"第 {line} 行：找不到共享角色名册 {fname}"
                       f"（应与 asm 同目录）")
    with open(path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    keys: list[int] = []
    for row in OL.tokenize(text):
        if not row:
            continue
        if len(row) != 1 or row[0].kind != "int":
            raise AsmError(f"{fname} 第 {row[0].line} 行："
                           f"共享名册每行只能是一个 #角色键")
        keys.append(row[0].value)
    if not keys:
        raise AsmError(f"{fname}：共享名册里没有角色键")
    out["speaker_keys"] = keys
    out.update(OL.speakers_derive(keys))


_BLOCKS = {
    "nodes": _read_nodes,
    "paths": lambda cur, out, labels: _read_paths(cur, out),
    "speakers": lambda cur, out, labels: _read_speakers(cur, out),
    "table": lambda cur, out, labels: _read_map(cur, "table", out),
    "pieces": lambda cur, out, labels: _read_map(cur, "pieces", out),
}

# 只出现在头部、不进载荷的伪指令
_META = ("payload", "encoding", "keyorder")


# ---------------------------------------------------------------------------
# 顶层
# ---------------------------------------------------------------------------


def _key_sort(key):
    """默认键序：整数在前，各自升序。实测除字体外全部载荷都是这个序。"""
    return (isinstance(key, str), key)


def _label_index(key: int, labels: dict[str, int]) -> int:
    """标签哈希 -> 当前节点下标。"""
    name = f"label_{key:08X}"
    if name not in labels:
        raise AsmError(f"label_keys 里的 0x{key:08X} 在 .nodes 里没有对应的 "
                       f"{name}: 标签")
    return labels[name]


def assemble(text: str, base_dir: str, encoding: str | None = None):
    """asm 文本 -> (CBOR 值, 使用的编码)。"""
    lines = OL.tokenize(text)
    cur = Cursor(lines)
    out: dict = {}
    labels: dict[str, int] = {}
    pending: list[tuple[str, str, int]] = []       # 待回填的标签引用字段
    keyorder: list | None = None
    enc = encoding
    header_enc: str | None = None
    single = None                                  # 非映射载荷

    while True:
        toks = cur.next()
        if toks is None:
            break
        name = _directive(toks)
        if name is None:
            raise AsmError(f"第 {toks[0].line} 行：顶层只允许 .伪指令，遇到 "
                           f"{toks[0].value!r}")

        if name == "encoding":
            header_enc = _one_value(toks, 1, ".encoding")
            continue
        if name == "payload":
            _one_value(toks, 1, ".payload")        # 仅供人看
            continue
        if name == "keyorder":
            keyorder = _one_value(toks, 1, ".keyorder")
            continue
        if name == "value":
            single = _one_value(toks, 1, ".value")
            continue

        if len(toks) == 1 and name in _BLOCKS:
            _BLOCKS[name](cur, out, labels)
            continue
        if name == "speakers" and _is_include(toks):
            _read_roster(toks, out, base_dir)
            continue
        if len(toks) == 1:
            raise AsmError(f"第 {toks[0].line} 行：.{name} 后面缺少值")
        if len(toks) == 2 and _is_label_ref(toks[1]):
            pending.append((name, toks[1].value, toks[1].line))
            out[name] = 0
            continue
        out[name] = _one_value(toks, 1, f".{name}")

    for field, label, line in pending:
        if label not in labels:
            raise AsmError(f"第 {line} 行：.{field} 引用了未定义的标签 {label}")
        out[field] = labels[label]

    # 脚本载荷里恒定的标量与空数组不写在 asm 里，这里补回
    if "nodes" in out:
        for key, val in OL.SCRIPT_DEFAULTS.items():
            if key not in out:
                out[key] = list(val) if isinstance(val, list) else val

    # label_values 由 label_keys 逐个查标签算出，指令增删也不会错位。
    # 只对有 .nodes 的脚本载荷成立；project 的 label_keys 是全局跨 scene 表。
    if "nodes" in out and "label_keys" in out and "label_values" not in out:
        out["label_values"] = [
            _label_index(key, labels) for key in out["label_keys"]
        ]

    if enc is None:
        enc = header_enc or OL.DEFAULT_ENCODING

    if single is not None:
        if out:
            raise AsmError(".value 不能和其他字段同时出现")
        return _load_blobs(single, base_dir), enc

    order = keyorder if keyorder is not None else sorted(out, key=_key_sort)
    missing = set(out) - set(order)
    if missing:
        raise AsmError(f".keyorder 漏了字段: {sorted(missing)}")
    unknown = [k for k in order if k not in out]
    if unknown:
        raise AsmError(f".keyorder 里有未定义的字段: {unknown}")

    pairs = [(k, _load_blobs(out[k], base_dir)) for k in order]
    return ve_cbor.CborMap(pairs), enc


def _load_blobs(value, base_dir: str):
    """把 blob 占位换成真实字节，其余原样。"""
    if isinstance(value, OL.Placeholder):
        path = os.path.join(base_dir, value.path)
        if not os.path.isfile(path):
            raise AsmError(f"找不到外置字节块 {value.path}（应与 asm 同目录）")
        with open(path, "rb") as fh:
            return fh.read()
    if isinstance(value, list):
        return [_load_blobs(v, base_dir) for v in value]
    if isinstance(value, ve_cbor.CborMap):
        return ve_cbor.CborMap(
            [(_load_blobs(k, base_dir), _load_blobs(v, base_dir))
             for k, v in value.pairs]
        )
    return value


def _collect(inputs: list[str]) -> list[str]:
    files: list[str] = []
    for item in inputs:
        if os.path.isdir(item):
            for name in sorted(os.listdir(item)):
                if name.endswith(".asm.txt"):
                    files.append(os.path.join(item, name))
        else:
            files.append(item)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="VersaEngine 脚本汇编器：asm.txt -> CBOR")
    ap.add_argument("inputs", nargs="+", help="输入 .asm.txt 文件或目录")
    ap.add_argument("-o", "--output",
                    help="输出文件（单输入）或输出目录（多输入）")
    ap.add_argument("--encoding",
                    help="覆盖 asm 头部的 .encoding")
    ap.add_argument("--as-cbor", action="store_true",
                    help="输出用 .cbor 而不是 .rebuild 后缀")
    args = ap.parse_args(argv)

    files = _collect(args.inputs)
    if not files:
        print("错误: 没有输入文件", file=sys.stderr)
        return 2
    for path in files:
        if not os.path.isfile(path):
            print(f"错误: 找不到 {path}", file=sys.stderr)
            return 2

    single = len(files) == 1
    named = single and args.output and not os.path.isdir(args.output)
    if named:
        out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    else:
        out_dir = args.output or os.path.dirname(os.path.abspath(files[0]))
    os.makedirs(out_dir, exist_ok=True)

    suffix = ".cbor" if args.as_cbor else ".rebuild"
    fails = 0
    for path in files:
        stem = os.path.basename(path)
        for tail in (".asm.txt", ".txt"):
            if stem.endswith(tail):
                stem = stem[: -len(tail)]
                break
        dst = args.output if named else os.path.join(out_dir, stem + suffix)
        try:
            with open(path, "r", encoding="utf-8", newline="") as fh:
                text = fh.read()
            value, enc = assemble(text, os.path.dirname(os.path.abspath(path)),
                                  args.encoding)
            payload = ve_cbor.dumps(value)
        except (AsmError, ve_cbor.CborError) as exc:
            print(f"[失败] {stem}: {exc}", file=sys.stderr)
            fails += 1
            continue
        with open(dst, "wb") as fh:
            fh.write(payload)
        print(f"[完成] {stem} -> {os.path.basename(dst)} "
              f"（{len(payload)} 字节，编码 {enc}）")

    print(f"\n共 {len(files)} 个输入，成功 {len(files) - fails}，失败 {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    code = main()
    if len(sys.argv) > 1 and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\n按回车关闭…")
        except EOFError:
            pass
    sys.exit(code)
