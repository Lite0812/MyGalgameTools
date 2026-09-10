# Kogado .kgo VM 分析

## 1. 范围与证据等级

本文根据 Inugami.exe 的静态反编译/反汇编结果，以及工作目录中的 script/Scene.tbl、script/*.kgo 和 Script.pak 样本整理。文中使用两种证据等级：

- **EXE 已确认**：可以在反编译函数或汇编指令中直接观察到的读取、边界检查、字段访问和运算。
- **行为推断**：由多个调用点和样本布局推导出的名称或用途，尚未通过调试器、完整符号或修改样本运行确认。

当前分析足以作为 opcodelist.py、disassembler.py、assembler.py 的格式真值源。0x40..0xBA 的有效宿主指令已通过 EXE 内置元数据表取得官方名称、参数数和返回值数，并与每个 handler 的机器行为交叉核对；仍不确定的只是少数字段的业务含义，而不是 opcode 身份或指令边界。

## 2. 结论摘要

1. VM 是 **32 位整数栈式 VM**。指令流位于 .kgo 主段，PC 是主段内相对偏移。
2. 每条指令的 opcode 是 **uint16 little-endian**，不要求 4 字节对齐。只有 0x0001、0x0003、0x0004、0x0005 带 4 字节内联操作数，长度为 6；其余 dispatch 指令长度为 2。
3. 跳转偏移是 **相对于指令起始 PC 的有符号 int32**：target = instruction_pc + rel。
4. Scene.tbl 和 .kgo 都通过资源/VFS 对象打开。反编译代码中没有把它们直接写成 CreateFileA("script\\\\...")；裸文件名由资源层映射到 loose 文件或 pak。
5. Scene.tbl 按名称建立哈希索引，记录中的脚本 ID 决定 scr%05d.kgo；记录中的入口偏移再定位 .kgo 主段函数。
6. dispatch 表位于 EXE 数据地址 0x599448，每项 12 字节。0x00..0xBA 共 187 个槽，其中 150 个非空、37 个为空。扩展元数据表位于 0x598890，以 opcode-0x40 为索引，每项为 `name_ptr, pop_count, push_count` 三个 uint32；因此 0x40..0xBA 的 107 条有效宿主指令均已有官方语义名和确定栈签名。

## 3. Scene.tbl 的加载与索引

### 3.1 调用链（EXE 已确认）

初始化函数 [42754C.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/42754C.c) 执行以下步骤：

1. sub_4C852C(..., "Scene.tbl") 构造资源路径字符串。
2. sub_4BF95C(...) 从资源管理器取得流对象；空返回表示资源不存在或无法打开。
3. 通过流对象 vtable 的 +0x0C 方法读取 4 字节魔数，必须等于 IN10。
4. 再读一个 uint32 长度和一个 uint32 记录数。
5. Inugami_7(dword_5909F4, length) 分配缓冲区，并从当前流位置（偏移 12）读取 length 字节。
6. 逐记录扫描缓冲区。记录名称位于记录起始处 +0x16，通过 sub_4C852C 转成字符串，再以 sub_4C94F0 得到哈希键，调用 sub_4C2CC4(a1 + 8, hash, record_offset) 建立名称索引。

sub_4C852C 本身只是字符串对象构造；sub_4C0170 是资源存在性检查；真正的介质读取由 dword_590A00/dword_5909F4 资源对象及其流接口完成。因此，样本位于 script\\Scene.tbl 只能说明资源挂载后的路径，不足以断言运行时一定调用 Win32 文件 API。

静态导出中能直接看到 Scene.tbl 和 scr%05d.kgo 两个资源名，但没有足够证据证明 EXE 必然直接打开工作目录中的 Script.pak；pak、解包目录或其他挂载介质都可以由同一资源接口提供。

### 3.2 Scene.tbl 外层格式

样本 script/Scene.tbl 长度为 2566 字节，记录数为 91，长度字段为 2554；2554 正好是从文件偏移 12 开始的记录区长度。

| 文件偏移 | 类型 | 含义 | 证据 |
|---:|---|---|---|
| 0x00 | char[4] | IN10 | 42754C.c 读取并比较 |
| 0x04 | uint32 | 记录区长度（从偏移 0x0C 读取） | 42754C.c 的 v38 |
| 0x08 | uint32 | 记录数 | 42754C.c 的 v37 |
| 0x0C | bytes | 第一个记录起点 | 读完上述 12 字节后开始 |

记录区没有独立的偏移表；记录按 record_size 顺序串联。实现应检查所有记录尺寸之和等于长度字段，避免把尾部误判为记录。

### 3.3 单条 Scene.tbl 记录

以下字段位置相对于记录起点。名称以 NUL 结束，记录总长度按样本表现为偶数字节；不足部分是对齐/记录尾部。

| 记录偏移 | 类型 | 当前解释 | 证据等级 |
|---:|---|---|---|
| +0x00 | uint16 | 记录总长度；加载循环用它递增游标 | EXE 已确认 |
| +0x02 | uint32 | .kgo 脚本 ID | sub_428064 比较 record+2 与 VM 对象 +0x20 |
| +0x06 | uint16 | 未命名类型/类别字段 | 行为推断 |
| +0x08 | uint16 | 未命名事件键/元数据 | 行为推断 |
| +0x0A | uint32 | 主段记录相对偏移，传给 sub_4291D8 | EXE 已确认字段被读取；样本多数为 0 |
| +0x0E | uint16 | 场景初始化参数 A | EXE 已确认传给 sub_405EE4 |
| +0x10 | uint16 | 场景初始化参数 B | EXE 已确认传给 sub_405F20 |
| +0x12 | uint16 | 场景初始化参数 C；为 0 时清理对应状态 | EXE 已确认 |
| +0x14 | uint16 | 场景初始化参数 D | EXE 已确认传给 sub_405FF4 |
| +0x16 | char[] | NUL 结尾名称，如 A01_01、GameStart | EXE 已确认 |

sub_429110 会遍历主段记录并按 record+0x0A 的名称查找函数。sub_4C30B8 使用哈希桶和链表完成 Scene.tbl 名称查找，哈希冲突不会改变原始记录顺序。

### 3.4 从场景名到 .kgo 入口

[428064.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428064.c) 是场景/事件启动入口：

1. 将传入名称哈希后在 a1+8 的索引中查找记录。
2. 若记录的脚本 ID 与当前脚本相同则继续；否则调用 sub_42855C 切换脚本。
3. 读取记录的 +0x0A，交给 sub_4291D8 转成主段代码入口。
4. 设置全局 dword_5DB7E8（脚本 ID）、dword_5DB7EC（相关段状态）和 dword_5DB7F0（下一条 PC），清空停止/暂停标志并初始化 VM。

sub_4291D8 的精确公式为：

~~~text
record_offset == -1 -> -1
code_entry = main_buffer + record_offset + 0x0A
                         + uint16(main_buffer + record_offset + 0x04)
~~~

## 4. .kgo 的加载与外层格式

### 4.1 加载调用链（EXE 已确认）

[42855C.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/42855C.c) 和 [428270.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428270.c) 都使用同一套流程：

~~~text
script_id
  -> sprintf("scr%05d.kgo", script_id)
  -> sub_4C859C / sub_4C0170      资源路径与存在性检查
  -> sub_4BF95C                   打开资源流
  -> stream.read(..., 64)         读取 IN10 头
  -> stream.seek(offset) + read   读取三个段
~~~

切换脚本时，旧的主段、第二段、第三段和第三段指针表会先释放。新脚本读取失败会返回失败，不会把旧脚本伪装成新脚本。

Inugami_7（[40A004.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/40A004.c)）负责分配并清零缓冲区，然后交给 sub_4C6370 资源复制/处理。该函数支持资源上下文回调；仅凭静态代码不能把它确定命名为解密或压缩算法。当前样本的段内容可直接按下述格式解析。

### 4.2 64 字节 IN10 头

下表是 .kgo 头部字段；所有整数均为 little-endian。头部被原样读入脚本对象 +0x24，所以对象中的段字段正好对应头部偏移。

| 偏移 | 类型 | 含义 | 证据 |
|---:|---|---|---|
| 0x00 | char[4] | IN10 | 42855C.c 读取 64 字节后由资源对象校验/使用 |
| 0x04 | uint32 | 文件总长度；样本中等于实际文件长度 | 观察样本；加载器保留但未单独比较 |
| 0x08 | uint32 | 脚本 ID | sub_42855C 设置对象 +0x20 并用于切换 |
| 0x0C | uint32 | checksum/version-like 字段 | 被 sub_404C8C 使用；确切算法未确认 |
| 0x10 | uint32 | 段 0 文件偏移；样本为 0x40 | 观察样本；当前 VM 加载路径未消费 |
| 0x14 | uint32 | 段 0 字节数；样本为 0x00/0x1A/0x1C/0x1E/0x20 | 观察样本；用途未确认 |
| 0x18 | uint32 | 段 0 记录数；样本为 0 或 1 | 观察样本；用途未确认 |
| 0x1C | uint32 | 主段文件偏移 | 读取主段前作为 seek 偏移 |
| 0x20 | uint32 | 主段字节数 | 分配并读取主段 |
| 0x24 | uint32 | 主段记录数 | 主段记录遍历上限 |
| 0x28 | uint32 | 第二段文件偏移 | 读取第二段前作为 seek 偏移 |
| 0x2C | uint32 | 第二段字节数 | 分配并读取第二段 |
| 0x30 | uint32 | 第二段记录数/相关计数 | 头部字段存在；CALL 的完整索引约定未确认 |
| 0x34 | uint32 | 第三段文件偏移 | 读取第三段前作为 seek 偏移 |
| 0x38 | uint32 | 第三段字节数 | 分配并读取第三段 |
| 0x3C | uint32 | 第三段记录数 | 分配指针表并建立第三段字符串索引 |

以 script/scr00002.kgo 为例：主段 0x60/0x7080、第二段 0x70E0/0x3F、第三段 0x7120/0xE292，第三段记录数为 0x66C。

头部 0x10..0x18 在多数样本中描述文件偏移 0x40 的一条短记录。该记录的布局与 Scene.tbl 记录相似，并重复脚本 ID/名称；当前展示的 VM 加载函数只按 0x1C..0x3C 读取主段、第二段和第三段，所以段 0 的消费者尚未定位。重建器必须把段 0 和头部未知字段原样保留。

### 4.3 主段记录与代码边界

主段由 header[0x24] 个记录串联。记录格式相对于记录起点如下：

| 偏移 | 类型 | 含义 |
|---:|---|---|
| +0x00 | uint32 | 记录总长度；sub_429110 用它递增记录游标 |
| +0x04 | uint16 | 名称区到代码起点的相对偏移 |
| +0x06 | uint32 | `aux_hint`：编译器辅助/边界提示字段；运行时取指未见读取，不能作为代码起点或结束判据 |
| +0x0A | char[] | NUL 结尾函数名 |
| +0x0A + uint16(+0x04) | bytes | VM 代码起点 |
| +uint32(+0x06) | bytes | 辅助边界提示位置；实际代码结束需按入口可达控制流（RET/EXIT 及合法跳转目标）确定，之后才是记录尾部/事件元数据 |

例如 scr00002.kgo 的第一条记录从主段偏移 0 开始，记录长度 0x7080，名称偏移 0x0C，因此代码从记录相对偏移 0x16 开始；+0x06 的原始字段值 0x7062 落在结尾 PUSH 的操作数字节中，入口可达代码实际延伸到 RET（约 0x7078）。反编译得到的运行时取指路径只使用主段基址、记录偏移、+0x04 名称偏移和主段总长度，没有读取记录 +0x06；因此这里将 +0x06 命名为 `aux_hint`（辅助边界提示）而不是代码结束指针，不能机械地用它截断代码，也不能在 RET/EXIT 后继续把尾部当作 opcode。

对 `scr00501.kgo` 的 `Func_yanari_ima`、`Func_yanari_kyakuma`、`Func_yanari_tsukumo` 三条记录，`aux_hint=0x18` 甚至早于由 `0x0A + name_offset` 得到的代码入口（分别为 0x1A、0x1E、0x1E）。从入口按官方 opcode 长度解码都能得到完整的 `PUSH -> SetBG -> PUSH -> SetWeather -> PUSH -> RET`，RET 后仅有零填充。这是字段不应被视为代码起点/终点的直接样本；反汇编器只在这种“入口可达并以 RET/EXIT 终止、其后全零”的条件下恢复代码，其余无法确认的记录才保留为 `.record_data`。

### 4.4 第二段

样本第二段由短记录串联。CALL 不是按“第几个记录”索引，而是从栈中取得第二段内的**字节偏移**，再读取该位置的变体记录：

~~~text
uint16 record_size
uint16 type_or_callback_kind
union payload {
    // kind == 1：同脚本调用
    uint32 main_record_offset;
    // kind == 2：跨脚本/命名回调
    char nul_terminated_target_name[];
}
~~~

kind 1 保存当前 28 字节调用帧，并把 payload 的主段记录偏移经 sub_4291D8 换算为入口 PC。kind 2 把 payload 解释为目标名，解析 `script::function` 形式，必要时切换 `.kgo`，再按名称查找主段记录并进入；失败或其他 kind 会设置停止标志。scr00002.kgo 中可见 `scr00501::Func_header`、`scr00501::Func_footer` 以及 `A01_03`。反汇编器将同脚本 kind 1 目标格式化为 `@main::函数名` 标签，跨脚本目标保留脚本/函数名称；仅在无法解析为标签时才退回 `@main+0x...` 数值形式。汇编器据此回填第二段调用记录，并在主段重排后更新 kind 1 的记录偏移。

### 4.5 第三段字符串表

第三段是 uint16 record_size + NUL 结尾字符串的串联表。加载器为每条记录写入指针：

~~~text
pointer[i] = third_buffer + record_offset + 2
record_offset += uint16(third_buffer + record_offset)
~~~

因此 VM 的正数字符串引用是 **1-based**，引用 n 取 pointer[n-1]。第三段记录尺寸必须参与边界检查，不能只靠搜索 NUL。

## 5. VM 对象与执行循环

### 5.1 关键状态（EXE 已确认）

| 脚本对象偏移 | 结构/含义 |
|---:|---|
| +0x20 | 当前脚本 ID |
| +0x24..+0x63 | 64 字节 .kgo 头副本 |
| +0x40/+0x44/+0x48 | 主段偏移、大小、记录数（由头部 0x1C/0x20/0x24 映射） |
| +0x4C/+0x50/+0x54 | 第二段偏移、大小、记录数 |
| +0x58/+0x5C/+0x60 | 第三段偏移、大小、记录数 |
| +0x64/+0x68/+0x6C | 主段、第二段、第三段缓冲区指针 |
| +0x70 | 第三段字符串指针表 |
| +0x78 | handler 计算出的下一条 PC |
| +0x9C8 | 栈顶 peek 使用的槽区起点 |
| +0x9CC..+0xDCB | 256 个 int32 值栈槽 |
| +0xDCC | 栈边界/容量检查字段 |
| +0xDD0 | 当前值栈元素数 |
| +0xDE4.. | 64 个 28 字节调用帧 |
| +0x14E4 | 当前调用帧数 |

sub_406218、sub_4062D8、sub_406398 分别实现 push、pop、peek；sub_4061EC 清空 0x400 字节值栈并将栈计数清零。sub_406480/sub_406570 压入/弹出调用帧，帧上限为 64。

全局 dword_5DB7F0 是当前 PC，byte_5DB7F4 是停止/结束/错误标志，byte_5DB7F5 是另一类暂停状态。428AD0.c 显示主循环在“值栈/事件可执行”和“等待/输入事件”两种状态之间切换。

### 5.2 单步解码流程

[428C20.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428C20.c) 的逻辑可化为：

~~~text
if stop or pause: return
if current_script_id < 0 or pc >= main_size:
    stop = 1
    return

opcode = uint16_le(main_buffer + pc)
next_pc = pc + instruction_length(opcode)
entry = dispatch_table + opcode * 12
handler = entry.handler
if handler == 0 and entry.field2 == 0 and entry.field3 == 0:
    raise GameErr
handler(vm)
pc = vm.next_pc
~~~

opcode 表项结构为 12 字节：handler、field2、field3。目前转储中 field2/field3 均为 0，但仍应保留这两个字段，因为 dispatcher 会检查三者是否全 0。

## 6. 指令编码与长度

[4273EC.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/4273EC.c) 是唯一的长度函数：

~~~python
def instruction_length(opcode: int) -> int:
    if opcode < 0x10 and opcode in (0x01, 0x03, 0x04, 0x05):
        return 6
    return 2
~~~

所以：

- 所有 opcode 字段是 uint16_le。
- PUSH/JMP/JZ/JNZ 的布局是 uint16 opcode + uint32 operand。
- 6 字节指令不按 4 字节对齐；下一条指令紧跟在 pc+6。
- 其他已注册 dispatch opcode（包括 0x40..0xBA 宿主扩展）长度均为 2，操作数主要通过值栈、脚本对象或事件状态传递。
- 当前 EXE dispatch 中没有额外的子 opcode 字段或按操作数字节切换的隐式变体；LK/UL、Play/PlayQ 等差异由独立 opcode 及其官方名称表达。
- dispatch 表只覆盖 `0x00..0xBA`；`0xBB..0xFFFF` 没有官方条目，解析器必须将其报告为越界/未定义 opcode，而不能把邻接数据当作 handler。

## 7. 跳转、调用和返回

### 7.1 相对跳转

0x03/0x04/0x05 的 handler 分别为 0x4298B4、0x4298D4、0x429904，通过 [428F1C.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428F1C.c) 读取有符号 32 位偏移。基准是 **该指令的起始 PC**：

~~~python
target = instruction_pc + read_i32_le(code, instruction_pc + 2)
~~~

- JMP：无条件跳转，不改变栈。
- JZ：先 pop 条件；条件为 0 时跳转，否则执行下一条。
- JNZ：先 pop 条件；条件非 0 时跳转，否则执行下一条。

反汇编器必须为目标生成 loc_XXXXXXXX 标签。汇编器必须在最终布局阶段使用 target_offset - instruction_start 重算相对值；不能复用反汇编文本中的旧整数偏移。

### 7.2 CALL/RET

0x06 CALL 长度为 2，从值栈 pop 一个值，并通过第二段记录/回调表进入调用帧逻辑；其完整目标解析依赖缺失的 0x429934 函数反编译结果，当前只能确认“栈索引 + 第二段记录类型分支”。

0x0E RET 通过 sub_406570 弹出 28 字节调用帧，恢复脚本 ID、段状态、PC 以及参数/上下文字段。无调用帧时将 byte_5DB7F4 置 1。返回值是否固定留在值栈上，要结合具体 CALL 约定再确认。

0x0F EXIT 直接将 byte_5DB7F4 置 1。

## 8. 字符串引用表示

[428FD4.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428FD4.c) 将值栈中的 int32 解释为三类引用：

| 值 | 解释 |
|---:|---|
| 0 | 空字符串 |
| value & 0x80000000 != 0 | 本地字符串槽，索引为 value & 0x7FFFFFFF |
| 1 <= value <= third_record_count | 第三段字符串，按 1-based 指针表索引 |
| 其他 | GameErr/非法引用 |

本地字符串通过 sub_405A38 取得。该函数显示 VM 有 16 个本地字符串槽，每槽容量约 0x80 字节。因而工具中的 strref 不能简单打印成普通十进制整数，至少应区分 empty、local_str(index) 和 third_segment_str(index)。

## 9. 全量核心 opcode（0x00..0x3F）

栈记号约定：`a` 是第一次 pop 的栈顶值，`b` 是第二次 pop 的值；`m -> n` 表示消费 m 个值并产生 n 个值。除 PUSH/JMP/JZ/JNZ 外均无内联操作数。核心有效 opcode 名称直接采用 EXE 官方字符串；`INVALID_xx` 仅用于无官方名称的空 dispatch 槽。它们仍有可判定的 2 字节编码边界，但 dispatch 三字段全为 0，执行时由 428C20.c 抛出 GameErr，不是可汇编的有效指令。

| Opcode | 官方名称 | byte_pattern / 长度 | operand_schema | 栈 | 精确定义 | 佐证 |
|---:|---|---|---|---:|---|---|
| 0x00 | NOP | `00 00` / 2 | `[]` | 0 -> 0 | 不改变 VM 状态。 | 0x429884 |
| 0x01 | PUSH | `01 00 ?? ?? ?? ??` / 6 | `[imm32_le]` | 0 -> 1 | 读取 pc+2 的有符号 int32 并压栈。 | 0x429888、4273EC.c |
| 0x02 | POP | `02 00` / 2 | `[]` | 1 -> 0 | 弹出栈顶并丢弃。 | 0x4298A8 |
| 0x03 | JMP | `03 00 ?? ?? ?? ??` / 6 | `[rel_i32_le@instruction_start]` | 0 -> 0 | `next_pc = pc + rel`。 | 0x4298B4、428F1C.c |
| 0x04 | JZ | `04 00 ?? ?? ?? ??` / 6 | `[rel_i32_le@instruction_start]` | 1 -> 0 | pop a；a==0 时 `next_pc=pc+rel`，否则 pc+6。 | 0x4298D4 |
| 0x05 | JNZ | `05 00 ?? ?? ?? ??` / 6 | `[rel_i32_le@instruction_start]` | 1 -> 0 | pop a；a!=0 时 `next_pc=pc+rel`，否则 pc+6。 | 0x429904 |
| 0x06 | CALL | `06 00` / 2 | `[]` | 1 -> 0/1 | pop 第二段字节偏移。kind 1 保存调用帧并进入同脚本主段记录；kind 2 按名称解析/切换脚本后进入目标；返回值由 RET 约定恢复。非法偏移、kind 或目标使 VM 停止。 | 0x429934..0x429C9A |
| 0x07 | INVALID_07 | `07 00` / 2 | `[]` | - | 空 dispatch 槽，执行报错。 | 0x599448 表项全 0 |
| 0x08 | DUP | `08 00` / 2 | `[]` | 1 -> 2 | `[...,a] -> [...,a,a]`。 | 0x429C9C |
| 0x09 | SWAP | `09 00` / 2 | `[]` | 2 -> 2 | `[...,b,a] -> [...,a,b]`。 | 0x429CB8 |
| 0x0A | DUP2 | `0A 00` / 2 | `[]` | 2 -> 3 | `[...,b,a] -> [...,b,b,a]`，只增加一个值。 | 0x429CF0 |
| 0x0B | SWAP2 | `0B 00` / 2 | `[]` | 3 -> 3 | `[...,c,b,a] -> [...,b,c,a]`，栈顶 a 不变。 | 0x429D28 |
| 0x0C | INVALID_0C | `0C 00` / 2 | `[]` | - | 空 dispatch 槽，执行报错。 | 表项全 0 |
| 0x0D | INVALID_0D | `0D 00` / 2 | `[]` | - | 空 dispatch 槽，执行报错。 | 表项全 0 |
| 0x0E | RET | `0E 00` / 2 | `[]` | 条件式 | 弹出 28 字节调用帧，恢复脚本、段、PC 和上下文；调用层声明返回值时从当前栈取一值，否则压入 0；无帧则停止。 | 0x429D78、406570.c |
| 0x0F | EXIT | `0F 00` / 2 | `[]` | 0 -> 0 | 设置停止标志 `byte_5DB7F4=1`。 | 0x429E20 |
| 0x10 | LNOT | `10 00` / 2 | `[]` | 1 -> 1 | pop a，压入 `a==0 ? 1 : 0`。 | 0x429E28 |
| 0x11 | INVALID_11 | `11 00` / 2 | `[]` | - | 空 dispatch 槽，执行报错。 | 表项全 0 |
| 0x12 | INVALID_12 | `12 00` / 2 | `[]` | - | 空 dispatch 槽，执行报错。 | 表项全 0 |
| 0x13 | MUL | `13 00` / 2 | `[]` | 2 -> 1 | pop a,b，压入 int32 `b*a`。 | 0x429E58 |
| 0x14 | DIV | `14 00` / 2 | `[]` | 2 -> 1 | pop a,b；a!=0 压入 `b/a`；a==0 时 b>=0 压 -1，b<0 压 0。 | 0x429E84 |
| 0x15 | REM | `15 00` / 2 | `[]` | 2 -> 1 | pop a,b；a!=0 压 `b%a`，否则压 0。 | 0x429EC8 |
| 0x16 | ADD | `16 00` / 2 | `[]` | 2 -> 1 | pop a,b，压入 `b+a`。 | 0x429F04 |
| 0x17 | SUB | `17 00` / 2 | `[]` | 2 -> 1 | pop a,b，压入 `b-a`。 | 0x429F2C |
| 0x18 | LT | `18 00` / 2 | `[]` | 2 -> 1 | 压入 `b<a` 的 0/1。 | 0x429F58 |
| 0x19 | LE | `19 00` / 2 | `[]` | 2 -> 1 | 压入 `b<=a` 的 0/1。 | 0x429F8C |
| 0x1A | GT | `1A 00` / 2 | `[]` | 2 -> 1 | 压入 `b>a` 的 0/1。 | 0x429FC0 |
| 0x1B | GE | `1B 00` / 2 | `[]` | 2 -> 1 | 压入 `b>=a` 的 0/1。 | 0x429FF4 |
| 0x1C | EQ | `1C 00` / 2 | `[]` | 2 -> 1 | 压入 `b==a` 的 0/1。 | 0x42A028 |
| 0x1D | NE | `1D 00` / 2 | `[]` | 2 -> 1 | 压入 `b!=a` 的 0/1。 | 0x42A05C |
| 0x1E | LAND | `1E 00` / 2 | `[]` | 2 -> 1 | 将 a、b 各自布尔化，压入逻辑与结果。 | 0x42A090 |
| 0x1F | LOR | `1F 00` / 2 | `[]` | 2 -> 1 | 将 a、b 各自布尔化，压入逻辑或结果。 | 0x42A0D8 |
| 0x20 | SETF | `20 00` / 2 | `[]` | 2 -> 0 | pop value,index；若 index<0x200，设置 VM 512-bit 标志位为 bool(value)。 | 0x42A120、4055B4.c |
| 0x21 | GETF | `21 00` / 2 | `[]` | 1 -> 1 | pop index；读取 VM 512-bit 标志，越界为 0，压入 0/1。 | 0x42A14C、4055F8.c |
| 0x22 | SETSF | `22 00` / 2 | `[]` | 2 -> 0 | pop value,index；若 index<0x40，设置全局 64-bit 标志位。 | 0x42A178、404AA0.c |
| 0x23 | GETSF | `23 00` / 2 | `[]` | 1 -> 1 | pop index；读取全局 64-bit 标志，越界为 0，压入 0/1。 | 0x42A1A4、404ADC.c |
| 0x24 | SETV | `24 00` / 2 | `[]` | 2 -> 0 | pop value,index；若 index<64，写 VM int32 变量槽。 | 0x42A1D0、405678.c |
| 0x25 | GETV | `25 00` / 2 | `[]` | 1 -> 1 | pop index；读取 64 槽 VM int32 变量，越界压 0。 | 0x42A1F8、405688.c |
| 0x26 | SETSV | `26 00` / 2 | `[]` | 2 -> 0 | pop value,index；若 index<8，写全局 int32 变量槽。 | 0x42A224、404B20.c |
| 0x27 | INCSV | `27 00` / 2 | `[]` | 1 -> 1 | EXE 名称为 INCSV，但机器行为是 pop index，读取全局 8 槽 int32 并压回；越界为 0，没有递增。 | 0x42A24C、404B30.c |
| 0x28 | SETSTR | `28 00` / 2 | `[]` | 2 -> 0 | pop source_ref,dest_slot；按 428FD4 解析 source_ref，并写入 16 个本地字符串槽之一。 | 0x42A278、4056C4.c |
| 0x29 | GETSTR | `29 00` / 2 | `[]` | 1 -> 1 | EXE 名称为 GETSTR；实际只压入 `value & 0x80000000`，结果为 0 或 INT_MIN，不读取字符串内容。 | 0x42A30C |
| 0x2A | SETRES | `2A 00` / 2 | `[]` | 1 -> 0 | pop value，写 VM 当前结果字段（+0x96C）。 | 0x42A32C、405ACC.c |
| 0x2B | GETRES | `2B 00` / 2 | `[]` | 0 -> 1 | 压入 VM 当前结果字段（+0x96C）。 | 0x42A348、405AD4.c |
| 0x2C | GETCF | `2C 00` / 2 | `[]` | 1 -> 1 | pop index；读取 VM +0x41 起的 64-bit 上下文标志，越界为 0。 | 0x42A364、405630.c |
| 0x2D | GETCV | `2D 00` / 2 | `[]` | 1 -> 1 | pop index；读取 VM +0x14C 起的 8 个 int32 上下文槽，越界为 0。 | 0x42A390、40569C.c |
| 0x2E | GETARG | `2E 00` / 2 | `[]` | 0 -> 1 | 压入 VM 当前参数/上下文字段（+0x970）。 | 0x42A3BC、405AE4.c |
| 0x2F | INVALID_2F | `2F 00` / 2 | `[]` | - | 空 dispatch 槽，执行报错。 | 表项全 0 |
| 0x30 | SCNCHG | `30 00` / 2 | `[]` | 1 -> 0 | pop 第二段字节偏移，从记录 +4 取得场景/事件名，调用 428064.c 查表并切换脚本/入口；失败时停止。 | 0x42A3D8 |
| 0x31 | INVALID_31 | `31 00` / 2 | `[]` | - | 空 dispatch 槽，执行报错。 | 表项全 0 |
| 0x32 | INVALID_32 | `32 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x33 | INVALID_33 | `33 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x34 | INVALID_34 | `34 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x35 | INVALID_35 | `35 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x36 | INVALID_36 | `36 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x37 | INVALID_37 | `37 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x38 | INVALID_38 | `38 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x39 | INVALID_39 | `39 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x3A | INVALID_3A | `3A 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x3B | INVALID_3B | `3B 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x3C | INVALID_3C | `3C 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x3D | INVALID_3D | `3D 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x3E | INVALID_3E | `3E 00` / 2 | `[]` | - | 同上。 | 表项全 0 |
| 0x3F | INVALID_3F | `3F 00` / 2 | `[]` | - | 同上。 | 表项全 0 |

## 10. 宿主扩展 dispatch 区

### 10.1 规则

0x40..0xBA 的非空槽、官方名称和栈签名来自 EXE 数据区 `0x598890` 的扩展元数据表；handler 来自 `0x599448` dispatch 表。扩展指令均由长度函数判定为 2 字节，没有内联操作数字段；`pop/push` 是元数据中的值栈消费/产生数量。下表中的“操作数”描述这些值栈参数的约定，具体业务字段仍以 handler 的静态访问为准。

所有扩展行的字节模式均为 `<opcode:u16 little-endian>`，长度固定为 2；因此表中不再重复写出相同的 `xx 00` 模式。下表先列出全部 107 条非空扩展槽，表末另行展开 16 个空槽。

| Opcode | 官方名称 | 长度 | 操作数（值栈） | 栈 | 机器语义 | handler |
|---:|---|---:|---|---:|---|---:|
| 0x40 | Text | 2 | 字符串引用 | 1 -> 0 | 取字符串引用写入文本缓冲并更新文本状态。 | 0x42A4B4 |
| 0x41 | NewLine | 2 | 无 | 0 -> 0 | 在文本缓冲追加引擎换行并推进光标。 | 0x42A544 |
| 0x42 | NewPage | 2 | 无 | 0 -> 0 | 结束当前页并请求新页状态。 | 0x42A54C |
| 0x43 | TextShow | 2 | 无 | 0 -> 0 | 显示文本层/文本缓冲。 | 0x42A554 |
| 0x44 | TextHide | 2 | 无 | 0 -> 0 | 隐藏文本层/文本缓冲。 | 0x42A55C |
| 0x45 | TextSpeed | 2 | 速度值 | 1 -> 0 | 设置文字显示速度。 | 0x42A564 |
| 0x46 | NovelMode | 2 | 模式值 | 1 -> 0 | 设置 Novel 模式及文本行为。 | 0x42A580 |
| 0x47 | Locate | 2 | 坐标参数 | 2 -> 0 | 设置文本定位坐标。 | 0x42A5C8 |
| 0x48 | Ruby | 2 | 正文与注音引用 | 2 -> 0 | 设置正文与 Ruby 注音关联文本。 | 0x42A5F0 |
| 0x49 | TextSize | 2 | 字号 | 1 -> 0 | 设置文本字号。 | 0x42A690 |
| 0x4C | Wait | 2 | 时长值 | 1 -> 0 | 设置 VM 等待计时并暂停后续执行。 | 0x42A6A0 |
| 0x4D | KeyWait | 2 | 无 | 0 -> 0 | 进入按键/输入等待状态。 | 0x42A6C4 |
| 0x4E | PageWait | 2 | 无 | 0 -> 0 | 进入翻页等待状态。 | 0x42A6F4 |
| 0x4F | GoTitle | 2 | 无 | 0 -> 0 | 设置 VM 停止标志，由宿主流程转入标题场景。 | 0x42A728 |
| 0x50 | GoBattle | 2 | 战斗参数 | 1 -> 1 | 请求进入战斗场景；暂停结束后按元数据约定留下战斗结果。 | 0x42A730 |
| 0x51 | Select | 2 | 选项参数组 | 4 -> 1 | 创建选择项并等待用户选择，恢复执行时留下选择结果。 | 0x42A768 |
| 0x52 | ExSelect | 2 | 扩展选择参数 | 1 -> 1 | 执行扩展选择流程，恢复执行时留下选择结果。 | 0x42A980 |
| 0x55 | SkipDisable | 2 | 布尔值 | 1 -> 0 | 设置是否禁止跳过文本/事件。 | 0x42A99C |
| 0x56 | SetStage | 2 | 场景值 | 1 -> 0 | 设置当前 Stage/场景编号。 | 0x42A9B4 |
| 0x57 | Random | 2 | 上界/范围值 | 1 -> 1 | 读取范围参数，生成伪随机 int32 并压回值栈；边界细节待运行时确认。 | 0x42A9D0 |
| 0x58 | SetAlbum | 2 | 图鉴值 | 1 -> 0 | 设置 Album/回想解锁状态。 | 0x42A9FC |
| 0x59 | ShowCursor | 2 | 无 | 0 -> 0 | 显示输入光标。 | 0x42AA34 |
| 0x5A | HideCursor | 2 | 无 | 0 -> 0 | 隐藏输入光标。 | 0x42AA54 |
| 0x5B | ShowMovie | 2 | 资源引用 | 1 -> 0 | 播放指定影片资源。 | 0x42AA74 |
| 0x5C | ShowOpening | 2 | 无 | 0 -> 0 | 播放/显示 Opening。 | 0x42AAB0 |
| 0x5D | ShowEnding | 2 | 结局参数 | 1 -> 0 | 播放/显示 Ending。 | 0x42AAD8 |
| 0x5E | BacklogClear | 2 | 无 | 0 -> 0 | 清空文本回顾（Backlog）。 | 0x42AB0C |
| 0x5F | SetWeather | 2 | 天气值 | 1 -> 0 | 设置当前天气类型。 | 0x42AB18 |
| 0x60 | GetWeather | 2 | 无 | 0 -> 1 | 读取当前天气类型并压栈。 | 0x42AB34 |
| 0x61 | SetDate | 2 | 日期值 | 1 -> 0 | 设置当前日期状态。 | 0x42AB4C |
| 0x62 | GetDate | 2 | 无 | 0 -> 1 | 读取当前日期编码并压栈。 | 0x42AB74 |
| 0x63 | GetMonth | 2 | 无 | 0 -> 1 | 读取当前月份并压栈。 | 0x42AB9C |
| 0x64 | GetDay | 2 | 无 | 0 -> 1 | 读取当前日期中的日并压栈。 | 0x42ABB0 |
| 0x65 | GetWeek | 2 | 无 | 0 -> 1 | 读取当前星期值并压栈。 | 0x42ABC4 |
| 0x66 | SetTime | 2 | 时间值 | 1 -> 0 | 设置当前时间状态。 | 0x42ABDC |
| 0x67 | GetTime | 2 | 无 | 0 -> 1 | 读取当前时间编码并压栈。 | 0x42AC04 |
| 0x68 | GetHour | 2 | 无 | 0 -> 1 | 读取当前小时并压栈。 | 0x42AC2C |
| 0x69 | GetMin | 2 | 无 | 0 -> 1 | 读取当前分钟并压栈。 | 0x42AC40 |
| 0x6A | AddDay | 2 | 天数 | 1 -> 0 | 将当前日期增加指定天数。 | 0x42AC54 |
| 0x6B | AddMin | 2 | 分钟数 | 1 -> 0 | 将当前时间增加指定分钟数。 | 0x42AC7C |
| 0x6C | DateShow | 2 | 显示参数 | 1 -> 0 | 显示日期 UI。 | 0x42ACA4 |
| 0x6D | DateHide | 2 | 显示参数 | 1 -> 0 | 隐藏日期 UI。 | 0x42ACC8 |
| 0x6E | TimeShow | 2 | 显示参数 | 1 -> 0 | 显示时间 UI。 | 0x42ACEC |
| 0x6F | TimeHide | 2 | 显示参数 | 1 -> 0 | 隐藏时间 UI。 | 0x42AD10 |
| 0x70 | ShowPlace | 2 | 地点引用 | 1 -> 0 | 显示地点名称/地点 UI。 | 0x42AD34 |
| 0x75 | Voice | 2 | 资源引用 | 1 -> 0 | 播放语音资源。 | 0x42AD54 |
| 0x76 | VoiceVol | 2 | 音量 | 1 -> 0 | 设置语音音量。 | 0x42ADAC |
| 0x77 | VoicePos | 2 | 位置参数 | 2 -> 0 | 设置语音声道/空间位置。 | 0x42ADD4 |
| 0x78 | BGMPlay | 2 | 资源引用 | 1 -> 0 | 播放背景音乐。 | 0x42AE14 |
| 0x79 | BGMStop | 2 | 无 | 0 -> 0 | 停止背景音乐。 | 0x42AE4C |
| 0x7A | BGMPlayQ | 2 | 资源引用 | 1 -> 0 | 以队列模式播放背景音乐。 | 0x42AE68 |
| 0x7B | BGMStopQ | 2 | 无 | 0 -> 0 | 停止/清理背景音乐队列。 | 0x42AEA0 |
| 0x7C | BGMVol | 2 | 音量 | 1 -> 0 | 设置背景音乐音量。 | 0x42AEBC |
| 0x7D | SongPlay | 2 | 资源引用 | 1 -> 0 | 播放 Song 通道资源。 | 0x42AEE4 |
| 0x7E | SongStop | 2 | 无 | 0 -> 0 | 停止 Song 通道。 | 0x42AF10 |
| 0x7F | SongPlayQ | 2 | 资源引用 | 1 -> 0 | 以队列模式播放 Song。 | 0x42AF2C |
| 0x80 | SongStopQ | 2 | 无 | 0 -> 0 | 停止/清理 Song 队列。 | 0x42AF58 |
| 0x81 | SongVol | 2 | 音量 | 1 -> 0 | 设置 Song 通道音量。 | 0x42AF74 |
| 0x82 | SEPlay | 2 | 资源与参数 | 2 -> 0 | 播放音效资源并应用实例参数。 | 0x42AF9C |
| 0x83 | SEStop | 2 | 实例/资源标识 | 1 -> 0 | 停止指定音效实例。 | 0x42B014 |
| 0x84 | SEVol | 2 | 实例与音量 | 2 -> 0 | 设置指定音效实例音量。 | 0x42B03C |
| 0x85 | SEPos | 2 | 位置参数组 | 3 -> 0 | 设置音效实例空间位置。 | 0x42B080 |
| 0x86 | EnvPlay | 2 | 资源与参数 | 2 -> 0 | 播放环境音资源并应用实例参数。 | 0x42B0E0 |
| 0x87 | EnvStop | 2 | 实例/资源标识 | 1 -> 0 | 停止环境音实例。 | 0x42B17C |
| 0x88 | EnvPlayQ | 2 | 资源与参数 | 2 -> 0 | 以队列模式播放环境音。 | 0x42B1B0 |
| 0x89 | EnvStopQ | 2 | 实例/资源标识 | 1 -> 0 | 停止/清理环境音队列。 | 0x42B24C |
| 0x8A | EnvVol | 2 | 实例与音量 | 2 -> 0 | 设置环境音实例音量。 | 0x42B280 |
| 0x8B | EnvPos | 2 | 位置参数组 | 3 -> 0 | 设置环境音实例空间位置。 | 0x42B2C4 |
| 0x8C | RainVol | 2 | 音量 | 1 -> 0 | 设置雨声环境音音量。 | 0x42B324 |
| 0x8D | SetRainPower | 2 | 强度 | 1 -> 0 | 设置降雨强度。 | 0x42B34C |
| 0x8E | GetRainPower | 2 | 无 | 0 -> 1 | 读取降雨强度并压栈。 | 0x42B380 |
| 0x8F | SetRainLevel | 2 | 等级 | 1 -> 0 | 设置降雨显示/音效等级。 | 0x42B39C |
| 0x90 | GetRainLevel | 2 | 无 | 0 -> 1 | 读取降雨等级并压栈。 | 0x42B3DC |
| 0x91 | AddRainPower | 2 | 增量 | 1 -> 0 | 增加降雨强度。 | 0x42B3F8 |
| 0x92 | SubRainPower | 2 | 减量 | 1 -> 0 | 减少降雨强度。 | 0x42B43C |
| 0x93 | AddRainPowLv | 2 | 增量 | 1 -> 0 | 增加降雨强度等级。 | 0x42B488 |
| 0x94 | SubRainPowLv | 2 | 减量 | 1 -> 0 | 减少降雨强度等级。 | 0x42B528 |
| 0x99 | SetBG | 2 | 资源引用 | 1 -> 0 | 设置背景资源。 | 0x42B5B8 |
| 0x9A | SetWhite | 2 | 无 | 0 -> 0 | 将画面背景状态设为白色。 | 0x42B5D0 |
| 0x9B | SetBlack | 2 | 无 | 0 -> 0 | 将画面背景状态设为黑色。 | 0x42B5DC |
| 0x9C | Fade | 2 | 时长/模式 | 1 -> 0 | 执行当前画面淡入淡出。 | 0x42B5E8 |
| 0x9D | FadeBG | 2 | 背景与淡化参数 | 2 -> 0 | 对背景执行带参数淡入淡出。 | 0x42B600 |
| 0x9E | WhiteOut | 2 | 时长 | 1 -> 0 | 淡出至白场。 | 0x42B630 |
| 0x9F | BlackOut | 2 | 时长 | 1 -> 0 | 淡出至黑场。 | 0x42B658 |
| 0xA0 | FadeSpeed | 2 | 速度 | 1 -> 0 | 设置淡化速度。 | 0x42B680 |
| 0xA1 | ActionChar | 2 | 无 | 0 -> 0 | 执行角色显示队列中的动作。 | 0x42B69C |
| 0xA2 | SetShowChar | 2 | 角色显示参数组 | 4 -> 0 | 设置角色显示动作及资源、位置和效果参数。 | 0x42B6A4 |
| 0xA3 | ShowCharLK | 2 | 角色与动作参数 | 2 -> 0 | 按 LK 变体显示角色。 | 0x42B744 |
| 0xA4 | ShowCharUL | 2 | 角色与动作参数 | 2 -> 0 | 按 UL 变体显示角色。 | 0x42B794 |
| 0xA5 | SetHideChar | 2 | 角色隐藏参数组 | 3 -> 0 | 设置角色隐藏动作及参数。 | 0x42B7E4 |
| 0xA6 | HideCharLK | 2 | 角色参数 | 1 -> 0 | 按 LK 变体隐藏角色。 | 0x42B868 |
| 0xA7 | HideCharUL | 2 | 角色参数 | 1 -> 0 | 按 UL 变体隐藏角色。 | 0x42B8AC |
| 0xA8 | SetMoveChar | 2 | 角色移动参数组 | 3 -> 0 | 设置角色移动动作及参数。 | 0x42B8F0 |
| 0xA9 | MoveCharLK | 2 | 角色与动作参数 | 2 -> 0 | 按 LK 变体移动角色。 | 0x42B978 |
| 0xAA | MoveCharUL | 2 | 角色与动作参数 | 2 -> 0 | 按 UL 变体移动角色。 | 0x42B9EC |
| 0xAB | AllMoveChar | 2 | 移动参数 | 1 -> 0 | 对所有当前角色执行移动动作。 | 0x42BA60 |
| 0xAC | AllHideChar | 2 | 无 | 0 -> 0 | 隐藏所有当前角色。 | 0x42BAA8 |
| 0xAD | SetChangeChar | 2 | 角色切换参数 | 2 -> 0 | 设置角色切换动作及资源参数。 | 0x42BABC |
| 0xAE | ChangeChar | 2 | 角色切换参数 | 2 -> 0 | 执行角色资源/服装切换。 | 0x42BB04 |
| 0xAF | SetPriority | 2 | 优先级 | 1 -> 0 | 设置角色或显示层优先级。 | 0x42BB58 |
| 0xB0 | Effect | 2 | 效果引用 | 1 -> 0 | 触发显示效果资源。 | 0x42BB74 |
| 0xB5 | SpeakPos | 2 | 说话位置 | 1 -> 0 | 设置当前说话框/说话角色位置。 | 0x42BB98 |
| 0xB6 | SpeakPosAll | 2 | 无 | 0 -> 0 | 对全部说话角色应用位置状态。 | 0x42BBD4 |
| 0xB7 | SpeakClear | 2 | 无 | 0 -> 0 | 清除当前说话状态。 | 0x42BBF8 |
| 0xB8 | SpeakOn | 2 | 说话参数 | 1 -> 0 | 打开说话角色显示/标记。 | 0x42BC1C |
| 0xB9 | SpeakOff | 2 | 无 | 0 -> 0 | 关闭说话角色显示/标记。 | 0x42BC40 |
| 0xBA | SpeakChar | 2 | 角色参数 | 1 -> 0 | 设置当前说话角色。 | 0x42BC50 |
| 0x4A | INVALID_4A | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x4B | INVALID_4B | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x53 | INVALID_53 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x54 | INVALID_54 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x71 | INVALID_71 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x72 | INVALID_72 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x73 | INVALID_73 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x74 | INVALID_74 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x95 | INVALID_95 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x96 | INVALID_96 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x97 | INVALID_97 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0x98 | INVALID_98 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0xB1 | INVALID_B1 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0xB2 | INVALID_B2 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0xB3 | INVALID_B3 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |
| 0xB4 | INVALID_B4 | 2 | 无 | - | 空 dispatch 槽，执行时报错。 | 表项全 0 |

扩展元数据按 `opcode - 0x40` 索引，每项 12 字节 little-endian：`name_ptr:u32`、`pop_count:u32`、`push_count:u32`。例如 0x40 项的 `name_ptr` 指向 `Text`，其签名为 `1 -> 0`；0x50、0x51、0x52 的签名分别为 `1 -> 1`、`4 -> 1`、`1 -> 1`，这些异步流程的结果在暂停恢复时由宿主状态填回。0x57 项指向 `Random`，其签名为 `1 -> 1`。名称字符串连续存放于 `0x598F90` 起始区域，dispatch handler 则由 `0x599448 + opcode*12` 的第一字段给出。空槽的元数据/dispatch 均为零，不得当作有效扩展指令。

空槽为：

~~~text
0x31..0x3F, 0x4A..0x4B, 0x53..0x54, 0x71..0x74,
0x95..0x98, 0xB1..0xB4
~~~

区间写法仅用于汇总连续空槽；上表已把每个空槽展开为独立 `INVALID_xx` 条目，实现时也应保持独立字典项。

## 11. 样本 opcode 覆盖

对 script/*.kgo 的主段记录，从 record+0x0A+code_name_offset 进入，按入口可达控制流扫描至 RET/EXIT 终点；0x01/0x03/0x04/0x05 按 6 字节，其余按 2 字节。+0x06 字段只作辅助边界提示，部分样本会落在最后 PUSH 的操作数字节中。样本中出现了 68 个不同 opcode，全部落在上面的非空 dispatch 槽；EXE dispatch/扩展元数据则覆盖全部 150 个非空槽（核心与宿主扩展），未出现在样本中的槽仍按官方名称保留。

总计数如下，作为后续回归测试基线：

~~~text
01=82793 02=234 03=453 04=138 06=176 08=58 0B=37
16=10 18=1 1B=33 1C=104 20=6 21=20 22=7 23=10
24=23 25=1363 26=1 27=4 2A=21 2B=35 2C=7 30=22
40=47147 41=39116 42=7932 44=2203 45=8 48=269 4C=1034
4D=20092 4E=7867 4F=1 51=21 55=10 56=21 58=5 5F=163
61=1 66=79 6A=7 6B=5 6D=1 6F=1 78=363 79=322 7B=5
7C=19 7D=1 82=922 83=10 86=116 87=112 8A=172 99=739
9B=2 9C=852 9D=11 9E=38 9F=310 A1=1864 A2=1646 A5=3542
AD=204 AE=5217 AF=14 B0=130 B9=7821
~~~

## 12. 对三个工具的实现约束

### 12.1 disassembler.py

- 先验证 .kgo 的 64 字节头和三个段范围；不要把整个文件从 0x60 之后盲目当作代码。
- 头部段布局使用 `.header_segment name=... offset=... size=... count=...` 分行输出；段 0 元数据拆为 `.segment_meta`、`.segment_meta_fields` 和 `.segment_meta_name`，避免长行难以核对。
- 主段按 main_record_count 和 4 字节 record_size 切分；`code_offset = 0x0A + uint16(record+0x04)` 已由 sub_4291D8 反编译公式确认。从该入口做可达控制流解析，遇到 RET/EXIT 后结束；+0x06 仅以 `aux_hint` 输出和保留，不能用它截断或定位代码。编译器在条件分支后可能留下入口不可达的连续 `JMP`，反汇编器仅在这些字节完整、两字节对齐且目标已确认属于代码（或已确认的同类跳转链）时恢复为 `JMP`；其余未知区域仍按 `.byte` 保留。
- 主段记录头拆为 `.record`（索引和函数名）及 `.record_layout`（偏移、尺寸和边界）；无代码的事件参数记录使用带偏移的分组 `.record_data`，不再输出一整行数字。
- 二次扫描建立所有指令起点集合，再解析 JMP/JZ/JNZ 的目标。目标必须落在代码范围和合法指令边界内，否则报错。
- 对 0x40..0xBA 输出 EXE 官方名称（Text、BGMPlay 等），并保留 opcode、handler、pop/push 元数据；不能因为业务字段尚未完全确认就跳过字节。
- 常见的 `PUSH <第三段序号>; Text` 序列应折叠为 `PUSH Text <序号> "文本"`；`PUSH <正文序号>; PUSH <注音序号>; Ruby` 序列折叠为 `PUSH Ruby ...`；汇编器据此自动重建字符串引用。
- `Select` 的选项文本是直接压入值栈后由 `Select` 一次性消费，不经过 `Text` 指令；这类索引以内联 `PUSH TextRef <序号> "文本"` 表示。`TextRef` 只生成原始 `PUSH`，不会附带 `Text` opcode。
- 常见的 `PUSH <第二段偏移>; CALL` 序列应折叠为 `CALL call_XXXXXXXX 目标标签`；`call_XXXXXXXX` 是第二段记录标签，汇编器据此自动重建第二段调用记录。`PUSH <第二段偏移>; SCNCHG` 同样折叠为 `SCNCHG call_XXXXXXXX 目标标签`，场景记录长度或前置跨脚本名称变化时自动重定位。
- 未被代码引用的少数第三段字符串使用独立 `.orphan_string` 保留，不输出整张底部字符表；所有已识别的 `Text`、`Ruby` 和 `Select` 引用均不应误报为 orphan。
- 第二段和第三段是数据块，不是 VM 指令。第三段字符串引用按 1-based 解释；高位本地字符串引用单独格式化。
- 记录尾部、段间填充以及不在当前官方字典中的保留字节必须通过 .byte/保留数据伪指令完整输出，以满足零突变。
- 已确认的记录尾部全为 16 字节对齐所需的零填充时，反汇编使用 `.tail_align alignment=0x10 size=...` 记录对齐尺寸而省略零字节；汇编器按当前代码长度自动补齐，仍保持原记录尺寸和零突变。
- 文本编码、\\n 三字节引擎换行标记、真实反斜杠和 {{XX}} 占位符按原规范处理；不能用 \\xNN 作为输出格式。

### 12.2 assembler.py

- 标签重定位公式必须是 relative = target_offset - instruction_start，并以有符号 int32 little-endian 写回。
- 只在明确修改代码区时按代码长度增量调整 `aux_hint` 原始字段、记录尺寸和后续段偏移；没有语义修改时直接复用原始字段与填充，避免突变。汇编器同时兼容旧版文本中的 `aux_offset=` 键。
- 第二段 `kind=1` 回调的 payload 保存主段记录偏移；汇编器会根据重建后的主段记录布局自动把 `@main::函数名`（旧文本的 `@main+偏移` 也兼容）映射为新偏移，避免前置记录增删代码后同脚本 CALL 指向旧位置。第二段 `kind=2` 的跨脚本目标和 `kind=3` 的场景目标保存限定名称/文本，不携带目标脚本内部偏移；目标文件长度变化不会影响它们，但重命名目标时需同步更新引用文本。
- `disassembler.py` 现在可将 Scene.tbl 输出为 `.scene_tbl` 语义汇编，`assembler.py` 可按该格式重建表；记录长度、记录区长度、记录数和偶数对齐会自动计算。批量汇编同目录 `.kgo` 时，汇编器会依据原始 `.kgo` 的记录偏移和函数名，将 Scene.tbl 的 `entry_offset` 自动重定位到新主段记录；单独修改并汇编一个 `.kgo` 时，若旁边存在 `Scene.tbl.asm.txt` 也会自动生成同步后的表。若函数被重命名或脚本 ID 被修改，无法安全推断时会保留原值或报错，需手工同步。
- 对官方名称之外的保留/空槽，应支持“原 opcode + 原始数据”的保留形式；有效扩展的值栈参数按元数据编码，不能擅自改写为内联操作数。
- 记录尾部的已知 uint16 元数据应使用 `.tail_meta values=...` 语义形式；第二段回调使用 `.segment_calls/.call kind=... target=...`，汇编时自动计算记录长度。
- 汇编前后检查：段边界、记录尺寸、第三段指针数量、字符串引用范围、所有跳转目标和 uint16/uint32 溢出。

### 12.3 opcodelist.py

建议每项至少包含：

~~~python
{
    "opcode": 0x03,
    "mnemonic": "JMP",
    "length": 6,
    "operands": [{"type": "rel_offset", "width": 4,
                  "base": "instruction_start"}],
    "stack": "0",
    "evidence": "sub_4298B4 + sub_428F1C",
}
~~~

扩展项应使用 EXE 官方 mnemonic（例如 `Text`、`Random`、`BGMPlay`），`length=2`、对应的 `pop/push` 栈签名、handler 地址和业务语义字段。少数参数的具体业务含义未确认时，使用 `semantic_status="runtime_pending"`，但不得把已确认的官方身份和栈签名标为 unknown。

## 13. 零突变验证方案

对每个样本执行：

~~~text
原始 .kgo
  -> 解析头/段/记录
  -> 反汇编到语义文本
  -> 汇编（无语义修改）
  -> 比较整个文件的 SHA-256/逐字节
~~~

验证不仅要比较主段代码，还要比较：

- 64 字节头中未知字段和 checksum 字段；
- 主段记录名、尾部元数据和对齐字节；
- 第二段每个记录的尺寸、类型和字符串；
- 第三段每个 uint16 尺寸、字符串和段间填充；
- 所有扩展 opcode 的原始字节。

若只修改字符串导致代码长度变化，必须在临时布局中重新计算记录 `aux_hint`、记录尺寸和后续段偏移；第三段字符串记录尺寸由编码后的字节数（含 NUL 与偶数对齐）自动计算。若要求严格零突变，则无修改输入必须走原字节保留路径，而不是重新编码字符串。

## 14. 未确认事项与排查顺序

1. .kgo 段 0（头 0x10..0x18，通常位于文件偏移 0x40）的消费者尚未由 VM 读取点确认。
2. sub_404C8C 与资源上下文可能涉及缓存、校验或变换，但目前没有足够证据命名为加密/压缩。
3. CALL 的 0x429934 handler 反编译缺失，第二段类型字段和索引基准需要运行时确认。
4. 少数扩展 handler 的业务字段（例如坐标轴顺序、资源编号范围、等待完成条件）仍需运行时确认；官方名称、handler、长度和 pop/push 已由 EXE 元数据确认。
5. INCSV 的官方助记符与机器行为（当前观察像 GETSV）不一致；GETSTR 的高位掩码行为也只能标为推断。
6. CALL kind 2 的完整目标切换约定，以及 SCNCHG 的场景切换条件、返回值和事件等待状态，需要结合 sub_42BCB8、sub_428064 的运行时路径确认。

遇到未定义 opcode 时，按以下顺序处理：

1. 回到 0x599448 dispatch 表和对应 handler 地址，确认是否只是漏记字典。
2. 用长度函数重新检查前一条指令，排除把 6 字节操作数的后半部分误当成 opcode。
3. 检查是否已经越过 `aux_hint` 提示并到达 RET/EXIT 后的记录尾部；该字段不能替代入口可达解析。
4. 若 handler 非空但业务字段仍待确认，使用 EXE 官方名称并标记 `runtime_pending`，同时保留原始字节；只有空 dispatch 槽才登记为 `INVALID_xx`。
5. 更新本文件和 opcodelist.py 后，对全部 script/*.kgo 重新扫描，确保没有非法槽位或边界歧义。

## 15. EXE 证据索引

- Scene.tbl 加载、IN10/长度/记录数读取、名称索引：[42754C.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/42754C.c)
- 按名称查找、脚本切换、三个段读取、第三段指针表：[42855C.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/42855C.c)
- scr%05d.kgo 打开并读取 64 字节头：[428270.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428270.c)
- 场景/事件到脚本 ID 和入口 PC：[428064.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428064.c)
- VM 主循环：[428AD0.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428AD0.c)
- 单步解码和 dispatch：[428C20.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428C20.c)
- opcode 长度：[4273EC.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/4273EC.c)
- 核心/扩展官方名称字符串与扩展 `pop/push` 元数据：[00590000--00600000.txt](/E:/gal_pojie/Kogado引擎/export-for-ai/memory/00590000--00600000.txt)（名称区约 0x598F90、扩展表约 0x598890、dispatch 约 0x599448）
- 跳转相对偏移读取：[428F1C.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428F1C.c)、[4298B4.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/4298B4.c)、[4298D4.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/4298D4.c)、[429904.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/429904.c)
- 字符串引用：[428FD4.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/428FD4.c)
- 值栈：[4061EC.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/4061EC.c)、[406218.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/406218.c)、[4062D8.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/4062D8.c)、[406398.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/406398.c)
- 调用帧：[406480.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/406480.c)、[406570.c](/E:/gal_pojie/Kogado引擎/export-for-ai/decompile/406570.c)
- dispatch 表和核心助记符字符串：[00590000--00600000.txt](/E:/gal_pojie/Kogado引擎/export-for-ai/memory/00590000--00600000.txt)
