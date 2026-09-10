# YOX ADV

- 引擎：YOX ADV
- 测试游戏：《Floating Material》

## 文件

| 文件 | 用途 |
| --- | --- |
| `script_dat_tool.cs` | `script.dat` 工具的命令行入口和文件调度。 |
| `disassembler.cs` | 反汇编 YOX ADV 脚本。 |
| `assembler.cs` | 将反汇编文本重新汇编为 `script.dat`。 |
| `opcodelist.cs` | 定义脚本 VM 的 Opcode 和参数。 |
| `script_dat_tool.csproj` | .NET 工程配置。 |
| `script_dat_tool.exe` | 已构建的命令行工具。 |
| `vm_analysis.md` | YOX ADV 脚本 VM 与文件结构分析。 |
