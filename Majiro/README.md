# Majiro

- 引擎：Majiro
- 测试游戏：《3つの催眠》

## 文件

| 文件 | 用途 |
| --- | --- |
| `majiro_tool.py` | MJO 与 MJIL 之间汇编、反汇编的命令行入口。 |
| `majiro_gui.py` | Majiro 脚本批量汇编、反汇编的图形界面。 |
| `majiro_disassembler.py` | 读取 MJO 对象并生成 MJIL。 |
| `majiro_assembler.py` | 将 MJIL 构建为可选加密的 MJO 对象。 |
| `majiro_script.py` | 定义 Majiro 类型、作用域、指令及脚本对象结构。 |
| `majiro_opcodes.py` | Majiro VM Opcode 表。 |
| `majiro_crc.py` | 实现 Majiro CRC32 哈希与脚本代码区异或加解密。 |
| `requirements.txt` | 图形界面所需的 Python 依赖。 |
