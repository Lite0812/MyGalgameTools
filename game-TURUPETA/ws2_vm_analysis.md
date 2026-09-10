# WS2 脚本加载与容器 VM 分析定义文档

本文档按 `CLAUDE.md` 中“0. 前置 VM 分析与指令集建模”的要求整理，作为后续 `opcodelist.py`、`disassembler.py`、`assembler.py` 处理 `ws2/script.ws2` 的结构依据。

当前分析对象是 EXE 启动时加载的 `ws2/script.ws2` 主脚本包。本文档重点覆盖：

- EXE 如何定位并加载 `ws2/script.ws2`。
- `script.ws2` 的外层压缩头与解压选择逻辑。
- 解压后 WS2 数据尾部索引结构。
- 运行期脚本读取器的基础字段与读取 primitive。
- 当前已确认与未完成的置信边界。

注意：本文档目前是 **WS2 容器层 + 脚本读取器层** 的高置信分析，不是完整 opcode 全表。后续若实现完整 WS2 反汇编/汇编器，仍需继续补齐运行期命令分发器、所有 opcode 长度和跳转语义。

已确认关键反编译入口：

```text
export-for-ai/decompile/4013A0.c   创建主窗口后初始化游戏对象并调用 sub_423440
export-for-ai/decompile/423440.c   包装调用 sub_423060
export-for-ai/decompile/423060.c   启动初始化入口，硬编码加载 "ws2/script.ws2"
export-for-ai/decompile/41D5B0.c   将全局对象 +0x1FD14 作为脚本读取器传入 sub_41CAD0
export-for-ai/decompile/41CAD0.c   打开、读取、解压、解析 WS2 数据
export-for-ai/decompile/417960.c   CreateFileA + GetFileSize
export-for-ai/decompile/417740.c   ReadFile
export-for-ai/decompile/4178E0.c   关闭文件句柄
export-for-ai/decompile/4026E0.c   通用压缩头解析与解压分发
export-for-ai/decompile/4025A0.c   format 0/3：无压缩复制路径
export-for-ai/decompile/401EE0.c   format 1：xg 解压路径，调用 sub_45D9D0
export-for-ai/decompile/402160.c   format 2：xb 解压路径，调用 sub_45E8B0
export-for-ai/decompile/41C8A0.c   解压后 WS2 尾部索引与字符串表解析
export-for-ai/decompile/41BA80.c   按脚本 id 定位首个 offset
export-for-ai/decompile/41BAF0.c   按脚本 id + 子 index 定位 offset
export-for-ai/decompile/41B9B0.c   从当前脚本 cursor 读取 u8
export-for-ai/decompile/41B990.c   从当前脚本 cursor 读取 u16le
export-for-ai/decompile/41B970.c   从当前脚本 cursor 读取 u32le
export-for-ai/decompile/41BE80.c   从当前脚本 cursor 读取长度前缀字符串
```

## 1. VM 类型识别

### 1.1 资产类型与命名

EXE 中 `ws2/script.ws2` 是硬编码启动脚本路径。字符串位于导出字符串表：

```text
export-for-ai/strings.txt:65  0x486c10 | ASCII | ws2/script.ws2
```

唯一直接引用点位于 `sub_423060`：

```c
result = sub_41D5B0((void *)this, "ws2/script.ws2");
if ( result ) {
    ...
}
```

因此 `script.ws2` 不是按场景名动态拼接的普通资源，而是启动时必须成功载入的主 WS2 脚本包。加载失败会导致 `sub_423060` 直接返回 0，后续窗口、系统、输入、脚本状态初始化不会继续执行。

### 1.2 架构判断

WS2 层当前可拆分为三层：

1. **外层压缩容器**：文件开头 `0x20` 字节为通用压缩头，`format_id` 选择解压 handler。
2. **解压后 WS2 数据区**：正文 + 尾部字符串表/索引表/页脚 offset。
3. **运行期脚本读取器**：对象偏移 `global_this + 0x1FD14`，维护解压缓冲区、当前 cursor、当前脚本 id/sub index、索引表和字符串表。

当前可高置信确认：

- 外层文件不是纯脚本文本，也不是直接可解释 bytecode，必须先按压缩头解压。
- 解压后数据通过尾部 offset 找到索引表和字符串表。
- 运行期执行不是从文件偏移执行，而是先通过 `script_id` 或 `script_id + sub_index` 查索引表，将 `cursor = data_base + offset` 后再逐字段读取。
- 基础读取 primitive 已确认为 `u8/u16le/u32le/string`。
- runtime opcode 分发边界已定位到 `sub_42C150`，主执行循环已定位到 `sub_41DF00`。

后续工具应把 `script.ws2` 视为“**压缩容器 + 解压后分段脚本包 + 索引定位的 bytecode 流**”，而不是单个线性脚本文件。

## 2. 启动加载流程

高层调用链：

```text
sub_4013A0(hInstance, nCmdShow)
  -> CreateWindowExA / ShowWindow / UpdateWindow
  -> sub_41D1A0(global_obj, hWnd, hInstance, 800, 600, ...)
  -> sub_423440(global_obj, hWnd, hInstance)
    -> sub_423060(global_obj, hWnd, hInstance, xRight, yBottom, ...)
      -> sub_41D5B0(global_obj, "ws2/script.ws2")
        -> sub_41CAD0(global_obj + 0x1FD14, "ws2/script.ws2")
          -> sub_417960(reader, path)       ; CreateFileA + GetFileSize
          -> sub_401D10(temp_buffer, size)  ; 分配临时输入 buffer
          -> sub_417740(reader, temp, size) ; ReadFile 整个 script.ws2
          -> sub_4026E0(temp, reader+0x24)  ; 按压缩头解压到 reader 数据 buffer
          -> sub_4178E0(reader)             ; 关闭文件句柄
          -> sub_41C8A0(reader)             ; 解析 WS2 索引与字符串表
```

关键代码位置：

- `sub_4013A0` 创建窗口后调用 `sub_423440`。
- `sub_423440` 仅包装参数，转入 `sub_423060`。
- `sub_423060` 第一条业务逻辑即 `sub_41D5B0(this, "ws2/script.ws2")`。
- `sub_41D5B0` 将 `this + 130324` 即 `this + 0x1FD14` 作为读取器对象传入 `sub_41CAD0`。

## 3. 外层压缩容器格式

### 3.1 Compression header

`sub_4026E0` 要求输入大小至少 `0x20` 字节，否则抛出 `no compression header`。随后复制前 `0x20` 字节作为压缩头，并检查第一个 DWORD：

```c
if (input.size < 0x20)
    throw compression_error("no compression header");

memcpy(header, input.data, 0x20);
if (header.format_id >= 4)
    throw invalid_argument("invalid format_id passed to uncompress()");

funcs_40280C[header.format_id](input, output, header.unpacked_size);
```

当前可定义头部：

| 文件偏移 | 大小 | 字段 | 说明 |
|---:|---:|---|---|
| `0x00` | 4 | `format_id` | 解压算法 id，必须 `< 4` |
| `0x04` | 4 | `unpacked_size` | 解压后大小，传给 handler 作输出大小校验 |
| `0x08` | 24 | `reserved_or_codec_state[6]` | 由具体算法使用；当前 loader 只整体传给 handler |
| `0x20` | variable | `payload` | 压缩或未压缩数据 |

### 3.2 `script.ws2` 实测头部

当前样本：

```text
path = ws2/script.ws2
file_size = 429695 bytes
format_id = 2
unpacked_size = 0x001BE038 = 1826872 bytes
```

因此该文件走 `funcs_40280C[2] -> sub_402160`，即 `xb` 解压路径。

### 3.3 解压 handler 表

函数表位于内存导出 `0x4865D4` 附近：

```text
format 0 -> sub_4025A0
format 1 -> sub_401EE0
format 2 -> sub_402160
format 3 -> sub_4026D0 -> sub_4025A0
```

| format_id | handler | 语义 | 证据 |
|---:|---|---|---|
| `0` | `sub_4025A0` | 无压缩复制；要求 `input_size == unpacked_size + 0x20` | `compression error (xn:size)` |
| `1` | `sub_401EE0` | `xg` 解压；调用 `sub_45D9D0` | 错误字符串 `compression error (xg:*)` |
| `2` | `sub_402160` | `xb` 解压；调用 `sub_45E8B0` | 错误字符串 `compression error (xb:*)` |
| `3` | `sub_4026D0` | thunk 到 `sub_4025A0` | `jmp sub_4025A0` |

对 `script.ws2`，反汇编器应：

1. 读取 `0x20` 压缩头。
2. 验证 `format_id == 2` 或至少支持 `format_id < 4`。
3. 使用 `sub_45E8B0` 等价算法解压 payload 到 `unpacked_size` 字节。
4. 后续解析全部针对解压后数据，而非磁盘压缩字节。

汇编器若要求零突变，需要在重建解压后数据后重新应用原 `format_id=2` 压缩算法，并保留/重建 `0x20` 压缩头。当前样本的 `format_id=2` payload 头为 `BZh91AY&`，可用标准 bzip2 解压；对解压后的 `script.ws2` image 使用 bzip2 level 9 重新压缩，payload 与原文件逐字节一致。因此 `script.ws2` 的压缩层零突变可通过“原 0x20 header + bzip2 level 9 payload”实现。

`format_id=1` 路径调用的是 zlib inflate 系列代码，初始化版本字符串为 `1.1.4`，对应 zlib/deflate stream。当前主样本不使用 format 1，因此工具实现上应支持 zlib 解压；若遇到 format 1 文件且需要压缩层逐字节一致，必须使用与 zlib 1.1.4 参数兼容的 deflate 编码器，或在解压后数据未改变时保留原 payload。该限制是实现边界，不影响当前 `script.ws2` 的零突变。

## 4. 解压后 WS2 数据结构

### 4.1 总体布局

`sub_41C8A0` 解析的是解压后 buffer，设：

```text
base = reader.data_ptr
size = reader.data_size
```

文件尾部两个 DWORD 是关键 offset：

| 解压后偏移 | 大小 | 字段 | 说明 |
|---:|---:|---|---|
| `size - 8` | 4 | `string_table_offset` | 字符串表起始 offset，相对 `base` |
| `size - 4` | 4 | `index_table_offset` | 索引表起始 offset，相对 `base` |

解析顺序：

```c
index_ptr = base + u32le(base + size - 4);
index_end = base + size - 8;
while (index_ptr < index_end) {
    parse_index_entry();
}

string_ptr = base + u32le(base + size - 8);
parse_string_table();
```

因此后续工具必须把解压后最后 8 字节视为页脚，而不是脚本指令流。

### 4.2 Index table

索引表从 `index_table_offset` 开始，到 `size - 8` 结束。每个 entry 是变长结构：

```text
u32le script_id
u32le offset_count
u32le offsets[offset_count]
```

解析行为：

1. 读取 `script_id`。
2. 读取 `offset_count`。
3. 读取 `offset_count` 个 `u32le` offset。
4. 将该记录压入 `reader + 0x34` 的索引 vector。
5. `offsets[0]` 作为该 script id 的默认入口 offset。
6. 完整 offsets 表用于 `script_id + sub_index` 定位。

运行期定位函数：

- `sub_41BA80(reader, script_id)`：查找 `script_id`，设置 `cursor = data_base + offsets[0]`，`current_sub_index = 0`。
- `sub_41BAF0(reader, script_id, sub_index)`：查找 `script_id`，若 `sub_index < offset_count`，设置 `cursor = data_base + offsets[sub_index]`。

工具输出建议：

```text
.index script_id, loc_xxxxxxxx, [loc_xxxxxxxx, ...]
```

在 asm 文本中不要直接把 offsets 当文件绝对偏移；它们是 **解压后 buffer 内的 0-based offset**。

### 4.3 String table

字符串表从 `string_table_offset` 开始。`sub_41C8A0` 的读取方式是：

```text
u8 string_count
repeat string_count times:
    ws2_string
```

`ws2_string` 使用 `sub_41BE80` 读取，当前可确认格式：

```text
u16le length
if length > 0:
    bytes[length]
    trailing_nul
else:
    empty string; cursor remains immediately after length field
```

`sub_41BE80` 的关键细节：函数先用 `sub_41B950(*(this+12))` 读取当前 cursor 处的 `u16le length`。`length > 0` 时才执行 `cursor += 2`，随后为目标字符串分配 `length + 1` 空间，用 C 字符串复制，并将 cursor 再推进 `length + 1` 字节。因此非空字符串总长度为 `2 + length + 1`，`length` 不含尾随 NUL。

`length == 0` 时，函数不写回 `reader.cursor`，也不消费尾随 NUL；它只对目标 `std::string` 调用 erase/clear 风格逻辑，使输出字符串为空。由于 `sub_41B950` 本身不推进 cursor，调用者若在字节流中使用空字符串，实际只可安全表达为仅有两字节 length 字段且没有额外 NUL。当前 `script.ws2` 的 string table 实测 `string_count=1`，唯一字符串为 `VERSION_1.0.0`，不存在 `length==0` 表项。

字符串表被插入到 `reader + 0x6C` 的 vector 中；`sub_41C060` 可将该 vector 拷出给 UI/系统对话框（例如 `sub_41F210` 传给 `sub_454770`），当前未发现 runtime opcode 通过整数索引直接引用该表。运行期命令中的文本主要以内联 `WS2_STRING` operand 读取，即 handler 直接调用 `sub_41BE80(reader, target_string)`。

### 4.4 Body / script stream

解压后 body 中的脚本流不是单一起点，而是由 index table 中的 offsets 决定。任意一个 `offsets[i]` 都可能成为 VM cursor 起点。

当前已确认 primitive：

| reader 函数 | 编码 | 行为 |
|---|---|---|
| `sub_41B9B0` | `u8` | 返回 `*cursor`，`cursor += 1` |
| `sub_41B990` | `u16le` | 返回 `cursor[0] + (cursor[1] << 8)`，`cursor += 2` |
| `sub_41B970` | `u32le` | 返回 `u32le(cursor)`，`cursor += 4` |
| `sub_41BE80` | `ws2_string` | 读取 `u16le length` 与 NUL 结尾字符串，见 4.3 |

后续 opcode 分析应以这些 primitive 为基础识别每个命令 handler 的字段边界。

## 5. 脚本读取器上下文结构

以 `reader = global_this + 0x1FD14` 为基址，可建立以下高置信字段：

| reader 偏移 | 大小 | 字段 | 说明 |
|---:|---:|---|---|
| `+0x00` | 4 | `compressed_file_size` | `sub_417960` 写入的磁盘文件大小 |
| `+0x04` | 4 | `file_handle` | `CreateFileA` 返回句柄，读完后关闭 |
| `+0x24` | 4 | `data_ptr` | 解压后 WS2 buffer 指针 |
| `+0x28` | 4 | `data_size` | 解压后大小；`sub_41CAD0` 复制到 `+0x68` |
| `+0x2C` | 4 | `data_capacity` | 解压后 buffer 容量/分配大小 |
| `+0x30` | 4 | `cursor` | 当前脚本读取位置，所有 primitive 自动推进 |
| `+0x34` | vector | `index_entries` | 由 `sub_41C8A0` 清理并填充；元素大小 24 字节 |
| `+0x44` | 4 | `current_script_id` | `sub_41BA80/sub_41BAF0` 设置 |
| `+0x48` | 2 | `current_sub_index` | `sub_41BA80` 置 0，`sub_41BAF0` 写入指定 index |
| `+0x68` | 4 | `active_size` | 先为文件大小，解压后改为 `data_size` |
| `+0x6C` | vector/list | `string_table` | `sub_41C8A0` 从字符串表填充 |

全局对象中对应偏移为 `global_this + 0x1FD14 + reader_offset`。例如 `reader.data_ptr` 在全局对象上是 `global_this + 0x1FD38`。

## 6. 运行期执行与 opcode 现状

### 6.1 已确认执行入口迹象

加载完成后，`sub_423060` 继续初始化窗口、系统资源、输入设备与运行状态。后续多处函数通过 `global_this + 0x1FD14` 读取脚本字段，例如 `sub_42C630`：

```c
*(_BYTE *)(this + 222448) = sub_41B9B0((_DWORD *)(this + 130324));
*(_WORD *)(this + 222446) = sub_41B990((unsigned __int8 **)(this + 130324));
*(_WORD *)(this + 222444) = sub_41B990((unsigned __int8 **)(this + 130324));
```

这说明 WS2 指令或命令 handler 是围绕 reader cursor 顺序读取字段，而不是一次性把指令解析成固定结构。

### 6.2 主 dispatch loop 与 opcode 编码

WS2 主执行循环已定位为 `sub_41DF00`：它在每帧/每次驱动时反复调用全局对象字段 `this + 0x368B4` 保存的当前 handler，直到 handler 返回 `1` 为止。handler 通常在结束前调用 `sub_42C150` 读取下一条 opcode 并更新 `this + 0x368B4`。

```c
while ( (*(this + 55853))(this, hWnd) != 1 )
    ;
```

`sub_42C150` 是 opcode fetch/dispatch 函数：

```text
opcode0 = read_u8()
if opcode0 < 0x80:
    handler = direct_table[opcode0]
else:
    opcode1 = read_u8()
    handler = extended_table[((opcode0 & 0x7F) << 8) | opcode1]
```

因此 WS2 runtime opcode 是 **1 字节主 opcode + 可选 1 字节扩展 opcode** 的混合编码：

- `0x00..0x7F`：单字节 opcode，handler 表基址 `off_49A130`。
- `0x80..0xFF xx`：双字节扩展 opcode，索引为 `((first & 0x7F) << 8) | second`，handler 表基址 `off_49A234`。
- 当前 EXE 中直接 opcode 高置信有效范围为 `0x00..0x44`；`0x40` 与 `0x45..0x48` 为无效/占位项，表项不是可执行 handler。
- 当前 EXE 中扩展表已确认有效项为 `0x80 0x00 .. 0x80 0x03`，分别别名到直接 opcode `0x41..0x44` 的 handler；其后紧邻非代码数据，不能当作无限 handler 表。

`script.ws2` 解压样本验证结果：

```text
unpacked_size = 0x001BE038
string_table_offset = 0x001A5A68
index_table_offset  = 0x001A5A7C
string_count = 1, string[0] = "VERSION_1.0.0"
index_entries = 1019
total indexed offsets = 22903
unique indexed offsets = 22903
indexed entry first opcode distribution: 0x2B = 21884, 0x2A = 1019
```

这说明 index table 的 offsets 主要指向脚本子入口；入口处常见 `0x2A/0x2B` 是子入口序言/状态记录类命令，不代表 VM 只有两条 opcode。完整线性覆盖应从入口按 handler 的跳转/调用语义递归跟踪。

## 7. Opcode 长度与格式表（当前已确认）

本节补齐 `CLAUDE.md` 要求的 Opcode/格式定义表。下表分为三类：

1. **容器/包结构伪指令**：反汇编器必须识别并完整重建的结构单元，不是 VM 运行期 opcode。
2. **reader primitive**：EXE 已确认的基础读取操作，可作为真实 opcode handler 的 operand reader。
3. **runtime opcode**：由 `sub_42C150` 分发到 handler 的 WS2 VM 指令。

### 7.1 容器与包结构格式表

| Opcode | 命名 | byte_pattern | length / format | operand_schema | sub_opcode / variants | 精确定义与证据 |
|---:|---|---|---|---|---|---|
| `CONTAINER` | `WS2_COMPRESSED_FILE` | 文件偏移 `0x00000000` 起始 | `0x20 + payload_size` | `compression_header, payload` | `format_id=0..3` | 磁盘层完整 `script.ws2`；`sub_41CAD0` 先整包 `ReadFile`，再交给 `sub_4026E0` 解压。 |
| `HEADER` | `COMPRESSION_HEADER` | 文件偏移 `0x00000000` | 32 bytes / `<u32 format_id,u32 unpacked_size,u8 codec_header[24]>` | `format_id:u32le, unpacked_size:u32le, codec_header:bytes[24]` | `format_id` 选择解压 handler | `sub_4026E0` 检查输入大小至少 `0x20`，复制前 `0x20` 字节，并要求 `format_id < 4`。 |
| `FORMAT_0` | `XN_COPY` | `format_id == 0` | `0x20 + unpacked_size` | `payload:bytes[unpacked_size]` | 无压缩复制 | `funcs_40280C[0] -> sub_4025A0`；要求 `input_size == unpacked_size + 0x20`，否则 `compression error (xn:size)`。 |
| `FORMAT_1` | `XG_COMPRESSED` | `format_id == 1` | dynamic / `0x20 + compressed_payload` | `payload:xg_stream` | `sub_45D9D0` 解压 | `funcs_40280C[1] -> sub_401EE0`；错误字符串为 `compression error (xg:*)`。 |
| `FORMAT_2` | `XB_COMPRESSED` | `format_id == 2` | dynamic / `0x20 + compressed_payload` | `payload:xb_stream` | `sub_45E8B0` 解压 | 当前 `ws2/script.ws2` 使用该格式：`format_id=2`，`unpacked_size=0x1BE038`；`funcs_40280C[2] -> sub_402160`。 |
| `FORMAT_3` | `XN_COPY_ALIAS` | `format_id == 3` | `0x20 + unpacked_size` | `payload:bytes[unpacked_size]` | thunk 到 `FORMAT_0` | `funcs_40280C[3] -> sub_4026D0 -> sub_4025A0`。 |
| `UNPACKED` | `WS2_UNPACKED_IMAGE` | 解压后 buffer 起点 | `unpacked_size` | `body, string_table, index_table, footer` | 由 footer 两个 offset 切分 | `sub_402160/sub_401EE0/sub_4025A0` 输出到 reader `+0x24/+0x28`，随后 `sub_41C8A0` 解析。 |
| `FOOTER` | `WS2_FOOTER` | 解压后偏移 `size-8` | 8 bytes / `<u32 string_table_offset,u32 index_table_offset>` | `string_table_offset:u32le, index_table_offset:u32le` | offsets 均相对解压后 buffer 起点 | `sub_41C8A0` 从 `base+size-8` 读取字符串表 offset，从 `base+size-4` 读取索引表 offset。 |
| `INDEX_TABLE` | `WS2_INDEX_TABLE` | `index_table_offset .. size-8` | variable / `index_entry[]` | `entries:index_entry[]` | 结束位置固定为 `size-8` | `sub_41C8A0` 设置 `index_ptr=base+u32le(size-4)`，循环至 `base+size-8`。 |
| `INDEX_ENTRY` | `WS2_INDEX_ENTRY` | index table 内顺序记录 | `8 + 4*offset_count` bytes / `<u32 script_id,u32 offset_count,u32 offsets[offset_count]>` | `script_id:u32le, offset_count:u32le, offsets:u32le[]` | `offsets[0]` 为默认入口；`offsets[n]` 为子入口 | `sub_41C8A0` 读取 id、count 与 offsets 后调用 `sub_41C790` 存入 reader `+0x34`。 |
| `STRING_TABLE` | `WS2_STRING_TABLE` | `string_table_offset` | variable / `<u8 count, ws2_string[count]>` | `count:u8, strings:ws2_string[]` | count 为单字节 | `sub_41C8A0` 将 cursor 设到 `base+u32le(size-8)`，先读 `sub_41B9B0` 作为数量，再循环 `sub_41BE80`。 |
| `WS2_STRING` | `WS2_LENGTH_PREFIXED_STRING` | string table 或脚本流内字符串字段 | `2 + length + 1` bytes when `length>0`; `2` bytes when `length==0` | `length:u16le, bytes:bytes[length], nul:u8?` | `length==0` 表示空字符串且不含 trailing NUL | `sub_41BE80` 先窥读 `u16le length`；`length>0` 时写回 cursor 并复制 C 字符串，`length==0` 时只清空目标 string。 |

### 7.2 Reader primitive 表

| Opcode | 命名 | byte_pattern | length / format | operand_schema | sub_opcode / variants | 精确定义与证据 |
|---:|---|---|---|---|---|---|
| `READ_U8` | `WS2_READ_U8` | 当前 `reader.cursor` | 1 byte | `value:u8` | none | `sub_41B9B0` 返回 `*cursor`，随后 `cursor += 1`。 |
| `READ_U16` | `WS2_READ_U16LE` | 当前 `reader.cursor` | 2 bytes / little-endian | `value:u16le` | none | `sub_41B990` 调用 `sub_41B950`，返回 `cursor[0] + (cursor[1] << 8)`，随后 `cursor += 2`。 |
| `READ_U32` | `WS2_READ_U32LE` | 当前 `reader.cursor` | 4 bytes / little-endian | `value:u32le` | none | `sub_41B970` 调用 `sub_41B920`，随后 `cursor += 4`。 |
| `READ_STRING` | `WS2_READ_STRING` | 当前 `reader.cursor` | variable / `ws2_string` | `length:u16le, bytes, trailing_nul?` | `length==0` 为空字符串 | `sub_41BE80` 读取长度前缀字符串；用于 string table 与运行期命令的内联字符串 operand。 |
| `SEEK_SCRIPT` | `WS2_SEEK_SCRIPT_ID` | 非字节流 opcode；索引表查找动作 | runtime action | `script_id:u32le` | 默认使用 `offsets[0]` | `sub_41BA80` 遍历 index entries，命中后设置 `reader.cursor = data_ptr + offsets[0]`。 |
| `SEEK_SCRIPT_SUB` | `WS2_SEEK_SCRIPT_ID_SUBINDEX` | 非字节流 opcode；索引表查找动作 | runtime action | `script_id:u32le, sub_index:u16le` | 使用 `offsets[sub_index]` | `sub_41BAF0` 遍历 index entries，并检查 `sub_index < offset_count`。 |

### 7.3 WS2 runtime opcode 表（已定位）

`S` 表示一个 `WS2_STRING` operand，其长度规则见 `WS2_STRING` 行：`length>0` 时为 `2 + length + 1`，`length==0` 时为 `2`。`next` 表示 handler 末尾调用 `sub_42C150` 继续取下一条 opcode；`seek_script`/`seek_sub` 表示 handler 会通过索引表改变 cursor，反汇编器应将目标 `script_id`/`sub_index` 标签化。运行期 opcode 的助记符命名避免 `WS2_`/`ws_` 前缀；证据不足的业务语义采用保守名，并在证据列标明不确定性。

| Opcode | 命名 | byte_pattern | length / format | operand_schema | sub_opcode / variants | 精确定义与证据 |
|---:|---|---|---|---|---|---|
| `0x00` | `RESET_EXEC_GATE` | `00` | 1 | none | handler `sub_42C1B0` | 只调用 `sub_42C150` 取下一 opcode，并把 `this+284624` 置 1；像恢复/重置执行门控标志。 |
| `0x01` | `CLEAR_TEXT_QUEUE` | `01` | 1 | none | handler `sub_42D540` | 在执行门控为真时释放并清零 `this+222108/222112/222116` 的动态缓冲，再取下一 opcode；该缓冲与后续文本/记录队列相关，具体用途不完全确定。 |
| `0x02` | `QUEUE_TEXT_RECORD` | `02` | `2 + S` | `u8, WS2_STRING` | handler `sub_42D9D0` | 读 `u8 + string`，把字符串复制到 `0x82` 字节记录后经 `sub_42D8B0` 追加到 `this+222104` vector；偏文本/消息记录队列，字段含义不完全确定。 |
| `0x03` | `START_TEXT_OUTPUT` | `03` | 1 | none | handler `sub_42C1D0` | 执行时调用 `sub_4202C0` 并把当前 handler 改为 `sub_42ACB0`；`sub_42ACB0` 统计 `0x82` 记录数并调用 `sub_447E50`，像启动已排队文本/消息输出。 |
| `0x04` | `SET_VISUAL_SLOT` | `04` | 3 | `u8, u8` | handler `sub_42D5A0` | 读两个 `u8`，调用 `sub_443160(slot,value)`；slot 为 `0/1/2` 时还清空若干缓存字符串并调用 `sub_41D660`。视觉/图层 slot 语义较可能，但保守。 |
| `0x05` | `SET_VISUAL_RESOURCE` | `05` | `3 + S` | `u8 mode, u8 count, WS2_STRING[count]` | handler `sub_42EF80` | 读 `mode + count + string[count]`，调用 `sub_41E9C0`、`sub_4201E0`、`sub_443730`，并维护 `this+222460..222600` 字符串缓存；像设置图像/立绘/视觉资源，参数格式复杂，保守命名。 |
| `0x06` | `RUN_DRAW_TRANSITION` | `06` | 1 | none | handler `sub_42C220` | 把当前 handler 设为 `sub_428430`；`sub_428430` 使用 `sub_435170/sub_43AD90` 执行显示过渡，并受 `0x07/0x08/0x09` 设置的字段影响。 |
| `0x07` | `SET_DRAW_EFFECT` | `07` | `3 + S` | `u8 mode, u8 count, WS2_STRING[count]` | handler `sub_42F480` | 读 `mode + count + string[count]`；错误字符串直接出现 `DrawEffect`/`MaskEffect`，并写 `this+284584/284588/284596` 等效果字段。 |
| `0x08` | `SET_TRANSITION_STEP` | `08` | 2 | `u8` | handler `sub_42C260` | 读 `u8` 后写 `this+284580 = value + 1`；该字段被 `sub_428430` 作为过渡执行参数使用。具体是页数/速度/步进仍不确定。 |
| `0x09` | `SET_TRANSITION_MODE` | `09` | 2 | `i8 mode` | handler `sub_42C2A0` | 读有符号 `i8`，将 `-2/-1/1/2/default` 映射到 `this+284568 = 4/3/1/2/0`；明显是过渡/绘制模式选择。 |
| `0x0A` | `SHOW_INLINE_TEXT` | `0A` | `1 + S` | `WS2_STRING` | handler `sub_42CA80` | 读 inline string；根据 `this+223436` 调 `sub_41FD60` 或 `sub_41FFA0`，并切到 `sub_4268F0` / `sub_424F90` handler；像显示文本或文本窗口内容。 |
| `0x0B` | `PLAY_TABLE_SOUND` | `0B` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_42F6C0` | 读 `count + string[count]`，第一个字符串 `atol` 后索引 `dword_49A5F0`，再调用 `sub_44E650` 播放/载入音频；表项具体是 SE/BGM 不确定。 |
| `0x0C` | `STOP_TABLE_SOUND` | `0C` | 1 | none | handler `sub_42D670` | 清空 `this+222684` 并调用 `sub_44E220(this+209568)`；与 `0x0B` 的音频对象邻近，像停止/清除该音频通道。 |
| `0x0D` | `LOAD_AUDIO_FILE` | `0D` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_42F850` | 读 `count + string[count]`，在条件允许时调用 `sub_44E720(first_string)`；周边为音频 handler，但 `sub_44E720` 未深挖，保守命名为加载/准备音频文件。 |
| `0x0E` | `PLAY_VOICE` | `0E` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_42F960` | 读 `count + string[count]`，复制到 `this+222628`，调用 `sub_44E540(file)`；`sub_44E540` 明确打开 `SoundData/voice_b.vbd`，后续可调用 `sub_44E5F0`。 |
| `0x0F` | `CLEAR_WAIT_TIMER` | `0F` | 1 | none | handler `sub_42C380` | 执行时把 `this+375336` 清零；与 `0x10` 的等待计数同字段。 |
| `0x10` | `WAIT_TIME` | `10` | 5 | `u32` | handler `sub_42C3B0` | 读 `u32` 到 `this+375336` 并切到 `sub_4238A0`；`sub_4238A0` 每次约消耗 `33.33`，未完返回 1 阻塞主循环，典型 wait。 |
| `0x11` | `WAIT_TEXT_ADVANCE` | `11` | 1 | none | handler `sub_42C400` | 无 operand；根据 `this+223436` 切到 `sub_42AC00` 或 `sub_42AC60`，否则继续。像等待文本/窗口推进，具体输入条件未完全确认。 |
| `0x12` | `PUSH_CONST` | `12` | 5 | `u32` | handler `sub_42DAD0` | 读 `u32`，调用 `sub_428F70(this+223464,&value)` 压入运行期栈；debug 输出也记录该值。 |
| `0x13` | `POP_STACK` | `13` | 1 | none | handler `sub_42CDB0` | 检查 `this+223468..223472` stack，非空则 `top -= 4`；错误字符串为 `POP_BACK`。 |
| `0x14` | `PUSH_FLAG` | `14` | 5 | `flag_index:u32le` | handler `sub_42DB60` | 读 flag index `<0x1388`，把 `this+4*idx+223480` 压栈；错误字符串 `PushFlag`。 |
| `0x15` | `POP_FLAG` | `15` | 5 | `flag_index:u32le` | handler `sub_42CEC0` | 读 flag index，从栈 pop 一个值，夹到 `0..100` 后写 `this+4*idx+223480`；字符串 `PopFlag` / `POP_FLAG`。 |
| `0x16` | `PUSH_PERSIST_FLAG` | `16` | 5 | `flag_index:u32le` | handler `sub_42DC30` | 读 index，压入 `this+4*idx+70228`；错误字符串 `PushPerFlag`，即 persistent/permanent flag。 |
| `0x17` | `POP_PERSIST_FLAG` | `17` | 5 | `flag_index:u32le` | handler `sub_42D030` | 读 index，从栈 pop 写 `this+4*idx+70228`；错误字符串 `PopPerFlag`。 |
| `0x18` | `ADD` | `18` | 1 | none | handler `sub_42DD00` | 要求栈上至少两个值，计算倒数第二 + 栈顶，弹两个再压结果；debug 格式显示加法表达式。 |
| `0x19` | `SUB` | `19` | 1 | none | handler `sub_42DE40` | 同样取两个栈值，计算倒数第二 - 栈顶；debug 格式显示减法表达式。 |
| `0x1A` | `EQ` | `1A` | 1 | none | handler `sub_42DF80` | 比较两个栈值相等并压布尔结果；错误字符串 `EQUAL`。 |
| `0x1B` | `GT` | `1B` | 1 | none | handler `sub_42E0D0` | 比较倒数第二 > 栈顶并压布尔结果；错误字符串 `GREATER`。 |
| `0x1C` | `LT` | `1C` | 1 | none | handler `sub_42E220` | 比较倒数第二 < 栈顶并压布尔结果；错误字符串 `LESS`。 |
| `0x1D` | `AND` | `1D` | 1 | none | handler `sub_42E370` | 对两个栈值做逻辑与：`left && right`，弹两个压布尔结果；无明确英文名字符串，但代码直观。 |
| `0x1E` | `OR` | `1E` | 1 | none | handler `sub_42E4E0` | 对两个栈值做逻辑或：`left || right`，弹两个压布尔结果；无明确英文名字符串，但代码直观。 |
| `0x1F` | `BOOL_NOT` | `1F` | 1 | none | handler `sub_42C840` | 对栈顶原地写 `top == 0`；无 operand，错误路径为栈空。 |
| `0x20` | `IF_TRUE` | `20` | 1 | none | handler `sub_42D190` | 弹栈顶并设置 `this+284624 = (top != 0)`，错误字符串 `IFTRUE`；该标志控制后续 handler 是否执行主体。 |
| `0x21` | `IF_FALSE` | `21` | 1 | none | handler `sub_42D2C0` | 弹栈顶并设置 `this+284624 = (top == 0)`，错误字符串 `IFFALSE`。 |
| `0x22` | `GOTO_LABEL` | `22` | 5 | `script_id:u32le` | handler `sub_42C460` | 读 `u32 script_id`，debug 字符串 `LabelJump : %d -> %d\n`，调用 `sub_41BA80(reader, id)` 跳到 index table 目标；错误字符串 `GoLabel`。 |
| `0x23` | `CALL_LABEL` | `23` | 5 | `script_id:u32le` | handler `sub_42C570` | 读 `u32 script_id`，debug 字符串 `LabelCall : %d -> %d\n`，调用 `sub_421A90` 建立 call；错误字符串 `CallLabel`。 |
| `0x24` | `RETURN_LABEL` | `24` | 1 | none inline | handler `sub_42D3F0` | 无 inline operand；从 `this+222284` return stack 弹 `(script_id, sub_index)`，调用 `sub_41BAF0`；debug 字符串 `Ret : %d -> %d\n`。 |
| `0x25` | `RUN_SYSTEM_COMMAND` | `25` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_42FAC0` | 读 `count + string[count]`，第一个字符串 `atol` 后 switch：case `0` 设置 rect 并 `sub_435B90`，case `1` 调 `sub_435C30`，case `10/12` 涉及 voice/选择流程等；是混合系统/UI/audio 命令分发，保守命名。 |
| `0x26` | `MAX_VALUE` | `26` | 2 | `count:u8` | handler `sub_42E650` | 读 `u8 count`，在栈顶 count 个值中找最大值，弹 count 个后压最大；错误字符串 `maximum`。 |
| `0x27` | `MIN_VALUE` | `27` | 2 | `count:u8` | handler `sub_42E7A0` | 读 `u8 count`，代码比较方向为找最小值；错误字符串仍是 `maximum`，疑似复制粘贴错误，所以命名按代码语义。 |
| `0x28` | `ABS_VALUE` | `28` | 1 | none | handler `sub_42C930` | 对栈顶原地执行 `abs32(top)`；错误字符串 `absolute`。 |
| `0x29` | `PUSH_LABEL_FLAG` | `29` | 5 | `label_id:u32le` | handler `sub_42E8F0` | 读 label id `<0x2710`，压入 `this+4*id+243480`；错误字符串 `PushLabelFlag`。 |
| `0x2A` | `ENTER_SCRIPT` | `2A` | `5 + S` | `script_id:u32le, WS2_STRING` | handler `sub_42CB80` | 读 `u32 script_id + string`，保存前一位置，更新 `this+222440` 当前 script id 与 `this+222312` 当前名称；入口序言性质明确。 |
| `0x2B` | `ENTER_SUBENTRY` | `2B` | 6 | `u8, sub_index:u16le, line_or_state:u16le` | handler `sub_42C630` | 读 `u8 + u16 sub_index + u16 line_or_state`，更新 `this+222448/222446/222444`，并可调用 `sub_41ED50`；常见子入口序言，最后字段具体含义略保守。 |
| `0x2C` | `SET_LINE_MARK` | `2C` | 3 | `u16le` | handler `sub_42C6E0` | 读 `u16` 写入 `this+222444`，并在 UI/debug 开启时调用 `sub_454F20`；像设置行号/当前位置标记。 |
| `0x2D` | `MAX_INDEX` | `2D` | 2 | `count:u8` | handler `sub_42E990` | 读 `u8 count`，在栈顶 count 个值中找最大值所在相对 index 并压回；错误字符串 `maximumindex`。 |
| `0x2E` | `MIN_INDEX` | `2E` | 2 | `count:u8` | handler `sub_42EAF0` | 读 `u8 count`，在栈顶 count 个值中找最小值所在相对 index 并压回；错误字符串 `minimumindex`。 |
| `0x2F` | `CALL_BRANCH_DIALOG` | `2F` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_430100` | 读分支字符串表，要求栈顶选择 index 合法，调用 `sub_421890` 后按字符串转 label id，`sub_41BA80` 跳转；错误字符串 `callbranch`。带 dialog/选择副作用。 |
| `0x30` | `CALL_BRANCH` | `30` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_430390` | 同样读分支字符串表并按栈顶选择跳到 `sub_41BA80` 目标；比 `0x2F` 少 dialog 调用路径，当前文档已有 call-branch 语义。 |
| `0x31` | `RUN_SUBCOMMAND_10` | `31` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_4305A0` | 读 `count + string[count]`，第一个字符串 `atol` 等于 `10` 时调用 `sub_4202C0` 并切到 `sub_42C100`，否则继续；具体 UI/等待语义未确认，保守按子命令命名。 |
| `0x32` | `REPORT_SCRIPT_ERROR` | `32` | 1 | none | handler `sub_42C730` | 无条件调用 `sub_451B10` 报错/提示后继续；没有业务副作用，像无效或显式错误 opcode。 |
| `0x33` | `CLEAR_UI_STATE` | `33` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_4306B0` | 读但基本忽略 `count + string[count]`，执行时清零 `this+222120..222186/222260` 并释放 `this+222268` vector；像清除选择/UI 状态。 |
| `0x34` | `LOAD_UI_RECORDS` | `34` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_4307D0` | 读 `count + string[count]`，对 `4/8/10` 项等格式分支处理，并通过 `sub_42D940` 追加 `0xD0` 字节记录；疑似菜单/选择项记录，保守命名。 |
| `0x35` | `CONTROL_UI_MODE` | `35` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_430C90` | 读 `count + string[count]`，第一个参数控制分支；会设置 `sub_42BCD0` handler、调用 `sub_4202C0/sub_42B8F0/sub_443160` 等。UI/选择流程控制可能性高，但细节不确定。 |
| `0x36` | `LOAD_RESOURCE_SET` | `36` | `2 + sum(S[i])` | `count:u8, WS2_STRING[count]` | handler `sub_431350` | 读 `count + string[count]`，要求 5 个字符串时调用 `sub_442480(a,b,c,d,e)`；`sub_442480` 根据字符串装载/解析多组资源，随后可 `sub_442A40/sub_455040`。 |
| `0x37` | `STACK_DUP` | `37` | 2 | `depth:u8` | handler `sub_42EC50` | 读 `u8 depth`，复制栈中指定深度值并压栈；错误字符串 `stack_dup`。 |
| `0x38` | `STACK_SWAP` | `38` | 2 | `depth:u8` | handler `sub_42C9D0` | 读 `u8 depth`，交换栈顶与指定深度值；错误字符串 `stack_swap`。 |
| `0x39` | `MUL` | `39` | 1 | none | handler `sub_42ED00` | 弹两个栈值相乘后压结果；错误字符串 `multiply`。 |
| `0x3A` | `DIV` | `3A` | 1 | none | handler `sub_42EE40` | 弹两个栈值做整数除法后压结果；错误字符串 `divide`。 |
| `0x3B` | `SET_DISPLAY_MODE` | `3B` | 2 | `mode:u8` | handler `sub_42C780` | 读 `u8 <= 3`，写 `this+284576` 并调用 `sub_4357A0(mode)`；显示/绘制模式可能性高，但具体枚举不确定。 |
| `0x3C` | `SET_DISPLAY_ENABLE` | `3C` | 2 | `enabled:u8` | handler `sub_42C7D0` | 读 `u8`；为 0 时清 `this+223460` 并调用 `sub_4356E0`，非 0 时置 `this+223460=1`。像启停某显示/UI 状态，保守命名。 |
| `0x3D` | `RUN_UI_ACTION` | `3D` | `3 + S` | `u8 mode, u8 count, WS2_STRING[count]` | handler `sub_431540` | 读 `mode + count + string[count]`；mode 0 取首字符串 `atol` 调 `sub_4250C0`，mode 1 设置 `this+70224=1` 并调用 `sub_42B040`。UI/流程动作，细节不确定。 |
| `0x3E` | `CLEAR_VISUAL_SLOTS` | `3E` | 1 | none | handler `sub_42D6E0` | 调 `sub_443160(1,0)` 与 `sub_443160(2,0)`，并清空 `this+222516/222544/222572/222600` 字符串缓存；明显是清除视觉 slot/layer。 |
| `0x3F` | `NOP` | `3F` | 1 | none | handler `sub_431750` | 仅调用 `sub_42C150`，无 operand、无状态写入。 |
| `0x40` | `INVALID_RESERVED` | `40` | invalid | none | no executable handler | direct table `0x40` 是无效/占位项，不是可执行 handler；显式保留为 invalid/reserved。 |
| `0x41` | `SET_EXT_PARAMS` | `41` | 3 | `u8, u8` | handler `sub_4316F0` | 读两个 `u8`，若第一个 `<=2` 则写 `this+375340/375344`；字段邻近 wait/运行参数区，但实际消费者未闭合，保守命名。 |
| `0x42` | `RUN_EXT_ACTION` | `42` | `3 + S` | `u8 mode, u8 count, WS2_STRING[count]` | handler `sub_431840` | 读 `mode + count + string[count]`，mode `0/1` 分别调用 `sub_4272A0` / `sub_427730`，失败时 `sub_41D5C0` 保存当前位置；具体动作未确认。 |
| `0x43` | `RANDOM` | `43` | 1 | none | handler `sub_431760` | 要求栈顶存在上限值，调用 `rand()` 并按栈顶缩放，弹上限后压随机结果；错误字符串 `random`。 |
| `0x44` | `NOP_ALIAS` | `44` | 1 | none | handler `sub_431750` | handler 同 `0x3F`，即 `sub_431750` 只 fetch next；可视为 `NOP` alias。 |
| `0x80 0x00` | `SET_EXT_PARAMS_ALIAS` | `80 00` | `2 + operands(0x41)` | same as `0x41` | handler `sub_4316F0` | 扩展表 index 0 alias 到 direct `0x41` 的 `sub_4316F0`；operand 同 `0x41`，只是 opcode 前缀为双字节。 |
| `0x80 0x01` | `RUN_EXT_ACTION_ALIAS` | `80 01` | `2 + operands(0x42)` | same as `0x42` | handler `sub_431840` | 扩展表 index 1 alias 到 direct `0x42` 的 `sub_431840`；operand 同 `0x42`。 |
| `0x80 0x02` | `RANDOM_ALIAS` | `80 02` | `2 + operands(0x43)` | same as `0x43` | handler `sub_431760` | 扩展表 index 2 alias 到 direct `0x43` 的 `sub_431760`；operand 同 `0x43`。 |
| `0x80 0x03` | `NOP_ALIAS_EXT` | `80 03` | `2 + operands(0x44)` | same as `0x44` | handler `sub_431750` | 扩展表 index 3 alias 到 direct `0x44/0x3F` 的 `sub_431750`；无 operand。 |

无效项：直接表 `0x45..0x7F` 当前不是可执行 handler；扩展表除 `0x80 00..03` 外后续区域紧邻普通数据表与字符串，不能按 opcode handler 扫描。

### 7.4 当前可用于工具实现的最小结构集合

在 VM opcode 表已补齐的基础上，反汇编器可安全输出以下结构化 asm 片段，保证解压后 buffer 覆盖与索引信息不丢失：

```text
.ws2_header format=2, unpacked_size=0x001BE038
.ws2_footer string_table=loc_xxxxxxxx, index_table=loc_yyyyyyyy

loc_xxxxxxxx:
.string_table count=N
    .ws2_string "..."

loc_yyyyyyyy:
.index script_id, [loc_entry0, loc_entry1, ...]
```

对不在 index/script 可达区间、或落入数据表的区域，仍应输出结构化 `.byte`/`.blob` 数据伪指令以保持逐字节覆盖；对可达脚本区则按上表 opcode 解析。

## 8. 反汇编/汇编工具实现建议

### 8.1 反汇编器阶段拆分

建议 `disassembler.py` 分为四层：

1. **Container reader**
   - 读取磁盘 `script.ws2`。
   - 解析 `0x20` compression header。
   - 解压 payload 得到 raw WS2 buffer。

2. **WS2 package parser**
   - 读取 footer 两个 offset。
   - 解析 string table。
   - 解析 index table。
   - 标记所有 `offsets[]` 为候选入口 label。

3. **VM disassembler**
   - 根据本文档的 runtime opcode 表，从每个入口 offset 递归/线性解析指令。
   - 遇到跳转/调用字段生成符号标签。
   - 对无法确认的区域输出结构化 `.byte` 或 `.blob` 伪指令，但不得遗漏任何字节。

4. **Text renderer**
   - 按 `CLAUDE.md` 要求输出语义 asm。
   - 文本字符串按 `--encoding` 解码，默认建议 `cp932`。
   - 无法安全显示的字节使用 `{{XX}}` 或 `{{XX:XX}}` 占位符。

### 8.2 汇编器阶段拆分

建议 `assembler.py` 对称实现：

1. 解析 asm 文本、标签、索引伪指令、字符串伪指令和 opcode 指令。
2. 重建解压后 WS2 buffer。
3. 重建 string table、index table 与尾部两个 offset。
4. 重新压缩为原 `format_id`。
5. 写回 `0x20` compression header + payload。

零突变要求下的关键风险是压缩层；当前主样本已经验证 `format_id=2` 可用 bzip2 level 9 逐字节复现 payload。验收时仍建议同时区分：

| 验证层级 | 目标 | 说明 |
|---|---|---|
| 解压层零突变 | rebuilt_unpacked == original_unpacked | VM/脚本结构正确性的最低要求 |
| 压缩层零突变 | rebuilt_script.ws2 == original_script.ws2 | 当前 `format_id=2` 可用 bzip2 level 9 复现；`format_id=1` 使用 zlib/deflate 兼容路径 |
| 运行期等价 | EXE 可加载并行为一致 | 可接受不同压缩输出，只要 EXE 解压一致 |

## 9. 当前置信边界

高置信：

- `ws2/script.ws2` 的启动加载入口是 `sub_423060 -> sub_41D5B0 -> sub_41CAD0`。
- 读取器对象位于全局对象 `+0x1FD14`。
- 磁盘文件整包通过 `CreateFileA/GetFileSize/ReadFile` 读入内存。
- 外层压缩头固定 `0x20` 字节。
- 压缩头 `DWORD[0]` 是 `format_id`，必须 `< 4`。
- 压缩头 `DWORD[1]` 是解压后大小。
- 当前 `script.ws2` 为 `format_id=2`，解压后大小 `0x1BE038`。
- `format_id=2` 调用 `sub_402160 -> sub_45E8B0`，错误字符串为 `xb:*`；样本 payload 是标准 bzip2 stream，bzip2 level 9 可逐字节复现。
- `format_id=1` 调用 zlib/deflate inflate 路径，初始化版本字符串为 `1.1.4`；实现可用 zlib 兼容流处理。
- 解压后最后 8 字节分别是 `string_table_offset` 与 `index_table_offset`。
- Index entry 是 `script_id + offset_count + offsets[]` 的变长结构。
- `sub_41BA80` 按 `script_id` 设置 cursor 到首 offset。
- `sub_41BAF0` 按 `script_id + sub_index` 设置 cursor 到指定 offset。
- 基础 reader primitive `u8/u16le/u32le/ws2_string` 已确认。
- WS2 主执行循环为 `sub_41DF00`，opcode fetch/dispatch 为 `sub_42C150`。
- runtime opcode 编码为 `u8` 直接 opcode 或 `0x80..0xFF + u8` 扩展 opcode；已确认直接 opcode `0x00..0x44`（除无效占位）和扩展别名 `0x80 00..03`。
- 每条已确认 runtime opcode 的 operand schema、长度规则、handler 与跳转/标签基准已列入 7.3。
- String table 当前样本为单项 `VERSION_1.0.0`，主要作为包/版本字符串集合；运行期文本 operand 由 handler 内联调用 `sub_41BE80` 读取。
- `length == 0` 的 `WS2_STRING` 表示空字符串；只占两字节 length 字段，不带 trailing NUL。

当前边界：

- 对当前 `ws2/script.ws2` 与当前 EXE，VM 分发、opcode 长度、字符串语义、索引跳转基准、format 2 零突变压缩均已闭合。
- 若后续遇到其他游戏版本或 format 1 样本，应重新验证其 zlib 编码参数是否需要 bit-exact 兼容。