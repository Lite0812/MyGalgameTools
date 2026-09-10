# 今日からプリンス 脚本 VM 分析定义文档

本文档对应 `CLAUDE.md` 中“0. 前置 VM 分析与指令集建模”的要求，是后续 `opcodelist.py`、`disassembler.py`、`assembler.py` 的结构依据。

当前分析对象是游戏脚本层，不是外层 FPK/FBX 容器层。外层资源路径大致为：

```text
FPK archive -> FBX\x01 container -> payload(gkx/IPT) -> GSX1 script / PageInfo cache
```

已确认样本位于：

```text
unpacked_fbx_by_type/gkx/.../*.gkx
unpacked_fbx_by_type/IPT/.../pageinfo.ipt
```

相关反编译入口：

```text
export-for-ai/decompile/457920.c   GSX1 脚本加载/编译/缓存写回
export-for-ai/decompile/4587B0.c   GKS 源脚本编译器主循环
export-for-ai/decompile/458250.c   编译期 token 归约/表达式栈处理
export-for-ai/decompile/47FEE0.c   PageInfo/IPT 加载与重建
export-for-ai/decompile/456B60.c   编译期空白/注释跳过
export-for-ai/decompile/456BF0.c   编译期按行取 token/预处理指令
export-for-ai/decompile/456F50.c   命令表匹配
export-for-ai/decompile/457080.c   label/macro 查找
```

## 1. VM 类型识别

### 1.1 资产类型与命名

游戏资源中的 FBX payload type 有 `gkx` 和 `IPT`：

- `gkx` payload 实际是编译后的脚本二进制，文件头为 `GSX1`。
- `IPT` payload 实际是 `PageInfo` 快速索引缓存，不是脚本指令流。

EXE 内部字符串还能看到 `.GKX`、`.GKI`、`.GKS`、`ScrBin`、`Script`：

- `ScrBin\<name>`：优先加载的编译后脚本缓存。
- `Script\<name>`：源脚本路径，用于缓存不存在或失效时编译。
- `gks`：源脚本扩展名，`47FEE0.c` 重建 PageInfo 时只遍历扩展名为 `gks` 的脚本条目。
- `gkx`：编译后缓存写回扩展名，`457920.c` 中 `off_4E662C` 指向字符串 `gkx`。

当前已解包资源中没有独立的 `.gks` 源脚本，`*.gkx` payload 已经是 `GSX1` 编译产物。

### 1.2 架构判断

该 VM 更接近“命令调用 + 表达式栈”的脚本 VM：

- 指令流由固定 8 字节 token 组成。
- token 可以表示：
  - 命令调用；
  - 立即数；
  - 字符串引用；
  - 表达式运算符；
  - 跳转/调用目标或 label 引用；
  - 文本输出相关控制 token。
- 编译器使用命令表描述命令名、参数个数、优先级/结合关系、状态位和 emit 函数。
- 表达式部分通过 `458250.c` 做类似操作符优先级归约，存在左参数/右参数计数检查。

因此后续工具应把 `GSX1` 视为“分段二进制脚本 + 固定宽度 token 流”，而不是普通线性 opcode 字节流。

## 2. GSX1 文件结构

### 2.1 Header

`GSX1` 文件头固定 40 字节，小端序：

| 偏移 | 大小 | 字段 | 说明 |
|---:|---:|---|---|
| `0x00` | 4 | magic | ASCII `GSX1` |
| `0x04` | 4 | version_or_guard | 必须等于 EXE 运行期 `dword_856074` |
| `0x08` | 4 | string_pool_offset | 字符串池相对文件起始偏移 |
| `0x0C` | 4 | string_pool_size | 字符串池字节数 |
| `0x10` | 4 | label_table_offset | label 表偏移 |
| `0x14` | 4 | label_table_size | label 表字节数，必须是 16 的倍数 |
| `0x18` | 4 | ref_table_offset | 辅助引用表偏移 |
| `0x1C` | 4 | ref_table_size | 辅助引用表字节数，必须是 8 的倍数 |
| `0x20` | 4 | token_stream_offset | token 流偏移 |
| `0x24` | 4 | token_stream_size | token 流字节数，必须是 8 的倍数 |

`457920.c` 读取 header 后会将各段偏移加上文件基址，并把部分相对 offset 修正为内存指针。

### 2.2 样本统计

当前 12 个 `GSX1` 样本均使用同一版本值：

```text
version_or_guard = 0x07F20198
```

样本统计：

| 文件 | 大小 | 字符串池 | labels | refs | tokens |
|---|---:|---:|---:|---:|---:|
| `ending.gkx` | 416664 | 36528 | 934 | 994 | 44650 |
| `extra.gkx` | 404056 | 35664 | 916 | 984 | 43228 |
| `freetalk.gkx` | 411112 | 37520 | 949 | 994 | 43802 |
| `gamemenu.gkx` | 542168 | 37288 | 1002 | 1180 | 59921 |
| `loadinit.gkx` | 225272 | 18952 | 455 | 452 | 24423 |
| `memories.gkx` | 478192 | 45768 | 1171 | 1079 | 50627 |
| `museum.gkx` | 431456 | 40568 | 1001 | 1018 | 45836 |
| `sce01.gkx` | 5113736 | 1279288 | 1692 | 3592 | 472325 |
| `scemain.gkx` | 524528 | 43216 | 1045 | 1503 | 56566 |
| `sndmode.gkx` | 416760 | 36592 | 926 | 1101 | 44563 |
| `system.gkx` | 163824 | 22888 | 7 | 2 | 17596 |
| `title.gkx` | 384928 | 34384 | 893 | 961 | 41066 |

### 2.3 字符串池

字符串池是以 `0x00` 结尾的 byte string 集合，编码按当前游戏环境应使用 `cp932`。

字符串池内容包含：

- label 名；
- 命令参数字符串；
- 剧情文本；
- 角色名/语音名/资源名；
- 脚本内部宏名或临时符号。

反汇编器不得通过正则直接从整个文件抓文本。必须通过结构字段和 token 引用来呈现字符串。对于暂未被 token 引用但仍位于字符串池内的字节，也必须用数据伪指令覆盖，以保证零突变重建。

### 2.4 Label 表

每项 16 字节：

| 偏移 | 大小 | 字段 | 说明 |
|---:|---:|---|---|
| `+0x00` | 4 | hash_next | label 哈希链表 next 指针/offset；文件内可为相对或 0 |
| `+0x04` | 4 | name_offset | 指向字符串池的相对 offset；`-1` 表示空 |
| `+0x08` | 4 | name_len | `457340.c` 填入的 label 名长度；现有样本里常见为 0，需保留原值 |
| `+0x0C` | 4 | token_offset | 指向 token 流的相对 offset；`-1` 表示空 |

`457920.c` 加载后执行 fixup：

- `name_offset != -1` 时，加上字符串池基址。
- `token_offset != -1` 时，加上 token 流基址。
- 之后用 `sub_4572A0`/`sub_457340` 重建 label 哈希表。

后续 asm 标签策略：

- 所有 label 表项必须生成符号标签。
- token 流中指向 label 的引用必须使用符号名，而不是硬编码 offset。
- 若 label 名可读，优先使用原 label 名；否则生成 `loc_XXXXXXXX`。

### 2.5 Ref 表

每项 8 字节。`457920.c` 对该表的 `+4` 字段做 token 流基址 fixup：

| 偏移 | 大小 | 字段 | 当前理解 |
|---:|---:|---|---|
| `+0x00` | 2 | serial_or_ref_index | 编译期引用序号/嵌套层级索引；当前样本只见 `1`、`2` |
| `+0x02` | 2 | ref_kind_flags | 引用种类/控制流标记；当前样本只见 `1`、`2`、`4`、`16`、`32`、`64` |
| `+0x04` | 4 | token_offset | 指向 token 流的相对 offset；`-1` 表示空 |

加载/保存路径已确认。`457920.c` 把脚本对象内的 label 表、ref 表、token 流分别放在对象 `+138/+139`、`+140/+141`、`+142/+143`；加载 GSX1 时将 ref 表 `+4` 从 token 相对 offset 改成 token 指针，保存前再减回 token 流基址。`sub_4572A0`/`sub_457340` 重建的是 label 哈希索引，不是 ref 表索引。

编译期若触发若干 emit helper，会向该表追加记录。`45F4F0.c`、`45F5D0.c`、`45F6F0.c`、`45F820.c`、`45F900.c`、`45FA70.c` 都按两个 `WORD` 写首字段：低 16 位写 `dword_85504C`，高 16 位分别写 `1/2/4/16/32/64`，再在 `+4` 写 token offset。12 个官方样本统计也只出现这些高 16 位组合。

`ref_kind_flags` 与编译期控制流 helper 的对应关系已确认：

| `ref_kind_flags` | 写入函数 | 对应命令 | 当前定义 |
|---:|---|---|---|
| `1` | `45F4F0.c` | `CMD_125 If2` | `if` 块起点/条件引用 marker |
| `2` | `45F5D0.c` | `CMD_126 Else` | `else` 分支 marker |
| `4` | `45F6F0.c` | `CMD_127 EndIf` | `if` 块结束 marker |
| `16` | `45F820.c` | `CMD_128 Switch` | `switch` 块起点 marker |
| `32` | `45F900.c` | `CMD_129 Case` | `case` 分支 marker |
| `64` | `45FA70.c` | `CMD_130 EndSwitch` | `switch` 块结束 marker |

运行期消费路径已定位：控制流 handler 通过 `sub_45F0A0` 读取的第一个运行期栈值是编译器插入的隐藏 `ref_index`，不一定对应源码显式参数。handler 通过 `script.ref_table[ref_index]` 取当前 marker 的 `serial_or_ref_index`，再在同一 ref 表内按方向和 mask 扫描同 serial 的目标 marker；命中后读取目标 entry 的 `token_offset` 指针，写入 `dword_14A6F7C`，并执行 `or ah, 1` 置位 `dword_14A55A4` 的 `0x00000100` 跳转请求位。

| 命令 | handler | 扫描规则 | 精确定义与证据 |
|---|---|---|---|
| `CMD_125 If2` | `46B340` | 条件为 0 时向后扫描 `ref_kind_flags & 0x0006`，即 `Else/EndIf` | `46B340.c` 先取两个运行期栈值：隐藏 `ref_index` 与条件值；条件非 0 直接返回，条件为 0 时按同 serial 扫描并跳转。12 个样本中 `If2` 扫描目标为 `Else` 601 次、`EndIf` 4705 次，无反例。 |
| `CMD_126 Else` | `46B420` | 向后扫描 `ref_kind_flags & 0x0004`，即 `EndIf` | `46B420` 未导出单独 C 文件，但内存段 `00401000--004DC000` 可反汇编确认：取 `ref_index` 后扫描同 serial 的 `EndIf` 并跳转。12 个样本中 601 个 `Else` 全部可解析到 `EndIf`。 |
| `CMD_127 EndIf` | `47B240` | 不扫描 | handler 是 `nullsub_1`，运行期无操作；其 ref marker 由 `45F6F0.c` 写入，作为 `If2/Else/Break` 的扫描目标。 |
| `CMD_128 Switch` | `46B4F0` | 向后扫描 `ref_kind_flags & 0x0060`，即 `Case/EndSwitch` | `46B4F0.c` 取隐藏 `ref_index` 和 switch 值，将 switch 值写入 `dword_162FC44`，再跳到同 serial 的下一个 `Case/EndSwitch`。12 个样本中 506 个 `Switch` 全部先命中 `Case`。 |
| `CMD_129 Case` | `46B5D0` | case 值不等时向后扫描 `ref_kind_flags & 0x0060`，即下一个 `Case/EndSwitch` | `46B5D0.c` 取隐藏 `ref_index` 和 case 值；等于 `dword_162FC44` 时直接继续执行，否则跳转。12 个样本中 `Case` 扫描目标为下一个 `Case` 1160 次、`EndSwitch` 475 次，无反例。 |
| `CMD_130 EndSwitch` | `47B240` | 不扫描 | handler 是 `nullsub_1`，运行期无操作；其 ref marker 由 `45FA70.c` 写入，作为 `Switch/Case/Break` 的扫描目标。 |
| `CMD_131 Continue` | `46B6B0` | 向前扫描 `ref_kind_flags & 0x0011`，即 `If2/Switch` 起点 marker | `46B6B0` 未导出单独 C 文件，但内存段反汇编确认：取 `ref_index` 后从 `ref_index - 1` 反向扫描同 serial 的 `If2/Switch` 起点并跳转。当前 12 个样本未出现 `Continue`，所以只有 handler 语义高置信，样本覆盖为 0。 |
| `CMD_132 Break` | `46B760` | 向后扫描 `ref_kind_flags & 0x0044`，即 `EndIf/EndSwitch` | `46B760` 未导出单独 C 文件，但内存段反汇编确认：取 `ref_index` 后正向扫描同 serial 的 `EndIf/EndSwitch` 并跳转。12 个样本中 1635 个 `Break` 全部可解析，目标为 `EndSwitch` 1447 次、`EndIf` 188 次。 |

因此 ref 表不是可丢弃的编译中间产物，而是运行期控制流索引。反汇编器必须完整保留并可重建；可以额外展示结构化 if/switch，但不能只按结构化语法重算后丢弃原 ref 表顺序。

## 3. Token 指令流

### 3.1 基本编码

token 流每条记录固定 8 字节，小端序：

| 偏移 | 大小 | 字段 | 说明 |
|---:|---:|---|---|
| `+0x00` | 1 | kind | token 类型/标志 |
| `+0x01` | 1 | argc_or_state | 参数个数、状态或归约用计数 |
| `+0x02` | 2 | opcode_or_type | 命令 ID、特殊类型或运算符类型 |
| `+0x04` | 4 | value | 立即数、字符串 offset、label offset、源码位置或命令私有值 |

应使用如下结构解析：

```python
kind = u8(token + 0)
argc_or_state = u8(token + 1)
opcode_or_type = i16le(token + 2)
value = u32le(token + 4)
```

注意：某些反编译代码以 `*(int *)(token)` 或 `*(__int16 *)(token + 1)` 读取，是为了利用重叠字段做快速判断，不代表文件中存在另一种长度。

### 3.2 已确认 token kind

基于 12 个样本统计，`kind` 只出现以下值：

| kind | 数量 | 当前语义 |
|---:|---:|---|
| `0x00` | 405941 | 立即数、表达式节点、label/page 辅助 token |
| `0x01` | 78899 | 字符串引用或文本片段引用 |
| `0x08` | 459763 | 命令 token，`opcode_or_type` 为命令表 ID |

`opcode_or_type` 的负值分布：

| 值 | 数量 | 当前语义 |
|---:|---:|---|
| `-32768` / `0x8000` | 324445 | 数值立即数 |
| `-32766` / `0x8002` | 78899 | 字符串引用 |
| `-32752` / `0x8010` | 69385 | 全局槽 f32 引用，`value` 为 `dword_14CB7C0` 槽索引 |
| `-32496` / `0x8110` | 12111 | 当前上下文局部槽 f32 引用，`value` 为 `flt_25C45D0[64*dword_970AB8 + value]` 槽索引 |

### 3.3 Opcode 空间与置信边界

本文档中的“全量 opcode”限定为 `GSX1` token 流会被脚本编译器/运行期按 opcode 解释的字段，不把外层 `FPK/FBX` 容器、`IPT` 页缓存或其它 UI/event 队列的私有消息码混入。高置信来源分三类：

1. 12 个官方 `GSX1` 样本中逐 token 实际出现的 `kind/opcode_or_type` 组合。
2. EXE 命令表 `0x4E7CE0` 中连续 408 项命令定义；`456F50.c` 用该表按名称最长匹配，`458250.c` 对非负 `opcode_or_type` 用 `0x4E7CE0 + id*44` 查表归约。
3. 运行期读参函数明确支持的负值 `opcode_or_type` 变体；其中未出现在当前样本内的项只标为“EXE reader 支持”，不伪装成样本事实。

当前官方样本的 token kind 覆盖为：`0x00` 405941 次、`0x01` 78899 次、`0x08` 459763 次。未见其它 kind。

需要特别排除两类容易误合并的值：

- `458250.c` 在表达式归约时会向编译期栈压入 `type=0x8020` 的内部占位 token；当前文件 token 流未出现该值，不能作为 GSX1 文件 opcode。
- `453310.c` 也 switch `0x8000..0x8005`，但调用链是 `452F10 -> 453310` 的另一个对象/事件队列，不经 `457920/4587B0/458250` 的 GSX1 token 流；本表不把它计入脚本 VM opcode。

命令表项字段在下方 `sub_opcode / variants` 中统一写作：`flags, unk10, accept, prec, left, right, push, argc_seen`。其中 `right=min..max` 来自表项 `+0x20/+0x24`，是编译期右参数数量范围；`argc_seen` 是 12 个官方样本中该命令 token 的 `argc_or_state` 实见值。

EXE 内存表项大小固定为 `44` bytes，基址 `0x004E7CE0`。按 `MsgOut`、`If2`、`IPageStart` 等表项直接从内存 dump 交叉验证后，字段布局如下：

| 偏移 | 字段 | 精确定义与证据 |
|---:|---|---|
| `+0x00` | `name_ptr` | 命令名字符串指针；`456F50.c` 从 `lpString` 起按 44 bytes 步长遍历并取该字段做最长前缀匹配。 |
| `+0x04` | `compile_handler` | 编译期 emit 函数；`4587B0.c` 通过 `funcs_458943 + 11*cmd_id` 调用，实际地址为 `0x4E7CE0 + 4 + 44*cmd_id`。常规命令为 `45F450`，控制流命令有专用 emit。 |
| `+0x08` | `handler` | 运行期 handler；`45E740.c` 通过 `off_4E7CE8 + 11*cmd_id` 调用，实际地址为 `0x4E7CE0 + 8 + 44*cmd_id`。 |
| `+0x0C` | `flags` | 编译期归约/文本状态标志；`458250.c/4587B0.c/45F450.c` 有直接读取点。 |
| `+0x10` | `struct_ordinal / unk10` | 独立 32-bit 表字段；内存表确认存在。非负值只出现在 `flags & 1` 的 21 个结构/控制命令上，已定位的 VM 编译/执行路径均不读取该字段参与分支。 |
| `+0x14` | `accepted_states / accept` | 命令名匹配状态 mask；`456F50.c` 只在 `entry.accept & current_state != 0` 时参与最长匹配。 |
| `+0x18` | `precedence / prec` | 编译期归约优先级；`458250.c` 比较新旧命令优先级。 |
| `+0x1C` | `left_min / left` | 左侧参数/栈扫描下界；`458250.c` 在归约前扫描右值栈边界时读取。 |
| `+0x20` | `right_min` | 右参数最小数量；`458250.c` 归约前校验。 |
| `+0x24` | `right_max` | 右参数最大数量，并参与编译状态切换；`458250.c/4587B0.c` 读取。 |
| `+0x28` | `push_count / push` | 归约后压回的占位 token 数；`458250.c` 按该字段循环追加 `0x8020` 占位 token。 |

### 3.4 槽索引命名边界

`TYPE_8010/8011/8012` 和 `TYPE_8110/8111/8112` 的 `value` 是槽索引，不是变量名索引。`GSX1` 文件头只有字符串池、label 表、ref 表和 token 流；字符串池内没有全局/局部变量名表，label 表只指向 token offset，ref 表只服务控制流 marker。运行期 reader 也只按索引访问数组：

| token 类型 | 存储位置 | 已确认访问公式 |
|---|---|---|
| `0x8010/0x8011/0x8012` | 全局槽数组 | `dword_14CB7C0[value]` |
| `0x8110/0x8111/0x8112` | 当前 VM 上下文局部槽数组 | `flt_25C45D0[64 * dword_970AB8 + value]` |

因此反汇编器应把槽稳定输出为 `G[index]` / `L[index]` 或等价语法。只有 EXE 或脚本模板直接给出机械用途时才增加注释，例如本文件已确认的全局槽 `23/24` 是 PageInfo 最高字节 include/exclude mask，`EvtCtrl(0x23/0x24)` 样本中的 `G[501]` 是动态目标槽号来源，`551..554` 是 byte pack/unpack 临时槽索引。二进制不保存源码级业务变量名；这不是待逆向字段。

### 3.5 高置信 Opcode 全表

| Opcode | 命名 | byte_pattern | length / format | operand_schema | sub_opcode / variants | 精确定义与证据 |
|---|---|---|---|---|---|---|
| `TYPE_8000` | `VAL_RAW32`<br>32 位原始数值/立即数 | `kind=0x00, opcode_or_type=0x8000, argc_or_state=0x00\|0xCC` | `8 bytes / <u8 kind, u8 argc_or_state, i16 opcode_or_type, u32 value>` | `value:u32；上下文决定按整数、float bit pattern 或字符串表索引解释` | sample_count=324445; kind=0x00/argc=0x00:55319, kind=0x00/argc=0xCC:269126 | 45F330 直接生成 type=0x8000；4587B0 解析数字/宏时写 type=0x8000；45F0A0 读取原始 dword，45F1B0 可把 value 当 dword_14CB7C0 索引取字符串。 样本统计 count=324445。 |
| `TYPE_8001` | `VAL_F32_IMM_TO_INT`<br>float 立即数转整数 reader 变体 | `opcode_or_type=0x8001（当前官方样本未出现）` | `8 bytes / <u8 kind, u8 argc_or_state, i16 opcode_or_type, u32 value>` | `value:f32_bits` | sample_count=0; 样本未出现 | 45F0A0 对 0x8001 执行 float-to-int；45F1B0/45FF30 也按 float-to-index 路径处理。 样本统计 count=0。 |
| `TYPE_8002` | `VAL_STRREF`<br>字符串池引用/文本片段 | `kind=0x01, opcode_or_type=0x8002, argc_or_state=0xCC` | `8 bytes / <u8 kind, u8 argc_or_state, i16 opcode_or_type, u32 value>` | `value:string_pool_offset（加载后可被 fixup 为字符串指针）` | sample_count=78899; kind=0x01/argc=0xCC:78899 | 4587B0 在双引号字符串和文本正文路径写 kind=1,type=0x8002；45F1B0/45FF30 按字符串指针/文本输出读取。 样本统计 count=78899。 |
| `TYPE_8010` | `VAL_GLOBAL_SLOT_F32`<br>全局槽 f32 引用 | `kind=0x00, opcode_or_type=0x8010, argc_or_state=0x00` | `8 bytes / <u8 kind, u8 argc_or_state, i16 opcode_or_type, u32 value>` | `value:u32 slot_index；访问 dword_14CB7C0[value]` | sample_count=69385; kind=0x00/argc=0x00:69385 | 45F390 直接生成 type=0x8010；45F0A0/45F1B0 通过 dword_14CB7C0[value] 取值；460AB0 可向该槽写回。 样本统计 count=69385。 |
| `TYPE_8011` | `VAL_GLOBAL_SLOT_I32CAST`<br>全局槽整数化引用 | `opcode_or_type=0x8011（当前官方样本未出现）` | `8 bytes / <u8 kind, u8 argc_or_state, i16 opcode_or_type, u32 value>` | `value:u32 slot_index；访问 dword_14CB7C0[value] 后按整数化路径解释` | sample_count=0; 样本未出现 | 45F0A0 对 0x8011 取全局槽 float 后转 int；460AB0 对 0x8011 将输入整数化后写入全局槽。 样本统计 count=0。 |
| `TYPE_8012` | `VAL_TEXT_GLOBAL_BASE`<br>文本输出用全局基址引用 | `opcode_or_type=0x8012（当前官方样本未出现）` | `8 bytes / <u8 kind, u8 argc_or_state, i16 opcode_or_type, u32 value>` | `value:u32 index；用于 dword_14CB7C0 基址寻址` | sample_count=0; 样本未出现 | 45FF30 显式接受 0x8012，并走与 0x8000 类似的 dword_14CB7C0 基址文本指针路径；其它通用 reader 未完整覆盖，故限定为文本输出 reader 变体。 样本统计 count=0。 |
| `TYPE_8110` | `VAL_LOCAL_SLOT_F32`<br>当前上下文局部槽 f32 引用 | `kind=0x00, opcode_or_type=0x8110, argc_or_state=0x00` | `8 bytes / <u8 kind, u8 argc_or_state, i16 opcode_or_type, u32 value>` | `value:u32 slot_index；访问 flt_25C45D0[64*dword_970AB8 + value]` | sample_count=12111; kind=0x00/argc=0x00:12111 | 45F3F0 直接生成 type=0x8110；45F0A0/45F1B0 通过当前上下文局部槽取值；460AB0 可向该槽写回。 样本统计 count=12111。 |
| `TYPE_8111` | `VAL_LOCAL_SLOT_I32CAST`<br>当前上下文局部槽整数化引用 | `opcode_or_type=0x8111（当前官方样本未出现）` | `8 bytes / <u8 kind, u8 argc_or_state, i16 opcode_or_type, u32 value>` | `value:u32 slot_index；访问 flt_25C45D0[64*dword_970AB8 + value] 后整数化` | sample_count=0; 样本未出现 | 45F0A0/45F1B0/45FF30 都显式处理 0x8111 的局部槽 float-to-int 路径；460AB0 可写回整数化结果。 样本统计 count=0。 |
| `TYPE_8112` | `VAL_LOCAL_SLOT_ADDR`<br>当前上下文局部槽地址引用 | `opcode_or_type=0x8112（当前官方样本未出现）` | `8 bytes / <u8 kind, u8 argc_or_state, i16 opcode_or_type, u32 value>` | `value:u32 slot_index；指向 flt_25C45D0[64*dword_970AB8 + value]` | sample_count=0; 样本未出现 | 45FF30 显式接受 0x8112，并计算局部槽地址作为文本指针/缓冲地址；限定为该 reader 支持的变体。 样本统计 count=0。 |
| `CMD_000` | `MsgOut` | `kind=0x08, opcode_or_type=0x0000` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=0/0x00000000; accept=0x00000000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:22389 | EXE 命令表项 0x004E7CE0=0x4E7CE0+0*44；name_ptr=0x004ED1F8 指向 `MsgOut`；compile=0x0045F450，handler=0x0045FF30。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 22389 次。 |
| `CMD_001` | `{` | `kind=0x08, opcode_or_type=0x0001` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=1/0x00000001; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:11386 | EXE 命令表项 0x004E7D0C=0x4E7CE0+1*44；name_ptr=0x004ED1F4 指向 `{`；compile=0x0045F450，handler=0x0045FC70。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 11386 次。 |
| `CMD_002` | `}` | `kind=0x08, opcode_or_type=0x0002` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=2/0x00000002; accept=0x11110000; prec=-999/0xFFFFFC19; left=0; right=0..0; push=0; argc_seen=0x00:11386 | EXE 命令表项 0x004E7D38=0x4E7CE0+2*44；name_ptr=0x004ED1F0 指向 `}`；compile=0x0045F450，handler=0x0045FE20。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 11386 次。 |
| `CMD_003` | `;` | `kind=0x08, opcode_or_type=0x0003` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=3/0x00000003; accept=0x00001101; prec=-999/0xFFFFFC19; left=0; right=0..0; push=0; argc_seen=0x00:168062 | EXE 命令表项 0x004E7D64=0x4E7CE0+3*44；name_ptr=0x004ED1EC 指向 `;`；compile=0x0045F450，handler=0x004604F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 168062 次。 |
| `CMD_004` | `(` | `kind=0x08, opcode_or_type=0x0004` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00020101; unk10=4/0x00000004; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=0..999; push=1; argc_seen=未见 | EXE 命令表项 0x004E7D90=0x4E7CE0+4*44；name_ptr=0x004ED1E8 指向 `(`；compile=0x0045F450，handler=0x00460510。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_005` | `[` | `kind=0x08, opcode_or_type=0x0005` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000101; unk10=5/0x00000005; accept=0x01000100; prec=-997/0xFFFFFC1B; left=1; right=1..1; push=1; argc_seen=0x01:1194 | EXE 命令表项 0x004E7DBC=0x4E7CE0+5*44；name_ptr=0x004ED1E4 指向 `[`；compile=0x0045F450，handler=0x004606F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1194 次。 |
| `CMD_006` | `)` | `kind=0x08, opcode_or_type=0x0006` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000001; unk10=6/0x00000006; accept=0x11001100; prec=-998/0xFFFFFC1A; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004E7DE8=0x4E7CE0+6*44；name_ptr=0x004ED1E0 指向 `)`；compile=0x0045F450，handler=0x00000000。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_007` | `]` | `kind=0x08, opcode_or_type=0x0007` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000001; unk10=7/0x00000007; accept=0x01000100; prec=-998/0xFFFFFC1A; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004E7E14=0x4E7CE0+7*44；name_ptr=0x004ED1DC 指向 `]`；compile=0x0045F450，handler=0x00000000。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_008` | `;` | `kind=0x08, opcode_or_type=0x0008` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000001; unk10=8/0x00000008; accept=0x11000000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004E7E40=0x4E7CE0+8*44；name_ptr=0x004ED1EC 指向 `;`；compile=0x0045F450，handler=0x00000000。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_009` | `,` | `kind=0x08, opcode_or_type=0x0009` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000001; unk10=9/0x00000009; accept=0x01000100; prec=-996/0xFFFFFC1C; left=0; right=1..1; push=1; argc_seen=未见 | EXE 命令表项 0x004E7E6C=0x4E7CE0+9*44；name_ptr=0x004ED1D8 指向 `,`；compile=0x0045F450，handler=0x00000000。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_010` | `+` | `kind=0x08, opcode_or_type=0x000A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=15/0x0000000F; left=0; right=1..1; push=1; argc_seen=未见 | EXE 命令表项 0x004E7E98=0x4E7CE0+10*44；name_ptr=0x004ED1D4 指向 `+`；compile=0x0045F450，handler=0x00460510。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_011` | `~` | `kind=0x08, opcode_or_type=0x000B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=15/0x0000000F; left=0; right=1..1; push=1; argc_seen=0x01:30 | EXE 命令表项 0x004E7EC4=0x4E7CE0+11*44；name_ptr=0x004ED1D0 指向 `~`；compile=0x0045F450，handler=0x00460540。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 30 次。 |
| `CMD_012` | `!` | `kind=0x08, opcode_or_type=0x000C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=15/0x0000000F; left=0; right=1..1; push=1; argc_seen=0x01:2515 | EXE 命令表项 0x004E7EF0=0x4E7CE0+12*44；name_ptr=0x004ED1CC 指向 `!`；compile=0x0045F450，handler=0x00460570。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2515 次。 |
| `CMD_013` | `-` | `kind=0x08, opcode_or_type=0x000D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=15/0x0000000F; left=0; right=1..1; push=1; argc_seen=0x01:69 | EXE 命令表项 0x004E7F1C=0x4E7CE0+13*44；name_ptr=0x004ED1C8 指向 `-`；compile=0x0045F450，handler=0x004605A0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 69 次。 |
| `CMD_014` | `@@` | `kind=0x08, opcode_or_type=0x000E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000101; unk10=10/0x0000000A; accept=0x10001001; prec=15/0x0000000F; left=0; right=1..1; push=1; argc_seen=0x01:76 | EXE 命令表项 0x004E7F48=0x4E7CE0+14*44；name_ptr=0x004ED1C4 指向 `@@`；compile=0x0045F450，handler=0x004605D0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 76 次。 |
| `CMD_015` | `@` | `kind=0x08, opcode_or_type=0x000F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000101; unk10=11/0x0000000B; accept=0x10001001; prec=15/0x0000000F; left=0; right=1..1; push=1; argc_seen=0x01:1118 | EXE 命令表项 0x004E7F74=0x4E7CE0+15*44；name_ptr=0x004ED1C0 指向 `@`；compile=0x0045F450，handler=0x00460600。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1118 次。 |
| `CMD_016` | `*` | `kind=0x08, opcode_or_type=0x0010` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=12/0x0000000C; left=1; right=1..1; push=1; argc_seen=0x01:4808 | EXE 命令表项 0x004E7FA0=0x4E7CE0+16*44；name_ptr=0x004E63F8 指向 `*`；compile=0x0045F450，handler=0x00460630。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 4808 次。 |
| `CMD_017` | `/` | `kind=0x08, opcode_or_type=0x0011` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=12/0x0000000C; left=1; right=1..1; push=1; argc_seen=0x01:3138 | EXE 命令表项 0x004E7FCC=0x4E7CE0+17*44；name_ptr=0x004ED1BC 指向 `/`；compile=0x0045F450，handler=0x00460670。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 3138 次。 |
| `CMD_018` | `%` | `kind=0x08, opcode_or_type=0x0012` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=12/0x0000000C; left=1; right=1..1; push=1; argc_seen=0x01:908 | EXE 命令表项 0x004E7FF8=0x4E7CE0+18*44；name_ptr=0x004ED1B8 指向 `%`；compile=0x0045F450，handler=0x004606B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 908 次。 |
| `CMD_019` | `+` | `kind=0x08, opcode_or_type=0x0013` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=11/0x0000000B; left=1; right=1..1; push=1; argc_seen=0x01:10332 | EXE 命令表项 0x004E8024=0x4E7CE0+19*44；name_ptr=0x004ED1D4 指向 `+`；compile=0x0045F450，handler=0x004606F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10332 次。 |
| `CMD_020` | `-` | `kind=0x08, opcode_or_type=0x0014` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=11/0x0000000B; left=1; right=1..1; push=1; argc_seen=0x01:2435 | EXE 命令表项 0x004E8050=0x4E7CE0+20*44；name_ptr=0x004ED1C8 指向 `-`；compile=0x0045F450，handler=0x00460730。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2435 次。 |
| `CMD_021` | `<<` | `kind=0x08, opcode_or_type=0x0015` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=10/0x0000000A; left=1; right=1..1; push=1; argc_seen=0x01:865 | EXE 命令表项 0x004E807C=0x4E7CE0+21*44；name_ptr=0x004ED1B4 指向 `<<`；compile=0x0045F450，handler=0x00460F90。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 865 次。 |
| `CMD_022` | `>>` | `kind=0x08, opcode_or_type=0x0016` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=10/0x0000000A; left=1; right=1..1; push=1; argc_seen=0x01:254 | EXE 命令表项 0x004E80A8=0x4E7CE0+22*44；name_ptr=0x004ED1B0 指向 `>>`；compile=0x0045F450，handler=0x00460770。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 254 次。 |
| `CMD_023` | `<=` | `kind=0x08, opcode_or_type=0x0017` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=9/0x00000009; left=1; right=1..1; push=1; argc_seen=0x01:292 | EXE 命令表项 0x004E80D4=0x4E7CE0+23*44；name_ptr=0x004ED1AC 指向 `<=`；compile=0x0045F450，handler=0x004607B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 292 次。 |
| `CMD_024` | `<` | `kind=0x08, opcode_or_type=0x0018` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=9/0x00000009; left=1; right=1..1; push=1; argc_seen=0x01:3553 | EXE 命令表项 0x004E8100=0x4E7CE0+24*44；name_ptr=0x004ED1A8 指向 `<`；compile=0x0045F450，handler=0x004607F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 3553 次。 |
| `CMD_025` | `>=` | `kind=0x08, opcode_or_type=0x0019` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=9/0x00000009; left=1; right=1..1; push=1; argc_seen=0x01:1704 | EXE 命令表项 0x004E812C=0x4E7CE0+25*44；name_ptr=0x004ED1A4 指向 `>=`；compile=0x0045F450，handler=0x00460830。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1704 次。 |
| `CMD_026` | `>` | `kind=0x08, opcode_or_type=0x001A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=9/0x00000009; left=1; right=1..1; push=1; argc_seen=0x01:1166 | EXE 命令表项 0x004E8158=0x4E7CE0+26*44；name_ptr=0x004ED1A0 指向 `>`；compile=0x0045F450，handler=0x00460870。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1166 次。 |
| `CMD_027` | `==` | `kind=0x08, opcode_or_type=0x001B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=8/0x00000008; left=1; right=1..1; push=1; argc_seen=0x01:1655 | EXE 命令表项 0x004E8184=0x4E7CE0+27*44；name_ptr=0x004ED19C 指向 `==`；compile=0x0045F450，handler=0x004608B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1655 次。 |
| `CMD_028` | `!=` | `kind=0x08, opcode_or_type=0x001C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=8/0x00000008; left=1; right=1..1; push=1; argc_seen=0x01:428 | EXE 命令表项 0x004E81B0=0x4E7CE0+28*44；name_ptr=0x004ED198 指向 `!=`；compile=0x0045F450，handler=0x004608F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 428 次。 |
| `CMD_029` | `&` | `kind=0x08, opcode_or_type=0x001D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=7/0x00000007; left=1; right=1..1; push=1; argc_seen=0x01:6966 | EXE 命令表项 0x004E81DC=0x4E7CE0+29*44；name_ptr=0x004ED194 指向 `&`；compile=0x0045F450，handler=0x00460930。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 6966 次。 |
| `CMD_030` | `^` | `kind=0x08, opcode_or_type=0x001E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=6/0x00000006; left=1; right=1..1; push=1; argc_seen=0x01:30 | EXE 命令表项 0x004E8208=0x4E7CE0+30*44；name_ptr=0x004ED190 指向 `^`；compile=0x0045F450，handler=0x00460970。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 30 次。 |
| `CMD_031` | `\|` | `kind=0x08, opcode_or_type=0x001F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=5/0x00000005; left=1; right=1..1; push=1; argc_seen=0x01:1813 | EXE 命令表项 0x004E8234=0x4E7CE0+31*44；name_ptr=0x004ED18C 指向 `\\|`；compile=0x0045F450，handler=0x004609B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1813 次。 |
| `CMD_032` | `&&` | `kind=0x08, opcode_or_type=0x0020` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=4/0x00000004; left=1; right=1..1; push=1; argc_seen=0x01:1630 | EXE 命令表项 0x004E8260=0x4E7CE0+32*44；name_ptr=0x004ED188 指向 `&&`；compile=0x0045F450，handler=0x004609F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1630 次。 |
| `CMD_033` | `\|\|` | `kind=0x08, opcode_or_type=0x0021` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=3/0x00000003; left=1; right=1..1; push=1; argc_seen=0x01:136 | EXE 命令表项 0x004E828C=0x4E7CE0+33*44；name_ptr=0x004ED184 指向 `\\|\\|`；compile=0x0045F450，handler=0x00460A50。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 136 次。 |
| `CMD_034` | `=` | `kind=0x08, opcode_or_type=0x0022` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x01010100; unk10=-1/0xFFFFFFFF; accept=0x01000100; prec=1/0x00000001; left=1; right=1..1; push=1; argc_seen=0x01:24086 | EXE 命令表项 0x004E82B8=0x4E7CE0+34*44；name_ptr=0x004ED180 指向 `=`；compile=0x0045F450，handler=0x00460AB0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 24086 次。 |
| `CMD_035` | `%MouseX` | `kind=0x08, opcode_or_type=0x0023` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=1; argc_seen=0x00:14 | EXE 命令表项 0x004E82E4=0x4E7CE0+35*44；name_ptr=0x004ED178 指向 `%MouseX`；compile=0x0045F450，handler=0x00460B70。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 14 次。 |
| `CMD_036` | `%MouseY` | `kind=0x08, opcode_or_type=0x0024` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=1; argc_seen=0x00:14 | EXE 命令表项 0x004E8310=0x4E7CE0+36*44；name_ptr=0x004ED170 指向 `%MouseY`；compile=0x0045F450，handler=0x00460B90。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 14 次。 |
| `CMD_037` | `%MouseBL` | `kind=0x08, opcode_or_type=0x0025` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=1; argc_seen=未见 | EXE 命令表项 0x004E833C=0x4E7CE0+37*44；name_ptr=0x004ED164 指向 `%MouseBL`；compile=0x0045F450，handler=0x00460BB0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_038` | `%MouseBR` | `kind=0x08, opcode_or_type=0x0026` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=1; argc_seen=未见 | EXE 命令表项 0x004E8368=0x4E7CE0+38*44；name_ptr=0x004ED158 指向 `%MouseBR`；compile=0x0045F450，handler=0x00460BD0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_039` | `%Random(` | `kind=0x08, opcode_or_type=0x0027` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=1; argc_seen=0x01:51 | EXE 命令表项 0x004E8394=0x4E7CE0+39*44；name_ptr=0x004ED14C 指向 `%Random(`；compile=0x0045F450，handler=0x00460BF0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 51 次。 |
| `CMD_040` | `%Random2(` | `kind=0x08, opcode_or_type=0x0028` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=1; argc_seen=未见 | EXE 命令表项 0x004E83C0=0x4E7CE0+40*44；name_ptr=0x004ED140 指向 `%Random2(`；compile=0x0045F450，handler=0x00460C30。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_041` | `%AINBOX(` | `kind=0x08, opcode_or_type=0x0029` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=1; argc_seen=0x04:10 | EXE 命令表项 0x004E83EC=0x4E7CE0+41*44；name_ptr=0x004ED134 指向 `%AINBOX(`；compile=0x0045F450，handler=0x00460C70。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10 次。 |
| `CMD_042` | `%FileFlag` | `kind=0x08, opcode_or_type=0x002A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=1; argc_seen=未见 | EXE 命令表项 0x004E8418=0x4E7CE0+42*44；name_ptr=0x004ED128 指向 `%FileFlag`；compile=0x0045F450，handler=0x00460D10。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_043` | `%PADALL` | `kind=0x08, opcode_or_type=0x002B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=1; argc_seen=0x00:30 | EXE 命令表项 0x004E8444=0x4E7CE0+43*44；name_ptr=0x004ED120 指向 `%PADALL`；compile=0x0045F450，handler=0x00460D30。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 30 次。 |
| `CMD_044` | `%BitMask(` | `kind=0x08, opcode_or_type=0x002C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=1; argc_seen=未见 | EXE 命令表项 0x004E8470=0x4E7CE0+44*44；name_ptr=0x004ED114 指向 `%BitMask(`；compile=0x0045F450，handler=0x00460D50。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_045` | `%SinMul(` | `kind=0x08, opcode_or_type=0x002D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=1; argc_seen=0x02:90 | EXE 命令表项 0x004E849C=0x4E7CE0+45*44；name_ptr=0x004ED108 指向 `%SinMul(`；compile=0x0045F450，handler=0x00460D90。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 90 次。 |
| `CMD_046` | `%CosMul(` | `kind=0x08, opcode_or_type=0x002E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=1; argc_seen=0x02:70 | EXE 命令表项 0x004E84C8=0x4E7CE0+46*44；name_ptr=0x004ED0FC 指向 `%CosMul(`；compile=0x0045F450，handler=0x00460DE0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 70 次。 |
| `CMD_047` | `%Ang(` | `kind=0x08, opcode_or_type=0x002F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=1; argc_seen=未见 | EXE 命令表项 0x004E84F4=0x4E7CE0+47*44；name_ptr=0x004ED0F4 指向 `%Ang(`；compile=0x0045F450，handler=0x00460E30。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_048` | `%Dist(` | `kind=0x08, opcode_or_type=0x0030` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=1; argc_seen=未见 | EXE 命令表项 0x004E8520=0x4E7CE0+48*44；name_ptr=0x004ED0EC 指向 `%Dist(`；compile=0x0045F450，handler=0x00460EB0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_049` | `%Sqrt(` | `kind=0x08, opcode_or_type=0x0031` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=1; argc_seen=0x01:50 | EXE 命令表项 0x004E854C=0x4E7CE0+49*44；name_ptr=0x004ED0E4 指向 `%Sqrt(`；compile=0x0045F450，handler=0x00460F30。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 50 次。 |
| `CMD_050` | `%BitSHL(` | `kind=0x08, opcode_or_type=0x0032` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=1; argc_seen=0x02:250 | EXE 命令表项 0x004E8578=0x4E7CE0+50*44；name_ptr=0x004ED0D8 指向 `%BitSHL(`；compile=0x0045F450，handler=0x00460F90。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 250 次。 |
| `CMD_051` | `%BitSHR(` | `kind=0x08, opcode_or_type=0x0033` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00000100; unk10=-1/0xFFFFFFFF; accept=0x10001000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=1; argc_seen=0x02:10 | EXE 命令表项 0x004E85A4=0x4E7CE0+51*44；name_ptr=0x004ED0CC 指向 `%BitSHR(`；compile=0x0045F450，handler=0x00460FD0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10 次。 |
| `CMD_052` | `\Shadow(` | `kind=0x08, opcode_or_type=0x0034` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:341 | EXE 命令表项 0x004E85D0=0x4E7CE0+52*44；name_ptr=0x004ED0C0 指向 `\Shadow(`；compile=0x0045F450，handler=0x00461010。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 341 次。 |
| `CMD_053` | `\ShadowR(` | `kind=0x08, opcode_or_type=0x0035` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:77 | EXE 命令表项 0x004E85FC=0x4E7CE0+53*44；name_ptr=0x004ED0B4 指向 `\ShadowR(`；compile=0x0045F450，handler=0x004611B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 77 次。 |
| `CMD_054` | `\FontCol(` | `kind=0x08, opcode_or_type=0x0036` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=8..8; push=0; argc_seen=0x08:495 | EXE 命令表项 0x004E8628=0x4E7CE0+54*44；name_ptr=0x004ED0A8 指向 `\FontCol(`；compile=0x0045F450，handler=0x00461350。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 495 次。 |
| `CMD_055` | `\R(` | `kind=0x08, opcode_or_type=0x0037` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:582 | EXE 命令表项 0x004E8654=0x4E7CE0+55*44；name_ptr=0x004ED0A4 指向 `\R(`；compile=0x0045F450，handler=0x00461530。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 582 次。 |
| `CMD_056` | `\RA(` | `kind=0x08, opcode_or_type=0x0038` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E8680=0x4E7CE0+56*44；name_ptr=0x004ED09C 指向 `\RA(`；compile=0x0045F450，handler=0x00461A90。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_057` | `\FaceInit(` | `kind=0x08, opcode_or_type=0x0039` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=34..34; push=0; argc_seen=0x22:66 | EXE 命令表项 0x004E86AC=0x4E7CE0+57*44；name_ptr=0x004ED090 指向 `\FaceInit(`；compile=0x0045F450，handler=0x00461C20。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 66 次。 |
| `CMD_058` | `\FaceConv(` | `kind=0x08, opcode_or_type=0x003A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=7..7; push=0; argc_seen=0x07:66 | EXE 命令表项 0x004E86D8=0x4E7CE0+58*44；name_ptr=0x004ED084 指向 `\FaceConv(`；compile=0x0045F450，handler=0x00462380。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 66 次。 |
| `CMD_059` | `\Face(` | `kind=0x08, opcode_or_type=0x003B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:10470 | EXE 命令表项 0x004E8704=0x4E7CE0+59*44；name_ptr=0x004ED07C 指向 `\Face(`；compile=0x0045F450，handler=0x00462540。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10470 次。 |
| `CMD_060` | `\Name(` | `kind=0x08, opcode_or_type=0x003C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:154 | EXE 命令表项 0x004E8730=0x4E7CE0+60*44；name_ptr=0x004ED074 指向 `\Name(`；compile=0x0045F450，handler=0x00462CD0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 154 次。 |
| `CMD_061` | `\NameR(` | `kind=0x08, opcode_or_type=0x003D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E875C=0x4E7CE0+61*44；name_ptr=0x004ED06C 指向 `\NameR(`；compile=0x0045F450，handler=0x00463000。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_062` | `\NameGet(` | `kind=0x08, opcode_or_type=0x003E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=未见 | EXE 命令表项 0x004E8788=0x4E7CE0+62*44；name_ptr=0x004ED060 指向 `\NameGet(`；compile=0x0045F450，handler=0x004635A0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_063` | `\Voice(` | `kind=0x08, opcode_or_type=0x003F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=0x04:4573 | EXE 命令表项 0x004E87B4=0x4E7CE0+63*44；name_ptr=0x004ED058 指向 `\Voice(`；compile=0x0045F450，handler=0x004637C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 4573 次。 |
| `CMD_064` | `\Efc(` | `kind=0x08, opcode_or_type=0x0040` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:33 | EXE 命令表项 0x004E87E0=0x4E7CE0+64*44；name_ptr=0x004ED050 指向 `\Efc(`；compile=0x0045F450，handler=0x00463AE0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 33 次。 |
| `CMD_065` | `\EfcR(` | `kind=0x08, opcode_or_type=0x0041` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:33 | EXE 命令表项 0x004E880C=0x4E7CE0+65*44；name_ptr=0x004ED048 指向 `\EfcR(`；compile=0x0045F450，handler=0x00463AE0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 33 次。 |
| `CMD_066` | `\Col(` | `kind=0x08, opcode_or_type=0x0042` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..2; push=0; argc_seen=0x01:482, 0x02:264 | EXE 命令表项 0x004E8838=0x4E7CE0+66*44；name_ptr=0x004ED040 指向 `\Col(`；compile=0x0045F450，handler=0x00463C10。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 746 次。 |
| `CMD_067` | `\ColS(` | `kind=0x08, opcode_or_type=0x0043` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..4; push=0; argc_seen=未见 | EXE 命令表项 0x004E8864=0x4E7CE0+67*44；name_ptr=0x004ED038 指向 `\ColS(`；compile=0x0045F450，handler=0x00463DB0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_068` | `\ColF(` | `kind=0x08, opcode_or_type=0x0044` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..11; push=0; argc_seen=0x0B:33 | EXE 命令表项 0x004E8890=0x4E7CE0+68*44；name_ptr=0x004ED030 指向 `\ColF(`；compile=0x0045F450，handler=0x00463FA0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 33 次。 |
| `CMD_069` | `\DifTbl(` | `kind=0x08, opcode_or_type=0x0045` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=10..10; push=0; argc_seen=未见 | EXE 命令表项 0x004E88BC=0x4E7CE0+69*44；name_ptr=0x004ED024 指向 `\DifTbl(`；compile=0x0045F450，handler=0x00464440。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_070` | `\DifCur(` | `kind=0x08, opcode_or_type=0x0046` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=未见 | EXE 命令表项 0x004E88E8=0x4E7CE0+70*44；name_ptr=0x004ED018 指向 `\DifCur(`；compile=0x0045F450，handler=0x004645E0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_071` | `\FaceMode(` | `kind=0x08, opcode_or_type=0x0047` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:22 | EXE 命令表项 0x004E8914=0x4E7CE0+71*44；name_ptr=0x004ED00C 指向 `\FaceMode(`；compile=0x0045F450，handler=0x00464740。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 22 次。 |
| `CMD_072` | `\PageKind(` | `kind=0x08, opcode_or_type=0x0048` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:66 | EXE 命令表项 0x004E8940=0x4E7CE0+72*44；name_ptr=0x004ED000 指向 `\PageKind(`；compile=0x0045F450，handler=0x004647A0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 66 次。 |
| `CMD_073` | `\WaitPos(` | `kind=0x08, opcode_or_type=0x0049` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..3; push=0; argc_seen=0x03:33 | EXE 命令表项 0x004E896C=0x4E7CE0+73*44；name_ptr=0x004ECFF4 指向 `\WaitPos(`；compile=0x0045F450，handler=0x004648F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 33 次。 |
| `CMD_074` | `\StandS(` | `kind=0x08, opcode_or_type=0x004A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=未见 | EXE 命令表项 0x004E8998=0x4E7CE0+74*44；name_ptr=0x004ECFE8 指向 `\StandS(`；compile=0x0045F450，handler=0x00464AA0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_075` | `\StandM(` | `kind=0x08, opcode_or_type=0x004B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=未见 | EXE 命令表项 0x004E89C4=0x4E7CE0+75*44；name_ptr=0x004ECFDC 指向 `\StandM(`；compile=0x0045F450，handler=0x00464DE0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_076` | `\StandW(` | `kind=0x08, opcode_or_type=0x004C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004E89F0=0x4E7CE0+76*44；name_ptr=0x004ECFD0 指向 `\StandW(`；compile=0x0045F450，handler=0x004650C0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_077` | `\StandE(` | `kind=0x08, opcode_or_type=0x004D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004E8A1C=0x4E7CE0+77*44；name_ptr=0x004ECFC4 指向 `\StandE(`；compile=0x0045F450，handler=0x00465260。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_078` | `\TextWait(` | `kind=0x08, opcode_or_type=0x004E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E8A48=0x4E7CE0+78*44；name_ptr=0x004ECFB8 指向 `\TextWait(`；compile=0x0045F450，handler=0x00465410。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_079` | `\msr` | `kind=0x08, opcode_or_type=0x004F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..6; push=0; argc_seen=未见 | EXE 命令表项 0x004E8A74=0x4E7CE0+79*44；name_ptr=0x004ECFB0 指向 `\msr`；compile=0x0045F450，handler=0x004655A0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_080` | `\ms` | `kind=0x08, opcode_or_type=0x0050` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..6; push=0; argc_seen=0x06:264 | EXE 命令表项 0x004E8AA0=0x4E7CE0+80*44；name_ptr=0x004ECFAC 指向 `\ms`；compile=0x0045F450，handler=0x00465860。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 264 次。 |
| `CMD_081` | `\mdr` | `kind=0x08, opcode_or_type=0x0051` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..6; push=0; argc_seen=0x04:22 | EXE 命令表项 0x004E8ACC=0x4E7CE0+81*44；name_ptr=0x004ECFA4 指向 `\mdr`；compile=0x0045F450，handler=0x00465B00。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 22 次。 |
| `CMD_082` | `\md` | `kind=0x08, opcode_or_type=0x0052` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..6; push=0; argc_seen=0x04:22 | EXE 命令表项 0x004E8AF8=0x4E7CE0+82*44；name_ptr=0x004ECFA0 指向 `\md`；compile=0x0045F450，handler=0x00465DF0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 22 次。 |
| `CMD_083` | `\mr` | `kind=0x08, opcode_or_type=0x0053` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E8B24=0x4E7CE0+83*44；name_ptr=0x004ECF9C 指向 `\mr`；compile=0x0045F450，handler=0x00466150。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_084` | `\m` | `kind=0x08, opcode_or_type=0x0054` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E8B50=0x4E7CE0+84*44；name_ptr=0x004ECF98 指向 `\m`；compile=0x0045F450，handler=0x00466340。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_085` | `\$` | `kind=0x08, opcode_or_type=0x0055` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:231 | EXE 命令表项 0x004E8B7C=0x4E7CE0+85*44；name_ptr=0x004ECF94 指向 `\$`；compile=0x0045F450，handler=0x00466520。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 231 次。 |
| `CMD_086` | `\xp` | `kind=0x08, opcode_or_type=0x0056` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E8BA8=0x4E7CE0+86*44；name_ptr=0x004ECF90 指向 `\xp`；compile=0x0045F450，handler=0x00466790。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_087` | `\xi` | `kind=0x08, opcode_or_type=0x0057` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E8BD4=0x4E7CE0+87*44；name_ptr=0x004ECF8C 指向 `\xi`；compile=0x0045F450，handler=0x00466920。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_088` | `\x` | `kind=0x08, opcode_or_type=0x0058` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004E8C00=0x4E7CE0+88*44；name_ptr=0x004ECF88 指向 `\x`；compile=0x0045F450，handler=0x00466AD0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_089` | `\n` | `kind=0x08, opcode_or_type=0x0059` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:11130 | EXE 命令表项 0x004E8C2C=0x4E7CE0+89*44；name_ptr=0x004ECF84 指向 `\n`；compile=0x0045F450，handler=0x00466C70。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 11130 次。 |
| `CMD_090` | `\p` | `kind=0x08, opcode_or_type=0x005A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004E8C58=0x4E7CE0+90*44；name_ptr=0x004ECF80 指向 `\p`；compile=0x0045F450，handler=0x004671B0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_091` | `\eh` | `kind=0x08, opcode_or_type=0x005B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004E8C84=0x4E7CE0+91*44；name_ptr=0x004ECF7C 指向 `\eh`；compile=0x0045F450，handler=0x00467700。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_092` | `\e` | `kind=0x08, opcode_or_type=0x005C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:10467 | EXE 命令表项 0x004E8CB0=0x4E7CE0+92*44；name_ptr=0x004ECF78 指向 `\e`；compile=0x0045F450，handler=0x00467700。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10467 次。 |
| `CMD_093` | `\ea` | `kind=0x08, opcode_or_type=0x005D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:201 | EXE 命令表项 0x004E8CDC=0x4E7CE0+93*44；name_ptr=0x004ECF74 指向 `\ea`；compile=0x0045F450，handler=0x00467F80。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 201 次。 |
| `CMD_094` | `\w` | `kind=0x08, opcode_or_type=0x005E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:746 | EXE 命令表项 0x004E8D08=0x4E7CE0+94*44；name_ptr=0x004ECF70 指向 `\w`；compile=0x0045F450，handler=0x00468740。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 746 次。 |
| `CMD_095` | `\r` | `kind=0x08, opcode_or_type=0x005F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x02010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004E8D34=0x4E7CE0+95*44；name_ptr=0x004ECF6C 指向 `\r`；compile=0x0045F450，handler=0x00468870。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_096` | `\vw` | `kind=0x08, opcode_or_type=0x0060` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=未见 | EXE 命令表项 0x004E8D60=0x4E7CE0+96*44；name_ptr=0x004ECF68 指向 `\vw`；compile=0x0045F450，handler=0x004689A0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_097` | `\va` | `kind=0x08, opcode_or_type=0x0061` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:264 | EXE 命令表项 0x004E8D8C=0x4E7CE0+97*44；name_ptr=0x004ECF64 指向 `\va`；compile=0x0045F450，handler=0x00468B80。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 264 次。 |
| `CMD_098` | `\v` | `kind=0x08, opcode_or_type=0x0062` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:482 | EXE 命令表项 0x004E8DB8=0x4E7CE0+98*44；name_ptr=0x004ECF60 指向 `\v`；compile=0x0045F450，handler=0x00468E80。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 482 次。 |
| `CMD_099` | `\tk` | `kind=0x08, opcode_or_type=0x0063` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E8DE4=0x4E7CE0+99*44；name_ptr=0x004ECF5C 指向 `\tk`；compile=0x0045F450，handler=0x00469170。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_100` | `\t` | `kind=0x08, opcode_or_type=0x0064` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00110000; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E8E10=0x4E7CE0+100*44；name_ptr=0x004ECF58 指向 `\t`；compile=0x0045F450，handler=0x00469370。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_101` | `DebugS` | `kind=0x08, opcode_or_type=0x0065` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..3; push=0; argc_seen=未见 | EXE 命令表项 0x004E8E3C=0x4E7CE0+101*44；name_ptr=0x004ECF50 指向 `DebugS`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_102` | `DebugW` | `kind=0x08, opcode_or_type=0x0066` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=未见 | EXE 命令表项 0x004E8E68=0x4E7CE0+102*44；name_ptr=0x004ECF48 指向 `DebugW`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_103` | `DebugV` | `kind=0x08, opcode_or_type=0x0067` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004E8E94=0x4E7CE0+103*44；name_ptr=0x004ECF40 指向 `DebugV`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_104` | `DebugPrintf` | `kind=0x08, opcode_or_type=0x0068` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..18; push=0; argc_seen=未见 | EXE 命令表项 0x004E8EC0=0x4E7CE0+104*44；name_ptr=0x004ECF34 指向 `DebugPrintf`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_105` | `DebugT` | `kind=0x08, opcode_or_type=0x0069` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E8EEC=0x4E7CE0+105*44；name_ptr=0x004ECF2C 指向 `DebugT`；compile=0x0045F450，handler=0x00469530。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_106` | `Connect` | `kind=0x08, opcode_or_type=0x006A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:1 | EXE 命令表项 0x004E8F18=0x4E7CE0+106*44；name_ptr=0x004ECF24 指向 `Connect`；compile=0x0045F450，handler=0x004697A0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_107` | `Jump` | `kind=0x08, opcode_or_type=0x006B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:2746 | EXE 命令表项 0x004E8F44=0x4E7CE0+107*44；name_ptr=0x004ECF1C 指向 `Jump`；compile=0x0045F450，handler=0x00469990。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2746 次。 |
| `CMD_108` | `ExtJump` | `kind=0x08, opcode_or_type=0x006C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:1342 | EXE 命令表项 0x004E8F70=0x4E7CE0+108*44；name_ptr=0x004ECF14 指向 `ExtJump`；compile=0x0045F450，handler=0x00469AD0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1342 次。 |
| `CMD_109` | `ForceJump` | `kind=0x08, opcode_or_type=0x006D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..15; push=0; argc_seen=0x0F:1 | EXE 命令表项 0x004E8F9C=0x4E7CE0+109*44；name_ptr=0x004ECF08 指向 `ForceJump`；compile=0x0045F450，handler=0x00469B30。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_110` | `Call` | `kind=0x08, opcode_or_type=0x006E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:395 | EXE 命令表项 0x004E8FC8=0x4E7CE0+110*44；name_ptr=0x004ECF00 指向 `Call`；compile=0x0045F450，handler=0x00469D20。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 395 次。 |
| `CMD_111` | `ExtCall` | `kind=0x08, opcode_or_type=0x006F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:1 | EXE 命令表项 0x004E8FF4=0x4E7CE0+111*44；name_ptr=0x004ECEF8 指向 `ExtCall`；compile=0x0045F450，handler=0x0046A130。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_112` | `Return` | `kind=0x08, opcode_or_type=0x0070` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:3494 | EXE 命令表项 0x004E9020=0x4E7CE0+112*44；name_ptr=0x004ECEF0 指向 `Return`；compile=0x0045F450，handler=0x0046A350。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 3494 次。 |
| `CMD_113` | `ClrRet` | `kind=0x08, opcode_or_type=0x0071` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:2 | EXE 命令表项 0x004E904C=0x4E7CE0+113*44；name_ptr=0x004ECEE8 指向 `ClrRet`；compile=0x0045F450，handler=0x0046A4D0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2 次。 |
| `CMD_114` | `GetLabel` | `kind=0x08, opcode_or_type=0x0072` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004E9078=0x4E7CE0+114*44；name_ptr=0x004ECEDC 指向 `GetLabel`；compile=0x0045F450，handler=0x0046A4F0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_115` | `GetExtLabel` | `kind=0x08, opcode_or_type=0x0073` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004E90A4=0x4E7CE0+115*44；name_ptr=0x004ECED0 指向 `GetExtLabel`；compile=0x0045F450，handler=0x0046A5E0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_116` | `CallSub` | `kind=0x08, opcode_or_type=0x0074` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..999; push=0; argc_seen=0x01:27532, 0x03:619, 0x04:1807, 0x05:5345, 0x06:1625, 0x08:13, 0x0C:51 | EXE 命令表项 0x004E90D0=0x4E7CE0+116*44；name_ptr=0x004ECEC8 指向 `CallSub`；compile=0x0045F450，handler=0x0046A6C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 36992 次。 |
| `CMD_117` | `ExtCallSub` | `kind=0x08, opcode_or_type=0x0075` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=0x02:94 | EXE 命令表项 0x004E90FC=0x4E7CE0+117*44；name_ptr=0x004ECEBC 指向 `ExtCallSub`；compile=0x0045F450，handler=0x0046A880。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 94 次。 |
| `CMD_118` | `CallPop` | `kind=0x08, opcode_or_type=0x0076` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..4; push=0; argc_seen=0x00:23 | EXE 命令表项 0x004E9128=0x4E7CE0+118*44；name_ptr=0x004ECEB4 指向 `CallPop`；compile=0x0045F450，handler=0x0046AAD0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 23 次。 |
| `CMD_119` | `CallPush` | `kind=0x08, opcode_or_type=0x0077` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E9154=0x4E7CE0+119*44；name_ptr=0x004ECEA8 指向 `CallPush`；compile=0x0045F450，handler=0x0046ACB0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_120` | `ReserveJump` | `kind=0x08, opcode_or_type=0x0078` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..4; push=0; argc_seen=0x02:2 | EXE 命令表项 0x004E9180=0x4E7CE0+120*44；name_ptr=0x004ECE9C 指向 `ReserveJump`；compile=0x0045F450，handler=0x0046ADF0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2 次。 |
| `CMD_121` | `Wait` | `kind=0x08, opcode_or_type=0x0079` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:1931 | EXE 命令表项 0x004E91AC=0x4E7CE0+121*44；name_ptr=0x004ECE94 指向 `Wait`；compile=0x0045F450，handler=0x0046AFB0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1931 次。 |
| `CMD_122` | `WaitR` | `kind=0x08, opcode_or_type=0x007A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E91D8=0x4E7CE0+122*44；name_ptr=0x004ECE8C 指向 `WaitR`；compile=0x0045F450，handler=0x0046B050。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_123` | `If` | `kind=0x08, opcode_or_type=0x007B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:6838 | EXE 命令表项 0x004E9204=0x4E7CE0+123*44；name_ptr=0x004ECE88 指向 `If`；compile=0x0045F450，handler=0x0046B190。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 6838 次。 |
| `CMD_124` | `On` | `kind=0x08, opcode_or_type=0x007C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..999; push=0; argc_seen=未见 | EXE 命令表项 0x004E9230=0x4E7CE0+124*44；name_ptr=0x004ECE84 指向 `On`；compile=0x0045F450，handler=0x0046B260。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_125` | `If2` | `kind=0x08, opcode_or_type=0x007D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=12/0x0000000C; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:5306 | EXE 命令表项 0x004E925C=0x4E7CE0+125*44；name_ptr=0x004ECE80 指向 `If2`；compile=0x0045F4F0，handler=0x0046B340。运行期 `46B340.c` 先读取隐藏 ref_index，再读取条件；条件为 0 时向后扫描同 serial 的 `Else/EndIf` 并跳转。12 个样本出现 5306 次，扫描目标无反例。 |
| `CMD_126` | `Else` | `kind=0x08, opcode_or_type=0x007E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=13/0x0000000D; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:601 | EXE 命令表项 0x004E9288=0x4E7CE0+126*44；name_ptr=0x004ECE78 指向 `Else`；compile=0x0045F5D0，handler=0x0046B420。`46B420` 内存反汇编确认先读取隐藏 ref_index，向后扫描同 serial 的 `EndIf` 并跳转；12 个样本出现 601 次，均可解析。 |
| `CMD_127` | `EndIf` | `kind=0x08, opcode_or_type=0x007F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=14/0x0000000E; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:5337 | EXE 命令表项 0x004E92B4=0x4E7CE0+127*44；name_ptr=0x004ECE70 指向 `EndIf`；compile=0x0045F6F0，handler=0x0047B240。`47B240.c` 为 `nullsub_1`，运行期无操作；`45F6F0.c` 写 ref_kind_flags=4，作为 `If2/Else/Break` 目标。12 个样本出现 5337 次。 |
| `CMD_128` | `Switch` | `kind=0x08, opcode_or_type=0x0080` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=15/0x0000000F; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:506 | EXE 命令表项 0x004E92E0=0x4E7CE0+128*44；name_ptr=0x004ECE68 指向 `Switch`；compile=0x0045F820，handler=0x0046B4F0。运行期 `46B4F0.c` 先读取隐藏 ref_index，再保存 switch 值到 `dword_162FC44`，随后向后扫描同 serial 的 `Case/EndSwitch` 并跳转。12 个样本出现 506 次，均命中 `Case`。 |
| `CMD_129` | `Case` | `kind=0x08, opcode_or_type=0x0081` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=16/0x00000010; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:1635 | EXE 命令表项 0x004E930C=0x4E7CE0+129*44；name_ptr=0x004ECE60 指向 `Case`；compile=0x0045F900，handler=0x0046B5D0。运行期 `46B5D0.c` 先读取隐藏 ref_index，再比较 case 值与 `dword_162FC44`；不等时向后扫描同 serial 的下一个 `Case/EndSwitch` 并跳转。12 个样本出现 1635 次。 |
| `CMD_130` | `EndSwitch` | `kind=0x08, opcode_or_type=0x0082` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=17/0x00000011; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:475 | EXE 命令表项 0x004E9338=0x4E7CE0+130*44；name_ptr=0x004ECE54 指向 `EndSwitch`；compile=0x0045FA70，handler=0x0047B240。`47B240.c` 为 `nullsub_1`，运行期无操作；`45FA70.c` 写 ref_kind_flags=64，作为 `Switch/Case/Break` 目标。12 个样本出现 475 次。 |
| `CMD_131` | `Continue` | `kind=0x08, opcode_or_type=0x0083` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=18/0x00000012; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E9364=0x4E7CE0+131*44；name_ptr=0x004ECE48 指向 `Continue`；compile=0x0045FBA0，handler=0x0046B6B0。`46B6B0` 内存反汇编确认先读取隐藏 ref_index，向前扫描同 serial 的 `If2/Switch` 起点并跳转；当前 12 个样本未出现该命令。 |
| `CMD_132` | `Break` | `kind=0x08, opcode_or_type=0x0084` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=19/0x00000013; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:1635 | EXE 命令表项 0x004E9390=0x4E7CE0+132*44；name_ptr=0x004ECE40 指向 `Break`；compile=0x0045FBA0，handler=0x0046B760。`46B760` 内存反汇编确认先读取隐藏 ref_index，向后扫描同 serial 的 `EndIf/EndSwitch` 并跳转；12 个样本 1635 次全部可解析，目标为 `EndSwitch` 1447 次、`EndIf` 188 次。 |
| `CMD_133` | `Loop` | `kind=0x08, opcode_or_type=0x0085` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:3 | EXE 命令表项 0x004E93BC=0x4E7CE0+133*44；name_ptr=0x004ECE38 指向 `Loop`；compile=0x0045F450，handler=0x0046B830。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 3 次。 |
| `CMD_134` | `SkipReset` | `kind=0x08, opcode_or_type=0x0086` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:1 | EXE 命令表项 0x004E93E8=0x4E7CE0+134*44；name_ptr=0x004ECE2C 指向 `SkipReset`；compile=0x0045F450，handler=0x0046B980。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_135` | `FileExist` | `kind=0x08, opcode_or_type=0x0087` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=0x04:42 | EXE 命令表项 0x004E9414=0x4E7CE0+135*44；name_ptr=0x004ECE20 指向 `FileExist`；compile=0x0045F450，handler=0x0046BA50。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 42 次。 |
| `CMD_136` | `SInit` | `kind=0x08, opcode_or_type=0x0088` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:3 | EXE 命令表项 0x004E9440=0x4E7CE0+136*44；name_ptr=0x004ECE18 指向 `SInit`；compile=0x0045F450，handler=0x0046BBC0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 3 次。 |
| `CMD_137` | `SChk` | `kind=0x08, opcode_or_type=0x0089` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..3; push=0; argc_seen=0x02:148 | EXE 命令表项 0x004E946C=0x4E7CE0+137*44；name_ptr=0x004ECE10 指向 `SChk`；compile=0x0045F450，handler=0x0046BC10。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 148 次。 |
| `CMD_138` | `SMake` | `kind=0x08, opcode_or_type=0x008A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..4; push=0; argc_seen=0x03:19, 0x04:298 | EXE 命令表项 0x004E9498=0x4E7CE0+138*44；name_ptr=0x004ECE08 指向 `SMake`；compile=0x0045F450，handler=0x0046BDB0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 317 次。 |
| `CMD_139` | `LoadPic` | `kind=0x08, opcode_or_type=0x008B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..4; push=0; argc_seen=0x02:5, 0x03:2, 0x04:299 | EXE 命令表项 0x004E94C4=0x4E7CE0+139*44；name_ptr=0x004ECE00 指向 `LoadPic`；compile=0x0045F450，handler=0x0046BE60。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 306 次。 |
| `CMD_140` | `SAsyncCtrl` | `kind=0x08, opcode_or_type=0x008C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..999; push=0; argc_seen=0x03:2 | EXE 命令表项 0x004E94F0=0x4E7CE0+140*44；name_ptr=0x004ECDF4 指向 `SAsyncCtrl`；compile=0x0045F450，handler=0x0046BF20。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2 次。 |
| `CMD_141` | `SSize` | `kind=0x08, opcode_or_type=0x008D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:60 | EXE 命令表项 0x004E951C=0x4E7CE0+141*44；name_ptr=0x004ECDEC 指向 `SSize`；compile=0x0045F450，handler=0x0046C010。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 60 次。 |
| `CMD_142` | `LoadPicAdd` | `kind=0x08, opcode_or_type=0x008E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..7; push=0; argc_seen=0x06:20 | EXE 命令表项 0x004E9548=0x4E7CE0+142*44；name_ptr=0x004ECDE0 指向 `LoadPicAdd`；compile=0x0045F450，handler=0x0046C0C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 20 次。 |
| `CMD_143` | `LoadPicAddA` | `kind=0x08, opcode_or_type=0x008F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..8; push=0; argc_seen=未见 | EXE 命令表项 0x004E9574=0x4E7CE0+143*44；name_ptr=0x004ECDD4 指向 `LoadPicAddA`；compile=0x0045F450，handler=0x0046C1F0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_144` | `SMode` | `kind=0x08, opcode_or_type=0x0090` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004E95A0=0x4E7CE0+144*44；name_ptr=0x004ECDCC 指向 `SMode`；compile=0x0045F450，handler=0x0046C3A0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_145` | `SErase` | `kind=0x08, opcode_or_type=0x0091` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..1; push=0; argc_seen=0x01:615 | EXE 命令表项 0x004E95CC=0x4E7CE0+145*44；name_ptr=0x004ECDC4 指向 `SErase`；compile=0x0045F450，handler=0x0046C440。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 615 次。 |
| `CMD_146` | `SFillR` | `kind=0x08, opcode_or_type=0x0092` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=0x06:134 | EXE 命令表项 0x004E95F8=0x4E7CE0+146*44；name_ptr=0x004ECDBC 指向 `SFillR`；compile=0x0045F450，handler=0x0046C4E0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 134 次。 |
| `CMD_147` | `SCopy` | `kind=0x08, opcode_or_type=0x0093` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:161 | EXE 命令表项 0x004E9624=0x4E7CE0+147*44；name_ptr=0x004ECDB4 指向 `SCopy`；compile=0x0045F450，handler=0x0046C5C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 161 次。 |
| `CMD_148` | `SCopyR` | `kind=0x08, opcode_or_type=0x0094` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=8..8; push=0; argc_seen=0x08:20 | EXE 命令表项 0x004E9650=0x4E7CE0+148*44；name_ptr=0x004ECDAC 指向 `SCopyR`；compile=0x0045F450，handler=0x0046C690。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 20 次。 |
| `CMD_149` | `SCopyRL` | `kind=0x08, opcode_or_type=0x0095` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=10..10; push=0; argc_seen=未见 | EXE 命令表项 0x004E967C=0x4E7CE0+149*44；name_ptr=0x004ECDA4 指向 `SCopyRL`；compile=0x0045F450，handler=0x0046C780。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_150` | `SPrintf` | `kind=0x08, opcode_or_type=0x0096` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=14..999; push=0; argc_seen=0x10:2 | EXE 命令表项 0x004E96A8=0x4E7CE0+150*44；name_ptr=0x004ECD9C 指向 `SPrintf`；compile=0x0045F450，handler=0x0046C970。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2 次。 |
| `CMD_151` | `CellTSet` | `kind=0x08, opcode_or_type=0x0097` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:106 | EXE 命令表项 0x004E96D4=0x4E7CE0+151*44；name_ptr=0x004ECD90 指向 `CellTSet`；compile=0x0045F450，handler=0x0046CAE0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 106 次。 |
| `CMD_152` | `CellTReset` | `kind=0x08, opcode_or_type=0x0098` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E9700=0x4E7CE0+152*44；name_ptr=0x004ECD84 指向 `CellTReset`；compile=0x0045F450，handler=0x0046CC90。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_153` | `CellI` | `kind=0x08, opcode_or_type=0x0099` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=0x06:3442 | EXE 命令表项 0x004E972C=0x4E7CE0+153*44；name_ptr=0x004ECD7C 指向 `CellI`；compile=0x0045F450，handler=0x0046CCD0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 3442 次。 |
| `CMD_154` | `SCashCtrl` | `kind=0x08, opcode_or_type=0x009A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=0x07:1 | EXE 命令表项 0x004E9758=0x4E7CE0+154*44；name_ptr=0x004ECD70 指向 `SCashCtrl`；compile=0x0045F450，handler=0x0046D030。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_155` | `MdlInit` | `kind=0x08, opcode_or_type=0x009B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004E9784=0x4E7CE0+155*44；name_ptr=0x004ECD68 指向 `MdlInit`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_156` | `MdlChk` | `kind=0x08, opcode_or_type=0x009C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E97B0=0x4E7CE0+156*44；name_ptr=0x004ECD60 指向 `MdlChk`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_157` | `MdlLoad` | `kind=0x08, opcode_or_type=0x009D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E97DC=0x4E7CE0+157*44；name_ptr=0x004ECD58 指向 `MdlLoad`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_158` | `MdlErase` | `kind=0x08, opcode_or_type=0x009E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E9808=0x4E7CE0+158*44；name_ptr=0x004ECD4C 指向 `MdlErase`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_159` | `MdlMode` | `kind=0x08, opcode_or_type=0x009F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..3; push=0; argc_seen=未见 | EXE 命令表项 0x004E9834=0x4E7CE0+159*44；name_ptr=0x004ECD44 指向 `MdlMode`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_160` | `TweenCtrl` | `kind=0x08, opcode_or_type=0x00A0` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=未见 | EXE 命令表项 0x004E9860=0x4E7CE0+160*44；name_ptr=0x004ECD38 指向 `TweenCtrl`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_161` | `CInit` | `kind=0x08, opcode_or_type=0x00A1` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:3 | EXE 命令表项 0x004E988C=0x4E7CE0+161*44；name_ptr=0x004ECD30 指向 `CInit`；compile=0x0045F450，handler=0x0046D1C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 3 次。 |
| `CMD_162` | `CChk` | `kind=0x08, opcode_or_type=0x00A2` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:877 | EXE 命令表项 0x004E98B8=0x4E7CE0+162*44；name_ptr=0x004ECD28 指向 `CChk`；compile=0x0045F450，handler=0x0046D210。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 877 次。 |
| `CMD_163` | `CAChk` | `kind=0x08, opcode_or_type=0x00A3` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:73 | EXE 命令表项 0x004E98E4=0x4E7CE0+163*44；name_ptr=0x004ECD20 指向 `CAChk`；compile=0x0045F450，handler=0x0046D290。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 73 次。 |
| `CMD_164` | `CAnime` | `kind=0x08, opcode_or_type=0x00A4` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:159 | EXE 命令表项 0x004E9910=0x4E7CE0+164*44；name_ptr=0x004ECD18 指向 `CAnime`；compile=0x0045F450，handler=0x0046D4A0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 159 次。 |
| `CMD_165` | `CExtAnime` | `kind=0x08, opcode_or_type=0x00A5` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:77 | EXE 命令表项 0x004E993C=0x4E7CE0+165*44；name_ptr=0x004ECD0C 指向 `CExtAnime`；compile=0x0045F450，handler=0x0046D560。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 77 次。 |
| `CMD_166` | `CAStop` | `kind=0x08, opcode_or_type=0x00A6` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..2; push=0; argc_seen=0x01:255 | EXE 命令表项 0x004E9968=0x4E7CE0+166*44；name_ptr=0x004ECD04 指向 `CAStop`；compile=0x0045F450，handler=0x0046D710。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 255 次。 |
| `CMD_167` | `CMake` | `kind=0x08, opcode_or_type=0x00A7` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..10; push=0; argc_seen=0x08:185, 0x09:26, 0x0A:1404 | EXE 命令表项 0x004E9994=0x4E7CE0+167*44；name_ptr=0x004ECCFC 指向 `CMake`；compile=0x0045F450，handler=0x0046D7A0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1615 次。 |
| `CMD_168` | `CMakeZ` | `kind=0x08, opcode_or_type=0x00A8` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=9..9; push=0; argc_seen=未见 | EXE 命令表项 0x004E99C0=0x4E7CE0+168*44；name_ptr=0x004ECCF4 指向 `CMakeZ`；compile=0x0045F450，handler=0x0046D900。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_169` | `ChangeZ` | `kind=0x08, opcode_or_type=0x00A9` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E99EC=0x4E7CE0+169*44；name_ptr=0x004ECCEC 指向 `ChangeZ`；compile=0x0045F450，handler=0x0046DA20。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_170` | `CUpdate` | `kind=0x08, opcode_or_type=0x00AA` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:440 | EXE 命令表项 0x004E9A18=0x4E7CE0+170*44；name_ptr=0x004ECCE4 指向 `CUpdate`；compile=0x0045F450，handler=0x0046DA80。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 440 次。 |
| `CMD_171` | `CShow` | `kind=0x08, opcode_or_type=0x00AB` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:2446 | EXE 命令表项 0x004E9A44=0x4E7CE0+171*44；name_ptr=0x004ECCDC 指向 `CShow`；compile=0x0045F450，handler=0x0046DAD0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2446 次。 |
| `CMD_172` | `CShowGroup` | `kind=0x08, opcode_or_type=0x00AC` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=0x04:157 | EXE 命令表项 0x004E9A70=0x4E7CE0+172*44；name_ptr=0x004ECCD0 指向 `CShowGroup`；compile=0x0045F450，handler=0x0046DC00。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 157 次。 |
| `CMD_173` | `CDrawMode` | `kind=0x08, opcode_or_type=0x00AD` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..3; push=0; argc_seen=0x02:496, 0x03:132 | EXE 命令表项 0x004E9A9C=0x4E7CE0+173*44；name_ptr=0x004ECCC4 指向 `CDrawMode`；compile=0x0045F450，handler=0x0046DCB0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 628 次。 |
| `CMD_174` | `CStencil` | `kind=0x08, opcode_or_type=0x00AE` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..4; push=0; argc_seen=未见 | EXE 命令表项 0x004E9AC8=0x4E7CE0+174*44；name_ptr=0x004ECCB8 指向 `CStencil`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_175` | `CPos` | `kind=0x08, opcode_or_type=0x00AF` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..7; push=0; argc_seen=0x03:709, 0x05:61 | EXE 命令表项 0x004E9AF4=0x4E7CE0+175*44；name_ptr=0x004ECCB0 指向 `CPos`；compile=0x0045F450，handler=0x0046DDB0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 770 次。 |
| `CMD_176` | `CPosZ` | `kind=0x08, opcode_or_type=0x00B0` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=0x04:1 | EXE 命令表项 0x004E9B20=0x4E7CE0+176*44；name_ptr=0x004ECCA8 指向 `CPosZ`；compile=0x0045F450，handler=0x0046DF20。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_177` | `CPosM` | `kind=0x08, opcode_or_type=0x00B1` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..8; push=0; argc_seen=0x06:100 | EXE 命令表项 0x004E9B4C=0x4E7CE0+177*44；name_ptr=0x004ECCA0 指向 `CPosM`；compile=0x0045F450，handler=0x0046DFE0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 100 次。 |
| `CMD_178` | `CDrawSurf` | `kind=0x08, opcode_or_type=0x00B2` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:10 | EXE 命令表项 0x004E9B78=0x4E7CE0+178*44；name_ptr=0x004ECC94 指向 `CDrawSurf`；compile=0x0045F450，handler=0x0046E1A0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10 次。 |
| `CMD_179` | `CGroup` | `kind=0x08, opcode_or_type=0x00B3` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:1215 | EXE 命令表项 0x004E9BA4=0x4E7CE0+179*44；name_ptr=0x004ECC8C 指向 `CGroup`；compile=0x0045F450，handler=0x0046E210。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1215 次。 |
| `CMD_180` | `CSurf` | `kind=0x08, opcode_or_type=0x00B4` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:43 | EXE 命令表项 0x004E9BD0=0x4E7CE0+180*44；name_ptr=0x004ECC84 指向 `CSurf`；compile=0x0045F450，handler=0x0046E290。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 43 次。 |
| `CMD_181` | `CSrc` | `kind=0x08, opcode_or_type=0x00B5` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=0x05:99 | EXE 命令表项 0x004E9BFC=0x4E7CE0+181*44；name_ptr=0x004ECC7C 指向 `CSrc`；compile=0x0045F450，handler=0x0046E300。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 99 次。 |
| `CMD_182` | `CReduct` | `kind=0x08, opcode_or_type=0x00B6` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..3; push=0; argc_seen=0x02:52 | EXE 命令表项 0x004E9C28=0x4E7CE0+182*44；name_ptr=0x004ECC74 指向 `CReduct`；compile=0x0045F450，handler=0x0046E410。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 52 次。 |
| `CMD_183` | `CTone` | `kind=0x08, opcode_or_type=0x00B7` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E9C54=0x4E7CE0+183*44；name_ptr=0x004ECC6C 指向 `CTone`；compile=0x0045F450，handler=0x0046E4C0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_184` | `CMove` | `kind=0x08, opcode_or_type=0x00B8` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E9C80=0x4E7CE0+184*44；name_ptr=0x004ECC64 指向 `CMove`；compile=0x0045F450，handler=0x0046E570。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_185` | `CAlpha` | `kind=0x08, opcode_or_type=0x00B9` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:924 | EXE 命令表项 0x004E9CAC=0x4E7CE0+185*44；name_ptr=0x004ECC5C 指向 `CAlpha`；compile=0x0045F450，handler=0x0046E630。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 924 次。 |
| `CMD_186` | `CCKey` | `kind=0x08, opcode_or_type=0x00BA` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:300 | EXE 命令表项 0x004E9CD8=0x4E7CE0+186*44；name_ptr=0x004ECC54 指向 `CCKey`；compile=0x0045F450，handler=0x0046E6B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 300 次。 |
| `CMD_187` | `CRedraw` | `kind=0x08, opcode_or_type=0x00BB` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004E9D04=0x4E7CE0+187*44；name_ptr=0x004ECC4C 指向 `CRedraw`；compile=0x0045F450，handler=0x0046E710。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_188` | `CErase` | `kind=0x08, opcode_or_type=0x00BC` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..1; push=0; argc_seen=0x01:1769 | EXE 命令表项 0x004E9D30=0x4E7CE0+188*44；name_ptr=0x004ECC44 指向 `CErase`；compile=0x0045F450，handler=0x0046E760。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1769 次。 |
| `CMD_189` | `CReductXY` | `kind=0x08, opcode_or_type=0x00BD` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..5; push=0; argc_seen=0x03:45, 0x05:3 | EXE 命令表项 0x004E9D5C=0x4E7CE0+189*44；name_ptr=0x004ECC38 指向 `CReductXY`；compile=0x0045F450，handler=0x0046E800。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 48 次。 |
| `CMD_190` | `CReductR` | `kind=0x08, opcode_or_type=0x00BE` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..8; push=0; argc_seen=未见 | EXE 命令表项 0x004E9D88=0x4E7CE0+190*44；name_ptr=0x004ECC2C 指向 `CReductR`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_191` | `CRotate` | `kind=0x08, opcode_or_type=0x00BF` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..8; push=0; argc_seen=0x05:10 | EXE 命令表项 0x004E9DB4=0x4E7CE0+191*44；name_ptr=0x004ECC24 指向 `CRotate`；compile=0x0045F450，handler=0x0047B240。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10 次。 |
| `CMD_192` | `CSrcA` | `kind=0x08, opcode_or_type=0x00C0` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=未见 | EXE 命令表项 0x004E9DE0=0x4E7CE0+192*44；name_ptr=0x004ECC1C 指向 `CSrcA`；compile=0x0045F450，handler=0x0046E910。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_193` | `CToneC` | `kind=0x08, opcode_or_type=0x00C1` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:43 | EXE 命令表项 0x004E9E0C=0x4E7CE0+193*44；name_ptr=0x004ECC14 指向 `CToneC`；compile=0x0045F450，handler=0x0046EAA0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 43 次。 |
| `CMD_194` | `CAlphaM` | `kind=0x08, opcode_or_type=0x00C2` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:853 | EXE 命令表项 0x004E9E38=0x4E7CE0+194*44；name_ptr=0x004ECC0C 指向 `CAlphaM`；compile=0x0045F450，handler=0x0046EB30。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 853 次。 |
| `CMD_195` | `CSrcC` | `kind=0x08, opcode_or_type=0x00C3` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=7..7; push=0; argc_seen=0x07:225 | EXE 命令表项 0x004E9E64=0x4E7CE0+195*44；name_ptr=0x004ECC04 指向 `CSrcC`；compile=0x0045F450，handler=0x0046EBE0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 225 次。 |
| `CMD_196` | `CSrcAC` | `kind=0x08, opcode_or_type=0x00C4` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=8..8; push=0; argc_seen=未见 | EXE 命令表项 0x004E9E90=0x4E7CE0+196*44；name_ptr=0x004ECBFC 指向 `CSrcAC`；compile=0x0045F450，handler=0x0046ED80。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_197` | `CClip` | `kind=0x08, opcode_or_type=0x00C5` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=0x05:12 | EXE 命令表项 0x004E9EBC=0x4E7CE0+197*44；name_ptr=0x004ECBF4 指向 `CClip`；compile=0x0045F450，handler=0x0046F050。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 12 次。 |
| `CMD_198` | `CExtDraw` | `kind=0x08, opcode_or_type=0x00C6` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:593 | EXE 命令表项 0x004E9EE8=0x4E7CE0+198*44；name_ptr=0x004ECBE8 指向 `CExtDraw`；compile=0x0045F450，handler=0x0046F0D0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 593 次。 |
| `CMD_199` | `CExtPrm` | `kind=0x08, opcode_or_type=0x00C7` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..4; push=0; argc_seen=0x04:1481 | EXE 命令表项 0x004E9F14=0x4E7CE0+199*44；name_ptr=0x004ECBE0 指向 `CExtPrm`；compile=0x0045F450，handler=0x0046F1F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1481 次。 |
| `CMD_200` | `COvl` | `kind=0x08, opcode_or_type=0x00C8` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:472 | EXE 命令表项 0x004E9F40=0x4E7CE0+200*44；name_ptr=0x004ECBD8 指向 `COvl`；compile=0x0045F450，handler=0x0046F420。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 472 次。 |
| `CMD_201` | `CCell` | `kind=0x08, opcode_or_type=0x00C9` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..3; push=0; argc_seen=0x02:515, 0x03:40 | EXE 命令表项 0x004E9F6C=0x4E7CE0+201*44；name_ptr=0x004ECBD0 指向 `CCell`；compile=0x0045F450，handler=0x0046F2D0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 555 次。 |
| `CMD_202` | `CCellA` | `kind=0x08, opcode_or_type=0x00CA` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..4; push=0; argc_seen=未见 | EXE 命令表项 0x004E9F98=0x4E7CE0+202*44；name_ptr=0x004ECBC8 指向 `CCellA`；compile=0x0045F450，handler=0x0046F340。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_203` | `CCopy` | `kind=0x08, opcode_or_type=0x00CB` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004E9FC4=0x4E7CE0+203*44；name_ptr=0x004ECBC0 指向 `CCopy`；compile=0x0045F450，handler=0x0046F4D0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_204` | `CCopyReg` | `kind=0x08, opcode_or_type=0x00CC` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:21 | EXE 命令表项 0x004E9FF0=0x4E7CE0+204*44；name_ptr=0x004ECBB4 指向 `CCopyReg`；compile=0x0045F450，handler=0x0046F8E0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 21 次。 |
| `CMD_205` | `CWrk` | `kind=0x08, opcode_or_type=0x00CD` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..4; push=0; argc_seen=0x04:714 | EXE 命令表项 0x004EA01C=0x4E7CE0+205*44；name_ptr=0x004ECBAC 指向 `CWrk`；compile=0x0045F450，handler=0x0046FB20。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 714 次。 |
| `CMD_206` | `CLWrk` | `kind=0x08, opcode_or_type=0x00CE` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..4; push=0; argc_seen=0x04:28 | EXE 命令表项 0x004EA048=0x4E7CE0+206*44；name_ptr=0x004ECBA4 指向 `CLWrk`；compile=0x0045F450，handler=0x0046FD80。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 28 次。 |
| `CMD_207` | `CAdjMsg` | `kind=0x08, opcode_or_type=0x00CF` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:40 | EXE 命令表项 0x004EA074=0x4E7CE0+207*44；name_ptr=0x004ECB9C 指向 `CAdjMsg`；compile=0x0045F450，handler=0x00470020。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 40 次。 |
| `CMD_208` | `Write` | `kind=0x08, opcode_or_type=0x00D0` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:81 | EXE 命令表项 0x004EA0A0=0x4E7CE0+208*44；name_ptr=0x004ECB94 指向 `Write`；compile=0x0045F450，handler=0x004700C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 81 次。 |
| `CMD_209` | `DispOff` | `kind=0x08, opcode_or_type=0x00D1` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:2 | EXE 命令表项 0x004EA0CC=0x4E7CE0+209*44；name_ptr=0x004ECB8C 指向 `DispOff`；compile=0x0045F450，handler=0x00470120。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2 次。 |
| `CMD_210` | `ScreenPos` | `kind=0x08, opcode_or_type=0x00D2` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:42 | EXE 命令表项 0x004EA0F8=0x4E7CE0+210*44；name_ptr=0x004ECB80 指向 `ScreenPos`；compile=0x0045F450，handler=0x00470180。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 42 次。 |
| `CMD_211` | `ScreenClip` | `kind=0x08, opcode_or_type=0x00D3` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=未见 | EXE 命令表项 0x004EA124=0x4E7CE0+211*44；name_ptr=0x004ECB74 指向 `ScreenClip`；compile=0x0045F450，handler=0x004701E0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_212` | `MChkChr` | `kind=0x08, opcode_or_type=0x00D4` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:34 | EXE 命令表项 0x004EA150=0x4E7CE0+212*44；name_ptr=0x004ECB6C 指向 `MChkChr`；compile=0x0045F450，handler=0x00470460。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 34 次。 |
| `CMD_213` | `RedrawWait` | `kind=0x08, opcode_or_type=0x00D5` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:344 | EXE 命令表项 0x004EA17C=0x4E7CE0+213*44；name_ptr=0x004ECB60 指向 `RedrawWait`；compile=0x0045F450，handler=0x004704C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 344 次。 |
| `CMD_214` | `AxisMapCtrl` | `kind=0x08, opcode_or_type=0x00D6` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..6; push=0; argc_seen=未见 | EXE 命令表项 0x004EA1A8=0x4E7CE0+214*44；name_ptr=0x004ECB54 指向 `AxisMapCtrl`；compile=0x0045F450，handler=0x004709B0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_215` | `PrmMapCtrl` | `kind=0x08, opcode_or_type=0x00D7` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..6; push=0; argc_seen=未见 | EXE 命令表项 0x004EA1D4=0x4E7CE0+215*44；name_ptr=0x004ECB48 指向 `PrmMapCtrl`；compile=0x0045F450，handler=0x00471320。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_216` | `PalAlloc` | `kind=0x08, opcode_or_type=0x00D8` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004EA200=0x4E7CE0+216*44；name_ptr=0x004ECB3C 指向 `PalAlloc`；compile=0x0045F450，handler=0x00471C60。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_217` | `PalLoad` | `kind=0x08, opcode_or_type=0x00D9` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EA22C=0x4E7CE0+217*44；name_ptr=0x004ECB34 指向 `PalLoad`；compile=0x0045F450，handler=0x00471CA0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_218` | `PalFree` | `kind=0x08, opcode_or_type=0x00DA` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EA258=0x4E7CE0+218*44；name_ptr=0x004ECB2C 指向 `PalFree`；compile=0x0045F450，handler=0x00471D10。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_219` | `PalGet` | `kind=0x08, opcode_or_type=0x00DB` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=未见 | EXE 命令表项 0x004EA284=0x4E7CE0+219*44；name_ptr=0x004ECB24 指向 `PalGet`；compile=0x0045F450，handler=0x00471D70。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_220` | `PalSet` | `kind=0x08, opcode_or_type=0x00DC` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=未见 | EXE 命令表项 0x004EA2B0=0x4E7CE0+220*44；name_ptr=0x004ECB1C 指向 `PalSet`；compile=0x0045F450，handler=0x00471DE0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_221` | `PalSetGroup` | `kind=0x08, opcode_or_type=0x00DD` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=7..7; push=0; argc_seen=未见 | EXE 命令表项 0x004EA2DC=0x4E7CE0+221*44；name_ptr=0x004ECB10 指向 `PalSetGroup`；compile=0x0045F450，handler=0x00471E50。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_222` | `LTblAlloc` | `kind=0x08, opcode_or_type=0x00DE` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004EA308=0x4E7CE0+222*44；name_ptr=0x004ECB04 指向 `LTblAlloc`；compile=0x0045F450，handler=0x00472260。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_223` | `LTblLoad` | `kind=0x08, opcode_or_type=0x00DF` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EA334=0x4E7CE0+223*44；name_ptr=0x004ECAF8 指向 `LTblLoad`；compile=0x0045F450，handler=0x004722A0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_224` | `LTblFree` | `kind=0x08, opcode_or_type=0x00E0` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EA360=0x4E7CE0+224*44；name_ptr=0x004ECAEC 指向 `LTblFree`；compile=0x0045F450，handler=0x00471D10。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_225` | `LTblSet` | `kind=0x08, opcode_or_type=0x00E1` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EA38C=0x4E7CE0+225*44；name_ptr=0x004ECAE4 指向 `LTblSet`；compile=0x0045F450，handler=0x00472310。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_226` | `LTblSet2` | `kind=0x08, opcode_or_type=0x00E2` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=未见 | EXE 命令表项 0x004EA3B8=0x4E7CE0+226*44；name_ptr=0x004ECAD8 指向 `LTblSet2`；compile=0x0045F450，handler=0x00472410。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_227` | `PDiv16Alloc` | `kind=0x08, opcode_or_type=0x00E3` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:29 | EXE 命令表项 0x004EA3E4=0x4E7CE0+227*44；name_ptr=0x004ECACC 指向 `PDiv16Alloc`；compile=0x0045F450，handler=0x004725F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 29 次。 |
| `CMD_228` | `PDiv16Free` | `kind=0x08, opcode_or_type=0x00E4` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:11 | EXE 命令表项 0x004EA410=0x4E7CE0+228*44；name_ptr=0x004ECAC0 指向 `PDiv16Free`；compile=0x0045F450，handler=0x00471D10。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 11 次。 |
| `CMD_229` | `PDiv16Get` | `kind=0x08, opcode_or_type=0x00E5` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=未见 | EXE 命令表项 0x004EA43C=0x4E7CE0+229*44；name_ptr=0x004ECAB4 指向 `PDiv16Get`；compile=0x0045F450，handler=0x00472640。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_230` | `PDiv16Set` | `kind=0x08, opcode_or_type=0x00E6` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=8..8; push=0; argc_seen=未见 | EXE 命令表项 0x004EA468=0x4E7CE0+230*44；name_ptr=0x004ECAA8 指向 `PDiv16Set`；compile=0x0045F450，handler=0x004726E0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_231` | `PDiv16Auto` | `kind=0x08, opcode_or_type=0x00E7` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..14; push=0; argc_seen=0x07:27, 0x0E:49 | EXE 命令表项 0x004EA494=0x4E7CE0+231*44；name_ptr=0x004ECA9C 指向 `PDiv16Auto`；compile=0x0045F450，handler=0x00472780。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 76 次。 |
| `CMD_232` | `RstExAlloc` | `kind=0x08, opcode_or_type=0x00E8` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..3; push=0; argc_seen=0x03:1 | EXE 命令表项 0x004EA4C0=0x4E7CE0+232*44；name_ptr=0x004ECA90 指向 `RstExAlloc`；compile=0x0045F450，handler=0x00473700。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_233` | `RstExFree` | `kind=0x08, opcode_or_type=0x00E9` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:1 | EXE 命令表项 0x004EA4EC=0x4E7CE0+233*44；name_ptr=0x004ECA84 指向 `RstExFree`；compile=0x0045F450，handler=0x00471D10。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_234` | `RstExGet` | `kind=0x08, opcode_or_type=0x00EA` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=未见 | EXE 命令表项 0x004EA518=0x4E7CE0+234*44；name_ptr=0x004ECA78 指向 `RstExGet`；compile=0x0045F450，handler=0x00473780。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_235` | `RstExSet` | `kind=0x08, opcode_or_type=0x00EB` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=未见 | EXE 命令表项 0x004EA544=0x4E7CE0+235*44；name_ptr=0x004ECA6C 指向 `RstExSet`；compile=0x0045F450，handler=0x00473820。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_236` | `RstExAuto` | `kind=0x08, opcode_or_type=0x00EC` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..10; push=0; argc_seen=0x05:1, 0x09:1 | EXE 命令表项 0x004EA570=0x4E7CE0+236*44；name_ptr=0x004ECA60 指向 `RstExAuto`；compile=0x0045F450，handler=0x004738C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2 次。 |
| `CMD_237` | `GLineAlloc` | `kind=0x08, opcode_or_type=0x00ED` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004EA59C=0x4E7CE0+237*44；name_ptr=0x004ECA54 指向 `GLineAlloc`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_238` | `GLineLoad` | `kind=0x08, opcode_or_type=0x00EE` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EA5C8=0x4E7CE0+238*44；name_ptr=0x004ECA48 指向 `GLineLoad`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_239` | `GLineFree` | `kind=0x08, opcode_or_type=0x00EF` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EA5F4=0x4E7CE0+239*44；name_ptr=0x004ECA3C 指向 `GLineFree`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_240` | `GLineGet` | `kind=0x08, opcode_or_type=0x00F0` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..6; push=0; argc_seen=未见 | EXE 命令表项 0x004EA620=0x4E7CE0+240*44；name_ptr=0x004ECA30 指向 `GLineGet`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_241` | `GLineSet` | `kind=0x08, opcode_or_type=0x00F1` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..6; push=0; argc_seen=未见 | EXE 命令表项 0x004EA64C=0x4E7CE0+241*44；name_ptr=0x004ECA24 指向 `GLineSet`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_242` | `GLineSet2` | `kind=0x08, opcode_or_type=0x00F2` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..7; push=0; argc_seen=未见 | EXE 命令表项 0x004EA678=0x4E7CE0+242*44；name_ptr=0x004ECA18 指向 `GLineSet2`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_243` | `GLineAuto` | `kind=0x08, opcode_or_type=0x00F3` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=8..8; push=0; argc_seen=未见 | EXE 命令表项 0x004EA6A4=0x4E7CE0+243*44；name_ptr=0x004ECA0C 指向 `GLineAuto`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_244` | `PDivAlloc` | `kind=0x08, opcode_or_type=0x00F4` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EA6D0=0x4E7CE0+244*44；name_ptr=0x004ECA00 指向 `PDivAlloc`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_245` | `PDivFree` | `kind=0x08, opcode_or_type=0x00F5` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EA6FC=0x4E7CE0+245*44；name_ptr=0x004EC9F4 指向 `PDivFree`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_246` | `PDivGet` | `kind=0x08, opcode_or_type=0x00F6` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=未见 | EXE 命令表项 0x004EA728=0x4E7CE0+246*44；name_ptr=0x004EC9EC 指向 `PDivGet`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_247` | `PDivSet` | `kind=0x08, opcode_or_type=0x00F7` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=8..8; push=0; argc_seen=未见 | EXE 命令表项 0x004EA754=0x4E7CE0+247*44；name_ptr=0x004EC9E4 指向 `PDivSet`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_248` | `PDivAuto` | `kind=0x08, opcode_or_type=0x00F8` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..14; push=0; argc_seen=未见 | EXE 命令表项 0x004EA780=0x4E7CE0+248*44；name_ptr=0x004EC9D8 指向 `PDivAuto`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_249` | `GFillAlloc` | `kind=0x08, opcode_or_type=0x00F9` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=7..7; push=0; argc_seen=未见 | EXE 命令表项 0x004EA7AC=0x4E7CE0+249*44；name_ptr=0x004EC9CC 指向 `GFillAlloc`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_250` | `GFillFree` | `kind=0x08, opcode_or_type=0x00FA` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EA7D8=0x4E7CE0+250*44；name_ptr=0x004EC9C0 指向 `GFillFree`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_251` | `GFillGet` | `kind=0x08, opcode_or_type=0x00FB` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=未见 | EXE 命令表项 0x004EA804=0x4E7CE0+251*44；name_ptr=0x004EC9B4 指向 `GFillGet`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_252` | `GFillSet` | `kind=0x08, opcode_or_type=0x00FC` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=未见 | EXE 命令表项 0x004EA830=0x4E7CE0+252*44；name_ptr=0x004EC9A8 指向 `GFillSet`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_253` | `OvlCtrl` | `kind=0x08, opcode_or_type=0x00FD` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:505 | EXE 命令表项 0x004EA85C=0x4E7CE0+253*44；name_ptr=0x004EC9A0 指向 `OvlCtrl`；compile=0x0045F450，handler=0x00473E20。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 505 次。 |
| `CMD_254` | `RedrawAll` | `kind=0x08, opcode_or_type=0x00FE` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:627 | EXE 命令表项 0x004EA888=0x4E7CE0+254*44；name_ptr=0x004EC994 指向 `RedrawAll`；compile=0x0045F450，handler=0x00473E60。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 627 次。 |
| `CMD_255` | `ForceNoDraw` | `kind=0x08, opcode_or_type=0x00FF` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:3 | EXE 命令表项 0x004EA8B4=0x4E7CE0+255*44；name_ptr=0x004EC988 指向 `ForceNoDraw`；compile=0x0045F450，handler=0x00473E80。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 3 次。 |
| `CMD_256` | `PartsBuilt` | `kind=0x08, opcode_or_type=0x0100` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=11..11; push=0; argc_seen=未见 | EXE 命令表项 0x004EA8E0=0x4E7CE0+256*44；name_ptr=0x004EC97C 指向 `PartsBuilt`；compile=0x0045F450，handler=0x00473EB0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_257` | `KeyJump` | `kind=0x08, opcode_or_type=0x0101` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EA90C=0x4E7CE0+257*44；name_ptr=0x004EC974 指向 `KeyJump`；compile=0x0045F450，handler=0x004741F0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_258` | `KeyStsGet` | `kind=0x08, opcode_or_type=0x0102` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:821 | EXE 命令表项 0x004EA938=0x4E7CE0+258*44；name_ptr=0x004EC968 指向 `KeyStsGet`；compile=0x0045F450，handler=0x00474310。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 821 次。 |
| `CMD_259` | `KeyStsSet` | `kind=0x08, opcode_or_type=0x0103` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:971 | EXE 命令表项 0x004EA964=0x4E7CE0+259*44；name_ptr=0x004EC95C 指向 `KeyStsSet`；compile=0x0045F450，handler=0x004744E0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 971 次。 |
| `CMD_260` | `KeyMouse` | `kind=0x08, opcode_or_type=0x0104` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EA990=0x4E7CE0+260*44；name_ptr=0x004EC950 指向 `KeyMouse`；compile=0x0045F450，handler=0x004745A0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_261` | `KeyReset` | `kind=0x08, opcode_or_type=0x0105` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:165 | EXE 命令表项 0x004EA9BC=0x4E7CE0+261*44；name_ptr=0x004EC944 指向 `KeyReset`；compile=0x0045F450，handler=0x004745E0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 165 次。 |
| `CMD_262` | `Mouse` | `kind=0x08, opcode_or_type=0x0106` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..2; push=0; argc_seen=0x01:230, 0x02:12 | EXE 命令表项 0x004EA9E8=0x4E7CE0+262*44；name_ptr=0x004EC93C 指向 `Mouse`；compile=0x0045F450，handler=0x00474C80。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 242 次。 |
| `CMD_263` | `MPos` | `kind=0x08, opcode_or_type=0x0107` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:53 | EXE 命令表项 0x004EAA14=0x4E7CE0+263*44；name_ptr=0x004EC934 指向 `MPos`；compile=0x0045F450，handler=0x00474D80。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 53 次。 |
| `CMD_264` | `MClip` | `kind=0x08, opcode_or_type=0x0108` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=未见 | EXE 命令表项 0x004EAA40=0x4E7CE0+264*44；name_ptr=0x004EC92C 指向 `MClip`；compile=0x0045F450，handler=0x00474DD0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_265` | `MouseReset` | `kind=0x08, opcode_or_type=0x0109` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004EAA6C=0x4E7CE0+265*44；name_ptr=0x004EC920 指向 `MouseReset`；compile=0x0045F450，handler=0x00474E60。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_266` | `CurCtrl` | `kind=0x08, opcode_or_type=0x010A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=0x02:1, 0x03:1, 0x04:4, 0x0C:12 | EXE 命令表项 0x004EAA98=0x4E7CE0+266*44；name_ptr=0x004EC918 指向 `CurCtrl`；compile=0x0045F450，handler=0x00474EA0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 18 次。 |
| `CMD_267` | `FontCtrl` | `kind=0x08, opcode_or_type=0x010B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=0x02:2, 0x04:11, 0x05:2, 0x06:5, 0x07:5, 0x08:6, 0x0A:26, 0x0B:4, 0x0D:1 | EXE 命令表项 0x004EAAC4=0x4E7CE0+267*44；name_ptr=0x004EC90C 指向 `FontCtrl`；compile=0x0045F450，handler=0x004750E0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 62 次。 |
| `CMD_268` | `TSet` | `kind=0x08, opcode_or_type=0x010C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=8..8; push=0; argc_seen=0x08:111 | EXE 命令表项 0x004EAAF0=0x4E7CE0+268*44；name_ptr=0x004EC904 指向 `TSet`；compile=0x0045F450，handler=0x00475B90。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 111 次。 |
| `CMD_269` | `MsgKeySet` | `kind=0x08, opcode_or_type=0x010D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=9..9; push=0; argc_seen=0x09:1 | EXE 命令表项 0x004EAB1C=0x4E7CE0+269*44；name_ptr=0x004EC8F8 指向 `MsgKeySet`；compile=0x0045F450，handler=0x00475DC0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_270` | `MsgKeySts` | `kind=0x08, opcode_or_type=0x010E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:372 | EXE 命令表项 0x004EAB48=0x4E7CE0+270*44；name_ptr=0x004EC8EC 指向 `MsgKeySts`；compile=0x0045F450，handler=0x00475EB0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 372 次。 |
| `CMD_271` | `MsgCSet` | `kind=0x08, opcode_or_type=0x010F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..2; push=0; argc_seen=0x01:627, 0x02:90 | EXE 命令表项 0x004EAB74=0x4E7CE0+271*44；name_ptr=0x004EC8E4 指向 `MsgCSet`；compile=0x0045F450，handler=0x00475F40。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 717 次。 |
| `CMD_272` | `MsgCReset` | `kind=0x08, opcode_or_type=0x0110` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:739 | EXE 命令表项 0x004EABA0=0x4E7CE0+272*44；name_ptr=0x004EC8D8 指向 `MsgCReset`；compile=0x0045F450，handler=0x00475FD0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 739 次。 |
| `CMD_273` | `MsgHideGet` | `kind=0x08, opcode_or_type=0x0111` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:42 | EXE 命令表项 0x004EABCC=0x4E7CE0+273*44；name_ptr=0x004EC8CC 指向 `MsgHideGet`；compile=0x0045F450，handler=0x00476070。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 42 次。 |
| `CMD_274` | `MsgHideSet` | `kind=0x08, opcode_or_type=0x0112` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:199 | EXE 命令表项 0x004EABF8=0x4E7CE0+274*44；name_ptr=0x004EC8C0 指向 `MsgHideSet`；compile=0x0045F450，handler=0x004760C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 199 次。 |
| `CMD_275` | `MsgHideUpd` | `kind=0x08, opcode_or_type=0x0113` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:229 | EXE 命令表项 0x004EAC24=0x4E7CE0+275*44；name_ptr=0x004EC8B4 指向 `MsgHideUpd`；compile=0x0045F450，handler=0x00476130。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 229 次。 |
| `CMD_276` | `MsgHideReset` | `kind=0x08, opcode_or_type=0x0114` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004EAC50=0x4E7CE0+276*44；name_ptr=0x004EC8A4 指向 `MsgHideReset`；compile=0x0045F450，handler=0x004762D0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_277` | `CurHideGet` | `kind=0x08, opcode_or_type=0x0115` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:558 | EXE 命令表项 0x004EAC7C=0x4E7CE0+277*44；name_ptr=0x004EC898 指向 `CurHideGet`；compile=0x0045F450，handler=0x00476300。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 558 次。 |
| `CMD_278` | `CurHideSet` | `kind=0x08, opcode_or_type=0x0116` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:541 | EXE 命令表项 0x004EACA8=0x4E7CE0+278*44；name_ptr=0x004EC88C 指向 `CurHideSet`；compile=0x0045F450，handler=0x00476350。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 541 次。 |
| `CMD_279` | `MsgSmooth` | `kind=0x08, opcode_or_type=0x0117` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EACD4=0x4E7CE0+279*44；name_ptr=0x004EC880 指向 `MsgSmooth`；compile=0x0045F450，handler=0x00476390。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_280` | `MsgRect` | `kind=0x08, opcode_or_type=0x0118` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..6; push=0; argc_seen=0x06:44 | EXE 命令表项 0x004EAD00=0x4E7CE0+280*44；name_ptr=0x004EC878 指向 `MsgRect`；compile=0x0045F450，handler=0x004766C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 44 次。 |
| `CMD_281` | `MsgFace` | `kind=0x08, opcode_or_type=0x0119` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..4; push=0; argc_seen=未见 | EXE 命令表项 0x004EAD2C=0x4E7CE0+281*44；name_ptr=0x004EC870 指向 `MsgFace`；compile=0x0045F450，handler=0x00476A50。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_282` | `MsgVoice` | `kind=0x08, opcode_or_type=0x011A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..6; push=0; argc_seen=0x06:11 | EXE 命令表项 0x004EAD58=0x4E7CE0+282*44；name_ptr=0x004EC864 指向 `MsgVoice`；compile=0x0045F450，handler=0x00476D10。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 11 次。 |
| `CMD_283` | `MsgReinit` | `kind=0x08, opcode_or_type=0x011B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EAD84=0x4E7CE0+283*44；name_ptr=0x004EC858 指向 `MsgReinit`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_284` | `SndInit` | `kind=0x08, opcode_or_type=0x011C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004EADB0=0x4E7CE0+284*44；name_ptr=0x004EC850 指向 `SndInit`；compile=0x0045F450，handler=0x00477090。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_285` | `Config` | `kind=0x08, opcode_or_type=0x011D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=未见 | EXE 命令表项 0x004EADDC=0x4E7CE0+285*44；name_ptr=0x004EC848 指向 `Config`；compile=0x0045F450，handler=0x00477540。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_286` | `PcmPlay` | `kind=0x08, opcode_or_type=0x011E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=0x06:36 | EXE 命令表项 0x004EAE08=0x4E7CE0+286*44；name_ptr=0x004EC840 指向 `PcmPlay`；compile=0x0045F450，handler=0x00478750。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 36 次。 |
| `CMD_287` | `PcmLoad` | `kind=0x08, opcode_or_type=0x011F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:4 | EXE 命令表项 0x004EAE34=0x4E7CE0+287*44；name_ptr=0x004EC838 指向 `PcmLoad`；compile=0x0045F450，handler=0x004787F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 4 次。 |
| `CMD_288` | `PcmLPlay` | `kind=0x08, opcode_or_type=0x0120` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=0x06:313 | EXE 命令表项 0x004EAE60=0x4E7CE0+288*44；name_ptr=0x004EC82C 指向 `PcmLPlay`；compile=0x0045F450，handler=0x00478850。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 313 次。 |
| `CMD_289` | `PcmStop` | `kind=0x08, opcode_or_type=0x0121` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:72 | EXE 命令表项 0x004EAE8C=0x4E7CE0+289*44；name_ptr=0x004EC824 指向 `PcmStop`；compile=0x0045F450，handler=0x004788F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 72 次。 |
| `CMD_290` | `PcmVol` | `kind=0x08, opcode_or_type=0x0122` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=0x04:100 | EXE 命令表项 0x004EAEB8=0x4E7CE0+290*44；name_ptr=0x004EC81C 指向 `PcmVol`；compile=0x0045F450，handler=0x00478980。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 100 次。 |
| `CMD_291` | `PcmGetPos` | `kind=0x08, opcode_or_type=0x0123` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:32 | EXE 命令表项 0x004EAEE4=0x4E7CE0+291*44；name_ptr=0x004EC810 指向 `PcmGetPos`；compile=0x0045F450，handler=0x00478A00。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 32 次。 |
| `CMD_292` | `MLoad` | `kind=0x08, opcode_or_type=0x0124` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..4; push=0; argc_seen=0x04:10 | EXE 命令表项 0x004EAF10=0x4E7CE0+292*44；name_ptr=0x004EC808 指向 `MLoad`；compile=0x0045F450，handler=0x00479F30。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10 次。 |
| `CMD_293` | `MLPlay` | `kind=0x08, opcode_or_type=0x0125` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=0x06:10 | EXE 命令表项 0x004EAF3C=0x4E7CE0+293*44；name_ptr=0x004EC800 指向 `MLPlay`；compile=0x0045F450，handler=0x00479FD0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10 次。 |
| `CMD_294` | `MPlay` | `kind=0x08, opcode_or_type=0x0126` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=0x06:10 | EXE 命令表项 0x004EAF68=0x4E7CE0+294*44；name_ptr=0x004EC7F8 指向 `MPlay`；compile=0x0045F450，handler=0x0047A070。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10 次。 |
| `CMD_295` | `MStop` | `kind=0x08, opcode_or_type=0x0127` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:50 | EXE 命令表项 0x004EAF94=0x4E7CE0+295*44；name_ptr=0x004EC7F0 指向 `MStop`；compile=0x0045F450，handler=0x0047A110。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 50 次。 |
| `CMD_296` | `MVol` | `kind=0x08, opcode_or_type=0x0128` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=0x04:80 | EXE 命令表项 0x004EAFC0=0x4E7CE0+296*44；name_ptr=0x004EC7E8 指向 `MVol`；compile=0x0045F450，handler=0x0047A150。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 80 次。 |
| `CMD_297` | `MGetPos` | `kind=0x08, opcode_or_type=0x0129` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:66 | EXE 命令表项 0x004EAFEC=0x4E7CE0+297*44；name_ptr=0x004EC7E0 指向 `MGetPos`；compile=0x0045F450，handler=0x0047A1D0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 66 次。 |
| `CMD_298` | `SeMVol` | `kind=0x08, opcode_or_type=0x012A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004EB018=0x4E7CE0+298*44；name_ptr=0x004EC7D8 指向 `SeMVol`；compile=0x0045F450，handler=0x0047A310。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_299` | `VoiceMVol` | `kind=0x08, opcode_or_type=0x012B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004EB044=0x4E7CE0+299*44；name_ptr=0x004EC7CC 指向 `VoiceMVol`；compile=0x0045F450，handler=0x0047A3F0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_300` | `BgmMVol` | `kind=0x08, opcode_or_type=0x012C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004EB070=0x4E7CE0+300*44；name_ptr=0x004EC7C4 指向 `BgmMVol`；compile=0x0045F450，handler=0x0047A480。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_301` | `MovieS` | `kind=0x08, opcode_or_type=0x012D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..10; push=0; argc_seen=0x03:2, 0x04:2, 0x0A:2 | EXE 命令表项 0x004EB09C=0x4E7CE0+301*44；name_ptr=0x004EC7BC 指向 `MovieS`；compile=0x0045F450，handler=0x0047A4D0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 6 次。 |
| `CMD_302` | `VoiceInit` | `kind=0x08, opcode_or_type=0x012E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=0x06:1 | EXE 命令表项 0x004EB0C8=0x4E7CE0+302*44；name_ptr=0x004EC7B0 指向 `VoiceInit`；compile=0x0045F450，handler=0x0047A7F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_303` | `VoiceChrSet` | `kind=0x08, opcode_or_type=0x012F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=15..15; push=0; argc_seen=0x0F:19 | EXE 命令表项 0x004EB0F4=0x4E7CE0+303*44；name_ptr=0x004EC7A4 指向 `VoiceChrSet`；compile=0x0045F450，handler=0x0047A8B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 19 次。 |
| `CMD_304` | `VoiceChrGet` | `kind=0x08, opcode_or_type=0x0130` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:10 | EXE 命令表项 0x004EB120=0x4E7CE0+304*44；name_ptr=0x004EC798 指向 `VoiceChrGet`；compile=0x0045F450，handler=0x0047AAA0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10 次。 |
| `CMD_305` | `SysVoice` | `kind=0x08, opcode_or_type=0x0131` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..6; push=0; argc_seen=0x01:1, 0x04:11, 0x06:40 | EXE 命令表项 0x004EB14C=0x4E7CE0+305*44；name_ptr=0x004EC78C 指向 `SysVoice`；compile=0x0045F450，handler=0x0047B070。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 52 次。 |
| `CMD_306` | `ShellExit` | `kind=0x08, opcode_or_type=0x0132` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EB178=0x4E7CE0+306*44；name_ptr=0x004EC780 指向 `ShellExit`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_307` | `ExtGame` | `kind=0x08, opcode_or_type=0x0133` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=7..7; push=0; argc_seen=未见 | EXE 命令表项 0x004EB1A4=0x4E7CE0+307*44；name_ptr=0x004EC778 指向 `ExtGame`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_308` | `GameEnd` | `kind=0x08, opcode_or_type=0x0134` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EB1D0=0x4E7CE0+308*44；name_ptr=0x004EC770 指向 `GameEnd`；compile=0x0045F450，handler=0x0047B250。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_309` | `MsgBox` | `kind=0x08, opcode_or_type=0x0135` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EB1FC=0x4E7CE0+309*44；name_ptr=0x004EC768 指向 `MsgBox`；compile=0x0045F450，handler=0x0047B370。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_310` | `WinMenu` | `kind=0x08, opcode_or_type=0x0136` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..9; push=0; argc_seen=未见 | EXE 命令表项 0x004EB228=0x4E7CE0+310*44；name_ptr=0x004EC760 指向 `WinMenu`；compile=0x0045F450，handler=0x0047B590。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_311` | `CfgCtrl` | `kind=0x08, opcode_or_type=0x0137` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..999; push=0; argc_seen=0x01:1, 0x03:11, 0x0A:1 | EXE 命令表项 0x004EB254=0x4E7CE0+311*44；name_ptr=0x004EC758 指向 `CfgCtrl`；compile=0x0045F450，handler=0x0047B690。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 13 次。 |
| `CMD_312` | `ExCmdCtrl` | `kind=0x08, opcode_or_type=0x0138` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=0x02:1, 0x05:100, 0x0E:4, 0x11:20 | EXE 命令表项 0x004EB280=0x4E7CE0+312*44；name_ptr=0x004EC74C 指向 `ExCmdCtrl`；compile=0x0045F450，handler=0x0047D0D0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 125 次。 |
| `CMD_313` | `SysReset` | `kind=0x08, opcode_or_type=0x0139` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004EB2AC=0x4E7CE0+313*44；name_ptr=0x004EC740 指向 `SysReset`；compile=0x0045F450，handler=0x0047B240。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_314` | `RegSet` | `kind=0x08, opcode_or_type=0x013A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..999; push=0; argc_seen=未见 | EXE 命令表项 0x004EB2D8=0x4E7CE0+314*44；name_ptr=0x004EC738 指向 `RegSet`；compile=0x0045F450，handler=0x0047D430。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_315` | `RegSetStr` | `kind=0x08, opcode_or_type=0x013B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EB304=0x4E7CE0+315*44；name_ptr=0x004EC72C 指向 `RegSetStr`；compile=0x0045F450，handler=0x0047D480。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_316` | `RegSetNum` | `kind=0x08, opcode_or_type=0x013C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=7..7; push=0; argc_seen=0x07:66 | EXE 命令表项 0x004EB330=0x4E7CE0+316*44；name_ptr=0x004EC720 指向 `RegSetNum`；compile=0x0045F450，handler=0x0047D520。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 66 次。 |
| `CMD_317` | `RegLoad` | `kind=0x08, opcode_or_type=0x013D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EB35C=0x4E7CE0+317*44；name_ptr=0x004EC718 指向 `RegLoad`；compile=0x0045F450，handler=0x0047D850。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_318` | `RegPrintf` | `kind=0x08, opcode_or_type=0x013E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..19; push=0; argc_seen=0x03:356, 0x04:119, 0x05:231, 0x06:21, 0x09:33, 0x0B:99 | EXE 命令表项 0x004EB388=0x4E7CE0+318*44；name_ptr=0x004EC70C 指向 `RegPrintf`；compile=0x0045F450，handler=0x0047D8B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 859 次。 |
| `CMD_319` | `RegCopy` | `kind=0x08, opcode_or_type=0x013F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:2 | EXE 命令表项 0x004EB3B4=0x4E7CE0+319*44；name_ptr=0x004EC704 指向 `RegCopy`；compile=0x0045F450，handler=0x0047DD20。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2 次。 |
| `CMD_320` | `RegFill` | `kind=0x08, opcode_or_type=0x0140` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:11 | EXE 命令表项 0x004EB3E0=0x4E7CE0+320*44；name_ptr=0x004EC6FC 指向 `RegFill`；compile=0x0045F450，handler=0x0047DD80。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 11 次。 |
| `CMD_321` | `RegStrLen` | `kind=0x08, opcode_or_type=0x0141` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:6 | EXE 命令表项 0x004EB40C=0x4E7CE0+321*44；name_ptr=0x004EC6F0 指向 `RegStrLen`；compile=0x0045F450，handler=0x0047DDD0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 6 次。 |
| `CMD_322` | `RegStrCut` | `kind=0x08, opcode_or_type=0x0142` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=0x06:66 | EXE 命令表项 0x004EB438=0x4E7CE0+322*44；name_ptr=0x004EC6E4 指向 `RegStrCut`；compile=0x0045F450，handler=0x0047DE30。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 66 次。 |
| `CMD_323` | `RegCalc` | `kind=0x08, opcode_or_type=0x0143` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:4 | EXE 命令表项 0x004EB464=0x4E7CE0+323*44；name_ptr=0x004EC6DC 指向 `RegCalc`；compile=0x0045F450，handler=0x0047E240。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 4 次。 |
| `CMD_324` | `IFNameSet` | `kind=0x08, opcode_or_type=0x0144` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=5..5; push=0; argc_seen=0x05:3 | EXE 命令表项 0x004EB490=0x4E7CE0+324*44；name_ptr=0x004EC6D0 指向 `IFNameSet`；compile=0x0045F450，handler=0x0047F020。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 3 次。 |
| `CMD_325` | `IFileChk` | `kind=0x08, opcode_or_type=0x0145` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:44 | EXE 命令表项 0x004EB4BC=0x4E7CE0+325*44；name_ptr=0x004EC6C4 指向 `IFileChk`；compile=0x0045F450，handler=0x0047F1B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 44 次。 |
| `CMD_326` | `ISaveGbl` | `kind=0x08, opcode_or_type=0x0146` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004EB4E8=0x4E7CE0+326*44；name_ptr=0x004EC6B8 指向 `ISaveGbl`；compile=0x0045F450，handler=0x0047F5F0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_327` | `IGblFlgGet` | `kind=0x08, opcode_or_type=0x0147` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:33 | EXE 命令表项 0x004EB514=0x4E7CE0+327*44；name_ptr=0x004EC6AC 指向 `IGblFlgGet`；compile=0x0045F450，handler=0x0047F700。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 33 次。 |
| `CMD_328` | `IGblFlgSet` | `kind=0x08, opcode_or_type=0x0148` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..2; push=0; argc_seen=0x01:350, 0x02:65 | EXE 命令表项 0x004EB540=0x4E7CE0+328*44；name_ptr=0x004EC6A0 指向 `IGblFlgSet`；compile=0x0045F450，handler=0x0047F7B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 415 次。 |
| `CMD_329` | `IGblFlgMsk` | `kind=0x08, opcode_or_type=0x0149` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:10 | EXE 命令表项 0x004EB56C=0x4E7CE0+329*44；name_ptr=0x004EC694 指向 `IGblFlgMsk`；compile=0x0045F450，handler=0x0047F870。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10 次。 |
| `CMD_330` | `IGblValGet` | `kind=0x08, opcode_or_type=0x014A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:815 | EXE 命令表项 0x004EB598=0x4E7CE0+330*44；name_ptr=0x004EC688 指向 `IGblValGet`；compile=0x0045F450，handler=0x0047F8C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 815 次。 |
| `CMD_331` | `IGblValSet` | `kind=0x08, opcode_or_type=0x014B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:50 | EXE 命令表项 0x004EB5C4=0x4E7CE0+331*44；name_ptr=0x004EC67C 指向 `IGblValSet`；compile=0x0045F450，handler=0x0047F970。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 50 次。 |
| `CMD_332` | `ILoadGbl` | `kind=0x08, opcode_or_type=0x014C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:1 | EXE 命令表项 0x004EB5F0=0x4E7CE0+332*44；name_ptr=0x004EC670 指向 `ILoadGbl`；compile=0x0045F450，handler=0x0047FE70。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_333` | `ISave` | `kind=0x08, opcode_or_type=0x014D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:22 | EXE 命令表项 0x004EB61C=0x4E7CE0+333*44；name_ptr=0x004EC668 指向 `ISave`；compile=0x0045F450，handler=0x0047F9F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 22 次。 |
| `CMD_334` | `ILoad` | `kind=0x08, opcode_or_type=0x014E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EB648=0x4E7CE0+334*44；name_ptr=0x004EC660 指向 `ILoad`；compile=0x0045F450，handler=0x004815E0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_335` | `ILoadCall` | `kind=0x08, opcode_or_type=0x014F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..3; push=0; argc_seen=0x03:22 | EXE 命令表项 0x004EB674=0x4E7CE0+335*44；name_ptr=0x004EC654 指向 `ILoadCall`；compile=0x0045F450，handler=0x00481640。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 22 次。 |
| `CMD_336` | `ILoadReg` | `kind=0x08, opcode_or_type=0x0150` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=0x04:33 | EXE 命令表项 0x004EB6A0=0x4E7CE0+336*44；name_ptr=0x004EC648 指向 `ILoadReg`；compile=0x0045F450，handler=0x00481730。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 33 次。 |
| `CMD_337` | `IHistReset` | `kind=0x08, opcode_or_type=0x0151` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:2 | EXE 命令表项 0x004EB6CC=0x4E7CE0+337*44；name_ptr=0x004EC63C 指向 `IHistReset`；compile=0x0045F450，handler=0x004818F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 2 次。 |
| `CMD_338` | `IHistCntGet` | `kind=0x08, opcode_or_type=0x0152` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:11 | EXE 命令表项 0x004EB6F8=0x4E7CE0+338*44；name_ptr=0x004EC630 指向 `IHistCntGet`；compile=0x0045F450，handler=0x00481920。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 11 次。 |
| `CMD_339` | `IHistGet` | `kind=0x08, opcode_or_type=0x0153` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:55 | EXE 命令表项 0x004EB724=0x4E7CE0+339*44；name_ptr=0x004EC624 指向 `IHistGet`；compile=0x0045F450，handler=0x00481970。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 55 次。 |
| `CMD_340` | `IPageInit` | `kind=0x08, opcode_or_type=0x0154` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:1 | EXE 命令表项 0x004EB750=0x4E7CE0+340*44；name_ptr=0x004EC618 指向 `IPageInit`；compile=0x0045F450，handler=0x00480170。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_341` | `IPageReset` | `kind=0x08, opcode_or_type=0x0155` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:1 | EXE 命令表项 0x004EB77C=0x4E7CE0+341*44；name_ptr=0x004EC60C 指向 `IPageReset`；compile=0x0045F450，handler=0x004801A0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1 次。 |
| `CMD_342` | `IMsgDisp` | `kind=0x08, opcode_or_type=0x0156` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..4; push=0; argc_seen=0x04:33 | EXE 命令表项 0x004EB7A8=0x4E7CE0+342*44；name_ptr=0x004EC600 指向 `IMsgDisp`；compile=0x0045F450，handler=0x00481A10。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 33 次。 |
| `CMD_343` | `IFileSet` | `kind=0x08, opcode_or_type=0x0157` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..14; push=0; argc_seen=0x0E:768 | EXE 命令表项 0x004EB7D4=0x4E7CE0+343*44；name_ptr=0x004EC5F4 指向 `IFileSet`；compile=0x0045F450，handler=0x00481CC0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 768 次。 |
| `CMD_344` | `IFileNum` | `kind=0x08, opcode_or_type=0x0158` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..10; push=0; argc_seen=0x0A:58 | EXE 命令表项 0x004EB800=0x4E7CE0+344*44；name_ptr=0x004EC5E8 指向 `IFileNum`；compile=0x0045F450，handler=0x00481F60。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 58 次。 |
| `CMD_345` | `IFileVal` | `kind=0x08, opcode_or_type=0x0159` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..11; push=0; argc_seen=0x0B:417 | EXE 命令表项 0x004EB82C=0x4E7CE0+345*44；name_ptr=0x004EC5DC 指向 `IFileVal`；compile=0x0045F450，handler=0x00482010。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 417 次。 |
| `CMD_346` | `IFileStr` | `kind=0x08, opcode_or_type=0x015A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..12; push=0; argc_seen=0x0C:127 | EXE 命令表项 0x004EB858=0x4E7CE0+346*44；name_ptr=0x004EC5D0 指向 `IFileStr`；compile=0x0045F450，handler=0x00482100。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 127 次。 |
| `CMD_347` | `IFileReset` | `kind=0x08, opcode_or_type=0x015B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EB884=0x4E7CE0+347*44；name_ptr=0x004EC5C4 指向 `IFileReset`；compile=0x0045F450，handler=0x00482230。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_348` | `ISurfSet` | `kind=0x08, opcode_or_type=0x015C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:46 | EXE 命令表项 0x004EB8B0=0x4E7CE0+348*44；name_ptr=0x004EC5B8 指向 `ISurfSet`；compile=0x0045F450，handler=0x004822C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 46 次。 |
| `CMD_349` | `ISSurfSet` | `kind=0x08, opcode_or_type=0x015D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:4 | EXE 命令表项 0x004EB8DC=0x4E7CE0+349*44；name_ptr=0x004EC5AC 指向 `ISSurfSet`；compile=0x0045F450，handler=0x00482320。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 4 次。 |
| `CMD_350` | `ISurfReset` | `kind=0x08, opcode_or_type=0x015E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EB908=0x4E7CE0+350*44；name_ptr=0x004EC5A0 指向 `ISurfReset`；compile=0x0045F450，handler=0x00482380。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_351` | `ISSurfReset` | `kind=0x08, opcode_or_type=0x015F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EB934=0x4E7CE0+351*44；name_ptr=0x004EC594 指向 `ISSurfReset`；compile=0x0045F450，handler=0x00482420。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_352` | `IChrSet` | `kind=0x08, opcode_or_type=0x0160` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:82 | EXE 命令表项 0x004EB960=0x4E7CE0+352*44；name_ptr=0x004EC58C 指向 `IChrSet`；compile=0x0045F450，handler=0x00482490。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 82 次。 |
| `CMD_353` | `ISChrSet` | `kind=0x08, opcode_or_type=0x0161` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EB98C=0x4E7CE0+353*44；name_ptr=0x004EC580 指向 `ISChrSet`；compile=0x0045F450，handler=0x004824F0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_354` | `IChrReset` | `kind=0x08, opcode_or_type=0x0162` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EB9B8=0x4E7CE0+354*44；name_ptr=0x004EC574 指向 `IChrReset`；compile=0x0045F450，handler=0x00482550。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_355` | `ISChrReset` | `kind=0x08, opcode_or_type=0x0163` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EB9E4=0x4E7CE0+355*44；name_ptr=0x004EC568 指向 `ISChrReset`；compile=0x0045F450，handler=0x004825C0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_356` | `IHistRewind` | `kind=0x08, opcode_or_type=0x0164` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EBA10=0x4E7CE0+356*44；name_ptr=0x004EC55C 指向 `IHistRewind`；compile=0x0045F450，handler=0x00482630。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_357` | `IHistChk` | `kind=0x08, opcode_or_type=0x0165` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:52 | EXE 命令表项 0x004EBA3C=0x4E7CE0+357*44；name_ptr=0x004EC550 指向 `IHistChk`；compile=0x0045F450，handler=0x004827B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 52 次。 |
| `CMD_358` | `IPageStart` | `kind=0x08, opcode_or_type=0x0166` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010101; unk10=20/0x00000014; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..2; push=0; argc_seen=0x01:9969, 0x02:425 | EXE 命令表项 0x004EBA68=0x4E7CE0+358*44；name_ptr=0x004EC544 指向 `IPageStart`；compile=0x0045F450，handler=0x00482800。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10394 次。 |
| `CMD_359` | `IPageEnd` | `kind=0x08, opcode_or_type=0x0167` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..2; push=0; argc_seen=0x02:10394 | EXE 命令表项 0x004EBA94=0x4E7CE0+359*44；name_ptr=0x004EC538 指向 `IPageEnd`；compile=0x0045F450，handler=0x00483500。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10394 次。 |
| `CMD_360` | `ISelStart` | `kind=0x08, opcode_or_type=0x0168` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:353 | EXE 命令表项 0x004EBAC0=0x4E7CE0+360*44；name_ptr=0x004EC52C 指向 `ISelStart`；compile=0x0045F450，handler=0x00484050。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 353 次。 |
| `CMD_361` | `ISelEnd` | `kind=0x08, opcode_or_type=0x0169` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:353 | EXE 命令表项 0x004EBAEC=0x4E7CE0+361*44；name_ptr=0x004EC524 指向 `ISelEnd`；compile=0x0045F450，handler=0x00484230。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 353 次。 |
| `CMD_362` | `ISelect` | `kind=0x08, opcode_or_type=0x016A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:129 | EXE 命令表项 0x004EBB18=0x4E7CE0+362*44；name_ptr=0x004EC51C 指向 `ISelect`；compile=0x0045F450，handler=0x004843F0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 129 次。 |
| `CMD_363` | `ISelSet` | `kind=0x08, opcode_or_type=0x016B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:129 | EXE 命令表项 0x004EBB44=0x4E7CE0+363*44；name_ptr=0x004EC514 指向 `ISelSet`；compile=0x0045F450，handler=0x00484510。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 129 次。 |
| `CMD_364` | `IGblSelSet` | `kind=0x08, opcode_or_type=0x016C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:510 | EXE 命令表项 0x004EBB70=0x4E7CE0+364*44；name_ptr=0x004EC508 指向 `IGblSelSet`；compile=0x0045F450，handler=0x00484640。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 510 次。 |
| `CMD_365` | `IGblSelDel` | `kind=0x08, opcode_or_type=0x016D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EBB9C=0x4E7CE0+365*44；name_ptr=0x004EC4FC 指向 `IGblSelDel`；compile=0x0045F450，handler=0x004847E0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_366` | `IGblPageMode` | `kind=0x08, opcode_or_type=0x016E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:22 | EXE 命令表项 0x004EBBC8=0x4E7CE0+366*44；name_ptr=0x004EC4EC 指向 `IGblPageMode`；compile=0x0045F450，handler=0x00484860。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 22 次。 |
| `CMD_367` | `ISkipGet` | `kind=0x08, opcode_or_type=0x016F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:938 | EXE 命令表项 0x004EBBF4=0x4E7CE0+367*44；name_ptr=0x004EC4E0 指向 `ISkipGet`；compile=0x0045F450，handler=0x004848A0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 938 次。 |
| `CMD_368` | `ISkipSet` | `kind=0x08, opcode_or_type=0x0170` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:986 | EXE 命令表项 0x004EBC20=0x4E7CE0+368*44；name_ptr=0x004EC4D4 指向 `ISkipSet`；compile=0x0045F450，handler=0x00484940。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 986 次。 |
| `CMD_369` | `IMPlay` | `kind=0x08, opcode_or_type=0x0171` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=未见 | EXE 命令表项 0x004EBC4C=0x4E7CE0+369*44；name_ptr=0x004EC4CC 指向 `IMPlay`；compile=0x0045F450，handler=0x00484F00。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_370` | `IMStop` | `kind=0x08, opcode_or_type=0x0172` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:20 | EXE 命令表项 0x004EBC78=0x4E7CE0+370*44；name_ptr=0x004EC4C4 指向 `IMStop`；compile=0x0045F450，handler=0x00484FA0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 20 次。 |
| `CMD_371` | `IQSaveCtrl` | `kind=0x08, opcode_or_type=0x0173` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..3; push=0; argc_seen=0x01:13, 0x02:22, 0x03:1 | EXE 命令表项 0x004EBCA4=0x4E7CE0+371*44；name_ptr=0x004EC4B8 指向 `IQSaveCtrl`；compile=0x0045F450，handler=0x004852C0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 36 次。 |
| `CMD_372` | `IChgSaveReg` | `kind=0x08, opcode_or_type=0x0174` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004EBCD0=0x4E7CE0+372*44；name_ptr=0x004EC4AC 指向 `IChgSaveReg`；compile=0x0045F450，handler=0x004855D0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_373` | `HistoryCtrl` | `kind=0x08, opcode_or_type=0x0175` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=0x02:66, 0x09:110 | EXE 命令表项 0x004EBCFC=0x4E7CE0+373*44；name_ptr=0x004EC4A0 指向 `HistoryCtrl`；compile=0x0045F450，handler=0x00485630。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 176 次。 |
| `CMD_374` | `MenuInit` | `kind=0x08, opcode_or_type=0x0176` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=未见 | EXE 命令表项 0x004EBD28=0x4E7CE0+374*44；name_ptr=0x004EC494 指向 `MenuInit`；compile=0x0045F450，handler=0x004869D0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_375` | `SetMenu` | `kind=0x08, opcode_or_type=0x0177` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EBD54=0x4E7CE0+375*44；name_ptr=0x004EC48C 指向 `SetMenu`；compile=0x0045F450，handler=0x00486A20。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_376` | `Menu` | `kind=0x08, opcode_or_type=0x0178` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..5; push=0; argc_seen=未见 | EXE 命令表项 0x004EBD80=0x4E7CE0+376*44；name_ptr=0x004EC484 指向 `Menu`；compile=0x0045F450，handler=0x00486AA0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_377` | `MenuCSet` | `kind=0x08, opcode_or_type=0x0179` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EBDAC=0x4E7CE0+377*44；name_ptr=0x004EC478 指向 `MenuCSet`；compile=0x0045F450，handler=0x00487300。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_378` | `MenuCReset` | `kind=0x08, opcode_or_type=0x017A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EBDD8=0x4E7CE0+378*44；name_ptr=0x004EC46C 指向 `MenuCReset`；compile=0x0045F450，handler=0x00487370。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_379` | `MenuStsGet` | `kind=0x08, opcode_or_type=0x017B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004EBE04=0x4E7CE0+379*44；name_ptr=0x004EC460 指向 `MenuStsGet`；compile=0x0045F450，handler=0x00487400。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_380` | `MenuStsSet` | `kind=0x08, opcode_or_type=0x017C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EBE30=0x4E7CE0+380*44；name_ptr=0x004EC454 指向 `MenuStsSet`；compile=0x0045F450，handler=0x00487460。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_381` | `SetMenuRect` | `kind=0x08, opcode_or_type=0x017D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=未见 | EXE 命令表项 0x004EBE5C=0x4E7CE0+381*44；name_ptr=0x004EC448 指向 `SetMenuRect`；compile=0x0045F450，handler=0x00487500。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_382` | `SMenuInit` | `kind=0x08, opcode_or_type=0x017E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=0..0; push=0; argc_seen=0x00:66 | EXE 命令表项 0x004EBE88=0x4E7CE0+382*44；name_ptr=0x004EC43C 指向 `SMenuInit`；compile=0x0045F450，handler=0x00487590。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 66 次。 |
| `CMD_383` | `SetSMenu` | `kind=0x08, opcode_or_type=0x017F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:99 | EXE 命令表项 0x004EBEB4=0x4E7CE0+383*44；name_ptr=0x004EC430 指向 `SetSMenu`；compile=0x0045F450，handler=0x004875E0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 99 次。 |
| `CMD_384` | `SMenu` | `kind=0x08, opcode_or_type=0x0180` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..5; push=0; argc_seen=0x05:33 | EXE 命令表项 0x004EBEE0=0x4E7CE0+384*44；name_ptr=0x004EC428 指向 `SMenu`；compile=0x0045F450，handler=0x00487660。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 33 次。 |
| `CMD_385` | `SMenuCSet` | `kind=0x08, opcode_or_type=0x0181` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:231 | EXE 命令表项 0x004EBF0C=0x4E7CE0+385*44；name_ptr=0x004EC41C 指向 `SMenuCSet`；compile=0x0045F450，handler=0x00487C30。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 231 次。 |
| `CMD_386` | `SMenuCReset` | `kind=0x08, opcode_or_type=0x0182` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:66 | EXE 命令表项 0x004EBF38=0x4E7CE0+386*44；name_ptr=0x004EC410 指向 `SMenuCReset`；compile=0x0045F450，handler=0x00487CA0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 66 次。 |
| `CMD_387` | `SMenuStsGet` | `kind=0x08, opcode_or_type=0x0183` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=未见 | EXE 命令表项 0x004EBF64=0x4E7CE0+387*44；name_ptr=0x004EC404 指向 `SMenuStsGet`；compile=0x0045F450，handler=0x00487D30。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_388` | `SMenuStsSet` | `kind=0x08, opcode_or_type=0x0184` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=0x03:330 | EXE 命令表项 0x004EBF90=0x4E7CE0+388*44；name_ptr=0x004EC3F8 指向 `SMenuStsSet`；compile=0x0045F450，handler=0x00487D90。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 330 次。 |
| `CMD_389` | `SEKWaitSet` | `kind=0x08, opcode_or_type=0x0185` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..6; push=0; argc_seen=0x06:10 | EXE 命令表项 0x004EBFBC=0x4E7CE0+389*44；name_ptr=0x004EC3EC 指向 `SEKWaitSet`；compile=0x0045F450，handler=0x00487E30。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 10 次。 |
| `CMD_390` | `ChipMode` | `kind=0x08, opcode_or_type=0x0186` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=0x01:156 | EXE 命令表项 0x004EBFE8=0x4E7CE0+390*44；name_ptr=0x004EC3E0 指向 `ChipMode`；compile=0x0045F450，handler=0x00488050。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 156 次。 |
| `CMD_391` | `ChipAlloc` | `kind=0x08, opcode_or_type=0x0187` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..7; push=0; argc_seen=未见 | EXE 命令表项 0x004EC014=0x4E7CE0+391*44；name_ptr=0x004EC3D4 指向 `ChipAlloc`；compile=0x0045F450，handler=0x004880A0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_392` | `ChipPrm` | `kind=0x08, opcode_or_type=0x0188` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=未见 | EXE 命令表项 0x004EC040=0x4E7CE0+392*44；name_ptr=0x004EC3CC 指向 `ChipPrm`；compile=0x0045F450，handler=0x00488580。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_393` | `ChipDel` | `kind=0x08, opcode_or_type=0x0189` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EC06C=0x4E7CE0+393*44；name_ptr=0x004EC3C4 指向 `ChipDel`；compile=0x0045F450，handler=0x004887D0。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_394` | `ChipUpdate` | `kind=0x08, opcode_or_type=0x018A` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..1; push=0; argc_seen=未见 | EXE 命令表项 0x004EC098=0x4E7CE0+394*44；name_ptr=0x004EC3B8 指向 `ChipUpdate`；compile=0x0045F450，handler=0x00488810。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_395` | `MWBTSet` | `kind=0x08, opcode_or_type=0x018B` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..4; push=0; argc_seen=0x04:462 | EXE 命令表项 0x004EC0C4=0x4E7CE0+395*44；name_ptr=0x004EC3B0 指向 `MWBTSet`；compile=0x0045F450，handler=0x00488860。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 462 次。 |
| `CMD_396` | `MWBTUpdate` | `kind=0x08, opcode_or_type=0x018C` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=6..6; push=0; argc_seen=0x06:11 | EXE 命令表项 0x004EC0F0=0x4E7CE0+396*44；name_ptr=0x004EC3A4 指向 `MWBTUpdate`；compile=0x0045F450，handler=0x004888E0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 11 次。 |
| `CMD_397` | `ScrSnap` | `kind=0x08, opcode_or_type=0x018D` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..4; push=0; argc_seen=0x02:66, 0x04:53 | EXE 命令表项 0x004EC11C=0x4E7CE0+397*44；name_ptr=0x004EC39C 指向 `ScrSnap`；compile=0x0045F450，handler=0x00488EB0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 119 次。 |
| `CMD_398` | `ColFilter` | `kind=0x08, opcode_or_type=0x018E` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=4..16; push=0; argc_seen=0x10:43 | EXE 命令表项 0x004EC148=0x4E7CE0+398*44；name_ptr=0x004EC390 指向 `ColFilter`；compile=0x0045F450，handler=0x00489570。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 43 次。 |
| `CMD_399` | `BlurFilter` | `kind=0x08, opcode_or_type=0x018F` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..16; push=0; argc_seen=未见 | EXE 命令表项 0x004EC174=0x4E7CE0+399*44；name_ptr=0x004EC384 指向 `BlurFilter`；compile=0x0045F450，handler=0x0048A470。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_400` | `GetAppInfo` | `kind=0x08, opcode_or_type=0x0190` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..3; push=0; argc_seen=未见 | EXE 命令表项 0x004EC1A0=0x4E7CE0+400*44；name_ptr=0x004EC378 指向 `GetAppInfo`；compile=0x0045F450，handler=0x0048A630。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |
| `CMD_401` | `SetAppInfo` | `kind=0x08, opcode_or_type=0x0191` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..2; push=0; argc_seen=0x02:14 | EXE 命令表项 0x004EC1CC=0x4E7CE0+401*44；name_ptr=0x004EC36C 指向 `SetAppInfo`；compile=0x0045F450，handler=0x0048A920。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 14 次。 |
| `CMD_402` | `MenuCtrl` | `kind=0x08, opcode_or_type=0x0192` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..999; push=0; argc_seen=0x05:28, 0x06:20, 0x07:240, 0x08:17, 0x0A:391, 0x0B:33 | EXE 命令表项 0x004EC1F8=0x4E7CE0+402*44；name_ptr=0x004EC360 指向 `MenuCtrl`；compile=0x0045F450，handler=0x0048E090。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 729 次。 |
| `CMD_403` | `IMMCtrl` | `kind=0x08, opcode_or_type=0x0193` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=0x02:7, 0x03:12, 0x04:8, 0x05:2, 0x09:12, 0x10:2 | EXE 命令表项 0x004EC224=0x4E7CE0+403*44；name_ptr=0x004EC358 指向 `IMMCtrl`；compile=0x0045F450，handler=0x004900D0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 43 次。 |
| `CMD_404` | `EvtCtrl` | `kind=0x08, opcode_or_type=0x0194` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..999; push=0; argc_seen=0x01:7, 0x02:7, 0x03:10, 0x04:287, 0x05:5, 0x06:641, 0x07:1, 0x0F:10, 0x10:289 | EXE 命令表项 0x004EC250=0x4E7CE0+404*44；name_ptr=0x004EC350 指向 `EvtCtrl`；compile=0x0045F450，handler=0x00490C50。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 1257 次。 |
| `CMD_405` | `FreeDBCtrl` | `kind=0x08, opcode_or_type=0x0195` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=3..999; push=0; argc_seen=0x03:13, 0x07:147, 0x08:22 | EXE 命令表项 0x004EC27C=0x4E7CE0+405*44；name_ptr=0x004EC344 指向 `FreeDBCtrl`；compile=0x0045F450，handler=0x00495A60。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 182 次。 |
| `CMD_406` | `AutoDBCtrl` | `kind=0x08, opcode_or_type=0x0196` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=2..999; push=0; argc_seen=0x02:1, 0x05:21, 0x06:4, 0x07:112, 0x09:11, 0x0A:57, 0x0B:4 | EXE 命令表项 0x004EC2A8=0x4E7CE0+406*44；name_ptr=0x004EC338 指向 `AutoDBCtrl`；compile=0x0045F450，handler=0x004962B0。456F50 按名称最长匹配返回该 ID，458250 对非负 opcode 按 ID 查表；12 个样本出现 210 次。 |
| `CMD_407` | `SLGPrmCtrl` | `kind=0x08, opcode_or_type=0x0197` | `8 bytes / <u8 kind, u8 argc_or_state, i16 cmd_id, u32 value>` | `argc_or_state:u8; cmd_id:u16; value:u32(cmd/emit private)` | flags=0x00010100; unk10=-1/0xFFFFFFFF; accept=0x00000001; prec=-997/0xFFFFFC1B; left=0; right=1..999; push=0; argc_seen=未见 | EXE 命令表项 0x004EC2D4=0x4E7CE0+407*44；name_ptr=0x004EC32C 指向 `SLGPrmCtrl`；compile=0x0045F450，handler=0x00497970。456F50/458250 的查表路径确认该 ID 合法；当前 12 个样本未出现。 |

命令行的“精确定义”在本阶段只承诺到 EXE 可证明的字段：命令 ID、源脚本名称、表项元数据、编译期查表路径、handler 地址和样本出现情况。具体运行期副作用需要继续逐个追 `handler`，不在本表中猜测。


## 4. 编译器逻辑

### 4.1 行读取与预处理

`456B60.c` 负责跳过空白与注释：

- 支持跳过空格、tab、换行、逗号；
- 支持 `//` 行注释；
- 使用 `IsDBCSLeadByte` 避免把日文双字节字符误判为控制字符。

`456BF0.c` 负责按行取 token，并识别：

- `#include`
- `#incdef`
- `#define`
- `:label`
- `@...`
- `{`

### 4.2 命令与宏匹配

`456F50.c` 在命令表中按当前编译状态掩码匹配命令名，采用最长匹配。

因此命令表字段 `accepted_states/accept` 可确认是编译期命令名匹配 mask：`entry.accept & current_state != 0` 才参与最长匹配。

`457080.c` 先查当前脚本 label 表，再查全局宏/define 哈希表。

### 4.3 字符串与数字

字符串：

- 源脚本字符串使用双引号。
- 编译器按 DBCS 复制字节。
- `\n` 被转换为 `0x0A`。
- 其他反斜杠转义通常直接取反斜杠后的字符。
- 编译成 `kind=0x01, type=0x8002, value=string_pool_offset`。

数字：

- 使用 `strtol(..., base=0)`，支持十进制和 `0x` 十六进制。
- 溢出时会尝试 `strtoul`。
- 编译成 `kind=0x00, type=0x8000, value=number`。

### 4.4 表达式归约

`458250.c` 使用命令表中的优先级/参数计数字段做归约：

- `opcode_or_type < 0` 时多为立即数/字符串/特殊引用，直接入右值栈。
- `opcode_or_type >= 0` 时查命令表。
- 命令表字段控制：
  - 最小/最大右参数；
  - 左参数需求；
  - 运算符优先级；
  - 是否向输出 token 流写入；
  - 是否压回占位 token。

已确认的 `flags` 位用途：

| bit | 精确定义与证据 |
|---:|---|
| `0x00000001` | 只出现在 21 个 `struct_ordinal/unk10 >= 0` 的结构/控制命令上，且该集合与 `unk10` 非负集合完全一致；`458250.c/4587B0.c/45F450.c/45E740.c` 的命令表访问路径均未按该位分支，`4E7CF0/dword_4E7CF0/unk_4E7CF0` 也未在导出反汇编/反编译文本中出现。定义为 `HAS_STRUCT_ORDINAL`：该命令表项带结构序号字段，但该 bit 本身没有独立编译期/运行期副作用。 |
| `0x00000100` | `458250.c` 末尾检查该位；置位时先校验右值栈数量，再把当前命令 token 压入运算符/命令栈，等待后续归约。 |
| `0x00010000` | `458250.c` 归约旧栈顶命令时读取旧命令 flags；置位时把分隔/扫描模式从 `3` 改为 `2`。 |
| `0x00020000` | `458250.c` 输出归约结果前检查该位；置位时不把被归约命令写入输出 token 流。 |
| `0x01000000` | `458250.c` 比较优先级时使用；当新旧命令优先级相等且新命令置位时停止继续归约，表现为结合性/优先级 tie-break 标记。 |
| `0x02000000` | `45F450.c` 和 `4587B0.c` 使用；置位命令在编译期会追加/切换文本控制状态 token，影响反斜杠文本命令解析状态。 |

命令表 `flags` 在 408 项内只出现上述 6 个 bit；其中除 `0x00000001` 外均已找到直接读取点。`0x00000001` 的出现集合已经确认，在已导出的 VM 编译/执行路径中未找到单独消费点，因此实现只需保留该 bit 并按 `HAS_STRUCT_ORDINAL` 展示。

`struct_ordinal/unk10` 不是 opcode；它是 EXE 命令表项 `+0x10` 的独立 32 位字段。非负值只出现在少量结构/控制类命令上：`MsgOut`、括号/分隔符、`@@/@`、`If2/Else/EndIf/Switch/Case/EndSwitch/Continue/Break`、`IPageStart`；其值连续覆盖 `0..20`，其余 387 个命令为 `-1`。`unk10 >= 0` 与 `flags & 0x00000001` 完全等价，已用 `opcodelist.py` 全表验证无 mismatch。`456F50.c` 消费 `+0x00/+0x14`，`4587B0.c` 消费 `+0x04/+0x0C/+0x24`，`458250.c` 消费 `+0x08/+0x0C/+0x18/+0x1C/+0x20/+0x24/+0x28`，`45E740.c` 消费 `+0x08`；这些已定位的命令表路径均不读取 `+0x10`。因此将 `unk10` 定义为表内结构序号字段；反汇编/汇编逐字节保留即可。

这意味着反汇编阶段不能只按 `kind=0x08` 展示命令名，还要保留 `argc_or_state`、`value` 和表达式上下文，否则汇编回去可能改变归约结果。

## 5. PageInfo / IPT 格式

### 5.1 文件结构

`pageinfo.ipt` 固定大小：

```text
576008 = 8 + 0x2EE00 * 3
```

结构：

| 偏移 | 大小 | 字段 | 说明 |
|---:|---:|---|---|
| `0x000000` | 8 | filetime | 生成时的 `FILETIME` |
| `0x000008` | `48000 * 4` | page_script_id | page -> 脚本资源 ID |
| `0x02EE08` | `48000 * 4` | page_token_offset | page -> token 流相对 offset |
| `0x05DC08` | `48000 * 4` | page_flags | page 属性位 |

当前样本统计：

```text
nonzero script_id entries = 10394
nonzero token offsets     = 10394
nonzero flags             = 425
```

样本内 flag 值：

| flag | 数量 | 已确认位段 |
|---:|---:|---|
| `0x00000000` | 47575 | 普通 page 或空项 |
| `0x02000000` | 169 | 最高字节 bit `0x02` |
| `0x00000002` | 127 | bit `0x00000002` |
| `0x01000002` | 98 | bit `0x01000000` + bit `0x00000002` |
| `0x01000000` | 30 | 最高字节 bit `0x01` |
| `0x04020000` | 1 | bit `0x04000000` + bit `0x00020000` |

样本实际只置位 `0x00000002`、`0x00020000`、`0x01000000`、`0x02000000`、`0x04000000`。

如果只统计实际存在的 `IPageStart` 记录，而不把空 page 项计入 `0x00000000`，12 个官方脚本的 flags 分布为：

| flag | `IPageStart` 数量 | 出现脚本 |
|---:|---:|---|
| `0x00000000` | 9969 | 多脚本 |
| `0x02000000` | 169 | `sce01.gkx` |
| `0x00000002` | 127 | `sce01.gkx` |
| `0x01000002` | 98 | `sce01.gkx` |
| `0x01000000` | 30 | `sce01.gkx` |
| `0x04020000` | 1 | `scemain.gkx` |

高字节 flags 与 page id 编号存在稳定样本模式。EXE 运行期只把最高字节当 include/exclude mask 使用；下表是脚本模板角色，用于人工阅读和测试定位，不反推为源码级业务名：

| flag 组 | page id 后两位分布 | 样本模板角色 |
|---:|---|---|
| `0x02000000` | 149 个后两位为 `01`，20 个为 `51` | `sce01` 编号段起点/剧情块入口形态。 |
| `0x01000000` | 30 个全部为 `99` | `sce01` 回答/反应选择提示页形态。 |
| `0x01000002` | 77 个为 `99`，21 个为 `49` | `sce01` 行动选择/接近对象选择页形态，同时带低位边界 bit。 |
| `0x04020000` | 仅 page `199` | `scemain` 地图循环入口形态，同时带 `0x00020000`。 |

脚本邻域统计进一步验证了这些模板角色：

| flag 组 | 邻域统计 | 高置信结论 |
|---:|---|---|
| `0x02000000` | 169/169 后续首个字符串为 `MENU_CHK_START`；前序常见 `MESSAGE_ON`、`SND_BGM_PLAY`、`OVERLAP`、`CHR_SET_ALL` | 样本中稳定对应 `sce01` 剧情/日程块入口模板。 |
| `0x01000000` | 30/30 位于 page 后两位 `99`；后续为 `MENU_CHK_START` + 回答提示 + `S_GET_RSEL_STS` | 样本中稳定对应回答/反应选择提示页模板。 |
| `0x01000002` | 98/98 后续首个字符串为 `MENU_CHK_START`；其中 84 次为行动选择提示，14 次为接近对象选择提示 | 样本中稳定对应行动选择页模板，并同时作为 page 队列扫描边界。 |
| `0x00000002` | 127/127 后续首个字符串为 `MENU_CHK_START`；page 后两位分布分散 | handler 层已定义为 page 队列扫描边界/停止位；脚本中多用于段落或回合中间边界。 |
| `0x04020000` | 1/1 位于 `scemain` page `199`；后续为 `S_SAVE_IN`、`MENU_CHK_START`、`MAP_INFO_START`、`SCEM_LOOP_INPUT`、`SCEM_LOOP_MAP` | 样本中稳定对应地图循环/保存入口模板；实现层仍只按 `0x04000000` 高位 mask 与 `0x00020000` 机械位处理。 |

运行期已定位读取点：

| 位 / 位段 | 读取点 | 当前可证明行为 |
|---:|---|---|
| `0x00000001` | `482800.c`、`483500.c` | 影响 page 进入/结束时的连续 page、历史/队列处理；当前样本未见该位单独置位，但 EXE 明确读取。 |
| `0x00000002` | `482800.c` | 在 page 队列扫描中作为边界/停止条件：循环跳过直到遇到带 `0x00000002` 的 page。12 个样本中 225 个 `IPageStart` 置位。 |
| `0x00010000` | `482800.c` | 与 bit 0 合并成 `flags & 0x10001`；影响 page 入口状态变量。当前样本未见该位。 |
| `0x00020000` | `482800.c`、`484050.c`、`480200.c` | 置位时跳过 `482800.c` 中一段 page 历史/队列维护；`480200.c` 恢复状态时只有当前 page 未置该位才递减 `dword_263AF18`；`484050.c ISelStart` 只有当前 page 未置该位才读取 `dword_1505FA8/FAC` 的 page -> selection bitmask 表。样本只见一次，为 `scemain.gkx` 的 `0x04020000`。 |
| `bits 8..11` | `483500.c` | `page_flags >> 8 & 0xF` 写入当前消息/页面状态字段。当前样本未见非零。 |
| `0xFF000000` | `482800.c`、`481970.c` | 最高字节作为高位属性 mask；`481970.c` 会把历史 page 编码的高 4 bit 映射到返回 flags 的 bits 12..15，并与 `page_flags` 合并返回；`482800.c` 用全局槽 23/24 左移 24 位后做 include/exclude 门控检查。 |

### 5.2 加载与重建逻辑

`47FEE0.c` 先尝试读取 `ScrBin\PageInfo`：

- 文件大小必须为 `576008`；
- 前 8 字节复制到 `FILETIME`；
- 后三段分别复制到全局数组：
  - `byte_14D6F88 + 544`
  - `dword_254B5DC`
  - `dword_258F030`

如果读取失败，则清空三张表，然后遍历脚本列表：

1. 仅处理扩展名为 `gks` 的脚本条目；
2. 调用 `sub_457920(-1, script_name)` 加载或编译对应 GSX1；
3. 扫描 token 流；
4. 遇到 `IPageStart` token 时填入三张表：
   - page id；
   - 脚本资源 ID；
   - token 相对 offset；
   - page flags。

`47FEE0.c` 的实际变量是 `v9 = token_base + 1`，随后判断 `*(__int16 *)(v9 + 1) == dword_8560C8`。换算回文件结构后，读取的是当前 token 的 `+0x02..+0x03`，也就是 `opcode_or_type`，不是另一种跨字段 token 编码。

与 12 个官方 `GSX1` 和 `pageinfo.ipt` 交叉验证后，PageInfo 扫描目标可确认对应 `CMD_358 IPageStart`：

- `IPageStart` 总数 `10394`，与 `PageInfo` 非零 `script_id/token_offset` 数一致；
- `IPageStart` 的 `argc_or_state` 分布为 `1:9969`、`2:425`，与 `PageInfo` 中非零 `page_flags=425` 一致；
- 按下方公式重建出的 page id、token offset 和 flags 与 `pageinfo.ipt` 完全一致。

扫描公式：

| `IPageStart.argc_or_state` | page id 来源 | page flags 来源 | PageInfo token offset |
|---:|---|---|---|
| `1` | 前 1 个 token 的 `value` | `0` | `IPageStart` token offset `- 8` |
| `2` | 前 2 个 token 的 `value` | 前 1 个 token 的 `value` | `IPageStart` token offset `- 16` |

因此 PageInfo 中记录的 offset 指向 `IPageStart` 的第一个参数 token，而不是命令 token 本身。

### 5.3 运行期访问与全局槽 23/24

`47FEB0.c` 是 PageInfo 查询 helper：

```text
sub_47FEB0(script_base, page_id, out_flags)
    if out_flags != NULL:
        *out_flags = dword_258F030[page_id]
    return script_base + dword_254B5DC[page_id]
```

`CMD_339 IHistGet` 的 handler `481970.c` 会从历史 page 队列取一项并写回两个全局槽。参数顺序为：

```text
IHistGet(history_index, page_slot, flags_slot)
```

写回公式：

```text
entry = dword_1674578[history_index]
dword_14CB7C0[page_slot]  = entry & 0x0FFFFFFF
dword_14CB7C0[flags_slot] = ((entry >> 16) & 0xF000) | dword_258F030[entry & 0x0FFFFFFF]
```

因此 `IHistGet` 返回的 flags 不是单纯的 `page_flags`，还包含历史队列 entry 的最高 4 bit 映射到 bits 12..15。

同一组历史命令的运行期行为：

| 命令 | handler | 参数 | 精确定义 |
|---|---:|---|---|
| `IHistReset` | `0x4818F0` | 无 | 若不处于 `off_4E74E4 & 2` 且 `dword_1680288 == dword_192F4A8` 的跳过条件，则清零 `dword_263AF18`，即历史 page 队列计数。 |
| `IHistCntGet` | `0x481920` | `out_slot` | 若不处于同一跳过条件，则写 `dword_14CB7C0[out_slot] = dword_263AF18`。 |
| `IHistChk` | `0x4827B0` | `out_slot` | 若 `off_4E74E4 & 2` 且 `dword_1680288 == dword_192F4A8`，写 `dword_14CB7C0[out_slot] = 1`；否则写 `0`。该 handler 不读取历史队列。 |
| `IHistRewind` | `0x482630` | `page_id` | 设置 `dword_4E74E0 = page_id`，按当前脚本资源名在历史 page 队列中从后向前查找同 page id 且同脚本资源 ID 的 entry；命中后把 `dword_263AF18` 回退到命中索引，并把 `dword_4E74E0` 设为该历史 entry。 |

`482800.c` 的 `IPageStart` 入口还会读取两个固定全局槽：

| 全局槽 | EXE 符号 | 地址 | 机械语义 |
|---:|---|---:|---|
| `23` | `dword_14CB81C` | `0x014CB81C` | `page_flags` 最高字节 include mask。运行期测试 `(slot23 << 24) & page_flags`。 |
| `24` | `dword_14CB820` | `0x014CB820` | `page_flags` 最高字节 exclude mask。运行期测试 `(slot24 << 24) & page_flags`。 |

触发条件为：

```text
slot23 != 0
((slot23 << 24) & current_page_flags) != 0
((slot24 << 24) & current_page_flags) == 0
```

命中后，`482800.c` 查找当前 VM 上下文的 call stack 项，把当前脚本上下文压入栈，调用 `sub_45E980(&page_id, -1, byte_257EDF8, byte_14A5438, 1)` 解析目标 page/script，并把 `dword_14A6F7C` 设置为目标 token 指针，同时设置 `dword_14A55A4` 的 `0x100` 跳转请求位。

`CMD_311 CfgCtrl` 的 handler `47B690.c` 中，子命令 `CfgCtrl(0xFF00, 0x30, value)` 会写：

```text
dword_14CB81C = value
dword_25C4374 = value
```

`dword_25C4374` 是 slot 23 mask 的镜像值；`CMD_371 IQSaveCtrl` 的 `0x90/0x91` 子命令分别把该镜像值读到全局槽、或从参数写回镜像值。`dword_14CB820` 没有独立直接写点；它是 `dword_14CB7C0[24]`，脚本通过普通全局槽赋值读写。`scemain.gkx` 中可见 `slot23 = slot501`、`slot24 = slot502` 以及反向保存，说明 slot 501/502 至少有脚本侧保存/恢复 mask 的用法；但它们不是 PageInfo mask 专用槽，源码侧别名不在二进制中保存。

### 5.4 `EvtCtrl` 子命令 `0x20..0x24`

`CMD_404 EvtCtrl` 的 handler 是 `0x490C50`。入口先用 `sub_45F0A0` 求值第一个参数作为子命令；若 `(off_4E74E4 & 2) != 0 && dword_1680288 == dword_192F4A8`，则在进入 switch 前直接走 default，不执行子命令副作用。

`EvtCtrl` 内部有一张 88-byte 表项数组：

```text
entry_base(index) = byte_2624CD8 + 0x58 * index
entry_count       = dword_14D05E0
```

本节只命名已由 handler 精确消费的机械字段，不给业务名：

| 表项偏移 | EXE 符号 | 当前确认用途 |
|---:|---|---|
| `+0x00` | `byte_2624CD8` | 字符串区域起点；`0x21/0x22` 用 `strcpy` 与 `dword_14CB7C0` 字节区互拷。 |
| `+0x20` | `dword_2624CF8` | 32-bit 字段；`0x21` 读出，`0x22` 写入。 |
| `+0x24` | `dword_2624CFC` | 32-bit 字段；`0x21` 读出，`0x22` 写入。 |
| `+0x28` | `dword_2624D00` | 32-bit 字段；`0x21` 读出，`0x22` 写入。 |
| `+0x2C` | `dword_2624D04` | 32-bit 字段；`0x21` 读出，`0x22` 写入。 |
| `+0x30` | `dword_2624D08` | 32-bit 字段；`0x21` 读出，`0x22` 写入。 |
| `+0x34` | `dword_2624D0C` | 32-bit 字段；`0x21` 读出，`0x22` 写入。 |
| `+0x40` | `dword_2624D18` | 32-bit 字段；`0x21` 读出。 |
| `+0x4C` | `dword_2624D24` | 权重/数值字段；`0x21` 读出并参与三段权重选择，`0x22` 写入。 |
| `+0x50` | `dword_2624D28` | 权重/数值字段；`0x21` 读出并参与三段权重选择，`0x22` 写入。 |
| `+0x54` | `dword_2624D2C` | 权重/数值字段；`0x21` 读出并参与三段权重选择，`0x22` 写入。 |

随机数 helper 已确认：

- `sub_45EA20` (`0x45EA20`) 用 `GetSystemTime` 和 `timeGetTime()` 生成新值并写 `dword_1D687CC`。
- `sub_45EAA0(limit)` (`0x45EAA0`) 以 `dword_1D687CC` 为种子推进一次 PRNG；`limit == 0` 返回 `0`，`0 < limit <= 0x10000` 返回 `HIWORD(new_seed) % limit`，`limit > 0x10000` 用递归补足高 16 bit 后取模。

| 子命令 | 地址 | 参数 | 精确定义 |
|---:|---:|---|---|
| `0x20` | `0x491D08` | `out_seed_slot, reseed_flag` | 若 `reseed_flag != 0`，先调用 `sub_45EA20()` 重置 `dword_1D687CC`；随后若 `out_seed_slot >= 0`，写 `dword_14CB7C0[out_seed_slot] = dword_1D687CC`。官方样本出现 2 次，均为 `EvtCtrl(0x20, 520, 1)`。 |
| `0x21` | `0x491D5C` | `name_out, f20_out, f24_out, f28_out, f2C_out, f30_out, f34_out, f40_out, w0_out, w1_out, w2_out, choice_out, index_slot, seed_slot` | 若 `index_slot >= 0`，取 `index = dword_14CB7C0[index_slot]`；否则 `index = 0`。负 `index` 被钳为 `0`；若 `index >= dword_14D05E0`，最终把 `-1` 写回 `index_slot` 并不读字段。index 有效时，非负输出槽依次接收表项 `+0x00/+0x20/+0x24/+0x28/+0x2C/+0x30/+0x34/+0x40/+0x4C/+0x50/+0x54`。若 `choice_out >= 0`，计算 `total = entry[+0x4C] + entry[+0x50] + entry[+0x54]`；若 `seed_slot >= 0`，临时用 `dword_14CB7C0[seed_slot]` 替换全局随机种子，调用 `sub_45EAA0(total)` 后把推进后的种子写回 `seed_slot`，并恢复原全局种子；随后按 `+0x4C/+0x50/+0x54` 三段权重写 `choice_out = 0/1/2`，没有命中时为 `3`。最后若 `index_slot >= 0`，写回规范化后的 `index` 或 `-1`。官方样本出现 9 次，全部在 `scemain.gkx`。 |
| `0x22` | `0x4920B2` | `index, name_src, f20_src, f24_src, f28_src, f2C_src, f30_src, f34_src_a, f34_src_b, w0_src, w1_src, w2_src` | 若 `0 <= index < dword_14D05E0`，把脚本侧值写入表项。`name_src >= 0` 时从 `&dword_14CB7C0[name_src]` 复制字符串到表项 `+0x00`；其余非负参数作为全局槽索引读取 `dword_14CB7C0[src]` 后写字段。精确写入顺序为 `+0x20/+0x24/+0x28/+0x2C/+0x30/+0x34/+0x34/+0x4C/+0x50/+0x54`，因此 `f34_src_b` 非负时会覆盖 `f34_src_a` 写入的 `+0x34`。12 个官方样本未出现该子命令；此定义来自 handler 反汇编。 |

子命令 `0x23` 和 `0x24` 都在子命令后再读取 5 个参数；这些参数都是 `sub_45F0A0` 返回的运行期值。注意 `sub_45F0A0` 对 `TYPE_8010 / VAL_GLOBAL_SLOT_F32` 返回的是 `dword_14CB7C0[value]` 的当前槽值，不是 token 内的 `value` 槽号本身。

| 子命令 | 地址 | 参数 | 精确定义 |
|---:|---:|---|---|
| `0x23` | `0x492302` | `slot_index, b0_slot, b1_slot, b2_slot, b3_slot` | 若 `slot_index < 0` 则不写。否则读取 `old = dword_14CB7C0[slot_index]`，再按每个 `bN_slot` 是否非负选择性替换 `old` 的第 N 个 byte：`b0` -> bits `0..7`，`b1` -> bits `8..15`，`b2` -> bits `16..23`，`b3` -> bits `24..31`；源值均取 `dword_14CB7C0[bN_slot] & 0xFF`。负的 `bN_slot` 表示该 byte 保留旧值。最后写回 `dword_14CB7C0[slot_index] = new_value`。 |
| `0x24` | `0x4923E8` | `slot_index, b0_slot, b1_slot, b2_slot, b3_slot` | 若 `slot_index < 0` 则不写。否则读取 `src = dword_14CB7C0[slot_index]`，并把四个 byte 按有符号 8-bit 扩展后分别写到非负目标槽：`dword_14CB7C0[b0_slot] = (int8)(src >> 0)`，`b1_slot = (int8)(src >> 8)`，`b2_slot = (int8)(src >> 16)`，`b3_slot = (int8)(src >> 24)`。负的 `bN_slot` 表示跳过该写入。 |

反汇编证据：

- `0x492373..0x4923D9` 对 `0x23` 依次清除/替换 4 个 byte 并写回目标槽；`0x4923BF -> 0x493581` 的负 `b3_slot` 分支仍会写回前三个 byte 已处理后的结果。
- `0x492459..0x4924A6` 对 `0x24` 使用 `movsx` / `sar` 拆出 4 个 signed byte 并写入目标槽。
- `0x45F0F3..0x45F0FD` 证明 `sub_45F0A0` 遇到 `0x8010` 时读取 `dword_14CB7C0[value]`。

12 个官方 `gkx` 样本中，`EvtCtrl(0x23, ...)` 和 `EvtCtrl(0x24, ...)` 各出现 `318` 次，全部为同一 6 参数形态：

```text
EvtCtrl(0x23, VAL_GLOBAL_SLOT_F32[501], 551, 552, 553, 554)
EvtCtrl(0x24, VAL_GLOBAL_SLOT_F32[501], 551, 552, 553, 554)
```

由于第二参数是 `VAL_GLOBAL_SLOT_F32[501]`，handler 实际使用的是 `dword_14CB7C0[501]` 的当前值作为 `slot_index`；`551..554` 是 raw immediate，在该子命令语境下作为全局槽索引使用。因此 `slot501` 在这些调用中是动态槽号来源，而不是被打包/拆包的目标槽本身。结合 `scemain.gkx` 里 `slot501/502` 的保存/恢复片段，可确定它们是脚本侧临时槽，不是 PageInfo mask 专用槽；源码侧别名不在二进制中保存。

### 5.5 全局 bit flag 命令

`CMD_327 IGblFlgGet`、`CMD_328 IGblFlgSet`、`CMD_329 IGblFlgMsk` 操作同一个 bitset：

```text
bitset_base = dword_1D37EBC
word_index = flag_index / 32
bit_mask = 1 << (flag_index % 32)
```

底层 helper 已确认：

| helper | 地址 | 精确定义 |
|---|---:|---|
| `sub_47F6C0` | `0x47F6C0` | 测试 `dword_1D37EBC[flag_index / 32] & (1 << (flag_index % 32))`，返回 `0/1`。 |
| `sub_47F760` | `0x47F760` | 若 bit 尚未置位，则置位并调用 `sub_47F490()`。 |
| `sub_47F820` | `0x47F820` | 若 bit 已置位，则清位并调用 `sub_47F490()`。 |

三个命令 handler 的参数与副作用：

| 命令 | handler | 参数 | 精确定义与证据 |
|---|---:|---|---|
| `IGblFlgGet` | `0x47F700` | `out_slot, flag_index` | 通过 `sub_45F0A0` 读取两个参数；非跳过状态下调用 `sub_47F6C0(flag_index)`，写 `dword_14CB7C0[out_slot] = 0/1`。 |
| `IGblFlgSet` | `0x47F7B0` | `flag_index, value=1` | 第二参数默认值为 `1`；非跳过状态下，`value != 0` 调用 `sub_47F760(flag_index)` 置位，`value == 0` 调用 `sub_47F820(flag_index)` 清位。 |
| `IGblFlgMsk` | `0x47F870` | `flag_index` | 非跳过状态下调用 `sub_47F820(flag_index)` 清位。 |

三者都有同一跳过保护：若 `(off_4E74E4 & 2) != 0 && dword_1680288 == dword_192F4A8`，则参数仍会被求值，但不会写全局槽或修改 `dword_1D37EBC`。

## 6. 跳转与标签化策略

### 6.1 地址空间

反汇编输出中至少需要三个地址空间：

- `str_XXXXXXXX`：字符串池 offset。
- `label_name` 或 `loc_XXXXXXXX`：label 表指向的 token offset。
- `tok_XXXXXXXX`：token 流内未命名目标或 PageInfo 目标。

所有 offset 均以段内相对 offset 表示，不使用加载后的内存指针。

### 6.2 Label 生成

生成规则：

1. label 表中 `name_offset` 可读时，使用原 label 名。
2. 若 label 名为空、重复或不适合作为 asm 符号，生成 `loc_XXXXXXXX`。
3. 如果多个 label 指向同一 token offset，保留全部 label，但主反汇编展示时可选择一个 canonical label。
4. 每个 label 定义前插入空行，符合 `CLAUDE.md` 输出规范。

### 6.3 跳转/调用重定位

以下命令必须优先确认并符号化目标：

- `Jump`
- `ExtJump`
- `ForceJump`
- `Call`
- `ExtCall`
- `CallSub`
- `ExtCallSub`
- `Return`
- `IPageStart`
- `IPageEnd`
- `ISelect`

对于 `value` 字段或字符串参数指向 label 名的形式，应输出符号引用。汇编器重建时重新计算 label 表和 token offset，禁止硬编码旧 offset。

## 7. 未定义 Opcode 的处理流程

遇到未知 token 时必须按以下流程处理：

1. 先检查 `GSX1` header 四段边界是否正确，确认没有把字符串池或 ref 表误当 token 流。
2. 检查 token 流是否按 8 字节对齐；若不对齐，优先怀疑 header 解析或外层解包错误。
3. 对未知 `kind/opcode_or_type`：
   - 回查 `4587B0.c` 中生成该 token 的路径；
   - 回查对应命令表项的 emit 函数；
   - 若是运行期指令，继续追 emit 函数或运行期 handler。
4. 将确认结果补回本文档和 `opcodelist.py`。
5. 重新对 12 个官方 `GSX1` 样本执行全量反汇编，确保无未识别 token。

## 8. 反汇编输出建议

后续 `asm.txt` 应为语义化文本，不出现原始 hex dump。建议格式：

```text
.format "kyoupri-gsx1"
.encoding "cp932"
.version 0x07F20198

.section strings
str_00000000: .string "SCE0001_INIT{{00}}"

.section labels
SCE0001_INIT: .label tok_00000450

.section code
tok_00000450:
    COMMAND IPageStart, page_id, flags
    STRREF str_00001234
    COMMAND MsgOut, 1
```

对于暂未语义化的字段，允许使用结构化伪指令：

```text
.token kind=0, argc=0, type=VAL_GLOBAL_SLOT_F32, value=...
.ref index=..., target=tok_...
```

但是不得使用 `\xNN` 字符串转义，也不得把原始字节整行 dump 到注释中。

## 9. 当前收敛状态

截至本次逆向，面向 `GSX1/IPT` 反汇编、汇编和逐字节回封的格式层面没有剩余阻塞项。以下项目已经收敛为高置信结论：

- `ref_table +0x00` 不是单个 opaque dword，而是 `u16 serial_or_ref_index + u16 ref_kind_flags`；`+0x04` 是 token offset。
- `ref_table.ref_kind_flags` 的取值来源已确认：`1=If2`、`2=Else`、`4=EndIf`、`16=Switch`、`32=Case`、`64=EndSwitch` 的控制流 marker。
- `ref_table` 的加载/保存 fixup 已确认：`457920.c` 加载时把 `+4` token offset 改成 token 指针，保存前减回 token 流基址；label 哈希重建函数不消费 ref 表。
- `ref_table` 的运行期消费已确认：`If2/Else/Switch/Case/Continue/Break` handler 均先读取编译器插入的隐藏 ref_index，按同 serial 和固定 mask 扫描 ref 表并设置跳转目标；`EndIf/EndSwitch` handler 为 no-op marker。
- `0x8010` 是全局 f32 槽引用，访问 `dword_14CB7C0[value]`；`0x8110` 是当前上下文局部 f32 槽引用，访问 `flt_25C45D0[64*dword_970AB8 + value]`。
- `PageInfo` 扫描条件对应当前 token 的 `opcode_or_type` 匹配；实际匹配目标为 `CMD_358 IPageStart`，记录 offset 指向其第一个参数 token。
- `PageInfo.page_flags` 的样本置位集合、主要运行期读取点、`IHistGet` 回传公式已确认。
- `PageInfo.page_flags` 最高字节的运行期门控已确认：`IPageStart` 使用全局槽 23 作为 include mask、全局槽 24 作为 exclude mask；`CfgCtrl(0xFF00, 0x30, value)` 写 slot 23 及其镜像 `dword_25C4374`。
- `EvtCtrl` 子命令 `0x20/0x21/0x22/0x23/0x24` 的机械语义已确认：`0x20` 读出/重置 PRNG seed，`0x21/0x22` 读写 88-byte 表项并解释 `slot500/501..510/520` 的脚本侧暂存用途，`0x23/0x24` 做 byte pack/unpack；官方样本中 `slot501` 在 `0x23/0x24` 调用形态下作为动态槽号来源，`551..554` 作为 byte 临时槽索引。
- 命令表 `accepted_states/accept` 是编译期命令名匹配状态 mask；`flags` 中除 `0x00000001` 外的实际置位 bit 已确认编译期行为；`0x00000001` 定义为 `HAS_STRUCT_ORDINAL`，与 `struct_ordinal/unk10 >= 0` 的集合完全一致；`unk10` 是表内结构序号字段，在已导出的 VM 编译/执行路径中无独立消费点。
- `IGblFlgGet/Set/Msk` 的 bitset 基址、参数顺序、默认参数和 set/clear/test 副作用已由 handler 反汇编确认。

证据边界如下；这些不是 VM 格式的待解问题：

- `0x8010/0x8110` 槽 token 只保存数值索引。`GSX1` 内没有源码变量名表，EXE reader 只按 `dword_14CB7C0[value]` 与 `flt_25C45D0[64*ctx + value]` 访问；因此反汇编输出使用 `G[index]` / `L[index]` 是最终可逆表示。
- `PageInfo.page_flags` 的运行期机械语义已经覆盖实现所需读取点：低位边界/历史队列位、高位 include/exclude mask、`IHistGet` 回传公式。`0x02000000`、`0x01000000`、`0x01000002`、`0x04020000` 的脚本模板角色已由 12 个官方样本统计确认；源码级剧情分类名不保存在二进制中。
- `EvtCtrl` 表项字段按 handler 消费偏移命名为 `+0x20/+0x24/.../+0x54` 和三段权重字段；`slot500/501..510/520/551..554` 的脚本侧暂存用途已按调用形态解释。源码侧业务别名不在表项或 token 中保存。
- `Continue` 在 12 个官方样本中覆盖数为 0，但 EXE handler 已直接反汇编：`0x46B6B0` 读取隐藏 `ref_index`，从 `ref_index - 1` 反向扫描同 `serial_or_ref_index` 且 `ref_kind_flags & 0x0011` 的 `If2/Switch` 起点 marker，命中后写 `dword_14A6F7C` 并设置 `dword_14A55A4` 的 `0x100` 跳转请求位。因此该命令机械语义为高置信；样本缺席只影响覆盖统计。
- 408 项命令的 opcode、名称、表项元数据、编译 handler、运行期 handler 地址、参数数量范围和官方样本 `argc_or_state` 已固化到 `opcodelist.py`。单个游戏命令的画面/声音/系统业务副作用可继续专题注释，但不影响 `GSX1 -> asm -> GSX1` 和 `IPT -> data -> IPT` 的逐字节可逆实现。

## 10. 验收前置条件

在实现反汇编器前，至少应完成：

1. 将本文档已提取的 408 项命令表固化到 `opcodelist.py`，并保留表项元数据。
2. 对 12 个 `GSX1` 样本逐 token 解析，确认没有未识别 `kind/type`。
3. 对字符串池、label 表、ref 表、token 流建立可逆 AST。
4. 对 `pageinfo.ipt` 建立可逆解析与重建。
5. 验证 `GSX1 -> asm -> GSX1` 对全部样本逐字节一致。
6. 验证 `IPT -> asm/data -> IPT` 对 `pageinfo.ipt` 逐字节一致。
