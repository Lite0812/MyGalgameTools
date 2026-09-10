# Floating Material 剧本虚拟机分析

## 1. 文档状态与适用范围

本文档分析对象为 `flo_mate.exe` 中的 YOX ADV 脚本虚拟机，以及当前样本 `script.dat` 中的 281 个编译剧本。它是后续 `opcodelist.cs`、反汇编器和汇编器的指令边界、操作数类型、控制流重定位与字符串池处理依据。

分析主要依据：

- VM 初始化与 Opcode 调度表：`sub_441FC0`（0x441FC0）。
- VM 执行循环：`sub_4419C0`（0x4419C0）。
- 单个脚本加载：`sub_441620`（0x441620）。
- 内建 Opcode：0x440560–0x4415B0、0x441CA0–0x441EA0。
- Native 子命令注册：`sub_4400C0` 及 0x41C670 等注册函数。
- 当前 `script.dat` 的静态全量边界验证。

命名与确定性规则：

- 0x00–0x3F 内建指令使用其处理器已经完全证明的行为名称。
- Native 指令使用处理器直接证明的语义助记符，例如 `SYSTEM_FLAG_SET_RANGE`、`LAYER_SET_TYPE`、`TIMER_BRANCH_WHILE_BELOW`。处理器地址只放在佐证列，不进入助记符。
- 当 EXE 没有暴露更具体的游戏层术语时，名称严格描述可观察的数据动作，例如 `MESSAGE_SET_LAYOUT_FIELDS`、`COMPAT_DISCARD3`；不根据调用场景补写未经处理器证明的业务含义。
- 同一处理器注册到多个 ID 时仍为每个 ID 提供唯一助记符，确保汇编文本可以无歧义还原原始 Native ID。
- 未注册槽位统一命名为 `INVALID_NATIVE_<ID>`，反汇编时必须报错。

## 2. 容器与脚本文件格式

### 2.1 外层 `script.dat`

外层文件为未压缩、未加密的 YOX DAT 容器，所有整数均为小端序。

| 偏移 | 大小 | 含义 |
|---:|---:|---|
| 0x00 | 4 | 魔数 `YOX\0` |
| 0x04 | 4 | 容器版本/标志，当前为 0 |
| 0x08 | 4 | 索引表绝对文件偏移 |
| 0x0C | 4 | 条目数量 |
| 0x10 | 16 | 保留字段 |

当前文件的索引偏移为 `0x5C1000`，条目数为 281。每个索引项为 16 字节：

```text
u32 file_offset
u32 file_size
u32 metadata1
u32 metadata2
```

当前样本中 `metadata1` 全部为 `0xFFFFFFFF`，`metadata2` 全部为 0。条目按 `0x800` 对齐。索引表后还有每条 12 字节的年月日时分秒记录，VM 加载路径不读取该区域。

### 2.2 内层剧本条目

每个 DAT 条目本身也是一个 YOX 文件：

| 偏移 | 大小 | 含义 |
|---:|---:|---|
| 0x00 | 4 | 魔数 `YOX\0` |
| 0x04 | 4 | 版本 `0x10000000` |
| 0x08 | 4 | `code_size`，字节码区长度 |
| 0x0C | 4 | `string_size`，字符串池长度 |
| 0x10 | 16 | 编译时间及编译器元数据，VM 忽略 |
| 0x20 | `code_size` | 指令流 |
| 0x20 + code_size | `string_size` | NUL 分隔字符串池 |

所有 281 个条目均满足：

```text
entry_size - 0x20 == code_size + string_size
```

VM 加载时剥离 0x20 字节内层头，只保存主体，并将 `code_size` 保存为代码区结束位置和字符串池基址。

## 3. VM 体系结构

该 VM 是“16 个通用整数寄存器 + 32 项共享操作数/返回地址栈 + 多任务调度”的混合寄存器 VM。

- 指令和字符串位于同一分配块，但逻辑上分成代码区与字符串池，可视为分段式冯·诺依曼结构。
- 指令指针是相对脚本主体开头的绝对字节偏移。
- 最多同时存在 64 个脚本任务。
- 每帧依次调度所有活动任务；一个任务会连续执行，直到等待、主动让出、结束或出错。
- Native 命令通过共享栈取得参数，也可把返回值压回共享栈。

### 3.1 任务结构（0xE4 字节）

| 偏移 | 大小 | 含义 |
|---:|---:|---|
| 0x00 | 4 | 原始分配指针 |
| 0x04 | 4 | 64 字节对齐后的脚本主体指针 |
| 0x08 | 2 | 任务状态标志；位 1/2 用于暂停、消息等待等 |
| 0x0A | 2 | 任务链接/返回关系 ID，`-1` 表示普通调用任务 |
| 0x0C | 2 | 父任务 ID |
| 0x0E | 2 | 父任务链接 ID |
| 0x10 | 4 | 浮点等待计时器 |
| 0x14 | 4 | 主体总长度，即 `code_size + string_size` |
| 0x18 | 4 | 指令指针 `IP` |
| 0x1C | 0x80 | 32 个 int32 的共享参数/调用栈 |
| 0x9C | 4 | 栈深度 `SP` |
| 0xA0 | 0x40 | 16 个 int32 通用寄存器 R0–R15 |
| 0xE0 | 4 | `code_size`，同时也是字符串池相对基址 |

### 3.2 执行循环

执行逻辑等价于：

```text
while task.active and not task.paused:
    if task.wait > 0:
        task.wait -= frame_delta
        yield
    if IP >= body_size:
        end/error
    opcode = body[IP]
    IP += 1
    result = dispatch[opcode](engine, task)
    result == 0: continue
    result == 1: terminate VM update as end/error
    result == 2: yield current task
```

调度表包含 256 项。初始化时定义 0x00–0x3F 的内建命令，其余项先指向失败处理器；游戏初始化随后把 Native 函数注册到特定槽位。

## 4. 指令与操作数编码

### 4.1 基本编码

- Opcode：1 字节。
- 普通操作数：固定 5 字节，不做对齐。

```text
u8  operand_kind
u32 operand_value_le
```

### 4.2 已确认的操作数类型

| kind | 名称 | 解释 |
|---:|---|---|
| 0x01 | `IMM` | 32 位立即数；是否有符号由具体命令决定 |
| 0x02 | `REG` | `operand_value` 为寄存器编号 0–15，读取时取 Rn |
| 0x04 | `STRREF` | 字符串池相对偏移；压栈时保留偏移值，Native 使用时再解析 |
| 0x10 | `CODEADDR` | 相对脚本主体开头的绝对代码偏移 |
| 0x40 | `TEXTREF` | 直接文本显示命令使用的字符串池相对偏移 |

字符串地址计算：

```text
string_address = body + code_size + string_offset
```

当前样本的 86,234 个 `STRREF/TEXTREF` 均在字符串池范围内，且全部指向文件首或前一字节为 NUL 的字符串起点。

### 4.3 栈参数约定

`PUSH1`–`PUSH4` 把值按源码顺序压栈。Native 函数调用 `sub_440270(task, argv, count)` 时从栈顶逆序弹出，从而恢复原参数顺序：

```text
PUSH3 a, b, c
CALL_NATIVE id

Native 接收 argv[0] = a, argv[1] = b, argv[2] = c
```

Native 返回值通过 `sub_440230` 再压回同一个栈。该栈也被 `CALL/RET` 用作返回地址栈。

## 5. 控制流与标签规则

- 所有 `CODEADDR` 均是相对脚本主体开头的绝对偏移，不是相对当前 PC 的位移。
- 汇编文本必须将非零目标转换为标签，例如 `loc_00001234`。
- 目标 0 在当前脚本中可作为空分支/未配置分支哨兵，重建时必须保留。
- 修改字符串池不会直接改变代码地址；修改指令长度时必须重算所有绝对目标。
- `CALL_NATIVE 0x55` 是例外：它可以按栈参数对 IP 做运行时相对调整，应在控制流分析中标记为动态边。

当前样本共发现 594 个静态控制流目标，其中 78 个为 0；其余目标全部落在已解析的指令起始边界。

## 6. 内建 Opcode 全表

长度均包含 1 字节 Opcode。`V` 表示 `IMM|REG`，`S` 表示 `STRREF`，`A` 表示 `CODEADDR`。

| Opcode | 助记符 | 长度 | operand_schema | 行为 |
|---:|---|---:|---|---|
| 0x00 | `INVALID_00` | 1 | 无 | 返回 1；当前样本未使用 |
| 0x01 | `CALL_NATIVE` | 6 | `IMM native_id` | 调用动态注册表；Native 0x3F 变体还会继续读取一个内联操作数 |
| 0x02 | `PUSH_ADDR` | 6 | `A` | 仅接受 kind 0x10，将代码地址压栈 |
| 0x03 | `PUSH1` | 6 | `IMM|REG|S` | 压入 1 个值 |
| 0x04 | `PUSH2` | 11 | 2 × `IMM|REG|S` | 压入 2 个值 |
| 0x05 | `PUSH3` | 16 | 3 × `IMM|REG|S` | 压入 3 个值 |
| 0x06 | `PUSH4` | 21 | 4 × `IMM|REG|S` | 压入 4 个值 |
| 0x07 | `POP_REG` | 6 | `REG dst` | 从共享栈弹出 1 项写入寄存器 |
| 0x08 | `END` | 1 | 无 | 返回 1，结束当前 VM 更新；样本中作为显式终止指令使用 |
| 0x09 | `TRACE_TEXT` | 6 | `S` | 格式化调试字符串 |
| 0x0A | `TRACE_REGS` | 6 | `IMM radix` | radix=10 时十进制，否则十六进制格式化 R0–R15 |
| 0x0B | `SKIP_ARG` | 6 | `ANY` | 跳过一个 5 字节操作数，不执行其他动作 |
| 0x0C | `FORMAT_REGS` | 6 | `IMM radix` | 格式化寄存器状态；当前实现不产生可见输出 |
| 0x0D | `RET_TASK` | 1 | 无 | 栈非空时弹出返回地址；否则结束任务并按父子关系恢复父任务 |
| 0x0E | `WAIT` | 6 | `V ticks` | 设置等待计时器并让出执行 |
| 0x0F | `WAIT_SKIP` | 6 | `V ticks` | 与 WAIT 相同；处于快进/跳过状态时强制等待值为 0 |
| 0x10 | `DJNZ` | 11 | `REG counter, A target` | `--counter`；结果大于 0 时跳转，否则顺序执行 |
| 0x11 | `JMP` | 6 | `A target` | 无条件绝对跳转 |
| 0x12 | `JE` | 16 | `REG lhs, V rhs, A target` | 相等时跳转 |
| 0x13 | `JNE` | 16 | `REG lhs, V rhs, A target` | 不等时跳转 |
| 0x14 | `JL` | 16 | `REG lhs, V rhs, A target` | 有符号小于时跳转 |
| 0x15 | `JLE` | 16 | `REG lhs, V rhs, A target` | 有符号小于等于时跳转 |
| 0x16 | `JG` | 16 | `REG lhs, V rhs, A target` | 有符号大于时跳转 |
| 0x17 | `JGE` | 16 | `REG lhs, V rhs, A target` | 有符号大于等于时跳转 |
| 0x18 | `SWITCH4` | 26 | `REG selector, A case0..case3` | selector 0–3 跳到对应目标；其他值顺序越过目标表 |
| 0x19 | `CALL` | 6 | `A target` | 把下一条指令地址压栈后绝对跳转 |
| 0x1A | `INVALID_1A` | 1 | 无 | 失败处理器；样本未使用 |
| 0x1B | `SPAWN_SCRIPT` | 16 | `IMM link_id, S archive, V rid` | 创建并行子任务，加载 DAT/RID，复制当前 R0–R15 |
| 0x1C | `FORK_TASK` | 11 | `V link_id, A entry` | 复制当前脚本主体与任务状态，从指定地址创建并行任务 |
| 0x1D | `KILL_TASK_LINK` | 6 | `V link_id` | 删除链接 ID 匹配的任务 |
| 0x1E | `CALL_SCRIPT` | 11 | `S archive, V rid` | 暂停当前任务，加载子剧本；子任务结束时恢复父任务 |
| 0x1F | `INVALID_1F` | 1 | 无 | 失败处理器；样本未使用 |
| 0x20 | `MOV` | 11 | `REG dst, V src` | `dst = src` |
| 0x21 | `ADD` | 11 | `REG dst, V src` | `dst += src` |
| 0x22 | `SUB` | 11 | `REG dst, V src` | `dst -= src` |
| 0x23 | `MUL` | 11 | `REG dst, V src` | `dst *= src` |
| 0x24 | `DIV` | 11 | `REG dst, V src` | 有符号整数除法；VM 未检查除数为 0 |
| 0x25 | `MOD` | 11 | `REG dst, V src` | 有符号余数；VM 未检查除数为 0 |
| 0x26 | `SET_ADD` | 16 | `REG dst, V a, V b` | `dst = a + b` |
| 0x27 | `ADD_CLAMP_MAX` | 16 | `REG dst, V delta, V max` | 加法后限制不超过 max |
| 0x28 | `SUB_CLAMP_MIN` | 16 | `REG dst, V delta, V min` | 减法后限制不低于 min |
| 0x29 | `ADD_MOD` | 16 | `REG dst, V delta, V modulo` | `dst = (dst + delta) % modulo` |
| 0x2A | `CLAMP` | 16 | `REG dst, V min, V max` | 把 dst 限制到 `[min,max]` |
| 0x2B–0x2F | `INVALID` | 1 | 无 | 失败处理器；样本未使用 |
| 0x30 | `OR` | 11 | `REG dst, V src` | 按位或 |
| 0x31 | `AND` | 11 | `REG dst, V src` | 按位与 |
| 0x32 | `XOR` | 11 | `REG dst, V src` | 按位异或 |
| 0x33 | `NOT` | 11 | `REG dst, V src` | `dst = ~src` |
| 0x34 | `SHL` | 11 | `REG dst, V bits` | 左移 |
| 0x35 | `SAR` | 11 | `REG dst, V bits` | 有符号算术右移 |
| 0x36–0x39 | `INVALID` | 1 | 无 | 失败处理器；样本未使用 |
| 0x3A | `RAND` | 6 | `REG dst` | `dst = rand()` |
| 0x3B | `SIN4096` | 11 | `V degrees, REG dst` | `dst = int(sin(degrees) * 4096)` |
| 0x3C | `COS4096` | 11 | `V degrees, REG dst` | `dst = int(cos(degrees) * 4096)` |
| 0x3D–0x3E | `INVALID` | 1 | 无 | 失败处理器；样本未使用 |
| 0x3F | `TEXT` | 6 | `TEXTREF text` | 游戏初始化后覆盖默认处理器；显示消息文本并进入消息等待状态 |

## 7. Native 子 Opcode

`CALL_NATIVE` 的 Native ID 操作数固定为 `IMM`。除 0x3F 外，参数由前置 `PUSHn` 提供。`pop/push` 是处理器对共享栈的精确消耗与返回数量；“字段”类名称表示 EXE 只公开了确定的内存写入，未公开更窄的游戏术语。

| ID | 规范助记符 | pop/push | 精确定义 | 处理器/佐证 |
|---:|---|---:|---|---|
| 0x3F | `DISPLAY_TEXT_INLINE` | inline 1/0 | 继续读取一个 `TEXTREF`，解析并提交消息文本，设置任务消息等待位 | 0x41D0C0 |
| 0x40 | `MESSAGE_SET_FLAG_1000` | 1/0 | 消息条目存在时按参数设置或清除消息对象标志 `0x1000` | 0x41D4F0 → 0x4426E0 |
| 0x41 | `MESSAGE_SET_FONT_CONFIG` | 5/0 | 写入消息对象 DWORD 5674–5678，随后重建两套字体资源 | 0x41D540 → 0x4424F0, 0x415580 |
| 0x42 | `MESSAGE_SET_MODE_FLAGS` | 4/0 | 参数 0 映射低模式位 0/1/2/4；参数 1–3 分别控制 `0x1000/0x10/0x100` | 0x41D590 → 0x442380 |
| 0x43 | `MESSAGE_SET_LAYOUT_FIELDS` | 4/0 | 写入消息对象 DWORD 5662–5665 | 0x41D5E0 → 0x4422F0 |
| 0x44 | `MESSAGE_SET_CONTROL_FIELDS` | 3/0 | 写入 DWORD 5670；值为 -1 时设置对象标志 `0x40`，否则清除该位并写 DWORD 5671–5672 | 0x41D630 → 0x443120 |
| 0x45 | `NAMEPLATE_SET_TEXT` | 1/0 | 非空字符串创建资源 299 的姓名牌图层；空字符串销毁该图层 | 0x41D6C0, 0x41D030 |
| 0x46 | `CHOICE_SET_STATE` | 2/0 | 写选择项状态：输入模式 0/1/2 映射为内部值 1/0/2，其他模式报错 | 0x41D8F0 |
| 0x47 | `CHOICE_SET_RECT` | 5/0 | 写选择项记录的四个矩形字段并刷新对应 UI 层 | 0x41D980 → 0x438FB0 |
| 0x48 | `MESSAGE_SET_FLAG_2000_INVERTED` | 1/0 | 对参数异或 1 后设置或清除消息对象标志 `0x2000` | 0x41DA50 → 0x4426E0 |
| 0x49 | `MESSAGE_SET_WINDOW_MODE` | 2/0 | 写消息窗口模式；模式非零时写附加参数，随后刷新窗口 | 0x41DA90 |
| 0x4A | `MESSAGE_LINK_LAYER_PAIR` | 5/0 | 取得源图层尺寸，建立目标、源及另一图层之间的组合关系，并禁用两源图层 | 0x41DAF0 |
| 0x4B | `MESSAGE_SET_RGB_SLOT` | 4/0 | 在索引槽写入三个字节值，并把第四字节设为 `0xFF` | 0x41DB90 → 0x442B00 |
| 0x4C | `MESSAGE_SET_STRING_SLOT_64` | 2/0 | 将字符串复制到 `byte_53BD40 + index*64` | 0x41DBE0 |
| 0x4D | `MESSAGE_SET_INTEGER_SLOT` | 2/0 | 执行 `dword_53C140[index] = value` | 0x41DC40 |
| 0x4E | `MESSAGE_SET_RENDER_FIELDS` | 3/0 | 写消息对象 DWORD 5666–5668 | 0x41DC80 → 0x442330 |
| 0x4F | `MESSAGE_SET_LABEL_SLOT_16` | 2/0 | 将字符串复制到消息对象的 `index*16` 标签槽 | 0x41DCC0 → 0x442D60 |
| 0x50 | `MESSAGE_SET_ITEM` | 3/0 | 清空索引项字符串；第三参数非 -1 时复制字符串，并写同索引整数值 | 0x41DD10 → 0x4429E0 |
| 0x51 | `MESSAGE_BUFFER_SET_HEAD` | 1/0 | 写消息缓冲对象首 DWORD | 0x41DD70 → 0x443EE0 |
| 0x52 | `MESSAGE_BUFFER_CLEAR` | 1/0 | 清零缓冲区 `+8..+0x1807` 并把 `+4` 计数设为 0；弹出的参数不参与写入 | 0x41DE00 → 0x443EF0 |
| 0x53 | `MESSAGE_SET_RECORD_FIELDS` | 5/0 | 在 `Src+23228+index*16` 写四个 DWORD | 0x41DE80 → 0x442DA0 |
| 0x54 | `TIMER_SET` | 2/0 | 在计时器索引写基值，并记录当前 `timeGetTime()` | 0x41F130 → 0x43F7B0 |
| 0x55 | `TIMER_BRANCH_WHILE_BELOW` | 3/0 | 若指定计时器的当前值小于阈值，则令 `IP -= distance`；总是返回让出 | 0x41F170 |
| 0x56 | `TIMER_GET_ELAPSED` | 1/1 | 读取 `base - start_tick + timeGetTime()` 并压栈 | 0x41F1D0 → 0x43F7E0 |
| 0x57 | `NAMEPLATE_SET_TARGET` | 3/0 | 写姓名牌目标槽 DWORD 与两个 WORD 坐标 | 0x41D670 |
| 0x58 | `MESSAGE_SET_DWORD_SLOT` | 2/0 | 执行 `dword_53BD38[index] = value` | 0x41D8B0 |
| 0x59 | `CHOICE_SET_TIMEOUT_60HZ` | 1/0 | 把 60 Hz tick 数换算为毫秒，写超时值并记录当前 tick | 0x41D9F0 |
| 0x5A | `CURSOR_LOAD` | 1/0 | 非空路径从资源根加载光标文件；空路径加载 Win32 资源 107 | 0x41CBE0 |
| 0x5B | `CURSOR_UNLOAD` | 1/0 | 卸载当前光标；弹出的参数不参与调用 | 0x41CCE0 → 0x439AD0 |
| 0x5C | `BUTTON_SET_MODE` | 2/0 | `group=0` 在 NORMAL/DEMO/DEMO_SKIP 间切换并更新按钮层；非零 group 只写备用模式值 | 0x41CD20 → 0x404CD0 |
| 0x5D | `BUTTON_LAYER_CONFIGURE` | 6/0 | 清空指定按钮层记录，按模式 1/2 写矩形或坐标字段并启用/禁用层 | 0x41CD60 |
| 0x5E | `BUTTON_LAYER_SET_PAIR` | 4/0 | 在按钮层记录的指定子索引写两个 DWORD 字段 | 0x41CE30 |
| 0x5F | `BUTTON_LAYER_GET_ACTIVE` | 0/1 | 返回活动层索引；结果不在 13–28 时压入 -1 | 0x41CE90 |
| 0x60 | `SYSTEM_FLAG_GET` | 1/1 | 读取系统存档位数组 `dword_565468` 的指定位 | 0x41E240 → 0x4105A0 |
| 0x61 | `SYSTEM_FLAG_SET_RANGE` | 4/0 | 对闭区间 `[first,last]` 设置系统存档位；第四参数非零时保存 `system.sav` | 0x41E290 |
| 0x62 | `SYSTEM_VALUE_GET` | 1/1 | 读取系统存档整数数组 `dword_565568[index]` | 0x41E2F0 → 0x410630 |
| 0x63 | `SYSTEM_VALUE_SET_RANGE` | 4/0 | 对闭区间写系统存档整数值；第四参数非零时保存 `system.sav` | 0x41E340 |
| 0x64 | `GAME_FLAG_GET` | 1/1 | 读取游戏存档位数组 `dword_55826C` 的指定位 | 0x41E3A0 → 0x410650 |
| 0x65 | `GAME_FLAG_SET_RANGE` | 3/0 | 对闭区间 `[first,last]` 设置游戏存档位 | 0x41E3F0 |
| 0x66 | `GAME_VALUE_GET` | 1/1 | 读取游戏存档整数数组 `dword_55836C[index]` | 0x41E440 → 0x4106E0 |
| 0x67 | `GAME_VALUE_SET_RANGE` | 3/0 | 对闭区间 `[first,last]` 写游戏存档整数值 | 0x41E490 |
| 0x68 | `SYSTEM_DATA_SAVE` | 1/0 | 保存 `system.sav`；弹出的参数不参与保存逻辑 | 0x41E4E0 → 0x40FD10 |
| 0x69 | `GAME_DATA_SAVE_SLOT` | 1/0 | 执行保存前处理并把参数减 1 后保存 `game_%03d.sav` | 0x41E510 → 0x411640 |
| 0x6A | `GAME_SAVE_NAME_SET` | 2/0 | 把字符串复制到 `byte_53148C + index*32` | 0x41E550 |
| 0x6B | `GAME_VALUE_MAX_INDEX` | 2/1 | 在闭区间内查找最大游戏值，压入相对 `first` 的索引 | 0x41E5B0 |
| 0x6C | `GAME_VALUE_MIN_INDEX` | 2/1 | 在闭区间内查找最小游戏值，压入相对 `first` 的索引 | 0x41E630 |
| 0x6E | `GAME_VALUE_SET_PAIR` | 3/0 | 写 `value[index]` 与 `value[index+1]` | 0x41E6B0 |
| 0x6F | `GAME_VALUE_SET_TRIPLE` | 4/0 | 写 `value[index]`、`value[index+1]`、`value[index+2]` | 0x41E700 |
| 0x70 | `GRAPHIC_LOAD` | 6/0 | 将 DAT/RID 图形加载到 0–63 槽；空 DAT 名替换为 `graphic.dat` | 0x41B0E0 |
| 0x71 | `GRAPHIC_UNLOAD` | 1/0 | 卸载指定图形槽；-1 遍历卸载全部 64 槽 | 0x41B1A0 |
| 0x72 | `LAYER_SET_ENABLED` | 2/0 | 按参数设置或清除图层状态 WORD 的 bit 0 | 0x41B1F0 → 0x41A180 |
| 0x73 | `LAYER_SET_TYPE` | 2/0 | 按类型位设置图层状态 bit 1 与 bit 2；非法类型调用错误路径 | 0x41B230 → 0x41A1E0 |
| 0x74 | `TWEEN_SET_FOUR_CHANNELS` | 7/0 | 忽略首参数，对后续四个通道执行立即赋值或定时插值 | 0x41B270 → 0x41A480 |
| 0x75 | `LAYER_TWEEN_POSITION` | 6/0 | 按模式应用相对坐标或水平/垂直居中修正，再设置 X/Y 插值 | 0x41B2D0 → 0x41A780 |
| 0x76 | `GRAPHIC_MOVE_SLOT` | 2/0 | 卸载目标槽，把源槽渲染状态迁移到目标槽，并清空源槽状态 | 0x41B400 |
| 0x77 | `TWEEN_SET_PRIMARY_GROUP` | 6/0 | 对选定范围的一组三个主通道执行立即赋值或定时插值 | 0x41B4E0 → 0x41A570 |
| 0x78 | `TWEEN_SET_SECONDARY_GROUP` | 6/0 | 对选定范围的一组三个次通道执行立即赋值或定时插值 | 0x41B560 → 0x41A6C0 |
| 0x79 | `LAYER_TWEEN_COLOR` | 5/0 | 根据资源类型设置一个或三个颜色通道的起止值与插值参数 | 0x41B5E0 |
| 0x7A | `LAYER_SET_BLEND_MODE` | 2/0 | 写图层 DWORD 40 为 `mode | 0x20000000` | 0x41B700 → 0x41A3A0 |
| 0x7B | `LAYER_TWEEN_COMPONENT` | 6/0 | 对指定图层组件或组件集合设置起止值与插值参数 | 0x41B740 → 0x41A3C0 |
| 0x7C | `LAYER_ATTACH_CHILD` | 9/0 | 向目标图层追加 32 字节子层记录并建立渲染链接 | 0x41B7B0 |
| 0x7D | `LAYER_SET_TEXTURE_RECT` | 5/0 | 绑定资源，重置四个通道，并写源矩形/尺寸记录 | 0x41B960 |
| 0x7E | `LAYER_RESET_TWEEN` | 2/0 | 对图层指定通道调用 tween reset | 0x41BA00 → 0x41A520 |
| 0x7F | `TWEEN_SET_FLAG2` | 3/0 | 忽略首参数，按第三参数设置或清除指定 tween 状态 WORD 的 bit 1 | 0x41BA40 → 0x41A530 |
| 0x80 | `LAYER_QUERY_RESOURCE` | 1/2 | 压入图层是否已加载；已加载时再压资源 ID，否则压 -1 | 0x41BA80 |
| 0x81 | `GRAPHIC_GET_SIZE` | 1/2 | 压入图形资源宽度与高度 | 0x41BB10 |
| 0x82 | `LAYER_GET_POSITION` | 2/2 | 模式 0 返回固定位置，非零模式返回两个位置 tween 的当前整数值 | 0x41BB70 |
| 0x84 | `LAYER_COPY_TRANSFORM` | 2/0 | 将源图层的位置、两组坐标、颜色及其他 tween 当前值复制到目标图层 | 0x41BC10 |
| 0x85 | `LAYER_SET_POSITION_PRIMARY` | 3/0 | 写图层主位置两个 WORD 并更新对应渲染对象 | 0x41BDD0 → 0x41A850 |
| 0x86 | `LAYER_SET_POSITION_SECONDARY` | 3/0 | 写图层次位置两个 WORD 并更新对应渲染对象 | 0x41BE10 → 0x41A8A0 |
| 0x87 | `GRAPHIC_SET_ACTIVE_SLOT` | 1/0 | 执行 `dword_53C180[0] = slot` | 0x41BE50 |
| 0x88 | `GRAPHIC_SET_GLOBAL_RECT` | 4/0 | 写 `dword_53C184..dword_53C190` 四个全局矩形字段 | 0x41BE80 |
| 0x89 | `SCREEN_EFFECT_ENABLE` | 2/0 | 写屏幕效果类型与参数；类型非零时创建全屏效果对象 | 0x41BED0 → 0x415C90 |
| 0x8A | `TWEEN_START` | 4/0 | 对指定 tween 设置起值、终值、时长和曲线 | 0x41BF10 → 0x447A70 |
| 0x8B | `SCREEN_EFFECT_SET_COLOR` | 3/0 | 创建类型 3 的全屏颜色效果，alpha 固定 255 | 0x41BF70 → 0x415D70 |
| 0x8C | `VFX_CREATE_PRESET` | 7/0 | 记录 VFX 槽模式；模式 0 清除，模式 1–3 创建对应预设实例 | 0x41BFB0 |
| 0x8D | `VFX_UPDATE` | 6/0 | VFX 模式为 1–3 时更新实例的五个参数 | 0x41C230 |
| 0x8E | `VFX_SET_GLOBAL` | 1/0 | 执行 `dword_54E090 = value` | 0x41C290 |
| 0x8F | `TWEEN_START_SCOPE` | 5/0 | 对单个、第一集合或第二集合中的 tween 启动同一插值 | 0x41C2C0 |
| 0x90 | `AUDIO_UNLOAD` | 1/0 | -1 执行全局音频卸载模式 1；其他值卸载通道 `id+10` | 0x41E8A0 |
| 0x91 | `AUDIO_LOAD_PLAY_EXTENDED` | 8/0 | 向通道 `id+10` 加载 DAT/RID，固定加载模式 1，并设置四个播放参数 | 0x41E8E0 |
| 0x92 | `AUDIO_LOAD_PLAY_ACTIVE` | 6/0 | 向通道 `id+10` 以加载模式 0 播放，设置一个播放参数并标记通道活动 | 0x41E960 |
| 0x93 | `VOICE_PLAY_AND_MARK` | 3/0 | 在当前语音通道加载 DAT/RID，写系统已播放标志并记录语音来源 | 0x41E9E0 |
| 0x94 | `AUDIO_SET_CHANNEL_MODE` | 2/0 | 对通道 `id+10` 调用音频模式设置函数 | 0x41EAD0 → 0x416240 |
| 0x95 | `AUDIO_SET_CHANNEL_MODE_TIMED` | 3/0 | 跳过状态或时长为 0 时立即设置；否则从当前值插值到目标值 | 0x41EB10 |
| 0x96 | `AUDIO_POLL_CHANNEL` | 1/0 | 查询通道 `id+10` 当前播放对象状态；返回值不压 VM 栈 | 0x41EB90 → 0x4162C0 |
| 0x98 | `VIDEO_PLAY` | 2/0 | 停止当前语音通道，打开资源根下的视频并配置显示区域，成功时返回让出 | 0x41E090 |
| 0xA0 | `ENGINE_SWITCH_STATE` | 1/0 | 按状态值切换主状态机；处理完成后返回让出 | 0x41EC60 |
| 0xA1 | `TWEEN_START_AND_SCALE_TIME` | 4/0 | 启动全局 tween，并用其当前值乘刷新倍率后更新引擎时间尺度 | 0x41ECC0 |
| 0xA2 | `ENGINE_SET_RUN_STATE` | 1/0 | 值 2 时重置计时对象；其他值直接写运行状态 | 0x41ED60 |
| 0xA3 | `ENGINE_QUERY_MODE` | 1/1 | selector 0/1/2 分别返回显示模式、系统模式和渲染模式；其他值返回 0 | 0x41EDA0 |
| 0xA4 | `PROCESS_RUN_WAIT` | 2/1 | 隐藏游戏窗口，运行资源根下的程序并等待；恢复窗口后压入退出码 | 0x41EE50 |
| 0xA5 | `WORK_MEMORY_REALLOC_MB` | 1/0 | 按 MB 数重新分配工作内存 | 0x41EF50 |
| 0xA8 | `DIALOG_OPEN_AND_SWITCH_STATE` | 4/0 | 配置一个状态值和最多三个字符串的对话框，切换状态机并返回让出 | 0x41EF90 → 0x403E50 |
| 0xA9 | `BUTTON_SET_STATE` | 2/0 | 按索引与模式 0/1/2 写按钮显示、启用和持久状态 | 0x41F010 → 0x404A80 |
| 0xB0 | `COMPAT_VALIDATE_STRING_MODE_YIELD` | 4/0 | 解析一个字符串，校验模式 0–3，成功时固定返回让出 | 0x41C940 |
| 0xB1 | `COMPAT_DISCARD1_FIRST` | 1/0 | 仅弹出 1 个参数 | 0x41C9B0 |
| 0xB2 | `COMPAT_DISCARD3_FIRST` | 3/0 | 仅弹出 3 个参数 | 0x41C9D0 |
| 0xB3 | `COMPAT_DISCARD1_SECOND` | 1/0 | 仅弹出 1 个参数；与 0xB1 共用处理器 | 0x41C9B0 |
| 0xB4 | `COMPAT_DISCARD3_SECOND` | 3/0 | 仅弹出 3 个参数；与 0xB2 共用处理器 | 0x41C9D0 |
| 0xB5 | `COMPAT_VALIDATE_STRING` | 3/0 | 解析第二参数字符串并计算长度，不保存结果 | 0x41CA00 |
| 0xB6 | `COMPAT_VALIDATE_SIX_ARGS_FIRST` | 6/0 | 校验参数 2 属于 -2/0/1/2；其余参数仅弹出 | 0x41CA50 |
| 0xB7 | `COMPAT_VALIDATE_SIX_ARGS_SECOND` | 6/0 | 与 0xB6 执行相同校验，共用处理器 | 0x41CA50 |
| 0xB8 | `COMPAT_VALIDATE_SIX_ARGS_THIRD` | 6/0 | 与 0xB6 执行相同校验，共用处理器 | 0x41CA50 |
| 0xB9 | `COMPAT_DISCARD5` | 5/0 | 仅弹出 5 个参数 | 0x41CAD0 |
| 0xC0 | `TWEEN_SET_CONTEXT` | 1/0 | 执行 `dword_53C194 = value` | 0x41C3A0 |
| 0xC1 | `TWEEN_START_PAIR_FIRST` | 6/0 | 记录同一开始时间，对两个 tween 设置目标值、时长、曲线和类型 | 0x41C3D0 |
| 0xC2 | `TWEEN_START_SINGLE` | 5/0 | 记录当前时间，对一个 tween 设置目标值、时长、曲线和类型 | 0x41C4A0 |
| 0xC3 | `TWEEN_START_PAIR_SECOND` | 6/0 | 与 0xC1 相同地启动两个 tween；独立 ID 保持原注册槽 | 0x41C530 |
| 0xC8 | `TWEEN_GET_PAIR_VALUES` | 1/2 | 读取指定记录内两个 tween 的当前整数值并依次压栈 | 0x41C600 |

未注册 ID 为 0x6D、0x83、0x97、0x99–0x9F、0xA6–0xA7、0xAA–0xAF、0xBA–0xBF、0xC4–0xC7 和 0xC9–0xFF。它们仍指向失败处理器，规范名称为 `INVALID_NATIVE_<ID>`；反汇编器必须报错。

## 8. 字符串池与文本规则

- 当前剧本字符串主要为 CP932/Shift-JIS，NUL 终止。
- `STRREF` 和 `TEXTREF` 保存的是字符串池内偏移，不是文件绝对地址。
- 普通反斜杠 0x5C 是脚本文本的一部分，反汇编时不得自动转义成其他语义。
- 文本中常见 `@L`、`@I`、`@P` 等引擎控制标记，它们仍是普通可打印字符串字节。
- 反汇编器必须从结构化字符串池提取文本，禁止对整个文件做正则文本扫描。
- 无法按选择编码安全显示的字节必须使用 `{{XX}}` 或 `{{XX:XX}}` 占位符；汇编器直接还原占位字节。
- asm 将 `STRREF/TEXTREF` 直接输出为双引号文本，例如 `CALL_SCRIPT "script.dat", 10`，不输出 `str_XXXXXXXX` 标签。
- 字符串末尾的终止 NUL 默认省略；汇编器为每次字符串引用独立建立池项并自动追加 NUL。只有字符串内容本身包含 NUL 时才写 `{{00}}`。
- 当前 281 个样本不存在未引用字符串池条目。新格式以运行语义稳定为目标，不保存原字符串槽复用关系，因此不要求重建文件与原文件逐字节一致。

## 9. 反汇编与重汇编实现约束

### 9.1 解析顺序

1. 校验内层 0x20 字节 YOX 头。
2. 读取 `code_size` 和 `string_size`。
3. 仅在 `[0, code_size)` 内按 Opcode 表线性解析。
4. 第一次扫描收集所有指令边界及控制流目标。
5. 校验所有非零静态目标都位于指令边界。
6. 第二次扫描输出标签和语义指令。
7. 按 `STRREF/TEXTREF` 偏移读取到首个 NUL，将所得文本直接内嵌到对应指令操作数。
8. 终止 NUL 不进入文本字面量；特殊内容字节继续使用 `{{XX}}` 占位符。

### 9.2 指令变体

- `CALL_NATIVE` 必须根据 Native ID 路由，记录其 pop/push 签名。
- Native 0x3F 是特殊内联变体；实际样本使用直接 Opcode 0x3F，不使用 `CALL_NATIVE 0x3F`。
- `PUSH1`–`PUSH4` 的每个操作数可独立选择 IMM、REG 或 STRREF。
- `WAIT`、算术右值和比较右值支持 IMM/REG 变体。
- 目的寄存器操作数虽然部分原生处理器未检查 kind，汇编器仍应强制 kind 0x02。

### 9.3 重定位

- `CODEADDR` 在 asm 中始终输出为标签。
- 汇编第一遍计算指令和标签新偏移，第二遍写入绝对偏移。
- `SWITCH4` 的四个槽位必须全部保留，包括值为 0 的槽位。
- 汇编器按代码中字符串字面量的出现顺序重建字符串池，每次引用生成独立 NUL 终止项，并更新所有 `STRREF/TEXTREF`。
- 该模式不保存原字符串槽共享关系；验收标准为重建后二次反汇编文本一致，而不是原始二进制哈希一致。

## 10. 全量样本验证结果

对当前 `script.dat` 的 281 个内层脚本执行了基于本表的线性解码：

- 解析指令总数：337,038。
- 未识别 Opcode：0。
- 未覆盖字节：0。
- 静态控制流目标：594。
- 非零目标未落在指令边界：0。
- 字符串引用：86,234。
- 越界字符串引用：0。
- 指向字符串中间的引用：0。
- Native 子 ID 均属于已注册集合。

当前样本实际直接使用的高位 Opcode 只有 0x3F；其他 Native 均通过 `CALL_NATIVE` 调用。

纯文本内嵌工具对全部 281 个脚本完成 `binary -> asm -> rebuild -> asm` 验证，二次 asm 文本差异为 0。输入脚本按文件头 `YOX\0` 魔数识别，与文件扩展名无关。

## 11. 未定义 Opcode 的校正流程

如果新版本脚本出现未记录 Opcode 或 Native ID：

1. 立即停止反汇编并报告 RID、代码偏移和上一条已解析指令。
2. 首先复核上一条指令的变长规则，避免把操作数误识别成 Opcode。
3. 在 EXE 的 `sub_441FC0` 调度表初始化和 `sub_4400C0` 注册调用中查找目标槽位。
4. 逆向对应处理器对 IP、栈和寄存器的访问，确定精确长度和栈签名。
5. 更新本文档与 `opcodelist.cs` 后，重新对全部脚本执行边界、跳转和字符串引用验证。
6. 在全量验证通过前，不得使用 `.byte` 静默吞掉未登记指令。
