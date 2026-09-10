# Lambda

- 引擎：Lambda
- 测试游戏：《ま～じゃんコネクト》

## 文件

| 文件 | 用途 |
| --- | --- |
| `disassembler.py` | 将 MBT0 消息表和 SAM 参数表反汇编为可编辑文本。 |
| `assembler.py` | 将反汇编文本重新构建为 MBT0 或 SAM 数据。 |
| `opcodelist.py` | 保存 MBT0/SAM 的结构、记录区与文本转义定义。 |
| `lambda_mbt0_opcode.py` | 保存汇编器与反汇编器共用的伪指令和固定结构常量。 |
| `lambda_dat_tool.py` | 列出、解包、封包和校验 `CLS_FILELINK` DAT 归档。 |
| `mcdat_tool.py` | 使用 TSV 索引无损解包和回包 `CLS_FILELINK` DAT。 |
| `lambda_dat_fast.dll` | 为 DAT 处理提供可选的本地加速。 |
| `lambda_text_tool.py` | 从 MBT0/SAM 提取 JSON 文本并将翻译回填。 |
| `extract_messages.py` | 在反汇编文本与角色名、消息 JSON 之间转换。 |
| `lambda_cls_font_rebuild.py` | 提取、重绘和重建 CLS `FONT26` 位图字体。 |
| `vm_analysis.md` | Lambda 数据结构、文本格式与处理流程分析。 |
