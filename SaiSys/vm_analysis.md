# Dual Colors SSB VM 分析

本文件是 `Script\Code.ssb` 与 `Script\Data.ssb` 的 VM 分析基线，依据 `SaiSys.exe` 的反汇编和反编译结果整理。文件只记录当前静态证据能证明的行为；对宿主对象的高层业务语义不做臆测。

## 1. 文件与加载模型

| 文件 | 原始大小 | VM 中的用途 | 变换 |
|---|---:|---|---|
| `Code.ssb` | 487524 bytes = 121881 dwords | 32 位字节码流 | 无 |
| `Data.ssb` | 768620 bytes | 数据/字符串表及运行时数据 | 读入后每字节 `XOR 0xAA` |

启动链为 `TMainForm_FormCreate` -> `sub_40AE6C` -> `sub_408AB0` -> `sub_402F40`。

- `sub_402F40` 中硬编码了 `Script\\Code.ssb` 与 `Script\\Data.ssb`。
- `Code.ssb` 通过 `sub_40A8B0` 加上 `ExtractFilePath(ParamStr(0))`，即相对 exe 目录打开，然后一次性读取到 `a1[3119]`。
- `Data.ssb` 直接传给 `TMemoryStream.LoadFromFile("Script\\Data.ssb")`，随后调用 `sub_40A708` 对流缓冲区逐字节异或，再复制到 `a1[3118]`。
- 反编译器将 `LoadFromFile` 误标成 `Ibsql::TIBXSQLVAR::LoadFromFile`，实际行为是 Delphi 流加载。

证据：`export-for-ai/decompile/402F40.c`、`40A8B0.c`、`40A708.c`。

## 2. VM 状态与解码

VM 是 32 位、小端序、定长 4 字节单元的栈机，不是寄存器机，也不是变长指令流。

关键字段（以 `sub_4081AC` / `sub_402F40` 的对象索引表示）：

| 字段 | 含义 |
|---|---|
| `a1[3118]` (`0x30B8`) | 解码后的 Data 缓冲区 |
| `a1[3119]` (`0x30BC`) | Code dword 缓冲区 |
| `a1[3120]` (`0x30C0`) | PC，单位是 dword 索引 |
| `a1[3091]` (`0x304C`) | Data 缓冲区长度（字节） |
| `*a1` | 主操作数栈对象 |
| `a1[1]` | 次级栈对象，保存返回 PC/控制数据 |

`sub_40ACA8(stack, value)` 为 push，`sub_40AD4C(stack)` 为 pop。栈操作顺序在下表中按实际 pop 顺序书写，`pop1` 表示第一次取出的栈顶值。

每次执行 `sub_4081AC`：

```text
w  = u32le(Code[PC * 4 .. PC * 4 + 3])
PC = PC + 1

if (w & 0x80000000) == 0:
    push_s32(w)
else:
    group = w & 0xffff0000
    subop = w & 0x0000ffff
    dispatch(group, subop)
```

因此每条指令固定 4 bytes；操作数不是紧随 opcode 的可变长字段，而是 Code 中后续的普通 dword（最高位为 0），或由前面的指令计算后放入操作数栈。跳转目标以 dword PC 表示。相对跳转以已经自增后的 PC 为基准；调用指令把该返回 PC 压入次级栈。

已实现的主分组为 `0x8000`、`0x8001`、`0x8002`、`0x8004`、`0x8005`、`0x8006`、`0x8007`、`0x8008`、`0x8009`、`0x800A`、`0x800B`、`0x800C`、`0x800D`。`0x8003` 没有对应分派分支；未知高位 opcode 会进入错误路径。

## 3. Opcode 字典

所有下列指令长度均为 4 bytes。`word` 是小端序 dword；当最高位为 1 时，`word & 0xffff0000` 选择主分组，`word & 0xffff` 选择子 opcode。表格统一使用 `Opcode | 命名 | 长度/格式 | 精确定义 | 佐证`；栈操作按实际 `pop` 顺序书写。

### 3.1 `0x8000xxxx`：栈、Data 访问和控制流

实现：`sub_4031DC`。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x80000000` | `PUSH_DATA32` | 4 bytes；`pop index` | push `Data[index]` 的 32 位单元 | `4031DC.c:31-39` |
| `0x80000001` | `STORE_DATA32` | 4 bytes；`pop index,value` | `Data[index] = value` | `4031DC.c:40-47` |
| `0x80000002` | `LOAD_DATA8` | 4 bytes；`pop byte_off,word_index` | push `Data[4*word_index + byte_off]` 的单字节值 | `4031DC.c:48-58` |
| `0x80000003` | `STORE_DATA8` | 4 bytes；`pop byte_off,word_index,value` | 写入 `Data[4*word_index + byte_off] = value & 0xff` | `4031DC.c:59-70` |
| `0x80000004` | `DUP` | 4 bytes；`pop value` | 依次 push `value,value` | `4031DC.c:71-76` |
| `0x80000005` | `REORDER3` | 4 bytes；`pop top,below` | 依次 push `below,top,below` | `4031DC.c:77-86` |
| `0x80000006` | `DROP` | 4 bytes；`pop value` | 丢弃栈顶 | `4031DC.c:87-90` |
| `0x80000007` | `RET_SECONDARY` | 4 bytes；次级栈 pop | 从 `a1[1]` 弹出值并写入 `PC` | `4031DC.c:91-95` |
| `0x80000008` | `SET_VM_ERROR` | 4 bytes；无栈操作数 | 设置 `a1[3082] = 1` | `4031DC.c:96-99` |
| `0x80000009` | `SET_BREAK` | 4 bytes；无栈操作数 | 设置 `a1[3086] = 1` | `4031DC.c:100-103` |
| `0x8000000A` | `JMP_ABS` | 4 bytes；`pop target` | `PC = target`，目标是 Code dword 索引 | `4031DC.c:104-108` |
| `0x8000000B` | `JMP_REL` | 4 bytes；`pop delta` | `PC += delta`，基准是本指令执行后的 PC | `4031DC.c:109-113` |
| `0x8000000C` | `CALL_ABS` | 4 bytes；`pop target` | 将当前 PC 压入次级栈，然后 `PC = target` | `4031DC.c:114-119` |
| `0x8000000D` | `CALL_REL` | 4 bytes；`pop delta` | 将当前 PC 压入次级栈，然后 `PC += delta` | `4031DC.c:120-125` |
| `0x8000000E` | `JNZ_ABS` | 4 bytes；`pop target,cond` | `cond != 0` 时 `PC = target` | `4031DC.c:126-135` |
| `0x8000000F` | `JNZ_REL` | 4 bytes；`pop delta,cond` | `cond != 0` 时 `PC += delta` | `4031DC.c:136-145` |
| `0x80000010` | `JZ_ABS` | 4 bytes；`pop target,cond` | `cond == 0` 时 `PC = target` | `4031DC.c:146-155` |
| `0x80000011` | `JZ_REL` | 4 bytes；`pop delta,cond` | `cond == 0` 时 `PC += delta` | `4031DC.c:156-165` |
| `0x80000012` | `SWAP` | 4 bytes；`pop top,below` | 依次 push `top,below`，交换栈顶两个值 | `4031DC.c:166-174` |
| `0x80000013` | `SCRIPT_ABORT` | 4 bytes；无栈操作数 | 设置 `a1[3087] = 1`，并清零 `a1[3088]` | `4031DC.c:175-181` |
| `0x80000014` | `DROP_SECONDARY` | 4 bytes；`pop count` | 从次级栈丢弃 `count` 个值 | `4031DC.c:182-190` |

### 3.2 `0x8001xxxx`：整数算术、比较和移位

实现：`sub_403554`。二元运算均先弹出栈顶 `a`，再弹出较低的 `b`；表中按该顺序记载。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x80010000` | `ADD` | 4 bytes；`pop a,b` | push `b + a` | `403554.c:57-61` |
| `0x80010001` | `SUB` | 4 bytes；`pop a,b` | push `b - a` | `403554.c:62-66` |
| `0x80010002` | `MUL` | 4 bytes；`pop a,b` | push `a * b` | `403554.c:67-71` |
| `0x80010003` | `DIV` | 4 bytes；`pop a,b` | push `b / a` | `403554.c:72-76` |
| `0x80010004` | `MOD` | 4 bytes；`pop a,b` | push `b % a` | `403554.c:77-81` |
| `0x80010005` | `OR` | 4 bytes；`pop a,b` | push `a \| b` | `403554.c:82-86` |
| `0x80010006` | `AND` | 4 bytes；`pop a,b` | push `a & b` | `403554.c:87-91` |
| `0x80010007` | `XOR` | 4 bytes；`pop a,b` | push `b ^ a` | `403554.c:92-96` |
| `0x80010008` | `NOT` | 4 bytes；`pop a` | push `~a` | `403554.c:97-100` |
| `0x80010009` | `NEG` | 4 bytes；`pop a` | push `-a` | `403554.c:101-104` |
| `0x8001000A` | `CMP_EQ` | 4 bytes；`pop a,b` | push `(a == b)` | `403554.c:105-110` |
| `0x8001000B` | `CMP_NE` | 4 bytes；`pop a,b` | push `(a != b)` | `403554.c:111-114` |
| `0x8001000C` | `CMP_GE_0C` | 4 bytes；`pop a,b` | push `(a >= b)` | `403554.c:115-119` |
| `0x8001000D` | `CMP_GE_0D` | 4 bytes；`pop a,b` | 与 `0x8001000C` 相同，但子 opcode 独立存在 | `403554.c:115-119` |
| `0x8001000E` | `CMP_LT` | 4 bytes；`pop a,b` | push `(a < b)` | `403554.c:120-123` |
| `0x8001000F` | `CMP_LE` | 4 bytes；`pop a,b` | push `(a <= b)` | `403554.c:124-127` |
| `0x80010010` | `MUL_ALT` | 4 bytes；`pop a,b` | push `b * a` | `403554.c:128-132` |
| `0x80010011` | `DIV_ALT` | 4 bytes；`pop a,b` | push `b / a` | `403554.c:133-137` |
| `0x80010012` | `MOD_ALT` | 4 bytes；`pop a,b` | push `b % a` | `403554.c:138-142` |
| `0x80010013` | `CMP_GT` | 4 bytes；`pop a,b` | push `(a > b)` | `403554.c:143-148` |
| `0x80010014` | `CMP_GE` | 4 bytes；`pop a,b` | push `(a >= b)` | `403554.c:149-152` |
| `0x80010015` | `CMP_LT_ALT` | 4 bytes；`pop a,b` | push `(a < b)` | `403554.c:153-157` |
| `0x80010016` | `CMP_LE_ALT` | 4 bytes；`pop a,b` | push `(a <= b)` | `403554.c:158-161` |
| `0x80010017` | `SHR` | 4 bytes；`pop shift,value` | x86 `shr`：push `uint32(value) >> shift` | `403554.c:162-166` |
| `0x80010018` | `SHL` | 4 bytes；`pop shift,value` | x86 `shl`：push `value << shift` | `403554.c:167-171` |
| `0x80010019` | `SAR` | 4 bytes；`pop shift,value` | x86 `sar`：push `int32(value) >> shift` | `403554.c:172-176` |

### 3.3 `0x8002xxxx`：随机、光标/计时和文本显示

实现：`sub_403AFC`。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x80020000` | `RAND_MOD` | 4 bytes；`pop n`，push 结果 | `n == 0` 时 push 0，否则 push `rand() % n` | `403AFC.c:30-36` |
| `0x80020001` | `SHOW_CURSOR` | 4 bytes；`pop enabled` | 根据状态调用 `ShowCursor(1/0)`，并维护 `a1[3100]` | `403AFC.c:37-52` |
| `0x80020002` | `CURSOR_TIMER` | 4 bytes；`pop enabled` | 非零时设置 `a1[3099] = 1` 并记录时间，否则清零 | `403AFC.c:53-63` |
| `0x80020003` | `SET_MAIN_TEXT` | 4 bytes；`pop data_index` | 将 `Data + 4*data_index` 作为字符串设置主窗体文本 | `403AFC.c:67-75` |
| `0x80020004` | `SET_TIMER_VALUE` | 4 bytes；`pop value` | `a1[3104] = value` | `403AFC.c:64-66` |
| `0x80020005` | `READ_TIME` | 4 bytes；无栈操作数 | `a1[3078] = timeGetTime()` | `403AFC.c:76-78` |

### 3.4 `0x8003xxxx`：未分派分组

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x8003????` | `UNDEFINED_GROUP` | 4 bytes；主分组 `0x8003` | `sub_4081AC` 没有 `0x80030000` 分支；执行时进入未知 opcode 错误路径，不得当作普通立即数 | `4081AC.c:37-65,94-97` |

### 3.5 `0x8004xxxx`：调试输出

实现：`sub_403CAC`。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x80040000` | `DEBUG_INT` | 4 bytes；`pop value` | 格式化为十进制并发送到 Debug 窗口 | `403CAC.c:20-23` |
| `0x80040001` | `DEBUG_DATA` | 4 bytes；`pop data_index` | 输出 `Data + 4*data_index` 指向的 C 字符串 | `403CAC.c:24-28` |
| `0x80040002` | `DEBUG_RESPONSE` | 4 bytes；无栈操作数 | 输出当前响应值 | `403CAC.c:29-32` |

### 3.6 `0x8005xxxx`：输入设备和鼠标

实现：`sub_403D78`。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x80050000` | `INPUT_SIZE` | 4 bytes；无栈操作数，push 结果 | 查询输入容器的 size | `403D78.c:23-26` |
| `0x80050001` | `INPUT_BITS` | 4 bytes；无栈操作数，push 结果 | 查询输入容器 bit storage | `403D78.c:27-30` |
| `0x80050002` | `KEY_STATE` | 4 bytes；`pop virtual_key`，push 结果 | 查询指定按键状态 | `403D78.c:31-35` |
| `0x80050003` | `SET_CURSOR_POS` | 4 bytes；`pop y,x` | 将客户区原点转屏幕坐标后加上 `x,y`，调用 `SetCursorPos` | `403D78.c:36-45` |

### 3.7 `0x8006xxxx`：显示模式

实现：`sub_403E60`。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x80060000` | `REBUILD_DISPLAY` | 4 bytes；`pop mode,p2,p3` | `mode != 0` 时读取 `DISPLAY` 的设备能力；调用 `sub_410FB0(a1[4], p3, p2, DeviceCaps, mode != 0)`，随后重建图形对象 | `403E60.c:19-40` |
| `0x80060001` | `GET_DISPLAY_MODE` | 4 bytes；无栈操作数，push 结果 | push `a1[3098]` 的低字节（反编译字段为 `*((BYTE*)a1+12392)`） | `403E60.c:42-48` |

### 3.8 `0x8007xxxx`：存档、文件和场景对象

实现：`sub_403F84`。文件名数组和后缀来自 exe 内部常量；以下只使用静态代码能确认的低层语义。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x80070000` | `LOAD_SAVE_VM` | 4 bytes；`pop save_index` | 构造 `Save\\<name>`；读入后逐字节 XOR `0xAA`，依次恢复 PC、Data 长度、响应/栈长度、Data、次级栈、主栈，并设置 `a1[3113] = 1` | `403F84.c:173-232` |
| `0x80070001` | `STORE_SAVE_VM` | 4 bytes；`pop save_index` | 序列化 PC、Data 长度、两个响应值、Data、次级栈、主栈；整体 XOR `0xAA` 后写入 `Save\\<name>` | `403F84.c:233-289` |
| `0x80070006` | `QUERY_RESOURCE` | 4 bytes；`pop data_index` | 组合 `Data[data_index]` 与内部后缀，调用 `a1[3097]` 虚表偏移 `+80`；返回值非 `-1` 时 push `1`，否则 push `0` | `403F84.c:290-318` |
| `0x80070007` | `WRITE_RESOURCE_STATE` | 4 bytes；`pop data_index` | 以 `Data[data_index]` 构造资源名；必要时通过 `a1[3097]` 虚表创建条目，构造 `Save\\SYS` 流，写入 `VMSF` 标识和 `a1[3098]`，再调用宿主流写入并 XOR `0xAA` | `403F84.c:319-399` |
| `0x80070008` | `RESOURCE_EXISTS` | 4 bytes；`pop data_index` | 组合 `Data[data_index]` 与内部后缀，调用 `a1[3097]` 虚表偏移 `+80`；结果非 `-1` 时 push `1` | `403F84.c:400-427` |
| `0x80070009` | `SET_RESOURCE_INDEX` | 4 bytes；`pop value` | `a1[3089] = value` | `403F84.c:428-430` |
| `0x8007000A` | `SHOW_SAVE_INFO` | 4 bytes；`pop display_arg,save_index` | 创建临时宿主对象；读取 `Save\\<name>` 的 DOS 日期时间，格式化后设置对象文本，再交给 `sub_405890(a1,display_arg,...)` 处理 | `403F84.c:431-569` |
| `0x8007000B` | `GET_SAVE_STATUS` | 4 bytes；无栈操作数，push 结果 | push `a1[3113]`，随后将 `a1[3113]` 清零 | `403F84.c:636-639` |
| `0x8007000C` | `SAVE_EXISTS` | 4 bytes；`pop save_index`，push 结果 | 检查 `Save\\<name>`；文件存在时 push `0`，不存在时 push `1` | `403F84.c:640-677` |
| `0x8007000D` | `GET_SAVE_TIME` | 4 bytes；`pop save_index`，push 6 值 | 读取 DOS 时间。执行顺序为 push `second,minute,hour,day,month,year`；因此执行完成后栈顶依次为 `year,month,day,hour,minute,second` | `403F84.c:570-635` |
| `0x8007000E` | `SCENE_RESOURCE_OP` | 4 bytes；`pop p1,p2` | 调用 `sub_408634(a1, p2, p1)` | `403F84.c:678-682` |
| `0x8007000F` | `LOAD_SAVE_PATH` | 4 bytes；`pop save_index` | 构造 `Save\\<name>` 路径并调用 `sub_408898(a1,path)` | `403F84.c:683-721` |
| `0x80070010` | `SAVE_PATH_OP` | 4 bytes；`pop p1,p2,save_index` | 构造 `Save\\<name>` 路径并调用 `sub_40892C(a1,path,p2,p1)` | `403F84.c:722-760` |

### 3.9 `0x8008xxxx`：GRD、图像/对象宿主调用

实现：`sub_4058D4`。操作数全部来自主栈；表中保留实际宿主函数地址，避免把业务含义误命名。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x80080000` | `OBJECT_DRAW_BASE` | 4 bytes；`pop object_index,p3,p2,p1,aux_index` | 确保 `a1[aux_index+5]` 存在后调用 `sub_4107B4(a1[4],a1[aux_index+5],p1,p2,p3,a1[object_index+1029],0)` | `4058D4.c:180-222` |
| `0x80080001` | `GRD_LOAD` | 4 bytes；`pop object_index,display_arg,data_index` | 用 `Data[data_index]` 定位 `GRD` 文件；创建/更新对象并调用 `sub_405890(a1,object_index,...)` | `4058D4.c:271-582` |
| `0x80080002` | `PIXEL_BUFFER_DRAW` | 4 bytes；`pop object_index,pitch,fill,height,width` | 分配 `height * (4*width)` 字节，将每行填为 `fill`，调用 `sub_405890(a1,object_index,buffer,width,height,4*width,pitch)` | `4058D4.c:583-616` |
| `0x80080003` | `AUX_RELEASE` | 4 bytes；`pop aux_index` | 释放 `a1[aux_index+5]`，然后清零该辅助资源槽 | `4058D4.c:617-633` |
| `0x80080004` | `DRAW_UPDATE_1` | 4 bytes；`pop p4,p3,p2,p1,object_index` | 若对象存在，调用 `sub_41104C(a1[4],a1[object_index+5],p1,p2,p3,p4)` | `4058D4.c:634-651` |
| `0x80080005` | `OBJECT_UPDATE` | 4 bytes；`pop value,object_index` | 若对象存在，调用 `sub_410E20(a1[4],a1[object_index+5],value)` | `4058D4.c:672-686` |
| `0x80080006` | `DRAW_UPDATE_LONG` | 4 bytes；`pop image_index,p9,p8,p7,p6,p5,p4,p3,p2,p1,aux_index` | 若 `a1[image_index+1029]` 存在，确保 `a1[aux_index+5]` 已初始化，然后调用 `sub_410A9C(a1[4],a1[aux_index+5],p1,p2,p3,p4,p5,p6,p7,p8,p9,a1[image_index+1029])` | `4058D4.c:223-262` |
| `0x80080007` | `DRAW_UPDATE_2` | 4 bytes；`pop p6,p5,p4,p3,p2,p1,object_index` | 若对象存在，调用 `sub_411190(a1[4],a1[object_index+5],p1,p2,p3,p4,p5,p6)` | `4058D4.c:652-671` |
| `0x80080008` | `OBJECT_RELEASE` | 4 bytes；`pop object_index` | 对 `a1[object_index+1029]` 调用 `sub_410408` 并清零对象槽 | `4058D4.c:263-270` |
| `0x80080009` | `OBJECT_CREATE_REPLACE` | 4 bytes；`pop object_index,p5,p4,p3,p2,p1` | 调用 `sub_411B50(a1[4],a1[object_index+1029],p1,p2,p3,p4,p5,0)` 并写入对象类型字符串 | `4058D4.c:687-710` |
| `0x8008000A` | `SET_GRD_MODE` | 4 bytes；`pop value` | `a1[3112] = (value != 0)` | `4058D4.c:711-714` |
| `0x8008000B` | `GET_OBJECT_SIZE` | 4 bytes；`pop object_index`，push 2 值 | 有对象时按执行顺序 push 字段 `+12`、字段 `+8`，否则 push `0,0` | `4058D4.c:715-736` |

### 3.10 `0x8009xxxx`：宿主控件/对象

实现：`sub_407120`。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x80090000` | `CONTROL_CREATE_DRAW` | 4 bytes；`pop object_index,arg3,arg2,arg1` | 调用 `sub_40BFAC(a1[object_index+2053],arg1,arg2,arg3,0xFFFFFF)`，再以 `a1[3114]` 调用 `sub_40C98C` | `407120.c:56-65` |
| `0x80090001` | `CONTROL_RELEASE` | 4 bytes；`pop object_index` | 调用 `sub_40C0F4(a1[object_index+2053])` | `407120.c:66-69` |
| `0x80090002` | `CONTROL_SET_DATA_TEXT` | 4 bytes；`pop object_index,data_index` | 将 `Data + 4*data_index` 转字符串后传给 `sub_40C170` | `407120.c:70-79` |
| `0x80090003` | `CONTROL_SET_VALUE_TEXT` | 4 bytes；`pop object_index,value` | 将 `value` 转 WideString 后传给 `sub_40C170` | `407120.c:80-89` |
| `0x80090004` | `CONTROL_GET_VALUE` | 4 bytes；`pop object_index`，push 结果 | push `sub_40C1F4(a1[object_index+2053])` | `407120.c:90-94` |
| `0x80090005` | `CONTROL_ATTACH` | 4 bytes；`pop owner_index,draw_arg,related_index` | 按 owner 的响应/应用对象关系创建或更新 `related_index` 对应对象，并用 `draw_arg` 调用控件绘制方法 | `407120.c:95-124` |
| `0x80090006` | `CONTROL_SET_PARAM_1` | 4 bytes；`pop value,object_index` | 调用 `sub_40C914(a1[object_index+2053],value)` | `407120.c:125-129` |
| `0x80090007` | `CONTROL_SET_PARAM_2` | 4 bytes；`pop value,object_index` | 调用 `sub_40C960(a1[object_index+2053],value)` | `407120.c:130-134` |
| `0x80090008` | `CONTROL_SET_FIELD40` | 4 bytes；`pop value,object_index` | 写入 `a1[object_index+2053] + 40 = value` | `407120.c:135-138` |

### 3.11 `0x800Axxxx`：MIDI

实现：`sub_4074E4`。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x800A0000` | `MIDI_PLAY_1` | 4 bytes；`pop data_index` | 查找 `Midi\\` 下的 Data 文件，找不到时尝试 `a1[3111]` 前缀，成功后调用 `sub_4018B4(...,-1)` | `4074E4.c:48-114` |
| `0x800A0001` | `MIDI_STOP` | 4 bytes；无栈操作数 | 调用 `sub_401928(a1[3])` 停止 MIDI | `4074E4.c:115-117` |
| `0x800A0002` | `MIDI_PLAY_2` | 4 bytes；`pop data_index` | 查找另一组 `Midi\\` 路径，成功后调用 `sub_4018B4(...,0)` | `4074E4.c:118-184` |
| `0x800A0003` | `MIDI_GET_STATE` | 4 bytes；无栈操作数，push 结果 | push `sub_401950(a1[3])` | `4074E4.c:185-188` |
| `0x800A0004` | `MIDI_SET_PARAM` | 4 bytes；`pop value` | 调用 `sub_4019A8(a1[3],value)` | `4074E4.c:189-192` |

### 3.12 `0x800Bxxxx`：WAV

实现：`sub_4079AC`。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x800B0000` | `WAV_PLAY` | 4 bytes；`pop channel,data_index` | 调用 `sub_402090` 更新声道，再从 `Wav\\`（必要时加 `a1[3111]` 前缀）查找并调用 `sub_40202C` | `4079AC.c:50-140` |
| `0x800B0001` | `WAV_STOP` | 4 bytes；`pop channel` | 调用 `sub_402090(a1[3077],channel)` | `4079AC.c:141-144` |
| `0x800B0002` | `WAV_GET_STATE` | 4 bytes；`pop channel`，push 结果 | push `sub_4020BC(a1[3077],channel)` | `4079AC.c:145-149` |
| `0x800B0003` | `WAV_SET_PARAM_1` | 4 bytes；`pop value` | 调用 `sub_402108(a1[3077],value)` | `4079AC.c:150-153` |
| `0x800B0004` | `WAV_SET_PARAM_2` | 4 bytes；`pop value1,value2` | 调用 `sub_402180(a1[3077],value2,value1)` | `4079AC.c:154-158` |
| `0x800B0005` | `WAV_SET_PARAM_3` | 4 bytes；`pop value` | 调用 `sub_402144(a1[3077],value)` | `4079AC.c:159-162` |
| `0x800B0006` | `WAV_SET_PARAM_4` | 4 bytes；`pop value1,value2` | 调用 `sub_4021D8(a1[3077],value2,value1)` | `4079AC.c:163-167` |
| `0x800B0007` | `WAV_PLAY_LOOP` | 4 bytes；`pop channel,data_index` | 新版 VM 的循环 WAV 播放。文件解析与 `WAV_PLAY` 相同，但调用底层播放函数时将循环标志设为 `1`；声道结束后更新函数会自动重新播放。Dual Colors 的 EXE 未实现此子码，妖恋愛奇譚与ミラクルハート实现。 | `ミラクルハート/export-for-ai/decompile/407DD0.c:153-242`；`402394.c:10-30`；`4025B0.c:10-29` |

### 3.13 `0x800Cxxxx`：输入脚本/选择状态

实现：`sub_407FB0`。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x800C0000` | `INPUT_SELECT` | 4 bytes；`pop index` | 写入当前输入索引，并设置状态字节为 `1` | `407FB0.c:18-21` |
| `0x800C0001` | `INPUT_STATE_3` | 4 bytes；无栈操作数 | 将状态字节设为 `3` | `407FB0.c:22-24` |
| `0x800C0002` | `INPUT_GET` | 4 bytes；无栈操作数，push 结果 | 读取当前索引对应表项；索引越界时 push `0` | `407FB0.c:25-32` |
| `0x800C0003` | `INPUT_SET` | 4 bytes；`pop value` | 写入 `a1[3093] = value`，调用 `sub_407DE8` | `407FB0.c:33-36` |
| `0x800C0004` | `INPUT_CLEAR` | 4 bytes；无栈操作数 | 清零状态并调用 `sub_409A60`、`sub_40FB50` 清理 | `407FB0.c:37-41` |

### 3.14 `0x800Dxxxx`：音频/播放对象

实现：`sub_408074`。

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---|---|---|---|---|
| `0x800D0000` | `AUDIO_FIND` | 4 bytes；`pop data_index`，push 结果 | 调用 `sub_415750(a1[3105],Data + 4*data_index)` 并 push 返回值 | `408074.c:20-24` |
| `0x800D0001` | `AUDIO_PLAY_1` | 4 bytes；`pop value` | 先调用 `sub_415B60(...,1)`，再 `sub_415A80(...,value)` | `408074.c:25-29` |
| `0x800D0002` | `AUDIO_STOP` | 4 bytes；无栈操作数 | 调用 `sub_415B1C(a1[3105])` | `408074.c:30-32` |
| `0x800D0003` | `AUDIO_DELAY` | 4 bytes；`pop value` | 调用运行库延时函数，参数为 `1000 * value` | `408074.c:33-36` |
| `0x800D0004` | `AUDIO_PLAY_0` | 4 bytes；`pop value` | 先调用 `sub_415B60(...,0)`，再 `sub_415A80(...,value)` | `408074.c:37-41` |
| `0x800D0005` | `AUDIO_GET_STATE` | 4 bytes；无栈操作数，push 结果 | push `(*(DWORD *)(a1[3105]+500) != 0)` | `408074.c:42-44` |

## 4. 跳转和标签化约束

当前 VM 的跳转操作数是运行时栈值，不是 opcode 内嵌的相对字段：

- 绝对目标必须解释为 Code dword 索引。
- 相对目标以 opcode 执行后已经自增的 `PC` 为基准。
- `0x8000000C/0x8000000D` 把返回 PC 压入次级栈；`0x80000007` 从次级栈取回 PC。
- 反汇编器应先扫描所有 `0x8000000A` 到 `0x80000011` 的数据流，能够证明是目标的立即数才生成标签；不能把任意普通立即数自动当成地址。
- 保存文件中的 PC 也以 dword 索引保存，不能转换成字节偏移后再写回。

## 5. 解码/重建注意事项

1. `Code.ssb` 必须按小端序每 4 bytes 解码，不能按字节搜索 opcode。
2. 普通立即数的判定是最高位为 0；因此负数立即数不能直接编码为普通 immediate，需确认脚本是否通过运算构造。
3. `Data.ssb` 的内存视图是 `raw_byte XOR 0xAA`；重建时必须对整个 Data 文件重新应用同一异或，包含填充和不可打印字节。
4. Save 文件使用同样的 `0xAA` 变换，但 Save 是运行时状态快照，不属于 `Script` 的静态输入。
5. `Data[index]` 的 `index` 是以 4 bytes 为步长的表索引；字符串 opcode 直接把 `Data + 4*index` 当 C 字符串，不能以任意字节偏移替代。Data 地址既可能是直接立即数，也可能由 `base + dynamic_index` 等算术表达式生成；重排 Data 时必须沿栈数据流保留算术来源并重定位 `base`，不能只处理紧邻 Data opcode 的直接立即数。
6. Data dword 也可暂存字符串或资源指针；若 `STORE_DATA32` 写入的值后来经 `PUSH_DATA32` 送入文字、WAV、GRD 等字符串 opcode，写入值的立即数来源同样必须重定位。分析调用入口时不得把跨 `CALL_*` 传递的普通数值参数误判为这种局部指针写入。
7. `0x8003xxxx` 当前未实现；遇到该组必须报告未定义 opcode，而不是按普通数据吞掉。
8. 未识别高位 opcode 应保留其原始 4 bytes 并报告地址，不能猜测长度。由于本 VM 全部定长，错误长度不会产生连锁偏移，但错误的“立即数/指令”分类会改变栈流。

## 6. 证据索引

- 解码主循环：`export-for-ai/decompile/4081AC.c`
- 执行循环：`export-for-ai/decompile/4084A4.c`
- 主/次级栈：`export-for-ai/decompile/40ACA8.c`、`40AD4C.c`
- Code/Data 初始化：`export-for-ai/decompile/402F40.c`
- Data XOR：`export-for-ai/decompile/40A708.c`
- opcode 分组处理器：`4031DC.c`、`403554.c`、`403AFC.c`、`403CAC.c`、`403D78.c`、`403E60.c`、`403F84.c`、`4058D4.c`、`407120.c`、`4074E4.c`、`4079AC.c`、`407FB0.c`、`408074.c`
