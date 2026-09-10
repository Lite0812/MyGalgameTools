# VersaEngine

- 引擎：VersaEngine
- 测试游戏：《ままごと ～ママとないしょのエッチしましょ～》手机移植版

## 文件

| 文件 | 用途 |
| --- | --- |
| `vetool.py` | 统一命令行入口，用于处理 `game.vseal`、VPK、PAK、脚本和文本。 |
| `ve_archive.py` | 解析、解包和重建 VPK/PAK 归档。 |
| `ve_cbor.py` | 读取和写入 VersaEngine 使用的 CBOR 数据。 |
| `ve_crypto.py` | 实现归档与脚本的加解密。 |
| `ve_format.py` | 定义 VSEAL、VPK、PAK 及脚本公共结构。 |
| `ve_lz4.py` | 处理引擎使用的 LZ4 压缩数据。 |
| `ve_opcodes.py` | 定义脚本 Opcode 与操作数。 |
| `ve_text.py` | 提取和回填脚本文本。 |
| `disassembler.py` | 将 CBOR 脚本反汇编为可编辑文本。 |
| `assembler.py` | 将反汇编文本重新汇编为 CBOR 脚本。 |
| `opcodelist.py` | 汇编器与反汇编器使用的 Opcode 表。 |
| `verify_roundtrip.py` | 检查脚本反汇编后重新汇编的一致性。 |
| `archive_analysis.md` | VPK/PAK 归档结构分析。 |
| `asm_format.md` | 可编辑汇编文本格式说明。 |
| `vm_analysis.md` | VersaEngine 脚本 VM 分析。 |
| `VersaEngine-原说明.md` | 工具命令和文件格式的详细说明。 |
