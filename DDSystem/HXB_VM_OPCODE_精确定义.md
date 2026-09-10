# HXB VM Opcode 精确定义

本文按 `Opcode | 命名 | 长度/格式 | 精确定义 | 佐证` 五列表整理 HXB 脚本 VM 的顶层 opcode。分析对象来自 `export-for-ai/decompile`，核心分发器为 `sub_410FC0`，分发表为 `funcs_411044`。

## Helper 命名说明

- `sub_40D920`：表达式/参数求值器，读取到表达式终止字节 `0xFF`，返回数值或字符串及类型标志。
- `sub_410E30`：调用参数块解析器，读取以 `0x00` 结束的参数描述流；字符串参数写入字符串空间 19，数值参数写入 `dword_499980`。
- `sub_40D250`：VM 错误报告/脚本运行时错误处理。
- `sub_40F410(id, type)`：按资源 ID 和类型取资源对象。
- `sub_40EFF0(id)`：通用资源加载入口，先尝试 HXB，再尝试图片/音频/原始资源。
- `dword_499980`：结果/参数寄存器数组；很多 opcode 把返回值写入 `dword_499980[0..]`。
- `target24`：24-bit big-endian 绝对 PC 目标，即 `aa << 16 | bb << 8 | cc`。
- `expr`：一段表达式字节码，长度不固定，由 `sub_40D920` 读取到表达式终止字节 `0xFF`；表中的长度均包含这个终止字节。
- `paramblock`：调用参数块，长度不固定，由 `sub_410E30` 读取到参数块终止字节 `0x00`。
- 长度/格式列默认包含 1 字节 opcode 本身，不包含扩展头模式下每条指令前额外读取的 2 字节前缀。

## Opcode 表

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x00` | `EVAL` | `1 + expr` | 调用 `sub_40D920(NULL)` 执行一段表达式并丢弃结果；表达式自身可包含赋值，因此该 opcode 常用于普通语句/赋值语句。解析失败时报 VM 表达式错误。 | [411050.c](export-for-ai/decompile/411050.c) |
| `0x01` | `SETPARAM` | `1 + expr*2` | 读取 2 个数值表达式；第 1 个作为资源/槽 ID，第 2 个作为参数；调用 `sub_40F3A0(id, value)`。任一表达式为字符串时报类型错误，失败默认 0。 | [415BC0.c](export-for-ai/decompile/415BC0.c) |
| `0x02` | `CALL` | `1 + paramblock + target24` | 先调用 `sub_410E30` 解析调用参数块，然后读取 `target24`；若当前 HXB 调用深度小于 8，则保存当前 PC 到对象返回地址栈并跳转到目标。 | [4112A0.c](export-for-ai/decompile/4112A0.c) |
| `0x03` | `ENTER` | `1 + expr + paramblock` | 读取 1 个数值表达式作为资源 ID，再解析参数块；资源 ID `0..199` 查全局槽，`200..255` 查当前 HXB 局部槽。槽类型为 `1` 时作为 HXB 包进入并重置 PC；槽类型为 `9` 时调用对象回调。 | [4135D0.c](export-for-ai/decompile/4135D0.c) |
| `0x04` | `BINDIMG` | `1 + expr*2` | 读取 2 个数值表达式；分别以 `sub_40F410(arg0, 7)` 取得 type7 显示 surface、以 `sub_40F410(arg1, 2)` 取得 type2 位图资源；`sub_40C190` 会先标记旧 surface 脏矩形，必要时重建像素缓冲，再调用 `sub_4038C0` 把 type2 位图拷贝进 type7 surface。 | [414240.c](export-for-ai/decompile/414240.c)；[40C190.c](export-for-ai/decompile/40C190.c) |
| `0x05` | `SETMODE` | `1 + expr` | 读取 1 个数值表达式并调用 `sub_40D020(value)`；字符串时报类型错误，失败默认 0。该 helper 属于运行时状态设置类。 | [4136F0.c](export-for-ai/decompile/4136F0.c) |
| `0x06` | `LOADHXB` | `1 + expr*3 + paramblock` | 依次读取 3 个表达式：第 1 个要求字符串，第 2 个要求数值，第 3 个数值作为标志；随后解析参数块，调用 `sub_41EEE0` 读取资源，再用 `sub_40E930` 按 HXB 解析，成功后 `sub_40EB30(new_hxb, flag & 1)` 挂入 HXB 包链。 | [413480.c](export-for-ai/decompile/413480.c) |
| `0x07` | `RESINFO` | `1 + expr` | 读取 1 个数值表达式；若参数为负，返回窗口位置和鼠标屏幕坐标；否则按资源 ID 查全局/局部资源槽，并把资源类型及 type 2/type 7 资源的关键状态写入 `dword_499980`。 | [4159B0.c](export-for-ai/decompile/4159B0.c) |
| `0x08` | `CLIPSET` | `1 + expr` | 读取 1 个字符串表达式；打开剪贴板，清空后把该宽字符串以 `CF_UNICODETEXT` 写入剪贴板。 | [417A00.c](export-for-ai/decompile/417A00.c) |
| `0x09` | `MEDSTAT` | `1 + expr` | 读取 1 个数值表达式作为媒体/音频通道索引，夹到 `0..15`；若 DirectShow/媒体系统已初始化且对应通道对象存在，则调用对象虚表 `+36` 取状态字节，最低位写入 `dword_499980[0]`，表示该通道是否处于活动/播放状态。 | [416C10.c](export-for-ai/decompile/416C10.c) |
| `0x0A` | `MEDPLAY` | `1` | 不读取表达式；把全局 `dword_498288` 的低字节写入 `dword_499980[0]` 并返回，用于查询当前全局媒体播放/活动标志。 | [415920.c](export-for-ai/decompile/415920.c) |
| `0x0B` | `IMEOPEN` | `1 + expr` | 读取 1 个数值表达式作为开关；调用 `sub_406CF0(flag)`。开启时设置 IME 字体、关联输入法上下文并 `ImmSetOpenStatus(TRUE)`，关闭时解除窗口输入法上下文并通知 IME 关闭。 | [413FF0.c](export-for-ai/decompile/413FF0.c)；[406CF0.c](export-for-ai/decompile/406CF0.c) |
| `0x0C` | `TEXTINDEX` | `1 + expr*2` | 读取资源 ID 和模式两个数值表达式；要求目标资源槽为 type4 文本/二进制块，先经 `sub_41F810` 转成宽文本并按 CR/LF 统计行。模式为 0 时生成按行偏移访问的 type10 表；模式非 0 时按逗号前字段建立小写 key 的哈希索引，结果行数写入 `dword_499980[0]`。 | [416CF0.c](export-for-ai/decompile/416CF0.c)；[416CB0.c](export-for-ai/decompile/416CB0.c)；[41F810.c](export-for-ai/decompile/41F810.c) |
| `0x0D` | `BINCOPY` | `1 + expr*3` | 读取目标资源 ID、源资源 ID、字节数 3 个数值表达式；目标和源都必须是 type4 二进制数据块，存在时执行 `memcpy(dst, src, size)`。 | [417900.c](export-for-ai/decompile/417900.c) |
| `0x0E` | `SETSLOT` | `1 + expr*11` | 读取 11 个数值表达式；第 1 个为 type7 surface ID，其余参数写入该 surface 的绘制槽表。`sub_40BD30` 先确保槽数组容量，然后把槽启用标记、源资源槽指针、坐标/尺寸/混合参数等 44 字节记录写入 `surface+36` 指向的槽表。 | [411FB0.c](export-for-ai/decompile/411FB0.c)；[40BD30.c](export-for-ai/decompile/40BD30.c) |
| `0x0F` | `COLOR` | `1 + expr*4` | 读取 4 个数值表达式；第 1 个为目标资源 ID，后 3 个字节参数保存为 3 字节颜色对象。`sub_40F600` 会释放旧槽内容，分配 3 字节缓冲并把资源槽类型置为 `6`。 | [412950.c](export-for-ai/decompile/412950.c)；[40F600.c](export-for-ai/decompile/40F600.c) |
| `0x10` | `FONT` | `1 + expr*7`；版本 `>=1210` 为 `1 + expr*9` | 读取资源 ID、字体名字符串、字号/字重/样式等参数；版本 `dword_4999A8 >= 1210` 时额外读取 2 个数值参数。`sub_40F560` 调 `sub_401460` 创建 GDI 字体/DC/字形缓存，并把资源槽类型置为 `5`。 | [412720.c](export-for-ai/decompile/412720.c)；[40F560.c](export-for-ai/decompile/40F560.c)；[401460.c](export-for-ai/decompile/401460.c) |
| `0x11` | `ICON` | `1 + expr*5` | 读取资源 ID、字符串路径/资源名、数值参数和另一个字符串；`sub_40F690` 调 `sub_40CBA0` 从资源数据创建图标/光标类对象，成功后把资源槽类型置为 `8`。 | [413750.c](export-for-ai/decompile/413750.c)；[40F690.c](export-for-ai/decompile/40F690.c)；[40CBA0.c](export-for-ai/decompile/40CBA0.c) |
| `0x12` | `SURFACE` | `1 + expr*5` | 读取 5 个表达式，前 4 个要求数值，第 5 个若为数值则作为最后参数；`sub_40F470` 会释放目标资源槽旧对象，分配 0x38 字节 type7 surface 对象，调用 `sub_402D60(width,height,1)` 创建像素缓冲，设置层级/调色板/附加资源后把槽类型置为 `7`。 | [411550.c](export-for-ai/decompile/411550.c)；[40F470.c](export-for-ai/decompile/40F470.c)；[402D60.c](export-for-ai/decompile/402D60.c) |
| `0x13` | `DIALOG` | `1 + expr*2` | 读取模式数值和字符串 2 个表达式；模式 `0` 显示普通 MessageBox，`1` 输出调试字符串，`2`/`6` 打开 BMP/JPEG 保存文件对话框并把选中路径写入 `SARG[0]`、成功标志写入 `dword_499980[0]`，`4` 显示 Yes/No MessageBox 并返回是否选择 Yes。 | [4149C0.c](export-for-ai/decompile/4149C0.c) |
| `0x14` | `COLORKEY` | `1 + expr*3` | 读取图像资源 ID、颜色资源 ID、模式 3 个数值表达式；取 type2 位图和 type6 三字节颜色。模式 `< 2` 时调用 `sub_405200`，模式 `>= 2` 时调用 `sub_404E00`，两者都会根据现有 alpha 周围像素生成软边/扩张遮罩，并把指定颜色混入透明或半透明区域。 | [417AB0.c](export-for-ai/decompile/417AB0.c)；[405200.c](export-for-ai/decompile/405200.c)；[404E00.c](export-for-ai/decompile/404E00.c) |
| `0x15` | `DRAWIMG` | `1 + expr*8` | 读取 8 个表达式；目标槽若为空则创建 type2 位图缓冲，随后按目标槽类型分派：type2 调 `sub_403C80` 做像素块绘制/拷贝，type7 调 `sub_40C300` 在 surface 上绘制并合并脏矩形。参数中包含目标资源 ID、坐标、宽高、源 type6 颜色/调色对象、混合/模式参数。 | [412D10.c](export-for-ai/decompile/412D10.c)；[403C80.c](export-for-ai/decompile/403C80.c)；[40C300.c](export-for-ai/decompile/40C300.c) |
| `0x16` | `BLIT` | `1 + expr*12` | 读取 12 个数值表达式；以一个资源槽作为源位图/type2 或 surface/type7 的像素缓冲，另一个资源槽作为目标。目标为空时创建 type2 缓冲；目标为 type2 时调 `sub_404CE0`，目标为 type7 时调 `sub_40C220` 并合并脏矩形。支持源/目标坐标、宽高、透明/混合模式等参数，负宽高会取源尺寸。 | [414500.c](export-for-ai/decompile/414500.c)；[404CE0.c](export-for-ai/decompile/404CE0.c)；[40C220.c](export-for-ai/decompile/40C220.c) |
| `0x17` | `DRAWTEXT` | `1 + expr*8` | 读取 8 个表达式，其中包含目标资源 ID、坐标、字符串、字体/type5 资源、颜色/type6 资源和模式参数；若目标是 type2 位图则调用 `sub_404D60` 逐字绘制文本，若目标是 type7 surface 则调用 `sub_40C3A0` 绘制文本并合并脏矩形。 | [4123E0.c](export-for-ai/decompile/4123E0.c)；[404D60.c](export-for-ai/decompile/404D60.c)；[40C3A0.c](export-for-ai/decompile/40C3A0.c) |
| `0x18` | `SLOTENABLE` | `1 + expr*3` | 读取 3 个数值表达式：surface ID、绘制槽索引/全局开关、启用标志。若索引非负则修改 type7 surface 槽表中对应 44 字节记录的启用位，并在必要时把该槽内容绘制到 surface；若索引为负则设置整个 surface 的显示/启用标志。 | [4122F0.c](export-for-ai/decompile/4122F0.c)；[40CA90.c](export-for-ai/decompile/40CA90.c) |
| `0x19` | `EXIT` | `1 + expr` | 读取 1 个数值表达式作为返回值写入 `dword_499980[0]`，随后释放当前 HXB 或退回上一个 HXB 上下文。 | [411380.c](export-for-ai/decompile/411380.c) |
| `0x1A` | `ENUMFONT` | `1 + expr` | 读取 1 个数值表达式但实际只保存到局部变量；调用 `sub_402CF0` 枚举系统字体族，最多取 200 项。字体名数量写入 `dword_499980[0]`，每个字体名依次写入字符串空间 19：`SARG[0..count-1]`。 | [416B40.c](export-for-ai/decompile/416B40.c)；[402CF0.c](export-for-ai/decompile/402CF0.c) |
| `0x1B` | `FREERES` | `1 + expr` | 读取 1 个数值表达式作为资源 ID；按 `0..199` 全局槽、`200..255` 当前 HXB 局部槽定位资源槽，调用 `sub_40EDF0(slot)` 释放/清空该资源。 | [413F50.c](export-for-ai/decompile/413F50.c) |
| `0x1C` | `READBYTE` | `1 + expr*2` | 读取资源 ID 和偏移两个数值表达式；要求资源槽类型为 `4` 的二进制数据块，检查偏移范围后读取 1 字节，结果写入 `dword_499980[0]`。 | [415DB0.c](export-for-ai/decompile/415DB0.c) |
| `0x1D` | `IMPORT` | `1 + expr` | 读取目标资源 ID；从当前序列化缓冲 `dword_498258` 读取下一条记录。记录类型 `3` 时用 `sub_40B910` 取原始块并写入目标 type4 资源；记录类型 `4` 时用 `sub_40B970` 取图像块，释放目标槽后设为 type2 位图。 | [413D40.c](export-for-ai/decompile/413D40.c) |
| `0x1E` | `READSTR` | `1` | 不读取表达式；从当前序列化缓冲读取记录类型 `2` 的 UTF-16LE 零结尾字符串，推进 `dword_498258`，并写入字符串空间 19 的 `SARG[0]`。记录类型不匹配时报错。 | [413CF0.c](export-for-ai/decompile/413CF0.c) |
| `0x1F` | `READINTS` | `1 + expr*2` | 从当前脚本输出/序列化缓冲 `dword_498258` 读取记录类型 `1` 的 24-bit big-endian 整数。若第二参数 `count <= 0`，只读一个值到 `dword_499980[0]`；否则连续读取 `count` 个值并写入全局数值变量表 `dword_499978[start..]`。 | [413B60.c](export-for-ai/decompile/413B60.c) |
| `0x20` | `GETKEY` | `1` | 从环形队列 `word_466700` 读取一个 16-bit 输入/按键码写入 `dword_499980[0]`；队列空时返回 0。 | [414060.c](export-for-ai/decompile/414060.c) |
| `0x21` | `INPUT` | `1` | 把输入/鼠标相关全局状态写入 `dword_499980[0..7]`，包括坐标、按键字节和滚轮方向。 | [412C90.c](export-for-ai/decompile/412C90.c) |
| `0x22` | `TIME` | `1` | 调用 `GetLocalTime`，把年、月、日、星期、时、分、秒写入 `dword_499980[0..6]`。 | [417780.c](export-for-ai/decompile/417780.c) |
| `0x23` | `SYSINFO` | `1 + expr` | 读取 1 个表达式做类型检查；随后收集系统信息：Windows 版本、用户名、画面尺寸、色深、物理内存、音频初始化状态、窗口样式、屏幕尺寸等。字符串结果写入 `SARG[0]` Windows 名称、`SARG[1]` 空字符串、`SARG[2]` 用户名；数值结果写入 `dword_499980[0..16]`。 | [413150.c](export-for-ai/decompile/413150.c) |
| `0x24` | `SYSPATH` | `1 + expr` | 读取 1 个数值 selector：`0` 查询 Windows 目录，`1` 查询 System 目录，`2` 查询 CSIDL 16，`3` 查询 CSIDL 12，`4` 查询 CSIDL 39，`5` 查询临时目录。handler 只把查询成功与否写入 `dword_499980[0]`；目录字符串写入局部 `Buffer` 或由 `sub_4193A0` 转换 PIDL 后用于成功判定，不写入脚本字符串变量。 | [4193D0.c](export-for-ai/decompile/4193D0.c)；[4193A0.c](export-for-ai/decompile/4193A0.c) |
| `0x25` | `TIMER` | `1 + expr` | 读取 1 个数值表达式作为起始 tick；取 `timeGetTime() & 0x7FFFFFFF`，若参数非负则计算当前 tick 与起始 tick 的差值，并处理 31-bit 回绕；结果写入 `dword_499980[0]`。 | [4138B0.c](export-for-ai/decompile/4138B0.c) |
| `0x26` | `JZTRUE` | `1 + expr + target24` | 格式为 `expr_ff target24`；表达式为数值真时跳转到 `target24`，为假时跳过目标继续执行；字符串结果报类型错误。 | [411070.c](export-for-ai/decompile/411070.c) |
| `0x27` | `JZFALSE` | `1 + expr + target24` | 格式为 `expr_ff target24`；表达式解析失败、字符串类型或数值假时跳转到 `target24`，数值真时跳过目标继续执行。 | [411160.c](export-for-ai/decompile/411160.c) |
| `0x28` | `BRANCH` | `1 + expr + target24*2` | 格式为 `expr_ff false_target true_target`。表达式解析成功且数值为真时，PC 先跳过第一个 `target24`，再读取第二个 `target24` 作为跳转目标；表达式失败、类型错误或数值为假时，PC 跳过两个目标后回读第一个 `target24` 作为跳转目标。 | [4110F0.c](export-for-ai/decompile/4110F0.c) |
| `0x29` | `JUMP` | `1 + target24` | 读取紧随 opcode 的 `target24` 并把当前 HXB PC 设为该绝对偏移。 | [4111D0.c](export-for-ai/decompile/4111D0.c) |
| `0x2A` | `SWITCH` | `1 + expr + count16 + target24*count` | 格式为 `expr_ff count16 target24[count]`；表达式值越界时跳过整个表，合法时读取对应下标的 `target24` 并跳转。 | [411200.c](export-for-ai/decompile/411200.c) |
| `0x2B` | `FILETIME` | `1 + expr` | 读取 1 个字符串表达式作为路径，调用 `sub_40BB30(path, SYSTEMTIME*)`；失败/成功状态与文件时间写入 `dword_499980`。 | [413EA0.c](export-for-ai/decompile/413EA0.c) |
| `0x2C` | `LOADRES` | `1 + expr*4` | 读取 4 个表达式：第 1 个数值为资源 ID，中间两个做类型校验，第 4 个数值为错误处理/返回标志；调用 `sub_40EFF0(id)`，可把加载结果写入 `dword_499980[0]`。 | [411400.c](export-for-ai/decompile/411400.c) |
| `0x2D` | `PREPRES` | `1 + expr*4` | 读取 4 个表达式，第 1 个数值为资源 ID，第 4 个数值为标志；调用 `sub_40F310(id)`，返回布尔写入 `dword_499980[0]`，失败且未置忽略标志时报错。 | [416070.c](export-for-ai/decompile/416070.c) |
| `0x2E` | `LOADMASK` | `1 + expr*3` | 读取目标 surface ID、一个字符串表达式、一个数值表达式；随后调用 `sub_41EEE0` 读取当前资源数据，要求目标为 type7 surface，且数据大小等于 surface 宽高乘积。成功时把原有 `surface+40` 附加缓冲释放并替换为新读入的 8-bit mask/附加平面。 | [4143A0.c](export-for-ai/decompile/4143A0.c) |
| `0x2F` | `MOVE` | `1 + expr*3` | 读取 3 个数值表达式；若第 1 个参数为非负，则取 type7 surface 并调用 `sub_40C4B0(surface, x, y)` 更新 surface 屏幕偏移，同时把旧/新矩形加入全局脏区；若第 1 个参数为负，则调用 `SetWindowPos(hWndParent, 0, x, y, 0, 0, 0xD)` 移动窗口。 | [412A90.c](export-for-ai/decompile/412A90.c)；[40C4B0.c](export-for-ai/decompile/40C4B0.c) |
| `0x30` | `NOOP` | `1` | 空函数 `nullsub_2`，无参数、无副作用。 | [41B450.c](export-for-ai/decompile/41B450.c) |
| `0x31` | `SOUNDPLAY` | `1 + expr*4` | 读取 4 个数值表达式；以第 1 个参数取 type 3 资源对象，通道/音量参数夹到 `0..15`，调用 `sub_409EF0(...)` 播放或控制声音。 | [4117B0.c](export-for-ai/decompile/4117B0.c) |
| `0x32` | `MOVIE` | `1 + expr*6` | 读取媒体文件名字符串和 5 个数值参数，调用 `sub_40AC40(file, x, y, w, h, volume)` 初始化 DirectShow graph、连接视频/音频 renderer，并把视频窗口嵌到主窗口指定矩形；成功状态写入 `dword_499980[0]`。 | [415760.c](export-for-ai/decompile/415760.c)；[40AC40.c](export-for-ai/decompile/40AC40.c) |
| `0x33` | `EXPORT` | `1 + expr` | 读取资源 ID；若资源为 type4 原始数据块，则向当前序列化缓冲写入记录类型 `3`、长度和原始字节；若资源为 type2 位图，则写入记录类型 `4`、宽高和像素数据。 | [413AA0.c](export-for-ai/decompile/413AA0.c)；[40B800.c](export-for-ai/decompile/40B800.c)；[40B880.c](export-for-ai/decompile/40B880.c) |
| `0x34` | `WRITESTR` | `1 + expr` | 读取 1 个字符串表达式，把它以记录类型 `2` 写入当前脚本输出/序列化缓冲 `dword_498258`。必要时调用 `sub_40B6E0` 扩容；写入格式为 `0x02` 后接 UTF-16LE 零结尾字符串。 | [413A40.c](export-for-ai/decompile/413A40.c) |
| `0x35` | `WRITEINTS` | `1 + expr*2` | 读取起始全局数值变量索引和数量；若数量 `<= 0`，只写入一个记录类型 `1` 的 24-bit 整数；否则从 `NGLOBAL[start..start+count)` 逐个读取并按记录类型 `1` 写入当前序列化缓冲。 | [413930.c](export-for-ai/decompile/413930.c) |
| `0x36` | `TEXTGET` | `1 + expr + selector8 + expr` | 读取 type10 文本索引资源 ID，然后紧随脚本流读取一个选择符：选择符为 0 时读取行号，非 0 时读取字符串 key。命中后把原始行文本写入 `SARG[0]`，并按逗号拆出数值字段和字符串字段，数量分别写入 `dword_499980[2]`、`dword_499980[3]`。 | [4170E0.c](export-for-ai/decompile/4170E0.c) |
| `0x37` | `REGGET` | `1 + expr` | 读取 1 个字符串表达式作为注册表 value 名，从 `HKCU\Software\String1\word_4982D8` 查询字符串值，并写入 `SARG[0]`；键或值不存在时写空字符串。 | [414D30.c](export-for-ai/decompile/414D30.c) |
| `0x38` | `SLOTPOS` | `1 + expr*2` | 读取 type7 surface ID 和索引；取 surface 的槽/子结构指针 `surface[9]`，若最大值允许则把当前索引字段设置为给定值，并置 `byte_49998E` 请求刷新。 | [4142F0.c](export-for-ai/decompile/4142F0.c) |
| `0x39` | `SETSRC` | `1` | 不解析表达式；把全局 `Src` 赋给 `dword_498258` 并返回。 | [413E30.c](export-for-ai/decompile/413E30.c) |
| `0x3A` | `DOWNSCALE` | `1 + expr*3` | 读取图像资源 ID、目标宽、目标高 3 个数值表达式；要求该资源当前为 type2 图像。根据源图像尺寸与目标尺寸的比例选择固定缩小分支：常见路径覆盖 2x、3x、4x、6x、8x 等整数降采样，也包含若干特定比例插值分支；生成新的 type2 图像对象后释放原槽内容并写回同一资源 ID。 | [417BC0.c](export-for-ai/decompile/417BC0.c) |
| `0x3B` | `RESIZE` | `1 + expr*3` | 读取 3 个数值表达式；取 type7 surface，调用 `sub_40C580(surface, width, height)`。负宽/高沿用当前尺寸；尺寸变化时释放旧像素缓冲并用 `sub_402D60` 重建，调用 `sub_40C010` 重绘/填充并更新脏矩形。 | [412BA0.c](export-for-ai/decompile/412BA0.c)；[40C580.c](export-for-ai/decompile/40C580.c) |
| `0x3C` | `RETURN` | `1 + expr` | 读取 1 个数值表达式写入 `dword_499980[0]`；从当前 HXB 对象调用栈弹出返回地址并恢复 PC；空栈时报错。 | [411300.c](export-for-ai/decompile/411300.c) |
| `0x3D` | `EXISTS` | `1 + expr` | 读取 1 个字符串表达式作为路径，调用 `sub_40B9F0(path)`，结果布尔值写入 `dword_499980[0]`。 | [413E40.c](export-for-ai/decompile/413E40.c) |
| `0x3E` | `SAVEIMG` | `1 + expr*2 + paramblock` | 读取图像资源 ID、输出路径字符串并解析参数块；要求资源为 type2 位图。参数 0/1/2 分别选择 BMP、RLE 类自定义格式、或调用 `sub_423020` 的编码器输出；返回成功状态写入 `dword_499980[0]`。 | [4161D0.c](export-for-ai/decompile/4161D0.c) |
| `0x3F` | `KEYSTATE` | `1` | 不读取表达式；调用 `GetKeyboardState` 并把 256 个键状态字节写入 `dword_499980[0..255]`，同时把 IME/特殊输入标志映射到索引 5、6、7 后清零这些一次性标志。 | [415EC0.c](export-for-ai/decompile/415EC0.c) |
| `0x40` | `FULLSCR` | `1 + expr` | 读取 1 个数值表达式控制显示模式；负值时恢复默认显示并最小化窗口，0/正值时调用 `sub_407D00(value == 0)` 在原始显示模式与匹配游戏尺寸的全屏/显示设置之间切换，成功状态写入 `dword_499980[0]`。 | [415150.c](export-for-ai/decompile/415150.c)；[407D00.c](export-for-ai/decompile/407D00.c) |
| `0x41` | `FINDDRV` | `1 + expr*2` | 读取卷标字符串和数值标志；枚举逻辑盘，若标志为 0 接受所有盘，否则只接受固定磁盘。成功时把根路径如 `C:\` 写入 `SARG[0]`，盘序号 `A=1` 写入 `dword_499980[0]`；失败写空字符串并返回 0。注意代码调用了 `lstrcmpW` 但未使用比较结果，实际更像“取第一个可用卷”。 | [414FD0.c](export-for-ai/decompile/414FD0.c) |
| `0x42` | `SETAPPKEY` | `1 + expr*2` | 读取 2 个字符串表达式，分别复制到全局 `String1` 和 `word_4982D8`。后续注册表 opcode 会用它们拼出 `HKCU\Software\String1\word_4982D8` 子键路径。 | [414C80.c](export-for-ai/decompile/414C80.c) |
| `0x43` | `ALPHA` | `1 + expr*2` | 读取 2 个数值表达式；取 type7 surface，调用 `sub_40C6A0(alpha, surface)` 设置 `surface+28` 的 0..255 透明/混合参数，超出 255 夹为 255，负数夹为 0；若 surface 可见则加入脏矩形。 | [4130A0.c](export-for-ai/decompile/4130A0.c)；[40C6A0.c](export-for-ai/decompile/40C6A0.c) |
| `0x44` | `WRITEBYTE` | `1 + expr*3` | 读取资源 ID、偏移、字节值三个数值表达式；要求资源槽类型为 `4` 的二进制数据块，检查偏移范围后把低 8 位写入数据块。 | [415C70.c](export-for-ai/decompile/415C70.c) |
| `0x45` | `WRITEWSTR` | `1 + expr*3` | 读取 type4 二进制资源 ID、字节偏移、字符串 3 个表达式；把 UTF-16 字符串连同结尾 `0x0000` 直接写入资源数据块的指定偏移，返回写入字节数到 `dword_499980[0]`；资源不存在时返回 0。 | [4177D0.c](export-for-ai/decompile/4177D0.c) |
| `0x46` | `OPTION` | `1 + expr*2` | 读取选项 ID 和数值 2 个表达式并设置运行时全局配置：`0` 控制失焦/尺寸变化时的暂停或刷新策略标志 `dword_498234`，`1` 控制激活/非激活时的刷新策略标志 `dword_498238`，`2` 控制剪贴板快捷键是否禁用，`5` 设置脚本版本号 `dword_4999A8`，`6` 控制图像绘制时的裁剪/透明相关开关，`7` 设置缩放/居中显示模式并可能重算窗口，`8` 切换 layered window 透明窗口模式并重建 DIB，`9` 设置最小 10、最大 600 的帧/等待间隔，`10` 控制主循环是否自动刷新/休眠。 | [41A9C0.c](export-for-ai/decompile/41A9C0.c) |
| `0x47` | `IMEPOS` | `1 + expr*2` | 读取 2 个数值表达式作为输入法候选/组合窗口位置，调用 `sub_406E40(x, y)`；该 helper 保存坐标并通过 `ImmSetCompositionWindow` 设置 IME composition window。 | [4176E0.c](export-for-ai/decompile/4176E0.c)；[406E40.c](export-for-ai/decompile/406E40.c) |
| `0x48` | `BLEND` | `1 + expr*4` | 读取 4 个数值表达式；源可为 type2 位图或 type7 surface，另外取两个 type2 位图作为 mask/混合源，要求三者尺寸一致；第 4 个参数为 1..255 混合强度。函数逐像素按 alpha/mask 计算 RGB 和 alpha，并在目标为 type7 时标记脏矩形。 | [4151F0.c](export-for-ai/decompile/4151F0.c)；[40BE60.c](export-for-ai/decompile/40BE60.c) |
| `0x49` | `MOUSEMOVE` | `1 + expr*2` | 读取客户端坐标 `x,y` 两个数值表达式，调用 `ClientToScreen(hWnd, &Point)` 转为屏幕坐标后 `SetCursorPos` 移动鼠标指针。 | [415B10.c](export-for-ai/decompile/415B10.c) |
| `0x4A` | `SNDSEEK` | `1 + expr*2` | 读取 2 个数值表达式；取 type3 声音资源，`sub_40A650` 读取音频格式信息，按每单位字节数计算偏移，把 `arg1 * bytes_per_unit` 写入声音对象字段 `+44`，用于后续播放/定位。 | [419520.c](export-for-ai/decompile/419520.c)；[40A650.c](export-for-ai/decompile/40A650.c) |
| `0x4B` | `SNDVOL` | `1 + expr*3` | 读取 3 个数值表达式；取 type3 声音资源，调用 `sub_409D00(value, sound, duration)`。`duration` 非 0 时设置渐变目标、开始时间和持续时间；为 0 时立即换算并调用底层声音接口设置音量/位置类参数。 | [415670.c](export-for-ai/decompile/415670.c)；[409D00.c](export-for-ai/decompile/409D00.c) |
| `0x4C` | `FILLGLOBAL` | `1 + expr*3` | 读取起始索引、数量、填充值 3 个数值表达式；若范围合法，用 `memset32` 把全局数值变量表 `dword_499978[start..start+count)` 填成指定值。 | [417440.c](export-for-ai/decompile/417440.c) |
| `0x4D` | `SHELL` | `1 + expr*3` | 读取 3 个字符串表达式：operation、file/command、directory；将 file 中第一个空格后的部分拆为参数，调用 `ShellExecuteW(hWndParent, operation, file, params, dir, SW_SHOW)`，返回值写入 `dword_499980[0]`。 | [4166D0.c](export-for-ai/decompile/4166D0.c) |
| `0x4E` | `SHOW` | `1 + expr*2` | 读取 2 个数值表达式；若第 1 个参数小于 0，则按第 2 个参数调用 `ShowWindow(hWndParent, SW_SHOW/SW_HIDE)`；否则取 type 7 资源并调用 `sub_40BEF0(obj, flag)`，把原状态写入 `dword_499980[0]`。 | [4116D0.c](export-for-ai/decompile/4116D0.c) |
| `0x4F` | `SLEEP` | `1 + expr` | 读取 1 个数值表达式作为毫秒数，最大夹到 25；必要时调用 `sub_4083A0(0)` 刷新，再 `Sleep(ms)`。 | [415940.c](export-for-ai/decompile/415940.c) |
| `0x50` | `SNDCTRL` | `1 + expr*3` | 读取 3 个数值表达式；第 1 个参数夹到 `0..15` 作为通道索引，若音频系统已初始化则调用 `sub_409E70(arg1, dword_497368[channel], arg2)`。 | [411900.c](export-for-ai/decompile/411900.c) |
| `0x51` | `MOVIESTOP` | `1` | 无表达式解析；调用 `sub_40AB50()` 释放 DirectShow/媒体相关接口、清除 `dword_498288` 播放标志、刷新画面并把焦点/激活窗口还给主窗口。 | [415910.c](export-for-ai/decompile/415910.c)；[40AB50.c](export-for-ai/decompile/40AB50.c) |
| `0x52` | `STRCAT` | `1 + expr*2` | 读取目标字符串变量索引和字符串表达式；把字符串追加到字符串空间 18 的对应槽，并写回同一槽。若拼接后长度超过约 1000 字符，会临时分配堆缓冲。 | [411AC0.c](export-for-ai/decompile/411AC0.c) |
| `0x53` | `STRCMP` | `1 + expr*2` | 读取 2 个字符串表达式，调用 `lstrcmpW(str0, str1)`，比较结果写入 `dword_499980[0]`。 | [411C30.c](export-for-ai/decompile/411C30.c) |
| `0x54` | `STRSET` | `1 + expr*2` | 读取目标字符串变量索引和字符串表达式；把字符串直接写入字符串空间 18 的对应槽。 | [411A00.c](export-for-ai/decompile/411A00.c) |
| `0x55` | `STRFIND` | `1 + expr*4` | 读取字符串、目标字符/字符码、起始位置、方向标志 4 个表达式；方向标志为 0 时从起始位置向后查找，为非 0 时从末尾/指定位置向前查找。找到则索引写入 `dword_499980[0]`，失败返回 -1。 | [411E10.c](export-for-ai/decompile/411E10.c) |
| `0x56` | `STRESCAPE` | `1 + expr*2` | 读取目标字符串变量索引和模板字符串；`sub_40E270` 展开反斜杠转义以及 `\c[...]` 变量引用等内嵌表达式，结果写入字符串空间 18 的对应槽。 | [411D40.c](export-for-ai/decompile/411D40.c)；[40E270.c](export-for-ai/decompile/40E270.c) |
| `0x57` | `STRLEN` | `1 + expr` | 读取 1 个字符串表达式，统计 UTF-16 字符数写入 `dword_499980[0]`，同时把字节数 `chars * 2` 写入 `dword_499980[1]`。 | [411CD0.c](export-for-ai/decompile/411CD0.c) |
| `0x58` | `STRSPLIT` | `1 + expr*2` | 读取字符串和分隔字符/字符码；按分隔字符切分字符串，每段依次写入字符串空间 19，段数写入 `dword_499980[0]`。 | [417540.c](export-for-ai/decompile/417540.c) |
| `0x59` | `SUBSTR` | `1 + expr*4` | 读取目标字符串变量索引、源字符串、起始偏移、长度 4 个表达式；从源字符串截取子串写入字符串空间 18 的目标槽，并把首字符码写入 `dword_499980[0]`。 | [4140A0.c](export-for-ai/decompile/4140A0.c) |
| `0x5A` | `TEXTSIZE` | `1 + expr*2` | 读取字体资源 ID 和字符串；取 type5 字体对象，调用 `sub_402A40(text, font)` 测量/计算文本宽度或字形布局结果，并写入 `dword_499980[0]`；字体不存在时返回 0。 | [415FA0.c](export-for-ai/decompile/415FA0.c)；[402A40.c](export-for-ai/decompile/402A40.c) |
| `0x5B` | `TRANS` | `1 + expr*6` | 读取 6 个表达式；从两个同尺寸 type2/type7 图像取源缓冲，目标槽由第 1 个参数指定。目标为空时创建 type2 缓冲；目标为 type2 调 `sub_406800`，目标为 type7 调 `sub_40C430` 并合并脏矩形。`sub_406800` 按 mode `0..0x10` 选择不同转场/混合算法，进度约为 `10000 * step / total`。 | [416840.c](export-for-ai/decompile/416840.c)；[406800.c](export-for-ai/decompile/406800.c)；[40C430.c](export-for-ai/decompile/40C430.c) |
| `0x5C` | `BREAK` | `1 + expr` | 读取 1 个数值表达式；设置 `byte_46592C = value != 0`。若置位且非特殊状态，则调用 `sub_4083A0(0)`；用于让主循环暂停/跳出当前连续执行。 | [413030.c](export-for-ai/decompile/413030.c) |
| `0x5D` | `WAIT` | `1` | 不解析表达式；设置 `dword_4982CC = 1`、清零相关输入/状态字段，使 VM 进入等待输入/事件模式。 | [413450.c](export-for-ai/decompile/413450.c) |
| `0x5E` | `REGSET` | `1 + expr*2` | 读取 value 名和值两个字符串表达式；目标键为 `HKCU\Software\String1\word_4982D8`。若二者都为空则删除该子键，否则打开或创建子键并用 `RegSetValueExW(..., REG_SZ, ...)` 写入字符串值。 | [414E40.c](export-for-ai/decompile/414E40.c) |
| `0x5F` | `READDATA` | `1 + expr*3` | 读取 type4 二进制资源 ID、偏移、长度 3 个数值表达式；长度为 0 时把偏移处的 UTF-16 字符串写入 `SARG[0]`，并返回字符数及版本 `>=1210` 时的字节数；长度为 1/2/4 时按小端读取整数到 `dword_499980[0]`，其他长度返回 0。 | [4195D0.c](export-for-ai/decompile/4195D0.c) |
| `0xFF` | `END` | `1` | 特殊 opcode，不走 `funcs_411044`；由 `sub_410FC0` 直接处理。结束当前 HXB 上下文：若对象需释放则调用 `sub_40E880`，否则退回链表上一层。 | [410FC0.c](export-for-ai/decompile/410FC0.c) |

## 精化结论

本表已完成 `0x00..0x5F` 与特殊 `0xFF` 的顶层 opcode 命名、长度/格式、参数语义、主要副作用和佐证函数整理。后续若进入反汇编器实现阶段，应优先把 `expr`、`paramblock`、`target24` 与扩展头 2 字节前缀建成结构化 AST/IR，以保证重汇编时能逐字节还原。
