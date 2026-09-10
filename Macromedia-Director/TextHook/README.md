# ONE TextHook

- 引擎：Macromedia Director
- 测试游戏：《ONE》

## 文件

| 文件 | 用途 |
| --- | --- |
| `ONE-scripts/frida_text_hook.py` | 使用 Frida 捕获 Win32/Director 绘制的文本并导出 JSONL。 |
| `ONE-scripts/export_translations_cp932.py` | 从捕获结果生成并校验 CP932 翻译表。 |
| `OneTextHook/build_x86.bat` | 构建 32 位 OneTextHook。 |
| `OneTextHook/build_all.ps1` | 批量执行工程构建。 |
| `OneTextHook/OneTextHook.sln` | Visual Studio 解决方案。 |
| `OneTextHook/src/OneTextHook.vcxproj` | Visual C++ 工程配置。 |
| `OneTextHook/src/OneTextHook.vcxproj.filters` | Visual Studio 文件分组配置。 |
| `OneTextHook/src/dllmain.cpp` | DLL 入口与 Hook 初始化。 |
| `OneTextHook/src/TextHook.cpp`、`TextHook.h` | 拦截 GDI 文本 API 并执行文本替换。 |
| `OneTextHook/src/JsonTrans.cpp`、`JsonTrans.h` | 加载和查询 JSON 翻译数据。 |
| `OneTextHook/src/VersionProxy.cpp` | 转发系统 `version.dll` 接口。 |
| `OneTextHook/src/version.def` | `version.dll` 导出定义。 |
| `OneTextHook/src/OneTextHook.ini` | Hook、编码和翻译文件配置。 |
| `OneTextHook/third/detours/include/detours.h` | Microsoft Detours 主接口头文件。 |
| `OneTextHook/third/detours/include/detver.h` | Detours 版本定义。 |
| `OneTextHook/third/detours/include/syelog.h` | Detours 日志接口头文件。 |
| `OneTextHook/third/detours/lib.X86/detours.lib` | 32 位 Detours 静态库。 |
