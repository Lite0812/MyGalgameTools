# Martopia DAT 运行时工具

- 引擎：未确认，按 Martopia DAT 资源格式整理
- 测试游戏：《メルトピア》

## 文件

| 文件 | 用途 |
| --- | --- |
| `dat-hook/martopia_dump.cpp` | 运行时解包 DAT、导出资源并生成清单。 |
| `dat-hook/engine_features.cpp`、`engine_features.h` | 扫描并解析目标程序中的归档处理函数与全局数据。 |
| `dat-hook/resource_patch.cpp`、`resource_patch.h` | 让散文件优先替换 DAT 内资源。 |
| `dat-hook/resource_patch_tests.cpp` | 散文件替换逻辑的测试。 |
| `dat-hook/winmm_proxy.cpp` | WinMM 代理 DLL 入口与转发。 |
| `dat-hook/winmm_exports.def` | WinMM 代理导出定义。 |
| `dat-hook/version_proxy.cpp`、`version_proxy.h` | 系统 Version API 的代理转发实现。 |
| `dat-hook/MartopiaWinmm.ini` | 解包、日志和散文件替换配置。 |
| `dat-hook/MartopiaWinmm.vcxproj` | 主 DLL 的 Visual C++ 工程。 |
| `dat-hook/ResourcePatchTests.vcxproj` | 替换逻辑测试工程。 |
| `dat-hook/build_winmm.ps1` | 构建 WinMM 代理 DLL。 |
| `dat-hook/start_parallel_dump.ps1` | 启动并行资源导出流程。 |
| `MartopiaWinmm-原说明.md` | 运行时工具的详细使用说明。 |
