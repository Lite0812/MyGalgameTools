# MEBIUS34 TXT Opcode 分析与逆向记录

## 文档目标

- 说明 MEBIUS34.exe 如何加载并执行 TXT/SEENxxxx.TXT 二进制脚本。
- 记录当前汇编/反汇编工具所依赖的格式结论、控制流结论与重定位约束。
- 给出完整 opcode 表，包含助记名、handler、长度模型、操作数模式与逆向证据。
- 说明哪些名字已经可以精确落到行为，哪些名字仍然只是保守的家族级命名。

## 范围

- 依据来源：export-for-ai/decompile/472260.c 中的分发表，以及对 MEBIUS34.exe 的直接 objdump 汇编校验。
- 显式赋值的有效 opcode 数量：174
- 未赋值表项的默认处理函数：sub_4035E0
- 本文把所有未显式写入 dword_A0D7F0 的 opcode 视为无效或至少未支持，因为它们都会落回默认处理函数，而不是脚本专用实现。

## 执行模型

- 主 TXT 脚本会整文件读入内存，主程序计数器从偏移 2 开始，因此文件前 2 字节是头部/保留字。
- 解释器每次先读取 16 位大端 opcode，再把 pc 前进 2 字节，然后通过 dword_A0D7F0[opcode] 分发。
- 主上下文使用 dword_A7B98C + dword_A00770；副上下文使用 dword_4DB1E8 + dword_4DB07C。
- 各类 tagged operand 在 tag 非 0 时会经由 dword_94B2E8 查表，因此部分操作数的实际字节数依赖数据内容。

## 操作数编码类型

- tag8：逻辑上是 1 字节值；立即数形式占 2 字节，查表形式占 3 字节。
- tag8_index：编码宽度与 tag8 相同，但查表结果会按索引语义做调整。
- tag16：固定 3 字节载荷。
- tag32：查表形式占 3 字节，立即数 32 位形式占 5 字节。
- tag32_x100：编码宽度与 tag32 相同，但查表结果会乘以 100。
- raw8：直接从脚本流读取的 1 字节原始值。
- raw16：直接从脚本流读取的 2 字节大端原始值。
- raw_be32：直接从脚本流读取的 4 字节大端原始值。
- raw_cstring：以 0 结尾的原始字节串，长度取决于终止位置。
- cstring_array：由计数字段控制的一串以 0 结尾字符串块。
- be16_length/raw32/blob：0x0040 使用的变长 blob 头，总长度为 2 + 4 + blob_size。
- text0/text1：以原始 0xAA 或 0xAB 终止的 XOR-0xAA 文本流。

## 文本与表达式系统

- 0x0000/0x0001 的文本体不是普通 cstring；普通字节都要做 raw ^ 0xAA 反解码，终止字节分别是原始 0xAA/0xAB。
- 原始字节序列 0x55 0xA8 在文本流中代表嵌入控制块，不能按普通字符处理。
- 0x0040 同时携带条件 blob 与真实脚本目标偏移；当前工具已经能把常见表达式单元语义化为 cond{...}。
- 对整库脚本扫描后，没有发现表达式子 opcode 自身还带有脚本内跳转目标这类二级控制流语义；当前确认的内部脚本目标只存在于主指令层。

## 跳转与重定位

- 当前已确认的内部脚本目标操作数：0x0030 的第 0 个操作数、0x0031 的第 0 个操作数、0x0040 的第 1 个操作数。
- 反汇编阶段会基于规格层声明自动收集引用目标并打 label，而不是在 formatter 里硬编码 opcode 特判。
- 汇编阶段会先完整计算所有指令长度，得到真实指令起始偏移，再把 label 解析回绝对脚本偏移。
- 如果用户保留了过期的绝对偏移，而且该偏移不再落在任何真实指令起点上，汇编器会直接报错，而不是静默生成坏脚本。
- 这意味着：只要脚本内部跳转继续写成 label，即使文本长度变化、表达式 blob 变化、或插删指令，重新汇编后的脚本仍可被 EXE 正确解析。

## 逆向过程

1. 先从 export-for-ai/decompile/472260.c 提取 dword_A0D7F0 的显式赋值，建立有效 opcode 集。
2. 再回到 sub_46E1E0 确认解释器的取码方式、大小端方向、主/副上下文切换，以及初始 pc=2。
3. 对各类读参 helper 做分类，确定 tag8/tag16/tag32/raw16/raw32/text/blob 的真实宽度与变长规则。
4. 先实现能稳定 roundtrip 的汇编/反汇编，再通过 TXT、TXT1、TXT2、TXT3、TXT4 五组语料把长度模型、文本编码和变长指令逐项校准。
5. 对控制流相关 opcode 单独验证，确认 0x0040 的第二个 raw32 才是实际脚本目标，并把脚本内目标声明集中收敛到规格层。
6. 在回归稳定后再做助记名收敛。命名优先使用 handler 的直接行为；如果只能确认到家族，则使用保守家族名；如果两个 opcode 共享同一 handler，则允许使用 alias 形式避免误导成“不同功能”。

## 命名约定

- 当前已经没有 fallback 形式的十六进制后缀助记名。
- 但这不代表所有名字都已经精确到最终产品语义。
- 对若干只确认到家族、布尔变体或 alias 入口的 opcode，文档会使用 a/b、plain/flagged、alias 等保守命名。
- 这类命名的目的，是在“不乱猜”的前提下保留结构信息，避免退回 cmd_xxxx 或 fallback_xxxx 这类无信息名称。

## 回归验证

- asm/disasm/asm 全量回归目录：TXT、TXT1、TXT2、TXT3、TXT4。
- 当前整库 roundtrip 已验证为 1072 个脚本字节一致。
- 本轮命名收敛后再次统计，fallback 助记名数量已为 0。

## 类型统计

- 跨文件调用：2
- 计数字符串块：1
- 计数字符串块16：1
- 计数标志块：1
- 计数标志块+尾3字节：1
- 表达式+blob：1
- 取参型：152
- 原始16位设槽：1
- 原始32位：10
- 返回：1
- tag16_tag32 对表：1
- 文本0：1
- 文本1：1

## 未解决 Handler

- 无

## Opcode 表

| Opcode | 助记名 | 实现 | 长度 | 类型 | 操作数 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 0x0000 | text_aa | 0x43C5B0 | 2 + text_body + 1 | 文本0 | - | 载荷以原始 0xAA 终止；普通字节按 raw_byte ^ 0xAA 解码；原始 0x55 0xA8 表示内嵌控制块 |
| 0x0001 | text_ab | 0x43D100 | 2 + text_body + 1 | 文本1 | - | 载荷以原始 0xAB 终止；普通字节按 raw_byte ^ 0xAA 解码；原始 0x55 0xA8 表示内嵌控制块 |
| 0x0003 | set_save_bit | 0x43D720 | 2 + 2 | 取参型 | raw16 | 由 decompile/43D720.c 与 decompile/41CDC0.c 控制流确认；先读取一个 16 位大端存档位标识，再更新相关菜单状态 |
| 0x0004 | set_save_bit_tag32 | 0x43D7C0 | 2 + 2 + 3|5 | 取参型 | raw16, tag32 | 由 decompile/43D7C0.c 与 decompile/41CDC0.c 控制流确认；先读取一个 16 位大端存档位标识，再读取一个 tag32 参数 |
| 0x0005 | check_flag_transition | 0x43D860 | 2 | 取参型 | - | - |
| 0x0006 | begin_scene_state | 0x401000 | 2 | 取参型 | - | - |
| 0x0007 | latch_scene_state | 0x401020 | 2 | 取参型 | - | - |
| 0x0008 | load_tagged_resource | 0x422D50 | 2 + 3|5 | 取参型 | tag32 | - |
| 0x0009 | load_tagged_resource_alt | 0x422DB0 | 2 + 3|5 | 取参型 | tag32 | - |
| 0x000A | poll_flag_transition | 0x43D9F0 | 2 + 2|3 + 2|3 | 取参型 | tag8, tag8 | - |
| 0x000B | set_flag_pair | 0x422E20 | 2 + 2|3 + 2|3 | 取参型 | tag8, tag8 | - |
| 0x000C | clear_slot_bank_a_save | 0x401140 | 2 | 取参型 | - | 主路径控制流中包含循环 |
| 0x000D | clear_slot_bank_a | 0x4012B0 | 2 | 取参型 | - | 主路径控制流中包含循环 |
| 0x000E | clear_slot_bank_b_save | 0x401310 | 2 | 取参型 | - | 主路径控制流中包含循环 |
| 0x000F | clear_slot_bank_b | 0x401400 | 2 | 取参型 | - | 主路径控制流中包含循环 |
| 0x0010 | set_slot_raw16 | 0x401460 | 2 + 2 | 原始16位设槽 | raw16 | 直接从脚本流读取 16 位大端变量槽编号 |
| 0x0011 | copy_slot_ref16 | 0x422EA0 | 2 + 2 | 取参型 | raw16 | 由 MEBIUS34.exe .text 0x422EA0 汇编确认；读取 16 位大端表索引，并将该槽值复制到当前目标槽 |
| 0x0012 | set_slot_imm32 | 0x422F50 | 2 | 原始32位 | raw_be32 | 主路径体内直接访问程序计数器；直接从脚本流读取一个 32 位大端立即数 |
| 0x0013 | add_slot_ref16 | 0x423000 | 2 + 2 | 取参型 | raw16 | 由 decompile/423000.c 控制流确认；读取 16 位大端表索引，并将该槽值加到当前目标槽 |
| 0x0014 | add_slot_imm32 | 0x4230B0 | 2 | 原始32位 | raw_be32 | 主路径体内直接访问程序计数器；直接从脚本流读取一个 32 位大端立即数 |
| 0x0015 | sub_slot_ref16 | 0x423160 | 2 + 2 | 取参型 | raw16 | 由 decompile/423160.c 控制流确认；读取 16 位大端表索引，并从当前目标槽减去该槽值 |
| 0x0016 | sub_slot_imm32 | 0x423210 | 2 | 原始32位 | raw_be32 | 主路径体内直接访问程序计数器；直接从脚本流读取一个 32 位大端立即数 |
| 0x0017 | mul_slot_ref16 | 0x4232C0 | 2 + 2 | 取参型 | raw16 | 由 decompile/4232C0.c 控制流确认；读取 16 位大端表索引，并将当前目标槽乘以该槽值 |
| 0x0018 | mul_slot_imm32 | 0x423380 | 2 | 原始32位 | raw_be32 | 主路径体内直接访问程序计数器；直接从脚本流读取一个 32 位大端立即数 |
| 0x0019 | div_slot_ref16 | 0x423440 | 2 + 2 | 取参型 | raw16 | 由 MEBIUS34.exe .text 0x423440 汇编确认；读取 16 位大端表索引，并用该槽值做整除运算 |
| 0x001A | div_slot_imm32 | 0x423500 | 2 | 原始32位 | raw_be32 | 主路径体内直接访问程序计数器；直接从脚本流读取一个 32 位大端立即数 |
| 0x001B | mod_slot_ref16 | 0x4235C0 | 2 + 2 | 取参型 | raw16 | 由 MEBIUS34.exe .text 0x4235C0 汇编确认；读取 16 位大端表索引，并将整除余数写回当前槽 |
| 0x001C | mod_slot_imm32 | 0x423680 | 2 | 原始32位 | raw_be32 | 主路径体内直接访问程序计数器；直接从脚本流读取一个 32 位大端立即数 |
| 0x001D | or_slot_ref16 | 0x423740 | 2 + 2 | 取参型 | raw16 | 由 MEBIUS34.exe .text 0x423740 汇编确认；读取 16 位大端表索引，并将其与当前槽做按位或 |
| 0x001E | or_slot_imm32 | 0x4237F0 | 2 | 原始32位 | raw_be32 | 主路径体内直接访问程序计数器；直接从脚本流读取一个 32 位大端立即数 |
| 0x001F | and_slot_ref16 | 0x4238A0 | 2 + 2 | 取参型 | raw16 | 由 decompile/4238A0.c 控制流确认；读取 16 位大端表索引，并将其与当前槽做按位与 |
| 0x0020 | and_slot_imm32 | 0x423950 | 2 | 原始32位 | raw_be32 | 主路径体内直接访问程序计数器；直接从脚本流读取一个 32 位大端立即数 |
| 0x0021 | xor_slot_ref16 | 0x423A00 | 2 + 2 | 取参型 | raw16 | 由 decompile/423A00.c 控制流确认；读取 16 位大端表索引，并将其与当前槽做按位异或 |
| 0x0022 | xor_slot_imm32 | 0x423AB0 | 2 | 原始32位 | raw_be32 | 主路径体内直接访问程序计数器；直接从脚本流读取一个 32 位大端立即数 |
| 0x0028 | define_string_table | 0x43DAA0 | 2 + 1 + 1 + strings_block | 计数字符串块 | raw8, raw8, cstring_array | 由 MEBIUS34.exe .text 0x43DAA0 汇编确认；先读取 1 字节条目数和 1 字节标志，再读取 count 个以 0 结尾的字符串 |
| 0x002C | random_div_ref16 | 0x423B70 | 2 + 3 | 取参型 | tag16 | 由 MEBIUS34.exe .text 0x423B70 汇编确认；读取 tag16，随后结合随机值与除法结果写回槽位 |
| 0x002D | check_install_game_registry | 0x423BE0 | 2 + 2|3 | 取参型 | tag8 | 主路径中包含条件读取 |
| 0x002E | check_install_game_env | 0x423EA0 | 2 + 2|3 | 取参型 | tag8 | - |
| 0x0030 | jump_abs32 | 0x4014E0 | 2 + 4 | 取参型 | raw_be32 | 由 decompile/4014E0.c 控制流确认；直接读取 32 位大端目标偏移，并把当前程序计数器改写为该绝对地址 |
| 0x0031 | jump_push_abs32 | 0x401590 | 2 + 4 | 原始32位 | raw_be32 | 由 MEBIUS34.exe .text 0x401590 汇编确认；直接读取 32 位大端目标偏移，并把当前程序计数器压入内部跳转栈 |
| 0x0032 | dispatch_engine_state | 0x401680 | 2 | 取参型 | - | 主路径控制流中包含循环；主路径体内直接访问程序计数器 |
| 0x0033 | call_seen_rotate | 0x43DB70 | 2 + 3 | 跨文件调用 | tag16 | 由 MEBIUS34.exe .text 0x43DB70 汇编确认；读取 tag16 后切换到另一个 SEEN 文件，并轮转一组返回栈缓冲区 |
| 0x0034 | return_seen | 0x43DCD0 | 2 | 返回 | - | 从返回栈恢复脚本编号和程序计数器 |
| 0x0035 | call_seen | 0x43DDF0 | 2 + 3 | 跨文件调用 | tag16 | 加载另一个 SEEN 文件；加载辅助脚本上下文；主路径控制流中包含循环；主路径体内直接访问程序计数器；压入当前文件/程序计数器并跳转到另一个 SEEN 文件的程序计数器偏移 2 处 |
| 0x0036 | select_install_game | 0x424190 | 2 + 2|3 | 取参型 | tag8 | 主路径中包含条件读取 |
| 0x0038 | mark_time_checkpoint | 0x4016E0 | 2 | 取参型 | - | - |
| 0x0039 | set_wait_timer_hold | 0x424560 | 2 + 3|5 | 取参型 | tag32 | - |
| 0x003A | set_wait_timer | 0x4245B0 | 2 + 3|5 | 取参型 | tag32 | 由 decompile/4245B0.c 控制流确认；读取一个 tag32，并更新一组等待/计时相关全局状态 |
| 0x003B | store_elapsed_ticks | 0x401720 | 2 | 取参型 | - | - |
| 0x003C | advance_wait_checkpoint | 0x424600 | 2 + 3|5 | 取参型 | tag32 | - |
| 0x0040 | jump_if_false | 0x401770 | 2 + 2 + 4 + blob_size | 表达式+blob | be16_length, raw32, blob | 读取 16 位大端二进制数据块长度和 32 位大端表达式参数；从脚本流复制 blob_size 字节，并按该长度推进程序计数器 |
| 0x0048 | dispatch_layout_state | 0x402120 | 2 | 取参型 | - | 主路径体内直接访问程序计数器 |
| 0x0049 | sample_pointer_state | 0x402240 | 2 | 取参型 | - | - |
| 0x004A | init_layout_slots9 | 0x4023E0 | 2 + 2*9 | 取参型 | raw16, raw16, raw16, raw16, raw16, raw16, raw16, raw16, raw16 | 由 MEBIUS34.exe .text 0x4023E0 汇编确认；连续读取 9 个 16 位大端槽位编号，用于后续布局/状态初始化 |
| 0x004B | reset_layout_groups | 0x424630 | 2 | 取参型 | - | 由 MEBIUS34.exe .text 0x424630 汇编确认；不从脚本流继续取参，只重置多组槽位和状态 |
| 0x0050 | define_layout_window | 0x424850 | 2 + 2|3 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32 | 主路径中包含条件读取 |
| 0x0051 | define_layout_motion | 0x4249D0 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 2|3 | 取参型 | tag8_index, tag32, tag32, tag32, tag32, tag8 | - |
| 0x0052 | define_layout_window_motion | 0x424AE0 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 2|3 | 取参型 | tag8_index, tag32, tag32, tag32, tag32, tag32, tag32, tag8 | 主路径中包含条件读取 |
| 0x0053 | clear_layout_window | 0x424D30 | 2 + 2|3 | 取参型 | tag8_index | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x0054 | set_layout_bounds | 0x424E70 | 2 + 2|3 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32 | 由 MEBIUS34.exe .text 0x424E70 汇编确认；读取 tag8_index、tag32、tag32 |
| 0x0058 | set_layout_transform | 0x424F20 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32, tag32_x100, tag32_x100, tag32_x100 | - |
| 0x0059 | set_layout_transform_ext | 0x425030 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32, tag32, tag32, tag32_x100, tag32_x100, tag32_x100 | - |
| 0x005A | reset_ui_bank_full | 0x4027E0 | 2 | 取参型 | - | 主路径控制流中包含循环 |
| 0x005B | reset_ui_bank_half | 0x402A70 | 2 | 取参型 | - | 主路径控制流中包含循环 |
| 0x005C | reset_ui_bank_aux | 0x402D00 | 2 | 取参型 | - | 主路径控制流中包含循环 |
| 0x0060 | init_named_resource | 0x43DEC0 | 2 + 2|3 + cstring+1 + 2|3 | 取参型 | tag8_index, raw_cstring, tag8 | 由 MEBIUS34.exe .text 0x43DEC0 汇编确认；读取 tag8_index、一个原始字符串和一个 tag8，用于资源名与状态初始化 |
| 0x0061 | define_mcg_slot | 0x43DFF0 | 2 + 2|3 + cstring+1 + 3|5 + 3|5 + 2|3 | 取参型 | tag8_index, raw_cstring, tag32, tag32, tag8 | - |
| 0x0062 | define_mcg_slot_basic | 0x43E160 | 2 + 2|3 + cstring+1 + 2|3 | 取参型 | tag8_index, raw_cstring, tag8 | - |
| 0x0063 | define_mcg_slot_pos | 0x43E2B0 | 2 + 2|3 + cstring+1 + 3|5 + 3|5 + 2|3 | 取参型 | tag8_index, raw_cstring, tag32, tag32, tag8 | - |
| 0x0068 | configure_resource_entry | 0x4251A0 | 2 + 2|3 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32 | 主路径中包含条件读取 |
| 0x0070 | dispatch_flagged_entries | 0x43E430 | 2 + 1 + count | counted_flags | raw8, blob | 由 decompile/43E430.c 控制流确认；先读取 1 字节条目数，再读取 count 个标志字节；非零条目会按当前索引调用 sub_437920 |
| 0x0071 | dispatch_flagged_entries_tail3 | 0x43E4E0 | 2 + 1 + count + 1 + 1 + 1 | counted_flags_tail3 | raw8, blob, raw8, raw8, raw8 | 由 decompile/43E4E0.c 控制流确认；先读取 1 字节条目数和 count 个标志字节；随后再读取 3 个附加标志字节，分别控制 sub_419B60(0..2) |
| 0x0072 | dispatch_all_entries_tail3 | 0x43E630 | 2 | 取参型 | - | 主路径控制流中包含循环 |
| 0x0073 | enable_dispatch_entry | 0x425590 | 2 + 2|3 + 2|3 | 取参型 | tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x0074 | select_dispatch_entry | 0x43E6E0 | 2 + 2|3 + 2|3 | 取参型 | tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x0075 | disable_dispatch_entry | 0x425830 | 2 + 2|3 + 2|3 | 取参型 | tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x0076 | select_dispatch_entry_alt | 0x43EA20 | 2 + 2|3 + 2|3 | 取参型 | tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x0077 | show_dispatch_entry | 0x425AD0 | 2 + 2|3 + 2|3 | 取参型 | tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x0078 | hide_dispatch_entry | 0x43ED20 | 2 + 2|3 + 2|3 | 取参型 | tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x0079 | set_global_flag | 0x425D70 | 2 + 2|3 | 取参型 | tag8 | 由 MEBIUS34.exe .text 0x425D70 汇编确认；读取一个 tag8 并更新全局状态标志 |
| 0x007A | clear_ui_cache_state | 0x402E70 | 2 | 取参型 | - | - |
| 0x007B | set_mask_entry_a | 0x425DC0 | 2 + 2|3 + 2|3 | 取参型 | tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x007C | start_mask_entry_a | 0x425F40 | 2 + 2|3 + 2|3 | 取参型 | tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x007D | set_mask_entry_b | 0x426100 | 2 + 2|3 + 2|3 | 取参型 | tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x007E | start_mask_entry_b | 0x426280 | 2 + 2|3 + 2|3 | 取参型 | tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x0080 | set_ui_move_target | 0x426460 | 2 + 2|3 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32 | 主路径中包含条件读取 |
| 0x0081 | start_ui_move | 0x4265A0 | 2 + 2|3 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32 | 主路径中包含条件读取 |
| 0x0084 | set_motion_geometry | 0x426720 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32, tag32, tag32, tag32 | 由 MEBIUS34.exe .text 0x426720 汇编确认；读取 1 个 tag8_index 和 6 个 tag32，用于一组几何/运动参数 |
| 0x0085 | set_ui_move_box | 0x4268B0 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32, tag32, tag32, tag32 | 主路径中包含条件读取 |
| 0x0086 | set_segment_params | 0x426B70 | 2 + 2|3 + 1 + 3|5 + 3|5 + 1 + 3|5 + 3|5 + 1 + 3|5 + 3|5 | 取参型 | tag8_index, raw8, tag32_x100, tag32, raw8, tag32_x100, tag32, raw8, tag32_x100, tag32 | 由 decompile/426B70.c 控制流确认；读取一个 tag8_index，以及 3 组(raw8 开关, tag32_x100, tag32)参数，用于更新三段状态/参数槽位 |
| 0x0087 | set_segment_curve | 0x426D70 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32_x100, tag32, tag32_x100, tag32, tag32_x100, tag32 | 主路径中包含条件读取 |
| 0x008C | set_ui_scale_x | 0x426FA0 | 2 + 2|3 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32 | 主路径中包含条件读取 |
| 0x008D | start_ui_scale_x | 0x4270A0 | 2 + 2|3 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32 | 主路径中包含条件读取 |
| 0x008E | set_ui_scale_y | 0x427200 | 2 + 2|3 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32 | 主路径中包含条件读取 |
| 0x008F | start_ui_scale_y | 0x427300 | 2 + 2|3 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32 | 主路径中包含条件读取 |
| 0x0090 | set_ui_motion2d | 0x427460 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32, tag32, tag32, tag32 | 主路径中包含条件读取 |
| 0x0091 | start_ui_motion2d | 0x427600 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32, tag32, tag32, tag32 | 主路径中包含条件读取 |
| 0x0092 | set_ui_motion3d | 0x427860 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32, tag32, tag32, tag32, tag32, tag32, tag32 | 主路径中包含条件读取 |
| 0x0093 | start_ui_motion3d | 0x427AA0 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32, tag32, tag32, tag32, tag32, tag32, tag32 | 主路径中包含条件读取 |
| 0x0096 | define_ui_motion_path | 0x427C80 | 2 + 2|3 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32_x100, tag32_x100, tag32_x100 | 主路径控制流中包含循环；主路径体内直接访问程序计数器；主路径中包含条件读取 |
| 0x0097 | prepare_ui_motion_path | 0x4281C0 | 2 + 2|3 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32_x100, tag32_x100, tag32_x100 | 主路径控制流中包含循环；主路径体内直接访问程序计数器；主路径中包含条件读取 |
| 0x0098 | load_mcg_batch | 0x43F030 | 2 + 2|3 + 3|5 + 3|5 + 2 + strings_block | counted_cstrings16 | tag8_index, tag32, tag32, raw16, cstring_array | 由 decompile/43F030.c 控制流确认；读取 tag8_index、两个 tag32、一个 16 位字符串数，再读取 count 个以 0 结尾的资源名字符串并批量加载对应 .mcg 文件 |
| 0x0099 | apply_ui_timeline | 0x428CA0 | 2 + 2|3 | 取参型 | tag8_index | 主路径控制流中包含循环；主路径体内直接访问程序计数器 |
| 0x009A | define_ui_timeline_a | 0x43FB90 | 2 + 2|3 | 取参型 | tag8_index | 主路径控制流中包含循环；主路径体内直接访问程序计数器 |
| 0x009B | define_pair_table | 0x428F90 | 2 + 2|3 + 2 + count*(3 + 3|5) | tag16_tag32_pairs | tag8_index, raw16, pair_table | 由 decompile/428F90.c 控制流确认；读取一个 tag8_index、一个 16 位条目数，然后依次读取 count 组(tag16, tag32)参数表 |
| 0x009C | define_ui_timeline_b | 0x440010 | 2 + 2|3 | 取参型 | tag8_index | 主路径控制流中包含循环；主路径体内直接访问程序计数器 |
| 0x009D | step_ui_timeline | 0x429280 | 2 + 2|3 | 取参型 | tag8_index | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x009E | define_ui_path_points | 0x4296E0 | 2 + 2|3 | 取参型 | tag8_index | 主路径控制流中包含循环；主路径体内直接访问程序计数器；主路径中包含条件读取 |
| 0x009F | start_ui_path_points | 0x4299E0 | 2 + 2|3 | 取参型 | tag8_index | 主路径控制流中包含循环；主路径体内直接访问程序计数器 |
| 0x00A0 | set_ui_quad_rect | 0x429E50 | 2 + 2|3 + 3|5 + 3|5 + 3|5 + 3|5 | 取参型 | tag8_index, tag32, tag32, tag32, tag32 | - |
| 0x00A1 | arm_ui_entry | 0x429F20 | 2 + 2|3 | 取参型 | tag8_index | 主路径中包含条件读取 |
| 0x00C0 | trigger_save_state | 0x402EC0 | 2 | 取参型 | - | - |
| 0x00C1 | write_save_timestamp | 0x429F70 | 2 + 3 | 取参型 | tag16 | 主路径中包含条件读取 |
| 0x00C2 | load_secondary_seen | 0x440490 | 2 | 取参型 | - | 加载辅助脚本上下文；主路径控制流中包含循环；主路径体内直接访问程序计数器 |
| 0x00C3 | define_save_entry_text | 0x471A60 | 2 + 3 + cstring+1 + 2|3 | 取参型 | tag16, raw_cstring, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x00C4 | read_save_timestamp_slots | 0x42A0A0 | 2 + 3 + 2*7 | 取参型 | tag16, raw16, raw16, raw16, raw16, raw16, raw16, raw16 | 由 decompile/42A0A0.c 控制流确认；读取一个 tag16 存档号，以及 7 个 raw16 变量槽位；成功时把存档中的 7 字节时间戳/状态字段写入这些槽位 |
| 0x00C5 | define_save_entry_slot | 0x440500 | 2 + 3 + 2|3 + 2|3 | 取参型 | tag16, tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x00C6 | define_save_entry_rect | 0x440930 | 2 + 3 + 2|3 + 3|5 + 3|5 + 2|3 | 取参型 | tag16, tag8_index, tag32, tag32, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x00C7 | define_save_entry_state | 0x440D90 | 2 + 3 + 2|3 + 2|3 | 取参型 | tag16, tag8_index, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x00C8 | define_save_entry_state_rect | 0x441260 | 2 + 3 + 2|3 + 3|5 + 3|5 + 2|3 | 取参型 | tag16, tag8_index, tag32, tag32, tag8 | 主路径控制流中包含循环；主路径中包含条件读取 |
| 0x00E0 | request_voice_a | 0x42A4F0 | 2 + cstring+1 + 2|3 | 取参型 | raw_cstring, tag8_index | - |
| 0x00E1 | request_voice_b | 0x42A560 | 2 + cstring+1 + 2|3 | 取参型 | raw_cstring, tag8_index | - |
| 0x00E2 | request_voice_c | 0x42A5D0 | 2 + cstring+1 + 2|3 | 取参型 | raw_cstring, tag8_index | - |
| 0x00E3 | request_voice_d | 0x42A640 | 2 + cstring+1 + 2|3 | 取参型 | raw_cstring, tag8_index | - |
| 0x00E4 | request_voice_slot | 0x42A6B0 | 2 + 2|3 | 取参型 | tag8_index | 主路径中包含条件读取 |
| 0x00E5 | refresh_media_group_a | 0x402EE0 | 2 | 取参型 | - | - |
| 0x00E6 | request_voice_param_a | 0x42A6F0 | 2 + cstring+1 + 2|3 + 3|5 | 取参型 | raw_cstring, tag8_index, tag32 | - |
| 0x00E7 | request_voice_param_b | 0x42A780 | 2 + cstring+1 + 2|3 + 3|5 | 取参型 | raw_cstring, tag8_index, tag32 | - |
| 0x00E8 | request_voice_param_c | 0x42A810 | 2 + cstring+1 + 2|3 + 3|5 | 取参型 | raw_cstring, tag8_index, tag32 | - |
| 0x00E9 | request_voice_param_d | 0x42A8A0 | 2 + cstring+1 + 2|3 + 3|5 | 取参型 | raw_cstring, tag8_index, tag32 | - |
| 0x00EA | set_media_group_a_value | 0x42A930 | 2 + 2|3 + 3|5 | 取参型 | tag8_index, tag32 | - |
| 0x00EB | broadcast_tag32_10x | 0x42A9A0 | 2 + 3|5 | 取参型 | tag32 | 由 MEBIUS34.exe .text 0x42A9A0 汇编确认；读取一个 tag32，并把同一数值广播到 10 组槽位 |
| 0x00EE | request_voice_named_a | 0x42AA40 | 2 + cstring+1 + 2|3 | 取参型 | raw_cstring, tag8_index | 主路径中包含条件读取 |
| 0x00EF | request_voice_named_b | 0x42AB00 | 2 + cstring+1 + 2|3 | 取参型 | raw_cstring, tag8_index | 主路径中包含条件读取 |
| 0x00F0 | set_media_group_mode | 0x42AC00 | 2 + 2|3 + 2|3 | 取参型 | tag8, tag8_index | 由 MEBIUS34.exe .text 0x42AC00 汇编确认；读取 tag8、tag8_index |
| 0x00F1 | request_bgm_a | 0x42ACB0 | 2 + 2|3 + 2|3 | 取参型 | tag8, tag8_index | - |
| 0x00F2 | request_bgm_b | 0x42AD60 | 2 + 2|3 + 2|3 | 取参型 | tag8, tag8_index | - |
| 0x00F3 | request_bgm_slot | 0x42AE10 | 2 + 2|3 | 取参型 | tag8_index | 主路径中包含条件读取 |
| 0x00F4 | refresh_media_group_b | 0x402F40 | 2 | 取参型 | - | - |
| 0x00F5 | request_bgm_param_a | 0x42AE50 | 2 + 2|3 + 2|3 + 3|5 | 取参型 | tag8, tag8_index, tag32 | - |
| 0x00F6 | request_bgm_param_b | 0x42AF30 | 2 + 2|3 + 2|3 + 3|5 | 取参型 | tag8, tag8_index, tag32 | - |
| 0x00F7 | request_bgm_param_c | 0x42B010 | 2 + 2|3 + 2|3 + 3|5 | 取参型 | tag8, tag8_index, tag32 | - |
| 0x00F8 | set_media_group_b_value | 0x42B0F0 | 2 + 2|3 + 3|5 | 取参型 | tag8_index, tag32 | - |
| 0x00F9 | set_ui_value | 0x42B170 | 2 + 3|5 | 取参型 | tag32 | 主路径中包含条件读取 |
| 0x0100 | arm_ui_state_entry | 0x42ABC0 | 2 + 2|3 | 取参型 | tag8_index | 主路径中包含条件读取 |
| 0x0101 | arm_bgm_slots_all | 0x402F20 | 2 | 取参型 | - | - |
| 0x0104 | request_bgm_named | 0x42B1C0 | 2 + 2|3 + 2|3 | 取参型 | tag8, tag8_index | - |
| 0x0110 | release_ui_buffers | 0x402F60 | 2 | 取参型 | - | 主路径控制流中包含循环 |
| 0x0111 | enable_ui_context | 0x42B270 | 2 | 取参型 | - | - |
| 0x0112 | disable_ui_context | 0x42B290 | 2 | 取参型 | - | - |
| 0x0113 | enable_ui_mode_a | 0x403380 | 2 | 取参型 | - | - |
| 0x0114 | disable_ui_mode_a | 0x4033C0 | 2 | 取参型 | - | - |
| 0x0115 | enable_ui_mode_b | 0x4033E0 | 2 | 取参型 | - | - |
| 0x0116 | disable_ui_mode_b | 0x403420 | 2 | 取参型 | - | - |
| 0x0120 | begin_frame_hold | 0x403460 | 2 | 取参型 | - | - |
| 0x0121 | clear_frame_hold | 0x403480 | 2 | 取参型 | - | - |
| 0x0122 | enable_ui_plane_a | 0x4034B0 | 2 | 取参型 | - | - |
| 0x0123 | disable_ui_plane_a | 0x4034E0 | 2 | 取参型 | - | - |
| 0x0124 | enable_ui_plane_b | 0x403500 | 2 | 取参型 | - | - |
| 0x0125 | capture_ui_plane_b | 0x403530 | 2 | 取参型 | - | - |
| 0x0128 | define_ui_entry | 0x42B2B0 | 2 + 2|3 + 1 + 3 + 3 + 3 + 3 | 取参型 | tag8, raw8, tag16, tag16, tag16, tag16 | 由 MEBIUS34.exe .text 0x42B2B0 汇编确认；先读取 tag8 和一个原始字节，再连续读取 4 个 tag16 |
| 0x0129 | refresh_ui_group_a | 0x403570 | 2 | 取参型 | - | - |
| 0x012A | activate_ui_entry | 0x42B390 | 2 + 2|3 | 取参型 | tag8 | 由 MEBIUS34.exe .text 0x42B390 汇编确认；读取一个 tag8 并置位对应条目的激活标记 |
| 0x012B | refresh_ui_group_b | 0x403590 | 2 | 取参型 | - | - |
| 0x012C | enable_ui_state_entry | 0x42B400 | 2 + 2|3 | 取参型 | tag8 | 由 decompile/42B400.c 控制流确认；手动读取一个 tag8，并把对应 UI 状态条目标记为启用 |
| 0x012D | set_ui_state_value | 0x42B470 | 2 + 2|3 + 2 | 取参型 | tag8, raw16 | 由 decompile/42B470.c 控制流确认；手动读取一个 tag8 和一个 raw16，并更新对应 UI 状态条目的附加数值 |
| 0x012E | set_ui_entry_rect | 0x42B510 | 2 + 2|3 + 1 + 3 + 3 + 3 + 3 | 取参型 | tag8, raw8, tag16, tag16, tag16, tag16 | 由 decompile/42B510.c 控制流确认；先读取一个 tag8 和原始字节，再读取 4 个 tag16，并据此更新 UI 条目的矩形区域与启用状态 |
| 0x012F | dispatch_ui_state | 0x42B5F0 | 2 | 取参型 | - | 主路径体内直接访问程序计数器 |
| 0x0130 | load_named_asset | 0x42B650 | 2 + cstring+1 | 取参型 | raw_cstring | 由 MEBIUS34.exe .text 0x42B650 汇编确认；读取一个以 0 结尾的原始字符串并交给 476079 处理 |
| 0x0132 | load_movie_flagged | 0x42B740 | 2 + cstring+1 | 取参型 | raw_cstring | 主路径中包含条件读取 |
| 0x0134 | load_movie_plain | 0x42B6D0 | 2 + cstring+1 | 取参型 | raw_cstring | 主路径中包含条件读取 |
| 0x0136 | load_movie_flagged_alias | 0x42B740 | 2 + cstring+1 | 取参型 | raw_cstring | 主路径中包含条件读取 |
| 0x0140 | mark_scene_dirty | 0x4035B0 | 2 | 取参型 | - | - |
| 0xFFFF | check_flag_transition_alias | 0x43D860 | 2 | 取参型 | - | - |
