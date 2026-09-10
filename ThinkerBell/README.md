# ThinkerBell / 铃铛社

- 引擎：ThinkerBell
- 测试游戏：《淫妖蟲 獄》、《淫妖蟲 外伝1》、《淫妖蟲 外伝2》、《新訳 淫妖蟲 特典 桜花菊花編》、《HIDAMARI》

## 文件

| 文件 | 用途 |
| --- | --- |
| `script-tools/disassembler.py` | 反汇编 ThinkerBell/HIDAMARI 脚本。 |
| `script-tools/assembler.py` | 将反汇编文本重新汇编为游戏脚本。 |
| `script-tools/opcodelist.py` | 定义脚本 VM 的 Opcode 与参数。 |
| `script-tools/thinkerbell_dat.py` | 列出、解包和重建 ThinkerBell DAT 归档。 |
| `script-tools/ThinkerBell_VM_OPCODE_精确定义.md` | ThinkerBell 脚本 VM 和 Opcode 说明。 |
| `docs/HIDAMARI_vm_analysis.md` | HIDAMARI 脚本 VM 分析。 |
| `docs/HIDAMARI_VM_OPCODE_精确定义.md` | HIDAMARI Opcode 详细定义。 |
| `runtime-patch/` | 通过 WinMM 代理 DLL 从 `patch` 目录优先加载散文件。 |
