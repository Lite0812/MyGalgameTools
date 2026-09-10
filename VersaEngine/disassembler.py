"""VersaEngine 脚本反汇编器：CBOR 载荷 -> 语义化 asm 文本。

用法::

    python disassembler.py <输入...> [-o 输出] [--encoding utf-8]

输入可以是单个 ``.cbor``、多个文件，或者一个目录（自动批处理目录下所有
``.cbor``）。也可以把文件直接拖到本脚本图标上。

  * 单文件且未指定 ``-o``：在输入同目录生成 ``<basename>.asm.txt``
  * 多文件或目录：每个输入各生成一份 ``<basename>.asm.txt``；``-o`` 此时
    当作输出目录

输出是纯语义视图：没有任何原始十六进制转储，字符串按选定编码可读，不可安全
显示的字节写成 ``{{XX}}`` 占位符。跳转目标全部符号化成 ``loc_XXXXXXXX``
标签，标签定义前留一个空行。

与 ``assembler.py`` 配对，往返逐字节一致（见 README 的零突变验证）。
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
from ve_crypto import fnv1a32
import ve_opcodes as OP


class Writer:
    """带缩进的行输出器，顺手记录是否需要在标签前补空行。"""

    def __init__(self, fh):
        self.fh = fh
        self._blank = True                 # 文件开头已经算"刚空过一行"

    def line(self, text: str = "") -> None:
        if text:
            self._blank = False
        else:
            if self._blank:
                return                     # 不连续输出空行
            self._blank = True
        print(text, file=self.fh)

    def blank(self) -> None:
        self.line()


# ---------------------------------------------------------------------------
# 名字反查
# ---------------------------------------------------------------------------


class Names:
    """把 FNV-1a32 哈希还原成可读名字，只用于生成 ``;`` 注释。

    注释不参与重建，所以这里查不到也无所谓；asm 里的数值本身始终是原值。
    """

    def __init__(self, pkg_dir: str | None):
        self.assets: dict[int, str] = {}
        self.text: dict[int, str] = {}
        self.scenes: dict[int, str] = {}
        if pkg_dir and os.path.isdir(pkg_dir):
            self._load(pkg_dir)

    def _read(self, path: str):
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            value = ve_cbor.loads(fh.read())
        if not isinstance(value, ve_cbor.CborMap):
            return None
        return {k: v for k, v in value.pairs}

    def _load(self, pkg: str) -> None:
        d = self._read(os.path.join(pkg, "assets.cbor"))
        if d:
            paths = d.get("paths") or []
            for key, val in zip(d.get("keys") or [], d.get("values") or []):
                if isinstance(val, list) and val and 0 <= val[0] < len(paths):
                    self.assets[key] = paths[val[0]]
        for name in sorted(os.listdir(pkg)):
            if not name.endswith(".strings.cbor"):
                continue
            d = self._read(os.path.join(pkg, name))
            tab = d.get("table") if d else None
            if isinstance(tab, ve_cbor.CborMap):
                for k, v in tab.pairs:
                    if isinstance(v, str) and v:
                        self.text.setdefault(k, v)
        d = self._read(os.path.join(pkg, "project.cbor"))
        if d:
            for s in d.get("scenes") or []:
                if isinstance(s, str):
                    self.scenes[fnv1a32(s.encode("utf-8"))] = s

    def hint(self, role: str, key: int) -> str | None:
        """给一个参数配一句可读说明；没有就返回 None。"""
        if not isinstance(key, int) or key == 0:
            return None
        if role == "asset":
            return self.assets.get(key)
        if role == "text":
            return self.text.get(key) or None
        if role in ("name", "word"):
            return self.assets.get(key) or self.scenes.get(key)
        return None


def _flat(text: str) -> str:
    """把一段文本压成注释里的一行：换行变空格，不截断。

    台词是脚本里最该看清的东西，截断只会逼人回头查哈希。
    """
    flat = "".join(" " if ch in "\r\n" or not ch.isprintable() else ch
                   for ch in text)
    return " ".join(flat.split())


# ---------------------------------------------------------------------------
# 值格式化
# ---------------------------------------------------------------------------


def fmt_value(value, enc: str) -> str:
    """一个 CBOR 值 -> asm 字面量（单行）。"""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return OL.quote(value, enc)
    if isinstance(value, (list, tuple)):
        return "[" + " ".join(fmt_value(v, enc) for v in value) + "]"
    if isinstance(value, ve_cbor.CborMap):
        return "{" + " ".join(
            f"{fmt_value(k, enc)}: {fmt_value(v, enc)}" for k, v in value.pairs
        ) + "}"
    if isinstance(value, bytes):
        raise OL.AsmError("字节块必须写成外置 blob，不能内联")
    raise OL.AsmError(f"无法格式化类型 {type(value).__name__}")


def _loc(index: int) -> str:
    return f"loc_{index:08X}"


def _lbl(key: int) -> str:
    """本 scene 声明的标签（label_keys 里的哈希）。"""
    return f"label_{key:08X}"


def _far(key: int) -> str:
    """跨 scene 跳转的全局标签键：落点在别的 scene，这里只是个哈希。"""
    return f"far_{key:08X}"


# ---------------------------------------------------------------------------
# 指令
# ---------------------------------------------------------------------------

# 跳转参数：值是本地节点下标，输出成标签引用
_JUMP_ARGS = {1: {0: "node"}, 27: {4: "node"}, 41: {0: "node"}}

# 跨 scene 跳转的全局标签键（op1 的第二参），不是本地下标
_FAR_ARGS = {1: {1: "far"}}


def _fmt_arg(name: str, value: int) -> str:
    """一个整数参数的字面量写法，按参数语义选进制。"""
    if name in OL.HASH_ARGS:
        return "#%08X" % value                     # 32 位标识符（FNV-1a32）
    if name in OL.COLOR_ARGS:
        return "0x%06X" % value if 0 <= value <= 0xFFFFFF else str(value)
    enum = OL.ENUM_ARGS.get(name)
    if enum and value in enum[0]:
        return enum[0][value]                      # 比较符 / 值类型写成词
    return str(value)


def _pad(name: str) -> str:
    return f"    {name:<12}"


def _hint_names(op: int, args: dict, names: Names) -> list[str]:
    """把能查到的哈希翻译成路径/台词，用于行尾注释。"""
    if op == 6:
        return _say_hint(args, names)
    got: list[str] = []
    argn = OP.arg_names(op)
    for i in sorted(args):
        arg = args[i]
        if not isinstance(arg, int):
            continue
        role = OL.ROLE_BY_NAME.get(argn[i] if i < len(argn) else "")
        if not role:
            continue
        hint = names.hint(role, arg)
        if hint:
            got.append(_flat(hint) if role == "text" else hint)
    return got


def _say_hint(args: dict, names: Names) -> list[str]:
    """say 的注释：``角色：台词  (配音)``。

    台词是脚本里最该看清的东西，所以不截断，也不套多余的引号。
    """
    who = names.hint("text", args.get(0, 0)) or ""
    line = names.hint("text", args.get(1, 0)) or ""
    if not who and not line:
        return []
    out = f"{who}：{_flat(line)}" if who else _flat(line)
    voice = names.hint("asset", args.get(2, 0))
    if voice:
        out += f"  ({os.path.basename(voice)})"
    return [out]


def fmt_node(node, names: Names, enc: str, allow_unknown: bool) -> str:
    """一个节点数组 -> 一条指令的文本（可能是多行）。"""
    if not isinstance(node, list) or not node or not isinstance(node[0], int):
        return f"    .node {fmt_value(node, enc)}"

    op, raw = node[0], list(node[1:])
    entry = OP.OPCODES.get(op)
    if entry is None:
        if not allow_unknown:
            raise OL.AsmError(
                f"未定义的 opcode {op}（0x{op:02X}）。请补进 vetool/ve_opcodes.py，"
                f"或加 --allow-unknown 以 .node 原样保留。节点内容: {node!r}"
            )
        return f"    .node {fmt_value(node, enc)}"

    mnemonic, argn = entry
    # 尾部嵌套结构（show 的布局、choice 的选项表）不算参数，单独处理
    tail = raw.pop() if op in OL.TAIL_OPS and raw and \
        isinstance(raw[-1], list) else None

    if op == 7 and tail is not None:
        return _fmt_choice(mnemonic, raw, tail, names, enc)

    args = OL.strip_args(op, raw)
    labels, far = _JUMP_ARGS.get(op, {}), _FAR_ARGS.get(op, {})
    fields: list[str] = []
    for i in sorted(args):
        arg = args[i]
        name = argn[i] if i < len(argn) else f"arg{i}"
        if isinstance(arg, int) and i in labels:
            text = _loc(arg)
        elif isinstance(arg, int) and i in far and arg:
            text = _far(arg)
        elif isinstance(arg, int):
            text = _fmt_arg(name, arg)
        else:
            text = fmt_value(arg, enc)
        fields.append(f"{name}={text}")

    hints = _hint_names(op, args, names)
    sub: list[str] = []
    if tail is not None:
        asset = raw[1] if len(raw) > 1 and isinstance(raw[1], int) else -1
        sprite = OL.sprite_decode(tail, asset)
        if sprite is not None and OL.sprite_encode(sprite, asset) == tail:
            fields += _sprite_fields(sprite)
            sub = _sprite_parts(sprite["extra"], names)
        else:
            # 不符合已知槽位模型：原样输出，宁可难看也不能丢字节
            sub = [f"        .layout {fmt_value(tail, enc)}"]

    head = (_pad(mnemonic) + " ".join(fields)).rstrip()
    if hints:
        head += "  ; " + "，".join(hints)
    return "\n".join([head] + sub)


def _fmt_choice(mnemonic: str, raw: list, tail: list,
                names: Names, enc: str) -> str:
    """choice 的选项表：一行一个选项。"""
    flat = all(isinstance(p, list) and len(p) == 2
               and all(isinstance(x, int) for x in p) for p in tail)
    if not flat:
        return f"    {mnemonic}\n        .options {fmt_value(tail, enc)}"
    lines = [_pad(mnemonic).rstrip()]
    for text, target in tail:
        hint = names.hint("text", text)
        note = f"  ; {_flat(hint)}" if hint else ""
        lines.append(f"        .option #{text:08X} -> {_loc(target)}{note}")
    return "\n".join(lines)


def _sprite_fields(f: dict) -> list[str]:
    """精灵布局 -> 主行上的具名字段（缺省值不写）。"""
    out = [f"as={OL.SPRITE_KINDS[f['kind']]}"]
    if f["flags"] != OL.SPRITE_DEFAULT[OL.SPRITE_FLAGS]:
        out.append(f"flags={f['flags']}")
    if f["sheet"]:
        out.append("sheet=" + ",".join(str(x) for x in f["sheet"]))
    if f["clip"]:
        out.append("clip=" + ",".join(str(x) for x in f["clip"]))
    if f["tint"] != OL.SPRITE_TINT:
        out.append("tint=0x%06X" % f["tint"])
    if not f["base"] and f["extra"]:
        out.append("base=none")                    # 部件表首项不是 asset 自身
    if not f["anim"]:
        out.append("anim=none")                    # 只有 86 条这样
    if not f["style"]:
        out.append("style=none")                   # 只有 3 条这样
    return out


def _sprite_parts(parts: list, names: Names) -> list[str]:
    """精灵部件表 -> ``.part`` 子行，一行一个部件。"""
    lines = []
    for item in parts:
        key, at = (item, "") if isinstance(item, int) else (
            item[0], f" at {item[1]},{item[2]}")
        hint = names.hint("asset", key)
        note = f"  ; {hint}" if hint else ""
        lines.append(f"        .part #{key:08X}{at}{note}")
    return lines


def branch_targets(node) -> tuple[int, ...]:
    """一条指令会跳到的本地节点下标（用于决定哪些节点需要 loc_ 标签）。"""
    if not isinstance(node, list) or not node or not isinstance(node[0], int):
        return ()
    op, args = node[0], node[1:]
    out: list[int] = []
    for i in _JUMP_ARGS.get(op, {}):
        if i < len(args) and isinstance(args[i], int):
            out.append(args[i])
    if op == 7 and args and isinstance(args[0], list):
        for pair in args[0]:
            if isinstance(pair, list) and len(pair) >= 2 and isinstance(pair[1], int):
                out.append(pair[1])
    return tuple(out)


# ---------------------------------------------------------------------------
# 值的多行排版
# ---------------------------------------------------------------------------

_WIDTH = 92

# 少于这么多次的重复不压缩，压了反而更长也更难看
_RUN_MIN = 3


def _runs(values) -> list[str]:
    """整数序列 -> 记号列表，连续 >=_RUN_MIN 个相同值写成 ``N*值``。"""
    out: list[str] = []
    i = 0
    n = len(values)
    while i < n:
        j = i + 1
        while j < n and values[j] == values[i]:
            j += 1
        count = j - i
        if count >= _RUN_MIN:
            out.append(f"{count}*{values[i]}")
        else:
            out.extend([str(values[i])] * count)
        i = j
    return out


def emit_value(w: Writer, head: str, value, enc: str, indent: int = 0) -> None:
    """输出 ``<head><值>``；值太长就折成多行。"""
    pad = " " * indent
    if isinstance(value, bytes):
        raise OL.AsmError("字节块必须写成外置 blob")

    flat = fmt_value(value, enc)
    if len(pad) + len(head) + len(flat) <= _WIDTH or not isinstance(
        value, (list, tuple, ve_cbor.CborMap)
    ):
        w.line(pad + head + flat)
        return

    # 纯标量的长数组：连续相同值压成 N*值，再按宽度折行
    if isinstance(value, (list, tuple)) and all(
        isinstance(v, int) and not isinstance(v, bool) for v in value
    ):
        pieces = _runs(value)
        flat2 = pad + head + "[" + " ".join(pieces) + "]"
        if len(flat2) <= _WIDTH:
            w.line(flat2)
            return
        w.line(pad + head + "[")
        inner = pad + "    "
        row = inner
        for piece in pieces:
            if len(row) + len(piece) > _WIDTH and row != inner:
                w.line(row.rstrip())
                row = inner
            row += piece + " "
        if row.strip():
            w.line(row.rstrip())
        w.line(pad + "]")
        return

    if isinstance(value, ve_cbor.CborMap):
        w.line(pad + head + "{")
        for k, v in value.pairs:
            emit_value(w, fmt_value(k, enc) + ": ", v, enc, indent + 4)
        w.line(pad + "}")
        return

    w.line(pad + head + "[")
    for item in value:
        emit_value(w, "", item, enc, indent + 4)
    w.line(pad + "]")


# ---------------------------------------------------------------------------
# 各类载荷的输出布局
#
# 通用规则：顶层映射的每个键写成一条 ``.<键> <值>`` 伪指令，顺序即原键序
# （实测全部载荷的键序已升序，但汇编器仍按 asm 里的出现顺序重建，不依赖排序）。
# ``nodes`` 这类需要特殊排版的键由各布局单独处理。
# ---------------------------------------------------------------------------


def _key_sort(key):
    """汇编器的默认键序，与 assembler._key_sort 一致。"""
    return (isinstance(key, str), key)


def _emit_header(w: Writer, kind: str, enc: str, blobs: list,
                 keys: list | None = None) -> None:
    w.line(f'.payload "{kind}"')
    w.line(f'.encoding "{enc}"')
    if keys is not None and keys != sorted(keys, key=_key_sort):
        # 键序不是默认的升序，必须显式记下来，否则重建的字节序会变
        w.line(".keyorder [" + " ".join(OL.quote(k, enc) for k in keys) + "]")
    if blobs:
        w.line(f"; 外置字节块 {len(blobs)} 个（体积太大，不入文本）")
    w.blank()


def _extract_blobs(d: dict, stem: str) -> list[tuple[str, str, bytes]]:
    """把顶层的 bytes 值挪到外置文件，返回 [(键, 文件名, 数据)]。"""
    out = []
    for key, val in list(d.items()):
        if isinstance(val, (bytes, bytearray)):
            fname = f"{stem}.{key}.bin"
            out.append((key, fname, bytes(val)))
    return out


def dump_generic(d: dict, w: Writer, enc: str, blobs: dict) -> None:
    for key, val in d.items():
        if key in blobs:
            w.line(f'.{key} blob "{blobs[key]}"')
            continue
        emit_value(w, f".{key} ", val, enc)


# 角色表：每个角色一行。前六个是一对一的列，后两个每角色 5 项。
_SPK_1 = OL.SPEAKER_COLS
_SPK_5 = OL.SPEAKER_WIDE
# 这些列是 32 位标识符，写成 #XXXXXXXX
_SPK_HASH = frozenset({"speaker_keys", "speaker_groups", "voice_group_keys",
                       "speaker_colors"})


def _spk_ok(d: dict) -> bool:
    n = len(d.get("speaker_keys") or [])
    if not n:
        return False
    if any(len(d.get(k) or []) != n for k in _SPK_1):
        return False
    if any(len(d.get(k) or []) != n * OL.SPEAKER_PER for k in _SPK_5):
        return False
    return all(all(isinstance(x, int) for x in d[k]) for k in _SPK_1 + _SPK_5)


def _roster_text(keys: list, names: Names) -> str:
    """共享名册文件的内容。一行一个角色键，附名字注释。"""
    out = [f"; 角色名册 {len(keys)} 名。每行一个角色键（FNV-1a32）。",
           "; 附属的 15 列全部可推导：组=语音组=键，颜色恒白，字体/屏/立绘全 0。",
           "; 由 .speakers include \"…\" 引用；表内容有偏差的脚本会自己内联写全。",
           ""]
    for key in keys:
        hint = names.hint("text", key)
        out.append("#%08X%s" % (key, "  ; " + hint if hint else ""))
    return "\n".join(out) + "\n"


def _dump_speakers(d: dict, w: Writer, names: Names,
                   roster: tuple[str, list] | None) -> None:
    """角色表。

    附属的 15 列全部可由 speaker_keys 推出时（本作 140 个脚本都如此），
    名册本身还跨脚本完全相同，就抽到共享文件里只写一行 include；
    否则逐行写出全部列。
    """
    keys = d["speaker_keys"]
    n = len(keys)
    w.blank()
    if OL.speakers_are_default(d):
        # 只有键是真数据，其余 15 列全部可推导
        if roster is not None and list(keys) == roster[1]:
            w.line(f"; 角色名册 {n} 名，与其他脚本相同，抽到共享文件")
            w.line(f'.speakers include "{roster[0]}"')
            return
        w.line(f"; 角色名册 {n} 名（组/语音组=键，颜色恒白，字体/屏/立绘全 0）")
        w.line(".speakers")
        for key in keys:
            hint = names.hint("text", key)
            w.line(f"    #%08X{'  ; ' + hint if hint else ''}" % key)
        w.line(".end")
        return
    w.line(f"; 角色 {n} 名：键 组 语音组 颜色 字体 屏 | 立绘×5 | 语音立绘×5")
    w.line(".speakers")
    per = OL.SPEAKER_PER
    for i in range(n):
        row = " ".join("#%08X" % d[k][i] if k in _SPK_HASH else str(d[k][i])
                       for k in _SPK_1)
        p1 = " ".join(str(x) for x in d[_SPK_5[0]][i * per : (i + 1) * per])
        p2 = " ".join(str(x) for x in d[_SPK_5[1]][i * per : (i + 1) * per])
        hint = names.hint("text", keys[i])
        tail = f"  ; {hint}" if hint else ""
        w.line(f"    {row} | {p1} | {p2}{tail}")
    w.line(".end")


def dump_script(d: dict, w: Writer, enc: str, names: Names,
                allow_unknown: bool,
                roster: tuple[str, list] | None = None) -> None:
    nodes = d.get("nodes") or []

    # 需要标签的节点：本 scene 声明的标签 + 所有跳转落点。
    # 目标可以等于 len(nodes)（跳到场景末尾即结束），故末尾也留一个标签位。
    at: dict[int, list[int]] = {}
    for key, idx in zip(d.get("label_keys") or [], d.get("label_values") or []):
        at.setdefault(idx, []).append(key)
    marked = set(at)
    for node in nodes:
        for target in branch_targets(node):
            marked.add(target)

    spk = _spk_ok(d)
    skip = set(_SPK_1 + _SPK_5) if spk else set()
    # label_values 是节点下标，汇编时按 label_keys 逐个查标签重算，不写死
    skip.add("label_values")
    # label_keys 若正好是 .nodes 里标签的出现顺序，就由标签定义收集，不写。
    # 实测 69/140 个脚本如此；剩下的顺序另有讲究，照原样写出来。
    order = [key for idx in sorted(at) for key in at[idx]]
    if order == list(d.get("label_keys") or ()):
        skip.add("label_keys")
    # 恒为空的数组、以及只有一个取值的标量，汇编时按 SCRIPT_DEFAULTS 补回
    skip.update(k for k, v in OL.SCRIPT_DEFAULTS.items()
                if k in d and d[k] == v)
    for key, val in d.items():
        if key == "nodes" or key in skip:
            continue
        if key == "entry" and isinstance(val, int) and val <= len(nodes):
            w.line(f".entry {_loc(val)}")
            continue
        emit_value(w, f".{key} ", val, enc)
    if spk:
        _dump_speakers(d, w, names, roster)

    w.blank()
    w.line(f"; 指令流：{len(nodes)} 个节点")
    w.line(".nodes")
    for i, node in enumerate(nodes):
        if i in marked:
            w.blank()                              # 标签定义前必须空行
            for key in at.get(i, ()):
                w.line(f"{_lbl(key)}:")
            w.line(f"{_loc(i)}:")
        w.line(fmt_node(node, names, enc, allow_unknown))
    if len(nodes) in marked:
        w.blank()
        for key in at.get(len(nodes), ()):
            w.line(f"{_lbl(key)}:")
        w.line(f"{_loc(len(nodes))}:")              # 场景末尾（跳出即结束）
    w.line(".end")


def dump_strings(d: dict, w: Writer, enc: str) -> None:
    for key, val in d.items():
        if key in ("table", "pieces") and isinstance(val, ve_cbor.CborMap):
            w.blank()
            w.line(f"; {key}：{len(val.pairs)} 条")
            w.line(f".{key}")
            for k, v in val.pairs:
                emit_value(w, f"    {fmt_value(k, enc)}: ", v, enc)
            w.line(".end")
            w.blank()
            continue
        emit_value(w, f".{key} ", val, enc)


def dump_assets(d: dict, w: Writer, enc: str) -> None:
    """资源索引：paths 与三个并行数组按行对齐，其余照通用规则。"""
    paths = d.get("paths") or []
    par = ("path_ids", "sample_counts", "loop_starts", "loop_lengths")
    ok = all(len(d.get(k) or []) == len(paths) for k in par)

    for key, val in d.items():
        if ok and (key == "paths" or key in par):
            continue
        emit_value(w, f".{key} ", val, enc)

    if not ok:
        return
    w.blank()
    w.line(f"; 资源路径 {len(paths)} 条：路径 哈希 采样数 循环起点 循环长度")
    w.line(".paths")
    cols = [d[k] for k in par]
    for i, path in enumerate(paths):
        row = " ".join(str(c[i]) for c in cols)
        w.line(f"    {OL.quote(path, enc)} {row}")
    w.line(".end")


# ---------------------------------------------------------------------------
# 驱动
# ---------------------------------------------------------------------------


def disassemble(payload: bytes, stem: str, out_dir: str, enc: str,
                names: Names, allow_unknown: bool,
                roster: tuple[str, list] | None = None
                ) -> tuple[str, list[str]]:
    """反汇编一个载荷，返回 (asm 文本, 写出的外置 blob 文件名列表)。

    ``roster`` 是 (共享角色名册文件名, 名册键表)；给了就用 include 引用它，
    但只对名册内容与之一致的脚本生效（不一致的照旧内联写全）。
    """
    value = ve_cbor.loads(payload)
    kind = OL.detect_payload(stem)

    import io
    buf = io.StringIO()
    w = Writer(buf)

    if not isinstance(value, ve_cbor.CborMap):
        _emit_header(w, "value", enc, [])
        emit_value(w, ".value ", value, enc)
        return buf.getvalue(), []

    d = {k: v for k, v in value.pairs}
    if len(d) != len(value.pairs):
        raise OL.AsmError(f"{stem}: 顶层映射有重复键，无法用 asm 表示")

    extracted = _extract_blobs(d, stem)
    blob_names = {key: fname for key, fname, _ in extracted}

    _emit_header(w, kind, enc, extracted, list(d))
    if kind == "script":
        dump_script(d, w, enc, names, allow_unknown, roster)
    elif kind == "strings":
        dump_strings(d, w, enc)
    elif kind == "assets":
        dump_assets(d, w, enc)
    else:
        dump_generic(d, w, enc, blob_names)

    written = []
    for _, fname, data in extracted:
        with open(os.path.join(out_dir, fname), "wb") as fh:
            fh.write(data)
        written.append(fname)
    return buf.getvalue(), written


def _collect(inputs: list[str]) -> list[str]:
    files: list[str] = []
    for item in inputs:
        if os.path.isdir(item):
            for name in sorted(os.listdir(item)):
                if name.endswith(".cbor"):
                    files.append(os.path.join(item, name))
        else:
            files.append(item)
    return files


ROSTER_FILE = "speakers.txt"


def _roster_keys(path: str) -> list | None:
    """载荷里那份可推导的角色名册；不是这种形状就返回 None。"""
    try:
        with open(path, "rb") as fh:
            value = ve_cbor.loads(fh.read())
    except (OSError, ve_cbor.CborError):
        return None
    if not isinstance(value, ve_cbor.CborMap):
        return None
    d = {k: v for k, v in value.pairs}
    if "nodes" not in d or not _spk_ok(d) or not OL.speakers_are_default(d):
        return None
    return list(d["speaker_keys"])


def shared_roster(files: list[str], names: Names,
                   out_dir: str) -> tuple[str, list] | None:
    """决定要不要把角色名册抽成共享文件，需要时就写出来。

    两个以上脚本用同一份名册才值得抽（本作 140 个脚本全都一样）。单个脚本
    单独反汇编时，若输出目录里已有匹配的共享文件，也照样引用，这样反复跑
    单文件和跑整个目录得到的 asm 一致。
    """
    rosters = [r for r in (_roster_keys(p) for p in files) if r is not None]
    if not rosters:
        return None
    first = rosters[0]
    if any(r != first for r in rosters[1:]):
        return None                  # 各脚本名册不同，抽出来没意义
    text = _roster_text(first, names)
    dst = os.path.join(out_dir, ROSTER_FILE)
    if len(rosters) < 2:
        # 单文件：只在已有同内容的共享文件时沿用
        try:
            with open(dst, "r", encoding="utf-8") as fh:
                if fh.read() != text:
                    return None
        except OSError:
            return None
        return ROSTER_FILE, first
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return ROSTER_FILE, first


def _guess_pkg_dir(files: list[str]) -> str | None:
    """名字反查表跟载荷同目录时自动装上（只影响注释）。"""
    for path in files:
        d = os.path.dirname(os.path.abspath(path))
        if os.path.isfile(os.path.join(d, "assets.cbor")):
            return d
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="VersaEngine 脚本反汇编器：CBOR -> asm.txt")
    ap.add_argument("inputs", nargs="+", help="输入 .cbor 文件或目录")
    ap.add_argument("-o", "--output",
                    help="输出文件（单输入）或输出目录（多输入）")
    ap.add_argument("--encoding", default=OL.DEFAULT_ENCODING,
                    help=f"asm 文本编码，默认 {OL.DEFAULT_ENCODING}")
    ap.add_argument("--pkg-dir",
                    help="名字反查用的载荷目录，默认自动探测（仅影响注释）")
    ap.add_argument("--allow-unknown", action="store_true",
                    help="遇到未定义 opcode 时写成 .node 而不是报错")
    args = ap.parse_args(argv)

    files = _collect(args.inputs)
    if not files:
        print("错误: 没有输入文件", file=sys.stderr)
        return 2
    for path in files:
        if not os.path.isfile(path):
            print(f"错误: 找不到 {path}", file=sys.stderr)
            return 2

    try:
        "".encode(args.encoding)
    except LookupError:
        print(f"错误: 未知编码 {args.encoding}", file=sys.stderr)
        return 2

    single = len(files) == 1
    if single and args.output and not os.path.isdir(args.output):
        out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    else:
        out_dir = args.output or os.path.dirname(os.path.abspath(files[0]))
    os.makedirs(out_dir, exist_ok=True)

    names = Names(args.pkg_dir or _guess_pkg_dir(files))
    roster = shared_roster(files, names, out_dir)

    fails = 0
    for path in files:
        stem = os.path.basename(path)
        if stem.endswith(".cbor"):
            stem = stem[: -len(".cbor")]
        if single and args.output and not os.path.isdir(args.output):
            dst = args.output
        else:
            dst = os.path.join(out_dir, stem + ".asm.txt")
        try:
            with open(path, "rb") as fh:
                payload = fh.read()
            text, blobs = disassemble(payload, stem, out_dir, args.encoding,
                                      names, args.allow_unknown, roster)
        except (OL.AsmError, ve_cbor.CborError) as exc:
            print(f"[失败] {stem}: {exc}", file=sys.stderr)
            fails += 1
            continue
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        extra = f"（+{len(blobs)} 个字节块）" if blobs else ""
        print(f"[完成] {stem} -> {os.path.basename(dst)}{extra}")

    if roster is not None:
        print(f"[共享] 角色名册 {len(roster[1])} 名 -> {roster[0]}")
    print(f"\n共 {len(files)} 个输入，成功 {len(files) - fails}，失败 {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    code = main()
    # 拖放运行时窗口会立刻关掉，留一下让人看见结果
    if len(sys.argv) > 1 and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\n按回车关闭…")
        except EOFError:
            pass
    sys.exit(code)
