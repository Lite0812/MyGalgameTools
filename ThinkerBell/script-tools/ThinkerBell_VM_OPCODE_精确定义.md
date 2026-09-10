# ThinkerBell VM Opcode 精确定义

本文按 `Opcode | 命名 | 长度/格式 | 精确定义 | 佐证` 五列表整理 ThinkerBell `.a` 脚本 VM 的顶层 opcode。分析对象来自 [export-for-ai/decompile/](export-for-ai/decompile/) 与现有 [arc02/](arc02/) 样本；核心分发器为 [4239C0.c](export-for-ai/decompile/4239C0.c)。

## 置信度口径

为满足“所有 opcode 都有命名且高置信度”的要求，本文采用**保守命名**：

- 若 handler 内有 `sub_427280/sub_427300/sub_427480(..., aName)` 字符串，则优先使用该脚本运行时名称，例如 `LJAMP`、`SCALL`、`SPEAK`。
- 若无可读名称，则按 handler 的**直接可验证副作用**命名，例如 `VOICE_ADVANCE_READY`、`UI_FLUSH`、`CALL_RETURN`、`NOOP_38`，避免猜测剧情/业务含义。
- “长度/格式”中的长度为 `.a` 外层 record payload 长度，不含前置 `uint32_le length`。所有普通命令 payload 均以 `M#N <opcode:u32le>` 起始，固定头长 7 字节。
- `N(x)` 表示 token `0x4E + uint32_le`；`A(x)` 表示 token `0x41 + uint32_le`。`S...` 表示 opcode 头之后直接跟文本字节，属于样本中 opcode 60 的特殊 payload 形态。
- 独立文本记录格式为 `S + uint32_le(text_length) + encrypted_text`。正文逐字节与 `text_length & 0xFF` 异或后按 CP932 解码；常规记录以解密后的 `0xFE` 结束，少数格式控制记录没有该结束字节。

## Helper 命名说明

- [401280.c](export-for-ai/decompile/401280.c)：读取一个 `N imm32`。
- [401500.c](export-for-ai/decompile/401500.c)：读取 `N imm32` 并追加到普通参数向量 `this+0x104`；`sub_427280` 的底层 parser。
- [4016F0.c](export-for-ai/decompile/4016F0.c)：读取 `A imm32` 并追加到变量/引用参数向量 `this+0x120`；`sub_427300` 的底层 parser。
- [4019D0.c](export-for-ai/decompile/4019D0.c)：读取比较符 token，合法值为 `<`、`>`、`=`、`!`；用于 `IF`。
- [427280.c](export-for-ai/decompile/427280.c)：强制读取一个 `N` 参数；失败时用传入名称抛 `VScriptException`。
- [427300.c](export-for-ai/decompile/427300.c)：强制读取一个 `A` 参数；失败时用传入名称抛异常。
- [427480.c](export-for-ai/decompile/427480.c)：读取 `N` 或 `A` 参数；返回 0 表示 `N` 立即数，返回 1 表示 `A` 引用。
- [4298A0.c](export-for-ai/decompile/4298A0.c)：按变量编号写入 VM 变量槽。
- [4299D0.c](export-for-ai/decompile/4299D0.c)：按变量编号读取 VM 变量槽。

## Opcode 表

| Opcode | 命名 | 长度/格式 | 精确定义 | 佐证 |
|---:|---|---|---|---|
| `-1` | `TEXT_OR_PARSE_FAIL` | 非 `M#N`；常见为 `S...` 文本记录 | `sub_401470` 无法解析为命令时返回 -1；执行器在文本/等待状态下可触发文本推进与 flush 路径。 | [401470.c](export-for-ai/decompile/401470.c)；[4236B0.c](export-for-ai/decompile/4236B0.c)；[4239C0.c](export-for-ai/decompile/4239C0.c) |
| `0` | `LOAD_SCRIPT_ENTRY` | `M#N 0, N(entry)`；样本未出现 | 读取目标 entry 参数后调用 `sub_424380(entry)` 切换/加载脚本入口。 | [4239C0.c](export-for-ai/decompile/4239C0.c)；[424380.c](export-for-ai/decompile/424380.c) |
| `1` | `LJAMP` | `M#N 1, N(record_index)`；样本长度 12 | 读取 record index，向当前队列追加 `<File End>`，再把当前执行位置移动到指定 record。名称来自运行时字符串 `aLJamp`。 | [42E210.c](export-for-ai/decompile/42E210.c) |
| `2` | `SCALL` | `M#N 2, N(entry)`；样本长度 12 | 保存当前脚本现场到 call stack，调用 `sub_424380(arg0)` 切换到目标脚本/入口。名称来自 `aSCall`。 | [42E410.c](export-for-ai/decompile/42E410.c) |
| `3` | `VAR_SET` | `M#N 3, A(dst), N/A(value)`；样本 `A N` 长度 17 | 读取目标变量 `A(dst)`；第二参数可为立即数或变量引用，最终调用 `sub_4298A0(dst, value)` 写变量。名称按精确副作用保守命名。 | [42E5F0.c](export-for-ai/decompile/42E5F0.c)；[4298A0.c](export-for-ai/decompile/4298A0.c) |
| `4` | `VAR_ADD` | `M#N 4, A(dst), N/A(lhs), N/A(rhs)`；样本 `A A N` 长度 22 | 读取目标变量和两个操作数；把 `lhs + rhs` 写回 `dst`。 | [42E650.c](export-for-ai/decompile/42E650.c) |
| `5` | `VAR_SUB` | `M#N 5, A(dst), N/A(lhs), N/A(rhs)`；样本 `A A N` 长度 22 | 读取目标变量和两个操作数；把 `lhs - rhs` 写回 `dst`。 | [42E650.c](export-for-ai/decompile/42E650.c) |
| `6` | `VAR_MUL` | `M#N 6, A(dst), N/A(lhs), N/A(rhs)`；样本未出现 | 读取目标变量和两个操作数；把 `lhs * rhs` 写回 `dst`。 | [42E650.c](export-for-ai/decompile/42E650.c) |
| `7` | `VAR_DIV` | `M#N 7, A(dst), N/A(lhs), N/A(rhs)`；样本未出现 | 读取目标变量和两个操作数；若任一操作数为 0 抛异常，否则把 `lhs / rhs` 写回 `dst`。 | [42E650.c](export-for-ai/decompile/42E650.c) |
| `8` | `IF_COMPARE_SKIP` | `M#N 8, N/A(lhs), cmp, N/A(rhs), N(else_id)`；样本 `A N N N` 长度 27 | 读取比较左值、比较符 `< > = !`、右值和 else/case 编号；比较为假时向后扫描 opcode 9/10 并跳到匹配编号。 | [42E7B0.c](export-for-ai/decompile/42E7B0.c)；[4019D0.c](export-for-ai/decompile/4019D0.c) |
| `9` | `CODE_ELSE` | `M#N 9, N(id)`；样本长度 12 | `IF` 的 else 标记。检查当前在 if 上下文中，向后扫描 opcode 10 的匹配编号并跳过 else 块。名称来自 `aCodeElse`。 | [42EB30.c](export-for-ai/decompile/42EB30.c) |
| `10` | `CODE_ENDIF` | `M#N 10, N(id)`；样本长度 12 | if/else 结束标记；dispatcher 内联执行 `--this[645]`，减少条件块嵌套计数。 | [4239C0.c](export-for-ai/decompile/4239C0.c) |
| `11` | `LJAMP_ENTRY_REC` | `M#N 11, N(entry), N(record_index)`；样本未出现 | 读取目标脚本 entry 与 record index；先 `sub_424380(entry)` 切换脚本，再移动到指定 record。字符串证据为 `aLJamp`、`aLJampXx`。 | [42ED90.c](export-for-ai/decompile/42ED90.c) |
| `12` | `BLD_DRAW` | `M#N 12, N(id), N(x), N(y), N(param)`；样本未出现 | 读取 4 个普通参数，调用 `sub_412170` 在图形/BLD 管理对象上构造或绘制对象。名称来自 `aBld`。 | [42EED0.c](export-for-ai/decompile/42EED0.c) |
| `13` | `SCREEN_CLEAR_AND_PRESENT` | `M#N 13`；样本未出现 | 清状态、清/重置绘制对象，调用刷新与 `sub_425770` 提交画面。 | [42EFD0.c](export-for-ai/decompile/42EFD0.c)；[42F010.c](export-for-ai/decompile/42F010.c) |
| `14` | `SCREEN_PRESENT` | `M#N 14`；样本未出现 | 调用 `sub_42A3B0(0)`、清绘制队列、刷新窗口并按当前画面尺寸提交。 | [42F010.c](export-for-ai/decompile/42F010.c) |
| `15` | `BLD_LOAD_SHOW` | `M#N 15, N(id)`；样本未出现 | 读取 BLD/图形对象 ID；对象未存在时清屏、加载并显示该对象，然后刷新画面。 | [42F060.c](export-for-ai/decompile/42F060.c) |
| `16` | `BLD_ALLDEL_COLOR` | `M#N 16, N(color_id)`；样本未出现 | 清除 BLD 图形层并用参数选择背景色：0 白、1 黑、2 红、3 绿、4 蓝，然后刷新画面。名称来自 `aBldalldelColor`。 | [42F520.c](export-for-ai/decompile/42F520.c) |
| `17` | `SEL_START` | `M#N 17`；样本未出现 | 开始普通选择等待：设置选择状态为 1，flush UI，进入等待/点击路径。 | [42F5D0.c](export-for-ai/decompile/42F5D0.c) |
| `18` | `CASE` | `M#N 18, N(value)`；样本未出现 | 记录当前 case 值到选择/分支状态字段；名称来自 `aCase`。 | [42F610.c](export-for-ai/decompile/42F610.c) |
| `19` | `SEL_END` | `M#N 19, A(dst), N(count)`；样本未出现 | 读取选择结果变量与选项数量，结束选择并把结果写入相关状态；名称来自 `aSelend`/`aSelendNumErr`。 | [42F680.c](export-for-ai/decompile/42F680.c) |
| `20` | `ANIM_START` | `M#N 20, N(id), N(x), N(y), N(param)`；样本未出现 | 读取 4 个参数，调用 `sub_407DB0` 加载/定位动画并登记动画状态；名称来自 `aAnim`。 | [42F6D0.c](export-for-ai/decompile/42F6D0.c) |
| `21` | `SELJAMP_START` | `M#N 21`；样本长度 7 | 开始跳转型选择等待：设置选择状态为 2，flush UI，进入等待路径。 | [42F760.c](export-for-ai/decompile/42F760.c) |
| `22` | `CASEJAMP` | `M#N 22, N(value)`；样本未出现 | 记录跳转选择 case 值；名称来自 `aCasejamp`。 | [42F7A0.c](export-for-ai/decompile/42F7A0.c) |
| `23` | `SELJAMP_END` | `M#N 23, N(count)`；样本未出现 | 结束跳转型选择，读取选项数量并调用选择结束处理。名称来自 `aSeljampendNumE`。 | [42F7D0.c](export-for-ai/decompile/42F7D0.c) |
| `24` | `UI_FLUSH` | `M#N 24`；样本未出现 | flush 当前 UI/text 缓冲；必要时调用 `sub_42A3B0(0)` 重置显示状态。 | [42F800.c](export-for-ai/decompile/42F800.c) |
| `25` | `BLD_DELETE` | `M#N 25, N(id)`；样本未出现 | 读取 BLD/图形对象 ID；若存在且可见则删除/隐藏并更新绘制队列。 | [42F3B0.c](export-for-ai/decompile/42F3B0.c) |
| `26` | `BLD_EFFECT_COMMIT` | `M#N 26`；样本未出现 | 遍历当前效果/绘制队列，执行对象绘制、释放与画面提交。 | [42F100.c](export-for-ai/decompile/42F100.c) |
| `27` | `BLD_RESOURCE_RELEASE` | `M#N 27, N(id)`；样本未出现 | 读取对象 ID，在图形对象集合中查找并释放关联资源/句柄。 | [42F830.c](export-for-ai/decompile/42F830.c) |
| `28` | `DYNCLICKABLEMAP_SET` | `M#N 28, N(group), A(value), N(target)`；样本长度 22 | 读取动态图形/clickable map 组、变量值与跳转目标，设置当前动态点击项并显示。 | [42F920.c](export-for-ai/decompile/42F920.c) |
| `29` | `IF_STATE_CLEAR` | `M#N 29`；样本未出现 | 若当前 UI 状态为 1，则清状态并调用 `sub_42A540` 结束/恢复条件状态。 | [42F9F0.c](export-for-ai/decompile/42F9F0.c) |
| `30` | `UI_FLUSH_NO_ADVANCE` | `M#N 30`；样本未出现 | flush UI/text 缓冲并清 `this+135932`，不执行额外等待推进。 | [42FA70.c](export-for-ai/decompile/42FA70.c) |
| `31` | `SLEEPWAIT` | `M#N 31, N(ticks)`；样本长度 12 | 设置 sleep wait 标志与等待时长；名称来自 `aSleepwait`。 | [42FAA0.c](export-for-ai/decompile/42FAA0.c) |
| `32` | `EFFECT_TARGET` | `M#N 32, N(effect_id)`；样本未出现 | 若效果系统有效，读取 effect id，写入当前图形组对象并请求重绘/跳转到该 effect。名称来自 `aEffect`。 | [42FAD0.c](export-for-ai/decompile/42FAD0.c) |
| `33` | `GRAPHICGROUP_RESTORE_ALL` | `M#N 33`；样本未出现 | 遍历保存的图形组/对象列表，恢复对象坐标、显示参数与绘制状态。 | [4301F0.c](export-for-ai/decompile/4301F0.c) |
| `34` | `GRAPHICGROUP_RESET_HELPER` | `M#N 34`；样本长度 7 | 直接 thunk 到 `sub_427FE0`，执行图形组相关重置/整理 helper。 | [4304B0.c](export-for-ai/decompile/4304B0.c) |
| `35` | `GRAPHICGROUP_ALL` | `M#N 35, N(target)`；样本长度 12 | 读取图形组目标编号，提交当前 graphic group 的整体显示/过渡；名称来自 `aGraphicgroupal`。 | [4304C0.c](export-for-ai/decompile/4304C0.c) |
| `36` | `DYNCLICKABLEMAP` | `M#N 36, N(group), N(value), N(target)`；样本长度 22 | 设置动态图形点击 map：选取 group，写入当前值和跳转 target，并显示该 clickable map。名称来自 `aDynclickablema*`。 | [430C90.c](export-for-ai/decompile/430C90.c) |
| `37` | `DYNCLICKABLEMAP_ENABLE_ITEM` | `M#N 37, N(group), N(index)`；样本长度 17 | 取指定 dynamic clickable map 中的项目并把其启用/选中标志置 1。 | [430D60.c](export-for-ai/decompile/430D60.c) |
| `38` | `NOOP_38` | `M#N 38`；样本未出现 | 空操作，dispatcher 调 `nullsub_8`。 | [4239C0.c](export-for-ai/decompile/4239C0.c) |
| `39` | `SETBGCOLOR` | `M#N 39, N(color)`；样本长度 12 | 读取颜色参数并写入背景色状态字段。名称来自 `aSetbgcolor`。 | [430DC0.c](export-for-ai/decompile/430DC0.c) |
| `40` | `POPSEL` | `M#N 40, N(x), N(y), N(target)`；样本长度 22 | 设置弹出选择 UI 的坐标与目标位置，进入选择等待。名称来自 `aPopselX/Y`、`aPopsel`。 | [430DE0.c](export-for-ai/decompile/430DE0.c) |
| `41` | `POPCASE` | `M#N 41, N(value)`；样本长度 12 | 设置 pop 选择的 case 值。名称来自 `aPopcase`。 | [430E90.c](export-for-ai/decompile/430E90.c) |
| `42` | `POPSEL_END` | `M#N 42, A(dst), N(count)`；样本长度 17 | 结束 pop 选择，读取结果变量与选项数量，调用 pop 选择结束处理。 | [430EC0.c](export-for-ai/decompile/430EC0.c) |
| `43` | `SPEAK_START` | `M#N 43, N(voice_id)`；样本长度 12 | 读取语音 ID；若非 -1 且语音系统允许，调用 `sub_409E60` 播放语音并更新 speak UI。名称来自 `aSpeak`。 | [430F10.c](export-for-ai/decompile/430F10.c) |
| `44` | `BGMSTART` | `M#N 44, N(bgm_id)`；样本长度 12 | 读取 BGM ID；若非 -1 且不同于当前 BGM，则更新当前 BGM，并在 BGM 系统开启时调用 `sub_4439E0`。名称来自 `aBgmstart`。 | [430FB0.c](export-for-ai/decompile/430FB0.c) |
| `45` | `SPEAK_STOP` | `M#N 45`；样本长度 7 | 清理/停止当前 speak 播放对象。 | [430FF0.c](export-for-ai/decompile/430FF0.c) |
| `46` | `SPEAK_ALT_START` | `M#N 46, N(voice_id)`；样本长度 12 | 读取语音 ID；通过 `sub_40A010` 路径启动另一套 speak/voice 播放并更新 speak UI。名称来自 `aSpeak`。 | [431010.c](export-for-ai/decompile/431010.c) |
| `47` | `IGNOREGG_ON` | `M#N 47, N(target)`；样本未出现 | 开启 ignore graphic group 状态：把相关图形组对象标记为 ignore，必要时复制当前显示状态，并跳转/重绘到 target。名称来自 `aIgnoreggOn`。 | [431070.c](export-for-ai/decompile/431070.c) |
| `48` | `IGNOREGG_OFF` | `M#N 48, N(group)`；样本未出现 | 关闭指定 graphic group 的 ignore 状态，恢复被忽略前的图形状态并重绘。名称来自 `aCodeIgnoreggOf`。 | [431940.c](export-for-ai/decompile/431940.c) |
| `49` | `IGNOREGG_CLEAR` | `M#N 49, N(target)`；样本未出现 | 清除所有 ignore graphic group 状态，恢复被保存的对象状态；若有恢复且 target != 9999，则跳转/重绘到 target。名称来自 `aIgnoreggClear`。 | [431B60.c](export-for-ai/decompile/431B60.c) |
| `50` | `CLICKWAIT` | `M#N 50, N(x), N(y), N(target)`；样本长度 22 | 设置点击等待坐标/区域与目标，进入 click wait 状态。名称来自 `aClickwait*`。 | [4321B0.c](export-for-ai/decompile/4321B0.c) |
| `51` | `TEXT_AUTOMODE_OFF` | `M#N 51`；样本长度 7 | 清 `this+135941`，关闭某文本/自动状态标志。 | [432240.c](export-for-ai/decompile/432240.c) |
| `52` | `TEXT_AUTOMODE_ON` | `M#N 52`；样本长度 7 | 置 `this+135941 = 1`，开启某文本/自动状态标志。 | [432250.c](export-for-ai/decompile/432250.c) |
| `53` | `TEXT_WINDOW_UPDATE` | `M#N 53`；样本长度 7 | 调用 `sub_44B2A0`，按当前窗口句柄与状态更新文本窗口/输入显示。 | [432260.c](export-for-ai/decompile/432260.c) |
| `54` | `RESET_CURSOR_54` | `M#N 54`；样本长度 7 | dispatcher 调用被 IDA 误命名为 `CDaoRecordset::ResetCursor` 的方法；从上下文看是光标/文本指针重置类操作。 | [4239C0.c](export-for-ai/decompile/4239C0.c) |
| `55` | `TEXT_WINDOW_COMMIT` | `M#N 55`；样本长度 7 | 若有待更新文本窗口状态则调用 `sub_447B40` 更新；随后 `sub_44AD50` 提交文本/UI 对象。 | [4322C0.c](export-for-ai/decompile/4322C0.c) |
| `56` | `TEXT_ADVANCE` | `M#N 56`；样本未出现 | thunk 到 `sub_42AAD0`，推进文本/等待状态。 | [432320.c](export-for-ai/decompile/432320.c) |
| `57` | `TEXT_ADVANCE_FLUSH` | `M#N 57`；样本长度 7 | 有待显示状态时先更新文本窗口并重置显示，再执行 `sub_44AFE0` 和 `sub_42AAD0`。 | [432330.c](export-for-ai/decompile/432330.c) |
| `58` | `TEXT_WAIT_RESET` | `M#N 58`；样本长度 7 | thunk 到 `sub_429290`，重置/处理文本等待状态。 | [4323A0.c](export-for-ai/decompile/4323A0.c) |
| `59` | `SAVE_TITLE_SET` | `M#N 59`；样本未出现 | 复制/设置存档标题字符串；失败时错误字符串为 `aSavetitleErrer`。 | [4323B0.c](export-for-ai/decompile/4323B0.c) |
| `60` | `STATE_TEXT_SET` | `M#N 60 + S + uint32_le(length) + encrypted_text` | 当前引擎调用 `sub_43FB60`，按与普通文本相同的长度异或算法解出 CP932 字符串，并写入持久 VM 状态字段；样本包括“右クリックスクリプト実行”和空格。 | [43FB60.c](export-for-ai/decompile/43FB60.c)；[45AC50.c](export-for-ai/decompile/45AC50.c) |
| `61` | `TEXT_CLEAR_BUFFERS` | `M#N 61`；样本长度 7 | 清文本显示缓冲、清选择/临时字符串列表，并 flush UI。 | [432500.c](export-for-ai/decompile/432500.c) |
| `62` | `CALL_RETURN` | `M#N 62`；样本长度 7 | 从 call stack 弹出返回现场，调用 `sub_424380(entry)` 恢复脚本和 record index；无栈时切到 entry 0。 | [432680.c](export-for-ai/decompile/432680.c) |
| `63` | `SAVE_MODE` | `M#N 63, A(value)`；样本长度 12 | 读取变量/参数值，设置 save mode 状态并进入保存 UI。名称来自 `aSavemode`。 | [4329B0.c](export-for-ai/decompile/4329B0.c) |
| `64` | `LOAD_MODE` | `M#N 64, A(value)`；样本长度 12 | 读取变量/参数值，刷新窗口，设置 load mode 状态并进入读取 UI。名称来自 `aLoadmode`。 | [4329E0.c](export-for-ai/decompile/4329E0.c) |
| `65` | `LOAD_CONFIRM_YES` | `M#N 65`；样本长度 7 | 显示 Yes/No MessageBox；选择 Yes 时调用 `sub_432E80` 执行读取/重置流程。 | [432A40.c](export-for-ai/decompile/432A40.c) |
| `66` | `SAVELOAD_MODE_1` | `M#N 66`；样本长度 7 | 设置 save/load 子系统模式字段为 1，并设置相关状态标志。 | [432A80.c](export-for-ai/decompile/432A80.c) |
| `67` | `SAVELOAD_MODE_2` | `M#N 67`；样本长度 7 | 设置 save/load 子系统模式字段为 2，并设置相关状态标志。 | [432A90.c](export-for-ai/decompile/432A90.c) |
| `68` | `SAVELOAD_MENU_BUILD` | `M#N 68, N(mode)`；样本长度 12 | 重置 UI，构造 save/load 菜单与窗口对象，并进入等待显示状态。 | [432AA0.c](export-for-ai/decompile/432AA0.c) |
| `69` | `RESET_CURSOR_69` | `M#N 69, N(arg)`；样本长度 12 | dispatcher 调用同 `54` 的 `CDaoRecordset::ResetCursor` 误命名函数；样本带一个普通参数。 | [4239C0.c](export-for-ai/decompile/4239C0.c) |
| `70` | `VOICE_ADVANCE_READY` | `M#N 70`；样本长度 7 | 若语音/文本系统激活，则设置 `this+135931=1`、`this+135932=1`，并同步当前文本行/页索引。高频无参数状态同步指令。 | [432D70.c](export-for-ai/decompile/432D70.c) |
| `71` | `POST_CLOSE` | `M#N 71`；样本长度 7 | 向主窗口发送 `WM_CLOSE` (`PostMessageA(hwnd, 0x10, 0, 0)`)。 | [432DB0.c](export-for-ai/decompile/432DB0.c) |
| `72` | `MOVIE` | `M#N 72, N(movie_id), N(next)`；样本长度 17 | 读取影片 ID 与结束后目标；若影片 ID 非 -1，调用 `sub_4443D0` 播放电影并设置 movie wait/next 状态。名称来自 `aMovie`。 | [432DD0.c](export-for-ai/decompile/432DD0.c) |
| `73` | `MOVIE_END_CHECK` | `M#N 73`；样本长度 7 | 若 movie 状态满足条件，清 movie 标志并调用 `sub_443A20(0)` 做结束处理。 | [432E40.c](export-for-ai/decompile/432E40.c) |
| `74` | `SCENE_RESET_TO_ZERO` | `M#N 74`；样本长度 7 | 清 call/if/graphic group 状态，切回脚本 entry 0，清 UI/图形/选择状态。 | [432E80.c](export-for-ai/decompile/432E80.c) |
| `75` | `SYS_STOP_MESSAGE` | `M#N 75`；样本未出现 | 调 `sub_4292D0` 后显示 `Sys Stop` MessageBox。 | [4332C0.c](export-for-ai/decompile/4332C0.c) |
| `76` | `RETURN_AND_CLEAR_ADVANCE` | `M#N 76`；样本长度 7 | 先执行 `CALL_RETURN`，再调用 `sub_423250`，并清文本推进标志。 | [4332F0.c](export-for-ai/decompile/4332F0.c) |
| `77` | `LOCAL_ALLOC` | `M#N 77, N(local_id)`；样本长度 12 | 在局部变量容器中为给定 local id 建槽并初始化为 0，局部变量计数加 1。名称来自 `aLocal`。 | [433310.c](export-for-ai/decompile/433310.c) |
| `78` | `TXTSIZE` | `M#N 78, N(lines)`；样本未出现 | 设置文本窗口高度/行数相关字段，并调用 `sub_447B40` 刷新文本窗口。名称来自 `aTxtsize`。 | [433370.c](export-for-ai/decompile/433370.c) |
| `79` | `REFFECT` | `M#N 79, N(effect_id), N(param)`；样本长度 17 | 读取反向/恢复效果 ID 与参数，调用 `sub_40A5F0`，设置 effect 状态并进入等待。名称来自 `aReffect`。 | [433410.c](export-for-ai/decompile/433410.c) |
| `80` | `GRAPHICGROUP_SHOW` | `M#N 80, N/A(target)`；样本 `N` 长度 12 | 显示 graphic group：刷新底层、调用 `sub_44D500`，读取目标可为 N 或 A，显示并可跳转到 target。 | [433470.c](export-for-ai/decompile/433470.c) |
| `81` | `GRAPHICGROUP_HIDE` | `M#N 81, N/A(target)`；样本 `N` 长度 12 | 隐藏 graphic group：刷新底层、调用 `sub_44D560`，读取目标可为 N 或 A，隐藏并可跳转到 target。 | [433590.c](export-for-ai/decompile/433590.c) |
| `82` | `GRAPHICGROUP_FORCE_SHOW` | `M#N 82`；样本长度 7 | 保存当前 graphic group 显示状态，调用 `sub_44D500` 强制显示，并设置显示标志为 1。 | [4336B0.c](export-for-ai/decompile/4336B0.c) |
| `83` | `GRAPHICGROUP_FORCE_HIDE` | `M#N 83`；样本未出现 | 保存当前 graphic group 显示状态，调用 `sub_44D560` 强制隐藏，并设置显示标志为 0。 | [4336E0.c](export-for-ai/decompile/4336E0.c) |
| `84` | `GRAPHICGROUP_RESTORE_VISIBILITY` | `M#N 84`；样本长度 7 | 若存在保存的显示状态，则恢复 show/hide 状态并清保存值。 | [433710.c](export-for-ai/decompile/433710.c) |
| `85` | `SAVE_RAW_DATA` | `M#N 85`；样本长度 7 | 通过 `sub_426D60` 生成文件名，打开文件并把内存缓冲写入磁盘。 | [433770.c](export-for-ai/decompile/433770.c) |
| `86` | `LOAD_RAW_DATA` | `M#N 86`；样本长度 7 | 通过 `sub_426D60` 生成文件名，必要时确认，然后读取文件到内存缓冲并调用 `sub_426370`。 | [433880.c](export-for-ai/decompile/433880.c) |
| `87` | `VOICE_ADVANCE_READY_SOFT` | `M#N 87`；样本长度 7 | 若语音/文本系统激活，则设置 `this+135931=1` 并同步当前文本行/页索引；与 opcode 70 相比不设置 `this+135932`。 | [433A80.c](export-for-ai/decompile/433A80.c) |
| `88` | `NOOP_88` | `M#N 88`；样本未出现 | 空操作，dispatcher 调 `nullsub_10`。 | [4239C0.c](export-for-ai/decompile/4239C0.c) |
| `89` | `NOOP_89` | `M#N 89`；样本未出现 | 空操作，dispatcher 调 `nullsub_11`。 | [4239C0.c](export-for-ai/decompile/4239C0.c) |
| `90` | `RBUTTON_POPCASE` | `M#N 90, N(value)`；样本长度 12 | 设置右键 pop case 值。名称来自 `aRbottonpopcase`。 | [433AD0.c](export-for-ai/decompile/433AD0.c) |
| `91` | `POPSELJUMP` | `M#N 91, N(x), N(y), N(target)`；样本长度 22 | 设置跳转型 pop 选择 UI 的坐标与目标，进入等待。名称来自 `aPopseljump*`。 | [433B00.c](export-for-ai/decompile/433B00.c) |
| `92` | `POPCASEJAMP` | `M#N 92, N(value)`；样本长度 12 | 设置跳转型 pop case 值。名称来自 `aPopcacejamp`。 | [433BB0.c](export-for-ai/decompile/433BB0.c) |
| `93` | `POPSELJAMP_END` | `M#N 93, N(count)`；样本长度 12 | 结束跳转型 pop 选择，读取选项数量并调用 pop 选择结束处理。名称来自 `aPopseljampendN`。 | [433BE0.c](export-for-ai/decompile/433BE0.c) |
| `94` | `RBUTTON_POPCASE_ALT` | `M#N 94, N(value)`；样本未出现 | 设置右键 pop case 的替代状态字段。名称来自 `aRbottonpopcase_0`。 | [433C10.c](export-for-ai/decompile/433C10.c) |
| `95` | `TEXT_WINDOW_MODE` | `M#N 95`；样本长度 7 | 调用 `sub_44B460` 更新/切换文本窗口模式。 | [432280.c](export-for-ai/decompile/432280.c) |
| `96` | `DYNCLICKABLEMAP_DRAW` | `M#N 96, N(group), N(target)`；样本长度 17 | 读取动态图形组与 target，绘制 dynamic clickable map 并重置各子项显示/选择状态。 | [433C40.c](export-for-ai/decompile/433C40.c) |
| `97` | `SPEAK_PAUSE` | `M#N 97`；样本长度 7 | 调用 `sub_4441D0` 操作 speak/voice 播放对象；按 helper 对称关系命名为 pause。 | [433D50.c](export-for-ai/decompile/433D50.c) |
| `98` | `SPEAK_RESUME` | `M#N 98`；样本长度 7 | 调用 `sub_444200` 操作 speak/voice 播放对象；按 helper 对称关系命名为 resume。 | [433D60.c](export-for-ai/decompile/433D60.c) |
| `99` | `SPEAK_QUEUE_NEXT` | `M#N 99`；样本长度 7 | 从 speak 队列取下一个 voice id，若可播放则调用 `sub_409E60` 与 speak UI 更新，并维护文本缓冲。 | [433D70.c](export-for-ai/decompile/433D70.c) |
| `100` | `INPUT_BRANCH_WAIT_TIMED` | `N, N, N` | 设置两路输入目标和超时值，进入输入等待；前两个参数可作为 record 目标。 | [43A3A0.c](export-for-ai/decompile/43A3A0.c) |
| `101` | `EXIT_REQUEST_SET` | 无参数 | 设置退出请求及关联状态。 | [4376F0.c](export-for-ai/decompile/4376F0.c) |
| `102` | `EXIT_REQUEST_CLEAR` | 无参数 | 清除退出请求状态。 | [437710.c](export-for-ai/decompile/437710.c) |
| `103` | `INPUT_STATE_SAVE_AND_CLEAR` | 无参数 | 保存当前输入状态并清零活动状态。 | [437720.c](export-for-ai/decompile/437720.c) |
| `104` | `INPUT_STATE_RESTORE` | 无参数 | 恢复此前保存的输入状态。 | [437740.c](export-for-ai/decompile/437740.c) |
| `105` | `GRAPHICGROUP_ITEM_JUMP` | `N, N` | 保存图形组项目值，并跳转到第二参数指定目标。 | [43A460.c](export-for-ai/decompile/43A460.c) |
| `106` | `GRAPHICGROUP_JUMP` | `N` | 以当前图形组执行目标跳转。 | [43A530.c](export-for-ai/decompile/43A530.c) |
| `107` | `INPUT_LOCK_ON` | 无参数 | 开启输入锁状态。 | [437750.c](export-for-ai/decompile/437750.c) |
| `108` | `INPUT_LOCK_OFF` | 无参数 | 关闭输入锁状态。 | [437760.c](export-for-ai/decompile/437760.c) |
| `109` | `VOICE_CHANNEL_LOAD` | `N` | 为新增语音通道装载并启动指定资源。 | [43A5E0.c](export-for-ai/decompile/43A5E0.c) |
| `110` | `GRAPHIC_EFFECT_MODE_3` | `N, N, N` | 用三个参数初始化图形效果模式 3。 | [43A150.c](export-for-ai/decompile/43A150.c) |
| `111` | `GRAPHIC_EFFECT_MODE_4` | `N, N, N` | 用三个参数初始化图形效果模式 4。 | [43A6C0.c](export-for-ai/decompile/43A6C0.c) |
| `112` | `OVERLAY_TEXTURE_SHOW` | `N` | 加载纹理并创建全屏 RHW 覆盖对象。 | [43A750.c](export-for-ai/decompile/43A750.c) |
| `113` | `RESERVED_113` | 无 handler | 新版 dispatcher 未定义此编号。 | [45AC50.c](export-for-ai/decompile/45AC50.c) |
| `114` | `AUDIO_SLOT3_SET` | `N` | 设置或以 `-1` 释放音频槽 3。 | [43A960.c](export-for-ai/decompile/43A960.c) |
| `115` | `GRAPHICGROUP_ITEM_ACTION` | `N, N` | 对指定图形组项目执行第二参数动作。 | [4430F0.c](export-for-ai/decompile/4430F0.c) |
| `116` | `UI_CANCEL_RESET` | 无参数 | 取消当前 UI 操作并清除退出/返回确认标志。 | [44A260.c](export-for-ai/decompile/44A260.c) |
| `117` | `EXIT_CONFIRM` | 无参数 | 显示退出确认对话框并设置退出请求。 | [43C390.c](export-for-ai/decompile/43C390.c) |
| `118` | `EXIT_CONFIRM_INFO_MODE_ON` | 无参数 | 将退出确认切换为提示框模式。 | [437770.c](export-for-ai/decompile/437770.c) |
| `119` | `RETURN_CONFIRM` | 无参数 | 显示返回确认对话框并触发返回流程。 | [443180.c](export-for-ai/decompile/443180.c) |
| `120` | `OVERLAY_FLAG_ON` | 无参数 | 开启覆盖层状态标志。 | [437780.c](export-for-ai/decompile/437780.c) |
| `121` | `OVERLAY_FLAG_OFF` | 无参数 | 关闭覆盖层状态标志。 | [437790.c](export-for-ai/decompile/437790.c) |
| `122` | `UI_RESOURCE_ID_SET` | `N` | 设置 UI 资源 ID。 | [43AA00.c](export-for-ai/decompile/43AA00.c) |
| `123` | `UI_RESOURCE_ID_CLEAR` | 无参数 | 将 UI 资源 ID 清为 `-1`。 | [4377A0.c](export-for-ai/decompile/4377A0.c) |
| `124` | `ARRAY_SET` | `N, N/A, N/A` | 写入二维脚本数组。 | [4431E0.c](export-for-ai/decompile/4431E0.c) |
| `125` | `ARRAY_GET` | `N, N/A, A` | 读取二维脚本数组并写入变量。 | [4432B0.c](export-for-ai/decompile/4432B0.c) |
| `126` | `INTERACTION_STATE_RESET` | 无参数 | 重置交互现场和输入/退出状态。 | [44A2A0.c](export-for-ai/decompile/44A2A0.c) |
| `127` | `UI_MODE3_OPEN` | `N, N, N, N` | 提交场景并以四个参数打开 UI 模式 3。 | [44A330.c](export-for-ai/decompile/44A330.c) |
| `128` | `UI_SELECTION_PREPARE` | `N, N/A` | 准备 UI 选择状态及选择值。 | [443370.c](export-for-ai/decompile/443370.c) |
| `129` | `UI_SELECTION_COMMIT` | `A` | 提交 UI 选择变量并执行选择处理。 | [443450.c](export-for-ai/decompile/443450.c) |
| `130` | `WINDOW_TITLE_MODE_TOGGLE` | 无参数 | 切换窗口标题显示模式并刷新标题。 | [44A4A0.c](export-for-ai/decompile/44A4A0.c) |
| `131` | `AUDIO_TRACK_PLAY_FADE` | `N, N` | 播放指定音轨；第二参数控制淡入时长，`-1` 表示直接采用当前音量。 | [43AA30.c](export-for-ai/decompile/43AA30.c) |
| `132` | `AUDIO_TRACK_STOP_FADE` | `N` | 停止当前音轨；参数为淡出毫秒数，0 表示立即停止。 | [43AD80.c](export-for-ai/decompile/43AD80.c) |
| `133` | `RESERVED_133` | 无 handler | 新版 dispatcher 未定义此编号。 | [45AC50.c](export-for-ai/decompile/45AC50.c) |
| `134` | `UI_MODE4_OPEN` | `N, N, N` | 提交场景并以三个参数打开 UI 模式 4。 | [44A4C0.c](export-for-ai/decompile/44A4C0.c) |
| `135` | `UI_MODE4_SELECT` | `N` | 设置 UI 模式 4 的选择值。 | [43AF60.c](export-for-ai/decompile/43AF60.c) |
| `136` | `UI_MODE4_COMMIT` | `A, N` | 提交 UI 模式 4 的结果变量和附加值。 | [44A590.c](export-for-ai/decompile/44A590.c) |
| `137` | `TABLE_GET` | `N, N/A, N/A, N/A` | 从二维表读取值并写入第三个可变参数指定的变量。 | [4434B0.c](export-for-ai/decompile/4434B0.c) |
| `138` | `AUDIO_TRACK_PLAY` | `N` | 无淡入地播放指定音轨。 | [43AF90.c](export-for-ai/decompile/43AF90.c) |
| `139` | `SCRIPT_CONFIG_EXEC` | `STR(text), N` | 读取加密字符串配置和编号，解释配置项并扫描到对应脚本标记。 | [44E800.c](export-for-ai/decompile/44E800.c) |
| `140` | `RESERVED_140` | 无 handler | 新版 dispatcher 未定义此编号。 | [45AC50.c](export-for-ai/decompile/45AC50.c) |
| `141` | `RESERVED_141` | 无 handler | 新版 dispatcher 未定义此编号。 | [45AC50.c](export-for-ai/decompile/45AC50.c) |
| `142` | `RESERVED_142` | 无 handler | 新版 dispatcher 未定义此编号。 | [45AC50.c](export-for-ai/decompile/45AC50.c) |
| `143` | `WAIT_MILLISECONDS` | `N/A` | 相对上次 tick 等待指定毫秒数。 | [443610.c](export-for-ai/decompile/443610.c) |
| `144` | `STATE_TABLE_SET` | `N, A` | 将变量当前值写入内部状态表的指定槽。 | [443690.c](export-for-ai/decompile/443690.c) |
| `145` | `LINEAR_INTERPOLATE` | `A, N/A, N/A, N/A, N/A` | 计算 `start + (end-start)*position/duration` 并写入变量。 | [4436F0.c](export-for-ai/decompile/4436F0.c) |
| `146` | `RANDOM_RANGE` | `A, N/A, N/A` | 生成 `[min,max)` 随机整数并写入变量。 | [4437A0.c](export-for-ai/decompile/4437A0.c) |
| `147` | `ARRAY_COPY` | `N, N` | 清空目标数组，或从另一数组复制内容。 | [443850.c](export-for-ai/decompile/443850.c) |
| `148` | `SCREENSHOT_SAVE` | 无参数 | 在工作线程中将当前画面保存为带时间戳的 JPEG。 | [43FC40.c](export-for-ai/decompile/43FC40.c) |
| `149` | `SCREENSHOT_BUFFER` | `N/A` | 参数 0 分配截图/编码缓冲，参数 1 释放缓冲。 | [443940.c](export-for-ai/decompile/443940.c) |
| `150` | `AUDIO_SLOT4_SET` | `N` | 设置或以 `-1` 释放音频槽 4。 | [43B130.c](export-for-ai/decompile/43B130.c) |
| `151` | `UI_STATE_1_TO_2` | 无参数 | 若 UI 状态为 1，则切换为 2。 | [4377B0.c](export-for-ai/decompile/4377B0.c) |
| `152` | `UI_STATE_2_TO_1` | 无参数 | 若 UI 状态为 2，则切换为 1。 | [4377D0.c](export-for-ai/decompile/4377D0.c) |
| `153` | `RETURN_TO_TITLE_CONFIRM` | 无参数 | 确认后清理场景、图形与音频并返回标题状态。 | [44A600.c](export-for-ai/decompile/44A600.c) |
| `154` | `SCENE_STATE_COMMIT` | 无参数 | 调用场景状态提交/恢复 helper。 | [44AAB0.c](export-for-ai/decompile/44AAB0.c) |
| `155` | `UI_SUBSYSTEM_RESOURCE_SET` | `N` | 释放旧资源 ID，设置新 ID 并初始化 UI 子系统资源。 | [44AAC0.c](export-for-ai/decompile/44AAC0.c) |
| `156` | `RENDER_REFRESH_REQUEST` | 无参数 | 在对应渲染对象存在时设置刷新请求。 | [4377F0.c](export-for-ai/decompile/4377F0.c) |
| `157` | `SYSTEM_MENU_STATE_INIT` | 无参数 | 捕获系统设置/菜单现场并进入菜单状态。 | [437810.c](export-for-ai/decompile/437810.c) |
| `158` | `VOICE_STATE_RESTART` | 无参数 | 按保存的语音状态重新启动当前语音。 | [437940.c](export-for-ai/decompile/437940.c) |
| `159` | `SYSTEM_MENU_TEXTURES_LOAD` | 无参数 | 加载系统菜单所需的五组纹理资源。 | [437970.c](export-for-ai/decompile/437970.c) |
| `160` | `RESERVED_160` | 无 handler | 新版 dispatcher 未定义此编号。 | [45AC50.c](export-for-ai/decompile/45AC50.c) |
| `161` | `SCRIPT_HISTORY_NEXT` | `A` | 从脚本历史/候选表取下一项并写回状态变量。 | [456520.c](export-for-ai/decompile/456520.c) |
| `162` | `UI_STATE_FLAG_ON` | 无参数 | 开启新增 UI 状态标志。 | [437A90.c](export-for-ai/decompile/437A90.c) |
| `163` | `UI_STATE_FLAG_OFF` | 无参数 | 关闭新增 UI 状态标志。 | [437AA0.c](export-for-ai/decompile/437AA0.c) |
| `164` | `AMBIENT_TEXTURE_SET` | `N` | 加载资源并设置 D3D ambient texture table。 | [43B1D0.c](export-for-ai/decompile/43B1D0.c) |
| `165` | `SCRIPT_VALUE_EVALUATE` | `N/A, N` | 求值第一个常量/变量参数并读取第二个普通参数；handler 没有可见持久副作用。 | [443E40.c](export-for-ai/decompile/443E40.c) |
| `167` | `INPUT_OR_TIMEOUT_WAIT` | `N/A(milliseconds)` | 将 elapsed 清零、保存等待毫秒数并开启输入等待；主循环逐帧累加 elapsed，达到目标后解除等待，鼠标/输入路径也可提前解除。 | [459530.c](../脚本/export-for-ai/decompile/459530.c)；[452340.c](../脚本/export-for-ai/decompile/452340.c)；[452160.c](../脚本/export-for-ai/decompile/452160.c) |
| `9999` | `END_MARKER` | `M$`；不是 `M#N` 普通命令 | `sub_401470` 读到 `M$` 返回 9999；dispatcher 调 `sub_42FB50`，用于图形组/脚本完成处理。 | [401470.c](export-for-ai/decompile/401470.c)；[42FB50.c](export-for-ai/decompile/42FB50.c) |

## 样本覆盖结论

当前 [arc02/](arc02/) 的 167 个 `.a` 样本中出现 65 种普通 opcode。未出现但 dispatcher 覆盖的 opcode 仍按 handler 直接行为完成命名。高频 opcode 的高置信度命名如下：

| Opcode | count | 命名 | 主要依据 |
|---:|---:|---|---|
| `70` | 24216 | `VOICE_ADVANCE_READY` | 无参数；仅同步 voice/text advance 状态；[432D70.c](export-for-ai/decompile/432D70.c) |
| `43` | 11209 | `SPEAK_START` | 字符串 `aSpeak`，调用 voice loader/player；[430F10.c](export-for-ai/decompile/430F10.c) |
| `87` | 5005 | `VOICE_ADVANCE_READY_SOFT` | 与 70 同类但少置一个状态位；[433A80.c](export-for-ai/decompile/433A80.c) |
| `46` | 1628 | `SPEAK_ALT_START` | 字符串 `aSpeak`，另一条 voice 播放 helper 路径；[431010.c](export-for-ai/decompile/431010.c) |
| `44` | 643 | `BGMSTART` | 字符串 `aBgmstart`，调用 BGM start helper；[430FB0.c](export-for-ai/decompile/430FB0.c) |

## 实现提示

反汇编/汇编第一版可把 `长度/格式` 作为严格 schema：

1. 外层记录始终保留 `uint32_le payload_len`。
2. 普通命令必须输出/重建 `M#N opcode` 头。
3. 对 `N/A` 可变参数，反汇编时保留 token 种类；汇编时按 token 种类逐字节恢复。
4. 对 `LJAMP`、`SCALL`、`LJAMP_ENTRY_REC`、`CALL_RETURN` 等控制流指令，后续应在 IR 层生成 record 标签，但底层重建仍以 record index/entry 参数为准。

## 新旧文本格式兼容

- 旧版脚本的文本 record 为 `S + CP932 明文`，通常以 `FE` 结束；asm 头写作 `.text_format plain`。
- 新版脚本的文本 record 为 `S + uint32_le(length) + XOR 密文`；asm 头写作 `.text_format xor_length`。
- `disassembler.py` 默认使用 `--text-format auto` 扫描 record 自动判断格式；也可显式指定 `plain` 或 `xor_length`。
- `assembler.py` 优先采用 asm 内的 `.text_format`。对于旧备份工具生成、没有该指令的 asm，可传入 `--text-format plain`；无指令且无覆盖参数时默认保持新版 `xor_length` 行为。
- 旧版 opcode 60 保留为 `NOOP_60_TEXT_PAYLOAD RAW(...)`，新版 opcode 60 解析为 `STATE_TEXT_SET "..."`，两者按文本格式自动区分。
