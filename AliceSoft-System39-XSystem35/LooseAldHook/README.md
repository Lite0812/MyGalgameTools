# LooseAldHook

- 引擎：AliceSoft System39、XSystem35
- 测试游戏：《俺の下であがけ2》

## 文件

| 文件 | 用途 |
| --- | --- |
| `build_x86.bat` | 构建供 32 位 System39 使用的 `version.dll`。 |
| `build_x64.bat` | 构建供 64 位 XSystem35 使用的 `version.dll`。 |
| `LooseAldHook.sln` | Visual Studio 解决方案。 |
| `LooseAldHook.vcxproj` | Visual C++ 工程配置。 |
| `LooseAldHook.vcxproj.filters` | Visual Studio 文件分组配置。 |
| `version.ini` | 散文件目录、Hook 定位方式及开关配置。 |
| `src/dllmain.cpp` | DLL 入口及 Hook 初始化。 |
| `src/config.cpp`、`src/config.h` | 读取和保存运行配置。 |
| `src/log.cpp`、`src/log.h` | 运行日志模块。 |
| `src/loose_ald_hook.cpp`、`src/loose_ald_hook.h` | 定位 ALD 读取函数，并让 `patch` 目录中的散文件优先于归档资源。 |
| `src/version_proxy.cpp`、`src/version_proxy.h` | 转发系统 `version.dll` 接口。 |
| `src/version.def` | 32 位 `version.dll` 导出定义。 |
| `src/version_x64.def` | 64 位 `version.dll` 导出定义。 |
