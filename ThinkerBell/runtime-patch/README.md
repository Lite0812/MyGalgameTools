# ThinkerBell Runtime Patch

- 引擎：ThinkerBell
- 测试游戏：《淫妖蟲 獄》、《淫妖蟲 外伝1》、《淫妖蟲 外伝2》、《新訳 淫妖蟲 特典 桜花菊花編》、《HIDAMARI》

## 文件

| 文件 | 用途 |
| --- | --- |
| `src/dllmain.cpp` | DLL 入口、配置加载和 Hook 初始化。 |
| `src/patch_hook.cpp`、`patch_hook.h` | 扫描资源加载函数并实现平铺散文件替换。 |
| `src/winmm_proxy.asm` | 将 WinMM 调用转发到系统 DLL。 |
| `src/winmm.def` | WinMM 代理导出定义。 |
| `ThinkerBellPatch.ini` | Hook 开关、补丁目录和人工 RVA 配置。 |
| `build_x86.bat` | 构建 32 位 WinMM 代理补丁。 |
| `tools/generate_winmm_proxy.py` | 生成 WinMM 代理汇编和导出定义。 |
| `tools/proxy_smoke_test.cpp` | 检查代理 DLL 的加载和导出转发。 |
| `tools/verify_exe_signatures.py` | 离线检查目标 EXE 的资源加载函数特征。 |
