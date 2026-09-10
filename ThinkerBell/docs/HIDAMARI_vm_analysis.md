# HIDAMARI `.a` 脚本 VM 分析与 Opcode 文档

> 状态：初版，基于 `hidamari.exe` 反编译输出与现有 `arc02/*.a` 样本统计。本文档作为后续 `opcodelist.py` / `disassembler.py` / `assembler.py` 的指令集建模依据。  
> 重点：当前已确认 `.a` 外层记录格式、VM 主调度、命令记录编码、Opcode 数值分发表；部分 Opcode 的业务语义仍需继续逐个函数命名。

## 1. 文件来源与加载链路

`.a` 类型文件并不是独立磁盘文件，而是 `Arc00.dat` 索引指向 `Arc02.dat` payload 的条目。

加载链路：

1. `sub_405530` 加载 `Arc00.dat` 索引与 `Arc02.dat` payload。
2. `sub_406280` 解析索引记录，遇到 `type == "a"` 时插入 `this + 0x54` 的 a-entry 容器。
3. `sub_407250(this, entry_id)` 按 `entry_id` 在 a-entry 容器中查找脚本入口。
4. `sub_407120(this, record, archive_id=1, decompress=0)` 从 `Arc02.dat` 按 offset/size 直接读取 `.a` 数据，不做 LZSS 解压。
5. `sub_43D710` 解析 `.a` 外层记录，生成内部 1032 字节 command record 列表。
6. 主循环 `sub_422E00` 每帧调用 `sub_4230F0` 取下一条 command record，再由 `sub_4236B0` / `sub_4239C0` 执行。

关键证据：

- `sub_406280` 中 `strstr(Str, "a")` 后调用 `sub_40C390` 插入 a-entry 容器。
- `sub_407250` 在 a-entry 容器中按 `record + 0x110 == entry_id` 查找。
- `sub_407120(..., a3=1, a4=0)` 走直接读取分支，不进入 `sub_406E40`。
- `sub_43D710` 将 `.a` 文件拆成长度前缀记录并插入 `sub_43EED0` 管理的 command record vector。

## 2. `.a` 文件外层格式

`.a` 文件是连续的长度前缀块：

```text
struct AFile {
    repeated ARecord records until EOF;
}

struct ARecord {
    uint32_le length;
    uint8 payload[length];
}
```

解析函数：`sub_43D710`。

伪代码：

```c
off = 0;
while (off < file_size) {
    len = read_u32le(data + off);
    off += 4;
    payload = data[off : off + len];
    off += len;
    if (len != 0) {
        append_command_record(payload);
    }
}
```

注意：

- `length == 0` 的记录不会插入执行队列。
- `sub_43D710` 使用 1024 字节临时缓冲保存 payload，因此单条 payload 的安全上限应视为 `<= 0x400`。
- `.a` 文件末尾没有显式 EOF 字节；游戏在 `sub_407250` 解析完后额外追加一条 `<File End>` command record。

## 3. VM Command Record 内存格式

`.a` 外层每条 payload 会被包装为 1032 字节 command record：

```text
struct CommandRecord {
    uint8  bytes[0x400];   // 原始 payload，零填充
    uint32 cursor;         // 当前解析游标，通常初始为 0
    uint32 length;         // payload 长度
}
```

证据：

- `sub_43EED0` 以 `1032` 字节为单位插入记录。
- `sub_4230F0` 取当前记录时复制 `0x400` 字节到 `this + 420`，并设置：
  - `this + 1444 = record.cursor`
  - `this + 1448 = record.length`
- `sub_401470` / `sub_401280` 使用 `record + 0x400` 作为当前游标，`record + 0x404` 作为记录长度。

## 4. 指令/记录类型总览

VM 的“opcode”不是单字节原生机器码，而是 command record payload 中的文本式/标记式字节流。

当前确认的顶层 payload 类型：

| payload 起始 | 类型 | 说明 |
|---|---|---|
| `S...` | TEXT | 文本记录。`S` 后跟文本字节，样本中常以专用终止字节结尾。 |
| `M#...` | COMMAND | 命令记录。`#` 后进入参数 token 流，第一个 `N imm32` 是 opcode 编号。 |
| `M$` | END_MARKER | 结束/特殊完成标记。`sub_401470` 返回 `9999`，进入 `sub_42FB50`。 |
| `<File End>` | FILE_END | 游戏主动追加的文件结束记录，不存在于原始 `.a` 文件中。 |

执行入口：

- `sub_4236B0` 调用 `sub_401470(this + 32, current_record)` 得到命令编号。
- `sub_4239C0(this, opcode)` 对 `opcode` 做 `switch` 分发。
- `opcode == 9999` 调用 `sub_42FB50`。
- `opcode == -1` 表示解析失败或非命令记录的路径，按当前状态处理文本/等待。

## 5. Command payload 编码

### 5.1 主命令格式

绝大多数命令形如：

```text
'M' '#' 'N' uint32_le opcode [operands...]
```

即：

```text
4D 23 4E <opcode:u32le> ...
```

`sub_401470` 逻辑：

1. 检查当前字节必须是 `M`。
2. 读取下一字节：
   - `#`：调用 `sub_401280` 读取 `N imm32`，返回该 imm32 作为 opcode。
   - `$`：设置完成标记，返回 `9999`。
   - 其他：返回 `-1`。

### 5.2 操作数 token

已确认 token：

| token | 字节 | 长度 | 解析函数/依据 | 含义 |
|---|---:|---:|---|---|
| `N` | `0x4E` | 5 | `sub_401280` | 立即数：`N` + `uint32_le`。用于 opcode 与普通整数参数。 |
| `M` | `0x4D` | 1+ | `sub_401470` | command record 起始。 |
| `#` | `0x23` | 1 | `sub_401470` | 命令模式，后面必须有 `N opcode`。 |
| `$` | `0x24` | 1 | `sub_401470` | 结束模式，返回 `9999`。 |
| `A` | `0x41` | 5 | 样本统计 | 变量/数组/资源引用型操作数，后跟 `uint32_le`。具体求值语义需结合各 opcode handler。 |
| `L` | `0x4C` | 可能 5 | 样本统计 | 标签/位置/逻辑块引用，见 `MLN...` 样本；语义待确认。 |
| `S` | `0x53` | 变长 | 样本统计 | 文本记录起始，不走 `M#` opcode 分发。 |

`N imm32` 确认读取方式：

```text
if current_byte != 'N': fail
cursor += 1
value = read_u32le(payload + cursor)
cursor += 4
```

### 5.3 文本记录

样本中大量 payload 以 `S` 开头，例如：

```text
S....
```

这类记录不会被 `sub_401470` 识别为 `M#` 命令，通常走 `opcode == -1` / 文本显示路径。文本显示、等待、换页逻辑在 `sub_4236B0`、`sub_448510`、`sub_423920` 附近处理。

## 6. Opcode 分发总表

Opcode 分发函数：`sub_4239C0`。

说明：

- `handler` 是当前反编译函数名。
- `confirmed_name` 来自 handler 内部 `sub_427280(..., aXXX)` 的错误字符串或明显行为；没有确认的暂用函数名。
- `observed_count` 来自当前 `arc02/*.a` 样本中 `M#N opcode` 统计。
- 未在样本出现但 switch 存在的 opcode 仍列出。

| opcode | handler | confirmed_name / 语义状态 | observed_count |
|---:|---|---|---:|
| -1 | special | 非 `M#` 命令或解析失败；可能进入文本/等待路径 | n/a |
| 0 | `sub_427280` + `sub_424380` | 读取参数并切换/跳转到指定脚本入口 | 0 |
| 1 | `sub_42E210` | `LJamp` / jump-like，疑似跳转到当前记录参数指定的行号 | 95 |
| 2 | `sub_42E410` | `SCall` / call，保存返回现场后切换脚本 | 163 |
| 3 | `sub_42E5F0` | 待命名 | 100 |
| 4 | `sub_42E650` | 待命名，同 handler 覆盖 4-7 | 33 |
| 5 | `sub_42E650` | 待命名，同 handler 覆盖 4-7 | 1 |
| 6 | `sub_42E650` | 待命名，同 handler 覆盖 4-7 | 0 |
| 7 | `sub_42E650` | 待命名，同 handler 覆盖 4-7 | 0 |
| 8 | `sub_42E7B0` | 待命名 | 388 |
| 9 | `sub_42EB30` | 待命名 | 35 |
| 10 | inline | `--this[645]`，计数器递减 | 388 |
| 11 | `sub_42ED90` | 待命名 | 0 |
| 12 | `sub_42EED0` | 待命名 | 0 |
| 13 | `sub_42EFD0` | 待命名 | 0 |
| 14 | `sub_42F010` | 待命名 | 0 |
| 15 | `sub_42F060` | 待命名 | 0 |
| 16 | `sub_42F520` | 待命名 | 0 |
| 17 | `sub_42F5D0` | 待命名 | 0 |
| 18 | `sub_42F610` | 待命名 | 0 |
| 19 | `sub_42F680` | 待命名 | 0 |
| 20 | `sub_42F6D0` | 待命名 | 0 |
| 21 | `sub_42F760` | 待命名 | 12 |
| 22 | `sub_42F7A0` | 待命名 | 0 |
| 23 | `sub_42F7D0` | 待命名 | 0 |
| 24 | `sub_42F800` | UI/text flush-like，调用 `sub_447F40` / `sub_44ABB0` | 0 |
| 25 | `sub_42F3B0` | 待命名 | 0 |
| 26 | `sub_42F100` | 待命名 | 0 |
| 27 | `sub_42F830` | 待命名 | 0 |
| 28 | `sub_42F920` | 待命名 | 10 |
| 29 | `sub_42F9F0` | 待命名 | 0 |
| 30 | `sub_42FA70` | 待命名 | 0 |
| 31 | `sub_42FAA0` | 待命名 | 7 |
| 32 | `sub_42FAD0` | 待命名 | 0 |
| 33 | `sub_4301F0` | 待命名 | 0 |
| 34 | `sub_4304B0` | 待命名 | 16 |
| 35 | `sub_4304C0` | 待命名 | 276 |
| 36 | `sub_430C90` | 待命名 | 8 |
| 37 | `sub_430D60` | 待命名 | 138 |
| 38 | `nullsub_8` | 空操作 | 0 |
| 39 | `sub_430DC0` | 待命名 | 318 |
| 40 | `sub_430DE0` | 待命名 | 27 |
| 41 | `sub_430E90` | 待命名 | 58 |
| 42 | `sub_430EC0` | 待命名 | 27 |
| 43 | `sub_430F10` | 待命名，高频 | 11209 |
| 44 | `sub_430FB0` | 待命名 | 643 |
| 45 | `sub_430FF0` | 待命名 | 20 |
| 46 | `sub_431010` | 待命名，高频 | 1628 |
| 47 | `sub_431070` | 待命名 | 0 |
| 48 | `sub_431940` | 待命名 | 0 |
| 49 | `sub_431B60` | 待命名 | 0 |
| 50 | `sub_4321B0` | 待命名 | 230 |
| 51 | `sub_432240` | 待命名 | 2 |
| 52 | `sub_432250` | 待命名 | 2 |
| 53 | `sub_432260` | 待命名 | 1 |
| 54 | `CDaoRecordset::ResetCursor` | 名称可能为 IDA 误识别；语义待确认 | 210 |
| 55 | `sub_4322C0` | 待命名 | 3 |
| 56 | `sub_432320` | 待命名 | 0 |
| 57 | `sub_432330` | 待命名 | 8 |
| 58 | `sub_4323A0` | 待命名 | 1 |
| 59 | `sub_4323B0` | 待命名 | 0 |
| 60 | `nullsub_9` | 空操作 | 8 |
| 61 | `sub_432500` | 待命名 | 1 |
| 62 | `sub_432680` | 待命名 | 11 |
| 63 | `sub_4329B0` | 待命名 | 1 |
| 64 | `sub_4329E0` | 待命名 | 2 |
| 65 | `sub_432A40` | 待命名 | 2 |
| 66 | `sub_432A80` | 待命名 | 64 |
| 67 | `sub_432A90` | 待命名 | 36 |
| 68 | `sub_432AA0` | 待命名 | 1 |
| 69 | `CDaoRecordset::ResetCursor` | 名称可能为 IDA 误识别；语义待确认 | 1 |
| 70 | `sub_432D70` | 高频，待命名 | 24216 |
| 71 | `sub_432DB0` | 待命名 | 2 |
| 72 | `sub_432DD0` | 待命名 | 3 |
| 73 | `sub_432E40` | 待命名 | 357 |
| 74 | `sub_432E80` | 待命名 | 1 |
| 75 | `sub_4332C0` | 待命名 | 0 |
| 76 | `sub_4332F0` | 待命名 | 1 |
| 77 | `sub_433310` | 待命名 | 6 |
| 78 | `sub_433370` | 待命名 | 0 |
| 79 | `sub_433410` | 待命名 | 4 |
| 80 | `sub_433470` | 待命名 | 201 |
| 81 | `sub_433590` | 待命名 | 217 |
| 82 | `sub_4336B0` | 待命名 | 1 |
| 83 | `sub_4336E0` | 待命名 | 0 |
| 84 | `sub_433710` | 待命名 | 1 |
| 85 | `sub_433770` | 待命名 | 1 |
| 86 | `sub_433880` | 待命名 | 1 |
| 87 | `sub_433A80` | 高频，待命名 | 5005 |
| 88 | `nullsub_10` | 空操作 | 0 |
| 89 | `nullsub_11` | 空操作 | 0 |
| 90 | `sub_433AD0` | 待命名 | 2 |
| 91 | `sub_433B00` | 待命名 | 13 |
| 92 | `sub_433BB0` | 待命名 | 27 |
| 93 | `sub_433BE0` | 待命名 | 13 |
| 94 | `sub_433C10` | 待命名 | 0 |
| 95 | `sub_432280` | 待命名 | 1 |
| 96 | `sub_433C40` | 待命名 | 1 |
| 97 | `sub_433D50` | 待命名 | 8 |
| 98 | `sub_433D60` | 待命名 | 5 |
| 99 | `sub_433D70` | 待命名 | 1 |
| 9999 | `sub_42FB50` | `M$` 结束/特殊完成 | n/a |

## 7. 样本中出现的 opcode

当前 `arc02/*.a` 统计：

- `.a` 文件数量：167
- 外层记录总数：101407
- `S` 文本记录：48690
- `M$` 结束记录：6194
- `M#N opcode` 命令记录 opcode 种类：65

高频 opcode：

| opcode | count | 备注 |
|---:|---:|---|
| 70 | 24216 | 最高频命令，语义优先分析 |
| 43 | 11209 | 高频命令，语义优先分析 |
| 87 | 5005 | 高频命令，语义优先分析 |
| 46 | 1628 | 高频命令 |
| 44 | 643 | 中高频 |
| 8 | 388 | 中频 |
| 10 | 388 | 中频，内联递减计数器 |
| 73 | 357 | 中频 |
| 39 | 318 | 中频 |
| 35 | 276 | 中频 |
| 50 | 230 | 中频 |
| 81 | 217 | 中频 |
| 54 | 210 | 中频 |
| 80 | 201 | 中频 |

## 8. 跳转/调用/控制流语义

当前确认：

### 8.1 opcode 1: `LJamp`

Handler：`sub_42E210`。

证据：handler 内部调用：

```c
sub_427280(this, aLJamp);
```

行为摘要：

1. 读取当前 command record 参数。
2. 追加 `<File End>` 到当前执行队列。
3. 取 `**(this + 296)` 作为目标 record index。
4. 移动当前 command record 指针到目标 index。
5. 将目标记录复制到当前执行记录。

推断：这是跳转类指令，命名字符串拼写为 `LJamp`，可能应理解为 `LJump`。

重定位策略：

- 该指令参数应视为 command record index，而不是字节偏移。
- 反汇编时应生成标签：`loc_rec_000123`。
- 汇编时按标签重新计算目标 record index。

### 8.2 opcode 2: `SCall`

Handler：`sub_42E410`。

证据：handler 内部调用：

```c
sub_427280(this, aSCall);
```

行为摘要：

1. 校验/读取当前参数。
2. 保存当前脚本现场：当前文件、当前 record index、若干状态字段。
3. 将返回现场压入 `this + 2532` 附近的 call stack。
4. 调用 `sub_424380(this, **(this + 296))` 切换到目标脚本/目标入口。

推断：这是脚本调用指令。

重定位策略：

- 至少一个参数是目标脚本 entry 或 record index。
- 需要继续确认目标参数是 `.a entry_id`、record index，还是两者组合。

### 8.3 opcode 9999: `M$`

`sub_401470` 遇到 `M$` 返回 `9999`，`sub_4239C0` 对 `9999` 调用 `sub_42FB50`。

`M$` 应作为特殊伪指令处理，而不是普通 opcode。

### 8.4 `<File End>`

`<File End>` 不是原始 `.a` 文件中的 opcode，而是加载后由 `sub_407250` 追加。

`sub_4230F0` 取到该字符串时：

- 如果存在 call stack，调用 `sub_423250` 返回上一层；
- 否则调用 `sub_424380(0)` 结束/切换。

## 9. 反汇编器建模建议

### 9.1 第一阶段：零突变结构反汇编

为了满足零突变，第一版反汇编器不应强行解释所有 opcode 的业务语义。建议采用两层结构：

```text
.record length=<原始长度>
    TEXT "..."

.record length=<原始长度>
    CMD opcode=70 operands=[raw token stream]
```

但输出中不能出现裸十六进制转储。不可打印字节使用 `{{XX}}` 占位符。

### 9.2 命令 token 语义化

对 `M#N opcode` 可解析为：

```text
CMD_0070    ; opcode 70
    N 70
    A 1
    N 0
```

或者单行：

```text
CMD_0070 A(1), N(0)
```

其中：

- `N(value)` 表示 `0x4E + uint32_le(value)`。
- `A(value)` 表示 `0x41 + uint32_le(value)`。
- 未知 token 必须保留为语义占位，如 `RAW_TOKEN "{{XX}}..."`，但不能丢弃。

### 9.3 文本记录

`S` 文本记录建议输出为：

```text
TEXT "..."
```

注意：

- `S` 起始字节本身是记录类型，应显式建模，汇编时恢复。
- 控制字节、终止字节、不可解码字节必须使用 `{{XX}}` 占位符。
- 不允许在注释里写原始 hex dump。

## 10. 未完成项与后续分析顺序

优先级建议：

1. 逐个分析高频 opcode handler：70、43、87、46、44。
2. 提取所有 handler 内部 `sub_427280(..., aName)` 的字符串名，回填 opcode 助记符。
3. 明确 `A imm32` 与 `L imm32` 的求值语义。
4. 明确文本记录 `S...` 的终止字节与编码；样本疑似 cp932/Shift-JIS 系文本，但有私用区/自定义字节。
5. 明确 jump/call 参数基准，验证变长文本重写后的标签重定位规则。
6. 完成 `opcodelist.py` 的最终 opcode schema。

## 11. 最小 opcode schema 草案

```python
OPCODES = {
    "TEXT": {
        "byte_pattern": b"S",
        "length": "outer_record.length",
        "operands": [{"type": "encoded_text", "encoding": "configurable"}],
    },
    "END_MARKER": {
        "byte_pattern": b"M$",
        "length": 2,
        "opcode_value": 9999,
        "operands": [],
    },
    "COMMAND": {
        "byte_pattern": b"M#N",
        "length": "outer_record.length",
        "opcode_offset": 3,
        "opcode_type": "u32le_after_N",
        "operands": "token_stream_after_opcode",
        "tokens": {
            "N": {"byte": 0x4E, "operand": "u32le"},
            "A": {"byte": 0x41, "operand": "u32le"},
            "L": {"byte": 0x4C, "operand": "u32le_or_pending"},
        },
    },
}
```

本 schema 已足够支持第一版零突变反汇编/汇编：即使具体 opcode 业务语义未完全命名，只要 token stream 原样结构化保存，就可以逐字节重建。
