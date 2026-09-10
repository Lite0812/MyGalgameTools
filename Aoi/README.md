# Aoi

- 引擎：Aoi
- 测试游戏：《ぼっちな魔王と俺の塔 巨塔edition》、《ウィザーズクライマー》

## 文件

| 文件 | 用途 |
| --- | --- |
| `aoi_archive_tool.py` | 列出、解包和重建 AOIMY01、AOIBX9 与 VFS 归档。 |
| `aoi_mya_disasm.py` | 将 MYA/MYU 二进制脚本反汇编为可编辑文本。 |
| `aoi_mya_asm.py` | 将反汇编文本重新汇编为 MYA/MYU 脚本。 |
| `aoi_mya_asm_gui.py` | MYA/MYU 汇编与反汇编的图形界面。 |
| `aoi_mya_json_tool.py` | 在 MYA/MYU 脚本与 JSON 之间提取、回填文本。 |
| `aoi_mya_json_migrate_tool.py` | 将旧版文本 JSON 转换为当前格式。 |
| `aoi_mya_opcode_common.py` | MYA/MYU 指令、操作数和公共解析定义。 |
| `aoi_mya_opcode_parser.py` | 读取 Opcode 表或扫描脚本中的指令使用情况。 |
| `aoi_mya_select_dump.py` | 将脚本 JSON 中的选择项提取为平铺 JSON。 |
| `aoi_mya_select_inject.py` | 将平铺 JSON 中的选择项回填到脚本 JSON。 |
| `aoi_obj_json_tool.py` | 在 Aoi OBJ 数据文件与 JSON 之间转换。 |
| `aoi_iph_tool.py` | 在 IPH 图像与 PNG 之间转换。 |
| `aoi_iph_meta_fix.py` | 依据原始 IPH 修复补丁图片缺失的元数据。 |
| `BOX_FORMAT_AND_OPCODES.md` | BOX/MYA 文件结构与 Opcode 说明。 |
| `Aoi-原说明.md` | Aoi 工具的详细操作与格式说明。 |
