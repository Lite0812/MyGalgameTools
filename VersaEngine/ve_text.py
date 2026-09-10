"""把 _packages 里的 CBOR 载荷转成可读文本。

六类载荷各有专门的输出格式：

    *.script  -> .txt   剧本，opcode 还原成语句，哈希还原成台词/资源路径
    *.strings -> .txt   字符串表，hash: 文本
    assets    -> .txt   资源索引，key -> 路径
    project   -> .txt   工程配置
    *.font    -> .txt   字形度量摘要（不含像素）
    ui        -> .txt   控件表

所有标识符都是 FNV-1a32 哈希。可还原的来源有三处：
  - 台词/人名/选项  -> strings 的 table / pieces
  - 资源            -> assets 的 keys→values→paths
  - 标签            -> script 自身的 label_keys
无法还原的（图层名、频道名、flag 名）在编译期就只剩哈希，输出为
`#<hash>` 原样保留。
"""

from __future__ import annotations

import os
from typing import Any

import ve_cbor
import ve_opcodes as OP
from ve_crypto import fnv1a32


# 反查候选词：引擎只存哈希，这些名字靠猜+验证还原
_KNOWN_NAMES = (
    # 音频频道（本作 'music' 已确证命中）
    "music", "sound", "voice", "ambient", "se", "bgm", "sysse", "master",
    "system", "ui", "effect",
    # 过渡规则（本作 'dissolve' 已确证命中）
    "dissolve", "crossfade", "wipe", "cut", "instant", "blend",
    # UI 屏（ui.cbor 的 17 个 key 里已确证命中 13 个）
    "title", "config", "save", "load", "extra", "extra_gallery",
    "history", "choice", "confirm", "quickload", "quicksave", "hud",
    # 图层 / 画面（未命中，留着以便别的作品）
    "bg", "fg", "cg", "ev", "base", "stage", "layer", "overlay",
    "character", "sprite", "face", "window", "message", "text",
    "screen", "choice", "prompt", "dialogue",
    "camera", "light", "particle", "movie",
    "flag", "var", "story", "common",
)


class Resolver:
    """把哈希还原成人类可读的名字。"""

    def __init__(self, pkg_dir: str, language: str | None = None):
        self.pkg_dir = pkg_dir
        self.table: dict[int, str] = {}
        self.pieces: dict[int, list] = {}
        self.assets: dict[int, str] = {}
        self.words: dict[int, str] = {}
        # 全局标签表：标签哈希 -> (scene 名, 节点下标)
        self.labels: dict[int, tuple[str, int]] = {}

        self._load_assets()
        self._load_strings(language)
        self._load_names()
        self._load_labels()

    # -- 加载 ---------------------------------------------------------------

    def _load(self, name: str) -> dict | None:
        path = os.path.join(self.pkg_dir, name)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            value = ve_cbor.loads(fh.read())
        if not isinstance(value, ve_cbor.CborMap):
            return None
        return {k: v for k, v in value.pairs}

    def _load_assets(self) -> None:
        d = self._load("assets.cbor")
        if not d:
            return
        paths = d.get("paths") or []
        keys = d.get("keys") or []
        values = d.get("values") or []
        for key, val in zip(keys, values):
            # values[i] = [路径下标, 种类]
            if isinstance(val, list) and val and 0 <= val[0] < len(paths):
                self.assets[key] = paths[val[0]]
        # 路径本身也参与哈希（op15 的过渡规则直接用路径哈希）
        for path in paths:
            self.words[fnv1a32(path.encode("utf-8"))] = path

    def _load_strings(self, language: str | None) -> None:
        names = []
        if language:
            names.append(f"{language}.strings.cbor")
        # 全部语言都装上：脚本可能引用任一语言的条目
        for name in sorted(os.listdir(self.pkg_dir)):
            if name.endswith(".strings.cbor") and name not in names:
                names.append(name)
        for name in names:
            d = self._load(name)
            if not d:
                continue
            tab = d.get("table")
            if isinstance(tab, ve_cbor.CborMap):
                for k, v in tab.pairs:
                    self.table.setdefault(k, v)
            pieces = d.get("pieces")
            if isinstance(pieces, ve_cbor.CborMap):
                for k, v in pieces.pairs:
                    self.pieces.setdefault(k, v)

    def _load_names(self) -> None:
        """还原编译期丢掉名字的标识符。

        图层名、频道名、flag 名在打包时只剩 FNV-1a32，引擎运行时也只比哈希
        （find_character 缺失即新建），所以原名不在任何数据里。这里用小候选
        集反查，命中即确证（32 位碰撞概率极低）；查不到的保留 #hash。

        实测本作只有音频频道 'music' 能反查出来，图层名穷举 4 字符 ASCII
        与日文常用词都无命中 —— 那些名字确实只剩哈希了。
        """
        for word in _KNOWN_NAMES:
            self.words.setdefault(fnv1a32(word.encode("utf-8")), word)

        # project.cbor 里的场景名与语言标签是明文，一并纳入
        d = self._load("project.cbor")
        if not d:
            return
        for key in ("scenes", "languages"):
            for item in d.get(key) or []:
                if isinstance(item, str):
                    self.words.setdefault(fnv1a32(item.encode("utf-8")), item)

    def _load_labels(self) -> None:
        """project 的全局标签表，跨 scene 跳转靠它落地。

        label_scenes 存的是 scene 名的 FNV-1a32，不是下标；实测 140 个场景
        全部反查得到（ve::Game::resolve_destination 也是这样比对的）。
        """
        d = self._load("project.cbor")
        if not d:
            return
        by_hash = {fnv1a32(s.encode("utf-8")): s
                   for s in d.get("scenes") or [] if isinstance(s, str)}
        for key, scene, node in zip(d.get("label_keys") or [],
                                    d.get("label_scenes") or [],
                                    d.get("label_nodes") or []):
            self.labels[key] = (by_hash.get(scene, f"#{scene}"), node)

    # -- 查询 ---------------------------------------------------------------

    def text(self, key: int) -> str | None:
        """还原一条文本，pieces 里的变量引用写成 {名字}。

        table 里的空串表示该条整体由变量拼成（如玩家自取的主角名），真正的
        内容在 pieces 里，所以空串要继续往下查。
        """
        if self.table.get(key):
            return self.table[key]
        if key in self.pieces:
            out = []
            for seg in self.pieces[key]:
                if not isinstance(seg, list) or len(seg) < 2:
                    continue
                if seg[0] == 0:
                    out.append(str(seg[1]))
                else:
                    out.append("{%s}" % self.var_name(seg[1]))
            return "".join(out)
        return self.table.get(key)      # 空串也算已还原

    def var_name(self, key: int) -> str:
        """文本内插引用的变量名，还原不了写成 var_XXXXXXXX。"""
        got = self.words.get(key)
        return got if got else f"var_{key:08X}"

    def asset(self, key: int) -> str | None:
        return self.assets.get(key)

    def label(self, key: int) -> str:
        """任意标识符 -> 可读名，还原不了就 #hash。"""
        if key == 0:
            return "0"
        for src in (self.assets, self.words):
            if key in src:
                return src[key]
        if key in self.table:
            return _q(self.table[key])
        return f"#{key}"

    def label_name(self, key: int) -> str:
        """标签哈希 -> 标签名。原名编译期已丢，退化成 label_<hash>。"""
        got = self.words.get(key) or self.table.get(key)
        return got if isinstance(got, str) and got.isidentifier() else f"label_{key:08X}"

    def scene_of(self, key: int) -> tuple[str, int] | None:
        """跨 scene 标签 -> (scene 名, 节点下标)，来自 project 的全局标签表。"""
        return self.labels.get(key)


# ---------------------------------------------------------------------------
# 剧本
# ---------------------------------------------------------------------------


def _oneline(s: str) -> str:
    r"""按 CLAUDE.md 的语义化规则把文本压成一行。

    仅 ``\n`` 与 ``\\`` 有转义含义：引擎的换行标记写作 ``\n``，真实反斜杠写作
    ``\\``。其余不可打印字节、CR/LF 以及私用区字符一律写成 ``{{XX}}`` 占位符，
    杜绝乱码；全角空格等可正常显示的字符保持原样。
    """
    out = []
    for ch in s:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")          # 引擎换行标记
        elif ch == "\r":
            out.append("{{0D}}")
        elif code < 0x20 or code == 0x7F:
            out.append("{{%02X}}" % code)
        elif 0xE000 <= code <= 0xF8FF or 0xF0000 <= code <= 0x10FFFD:
            # 私用区：按 UTF-8 原始字节成组输出
            out.append("".join("{{%02X}}" % b for b in ch.encode("utf-8")))
        else:
            out.append(ch)
    return "".join(out)


def _q(s: str) -> str:
    """台词转成单行带引号形式（内部双引号照 asm 惯例保持原样）。"""
    return '"' + _oneline(s) + '"'


def dump_script(payload: bytes, res: Resolver, out) -> None:
    value = ve_cbor.loads(payload)
    d = {k: v for k, v in value.pairs}

    name = d.get("name", "?")
    lang = d.get("language", "?")
    nodes = d.get("nodes") or []

    # 本 scene 自己声明的标签：节点下标 -> 标签名
    at_label: dict[int, list[str]] = {}
    for key, idx in zip(d.get("label_keys") or [], d.get("label_values") or []):
        at_label.setdefault(idx, []).append(res.label_name(key))
    # 跳转落点也要有名字，否则只能引用裸下标
    for node in nodes:
        for idx in _branch_targets(node):
            if 0 <= idx < len(nodes):
                at_label.setdefault(idx, [])

    print(f"; scene {name}，语言 {lang}，共 {len(nodes)} 个节点", file=out)
    print(f"; version={d.get('version')}  entry={_loc(d.get('entry') or 0)}", file=out)

    speakers = d.get("speaker_keys") or []
    if speakers:
        print(f"\n; 出场角色 {len(speakers)} 名", file=out)
        for key in speakers:
            nm = res.text(key)
            if nm:
                print(f";   {_oneline(nm)}", file=out)

    _dump_decls(d, res, out)

    for i, node in enumerate(nodes):
        names = at_label.get(i)
        if names is not None:
            print(file=out)                     # 标签定义前必须空行
            for nm in names:                    # 具名标签（可能多个别名）
                print(f"{nm}:", file=out)
            print(f"{_loc(i)}:", file=out)
        print("    " + _fmt_node(node, res), file=out)


def _loc(index: int) -> str:
    """节点下标 -> 符号化标签，格式同 CLAUDE.md 的 loc_XXXXXXXX。"""
    return f"loc_{index:08X}"


def _branch_targets(node: Any) -> tuple[int, ...]:
    """一条指令会跳到的所有本地节点下标。"""
    if not isinstance(node, list) or not node:
        return ()
    op, args = node[0], node[1:]
    if op == 1:                                  # jmp node [, label]
        return (args[0],) if args and isinstance(args[0], int) else ()
    if op == 27:                                 # 条件不成立时跳到 args[4]
        return (args[4],) if len(args) > 4 and isinstance(args[4], int) else ()
    if op == 41:                                 # call node
        return (args[0],) if args and isinstance(args[0], int) else ()
    if op == 7:                                  # choice: [[文本, 落点], ...]
        out = []
        for pair in (args[0] if args and isinstance(args[0], list) else ()):
            if isinstance(pair, list) and len(pair) >= 2 and isinstance(pair[1], int):
                out.append(pair[1])
        return tuple(out)
    return ()


def _dump_decls(d: dict, res: Resolver, out) -> None:
    for prefix, title in (("const", "常量"), ("var", "变量")):
        keys = d.get(f"{prefix}_keys") or []
        kinds = d.get(f"{prefix}_kinds") or []
        vals = d.get(f"{prefix}_values") or []
        if not keys:
            continue
        print(f"\n; {title} {len(keys)} 个", file=out)
        for key, kind, val in zip(keys, kinds, vals):
            kn = OP.VALUE_KINDS.get(kind, kind)
            print(f";   {res.label(key)} : {kn} = {val}", file=out)


def _fmt_node(node: Any, res: Resolver) -> str:
    if not isinstance(node, list) or not node:
        return f"; <畸形节点> {node!r}"

    op = node[0]
    args = node[1:]
    name = OP.name_of(op)

    # 特事特办的几个高频 opcode
    if op == 6:
        return _fmt_say(args, res)
    if op == 4:
        return _fmt_show(args, res)
    if op == 27:
        return _fmt_jump_if(name, args, res)
    if op == 1:
        return _fmt_jmp(args, res)
    if op == 7:
        return _fmt_choice(args, res)
    if op == 41:
        return f"{_pad(name)}node={_loc(args[0])}" if args else name
    if op == 30:
        return _fmt_set(args, res)
    if op == 55:
        return _fmt_set_random(args, res)

    parts = []
    names = OP.arg_names(op)
    for i, a in enumerate(args):
        label = names[i] if i < len(names) else f"arg{i}"
        if OP.is_default(op, label, a):
            continue                     # 全语料恒定，省略
        parts.append(f"{label}={_fmt_arg(label, a, res)}")
    return f"{_pad(name)}{' '.join(parts)}".rstrip()


def _pad(mnemonic: str, width: int = 12) -> str:
    """助记符与操作数之间留出固定列宽，对齐好读。"""
    return mnemonic.ljust(width)


def _fmt_arg(label: str, value: Any, res: Resolver) -> str:
    if isinstance(value, list):
        return _fmt_nested(value, res)
    if not isinstance(value, int):
        return repr(value)
    if value == 0:
        return "0"
    if label in ("asset", "rule"):
        # rule 可能是资源路径，也可能是内置规则名（如 dissolve）
        return res.asset(value) or res.label(value)
    if label in ("text", "speaker"):
        got = res.text(value)
        return _q(got) if got else f"#{value}"
    if label == "color":
        return f"0x{value:06X}"
    if label == "node":
        return _loc(value)
    if label == "flag":
        return _flag(value, res)
    if label == "var":
        return res.var_name(value)
    if label == "layer":
        return _layer(value, res)
    if label == "screen":
        got = res.words.get(value)
        return got if got else f"screen_{value:08X}"
    if label in ("channel", "key"):
        return res.label(value)
    return str(value)


def _fmt_nested(value: list, res: Resolver, depth: int = 0) -> str:
    """op4/op15 尾部的嵌套结构，原样打印但把能认出的资源换成路径。"""
    out = []
    for item in value:
        if isinstance(item, list):
            out.append(_fmt_nested(item, res, depth + 1))
        elif isinstance(item, int) and item > 0xFFFF:
            got = res.asset(item)
            out.append(got if got else str(item))
        else:
            out.append(_q(item) if isinstance(item, str) else str(item))
    return "[" + ", ".join(out) + "]"


def _fmt_say(args: list, res: Resolver) -> str:
    speaker = args[0] if len(args) > 0 else 0
    text = args[1] if len(args) > 1 else 0
    voice = args[2] if len(args) > 2 else 0
    line = args[3] if len(args) > 3 else 0

    who = res.text(speaker) if speaker else None
    body = res.text(text)
    fields = []
    if who:
        fields.append(f"who={_q(who)}")
    elif speaker:
        fields.append(f"who=#{speaker}")        # 有说话者但名字查不到
    fields.append("text=" + (_q(body) if body is not None else f"#{text}"))
    if voice:
        fields.append("voice=" + (res.asset(voice) or f"#{voice}"))
    if line:
        # ReadLog::seen/mark 的已读标记键，不可由文本推导，原样保留
        fields.append(f"id={line:08X}")
    return f"{_pad('say')}{' '.join(fields)}"


# 精灵类型（op4 尾部嵌套数组的第 0 槽）
SPRITE_KINDS = {0: "bg", 1: "frames", 3: "layered"}

# 尾部嵌套数组前段的槽位名。省略的槽位在全部 10172 条 show 里恒为同一个值
# （4=[]、6..9=0、10=1000、12=1000、13=0），没有信息量，不输出。
# slot1 由 slot0 唯一决定（layered→0，bg/frames→12000），同样省略。
# 数组总长有 16/15/14 三种，style 是尾部那个 6 元整数组，不能按固定下标取。
_LAYOUT_SLOTS = {0: "kind", 2: "flags", 3: "parts", 5: "frames", 11: "clip"}


def _fmt_show(args: list, res: Resolver) -> str:
    """op4：立绘/背景。

    统一 key=value；尾部嵌套数组按槽位拆成命名字段，恒定默认值不输出。
    部件多于一个时 parts 单独换行，一行一个差分图。
    """
    names = OP.arg_names(4)
    fields = []
    nested = None
    for i, a in enumerate(args):
        if isinstance(a, list):
            nested = a
            continue
        label = names[i] if i < len(names) else f"arg{i}"
        if i == 0:
            fields.append(f"layer={_layer(a, res)}")
        elif i == 1:
            fields.append(f"asset={_fmt_arg('asset', a, res)}")
        elif a and not OP.is_default(4, label, a):
            fields.append(f"{label}={a}")

    parts_lines: list[str] = []
    if nested:
        for slot, name in sorted(_LAYOUT_SLOTS.items()):
            if slot >= len(nested):
                continue
            val = nested[slot]
            if name == "kind":
                fields.append(f"kind={SPRITE_KINDS.get(val, val)}")
            elif name == "parts":
                parts_lines = _fmt_parts(val, res)
            elif name == "clip" and val:
                # 裁剪矩形 x,y,w,h
                if isinstance(val, list) and all(isinstance(v, int) for v in val):
                    fields.append("clip=" + ",".join(str(v) for v in val))
                else:
                    fields.append("clip=" + _fmt_nested(val, res))
            elif name == "frames" and val:
                fields.append("frames=" + _fmt_nested(val, res))
            elif name == "flags" and val != 17:
                fields.append(f"flags={val}")
        got = _fmt_style(nested)
        if got:
            fields.append(got)

    out = f"{_pad('show')}{' '.join(fields)}"
    for line in parts_lines:
        out += f"\n        {line}"
    return out


def _fmt_parts(parts: list, res: Resolver) -> list[str]:
    """图层部件：首项是基底图，其余是 [差分图, x, y]。"""
    lines = []
    for item in parts:
        if isinstance(item, list) and item:
            path = res.asset(item[0]) or f"#{item[0]}"
            fields = [f"asset={path}"]
            xy = [str(v) for v in item[1:] if v]
            if xy:
                fields.append("at=" + ",".join(str(v) for v in item[1:]))
            lines.append(".part " + " ".join(fields))
        elif isinstance(item, int):
            lines.append(f".part asset={res.asset(item) or f'#{item}'}")
    return lines


def _fmt_style(nested: list) -> str:
    """调色：布局数组的最后一项是 6 元整数组，其第 3 位是 RGB。

    数组总长有 16/15/14 三种，所以按尾部取而不是按固定下标。
    16777215（白）表示不着色。
    """
    if not nested:
        return ""
    style = nested[-1]
    if not isinstance(style, list) or len(style) != 6:
        return ""
    if not all(isinstance(v, int) for v in style):
        return ""
    tint = style[3]
    return "" if tint == 0xFFFFFF else f"tint=0x{tint:06X}"


def _layer(key: int, res: Resolver) -> str:
    """图层名编译期已丢，统一写成 layer_XXXXXXXX。"""
    got = res.words.get(key)
    return got if got else f"layer_{key:08X}"


def _flag(key: int, res: Resolver) -> str:
    """flag 键 -> 可读名。编译期只剩哈希，统一写成 flag_XXXXXXXX。"""
    got = res.words.get(key)
    return got if got else f"flag_{key:08X}"


def _kind_value(kind: int, value: int) -> str:
    """按 ValueKind 还原右值的可读形式。"""
    if kind == 1:                       # 定点：存值 / 1000
        return f"{value / 1000:g}"
    if kind == 3:                       # 引用：右值本身是另一个 flag 键
        return f"[flag_{value:08X}]"
    return str(value)


def _fmt_jump_if(name: str, args: list, res: Resolver) -> str:
    flag = args[0] if args else 0
    cmp_op = args[1] if len(args) > 1 else 0
    kind = args[2] if len(args) > 2 else 0
    rhs = args[3] if len(args) > 3 else 0
    node = args[4] if len(args) > 4 else None
    sym = OP.COMPARE_OPS.get(cmp_op, f"?{cmp_op}")
    cond = f"({_flag(flag, res)} {sym} {_kind_value(kind, rhs)})"
    tail = f" node={_loc(node)}" if node is not None else ""
    return f"{_pad(name)}cond={cond}{tail}"


def _fmt_jmp(args: list, res: Resolver) -> str:
    """op1：本地跳转；带第二参时是跨 scene 标签跳转。"""
    node = args[0] if args else 0
    if len(args) > 1 and args[1]:
        far = res.scene_of(args[1])
        if far:
            scene, idx = far
            return f"{_pad('jmp_far')}node={scene}:{_loc(idx)}"
        return f"{_pad('jmp_far')}node=label_{args[1]:08X}"
    return f"{_pad('jmp')}node={_loc(node)}"


def _fmt_set(args: list, res: Resolver) -> str:
    flag = args[0] if args else 0
    kind = args[1] if len(args) > 1 else 0
    value = args[2] if len(args) > 2 else 0
    return f"{_pad('set')}{_flag(flag, res)} = {_kind_value(kind, value)}"


def _fmt_set_random(args: list, res: Resolver) -> str:
    """op55：flag = random(lo, hi)。"""
    flag = args[0] if args else 0
    lo = args[1] if len(args) > 1 else 0
    hi = args[2] if len(args) > 2 else 0
    return f"{_pad('set_random')}{_flag(flag, res)} = random({lo}, {hi})"


def _fmt_choice(args: list, res: Resolver) -> str:
    """op7：选项表 [[文本哈希, 落点], ...]，一行一个选项。"""
    pairs = args[0] if args and isinstance(args[0], list) else []
    lines = [f"{_pad('choice')}{len(pairs)}"]
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) < 2:
            continue
        body = res.text(pair[0])
        text = _q(body) if body is not None else f"#{pair[0]}"
        lines.append(f"        .option text={text} node={_loc(pair[1])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 其它载荷
# ---------------------------------------------------------------------------


def dump_strings(payload: bytes, out) -> None:
    d = {k: v for k, v in ve_cbor.loads(payload).pairs}
    print(f"# strings {d.get('name')}  language={d.get('language')}", file=out)
    print(f"# count={d.get('count')}  version={d.get('version')}", file=out)

    tab = d.get("table")
    if isinstance(tab, ve_cbor.CborMap):
        print(f"\n[table] {len(tab.pairs)} 条", file=out)
        for k, v in tab.pairs:
            print(f"{k:>12}  {_oneline(str(v))}", file=out)

    pieces = d.get("pieces")
    if isinstance(pieces, ve_cbor.CborMap):
        print(f"\n[pieces] {len(pieces.pairs)} 条（含变量内插）", file=out)
        for k, segs in pieces.pairs:
            parts = []
            for seg in segs:
                if isinstance(seg, list) and len(seg) >= 2:
                    parts.append(
                        _oneline(str(seg[1])) if seg[0] == 0 else "{#%d}" % seg[1]
                    )
            print(f"{k:>12}  {''.join(parts)}", file=out)

    kin = d.get("kinsoku")
    if isinstance(kin, ve_cbor.CborMap):
        print("\n[kinsoku] 禁则处理字符集", file=out)
        for k, v in kin.pairs:
            chars = "".join(chr(c) for c in v) if isinstance(v, list) else str(v)
            print(f"  {k:<8} {chars}", file=out)


def dump_assets(payload: bytes, out) -> None:
    d = {k: v for k, v in ve_cbor.loads(payload).pairs}
    paths = d.get("paths") or []
    ids = d.get("path_ids") or []
    keys = d.get("keys") or []
    values = d.get("values") or []
    starts = d.get("loop_starts") or []
    lengths = d.get("loop_lengths") or []
    samples = d.get("sample_counts") or []

    print(f"# assets  version={d.get('version')}  "
          f"default_language={d.get('default_language')}", file=out)
    print(f"# paths={len(paths)}  keys={len(keys)}", file=out)

    print(f"\n[paths] {len(paths)} 条  (下标  FNV-1a32  路径)", file=out)
    for i, (path, pid) in enumerate(zip(paths, ids)):
        print(f"{i:>6} {pid:>12}  {path}", file=out)

    print(f"\n[keys] {len(keys)} 条  (逻辑键 -> 路径, 种类)", file=out)
    for i, (key, val) in enumerate(zip(keys, values)):
        idx, kind = (val + [0, 0])[:2] if isinstance(val, list) else (val, 0)
        path = paths[idx] if 0 <= idx < len(paths) else "?"
        extra = ""
        if i < len(samples) and samples[i]:
            extra = f"  samples={samples[i]}"
            if i < len(starts) and (starts[i] or lengths[i]):
                extra += f" loop={starts[i]}+{lengths[i]}"
        print(f"{key:>12}  kind={kind}  {path}{extra}", file=out)


def dump_project(payload: bytes, res: Resolver, out) -> None:
    value = ve_cbor.loads(payload)
    print("# project", file=out)
    for k, v in value.pairs:
        print(f"\n[{k}]", file=out)
        _dump_value(v, res, out, indent="  ")


def dump_font(payload: bytes, out) -> None:
    d = {k: v for k, v in ve_cbor.loads(payload).pairs}
    print("# font atlas", file=out)
    for k, v in d.items():
        if isinstance(v, bytes):
            print(f"{k:<16} <{len(v)} 字节>", file=out)
        elif isinstance(v, list):
            print(f"{k:<16} list[{len(v)}]", file=out)
        else:
            print(f"{k:<16} {v}", file=out)
    cps = d.get("codepoints") or []
    if cps:
        print(f"\n[codepoints] {len(cps)} 个字形", file=out)
        line = []
        for cp in cps:
            if isinstance(cp, int) and 0x20 <= cp <= 0x10FFFF:
                line.append(f"{cp:>6}:{chr(cp)}")
            if len(line) == 8:
                print("  " + " ".join(line), file=out)
                line = []
        if line:
            print("  " + " ".join(line), file=out)


def dump_generic(payload: bytes, res: Resolver, out) -> None:
    value = ve_cbor.loads(payload)
    # ui 载荷的 keys 是屏名哈希，能反查的先列成对照表
    if isinstance(value, ve_cbor.CborMap):
        d = {k: v for k, v in value.pairs}
        keys = d.get("keys")
        if isinstance(keys, list) and keys:
            print(f"; 屏 {len(keys)} 个（下标  名字）", file=out)
            for i, key in enumerate(keys):
                nm = res.words.get(key) or f"screen_{key:08X}"
                print(f";   {i:>3}  {nm}", file=out)
            print(file=out)
    _dump_value(value, res, out)


def _dump_value(value: Any, res: Resolver, out, indent: str = "") -> None:
    if isinstance(value, ve_cbor.CborMap):
        for k, v in value.pairs:
            if isinstance(v, (ve_cbor.CborMap, list)) and v:
                print(f"{indent}{k}:", file=out)
                _dump_value(v, res, out, indent + "  ")
            else:
                print(f"{indent}{k}: {_scalar(v)}", file=out)
    elif isinstance(value, list):
        # 全标量的短列表压成一行
        if all(not isinstance(x, (list, ve_cbor.CborMap)) for x in value):
            print(f"{indent}{_row(value)}", file=out)
        else:
            for i, item in enumerate(value):
                if isinstance(item, (ve_cbor.CborMap, list)):
                    print(f"{indent}[{i}]", file=out)
                    _dump_value(item, res, out, indent + "  ")
                else:
                    print(f"{indent}[{i}] {_scalar(item)}", file=out)
    else:
        print(f"{indent}{_scalar(value)}", file=out)


def _scalar(v: Any) -> str:
    if isinstance(v, bytes):
        return f"<{len(v)} 字节>"
    if isinstance(v, str):
        return _q(v)
    return str(v)


def _row(values: list, limit: int = 24) -> str:
    shown = [_scalar(v) for v in values[:limit]]
    tail = f" ...(共 {len(values)})" if len(values) > limit else ""
    return "[" + ", ".join(shown) + "]" + tail
