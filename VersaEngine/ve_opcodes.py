"""脚本 opcode 表。

取自 ve::Game::apply (0x2DBE48, GameScript.cpp) 的 53 路 switch，以及
ve::Vm::fetch (0x20C9D0, Vm.cpp) 处理的控制流 opcode。名字按各 case
实际调用的引擎函数命名。

Command 结构（ve::Vm::decode @ 0x20C1B0 填充）::

    +0x00 u8  opcode          节点数组首元素
    +0x04 u32 node_index
    +0x08 u8  argc            最多 8 个
    +0x0C .. +0x2C  i32 args[8]
    +0x30 cbor::Value         整个节点，用于取尾部的嵌套结构

decode 遇到 array/map 类型的参数就停止收集整数参数，故 op4/op15 第 9 个
参数（嵌套数组）不计入 argc，由 handler 自己从 +0x30 取。
"""

from __future__ import annotations

# opcode -> (名字, 参数名列表)
# 参数名取自 handler 的使用方式；None 表示语义未确认
OPCODES: dict[int, tuple[str, tuple[str, ...]]] = {
    # 0x00/0x1D：Vm::fetch 里只把 PC+1 就继续循环，既不派发也不产生动作。
    # 见 20CABC（CBZ W8 → loc_20CA48）与 20CA40（CMP #0x1D → loc_20CA48）。
    0:  ("nop",              ()),
    1:  ("jmp",              ("node", "label")),
    2:  ("stop_all",         ()),
    4:  ("show",             ("layer", "asset", "x", "y", "z",
                              "alpha", "flip", "variant")),
    # 0x05：arg0 是 find_character 的角色/图层键，最多再收 5 个整数
    # （fade_ms, mode, x, y）。语料只出现 2 参形式。
    5:  ("hide",             ("layer", "fade_ms", "mode", "x", "y")),
    6:  ("say",              ("speaker", "text", "voice", "line_id")),
    7:  ("choice",           ("count",)),
    8:  ("play_sound",       ("channel", "flags", "asset",
                              "volume", "fade_ms", "pitch", "loop")),
    9:  ("stop_sound",       ("channel", "fade_ms")),
    10: ("start_video",      ("asset",)),
    # 0x0B：ms 为负时改为"等待点击"（置 Game+5788/5734），否则按毫秒计时
    11: ("wait",             ("ms",)),
    12: ("fade_in",          ("ms", "color")),
    13: ("fade_out",         ("ms", "color")),
    14: ("camera_shake",     ("ms", "amplitude")),
    15: ("transition",       ("rule", "ms", "a", "b", "mode", "c", "d")),
    16: ("particles",        ("key", "rate")),
    17: ("set_font",         ("asset", "size")),
    # 0x12：写 Game+2612 的等待秒数（auto 模式每行停留时长）
    18: ("set_auto_delay",   ("ms",)),
    # 0x13/0x14：Game+6183 = auto 标志，+6184 = skip 标志
    19: ("set_auto",         ("on",)),
    20: ("set_skip",         ("on",)),
    21: ("set_nvl",          ("on",)),
    23: ("camera_zoom",      ("scale", "ms")),
    24: ("camera_pan",       ("x", "y", "ms")),
    25: ("camera_focus",     ("layer", "ms")),
    26: ("camera_reset",     ("ms",)),
    # 0x1B：条件成立则顺序落到下一节点，不成立才跳到 args[4]。
    # 见 20CF48-20CF74：六个比较分支全部 B.<cc> loc_20CA48（= PC+1），
    # 失败统一落到 loc_20CF74（= PC 取 args[4]）。故语义是 "不满足则跳"。
    27: ("jmp_if_not",       ("flag", "op", "kind", "rhs", "node")),
    # 0x1E：写 flag（Vm::set_flag(key, kind, value)）
    30: ("set",              ("flag", "kind", "value")),
    # 0x1D：同 0x00，纯占位（编译器留下的空槽），Vm::fetch 直接 PC+1
    29: ("nop_alt",          ()),
    # 0x1F/0x20：Game+6188 门控消息窗（sync_pinned 读它决定是否显示）
    31: ("hide_window",      ()),
    32: ("show_window",      ()),
    34: ("open_screen",      ("screen",)),
    35: ("close_screen",     ("all",)),
    36: ("wait_screen",      ()),
    # 0x25：arg0 经 find_character 定位角色，滤镜级联在尾部嵌套数组里
    # （read_filter_stages 跳过前 2 项后读取），末尾整数是时长。
    37: ("filter",           ("layer", "ms")),
    # 0x27：弹出文本输入框。arg0 是提示语，arg1 是接收结果的字符串变量，
    # arg2 是最大字数（0 按 32 处理）。见 ScreenView::set_dialog_input/set_prompt
    39: ("input_text",       ("text", "var", "max_len")),
    # 0x28：写 Game+7024/7028 目标偏移，visual_activity 按参考分辨率钳制后驱动画面
    40: ("set_offset",       ("x", "y")),
    # 0x29：压入返回帧（当前 scene hash + 返回节点），0x2A 弹出
    41: ("call",             ("node",)),
    42: ("ret",              ()),
    43: ("unlock_extras",    ()),
    # 0x2C：Dialogue::set_face，五个整数是立绘框的图与裁剪参数
    44: ("dialogue_face",    ("asset", "x", "y", "w", "h")),
    45: ("tint",             ("layer", "color", "ms")),
    46: ("camera_wait",      ()),
    47: ("offer_inspect",    ("flag",)),
    48: ("auto_save",        ()),
    49: ("unlock_gallery",   ("key",)),
    # 0x32：写 Game+2644/2648（震动幅度与时长），受 skip 与设置项 39 影响
    50: ("stage_shake",      ("ms", "amplitude", "hold")),
    # 0x33：翻页；参数非 0 表示保留当前页（不调 Dialogue::clear_page）
    51: ("clear_page",       ("keep", "ms")),
    52: ("camera_finish",    ()),
    # 0x35：写 Game+6268 的 say 屏 id（active_say_screen 读它）
    53: ("set_say_screen",   ("screen",)),
    54: ("wait_audio",       ("channel",)),
    # 0x37：draw_random(lo, hi, kind) 后写入 flag（Vm::fetch 0x20CB3C）
    55: ("set_random",       ("flag", "lo", "hi", "kind")),
}

# 控制流指令：由 ve::Vm::fetch 直接执行，不进 Game::apply
CONTROL_FLOW = frozenset({0, 1, 27, 29, 30, 41, 42, 55})

# 缺省值：这些参数在全部 140 个脚本里恒为同一个值，写出来只是噪声。
# 文本视图省略它们，需要原值时看 CBOR。键是 opcode，值是 {参数名: 缺省值}。
ARG_DEFAULTS: dict[int, dict[str, int]] = {
    4:  {"alpha": 255, "flip": 0, "variant": 0},
    5:  {"fade_ms": 0},
    8:  {"flags": 0, "volume": 1000, "fade_ms": 1000, "pitch": 1000, "loop": 0},
    12: {"color": 0xFFFFFF},                    # 白 = 不着色
    13: {"color": 0xFFFFFF},
    15: {"a": 0, "b": 0, "c": 0, "d": 0, "mode": 0},
}


def is_default(op: int, name: str, value) -> bool:
    """该参数是否取缺省值（可以从文本视图省略）。"""
    d = ARG_DEFAULTS.get(op)
    return bool(d) and name in d and d[name] == value

# 比较运算符 (Vm::fetch 0x20CD30-0x20CD9C, Vm::test 0x20C338)
COMPARE_OPS = {
    0: "==",
    1: "!=",
    2: "<",
    3: "<=",
    4: ">",
    5: ">=",
}

# ve::ValueKind（PersistentStore::set / Vm::set_flag 的第二参）
#   0 整数（含 0/1 布尔）
#   1 定点数，实际值 = 存值 / 1000（set_random 把随机数 ×1000 后按此种类写入）
#   2 原始 32 位量
#   3 引用：右值本身是另一个 flag 的键（Vm::test 命中 kind==3 时再取一次 flag）
VALUE_KINDS = {0: "int", 1: "fixed", 2: "raw", 3: "flag_ref"}


def name_of(opcode: int) -> str:
    entry = OPCODES.get(opcode)
    return entry[0] if entry else f"op{opcode}"


def arg_names(opcode: int) -> tuple[str, ...]:
    entry = OPCODES.get(opcode)
    return entry[1] if entry else ()
