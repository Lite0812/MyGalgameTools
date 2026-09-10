# VersaEngine 脚本虚拟机分析

目标：ままごと ～ママとないしょのエッチしましょ～ v0.9（`libmain.so`，arm64-v8a）。
本文是反汇编与文本化工具的唯一真值源。全部条目取自反编译代码，并在
140 个脚本、47,584 条节点的语料上交叉验证。

## 1. VM 类型识别

**不是字节码机。** VersaEngine 的脚本是 **CBOR 抽象语法树**，没有线性指令流、
没有操作数栈、没有寄存器组。每条"指令"是一个 CBOR 数组，第 0 项是 opcode，
其余是操作数：

```
[4, 324185735, 1618164253, 0, 0, 0, 255, 0, 0, [0, 12000, 17, …]]
 ↑  opcode=4 (show)         ↑ 整数操作数…              ↑ 尾部嵌套数组
```

执行模型的准确描述：**带程序计数器的树数组解释器**。

- **状态空间**：全局 flag 表（键为 FNV-1a 32 的哈希映射）+ 一个返回地址栈。
  没有通用寄存器，也没有表达式求值栈——条件比较是指令内建的，不由多条
  指令组合而成。
- **程序计数器**：`Vm` 对象 `+0x14` 处的 u32，值是 `nodes[]` 数组的**下标**，
  不是字节偏移。这一点决定了整个标签化策略（见第 6 节）。
- **地址空间**：每个 scene 一个独立的 `nodes[]` 数组。跨 scene 跳转必须经过
  project 的全局标签表，不能直接用下标。
- **无对齐、无大小端问题**：CBOR 自带类型与长度，解码由 `ve::cbor::Value`
  迭代器完成，脚本层看不到字节。

`checksum` 等容器层字段属于封包格式，见 `archive_analysis.md`。

## 2. 指令解码流程

### 2.1 `Command` 结构体

`ve::Vm::decode` @0x20C1B0 把一个 CBOR 节点填进 56 字节的 `Command`：

| 偏移 | 类型 | 字段 | 说明 |
|---|---|---|---|
| +0x00 | u8 | `opcode` | 节点第 0 项，经 `as_uint` 取低 8 位 |
| +0x04 | u32 | `node_index` | 当前 PC，供日志与返回帧使用 |
| +0x08 | u8 | `argc` | 已收集的整数操作数个数，上限 8 |
| +0x0C..+0x2C | i32[8] | `args` | 整数操作数，经 `as_i32` |
| +0x30 | cbor::Value | `node` | 整个节点的句柄，供处理器读嵌套尾部 |

### 2.2 解码规则

```
node = Script::node(pc)
if node.type != 5 (array):  return false      // 畸形
if node.count == 0:         return false
opcode = node[0].as_uint()
argc = 0
for child in node[1:]:
    if not child.valid() or argc > 7:  break
    if child.type == 5 (array) or == 6 (map):  break   // ← 关键
    args[argc++] = child.as_i32()
```

**关键点：遇到第一个数组或 map 就停止收集整数。** 所以 `show` 的
9 个整数之后的嵌套数组不会进 `args[]`——处理器通过 `+0x30` 的节点句柄
自己去迭代。这就是为什么 `show` 有 8 个命名参数却携带 16 槽的布局数组。

`argc` 上限 8 是硬限制，超出部分静默丢弃。语料中最长的整数序列是 `show`
的 9 个（layer + asset + 7 个），正好触及上限后被嵌套数组截断——实际
`argc` 为 8。

### 2.3 取指循环

`ve::Vm::fetch` @0x20C9D0：

```
while (++budget <= vm[17]) {              // vm+0x44 是控制流预算
    if (!decode(pc, cmd)) {
        log("vm: node %u is malformed, ending scene")
        pc = nodes.size();  return
    }
    switch (cmd.opcode) {
        case 0x00: case 0x1D:  pc += 1; continue          // NOP
        case 0x01:             pc = <跳转目标>; continue
        case 0x1B:             <比较>; continue           // 条件跳转
        case 0x1E:             set_flag(...); pc += 1; continue
        case 0x29:             <压栈>; continue           // call
        case 0x2A:             <弹栈>; continue           // ret
        case 0x37:             draw_random(...); pc += 1; continue
        default:  pc += 1; 把 cmd 拷给调用方返回          // 交 Game::apply
    }
}
log("vm: control flow budget exhausted at node %u, aborting scene")
```

预算机制防死循环：连续执行的控制流指令超过 `vm+0x44` 就中止场景。只有
真正产生动作的指令（走 `default` 分支返回给调用方）才重置计数。

## 3. 双派发路径

指令分成两组，这是理解整个指令集的前提：

**控制流组** {0x00, 0x01, 0x1B, 0x1D, 0x1E, 0x29, 0x2A, 0x37} —— 在 `Vm::fetch`
内部直接执行，**永不到达** `Game::apply`。`Game::apply` 的跳转表注释
（@0x2DBE94）明确列出 `default case, cases 3,22,27-30,33,38,41,42`，
其中 27-30、41、42 正是这一组（十进制）。

**动作组** 其余 43 个 —— 由 `ve::Game::apply` @0x2DBE48 的 53-case switch 处理，
`SUB W9, W8, #2` 说明跳转表以 opcode 2 为基。

## 4. 全量 Opcode 字典

`length` 一栏对 CBOR-AST 而言指**数组元素个数**（含 opcode 本身），
`operand_schema` 用语料实测的形状表示：`i` = 整数，`L` = 嵌套数组。

`flag`/`layer`/`asset`/`text` 类型的操作数都是 **FNV-1a 32 哈希**，不是索引。

### 4.1 控制流组（`Vm::fetch` 直接执行）

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| 0x00 | `nop` | 1，`<空>` | 仅 `pc += 1`，不派发不产生动作 | 20CABC `CBZ W8, loc_20CA48`（loc_20CA48 = PC+1 路径） |
| 0x01 | `jmp` | 2 或 3，`i` / `ii` | `args[0]` 是本 scene 的节点下标，直接写 PC。`argc >= 2` 且 `args[1] != 0` 时改为跨 scene 跳转，`args[1]` 是全局标签键 | 20CAC0-20CAEC；`argc<2` 走 loc_20CBE0。语料 738 条单参 + 199 条双参，单参目标 738/738 为合法下标 |
| 0x1B | `jmp_if_not` | 6，`iiiii` | `flag(args[0])` 与右值按 `args[1]` 比较；**成立则 PC+1，不成立才跳 `args[4]`**。`args[2]` 是 `ValueKind`，为 3 时 `args[3]` 是另一个 flag 的键需二次取值 | 20CF48-20CF74：六个比较分支全部 `B.<cc> loc_20CA48`（PC+1），失败统一落 loc_20CF74（取 args[4]）。`args[0]` 命中 `story_var_keys` 1950/1951 |
| 0x1D | `nop_alt` | 1，`<空>` | 同 0x00，纯占位 | 20CA40 `CMP W8,#0x1D` → `loc_20CA48` |
| 0x1E | `set` | 4，`iii` | `Vm::set_flag(args[0], kind=args[1], value=args[2])` | 20CB7C-20CDB4 调 `ve::Vm::set_flag`。`args[0]` 命中 `story_var_keys` 2201/2202 |
| 0x29 | `call` | 2，`i` | 压入返回帧 `scene_hash \| (pc+1)<<32` 到 `vm+0x18` 的栈，然后 PC = `args[0]` | 20CB08-20CB28 `ORR X8, X25, X20,LSL#32` + `STR X8,[X28],#8` |
| 0x2A | `ret` | 1，`<空>` | 弹出返回帧。栈空时记日志并结束场景；帧内 scene_hash 与当前不同则触发跨 scene 返回 | 20CB9C-20CBDC；日志串 `vm: 'return' at node %u with no open call` |
| 0x37 | `set_random` | 5，`iiii` | `draw_random(args[1], args[2], args[3])`，结果 ×1000 后以 `kind=1`（定点）写入 `flag(args[0])` | 20CDC8 调 `ve::Vm::draw_random`；20CE28 `MOV W8,#0x3E8; MUL` 即 ×1000；越界时日志 `vm: random result %d cannot be stored a…` |

### 4.2 动作组（`Game::apply` 派发）

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| 0x02 | `stop_all` | 1，`<空>` | `Game::stop_movie` + `ParticleSystem::stop_all` | `Game::apply` case 2 @2DBE48:481 |
| 0x04 | `show` | 10，`iiiiiiiiL` | 显示角色/背景。`args[0]` 经 `find_character` 查找已有精灵，未命中则新建（case 4 里那一大段字段初始化）；其余 args：asset, x, y, z, alpha, flip, variant；尾部 16 槽布局数组见 4.3 | case 4 @496：`find_character` + `Script::pick_variant` + `apply_variant_parts`；语料 10172 条形状全为 `iiiiiiiiL` |
| 0x05 | `hide` | 3..6，`ii`（语料）/ 最多 `iiiii` | `args[0]` 经 `find_character` 定位角色精灵；`argc>1` 时 `args[1]` 为 fade_ms（`argc<=1` 或未超 0 时按 200.0 默认），再往后是 mode / x / y | case 5 @1613：`v71 = argc` 逐级分支到 `argc==5`；`ve::Game::find_character` @0x2A8978 |
| 0x06 | `say` | 5，`iiii` | 显示台词。speaker, text, voice, line_id。`ReadLog::seen(line_id)` 决定是否算已读，再 `refresh_skip` + `show_text` | case 6 @1697；语料 27176 条，实测 `line_id != fnv1a32(text)`（0/27176 命中），确认为独立键 |
| 0x07 | `choice` | 2，`L` | 选项表 `[[文本哈希, 落点下标], …]`；先 `remember_choice` 再 `Vm::read_choices` | case 7 @1715；`Vm::read_choices` @0x20D488 |
| 0x08 | `play_sound` | 8，`iiiiiii` | channel, flags, asset, volume, fade_ms, pitch, loop。`Audio::play` 后 `remember_sound`，并 `UnlockLog::mark` + `flush`（音乐鉴赏解锁） | case 8 @1742 |
| 0x09 | `stop_sound` | 3，`ii` | `Audio::stop` + `forget_sound`；也可走 `stop_story_sounds` 批量停止 | case 9 @1829 |
| 0x0A | `start_video` | 2，`i` | 播放视频 `asset` | case 0xA @1853 |
| 0x0B | `wait` | 2，`i` | `ms >= 0` 按毫秒计时；**`ms` 为负数时改为"等待点击"**（置 `Game+5788`/`+5734`，跳过 `arm_skip_policy`） | case 0xB @1858：`(args[0] & 0x80000000) != 0` 分支 |
| 0x0C | `fade_in` | 3 或 2，`ii` / `i` | 从 `color` 淡入，历时 `ms`；省略第二参时用默认色 | case 0xC @1880；语料 159 条双参 + 4 条单参 |
| 0x0D | `fade_out` | 3 或 2，`ii` / `i` | 淡出到 `color` | case 0xD @1908 |
| 0x0E | `camera_shake` | 3，`ii` | 摄像机抖动 `ms` / `amplitude` | case 0xE @1938 |
| 0x0F | `transition` | 8，`iiiiiii` | 过渡。rule（规则名或遮罩图哈希）, ms, a, b, mode, c, d | case 0xF @1960；语料 rule 仅两值：`dissolve` 2177 条、遮罩图 184 条 |
| 0x10 | `particles` | 3，`ii` | 粒子发射器：`stop` → `Project::particle(key)` 取定义 → `set_rate` → `start` | case 0x10 @2209 |
| 0x11 | `set_font` | 3，`ii` | 字体 `asset` / `size`。路径经 `AssetIndex::path_of` + `game_detail::join` 拼出后 `FontLibrary::ensure` 加载，随后 `hand_line_to_window` 重排当前行 | case 0x11 @2267 |
| 0x12 | `set_auto_delay` | 2，`i` | 写 `Game+2612`：auto 模式每行停留时长 | case 0x12 @2413 |
| 0x13 | `set_auto` | 2，`i` | 写 `Game+6183` auto 标志 | case 0x13 @2419；`toggle_auto` @0x2CA2A4 读同一字节 |
| 0x14 | `set_skip` | 2，`i` | 写 `Game+6184` skip 标志 | case 0x14 @2427；`refresh_skip` @0x2CD46C |
| 0x15 | `set_nvl` | 2，`i` | `Dialogue::set_mode`，切换 NVL（全屏文本）/ ADV 模式 | case 0x15 @2435 |
| 0x17 | `camera_zoom` | 3，`ii` | 缩放到 `scale`，历时 `ms` | case 0x17 @2444 |
| 0x18 | `camera_pan` | 4，`iii` | 平移到 (x, y)，历时 `ms` | case 0x18 @2471 |
| 0x19 | `camera_focus` | 3，`ii` | `Camera::focus_on`，`args[0]` 经 `find_character` 定位目标角色 | case 0x19 @2535 |
| 0x1A | `camera_reset` | 2，`i` | 复位摄像机 | case 0x1A @2575 |
| 0x1F | `hide_window` | 1，`<空>` | 清 `Game+6188`，隐藏消息窗 | case 0x1F @2594；`sync_pinned` @0x2A779C 读它决定是否显示 |
| 0x20 | `show_window` | 1，`<空>` | 置 `Game+6188` | case 0x20 @2599 |
| 0x22 | `open_screen` | 2，`i` | 打开 UI 屏 `screen` | case 0x22 @2604 |
| 0x23 | `close_screen` | 2，`i` | `args[0] != 0` → `ScreenView::close_all`，否则 `close_top` | case 0x23 @2615 |
| 0x24 | `wait_screen` | 1，`<空>` | `ScreenView::has_unpinned()` 为真时挂起 VM，等待非固定屏关闭 | case 0x24 @2621 |
| 0x25 | `filter` | 3+，`ii` + 嵌套 | `args[0]` 经 `find_character` 定位角色；**滤镜级联在尾部嵌套数组里**（数组长度须 >= 3，`read_filter_stages` 跳过前 2 项后读取），随后的整数是时长。时长受设置项 39 与 skip 门控 | case 0x25 @2625：`read_filter_stages` + `begin_filter_blend`；不是简单的 `(layer,kind,ms)` |
| 0x27 | `input_text` | 4，`iii` | 文本输入框。`args[0]` 提示语，`args[1]` 接收结果的字符串变量，`args[2]` 最大字数（0 按 32 处理） | case 0x27 @2686；`ScreenView::set_prompt(v75=arg0)` + `set_dialog_input(v76=arg1, v220=arg2)` |
| 0x28 | `set_offset` | 3，`ii` | 写 `Game+7024/7028` 目标偏移，按参考分辨率钳制后驱动画面 | case 0x28 @2757；`visual_activity` @0x2A8CB8 用 `g_reference_width/height` 钳制 |
| 0x2B | `unlock_extras` | 1，`<空>` | 解锁附录内容 | case 0x2B @2773 |
| 0x2C | `dialogue_face` | 6，`iiiii` | `Dialogue::set_face`：立绘框的图与裁剪 asset / x / y / w / h | case 0x2C @2777 |
| 0x2D | `tint` | 2..3，`i` / `ii` | 前景整体调色：起点取 `Game::current_fg_tint`，终点 `args[0] \| 0xFF000000`，`args[1]` 是时长。是全局前景色而非单图层 | case 0x2D @2813：`current_fg_tint` + `v40 \| 0xFF000000` |
| 0x2E | `camera_wait` | 1，`<空>` | `Camera::remaining_ms() > 0` 时挂起 VM | case 0x2E @2841 |
| 0x2F | `offer_inspect` | 2..3，`i` / `ii` | `Game::offer_inspect(cond != 0)`。`args[0] == 3`（即 `ValueKind::flag_ref`）时 `args[1]` 是 flag 键，需先 `Vm::flag` 取值；否则 `args[1]` 直接作条件 | case 0x2F @2854：`*(a2+12) == 3` 分支调 `ve::Vm::flag` |
| 0x30 | `auto_save` | 1，`<空>` | 自动存档 | case 0x30 @2878 |
| 0x31 | `unlock_gallery` | 2，`i` | 解锁鉴赏项 `key` | case 0x31 @2881 |
| 0x32 | `stage_shake` | 4，`iii` | 写 `Game+2644/2648`（幅度与时长），受 skip 与设置项 39 影响 | case 0x32 @2891 |
| 0x33 | `clear_page` | 3，`ii` | 翻页。`args[0] != 0` 记入 `Game+2165`（保留当前页）；`args[1]` 是时长，`argc<=1` 或设置项 39 < 0.5 时归零 | case 0x33 @2925：`Dialogue::clear_page` + `Tween::value` |
| 0x34 | `camera_finish` | 1，`<空>` | `Camera::finish_shake`，立即结束抖动 | case 0x34 @2999 |
| 0x35 | `set_say_screen` | 2，`i` | 写 `Game+6268` 的 say 屏 id | case 0x35 @3030；`active_say_screen` 读它 |
| 0x36 | `wait_audio` | 2，`i` | `Audio::playing(channel)` 为真时挂起 VM | case 0x36 @3041 |

### 4.3 `show` 的布局数组（sub-schema）

`show` 尾部嵌套数组不进 `args[]`，由处理器自行迭代。总长有 16 / 15 / 14 三种
（分别 10083 / 86 / 3 条），槽位含义由语料统计确定：

| 槽 | 名称 | 取值 | 说明 |
|---|---|---|---|
| 0 | `kind` | 0=`bg`(4221), 1=`frames`(205), 3=`layered`(5746) | 精灵类型 |
| 1 | — | 由槽 0 唯一决定：`layered`→0，其余→12000 | 无独立信息，文本视图省略 |
| 2 | `flags` | 17（10169 条）/ 9（3 条） | |
| 3 | `parts` | `[]` 或 `[基底图, [差分图, x, y], …]` | 2133 种取值，差分立绘的核心 |
| 4 | — | 恒 `[]` | 省略 |
| 5 | `frames` | `[]`(9967) 或 `[2500, 80, 80]`(205) | 与 `kind=frames` 一一对应 |
| 6..9 | — | 恒 0 | 省略 |
| 10 | — | 恒 1000 | 省略 |
| 11 | `clip` | 83 种，如 `[0, 0, 1280, 720]` | 裁剪矩形 x,y,w,h |
| 12 | — | 恒 1000 | 省略 |
| 13 | — | 恒 0 | 省略 |
| 末项 | `style` | 6 元整数组，第 3 位是 RGB | 16777215（白）= 不着色，9620 条 |

`style` 必须**按数组尾部取而不是固定下标**——长度 14 的那 3 条里既没有
`anim` 也没有 `style`，按下标 15 取会越界。

`asm.txt` 里这个数组不以原始形式出现，而是拆成具名字段
（`as=` / `flags=` / `.part` 子行 / `sheet=` / `clip=` / `tint=`），无信息的
恒定槽全部省掉。编解码在 `opcodelist.py` 的 `sprite_decode` / `sprite_encode`，
两者严格互逆，反汇编时当场复核，不符合本表的数组退回原始 `.layout` 形式。
格式详见 `asm_format.md` §4.1。

### 4.4 未定义的 opcode 值

1..55 中 **3, 22, 28, 33, 38** 无定义，与 `Game::apply` 跳转表的
`default case, cases 3,22,27-30,33,38,41,42` 完全吻合（其中 27-30、41、42
是控制流组，由 `fetch` 处理；3、22、28、33、38 则确实是空洞）。这不是分析
遗漏，而是编译器保留的空槽。

## 5. 辅助枚举

### 5.1 比较运算符（`args[1]` of 0x1B）

| 值 | 运算符 | 佐证 |
|---|---|---|
| 0 | `==` | 20CF48 `CMP W23,W28` + `B.EQ` |
| 1 | `!=` | 20CF60 + `B.NE` |
| 2 | `<` | 20CD50 + `B.LT` |
| 3 | `<=` | 20CF54 + `B.LE` |
| 4 | `>` | 20CF6C + `B.GT` |
| 5 | `>=` | 20CD94 + `B.GE` |

注意 3/4/5 的顺序不是 `>`/`>=`/`<=`——这是一处容易搞错的地方，
`Vm::test` @0x20C338 的 case 分派可交叉验证。

### 5.2 `ve::ValueKind`

| 值 | 名称 | 语义 |
|---|---|---|
| 0 | `int` | 整数（含 0/1 布尔） |
| 1 | `fixed` | 定点数，实际值 = 存值 / 1000 |
| 2 | `raw` | 原始 32 位量 |
| 3 | `flag_ref` | 引用：右值本身是另一个 flag 的键，需再取一次 |

`kind == 3` 时 `Vm::fetch` 在 20CD1C-20CD2C 二次调用 `ve::Vm::flag`——
这是判定"引用"语义的直接证据。

## 6. 跳转标签化策略

### 6.1 偏移基准

**跳转目标是 `nodes[]` 的绝对下标，不是相对偏移。** 0x01 直接
`STR` 到 `vm+0x14`；0x1B 失败路径同样直接取 `args[4]`。没有任何
以指令起始/末尾/PC 为基的相对计算。这简化了标签化：下标与标签一对一。

标签命名 `loc_XXXXXXXX`，后缀是 8 位十六进制的节点下标：

```
loc_00000000:
    show        layer=layer_1352AE87 asset=images/bg/ＢＧ空曇り.webp kind=bg
```

### 6.2 本地标签

脚本自带 `label_keys` / `label_values` 并行数组：键是标签名的 FNV-1a 32，
值是节点下标。反汇编时在对应位置额外输出 `label_XXXXXXXX:` 别名行。

### 6.3 跨 scene 跳转

`project.cbor` 有全局标签表，三个并行数组共 633 条：

```
label_keys[i]   标签名的 FNV-1a 32
label_scenes[i] 目标 scene 名的 FNV-1a 32   ← 是哈希，不是下标
label_nodes[i]  目标节点下标
```

`label_scenes` 存哈希这一点最初被误判成 scene 下标，导致
`scenes[1494169878]` 越界。正确做法是反查：

```python
by_hash = {fnv1a32(s.encode("utf-8")): s for s in project["scenes"]}
```

140 个 scene 全部反查命中（140/140），与 `Game::resolve_destination`
@0x2A30D0 的比对方式一致。文本视图输出 `jmp_far node=scr_007:loc_00000000`。

### 6.4 变长文本重写后的重定位

节点下标与文本长度无关——文本只是 `strings` 表里的一个哈希引用，改写台词
不影响任何节点的下标。真正需要重定位的场景是**增删节点**，此时所有
`jmp`/`jmp_if_not`/`call`/`choice` 的目标下标与两级标签表都要同步更新。
这是文本视图坚持用符号标签而非硬编码下标的原因。

## 7. 哈希解析

所有名字在编译期都被替换成 FNV-1a 32，脚本里没有任何字符串。解析来源：

| 引用类型 | 来源 | 结果 |
|---|---|---|
| `text` / `speaker` | `*.strings.cbor` 的 `table` + `pieces` | 全部还原 |
| `asset` / `voice` | `assets.cbor` 的 `keys→values→paths` | 全部还原 |
| 跨 scene 标签 | `project.cbor` 全局标签表 | 140/140 |
| `channel` / `rule` / `screen` | 候选词表反查 | `music`、`dissolve`、17 个 UI 屏中的 13 个 |
| `layer` / `flag` / 玩家变量 | **不可还原** | 占位符 `layer_XXXXXXXX` 等 |

`pieces` 的处理有个陷阱：`table[key]` 为空串时表示该条整体由变量拼成
（如玩家自取的主角名），真正内容在 `pieces` 里，因此判空必须用真值判断
而不是 `key in table`。

不可还原的部分是经测量确认的，不是放弃：穷举了 1–5 字符小写+数字+下划线、
6 字符小写+下划线、二段与三段复合词，以及一份日语词表，全部未命中。
这些名字在编译期已被丢弃。

### 7.1 图层键的族结构（可推导的部分）

虽然名字本身不可还原，`layer` 键的**结构**是可以确定的。全部 `show`/`hide`
只用到 13 个不同的 layer 键，其中 7 个（占全部用量的 10527/11884 ≈ 89%）
两两之差恰为 FNV 质数 `0x01000193` 的整数倍，倍数为 `[0,1,3,4,5,6,7]`：

```
0E52A6A8  0F52A83B  1152AB61  1252ACF4  1352AE87  1452B01A  1552B1AD
```

FNV-1a 的递推是 `h = (h ^ c) * P`，所以差值是 P 的倍数 ⟺ **这些名字只有
最后一个字节不同，前缀完全相同**。倍数集合 `{0,1,3,4,5,6,7}` 落在同一个
8 字节对齐块内，而 `'0'..'7'` = `0x30..0x37` 正是这样一个块——即这一族是
`<前缀>0` 到 `<前缀>7`（缺 `2`）的编号图层。用反元素求前缀哈希得
`0xF6AF8788`，但穷举未命中，说明前缀长度或字符集超出已搜索范围。

结论：这 7 个是同一组编号图层槽（立绘层 0..7），另 6 个是独立命名的
图层（背景、UI 覆盖等）。文本视图仍输出 `layer_XXXXXXXX`，但这个族关系
说明它们在语义上是有序的槽位而非任意名字。

## 8. 未定义 Opcode 的发现与校正流程

按 CLAUDE.md §0.4 执行，本项目实际用到的三步：

1. **回溯宿主可执行文件**：在 `Game::apply` 的 switch 与 `Vm::fetch` 的
   `CMP` 链中查该字节。跳转表注释直接给出 default case 列表，可一次性
   确认哪些值是真空洞。
2. **检查边界连锁**：对 CBOR-AST 不存在"长度误判导致后续偏移错位"的问题
   （每个节点自带长度），所以这一步退化为检查 `argc` 是否被嵌套数组
   提前截断。
3. **回写文档并全量重跑**：修正后重新生成全部 140 个脚本，确认零未识别
   opcode、零裸哈希、零 `\x` 转义。

本项目在此流程中发现并修正的误判（全部是潜在错误——语料只用到 18 个
opcode，这些指令从未被实际执行过，所以不会自己暴露）：

| 原判断 | 修正后 | 依据 |
|---|---|---|
| 0x01 `nop_or_label` | `jmp` | args[0] 是本地节点下标，738/738 合法 |
| 0x1B `jump_if(target,lhs,op,rhs,next)` | `jmp_if_not(flag,op,kind,rhs,node)` | 参数顺序错、跳转极性反了 |
| 0x1D 视为 `jmp_if_not` 变体 | `nop_alt` | 20CA40 直接落 PC+1 路径 |
| 0x1E `call` | `set` | 实为 `Vm::set_flag(key,kind,value)` |
| 0x29 `push_return` | `call` | 压帧后即改 PC |
| 0x27 `prompt(screen,text,flags)` | `input_text(text,var,max_len)` | 参数完全反了 |
| 0x28 `set_name_offset` | `set_offset` | `visual_activity` 显示它驱动画面而非名牌 |
| 比较符 3/4/5 = `>`/`>=`/`<=` | `<=`/`>`/`>=` | `Vm::test` case 分派 |

## 9. 脚本容器结构

`*.script.cbor` 的顶层键（以 `scr_007` 为例，857 节点）：

| 键 | 类型 | 说明 |
|---|---|---|
| `name` / `language` / `version` | text / text / u32 | `scr_007` / `ja` / 1 |
| `entry` | u32 | 入口节点下标 |
| `nodes` | array | 指令数组，本文的主体 |
| `label_keys` / `label_values` | array | 本地标签表 |
| `const_keys/kinds/values` | array | 脚本级常量（本作全空） |
| `var_keys/kinds/values` | array | 脚本级变量（本作全空） |
| `speaker_keys` | array[84] | 说话者哈希 |
| `speaker_colors` | array[84] | 名字颜色 ARGB |
| `speaker_fonts` / `speaker_screens` | array[84] | 字体与所属 UI 屏 |
| `speaker_groups` / `voice_group_keys` | array[84] | 分组与语音组 |
| `speaker_portraits` / `speaker_voice_portraits` | array[420] | 每说话者 5 槽立绘 |
| `variants` | array | 差分变体表 |

`project.cbor` 除全局标签表外的要点：`scenes`(140)、`packages`(149)、
`story_var_keys`(146，条件跳转与 `set` 的 flag 全部命中此表)、
`reference_width/height` = 1280×720、`settings`(52)、`gallery_*` 与
`replay_*` 的鉴赏/回想数据、`pack_key_hi/lo`（VEPK 流加密的 64 位密钥，
本作因所有包都未设加密位而未使用）、`source_dir` =
`C:/Users/admin/Desktop/VersaEngine/projects-KaGuYa/mamagoto`。

## 10. 语料统计

140 个脚本，47,584 条节点。**实际只用到 18 个 opcode**：

| Opcode | 命名 | 次数 | 占比 |
|---|---|---|---|
| 0x06 | `say` | 27,176 | 57.1% |
| 0x04 | `show` | 10,172 | 21.4% |
| 0x0F | `transition` | 2,361 | 5.0% |
| 0x1E | `set` | 2,202 | 4.6% |
| 0x1B | `jmp_if_not` | 1,951 | 4.1% |
| 0x2A | `ret` | 1,624 | 3.4% |
| 0x01 | `jmp` | 937 | 2.0% |
| 0x05 | `hide` | 712 | 1.5% |
| 0x0B | `wait` | 562 | 1.2% |
| 0x08 | `play_sound` | 372 | 0.8% |
| 0x0D | `fade_out` | 164 | 0.3% |
| 0x0C | `fade_in` | 163 | 0.3% |
| 0x29 | `call` | 146 | 0.3% |
| 0x07 | `choice` | 42 | 0.1% |
| 0x02 | `stop_all` | 3 | — |
| 0x22 | `open_screen` | 1 | — |
| 0x24 | `wait_screen` | 1 | — |
| 0x27 | `input_text` | 1 | — |

剩余 33 个已定义 opcode 在本作未使用（摄像机组、粒子、滤镜、
NVL、auto/skip 控制等），但引擎支持——定义仍完整保留，以便同引擎
其他作品复用。

## 11. 佐证地址索引

| 函数 | 地址 | 作用 |
|---|---|---|
| `ve::Vm::fetch` | 0x20C9D0 | 取指循环 + 控制流执行 |
| `ve::Vm::decode` | 0x20C1B0 | `Command` 填充 |
| `ve::Vm::test` | 0x20C338 | 比较运算 |
| `ve::Vm::flag` | 0x20C44C | 读 flag |
| `ve::Vm::set_flag` | 0x20D1A4 | 写 flag |
| `ve::Vm::jump` | 0x20C140 | 设置 PC |
| `ve::Vm::jump_to_label` | 0x20C148 | 按标签跳转 |
| `ve::Vm::read_choices` | 0x20D488 | 选项表解析 |
| `ve::Vm::draw_random` | — | 随机数（`set_random` 调用） |
| `ve::Game::apply` | 0x2DBE48 | 43-case 动作派发 |
| `ve::Game::resolve_destination` | 0x2A30D0 | scene 哈希反查 |
| `ve::Game::follow_destination` | 0x2A33E8 | 跨 scene 跳转 |
| `ve::Game::sync_pinned` | 0x2A779C | 消息窗显示门控 |
| `ve::Game::visual_activity` | 0x2A8CB8 | 画面偏移钳制 |
| `ve::Script::find_label` | 0x2037A4 | 本地标签查找 |
| `ve::Script::enclosing_label` | 0x2038DC | 反查所属标签 |
| `toggle_auto` | 0x2CA2A4 | auto 标志 |
| `refresh_skip` | 0x2CD46C | skip 标志 |
| `hold_skip` | 0x2CCC28 | skip 保持 |
