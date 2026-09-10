# Martopia 资源覆盖与运行时解包代理

这是使用 Microsoft Detours 的独立 x86 `winmm.dll` 代理，完整转发 winmm 导出表，并在
`DllMain` 返回后启动初始化线程。代理可以按 INI 配置只做资源覆盖，也可以挂接
游戏的归档读取、解密和资源登记函数，抓取引擎实际解码后的文件内容。

## 输出目录

只有 `Mode=2/3` 才会在游戏目录旁生成 `martopia_dump\时间戳\`：

- `files\`：按游戏内部路径还原的文件。
- `meta\manifest.tsv`：导出文件清单和大小。
- `meta\archive_entries.tsv`：每个 DAT 的内部条目索引。
- `meta\archive_queue.tsv`：动态识别出的全部资源登记调用记录。
- `meta\runtime.tsv`：中文运行心跳和钩子调用诊断日志。
- `meta\decrypt_blocks.bin`：可选的运行时 32 字节解密块记录。
- `meta\resource_patch.tsv`：`Mode=3` 的资源覆盖命中与失败记录。

统计文件与解包文件分开保存，不会混在 `files\` 中。

## 构建与使用

在本目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_winmm.ps1
```

把生成的 `bin\Release\winmm.dll` 和 `MartopiaWinmm.ini` 放到
`Martopia.exe` 同目录。游戏必须使用 x86 DLL，因为 `Martopia.exe` 是 PE32。
构建时 INI 会自动复制到输出目录。

## INI 模式

`MartopiaWinmm.ini` 的 `[Martopia]` 控制运行方式：

- `Enable=false` 或 `Mode=0`：只转发 winmm 导出，不安装引擎钩子。
- `Mode=1`：只做资源覆盖；正常运行游戏，不创建 Dump、不弹进度控制台、不退出游戏。
- `Mode=2`：单进程全量解包；完成后写入统计并自动退出游戏。
- `Mode=3`：资源覆盖并全量解包；`files` 中保存引擎实际收到的补丁内容。

项目附带的默认配置是 `Mode=1`。需要直接双击游戏解出全部 759 个 DAT 时，把它
改为 `Mode=2`。每次启动都会创建新的 `martopia_dump\时间戳\`；控制台显示
`DAT 759/759`，并且 `meta\completion.tsv` 显示 `状态=成功`、`完成=759`、
`归档失败=0`、`写入失败=0` 才表示完整成功。DLL 会刷新全部统计文件后自动退出。

## 资源覆盖

补丁目录在 `[ResourcePatch]` 中配置，默认是游戏目录下的 `unencrypted`：

```ini
[ResourcePatch]
PatchFolderCount = 2
PatchFolderName_0 = unencrypted
PatchFolderName_1 = my_patch
EnableLog = true
MaxFileSize = 268435456
```

目录既可以是相对游戏目录的路径，也可以是绝对路径，还可以包含 `%变量名%`。
后配置的目录优先级更高。对于内部路径 `SS\scenario\start.lua`，每个目录会先查
保留结构的 `目录\SS\scenario\start.lua`，再查平铺的 `目录\start.lua`。
因此目录结构和平铺文件可以混用；同一目录两者都存在时，保留结构的文件优先。

覆盖时原 DAT 仍由引擎完整读取、解密和消费，只有交给资源输出流的数据被替换，
所以不会造成后续条目错位，补丁大小也不要求和原文件一致，零字节文件同样有效。
绝对内部路径、UNC、盘符和 `..` 会被拒绝。`Mode=1` 的命中日志写到游戏目录下
`MartopiaWinmm_patch.tsv`；`Mode=3` 则写到本次 Dump 的 `meta` 目录。

## 引擎特征识别

DLL 不再保存 Martopia 的固定 RVA。启动时会扫描主 EXE 的 PE 代码段，通过归档
工作线程和调度函数的机器码形态定位锚点，再沿相对调用关系识别验证、构建、读取、
收尾、缓存登记和析构函数；线程句柄、队列计数和缓存表地址从已重定位的指令操作数
取得。资源表通过连续的 `Resource` 与 `dat` 路径对识别，条目数量由表的实际边界
决定，Martopia 中识别结果为 759 项。

因此，同一套归档流程、数据结构和资源路径约定的同引擎 x86 游戏可以直接使用，
不要求 EXE 名称为 `Martopia.exe`。关键特征缺失、出现多个候选或指向错误的 PE 节
时，DLL 会写明中文原因并拒绝安装钩子，不会回退到固定偏移。`Mode=1` 写入
`MartopiaWinmm_patch.tsv`，`Mode=2/3` 写入 `meta\runtime.tsv`。

`Mode=2/3` 启动时默认弹出进度控制台。控制台显示已登记 DAT、已解析归档、条目数、已写入
文件数、待写队列和失败数。文件写入使用线程池，默认线程数根据 CPU 限制在
2 到 8 之间，也可以设置 `MARTOPIA_DUMP_THREADS=1..32`。

设置 `MARTOPIA_NO_CONSOLE=1` 可关闭进度窗口；设置
`MARTOPIA_ENABLE_DECRYPT_BLOCKS=1` 才会记录解密块。解密块现在批量写入，减少
对游戏线程的阻塞。

卡住时优先查看 `meta\runtime.tsv`：每秒心跳会记录归档构建、归档读取、登记、
流写入、待写队列和最后一次路径，并给出各类调用距今的空闲时间。设置
`MARTOPIA_VERBOSE_LOG=1` 会额外记录每次归档读取和构建的开始、结束事件，日志会
明显变大且可能降低速度，只建议用于定位问题。

`Mode=2/3` 读取动态识别出的完整资源表。登记从游戏主线程开始，并且每个归档完成或
验证失败后才提交下一项，备用队列始终保持一个待处理归档，模拟引擎自身的资源
读取节奏。设置 `MARTOPIA_NO_FEED_ALL=1` 可关闭全量登记。归档解码和缓存仍由
引擎工作线程负责，不能在 DLL 中并行调用解码入口；文件创建和写入才使用线程池。

## 单进程低内存完整解包

运行前先把 `MartopiaWinmm.ini` 改成 `Mode=2`。

需要严格使用一个游戏进程和一个文件写入线程，并且不允许失败后补跑时，执行：

```powershell
.\start_parallel_dump.ps1 -进程数 1 -每进程写入线程 1 -失败重试次数 0
```

DLL 在直接启动或 `-进程数 1` 时都会自动开启逐包释放。它只对自己主动登记的
DAT 跳过长期缓存，并在引擎原生工作线程成功返回后调用原生析构函数释放当前归档
对象；游戏自身预加载的资源缓存不会被释放。这样可以保持引擎原生读取、解密和
登记流程，同时避免 32 位进程的归档缓存随 DAT 数量持续增长。

该模式已使用一个 `Martopia.exe`、一个文件写入线程和零次失败重试完成全量测试：
`759/759` 成功、失败 `0`、写入失败 `0`，没有生成任何 `retry_*` 补跑目录。只有
`meta\summary.tsv` 显示 `DAT成功=759` 且 `DAT失败=0` 才算完整完成。

## 多进程快速解包

运行前先把 `MartopiaWinmm.ini` 改成 `Mode=2`。

单个 `Martopia.exe` 是 32 位进程。如果未开启逐包释放，连续处理大量 DAT 后，
进程专用内存可能接近地址空间上限，导致引擎 `_beginthreadex` 创建归档工作线程
失败。多进程模式可以进一步提高吞吐量，也能隔离各分片的内存空间。

使用中文启动器将 759 项分给多个独立引擎进程：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_parallel_dump.ps1
```

启动器默认使用 8 个引擎进程，每个进程再使用 2 个文件写入线程。每个引擎进程
仍严格按原生流程串行解码自己的 DAT，不共享密码、缓存或工作线程状态；不同进程
之间并行，因此既能绕开 32 位地址空间上限，也能真正利用多核 CPU。

可按机器配置调整：

```powershell
.\start_parallel_dump.ps1 -进程数 6 -每进程写入线程 2 -失败重试次数 2
```

统一控制台显示 759 项总进度。最终文件直接写入同一个 `files` 目录，统计文件分别
保存在 `meta\shards`，不会混入解包目录。首轮失败项会使用全新单包进程自动重试；
只有 `meta\summary.tsv` 显示 `DAT成功=759` 才算完整完成。

DLL 分片相关环境变量如下，通常由启动器自动设置：

- `MARTOPIA_SHARD_INDEX`：从 0 开始的分片序号。
- `MARTOPIA_SHARD_COUNT`：分片总数。
- `MARTOPIA_DUMP_ROOT`：本进程运行目录。
- `MARTOPIA_EXTRACT_ROOT`：解包文件目录。
- `MARTOPIA_META_ROOT`：本进程统计目录。
- `MARTOPIA_WORKER_START_TIMEOUT_MS`：工作线程启动超时，默认 5000 毫秒。
- `MARTOPIA_COMPLETION_QUIET_MS`：完成前静默等待，默认 3000 毫秒。
- `MARTOPIA_KEEP_FEED_ARCHIVES`：仅用于诊断；设置后恢复保留主动登记归档的旧行为。
- `MARTOPIA_KEEP_OPEN`：仅用于调试；设置后全量完成时不自动关闭游戏。

代理不会修改原始 `.dat` 文件，只观察游戏运行时实际使用的数据。
