# asm.txt 格式规范

`disassembler.py` 与 `assembler.py` 之间的中间表示。设计目标是**双向无损**：
同一份 asm 文本汇编回去必须与原 CBOR 载荷逐字节相同。

实测结论（151 个载荷全覆盖）：

| 项目 | 结果 |
| --- | --- |
| 反汇编 → 汇编 → 比对 | 151 一致 / 0 不一致 / 0 出错 |
| 载荷 → asm → 载荷 → 封包 → build 目录 | 154 个文件全部 sha256 相同 |
| asm 文本合计 | 14.4 MiB（utf-8），15.8 MiB（cp932，占位符更多） |

---

## 1. 文件结构

```
.payload "script"          ; 载荷类型，仅供人看
.encoding "utf-8"          ; 字符串编码，汇编时缺省从这里取
.keyorder [...]            ; 仅当顶层键序不是默认升序时出现

.<字段> <值>               ; 顶层标量/数组/映射字段

.nodes                     ; 块伪指令，成对出现
...
.end
```

顶层是一个 CBOR 映射，每个键一条 `.<键> <值>`。默认键序是"整数键在前、
各自升序"，与实测的全部载荷一致；两个字体载荷的键序不同，靠 `.keyorder`
显式记下来。

### 1.1 能推导出来的字段不写

脚本载荷有 22 个顶层字段，其中大半是常量或别处的函数，写出来只是噪音：

| 字段 | 为什么不写 |
| --- | --- |
| `version` `language` `entry` | 全语料恒为 `1` / `"ja"` / `0` |
| `const_*` `var_*` `variants` | 7 个数组全语料恒为空 |
| `label_values` | 节点下标，汇编时按 `label_keys` 逐个查标签重算 |
| `label_keys` | 若正好是 `.nodes` 里标签的出现顺序就不写（实测 69/140） |
| 角色表的 15 列 | 见 5.1 |

汇编器按 `opcodelist.SCRIPT_DEFAULTS` 补回。`label_keys` 之所以要判断：71 个
脚本的顺序另有讲究（65 个有多个标签落在同一节点），那就照原样写出来。

于是一个脚本的头部通常只剩两行 `.payload` / `.encoding` 加一行 `.name`。省略
一律遵循同一条纪律：**反汇编器当场用汇编侧的补全函数复核一遍，对不上就退回
全量输出** —— 省的只是噪音，永不丢字节。

## 2. 值语法

| 形式 | 含义 |
| --- | --- |
| `123` `-1` `0x1F` | 整数，十进制或十六进制，可用 `_` 分组 |
| `#1352AE87` | 32 位标识符（FNV-1a32 哈希），和整数等价，只是写法上区分开 |
| `"文本"` | 字符串，规则见第 3 节 |
| `[a b c]` | 数组，逗号可省 |
| `{k: v, k2: v2}` | 映射，键只能是整数或字符串 |
| `N*值` | 重复 N 次，长并行数组用它压缩 |
| `blob "文件名"` | 外置字节块，见第 6 节 |
| `null` `true` `false` | CBOR 的 simple value |

括号没闭合时自动续行，所以长数组可以折成多行。`;` 起注释到行尾。

## 3. 字符串与占位符

只有两个转义有意义：

| 写法 | 字节 |
| --- | --- |
| `\\` | 一个真实反斜杠 `0x5C` |
| `\n` | 换行 `0x0A`（本引擎的换行标记就是裸 LF） |
| `{{XX}}` | 一个原始字节 |
| `{{XX:XX:XX}}` | 一个字符的多个原始字节 |

需要占位符的字节：控制字符、双引号、`{{` 的首字符、代理逃逸出的裸字节、
Unicode 私用区，以及**在 `.encoding` 指定的编码下无法表示的字符**。全角空格
等能正常显示的字符保持原样。

占位符直接落原始字节、绕过编码，所以编码选错也不会丢字节：用 `cp932`
反汇编的 asm 可以用 `utf-8` 汇编回去，结果照样逐字节一致（已验证）。

**本工具的默认编码是 `utf-8`，不是 CLAUDE.md 里的 `cp932`。** 原因是本作
CBOR 里的字符串本身就是 UTF-8，实测有 26532 条字符串无法用 cp932 表示、
3960 条无法用 gbk 表示；用 cp932 只是把它们全变成占位符，可读性反而更差。
仍可用 `--encoding cp932` 切回去。

## 4. 指令与标签

参数写成 `名字=值`，顺序随意，等于缺省值的省略不写。

```
label_0F1749DB:          ; 本 scene 声明的标签（label_keys 里的哈希）
loc_00000000:            ; 节点下标标签，跳转落点用

    show        layer=#1352AE87 asset=#60733A1D as=bg  ; images/bg/ＢＧ空曇り.webp
    show        layer=#1252ACF4 asset=#5C4072E9 x=194 z=1000 as=layered clip=0,0,385,701
        .part #5C4072E9              ; images/fg/千景私服１特大.webp
        .part #B262F57C at 104,142   ; images/fg/千景小頬特大.webp
    say         speaker=#12860CB0 text=#9E534AF4 voice=#D5048D47 line_id=#08C976F9
                                 ; 響子：「ええ♪ ……」  (kyo0011.ogg)
    jmp_if_not  flag=#2D50DC24 kind=int rhs=1 node=loc_00000123
    choice
        .option #1A2B3C4D -> loc_00000200  ; 选项文本
        .option #5E6F7A8B -> loc_00000280
```

规则：

* 标签定义前必须有一个空行（文件首个可例外），格式 `名字:`，不缩进。
* 指令缩进 4 空格，助记符左对齐到 12 列。
* **缺省值省略。** `show` 的 `alpha` 恒 255、`play_sound` 的音量/淡入/音高恒
  1000，印出来只是噪音。汇编时按 `opcodelist.ARG_DEFAULTS` 补回；参数个数也
  由此确定（`jmp` 写了 `label=` 就是 2 个参数，没写就是 1 个）。反汇编器每条
  指令都会用 `fill_args` 复核一遍，对不上就退回全量输出。
* 枚举参数写助记词：`kind=int|fixed|raw|flag_ref`，`op=eq|ne|lt|le|gt|ge`。
  表里没有的值退回数字。
* 缩进 8 空格的 `.part` / `.option` 是**上一条指令的续行**，不是独立指令。
* 跳转目标一律写标签，绝不写裸下标。插入或删除指令后，汇编器按标签重算
  所有落点与 `label_values`，不会错位（已用插入一条 `wait` 验证：节点数
  721→722，全部跳转目标自动 +1）。
* `far_XXXXXXXX` 是跨 scene 跳转的**全局标签键**（哈希，不是本地下标），
  落点在别的 scene，本文件内无从解析，故原样保留数值。
* `loc_<节点总数>` 是合法标签，表示"跳到场景末尾即结束"。实测 210 处这样用。
* `;` 注释里的路径、台词是由哈希反查出来的**可读提示**，不参与重建，改了
  也没影响。注释里绝不出现原始十六进制字节序列。

### 4.1 show 的精灵布局

`show` 尾部挂着一个 14/15/16 项的嵌套数组，不进 `Command` 的整数参数槽，
由处理器自行迭代（详见 `vm_analysis.md` §4.3）。其中 8 个槽在全部 10172 条
`show` 里恒为同一值，槽 1 由槽 0 唯一决定，都不含信息，所以拆成具名字段：

| 字段 | 槽 | 含义 |
| --- | --- | --- |
| `as=bg\|frames\|layered` | 0 | 精灵类型。槽 1 随之确定 |
| `flags=N` | 2 | 缺省 17；只有 3 条是 9 |
| `.part` 子行 | 3 | 部件表，`#资源哈希` 或 `#资源哈希 at x,y` |
| `sheet=a,b,c` | 5 | 帧表，与 `as=frames` 一一对应 |
| `clip=x,y,w,h` | 11 | 裁剪矩形 |
| `tint=0xRRGGBB` | 末项 | 缺省白（不着色） |
| `anim=none` / `style=none` | 14/末项 | 数组短于 16 项时出现，共 89 条 |

部件表的首项恒等于 `asset` 参数本身（5865/5865），是底图自己，所以第一个
`.part` 不写；汇编时自动补回。极少数不这样的用 `base=none` 标出来。

具名形式与原数组严格互逆（`sprite_decode` / `sprite_encode`），反汇编器当场
用 `encode` 复核 `decode` 的结果，对不上就退回原始 `.layout [...]` 数组 ——
所以永不丢字节，本作全部 10172 条都走了具名路径。手写 `.layout` 仍然合法，
但不能和具名字段混用。

## 5. 结构化块

| 块 | 用于 | 一行的形状 |
| --- | --- | --- |
| `.nodes` … `.end` | script | 指令 / 标签 / `.node <原始数组>` |
| `.speakers` … `.end` | script | 见 5.1 |
| `.paths` … `.end` | assets | `"路径" 哈希 采样数 循环起点 循环长度` |
| `.table` / `.pieces` … `.end` | strings | `键: 值` |

这些块把并行数组转置成行，一行一个条目。汇编器按同样的列顺序还原成原数组。

`.node` 是逃生口：未定义 opcode 或形状怪异的节点原样写成数组，配
`--allow-unknown` 使用。

### 5.1 角色表的三种写法

载荷里角色表是 16 个等长数组（6 个一对一 + 2 个每角色 5 项 + …）。其中只有
`speaker_keys` 是真数据，其余 15 列全是它的函数：

| 列 | 取值 |
| --- | --- |
| `speaker_groups` / `voice_group_keys` | = `speaker_keys` |
| `speaker_colors` | 恒 `0xFFFFFFFF` |
| `speaker_fonts` / `speaker_screens` | 恒 0 |
| `speaker_portraits` / `speaker_voice_portraits` | 每角色 5 项，全 0 |

所以按冗余程度分三档，反汇编器自动挑最省的一档：

```
.speakers include "speakers.txt"   一份名册被多个脚本共用（本作 140 个全都一样）
.speakers … .end 每行一个 #键       名册独此一份，但 15 列都可推导
.speakers … .end 每行 6|5|5 列      15 列里有不合规则的值，只能写全
```

共享文件一行一个 `#键`，注释里配人名。它固定 UTF-8，与 `--encoding` 无关 ——
里面只有哈希是数据，人名在注释里，不参与重建。单独反汇编一个脚本时，只有输出
目录里已存在内容一致的共享文件才会引用它，否则内联写全，保证 asm 始终自洽。

## 6. 外置字节块

两个字体载荷各有一个 16 MiB 的 `pixels` 字节块（SDF 图集）。塞进文本毫无
意义，所以写成 `.pixels blob "main.font.pixels.bin"`，字节原样落在 asm
同目录的 `.bin` 文件里。汇编时按文件名读回。

## 7. 命令行

```bash
# 反汇编：单文件、多文件、或整个目录
python vetool/disassembler.py <输入...> [-o 输出] [--encoding utf-8]
                                        [--pkg-dir 反查目录] [--allow-unknown]

# 汇编
python vetool/assembler.py <输入...> [-o 输出] [--encoding utf-8] [--as-cbor]

# 零突变验证
python vetool/verify_roundtrip.py [载荷目录] [--work 中间目录] [--keep]
```

拖放：把 `.cbor` 拖到 `disassembler.py` 上得到 `<名字>.asm.txt`；把
`.asm.txt` 拖到 `assembler.py` 上得到 `<名字>.rebuild`。拖目录也行。
`--as-cbor` 让输出用 `.cbor` 后缀，可直接盖回 `_packages` 再用
`vetool.py repack` 封包。

输出目录里除了每个载荷一个 `.asm.txt`，还可能有两类附属文件：字体的
`*.pixels.bin`（第 6 节）和共享角色名册 `speakers.txt`（5.1）。汇编时两者都
要和 asm 放在同一目录。

## 8. 零突变验证流程

```bash
# 单个文件
python vetool/disassembler.py unpacked/_packages/scr_007.script.cbor -o t.asm.txt
python vetool/assembler.py t.asm.txt -o t.rebuild
cmp t.rebuild unpacked/_packages/scr_007.script.cbor     # 无差异

# 全部 151 个载荷
python vetool/verify_roundtrip.py
# -> 一致 151 / 不一致 0 / 出错 0

# 一路走到 APK 里的 build 目录
python vetool/assembler.py <asm 目录> --as-cbor -o <work>/_packages
python vetool/vetool.py repack <work> <out> --src base/assets/build
# -> 154 个文件全部 sha256 相同
```

正确性的依据分两层：`ve_cbor.dumps(ve_cbor.loads(x)) == x` 对全部载荷成立
（CBOR 编解码本身无损，头部用最短形式，键序按出现顺序不重排），因此
"asm ↔ 值树互逆"就等价于"asm → 二进制逐字节一致"。上面的验证同时覆盖了
这两层。

## 9. 错误处理

汇编器对下列情况报错中止，不产生半成品：

* 未定义的助记符
* 参数个数超过 8（引擎 `Command` 只有 8 个整数槽）或超过该指令声明的参数数
* 引用了未定义的标签、`label_keys` 里的哈希在 `.nodes` 里没有对应标签
* 字符串未闭合、占位符格式非法（非两位十六进制、缺 `}}`）
* 未定义的转义（只允许 `\\` 和 `\n`）
* 参数不写成 `名字=值`、参数名不属于该指令、同一参数重复赋值
* 没有缺省值的参数缺失（如 `show` 少了 `layer=`）
* `.part` / `.option` / `.layout` 放在了不带它们的指令后面
* `.layout` 与具名精灵字段混用
* `.keyorder` 与实际字段不匹配
* 找不到 `blob` 引用的外置文件
* 找不到 `.speakers include` 引用的共享名册，或它格式非法 / 没有角色键
* `.speakers` 块里名册行（一个 `#键`）和完整列行（`6|5|5`）混用

反汇编器遇到未记录的 opcode 默认报错并打印节点内容，提示补进
`ve_opcodes.py`；加 `--allow-unknown` 才降级成 `.node`。
