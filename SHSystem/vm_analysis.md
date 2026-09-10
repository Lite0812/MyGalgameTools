# SHSysSC VM Analysis

本文是 `コイビト遊戯.EXE` 中 `SHSysSC` 脚本 VM 的前置分析定义文档。后续反汇编、文本抽取、重组和跳转重定位均以本文为唯一真值源。

参考对象：

- 脚本装载器：`export-for-ai/decompile/40C430.c`
- 主分发器：`export-for-ai/decompile/40E270.c`
- 顶层 opcode 表：`0x439050`，由 `40E53B: mov ecx, [0x439050 + opcode * 4]` 使用
- 表达式 VM：`export-for-ai/decompile/40B630.c`
- 字符串参数读取器：`export-for-ai/decompile/40BC50.c`
- 参数块读取器：`export-for-ai/decompile/40EAA0.c`
- 资源读取层：`export-for-ai/decompile/414890.c`, `414920.c`, `414FF0.c`

## 1. VM 类型与执行模型

`SHSysSC` 是解释型字节码 VM。

- 顶层指令是 1 字节 opcode 加变长操作数。
- 顶层 VM 是基于 PC 的顺序解释器，opcode 通过函数指针表分发。
- 表达式是独立的栈式子 VM，由 `sub_40B630` 从当前 PC 读取到 `0xFF` 终止。
- 字符串参数是独立变长编码，由 `sub_40BC50` 读取。
- 调用参数块是独立变长编码，由 `sub_40EAA0` 读取到 `0x00` 终止。
- 顶层跳转目标是 24-bit big-endian 绝对 PC，不是相对偏移。
- 无对齐要求；所有字段按字节连续读取。

顶层有效 opcode：

- `0x00..0x63`：通过 `0x439050` 分发表分发。
- `0xFF`：特殊 END/RETURN，不进入分发表。
- `0x64..0xFE`：当前 EXE 没有合法表项定义，反汇编时必须报未定义 opcode。

## 2. SHSysSC 文件头

`sub_40C430` 校验并装载脚本：

| Offset | Size | Endian | 定义 |
|---:|---:|---|---|
| `0x00` | 7 | ASCII | 固定字符串 `SHSysSC` |
| `0x07` | 1 | - | 必须为 `0x00` |
| `0x08` | 3 | big | 文件总长 `file_size24 = b8 * 0x10000 + b9 * 0x100 + b10`，必须等于 entry 实际长度 |
| `0x0B` | 1 | - | line marker flag；非 0 时每条顶层 opcode 前有 2 字节 marker |
| `0x0C` | 4 | - | 未检查，样本通常为 0 |
| `0x10` | var | - | `flag == 0` 时为 bytecode 起点；`flag != 0` 时先是 NUL 结尾标题/源名，标题后才是 bytecode |

伪代码：

```python
if len(buf) < 0x10:
    reject()
if buf[0:8] != b"SHSysSC\x00":
    reject()
if ((buf[8] << 16) | (buf[9] << 8) | buf[10]) != len(buf):
    reject()

line_mode = buf[0x0B] != 0
pc = 0x10
if line_mode:
    pc = buf.index(0, 0x10) + 1
```

`Initial.hxp` 样本 `_sc_initial/entries/000001`：

```text
53 48 53 79 73 53 43 00 00 0A 7F 00 00 00 00 00
SHSysSC\0        size=0x0A7F=2687 flag=0
```

## 3. 运行时脚本对象

`sub_40C430` 分配 `script_size + 0x6E0` 字节，并把完整 `SHSysSC` 文件复制到对象尾部。

| Offset | Size | 定义 |
|---:|---:|---|
| `+0x000` | 4 | 当前 PC，启动时设为 `code_start` |
| `+0x004` | 4 | script base，指向 `object + 0x6E0` |
| `+0x008` | 4 | script size |
| `+0x00C` | 4 | code_start，相对 script base 的绝对偏移 |
| `+0x010` | 1 | active flag |
| `+0x011` | 1 | owned/free flag；置位时结束后释放对象 |
| `+0x012` | 1 | line marker flag，来自 header `0x0B` |
| `+0x014` | `0x400` | 当前脚本局部数值变量 `_iw[0..255]` |
| `+0x414` | `8 * 4` | gosub 返回 PC 栈 |
| `+0x434` | 4 | gosub 栈深度，最大 8 |
| `+0x438` | 4 | 当前 line/source marker |
| `+0x43C` | `0x2A0` | 当前脚本局部资源槽，最多 56 个 12 字节槽 |
| `+0x6DC` | 4 | 上一层脚本上下文 |
| `+0x6E0` | var | 完整 `SHSysSC` 文件数据 |

## 4. 资源槽类型

资源槽是 12 字节 `{type, ptr, size_or_aux}`。全局槽 `0..199` 位于 `0x4658D0`，当前脚本局部槽 `200..255` 位于脚本对象 `+0x43C`。

| Type | 定义 | 释放函数证据 |
|---:|---|---|
| `0` | 空槽 | - |
| `1` | `SHSysSC` 脚本对象 | `sub_40C2D0` |
| `2` | 位图/图像对象 | `sub_402FB0` |
| `3` | 声音/媒体数据对象 | `sub_4091D0` |
| `4` | 原始二进制块 | `HeapFree`/`sub_414210` |
| `5` | 字体对象 | `sub_4012C0` |
| `6` | 3 字节颜色对象 | `HeapFree` |
| `7` | surface/显示层对象 | `sub_40A550` |
| `8` | icon/cursor 类对象 | `sub_40CF10` |
| `9` | DLL/plugin 模块 | `sub_40D100` |
| `10` | 文本索引对象 | `sub_414210` |

## 5. 顶层指令解码

主循环位于 `sub_40E270`：

```python
while active_script:
    if script.line_mode:
        marker = read_u16be(base + pc)
        pc += 2
        script.current_marker = marker

    opcode = base[pc]
    pc += 1

    if opcode == 0xFF:
        end_current_context()
    elif 0x00 <= opcode <= 0x63:
        funcs_439050[opcode]()
    else:
        undefined_opcode()
```

### 5.1 `target24`

所有顶层跳转目标均为 3 字节 big-endian 绝对 PC：

```python
target = b0 << 16 | b1 << 8 | b2
```

地址基准是当前 `SHSysSC` 文件起点，也就是 `script base + target`。目标不是相对当前 PC，也不是相对 opcode 末尾。

反汇编时必须把所有 `target24` 转为标签：

```text
loc_000010:
  JUMP loc_000123
```

重组时必须从标签重新计算 24-bit 绝对 PC，禁止保留旧字节偏移。

### 5.2 `expr`

`expr` 是表达式子 VM 字节流，由 `sub_40B630` 读取到表达式终止字节 `0xFF`，长度包含终止字节。它返回数值结果，并可通过赋值 opcode 修改变量。

### 5.3 `strarg`

`strarg` 是字符串参数，由 `sub_40BC50` 读取。它可以是内联 NUL 结尾 CP932/ANSI 字符串，也可以引用字符串变量。

### 5.4 `paramblock`

`paramblock` 是调用参数块，由 `sub_40EAA0` 读取。格式为描述字节序列，直到 `0x00` 结束：

| Descriptor | 操作 |
|---:|---|
| `0x00` | 参数块结束 |
| `0x01` | 读取一个 `strarg`，复制到 `SARG[n]` |
| 其他非 0 | 读取一个 `expr`，复制到 `_is[n]` |

## 6. 表达式 VM 字节码

表达式 VM 是栈式机。每个表达式以 `0xFF` 结束，`sub_40B630` 最终把 PC 设置到终止字节后一位。

### 6.1 数值立即数

| Byte pattern | 长度 | 定义 |
|---|---:|---|
| `0x00..0x07` | 1 | push 常量 `0..7` |
| `0x08..0x0C` | 1 | push 常量 `7 - byte`，即 `-1..-5` |
| `0x0D imm8` | 2 | push signed/byte immediate |
| `0x0E imm16be` | 3 | push 16-bit big-endian immediate |
| `0x0F imm32be` | 5 | push 32-bit big-endian immediate |

### 6.2 变量读取

| Byte pattern | 长度 | 定义 |
|---|---:|---|
| `0x10..0x1D` | 1 | push `_is[index]`，index 为低 4 位 |
| `0x1E imm8` | 2 | push `_is[imm8]` |
| `0x1F imm16be` | 3 | push `_is[imm16]` |
| `0x20..0x2D` | 1 | push `_iw[index]` |
| `0x2E imm8` | 2 | push `_iw[imm8]` |
| `0x2F imm16be` | 3 | push `_iw[imm16]` |
| `0x30..0x3D` | 1 | push `_ig[index]` |
| `0x3E imm8` | 2 | push `_ig[imm8]` |
| `0x3F imm16be` | 3 | push `_ig[imm16]` |

变量 bank：

- `_is`：`dword_466330[0..255]`，全局结果/参数数值寄存器。
- `_iw`：当前脚本对象 `+0x14`，局部数值变量 `0..255`。
- `_ig`：`dword_466330 + 0x400`，全局大数值变量，数量为 `dword_46564C`。

### 6.3 赋值与复合赋值

赋值使用栈顶 lvalue 记录，低 4 位为子 opcode：

| Byte | 定义 |
|---:|---|
| `0x40` | `lhs = rhs` |
| `0x41` | `lhs += rhs` |
| `0x42` | `lhs -= rhs` |
| `0x43` | `lhs *= rhs` |
| `0x44` | `lhs /= rhs` |
| `0x45` | `lhs %= rhs` |
| `0x46` | `lhs &= rhs` |
| `0x47` | `lhs OR= rhs` |
| `0x48..0x4F` | 保留；代码仍会走赋值路径，未定义语义禁止生成 |

### 6.4 比较运算

| Byte | 定义 |
|---:|---|
| `0x50` | `==` |
| `0x51` | `!=` |
| `0x52` | `<` |
| `0x53` | `<=` |
| `0x54` | `>` |
| `0x55` | `>=` |
| `0x56..0x5F` | 保留；会弹栈但无明确比较语义，禁止生成 |

### 6.5 二元运算

| Byte | 定义 |
|---:|---|
| `0x60` | `+` |
| `0x61` | `-` |
| `0x68` | `*` |
| `0x69` | `/` |
| `0x6A` | `%` |
| `0x6B` | bitwise `&` |
| `0x6C` | bitwise OR |
| `0x6D` | logical `&&` |
| `0x6E` | logical OR |
| `0x62..0x67`, `0x6F` | 保留；会弹栈但无明确语义，禁止生成 |

### 6.6 一元运算与动态变量读取

| Byte | 定义 |
|---:|---|
| `0x70` | unary `-` |
| `0x71` | logical `!` |
| `0x72` | `rand() % value`，负数时结果取负 |
| `0x73` | `sin(value)`，角度按 1/100 度，结果乘 10000 |
| `0x74` | `cos(value)`，角度按 1/100 度，结果乘 10000 |
| `0x75` | 反三角/角度换算类 helper，结果归一到 `0..35999` |
| `0x76` | `sqrt(value)`，结果乘 10000 |
| `0x77` | 保留/identity |
| `0x78` | 将栈顶作为 index，读取 `_is[index]` |
| `0x79` | 将栈顶作为 index，读取 `_iw[index]` |
| `0x7A` | 将栈顶作为 index，读取 `_ig[index]` |
| `0x7B..0x7F` | 保留；禁止生成 |

### 6.7 表达式结束

| Byte | 定义 |
|---:|---|
| `0xFF` | 表达式结束；不是顶层 END |

## 7. 字符串参数编码

`sub_40BC50` 读取 `strarg`：

| Byte pattern | 长度 | 定义 |
|---|---:|---|
| `0x00` | 1 | 空字符串 |
| `0x01..0x05 imm8` | 2 | 1 字节 index，kind 为首字节 |
| `0x06..0x0B imm16be` | 3 | 2 字节 index，kind 为首字节减 6 |
| `0x0C..0x1F expr` | `1 + expr` | kind 为首字节减 12，index 由表达式给出 |
| `0x20..0xFF bytes 00` | `strlen + 1` | 内联 NUL 结尾 CP932/ANSI 字符串 |

kind 到字符串空间：

| kind | 映射 |
|---:|---|
| `1` | 转换为 `19`，返回 `lpString1 + index * 0x400`，即 `SARG[index]` |
| `3` | 转换为 `18`，返回 `dword_46633C + index * 0x400` |
| `18` | 返回 `dword_46633C + index * 0x400` |
| `19` | 返回 `lpString1 + index * 0x400` |
| 其他 | 返回空字符串/无效引用 |

## 8. 顶层 Opcode 字典

表中长度默认包含 1 字节 opcode 本身，不包含 line-mode 下 opcode 前额外的 2 字节 marker。

本表的 `byte_pattern`、`length / format`、`operand_schema`、`sub_opcode / variants` 是反汇编与重组必须遵守的硬定义。`命名` 是按 handler 行为给出的稳定助记符，少数图像/媒体/DLL 类 opcode 的高层语义名称仍可在后续脚本语料反汇编后细化，但不得改变已确认的读取顺序、操作数边界和跳转目标编码。

| Opcode | 命名 | byte_pattern | length / format | operand_schema | sub_opcode / variants | 精确定义与证据 |
|---:|---|---|---|---|---|---|
| `0x00` | `EVAL` | `00` | `1 + expr` | `expr` | 表达式内部子 opcode 见第 6 节 | 调用 `sub_40B630` 执行表达式，丢弃返回值；常用于赋值语句。证据 `40EC00.c` |
| `0x01` | `BIN_ALLOC` | `01` | `1 + expr*2` | `slot, size` | - | 读取槽号和大小，调用 `sub_40D500(slot, size)` 分配 type4 原始二进制块。证据 `4116E0.c`, `40D500.c` |
| `0x02` | `CALL` | `02` | `1 + paramblock + target24` | `paramblock, target24` | 返回栈最大 8 层 | 调用 `sub_40EAA0` 收集参数；读取 `target24`；保存当前 PC 到 `+0x414` 返回栈并跳转。证据 `40ED40.c` |
| `0x03` | `ENTER_SLOT` | `03` | `1 + expr + paramblock` | `slot, paramblock` | type1 脚本或 type9 回调 | 读取资源槽，解析参数块；type1 则进入脚本上下文，type9 则调用对象回调。证据 `40FF40.c` |
| `0x04` | `BINDIMG` | `04` | `1 + expr*2` | `surface_slot, image_slot` | - | 取 type7 surface 和 type2 位图，调用 `sub_40A6D0` 绑定/拷贝图像。证据 `4106D0.c` |
| `0x05` | `CURSOR_SURFACE` | `05` | `1 + expr` | `mode_or_slot` | `0` 恢复系统光标；正数使用 type8 资源 | 调用 `sub_40CF50` 设置/清除鼠标指针及其跟随 surface。证据 `410000.c`, `40CF50.c` |
| `0x06` | `LOAD_SCRIPT` | `06` | `1 + strarg + expr*2 + paramblock` | `path, arg, chain_flag, paramblock` | `chain_flag & 1` 控制是否挂回上一上下文 | 读取资源数据，要求能被 `sub_40C430` 解析为 `SHSysSC`，再由 `sub_40C540` 进入脚本。证据 `40FE90.c` |
| `0x07` | `RESINFO` | `07` | `1 + expr` | `slot` | type2/type7 返回扩展信息 | 查询资源槽类型；type2 返回宽高，type7 返回可见、位置、尺寸、alpha/层级等。证据 `411600.c` |
| `0x08` | `CLIPSET` | `08` | `1 + strarg` | `text` | ANSI clipboard | 打开剪贴板，写入 `CF_TEXT`。证据 `412AB0.c` |
| `0x09` | `MEDSTAT` | `09` | `1 + expr` | `channel` | channel clamp `0..7` | 查询媒体/音频通道状态，结果低位写入 `_is[0]`。证据 `411F90.c` |
| `0x0A` | `VIDEO_REFRESH` | `0A` | `1` | - | - | 若视频/子窗口标志有效则 invalidate 子窗口并写 `_is[0]=1`，否则 `_is[0]=0`。证据 `411590.c` |
| `0x0B` | `IMEOPEN` | `0B` | `1 + expr` | `flag` | - | 调用 `sub_406730(flag != 0)` 开关 IME。证据 `410530.c` |
| `0x0C` | `TEXTINDEX` | `0C` | `1 + expr*2` | `slot, mode` | `mode=0` 行索引；非 0 key 哈希索引 | 将 type4 文本资源转为 type10 文本索引，行数写 `_is[0]`。证据 `412030.c` |
| `0x0D` | `BINCOPY` | `0D` | `1 + expr*3` | `dst_slot, src_slot, size` | - | 要求两个槽均为 type4，执行 `memcpy(dst, src, size)`。证据 `412A60.c` |
| `0x0E` | `SETSLOT` | `0E` | `1 + expr*8` | `surface, args...` | - | 取 type7 surface，调用 `sub_40A2A0` 修改绘制槽/显示参数。证据 `40F560.c` |
| `0x0F` | `COLOR` | `0F` | `1 + expr*4` | `slot, r, g, b` | - | 创建 type6 三字节颜色资源。证据 `40F810.c`, `40D730.c` |
| `0x10` | `FONT` | `10` | `1 + expr + strarg + expr*5` | `slot, face, size, weight, style...` | - | 创建 type5 字体资源。证据 `40F7B0.c`, `40D690.c` |
| `0x11` | `ICON` | `11` | `1 + expr + strarg + expr + strarg + expr` | mixed | - | 创建 type8 icon/cursor 类资源。证据 `410010.c`, `40D7C0.c` |
| `0x12` | `SURFACE` | `12` | `1 + expr*5` | `slot, width, height, layer_ref, aux` | - | 创建 type7 surface/display layer。证据 `40EE70.c`, `40D5D0.c` |
| `0x13` | `DIALOG` | `13` | `1 + expr + strarg` | `mode, text` | mode `0..5` | MessageBox、debug 输出、保存文件对话框、输入对话框等，结果写 `_is[0]`/`SARG[0]`。证据 `410B50.c` |
| `0x14` | `COLORKEY` | `14` | `1 + expr*3` | `image_slot, color_slot, mode` | mode `<2`/`>=2` 分支 | 对 type2 图像应用颜色键/遮罩处理。证据 `412B10.c` |
| `0x15` | `DRAWIMG` | `15` | `1 + expr*8` | image draw args | - | 对 type2/type7 目标绘制图像块。证据 `40F950.c` |
| `0x16` | `BLIT` | `16` | `1 + expr*12` | blit args | - | 源/目标图像或 surface 间块传输。证据 `410810.c` |
| `0x17` | `DRAWTEXT` | `17` | `1 + expr*3 + strarg + expr*4` | `dst, x, y, text, font_slot, color_slot, mode, arg` | - | 使用字体/颜色资源绘制字符串；字符串参数位于第 3 个数值表达式之后。证据 `40F650.c` |
| `0x18` | `SLOTENABLE` | `18` | `1 + expr*3` | `surface, index, flag` | index `<0` 表示整体开关 | 开关 surface 或其绘制槽。证据 `40F600.c` |
| `0x19` | `EXIT` | `19` | `1 + expr` | `retval` | - | 写 `_is[0]`，结束当前脚本上下文。证据 `40EDE0.c` |
| `0x1A` | `ENUMFONT` | `1A` | `1 + expr` | unused/selector | - | 枚举字体或相关系统项，结果写 `_is`/`SARG`。证据 `411E70.c` |
| `0x1B` | `FREERES` | `1B` | `1 + expr` | `slot` | - | 释放指定资源槽。证据 `4104E0.c`, `40D130.c` |
| `0x1C` | `READBYTE` | `1C` | `1 + expr*2` | `slot, offset` | - | 从 type4 二进制块读取 1 字节到 `_is[0]`。证据 `4117B0.c` |
| `0x1D` | `IMPORT` | `1D` | `1 + expr` | `dst_slot` | record type 分支 | 从序列化缓冲导入资源记录到目标槽。证据 `410380.c` |
| `0x1E` | `READSTR` | `1E` | `1` | - | - | 从序列化缓冲读取字符串记录到 `SARG[0]`。证据 `410300.c` |
| `0x1F` | `READINTS` | `1F` | `1 + expr*2` | `start, count` | `count<=0` 单值 | 从序列化缓冲读取 24-bit 整数记录。证据 `4101E0.c` |
| `0x20` | `GETKEY` | `20` | `1` | - | - | 从输入队列取按键码到 `_is[0]`。证据 `410550.c` |
| `0x21` | `INPUT` | `21` | `1` | - | - | 写入鼠标/输入状态到 `_is`。证据 `40F8F0.c` |
| `0x22` | `TIME` | `22` | `1` | - | - | `GetLocalTime`，写年月日时分秒等到 `_is`。证据 `4129A0.c` |
| `0x23` | `SYSINFO` | `23` | `1 + expr` | `selector/check` | - | 收集系统信息，写 `_is`/`SARG`。证据 `40FB20.c` |
| `0x24` | `SYSPATH` | `24` | `1 + expr` | `selector` | `0..5` | 查询 Windows/System/特殊目录/Temp，成功标志写 `_is[0]`。证据 `4131B0.c` |
| `0x25` | `TIMER` | `25` | `1 + expr` | `start_tick` | 31-bit wrap | 计算 `timeGetTime() & 0x7fffffff` 差值到 `_is[0]`。证据 `410060.c` |
| `0x26` | `JTRUE` | `26` | `1 + expr + target24` | `cond, target` | - | 表达式真则跳转，否则跳过目标。证据 `40EC10.c` |
| `0x27` | `JFALSE` | `27` | `1 + expr + target24` | `cond, target` | - | 表达式假则跳转，否则跳过目标。证据 `40EC80.c` |
| `0x28` | `BRANCH` | `28` | `1 + expr + target24*2` | `cond, true_target, false_target` | 双目标 | 表达式真/假选择两个 `target24` 之一。证据 `40EC40.c` |
| `0x29` | `JUMP` | `29` | `1 + target24` | `target` | - | 无条件跳转。证据 `40ECB0.c` |
| `0x2A` | `SWITCH` | `2A` | `1 + expr + count16 + target24*count` | `index, table` | 越界跳过表 | 表达式值为合法下标时跳转到表项目标。证据 `40ECE0.c` |
| `0x2B` | `FILETIME` | `2B` | `1 + strarg` | `path` | - | 查询文件时间，状态和时间字段写 `_is`。证据 `410470.c` |
| `0x2C` | `LOADRES` | `2C` | `1 + expr + strarg + expr*2` | `slot, path, arg, ignore_error` | 按数据魔术头分 type | 读取资源，自动识别脚本/type2图像/type3音频/type4原始。证据 `40EE00.c`, `40D330.c` |
| `0x2D` | `PREPRES` | `2D` | `1 + expr + strarg + expr` | `slot, path, flag` | - | 预加载/准备声音类资源，创建 type3。证据 `411960.c`, `40D470.c` |
| `0x2E` | `LOADMASK` | `2E` | `1 + expr + strarg + expr` | `surface, path, flag` | - | 为 type7 surface 读取附加 mask/平面数据。证据 `410760.c` |
| `0x2F` | `MOVE` | `2F` | `1 + expr*3` | `slot_or_window, x, y` | slot `<0` 移动窗口 | 移动 surface 或主窗口。证据 `40F850.c` |
| `0x30` | `NOOP` | `30` | `1` | - | - | 空函数。证据 `413170.c` |
| `0x31` | `SOUNDPLAY` | `31` | `1 + expr*4` | `sound_slot, channel, loop_flag, arg` | channel clamp `0..7` | 控制/播放 type3 声音。证据 `40EF20.c` |
| `0x32` | `MOVIE` | `32` | `1 + strarg + expr*5` | `file, x, y, w, h, volume` | - | 初始化/播放视频或媒体。证据 `411530.c` |
| `0x33` | `EXPORT` | `33` | `1 + expr` | `slot` | type4/type2 分支 | 将资源写入当前序列化缓冲。证据 `410170.c` |
| `0x34` | `WRITESTR` | `34` | `1 + strarg` | `text` | - | 写字符串记录到序列化缓冲。证据 `410160.c` |
| `0x35` | `WRITEINTS` | `35` | `1 + expr*2` | `start, count` | `count<=0` 单值 | 写 24-bit 整数记录到序列化缓冲。证据 `4100A0.c` |
| `0x36` | `TEXTGET` | `36` | `1 + expr + selector8 + expr/strarg` | `textindex_slot, selector, key` | selector `0` 行号；非 0 key | 从 type10 文本索引取行/字段，写 `_is`/`SARG`。证据 `412320.c` |
| `0x37` | `REGGET` | `37` | `1 + strarg` | `value_name` | - | 读取注册表字符串到 `SARG[0]`。证据 `410E20.c` |
| `0x38` | `SLOTPOS` | `38` | `1 + expr*2` | `surface, index` | - | 设置 type7 surface 子槽位置/当前索引。证据 `410720.c` |
| `0x39` | `SETSRC` | `39` | `1` | - | - | 重置当前序列化缓冲指针为 `Src`。证据 `410440.c` |
| `0x3A` | `DOWNSCALE` | `3A` | `1 + expr*3` | `image_slot, width, height` | 多比例分支 | 缩放/降采样 type2 图像。证据 `412B80.c` |
| `0x3B` | `RESIZE` | `3B` | `1 + expr*3` | `surface, width, height` | 负值沿用原尺寸 | 调整 type7 surface 尺寸。证据 `40F8A0.c` |
| `0x3C` | `RETURN` | `3C` | `1 + expr` | `retval` | 返回栈必须非空 | 写 `_is[0]` 并从 gosub 栈弹出 PC。证据 `40EDA0.c` |
| `0x3D` | `EXISTS` | `3D` | `1 + strarg` | `path` | - | 文件存在性检测，结果写 `_is[0]`。证据 `410450.c` |
| `0x3E` | `SAVEIMG` | `3E` | `1 + expr + strarg` | `image_slot, path` | BMP/TGA 类输出分支 | 将 type2 图像编码保存，结果写 `_is[0]`。证据 `4119B0.c` |
| `0x3F` | `KEYSTATE` | `3F` | `1` | - | - | 读取键盘状态到 `_is[0..255]`。证据 `411850.c` |
| `0x40` | `FULLSCR` | `40` | `1 + expr` | `mode` | - | 切换全屏/显示模式，结果写 `_is[0]`。证据 `411030.c` |
| `0x41` | `FINDDRV` | `41` | `1 + strarg + expr` | `volume_label, fixed_only` | 未生成 `.c`，由 dump 反汇编确认 | 枚举逻辑盘，匹配卷标/固定磁盘条件；结果路径写 `SARG[0]`，成功标志写 `_is[0]`。证据 `0x410EE0` |
| `0x42` | `SETAPPKEY` | `42` | `1 + strarg*2` | `app, key` | - | 设置注册表基路径相关字符串。证据 `410DF0.c` |
| `0x43` | `ALPHA` | `43` | `1 + expr*2` | `surface, alpha` | clamp `0..255` | 设置 type7 surface alpha/透明度。证据 `40FAE0.c` |
| `0x44` | `WRITEBYTE` | `44` | `1 + expr*3` | `slot, offset, value` | - | 写 1 字节到 type4 二进制块。证据 `411710.c` |
| `0x45` | `WRITESTRDATA` | `45` | `1 + expr*2 + strarg` | `slot, offset, text` | - | 将字符串写入 type4 二进制块。证据 `4129F0.c` |
| `0x46` | `OPTION` | `46` | `1 + expr*2` | `option_id, value` | id `0..4` 已确认 | 设置运行时选项、关键脚本/图像资源指针。证据 `4140B0.c` |
| `0x47` | `IMEPOS` | `47` | `1 + expr*2` | `x, y` | - | 设置 IME 候选/组合窗口位置类全局。证据 `412940.c` |
| `0x48` | `BLEND` | `48` | `1 + expr*4` | `dst, src_a, src_b, strength` | - | 图像/surface 混合处理。证据 `411090.c` |
| `0x49` | `MOUSEMOVE` | `49` | `1 + expr*2` | `x, y` | client to screen | 移动系统鼠标指针。证据 `4116A0.c` |
| `0x4A` | `SNDSEEK` | `4A` | `1 + expr*2` | `sound_slot, pos` | - | 设置 type3 声音播放位置。证据 `4132B0.c` |
| `0x4B` | `SNDVOL` | `4B` | `1 + expr*3` | `sound_slot, value, duration` | - | 设置/渐变声音音量或参数。证据 `4114F0.c` |
| `0x4C` | `FILLGLOBAL` | `4C` | `1 + expr*3` | `start, count, value` | - | 填充 `_ig[start..start+count)`。证据 `412640.c` |
| `0x4D` | `SHELL` | `4D` | `1 + strarg*3` | `operation, file, dir` | file 内空格拆参数 | 调用 `ShellExecuteA`。证据 `411C30.c` |
| `0x4E` | `SHOW` | `4E` | `1 + expr*2` | `slot_or_window, flag` | slot `<0` 控制窗口 | 显示/隐藏窗口或 type7 surface，旧状态写 `_is[0]`。证据 `40EEB0.c` |
| `0x4F` | `SLEEP` | `4F` | `1 + expr` | `ms` | 最大 25ms | 休眠并可能刷新。证据 `4115E0.c` |
| `0x50` | `SNDCTRL` | `50` | `1 + expr*2` | `channel, value` | channel clamp `0..7` | 控制全局声音通道。证据 `40EF90.c` |
| `0x51` | `MOVIESTOP` | `51` | `1` | - | - | 停止/释放媒体播放。证据 `411580.c` |
| `0x52` | `STRCAT` | `52` | `1 + expr + strarg` | `dst_string, text` | - | 追加字符串到 `dword_46633C[dst]`。证据 `40F060.c` |
| `0x53` | `STRCMP` | `53` | `1 + strarg*2` | `a, b` | - | `lstrcmpA`，结果写 `_is[0]`。证据 `40F1D0.c` |
| `0x54` | `STRSET` | `54` | `1 + expr + strarg` | `dst_string, text` | - | 设置 `dword_46633C[dst]`。证据 `40EFE0.c` |
| `0x55` | `STRFIND` | `55` | `1 + strarg + expr*3` | `text, char, start, reverse` | - | 查找字符/DBCS 字符，结果写 `_is[0]`。证据 `40F2D0.c` |
| `0x56` | `STRESCAPE` | `56` | `1 + expr + strarg` | `dst_string, template` | 支持内嵌变量展开 | 展开转义/变量引用并写字符串槽。证据 `40F240.c`, `40BCF0.c` |
| `0x57` | `STRLEN` | `57` | `1 + strarg` | `text` | DBCS aware | 字符数/字节数写 `_is`。证据 `40F1F0.c` |
| `0x58` | `STRSPLIT` | `58` | `1 + strarg + expr` | `text, delimiter` | 支持 DBCS delimiter | 切分字符串到 `SARG[]`，数量写 `_is[0]`。证据 `4126A0.c` |
| `0x59` | `SUBSTR` | `59` | `1 + expr + strarg + expr*2` | `dst, text, start, len` | DBCS aware | 截取字符串写入目标字符串槽，首字符码写 `_is[0]`；字符串参数紧随目标槽表达式。证据 `410590.c` |
| `0x5A` | `TEXTSIZE` | `5A` | `1 + expr + strarg` | `font_slot, text` | - | 计算文本尺寸/宽度，结果写 `_is[0]`。证据 `411910.c` |
| `0x5B` | `TRANS` | `5B` | `1 + expr*6` | transition args | mode 多分支 | 图像/surface 转场混合。证据 `411CD0.c` |
| `0x5C` | `BREAK` | `5C` | `1 + expr` | `flag` | - | 设置连续执行中断/暂停标志。证据 `40FAD0.c` |
| `0x5D` | `WAIT` | `5D` | `1` | - | - | 进入等待输入/事件状态。证据 `40FE70.c` |
| `0x5E` | `REGSET` | `5E` | `1 + strarg*2` | `name, value` | 空名/空值删除键 | 写注册表字符串值。证据 `410E90.c` |
| `0x5F` | `READDATA` | `5F` | `1 + expr*3` | `slot, offset, size` | `size=0/1/2/4` | 从 type4 读取字符串或整数到 `SARG[0]`/`_is[0]`。证据 `4132F0.c` |
| `0x60` | `IMG_FILTER` | `60` | `1 + expr*5` | `slot, mode, a, b, c` | mode 多分支 | 对 type2/type7 图像执行滤镜/膨胀/模糊类处理。证据 `413440.c` |
| `0x61` | `DLL_CALL` | `61` | `1 + expr + strarg` | `dll_slot, proc_name` | - | 通过 type9/DLL 相关对象调用/绑定函数。证据 `414170.c` |
| `0x62` | `SETTITLE` | `62` | `1 + strarg` | `title` | - | `SetWindowTextA(hWnd, title)`。证据 `414190.c` |
| `0x63` | `SENDMSG` | `63` | `1 + expr*4` | `msg, wparam, lparam, sync` | `sync!=0` SendMessage；否则 PostMessage | 向主窗口发送或投递消息。证据 `4141B0.c` |
| `0xFF` | `END` | `FF` | `1` | - | 特殊 opcode，不走分发表 | 结束当前脚本上下文；有上一层则返回上一层，否则清空 `dword_466344`。证据 `40E270.c`, `40C310.c` |

## 9. 标签化与重定位策略

反汇编器必须收集以下目标：

- `0x26 JTRUE` 的 `target24`
- `0x27 JFALSE` 的 `target24`
- `0x28 BRANCH` 的两个 `target24`
- `0x29 JUMP` 的 `target24`
- `0x2A SWITCH` 的所有表项 `target24`
- `0x02 CALL` 的 `target24`

标签格式：

```text
loc_000010
loc_000123
```

地址空间是单个 `SHSysSC` 文件内部的绝对 PC。重组流程必须：

1. 先把 asm/IR 中所有标签映射到新 PC。
2. 再写回所有 `target24`。
3. 最后更新 header `0x08..0x0A` 的 24-bit 文件总长。

如果字符串或表达式长度变化，旧目标偏移全部作废。

## 10. 未定义 Opcode 处理流程

反汇编过程中遇到 `vm_analysis.md` 未定义的疑似 opcode 时必须：

1. 回查 `40E270` 的分发表和目标 handler，确认该字节是否真是顶层 opcode。
2. 检查上一条指令边界，尤其是 `expr` 是否正确读到 `0xFF`、`strarg` 是否正确读到 NUL、`paramblock` 是否正确读到 `0x00`。
3. 若确认是新 opcode 或此前误判，先修正本文，再全量重新反汇编。
4. 对 `0x64..0xFE`，除非 EXE 中发现新的分发表扩展，否则按非法 opcode 处理。

## 11. 反汇编实现最低要求

- 所有 `expr` 必须作为子 AST 保存，不能只保存原始字节。
- 表达式中的变量 bank、赋值子 opcode、比较/运算子 opcode 必须结构化。
- 所有 `strarg` 必须区分 inline string、string ref、expr-index string ref。
- 所有 `target24` 必须标签化。
- line-mode 文件必须保留每条指令前的 2 字节 marker；重组时可按原 marker 写回。
- 反汇编器应输出原始 PC、opcode、操作数 AST、目标标签和原始 handler 地址，便于校验。

## 12. 当前样本校验

用本文定义对当前工作区已解出的 `tools/koi_text/entries/*` 与 `_sc_initial/entries/*` 做线性边界扫描：

- 通过：72 个 `SHSysSC` 脚本。
- 覆盖：49,025 条顶层指令，25,930 个 `target24` 目标。
- 未纳入：`_sc_initial/entries/000002`，文件头不是 `SHSysSC`，内容为普通 CP932 文本。

该校验只证明 opcode/操作数边界、跳转目标范围和文件头长度自洽；高层命令语义仍以对应 handler 的反编译证据为准。
