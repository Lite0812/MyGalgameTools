# SHSystem 脚本与归档工具

- 引擎：SHSystem
- 测试游戏：《コイビト遊戯》
- 脚本格式：`SHSysSC`
- 归档格式：`SHS6`、`SHS7`（HXP）

## 文件

| 文件 | 用途 |
| --- | --- |
| `disassembler.py` | 将 `SHSysSC` 二进制脚本反汇编为语义化 `asm.txt`。 |
| `assembler.py` | 将语义化 `asm.txt` 重新汇编为 `SHSysSC` 脚本。 |
| `opcodelist.py` | 定义脚本 Opcode、表达式、字符串参数和编码规则。 |
| `shs_archive.py` | 列出、解包和重建 `SHS6`/`SHS7` HXP 归档。 |
| `vm_analysis.md` | `SHSysSC` 脚本 VM、文件头、Opcode 和重定位规则分析。 |

## 基本用法

```text
python disassembler.py <script>
python assembler.py <script.asm.txt>
python shs_archive.py unpack <archive.hxp> <out_dir>
python shs_archive.py pack <in_dir> <archive.hxp>
python shs_archive.py roundtrip <archive.hxp>
```

字符串参数默认按 `cp932` 编解码；脚本反汇编和汇编可通过 `--encoding` 覆盖。
