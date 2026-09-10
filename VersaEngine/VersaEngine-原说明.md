# vetool — VersaEngine 资源解包/封包

针对 `libmain.so` (arm64-v8a) 逆向所得的格式实现，支持 `game.vseal` / `*.vpk` / `*.pak`，
**往返二进制一致**（已对 154 个文件全量验证）。

## 依赖

```
pip install pynacl lz4
```

`lz4` 可选：缺失时回退到内置纯 Python 实现（解压精确，压缩略慢）。

## 用法

```bash
python vetool.py info      <build_dir>              # vseal 头 + 签名 + 清单概要
python vetool.py list      <build_dir>              # 列出全部条目
python vetool.py verify    <build_dir>              # 逐层解密并校验
python vetool.py unpack    <build_dir> <out_dir>    # 解包
python vetool.py repack    <work_dir> <out_dir> --src <build_dir>
python vetool.py roundtrip <build_dir>              # 解包+封包+逐字节比对
python vetool.py text      <pkg_dir> [out_dir]      # CBOR 载荷 -> 语义化文本
```

### 解包产物

资产直接还原成原始目录结构，不留原文件副本：

```
out_dir/
  ve_repack.json                 元信息（约 0.5 MB）
  images/bg/bg_black.webp        ← .pak 内资产，路径取自 assets.vpk 的路径表
  audio/bgm/BGM02.ogg
  i18n/  ui/  rule/
  _packages/boot.script.cbor     ← .vpk 的 CBOR 载荷
  _packages/main.font.bin        ← 非 CBOR 载荷
```

直接改文件即可，改完原地封包：

```bash
python vetool.py repack out/ new_build/ --src build/ --signing-key my.key
```

`--src` 指向原 build 目录，仅用于取回**未改动** LZ4 包的压缩字节（引擎的
压缩器无法逐字节复现，见下文）。`.pak` 完全从资产重建，不依赖它。

原始私钥不在 APK 里（只有公钥），所以要么自签并 patch `libmain.so` 里的
`signing_public_key`（偏移 `0x14070F`），要么用 `--no-verify` 跑离线分析。

### 语义化文本

```bash
python vetool.py text unpacked/_packages out_text --language ja
```

把 `_packages` 里的 CBOR 载荷转成可读文本，六类各有专门格式：剧本、字符串表、
资源索引、工程配置、字形度量、控件表。剧本按汇编风格输出，标签前留空行，
指令缩进四格：

```
loc_00000000:
    jmp_if      flag_4D65FFB5 == 0, loc_00000003
    set         flag_4D65FFB5 = 1
    ret

loc_00000003:
    play_sound  channel=music flags=0 asset=audio/bgm/BGM07.ogg volume=1000
    show        layer_1252ACF4, images/fg/響子裸水小.webp, x=487, alpha=255
    .layout     [3, 0, 17, [images/fg/響子裸水小.webp, ...], ...]
    say         "響子", "『……絶対に諦めないから』", audio/voice/kyo0208.ogg
    choice      2
    .option     "プロローグを見る", loc_00000009
    .option     "プロローグを見ない", loc_0000000B
```

文本转义只有两个：`\n` 是引擎换行标记，`\\` 是真实反斜杠。CR、其他控制字节和
私用区字符写成 `{{XX}}` 占位符（私用区按原始 UTF-8 字节成组），全角空格保持原样。
输出里没有任何 `\xNN` 转义或十六进制字节转储。

**哈希还原。** 所有标识符都是 FNV-1a32。台词/人名/选项来自 strings 表，资源来自
assets 路径表，跨 scene 跳转来自 project 的全局标签表（`label_scenes` 存的是场景名
哈希，140 个全部反查得到）。剩下的靠候选词反查，命中即确证：音频频道 `music`、
过渡规则 `dissolve`、17 个 UI 屏里的 13 个。

图层名、flag 名、玩家变量名在编译期就只剩哈希 —— 穷举 5 字符以内小写 ASCII 与
日文常用词都无命中，确实不可恢复。这些统一写成带类型前缀的 `layer_XXXXXXXX` /
`flag_XXXXXXXX` / `var_XXXXXXXX`，不猜名字。

这个子命令是**单向**的：它把哈希换成人名和路径以求最大可读性，代价是无法还原
回 CBOR。要改脚本再装回游戏，用上一级目录的 `disassembler.py` / `assembler.py`
那一对，格式相近但保留哈希原值，往返逐字节一致（见 `../asm_format.md`）。

## 格式

三层嵌套，全部小端：

```
game.vseal  "VESR"  144B 头 + Ed25519 签名 + XChaCha20-Poly1305 清单 (CBOR)
  └─ *.vpk  "VESF"   64B 头 + AEAD 载荷
        └─  "VEPK"   32B 头 + [splitmix64 XOR] + [LZ4] + CBOR + bulk 区
  └─ *.pak  "VESB"   96B 头 + AEAD 目录 (CBOR) + 64 KiB 分块资产
```

### game.vseal (VESR)

| 偏移 | 内容 |
|---|---|
| 0x00 | magic `VESR` |
| 0x04 | u16 version = 2 |
| 0x06 | u16 header_size = 144 |
| 0x08 | u64 manifest_len |
| 0x10 | build_id (16) |
| 0x20 | nonce (24) |
| 0x38 | tag (16) |
| 0x48 | signature (64) |
| 0x88 | 保留 (8) |
| 0x90 | 清单密文 |

摘要**只覆盖密文**（`base+0x90`, `manifest_len` 字节）。签名消息 126 字节：

```
"VersaEngine root signature v2" ‖ 00 ‖ build_id ‖ nonce ‖ tag ‖ u64le(len) ‖ digest
```

清单是 CBOR map，`files` 为 8 元素定长数组（位置编码，无字段名）：

```
[logical_name, stored_name, u16 type, u16 version, u64 size, nonce(24), tag(16), digest(32)]
```

### *.vpk (VESF)

| 偏移 | 内容 |
|---|---|
| 0x00 | magic `VESF` |
| 0x04 | u16 format_version = 2 |
| 0x06 | u16 content_version（与清单 version 比对）|
| 0x08 | u64 payload_size |
| 0x10 | nonce (24) |
| 0x28 | tag (16) |
| 0x38 | 保留 (8) |
| 0x40 | 密文 |

头部字段必须与清单逐一吻合，摘要覆盖**整个文件**（含头）。

### VEPK 内层

| 偏移 | 内容 |
|---|---|
| 0x00 | magic `VEPK` |
| 0x04 | u16 version = 1 |
| 0x06 | u16 flavour |
| 0x08 | u32 flags（bit0 LZ4，bit1 XOR）|
| 0x0C | u32 original_size |
| 0x10 | u32 stored_size |
| 0x14 | u32 checksum（FNV-1a 32，覆盖解压后数据）|
| 0x18 | u64 key_hint |
| 0x20 | 载荷 |

bulk 区起点 `(stored_size + 47) & ~15`。XOR 层是 splitmix64 流，
种子 `splitmix64(GAMMA * key_hint ^ key) | 1`，第 i 个 64 位字用 `seed + i*GAMMA`。
实测本作 151 个包全部 `flags=1`（仅 LZ4）或 `0`，未见启用 XOR。

### *.pak (VESB)

| 偏移 | 内容 |
|---|---|
| 0x00 | magic `VESB` |
| 0x04 | u16 format_version = 2 |
| 0x06 | u16 header_size = 96 |
| 0x08 | u32 directory_size |
| 0x0C | u32 flags |
| 0x10 | u64 total_size（分块区起点）|
| 0x18 | u32 chunk_count（资产条目数，**不是** AEAD 块数）|
| 0x20 | nonce (24) |
| 0x38 | tag (16) |
| 0x48 | chunk_salt (16) |
| 0x58 | 保留 (8) |
| 0x60 | 加密目录 |

清单摘要**只覆盖前 `total_size` 字节**（头 + 目录），其后的分块区由目录内
每块的 `digests` 保护 —— 所以 `images.pak` 有 132 MB 而清单只记 285902。

目录 CBOR 是并行数组：`keys` / `offsets` / `sizes` / `tag_starts` + 拼接的
`tags`(16B each) / `digests`(32B each)。资产按 **64 KiB** 切块，每块独立 AEAD：

```
nonce = chunk_salt(16) ‖ u64le(global_tag_index)
aad   = build_id(16) ‖ u32le(asset_key) ‖ u64le(asset_total_size)
        ‖ u64le(block_index) ‖ u32le(this_block_size)
```

注意 `asset_total_size` 是资产总长，`this_block_size` 是本块长度，二者在
多块资产上不同 —— 这是最容易搞错的地方。

## 密钥体系

根密钥硬编码在 `.rodata`，由两个不相邻的 16 字节常量拼成
（`ve::release_content_key` @ `0x356970`）：

```
content_key = facdbb6400279582ce690604e53e5512   (off 0x0BFFC0)
              704bd425a383953bb2dab5bc60e4f669   (off 0x0BE790)
build_id    = ac73847d53c986e74f7692dbf3faf34d   (off 0x14074F)
pubkey      = ee12ce97...d86e2184                (off 0x14070F)
cert_sha256 = 9eba83f6...b3088a83                (off 0x14072F)
```

派生是 keyed BLAKE2b-256，域分隔符 `0x00`：

```
crypto_derive(key, context, info) = blake2b(key=key, ctx ‖ 00 ‖ info)[0:32]
```

| 用途 | context | info |
|---|---|---|
| 清单 | `release manifest key` | build_id |
| 包 | `release package key` | build_id ‖ name ‖ 00 |
| pak 目录 | `release bundle directory key` | 同上 |
| pak 分块 | `release bundle chunk key` | 同上 |

清单里的 `salt` 字段（槽位 6）其实是 **AEAD tag**，不参与派生 —— per-file
密钥只取决于逻辑名和 build_id。

AAD 构造：

```
清单:  "VersaEngine manifest v2" ‖ 00 ‖ build_id                       (40B)
文件:  "VersaEngine release file v2" ‖ 00 ‖ build_id
       ‖ u16le(type) ‖ u16le(version) ‖ u64le(size) ‖ name ‖ 00       (len+57)
```

## 往返一致如何保证

不存原文件副本，靠三点：

1. **确定性重建**。XChaCha20-Poly1305 在相同 (key, nonce, 明文, AAD) 下输出
   确定，nonce 与派生参数都在元信息里，故密文逐字节可复现。
2. **可推导的不存**。清单条目按逻辑名排序；pak 目录的
   `keys`/`offsets`/`sizes`/`tag_starts`/`tags`/`digests` 全部由资产内容与
   路径哈希决定（key = FNV-1a32(path)，条目按 key 升序，每个资产在分块区里
   按 16 字节对齐）；所有填充字节实测恒为零。这些一律重算。
3. **唯一例外是 LZ4**。引擎压缩器与 python-lz4 任何档位都对不上 —— 实测某包
   引擎输出 31292 字节，落在 `acceleration=10`(28039) 与 `11`(34306) 之间，
   `high_compression` 与分块拼接也都不匹配。所以未改动的 LZ4 包要用 `--src`
   取回压缩字节；改动过的重新压缩，此时字节本就会变。

CBOR 编码器只产生最短形式定长编码，实测与引擎输出完全一致（153 条清单 +
所有包载荷 + 两个 pak 目录均往返相同）。

`repack` 默认 strict：任何"未标记改动却摘要不符"的情况直接报错，而不是
静默产出坏档。用 `--lenient` 可放宽。

已验证：154 个文件（含两个从零重建的 `.pak`，891 MB 分块区全部重新加密）
往返逐字节一致；替换 pak 内单个资产（含 1700 → 736387 字节的跨块替换）后
153 个文件全部校验通过。

## 已知限制

- 私钥不在 APK 内，改档后要么自签并 patch 公钥常量，要么关闭验签。
- APK 签名绑定（`certificate_sha256`）与本工具无关，但重打包在真机上跑
  会撞到它，报 *"The APK was not signed by the certificate this game was built for."*
- `flavour` 字段语义未确认（观测到 0 和 1），XOR 层在本作未启用，
  `cipher_xor` 的实现依据反编译推导，未经真实样本验证。
- `vetool.py text` 生成的语义化文本是**单向只读**的阅读视图。要改脚本请用
  上一级目录的 `disassembler.py` / `assembler.py`，它们双向无损（151 个载荷
  往返逐字节一致）。
- 图层/flag/变量名以及 4 个 UI 屏名不可恢复（见上文），两套工具都只能显示
  `#hash`。

## 文件

| 文件 | 内容 |
|---|---|
| `ve_crypto.py` | BLAKE2b 派生、XChaCha20-Poly1305、Ed25519、splitmix64、FNV-1a |
| `ve_cbor.py` | CBOR 编解码（保序、最短形式、保留未知键）|
| `ve_lz4.py` | LZ4 裸块，含纯 Python 回退 |
| `ve_format.py` | 四种容器的头解析与读写 |
| `ve_archive.py` | 解包/封包编排，路径还原与元信息 |
| `ve_opcodes.py` | 脚本 VM 的 51 个 opcode 定义（43 个 `Game::apply` + 8 个控制流）|
| `ve_text.py` | CBOR 载荷 -> 语义化文本，哈希反查 |
| `vetool.py` | 命令行入口 |

分析文档在上一级目录：

| 文件 | 内容 |
|---|---|
| `../archive_analysis.md` | 封包结构：三层容器、密钥派生、AEAD、签名、往返策略 |
| `../vm_analysis.md` | 虚拟机架构与全量 opcode 表（含佐证地址） |
| `../asm_format.md` | asm.txt 格式规范与零突变验证流程 |

反汇编器 / 汇编器在上一级目录：

| 文件 | 内容 |
|---|---|
| `../opcodelist.py` | asm 词法语法、字符串编解码、指令表转发 |
| `../disassembler.py` | CBOR 载荷 -> `asm.txt` |
| `../assembler.py` | `asm.txt` -> CBOR 载荷 |
| `../verify_roundtrip.py` | 批量往返验证 |
