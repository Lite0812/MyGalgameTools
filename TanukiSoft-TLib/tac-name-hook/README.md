# TAC 名称记录与散文件 Hook

- 引擎：TanukiSoft TLib
- 测试游戏：《現実が見えてきたので少女を愛するのを辞めました。》

## 文件

| 文件 | 用途 |
| --- | --- |
| `tac_name_hook.cpp` | 定位 TLib 归档函数、记录资源路径并实现散文件优先加载。 |
| `tac_name_hook.ini` | 控制名称记录、散文件加载和补丁目录。 |
| `build_x86.bat` | 构建 32 位普通 Hook DLL 和 WinMM 代理 DLL。 |
| `generate_winmm_proxy.ps1` | 生成 WinMM 导出表和代理桩。 |
| `generated/winmm_proxy.def` | 生成的 WinMM 导出定义。 |
| `generated/winmm_proxy_stubs.asm` | 生成的 WinMM 汇编转发桩。 |
| `generated/winmm_exports.inc` | 生成的 WinMM 导出名称数据。 |
| `tests/winmm_smoke.cpp` | 检查代理 DLL 导出和转发是否可用。 |
