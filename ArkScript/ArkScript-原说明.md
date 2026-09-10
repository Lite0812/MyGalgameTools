# ArkScript 反汇编/汇编工具

## 引擎信息
- **引擎**: ArkScript (月の守 / tkm.exe)
- **文件格式**: `.bin` = 12字节头(double版本2.2 + u32保留) + 字节码
- **字符串加密**: 逐字节取反 (~byte / XOR 0xFF)
- **编码**: CP932 (Shift-JIS)
- **VM架构**: 栈式虚拟机, 48个opcode

## 使用方法

### 反汇编
```
python disassembler.py seen01.bin -o seen01.asm.txt
```

### 汇编
```
python assembler.py seen01.asm.txt -o seen01.rebuild
```

### 验证
```
md5sum seen01.bin seen01.rebuild  # 必须完全一致
```

## 验证结果
- **28/28** .bin 文件 round-trip bit-perfect ✓
- 总文件大小: 1,851,375 字节
- 总指令数: ~198,000 条
- 总字符串: 20,944 个
- 对话文本: ~20,042 条

## 跳转标签系统
- 所有绝对偏移跳转 (0x70/0x71/0xA0/0xA2) 已标签化
- 修改字符串长度后, 汇编器自动重算所有标签偏移
- 实现方案B (无截断, 偏移自动修复)

## 翻译工作流

### 1. 提取文本
```bash
# 单文件
python text_extract.py seen01.bin -o seen01.json

# 批量 (整个data目录)
python text_extract.py data/ -o json_output/
```

### 2. GalTransl 翻译
将 JSON 文件送入 GalTransl 进行翻译.

### 3. 注入翻译
```bash
# 单文件
python text_inject.py seen01.bin seen01_translated.json -o seen01.bin.new

# 批量
python text_inject.py data/ translated_json/ -o output/
```

### JSON 格式
```json
{
  "id": "seen01/0001",
  "name": "比女",
  "pre_jp": "比女\n「とうさまっ、今夜も何かお話をしてくださいますか？」",
  "message": "「とうさまっ、今夜も何かお話をしてくださいますか？」"
}
```

翻译时修改 `message` 字段（台词）和可选的 `name` 字段（角色名）。

## 文本统计
| 类型 | 数量 |
|------|------|
| 对话 (角色名+「台词」) | 7,741 |
| 选项/旁白 (无括号) | 1,488 |
| 需翻译总量 | **9,229** |
| 路径/标识符 (不翻译) | 11,715 |

## 编码注意事项
当前工具默认使用 CP932 编码。中文翻译需要配合字体映射方案
（CP932→JIS映射自定义字体），否则简体中文字符无法编码。
这是字体/编码层面的工作，不影响工具本身的正确性。
