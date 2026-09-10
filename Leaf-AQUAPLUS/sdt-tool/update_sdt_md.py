from pathlib import Path
import re

md = Path('sdt_vm_analysis.md')
text = md.read_text(encoding='utf-8')

mem = {}
for line in Path('export-for-ai/memory/00539000--00639000.txt').read_text(encoding='utf-8', errors='ignore').splitlines():
    parts = line.split('|')
    if len(parts) < 3:
        continue
    try:
        addr = int(parts[0].strip(), 16)
    except ValueError:
        continue
    hs = ''.join(parts[1].split())
    try:
        bs = bytes.fromhex(hs)
    except ValueError:
        continue
    for i, b in enumerate(bs):
        mem[addr + i] = b


def u32(a: int) -> int:
    return sum(mem.get(a + i, 0) << (8 * i) for i in range(4))


def cstr(a: int) -> str:
    bs = []
    for i in range(256):
        b = mem.get(a + i)
        if b is None or b == 0:
            break
        bs.append(b)
    return bytes(bs).decode('ascii', 'replace')

cmds = []
for i in range(201):
    a = 0x53AA30 + i * 8
    p = u32(a)
    op = u32(a + 4)
    name = cstr(p)
    if name:
        cmds.append((op, name, a, p))

switch_text = Path('export-for-ai/decompile/4101C0.c').read_text(encoding='utf-8', errors='ignore')
handlers = {}
for m in re.finditer(r'case\s+(\d+):\s*\n\s*(sub_[0-9A-Fa-f]+|nullsub_\d+)\(\);', switch_text):
    handlers[int(m.group(1))] = m.group(2)

base = 0x53B080
desc_names = {1: 'u8', 2: 'typed_i32', 3: 'str8', 4: 'str16', 5: 'out_local', 6: 'cmp_expr', 7: 'u8_alt', 8: 'u16', 9: 'u16_alt'}


def get_desc(op: int):
    off = base + 17 * (op - 64)
    entry = [mem.get(off + i, 0) for i in range(17)]
    ds = []
    for b in entry[:15]:
        if b == 0:
            break
        ds.append(b)
    return off, ds, entry[15], entry[16]


def schema(ds):
    if not ds:
        return 'none'
    return ', '.join(f'arg{i}:{desc_names.get(d, "desc" + str(d))}' for i, d in enumerate(ds))


def fmt(ds):
    if not ds:
        return '2 bytes / `<u16 opcode>`'
    return 'dynamic / `<u16 opcode> + ' + ' + '.join(desc_names.get(d, f'desc{d}') for d in ds) + '`'

rows = []
rows.append('| Opcode | 命名 | byte_pattern | length / format | operand_schema | sub_opcode / variants | 精确定义与证据 |')
rows.append('|---:|---|---|---|---|---|---|')
for op, name, a, p in cmds:
    off, ds, ret, aux = get_desc(op)
    h = handlers.get(op, 'default/no explicit handler')
    variant = f'handler={h}; return_flag={ret}; aux={aux}'
    evidence = f'命令名来自命令表 `0x{a:06X}: 0x{p:06X} -> {name}, opcode={op}`；参数描述表 `0x{off:06X}`；运行分发见 `sub_4101C0`。'
    rows.append(f'| `0x{op:04X}` | `{name}` | `{op & 0xff:02X} {(op >> 8) & 0xff:02X}` | {fmt(ds)} | {schema(ds)} | {variant} | {evidence} |')

new_52 = '''### 5.2 `sub_458EF0` 数值读取约定

`sub_458EF0(ptr, wide_flag)` 是内置 opcode 常用的二选一取值器：

| `wide_flag` | 读取方式 | 说明 |
|---:|---|---|
| `0` | `locals[*ptr]` | `ptr` 指向 1 字节局部变量索引；返回 `ctx+0xC00+4*index` 的 DWORD。 |
| `1` | `i32le(ptr)` | `ptr` 指向 4 字节立即数；返回该 DWORD。 |

因此内置 opcode 成对出现时，偶数/短格式通常是“从局部变量取右操作数”，奇数/长格式通常是“从 4 字节立即数取右操作数”。例如：

- `0x0002` 长度 4：`locals[dst] = locals[src]`。
- `0x0003` 长度 7：`locals[dst] = imm32`。
- `0x0006` 长度 9：比较 `locals[lhs]` 与 `locals[rhs]`。
- `0x0007` 长度 12：比较 `locals[lhs]` 与 `imm32`。

'''

builtin_section = '''### 6.1 内置 VM opcode：`0x0001..0x0028`

内置 opcode 由 `sub_459150` 直接 switch 分发。下表已补齐可由 handler 高置信确认的名称、长度、操作数和行为；`0x0000` 与 `0x0029` 不是有效内置 case。

| Opcode | 命名 | byte_pattern | length / format | operand_schema | sub_opcode / variants | 精确定义与证据 |
|---:|---|---|---|---|---|---|
| `0x0001` | `END` | `01 00` | 2 bytes | none | none | `sub_458E90(ctx, 0)`：停止脚本，清 `state/ip`；若 `sp != 0` 报 `Script Error : Stack Pointer Abnormal`。 |
| `0x0002` | `MOV_LOCAL_LOCAL` | `02 00` | 4 bytes / `<op,u8 dst,u8 src>` | `dst:local_index, src:local_index` | `sub_459350(0)` | `locals[dst] = locals[src]`；`sub_458EF0(...,0)` 从局部变量取值。 |
| `0x0003` | `MOV_LOCAL_IMM32` | `03 00` | 7 bytes / `<op,u8 dst,i32 value>` | `dst:local_index, value:i32` | `sub_459350(1)` | `locals[dst] = imm32`；`sub_458EF0(...,1)` 读 4 字节立即数。 |
| `0x0004` | `SWAP_LOCAL` | `04 00` | 4 bytes / `<op,u8 a,u8 b>` | `a:local_index, b:local_index` | none | `sub_4593B0` 交换 `locals[a]` 与 `locals[b]`。 |
| `0x0005` | `RAND_LOCAL` | `05 00` | 3 bytes / `<op,u8 dst>` | `dst:local_index` | none | `sub_459410`：`locals[dst] = rand() % 0xFFFF`。 |
| `0x0006` | `JCC_LOCAL_LOCAL_SKIP` | `06 00` | 9 bytes / `<op,u8 lhs,u8 cmp,u8 rhs,u32 target>` | `lhs:local_index, cmp:cmp_op, rhs:local_index, target:body_offset` | false fallthrough | `sub_459450(0)`：比较成立则 `ip=target`，否则 `ip += 9`。 |
| `0x0007` | `JCC_LOCAL_IMM32_SKIP` | `07 00` | 12 bytes / `<op,u8 lhs,u8 cmp,i32 rhs,u32 target>` | `lhs:local_index, cmp:cmp_op, rhs:i32, target:body_offset` | false fallthrough | `sub_459450(1)`：比较成立则 `ip=target`，否则 `ip += 12`。 |
| `0x0008` | `JCC_LOCAL_LOCAL_ELSE` | `08 00` | 13 bytes / `<op,u8 lhs,u8 cmp,u8 rhs,u32 true,u32 false>` | `lhs:local_index, cmp:cmp_op, rhs:local_index, true:body_offset, false:body_offset` | two-way branch | `sub_459540(0)`：比较成立读 `+5` target，否则读 `+9` target。 |
| `0x0009` | `JCC_LOCAL_IMM32_ELSE` | `09 00` | 16 bytes / `<op,u8 lhs,u8 cmp,i32 rhs,u32 true,u32 false>` | `lhs:local_index, cmp:cmp_op, rhs:i32, true:body_offset, false:body_offset` | two-way branch | `sub_459540(1)`：比较成立读 `+8` target，否则读 `+12` target。 |
| `0x000A` | `LOOP_DEC_JNZ` | `0A 00` | 7 bytes / `<op,u8 counter,u32 target>` | `counter:local_index, target:body_offset` | none | `sub_459610`：若 `locals[counter] > 0`，先递减再跳到 target；否则 `ip += 7`。 |
| `0x000B` | `JMP` | `0B 00` | 6 bytes / `<op,u32 target>` | `target:body_offset` | none | `sub_459670`：无条件 `ip = target`。 |
| `0x000C` | `INC_LOCAL` | `0C 00` | 3 bytes / `<op,u8 local>` | `local:local_index` | none | `sub_4596A0(12)`：`locals[local]++`。 |
| `0x000D` | `DEC_LOCAL` | `0D 00` | 3 bytes / `<op,u8 local>` | `local:local_index` | none | `sub_4596A0(13)`：`locals[local]--`。 |
| `0x000E` | `BITNOT_LOCAL` | `0E 00` | 3 bytes / `<op,u8 local>` | `local:local_index` | none | `sub_4596A0(14)`：`locals[local] = ~locals[local]`。 |
| `0x000F` | `NEG_LOCAL` | `0F 00` | 3 bytes / `<op,u8 local>` | `local:local_index` | none | `sub_4596A0(15)`：`locals[local] = -locals[local]`。 |
| `0x0010` | `ADD_LOCAL_LOCAL` | `10 00` | 4 bytes / `<op,u8 dst,u8 rhs>` | `dst:local_index, rhs:local_index` | paired with `0x0011` | `sub_459720`：`locals[dst] = locals[dst] + locals[rhs]`。 |
| `0x0011` | `ADD_LOCAL_IMM32` | `11 00` | 7 bytes / `<op,u8 dst,i32 rhs>` | `dst:local_index, rhs:i32` | paired with `0x0010` | `sub_459720`：加法，右操作数为立即数。 |
| `0x0012` | `SUB_LOCAL_LOCAL` | `12 00` | 4 bytes | `dst:local_index, rhs:local_index` | paired | `locals[dst] = locals[dst] - locals[rhs]`。 |
| `0x0013` | `SUB_LOCAL_IMM32` | `13 00` | 7 bytes | `dst:local_index, rhs:i32` | paired | `locals[dst] = locals[dst] - imm32`。 |
| `0x0014` | `MUL_LOCAL_LOCAL` | `14 00` | 4 bytes | `dst:local_index, rhs:local_index` | paired | `locals[dst] = locals[dst] * locals[rhs]`。 |
| `0x0015` | `MUL_LOCAL_IMM32` | `15 00` | 7 bytes | `dst:local_index, rhs:i32` | paired | `locals[dst] = locals[dst] * imm32`。 |
| `0x0016` | `DIV_LOCAL_LOCAL` | `16 00` | 4 bytes | `dst:local_index, rhs:local_index` | paired | 整数除法；除数为 0 时结果为 0。 |
| `0x0017` | `DIV_LOCAL_IMM32` | `17 00` | 7 bytes | `dst:local_index, rhs:i32` | paired | 整数除法；除数为 0 时结果为 0。 |
| `0x0018` | `MOD_LOCAL_LOCAL` | `18 00` | 4 bytes | `dst:local_index, rhs:local_index` | paired | 取模；除数为 0 时结果为 0。 |
| `0x0019` | `MOD_LOCAL_IMM32` | `19 00` | 7 bytes | `dst:local_index, rhs:i32` | paired | 取模；除数为 0 时结果为 0。 |
| `0x001A` | `AND_LOCAL_LOCAL` | `1A 00` | 4 bytes | `dst:local_index, rhs:local_index` | paired | 按位与。 |
| `0x001B` | `AND_LOCAL_IMM32` | `1B 00` | 7 bytes | `dst:local_index, rhs:i32` | paired | 按位与。 |
| `0x001C` | `OR_LOCAL_LOCAL` | `1C 00` | 4 bytes | `dst:local_index, rhs:local_index` | paired | 按位或。 |
| `0x001D` | `OR_LOCAL_IMM32` | `1D 00` | 7 bytes | `dst:local_index, rhs:i32` | paired | 按位或。 |
| `0x001E` | `XOR_LOCAL_LOCAL` | `1E 00` | 4 bytes | `dst:local_index, rhs:local_index` | paired | 按位异或。 |
| `0x001F` | `XOR_LOCAL_IMM32` | `1F 00` | 7 bytes | `dst:local_index, rhs:i32` | paired | 按位异或。 |
| `0x0020` | `EVAL_EXPR` | `20 00` | variable / expression packet | `dst:local_index, expr_stream` | expression sub-opcodes `6..10` for `+,-,*,/,%` | `sub_459880` 解析表达式包并写回目标 local。 |
| `0x0021` | `PUSH_LOCALS` | `21 00` | 2 bytes | none | stack op | `sub_459AB0` 将 50 个 local 依次压入 VM 栈，`ip += 2`。 |
| `0x0022` | `POP_LOCALS` | `22 00` | 2 bytes | none | stack op | `sub_459B00` 从 VM 栈弹回 50 个 local，`ip += 2`。 |
| `0x0023` | `CALL_ENTRY` | `23 00` | 3 bytes / `<op,u8 entry_index>` | `entry_index:u8` | call stack | `sub_459B60`：压入返回地址 `ip+3`，跳到 `entry[entry_index]-1`，若 entry 为空报 Script Call Error。 |
| `0x0024` | `RET` | `24 00` | 2 bytes | none | call stack | `sub_459BC0`：弹出返回地址到 `ip`，恢复上一 `dword_587A08`。 |
| `0x0025` | `WAIT_FRAMES` | `25 00` | 4 bytes / `<op,i16 frames>` | `frames:i16` | state=2 yield | `sub_459BE0` 设置 `state=2`，每帧 `sub_459100` 计数到 frames 后恢复运行。 |
| `0x0026` | `WAIT_TIME_MS` | `26 00` | 4 bytes / `<op,i16 ms>` | `milliseconds:i16` | state=3 yield | `sub_459C40` 设置 `wake_time=timeGetTime()+ms` 与 `state=3`，到时由 `sub_459130` 恢复运行。 |
| `0x0027` | `YIELD_NOP` | `27 00` | 2 bytes | none | frame yield | `sub_459CA0`：`ip += 2`，并让 `sub_459150` 返回 0，停止本帧连续执行。 |
| `0x0028` | `LOAD_SDT` | `28 00` | `3 + len` bytes / `<op,u8 len,bytes filename>` | `filename:byte_string` | cross-script load | `sub_459010` 读长度与文件名，调用 `sub_458FD0(filename)` 重新加载脚本。 |

'''

external_section = '''### 6.2 外部命令 opcode 全表：`0x0040..0x0108`

外部命令的**清晰命名**不再依赖 handler 猜测，而是直接来自 EXE 命令名表。该表位于内存导出 `0x53AA30..0x53B070`，每项 8 字节：

```c
struct CommandNameEntry {
    char *name;
    uint32_t opcode;
};
```

因此下表的“命名”列为高置信命令原名；即使某些 opcode 在 `sub_4101C0` 中没有显式 case，也仍可由命令表和 `byte_53B080` 参数描述表获得稳定的反汇编名称与长度规则。没有显式 case 的命令在当前 EXE 运行分发中走 default 路径，仅执行通用参数后处理 `sub_410F00` 并返回对应 `return_flag`。

参数缩写：

| 缩写 | 对应描述码 | 含义 |
|---|---:|---|
| `u8` | `1` | 单字节参数 |
| `typed_i32` | `2` | `mode + i32/变量引用/字符串数值`，见 `sub_410C00` |
| `str8` | `3` | `u8 len + bytes` |
| `str16` | `4` | `u16 len + bytes` |
| `out_local` | `5` | 局部变量索引，执行后由 `sub_410F00` 写回 |
| `cmp_expr` | `6` | `local_index, cmp_op, typed_i32` 比较表达式 |
| `u8_alt` | `7` | 与 `u8` 同 reader，命令私有语义 |
| `u16` | `8` | 双字节参数 |
| `u16_alt` | `9` | 与 `u16` 同 reader，命令私有语义 |

''' + '\n'.join(rows) + '\n\n'

text = re.sub(r'### 5\.2 `sub_458EF0` 数值读取约定\n.*?(?=### 5\.3 )', new_52, text, flags=re.S)
text = re.sub(r'### 6\.1 内置 VM opcode：`0x0001\.\.0x0028`\n.*?(?=### 6\.2 )', builtin_section, text, flags=re.S)
text = re.sub(r'### 6\.2 外部命令 opcode：`0x0040\.\.` 高置信集合\n.*?(?=## 7\.)', external_section, text, flags=re.S)
text = text.replace(
    '- `sub_4593B0`、`sub_459410`、`sub_459610`、`sub_459670`、`sub_459A90`、`sub_459AE0`、`sub_459B60`、`sub_459BC0`、`sub_459BE0`、`sub_459C40` 的精确格式。\n- `sub_4101C0` 中全部外部命令的语义命名。\n- 从内存 dump 自动抽取完整 `byte_53B080` 参数描述表与 `byte_53B08F` 返回策略表。',
    '- `EVAL_EXPR` (`0x0020`) 内部表达式 packet 的所有子格式仍需继续拆细到子 opcode 表。\n- 外部命令已按 EXE 命令名表完成高置信命名；部分 default/no explicit handler 命令仍需在样本覆盖阶段确认是否实际出现。\n- `byte_53B080` 参数描述表已能自动抽取；`return_flag/aux` 的运行期影响还需结合更多样本统计。'
)

md.write_text(text, encoding='utf-8')
print(f'updated {md} with {len(cmds)} external command rows')
