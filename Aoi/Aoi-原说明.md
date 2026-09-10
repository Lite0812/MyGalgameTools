# AoiArchiveTool

这是一个面向 AOI 系游戏资源逆向、脚本分析与文本回注的工具集合。仓库当前主体已经从早期的 C++ 小工具演进为一套 Python 工具链，覆盖封包解包、脚本反汇编与回编、JSON 文本导出回注，以及一个把这些能力整合起来的 PyQt6 图形界面。

从代码结构看，这个项目主要服务于汉化、脚本研究、资源整理和 Mod 制作等场景，目标不是“只解一个包”，而是把 `box`/`vfs` 档案层和 `txt` 脚本层一起打通。

## 项目能做什么

当前仓库里已经实现了几类核心能力：

- 解包与封包多种归档格式：
  `AOIMY01/Unicode`、`AOIMY01/ANSI`、`AOIBX9/ANSI`、`VFS`
- 自动识别封包格式并输出元数据，便于后续无损回包
- 反汇编 `box` 中的编译后 `txt` 脚本，把字节码转换成可读的 `asm.txt`
- 将 `asm.txt` 再次汇编回原始脚本二进制
- 从脚本中提取人名、对白、选项、部分技能或迷宫相关文本到 JSON
- 根据 JSON 将翻译文本重新回注到脚本
- 提供一个 GUI，把解包、封包、反汇编、汇编、JSON 提取和回注整合到同一界面

需要注意的是，仓库里的 `*.txt` 脚本不是普通明文文本，而是游戏可执行文件解释执行的字节码。项目里的 `aoi_mya_*` 工具就是围绕这套脚本格式建立的。

## 核心文件

根目录下最值得先看的文件如下：

- `aoi_archive_tool.py`
  归档层工具。负责识别、解包、封包 `AOIMY01`、`AOIBX9A`、`VFS`。
- `aoi_mya_opcode_common.py`
  脚本层公共模块。定义 opcode 表、参数解析、反汇编和汇编的核心逻辑。
- `aoi_mya_disasm.py`
  将编译后脚本反汇编成 `*.asm.txt`。
- `aoi_mya_asm.py`
  将 `*.asm.txt` 回编成脚本二进制。
- `aoi_mya_json_tool.py`
  从脚本导出 JSON，或根据 JSON 回注文本。
- `aoi_mya_asm_gui.py`
  PyQt6 图形界面入口，适合不想手敲命令行时使用。
- `BOX_FORMAT_AND_OPCODES.md`
  归档格式与 opcode 逆向笔记，适合想继续扩展脚本支持的人阅读。

## 目录说明

这个仓库不只是源码，还保留了大量样例数据、实验目录和旧版本实现。大致可以这样理解：

- `AoiArchiveTool/`
  早期 C++ 版 archive 工具工程。
- `AoiMYAScriptSimpleTool/`
  早期 C++ 版 MYA 脚本工具工程。
- `AoiMYUScriptSimpleTool/`
  早期 C++ 版 MYU 脚本工具工程。
- `旧实现/`
  旧版 Python 实现，主要用于对照和回溯。
- `export-for-ai/`
  逆向辅助材料，如导出函数、反编译结果、内存段信息。
- `box/`、`box1/`
  解包后的脚本或资源样例目录。
- `box_out/`
  解包产物样例。
- `box_out_asm/`
  反汇编产物样例。
- `box_out_json/`
  JSON 提取产物样例。
- `new/`
  重新生成或回注后的脚本样例。

如果你的目的是直接使用工具，主要只需要关注根目录几个 Python 文件和你自己的输入输出目录即可。

## 运行环境

命令行工具只依赖 Python 标准库。

如果需要图形界面，还需要安装：

- `PyQt6`
- `darkdetect`：可选，用于跟随系统深浅色主题

这是一个明显偏 Windows 的项目，GUI 中还带有 Windows 主题探测逻辑；在 Windows 下使用会更顺手。

## 快速上手

### 1. 解包归档

```powershell
python aoi_archive_tool.py -e box.vfs box_out
```

也可以解包 `AOIMY01` 或 `AOIBX9` 类型的 `box` 文件，工具会自动识别格式。

解包后通常会在输出目录生成一个元数据文件，例如：

- `__aoi_vfs__.json`
- `__aoi_aoimy01__.json`
- `__aoi_aoibx9__.json`

这些文件用于尽量保留原始索引信息，回包时不要随手删除。

### 2. 反汇编脚本

```powershell
python aoi_mya_disasm.py box_out box_out_asm
```

默认脚本编码是 `cp932`。如果目标游戏脚本字符串编码不同，可以显式指定：

```powershell
python aoi_mya_disasm.py box_out box_out_asm --input-script-encoding utf-8
```

### 3. 导出 JSON 文本

```powershell
python aoi_mya_json_tool.py dump box_out box_out_json
```

这一步会把可提取的人名、对白、选项等内容写入 `*.json`，适合交给翻译或脚本整理流程使用。

### 4. 根据 JSON 回注脚本

```powershell
python aoi_mya_json_tool.py inject box_out box_out_json new --copy-extra-files
```

如果某个脚本没有找到对应 JSON，工具会直接复制原脚本到输出目录，避免整个流程中断。

### 5. 将汇编文本回编为脚本

```powershell
python aoi_mya_asm.py box_out_asm rebuilt_bin
```

### 6. 重新封包

```powershell
python aoi_archive_tool.py -p 4 box_out rebuilt.vfs
```

封包模式如下：

- `1`: `AOIMY01/Unicode`
- `2`: `AOIMY01/ANSI`
- `3`: `AOIBX9/ANSI`
- `4`: `VFS`

如果你是先解包再回包，最好保持与原始归档一致的格式。

## GUI 入口

直接运行：

```powershell
python aoi_mya_asm_gui.py
```

界面名称是 `AOI Tools GUI`。它本质上是对命令行功能的封装，适合批量处理和快速试错。

## 项目特点

这个仓库和常见“只提文本”的脚本工具不太一样，特点主要有三点：

- 归档层和脚本层是连通的，不需要在不同工具之间来回切换
- 提供 `asm` 和 `json` 两条编辑路线，既能做低层脚本修改，也能做文本回注
- 保留了较多逆向上下文，后续继续补 opcode、扩展格式时不需要从零开始

## 使用时要注意

- 默认脚本编码是 `cp932`，做中文或其他区域文本回注时要先确认目标游戏编码
- 元数据文件会影响无损回包，尤其是文件名字段和索引顺序
- `VFS` 当前只支持未压缩文件；遇到压缩条目会直接报错
- 项目根目录包含很多样例输出目录，不要把这些目录误认为工具运行必需文件
- 旧版 C++/Python 代码仍有参考价值，但当前主线实现是根目录这套 Python 脚本

## 适合谁看

如果你是下面这几类人，这个仓库会比较有用：

- 想研究 AOI 系游戏 `box`/`vfs` 格式的人
- 想做文本提取、汉化回注的人
- 想直接修改脚本逻辑、跳转、分支和 opcode 参数的人
- 想把已有逆向笔记整理成可复用工具链的人

## 建议阅读顺序

第一次看项目，建议按这个顺序：

1. `README.md`
2. `BOX_FORMAT_AND_OPCODES.md`
3. `aoi_archive_tool.py`
4. `aoi_mya_opcode_common.py`
5. `aoi_mya_disasm.py` / `aoi_mya_asm.py`
6. `aoi_mya_json_tool.py`
7. `aoi_mya_asm_gui.py`

这样会先建立整体认知，再进入脚本格式和实现细节。
