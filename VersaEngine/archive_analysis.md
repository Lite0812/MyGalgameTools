# VersaEngine 封包结构分析

目标：ままごと ～ママとないしょのエッチしましょ～ v0.9（Android APK，arm64-v8a）。
全部结论取自 `libmain.so` 反编译（`base/lib/arm64-v8a/export-for-ai/`）并经
`vetool.py verify` 在 153 个文件上逐字节验证通过。

## 1. 总览

发行数据放在 APK 的 `assets/build/` 下，共 154 个文件：

| 文件 | 数量 | 说明 |
|---|---|---|
| `game.vseal` | 1 | 根清单，20330 字节 |
| `*.vpk` | 151 | 单体密封文件（type 1） |
| `*.pak` | 2 | 分块资源包（type 2），`audio.pak` 758 MB、`images.pak` 132 MB |

除 `game.vseal` 外，文件名都是 24 位十六进制的存储名（如
`5df7e6699db7d34b07c26972.vpk`），与逻辑名（`00_kaguya_inc.script.vpk`）的
对应关系只存在于清单里。目录本身不泄露任何内容信息。

三层嵌套（`.vpk` 路径）：

```
game.vseal            "VESR"  144B 头 + Ed25519 签名 + AEAD 清单(CBOR)
    └─ *.vpk          "VESF"   64B 头 + AEAD 载荷
           └─ VEPK           32B 头 + [splitmix64 XOR] + [LZ4] + 载荷 + bulk 区
                  └─ CBOR    结构化数据（脚本 / 字符串表 / 资产表 / 字体 / UI）
```

`.pak` 走另一条路径：`VESB` 96B 头 + AEAD 目录（CBOR），目录之后是按
64 KiB 分块、每块独立 AEAD 的原始资源数据。

关键的架构区别：**`.vpk` 整个文件都被清单摘要覆盖，`.pak` 只有目录区被覆盖。**
所以 `images.pak` 实际 132 MB，而清单里 `size` 只记 285902 —— 那是目录长度。
分块数据的完整性由目录内的 `digests` 数组分块保护，这样引擎可以随机读取
单个资源而不必哈希整个 132 MB。

## 2. 密码学原语

| 用途 | 算法 | 地址 |
|---|---|---|
| 摘要 | BLAKE2b-256，无 key | `ve::crypto_digest` 0x345444 |
| 密钥派生 | keyed BLAKE2b-256，`0x00` 域分隔 | `ve::crypto_derive` 0x345274 |
| AEAD | XChaCha20-Poly1305-IETF，detached tag | `ve::crypto_decrypt` 0x345668 |
| 内层流加密 | splitmix64 序列 XOR | `ve::cipher_xor` 0x344FAC |
| 根签名 | Ed25519 | `ve::crypto_verify_message` |
| 标识哈希 | FNV-1a 32 | 校验和与全局 key 双用 |

派生函数：

```
crypto_derive(key, context, info):
    h = blake2b(key=key, digest_size=32)
    h.update(context)
    h.update(b"\x00")          # 域分隔符
    if info: h.update(info)
    return h.digest()
```

### 内置常量（`.rodata`）

| 名称 | 值 | 地址 |
|---|---|---|
| `release_content_key` | `facdbb64…60e4f669`（32B） | 0x356970，由 0x0BFFC0 与 0x0BE790 两个不相邻的 16B 常量池条目拼接 |
| `build_id` | `ac73847d53c986e74f7692dbf3faf34d` | 0x14074F |
| `signing_public_key` | `ee12ce97…d86e2184` | 0x14070F |
| `certificate_sha256` | `9eba83f6…b3088a83` | 0x14072F |

`build_id` 同时充当三个角色：清单密钥的派生 salt、所有 AAD 的一部分、以及
"这份数据属不属于这个构建" 的判别值。改动其中任何一个字节，全部 153 个文件
一起失效。

### 上下文串

| 常量 | 字面量 | 长度 |
|---|---|---|
| `CTX_ROOT_SIGNATURE` | `VersaEngine root signature v2` | 29 |
| `CTX_MANIFEST_AAD` | `VersaEngine manifest v2` | 23 |
| `CTX_FILE_AAD` | `VersaEngine release file v2` | 27 |
| `CTX_MANIFEST_KEY` | `release manifest key` | 20 |
| `CTX_PACKAGE_KEY` | `release package key` | 19 |
| `CTX_BUNDLE_DIR_KEY` | `release bundle directory key` | 28 |
| `CTX_BUNDLE_CHUNK_KEY` | `release bundle chunk key` | 24 |

这些长度不是猜的——反编译里每处 `std::string` 构造都带着明确的长度立即数
（如 `MOV W2, #0x1D` = 29），可以直接反查是哪个串。

## 3. `game.vseal`（VESR）

### 3.1 头（144 字节）

偏移取自 `ve::release_store_activate` 0x356A60 的字段访问序列。

| 偏移 | 大小 | 字段 | 校验 |
|---|---|---|---|
| 0x00 | 4 | magic `"VESR"` | `CMP 0x52455356` |
| 0x04 | 2 | `version` = 2 | `CMP #2` |
| 0x06 | 2 | `header_size` = 144 | `CMP #0x90` |
| 0x08 | 8 | `manifest_len` | 上限 0x400000 |
| 0x10 | 16 | `build_id` | 必须等于内置值 |
| 0x20 | 24 | `nonce` | AEAD nonce |
| 0x38 | 16 | `tag` | detached Poly1305 tag |
| 0x48 | 64 | `signature` | Ed25519 |
| 0x88 | 8 | 保留（本作全零） | |
| 0x90 | — | 清单密文起始 | |

### 3.2 验证与解密顺序

```
digest = blake2b256(file[0x90 : 0x90+manifest_len])      # 只覆盖密文
msg    = "VersaEngine root signature v2" ‖ 0x00 ‖ build_id(16)
         ‖ nonce(24) ‖ tag(16) ‖ u64le(manifest_len) ‖ digest(32)   # 共 126B
ed25519_verify(signature, msg, signing_public_key)

key = crypto_derive(content_key, "release manifest key", info=build_id)
aad = "VersaEngine manifest v2" ‖ 0x00 ‖ build_id(16)                # 共 40B
plain = xchacha20poly1305_ietf_decrypt(cipher, tag, aad, nonce, key)
```

签名覆盖的是**密文的摘要而不是明文**，所以攻击者即便改不了内容也改不了
密文——两者都被 126 字节的规范消息绑住了。126 这个长度在反编译里以
`容量预留 0x7E` 的形式直接出现，是核对字段拼接顺序的有力佐证。

### 3.3 清单（CBOR map）

```
{ "version": 2,
  "files": [ [logical_name, stored_name, type, version, size,
              nonce(24B), tag(16B), digest(32B)], ... ] }
```

8 元素定长数组，槽位与运行时 136 字节结构体的对应（`sub_3571D8` 0x3571D8）：

| 槽 | 类型 | 结构体偏移 | 含义 |
|---|---|---|---|
| 0 | text | +0x00 | 逻辑名 |
| 1 | text | +0x18 | 存储名（磁盘文件名） |
| 2 | u16 | +0x30 | 文件类型 1..4 |
| 3 | u16 | +0x32 | 内容版本 |
| 4 | u64 | +0x38 | 大小 |
| 5 | bytes24 | +0x40 | nonce（`sub_358A78` 检查 len==0x18） |
| 6 | bytes16 | +0x58 | **AEAD tag**（`sub_358AD8` 检查 len==0x10） |
| 7 | bytes32 | +0x68 | BLAKE2b 摘要（`sub_358B30` 检查 type==3 且 len==0x20） |

槽 6 一开始容易误判成 salt。判据是 `unseal_package` 里 `crypto_decrypt` 的
第 4 个参数取 `v6+88`（= +0x58），那个位置正是 detached tag 的入口。

文件类型与密钥标签：

| type | 用途 | 派生标签 | 本作数量 |
|---|---|---|---|
| 1 | package（`.vpk`） | `release package key` | 151 |
| 2 | bundle directory（`.pak`） | `release bundle directory key` | 2 |
| 3 | bundle chunk | `release bundle chunk key` | 0（分块在 `.pak` 内部） |
| 4 | aux | `release package key` | 0 |

## 4. `*.vpk`（VESF）

### 4.1 头（64 字节）

字段对照取自 `unseal_package` 0x3585D4-0x358700。

| 偏移 | 大小 | 字段 | 与清单的关系 |
|---|---|---|---|
| 0x00 | 4 | magic `"VESF"` | |
| 0x04 | 2 | `format_version` = 2 | |
| 0x06 | 2 | `content_version` | 与槽 3 比对 |
| 0x08 | 8 | `payload_size` | 与槽 4 比对 |
| 0x10 | 24 | `nonce` | 与槽 5 逐 qword 比对 |
| 0x28 | 16 | `tag` | 与槽 6 比对 |
| 0x38 | 8 | 保留 | |
| 0x40 | — | 密文起始 | |

0x10 起的 5 个 qword 通过 0x3585F8-0x358684 的一串 `LDP`/`CCMP` 与清单比对。
这种把同一份元数据在两处冗余存储再交叉验证的做法，使得单独替换 `.vpk`
而不同步改清单必然失败。

### 4.2 解封

```
digest = blake2b256(整个文件，含 64B 头)     # 注意：与 vseal 不同，这里含头
assert digest == manifest.digest

key = crypto_derive(content_key, "release package key",
                    info = build_id ‖ logical_name ‖ 0x00)
aad = "VersaEngine release file v2" ‖ 0x00 ‖ build_id(16)
      ‖ u16le(type) ‖ u16le(version) ‖ u64le(size) ‖ logical_name ‖ 0x00
plain = xchacha20poly1305_ietf_decrypt(file[64:], tag, aad, nonce, key)
```

密钥的 info 里带逻辑名（`ve::release_key_context` 0x355F8C：
`build_id(16) ‖ name ‖ 0x00`），所以**每个文件一把独立密钥**。把
`ja.strings.vpk` 的字节挪到 `en.strings.vpk` 的位置解不开。

清单里的 salt 字段不参与派生——这一点容易搞错。`release_store_file_key`
0x3583C0 的调用序只用到 `content_key`、标签和 `release_key_context` 的输出，
salt 纯粹是冗余校验字段。

## 5. VEPK 内层包

### 5.1 头（32 字节）

偏移取自 `ve::Package::parse` 0x348D0C 的加载序列 0x348E00-0x348E2C。

| 偏移 | 大小 | 字段 | 说明 |
|---|---|---|---|
| 0x00 | 4 | magic `"VEPK"` | `CMP 0x4B506556` |
| 0x04 | 2 | `version` = 1 | |
| 0x06 | 2 | `flavour` | → `this+0x84`，载荷种类 |
| 0x08 | 4 | `flags` | → `this+0x80`，bit0 压缩，bit1 加密 |
| 0x0C | 4 | `original_size` | LZ4 解压目标长度 |
| 0x10 | 4 | `stored_size` | 载荷实际长度 |
| 0x14 | 4 | `checksum` | FNV-1a 32，覆盖**解压后**数据 |
| 0x18 | 8 | `key_hint` | → `this+0x78`，混入 splitmix64 种子 |
| 0x20 | — | 载荷起始 | |

`bulk` 区起点 = `(stored_size + 47) & ~15`，即载荷末尾对齐到 16 字节再跳过
32 字节头（`Pack.cpp` 中 `v33` 的计算）。本作全部 151 个包 bulk 区都是空的。

### 5.2 本作实测

| flavour | 数量 | 内容 |
|---|---|---|
| 1 | 140 | `*.script.vpk` 脚本 |
| 2 | 6 | `*.strings.vpk` 文本表（en/ja/zh-CN × 正文/UI） |
| 3 | 1 | `assets.vpk` 资产路径表 |
| 4 | 2 | `*.font.vpk` 字体 |
| 5 | 1 | `project.vpk` 工程元数据与全局标签表 |
| 6 | 1 | `ui.vpk` UI 定义 |

`flags`：149 个为 `0x1`（仅压缩），2 个为 `0x0`（原样）。**没有一个包设置
`0x2` 加密位**——外层 AEAD 已经足够，内层的 splitmix64 XOR 在本作里根本没用上。
压缩率：7,722,648 → 4,175,112（54.1%）。全部 `key_hint` 为 0。

### 5.3 splitmix64 流加密（代码存在但未启用）

```
seed = pack_stream_seed(key_hint, key)
每 8 字节推进一次 splitmix64，与载荷 XOR
```

`Pack.cpp:127` 在 `flags & 0x2` 但 `key == 0` 时报错。本作走不到这条路径，
但工具仍实现了它以覆盖引擎全部行为。

## 6. `*.pak`（VESB）

### 6.1 头（96 字节）

字段读取序列取自 0x1FBC80-0x1FBD58。

| 偏移 | 大小 | 字段 | 校验 |
|---|---|---|---|
| 0x00 | 4 | magic `"VESB"` | |
| 0x04 | 2 | `format_version` = 2 | `CMP #2` |
| 0x06 | 2 | `header_size` = 96 | `CMP #0x60` |
| 0x08 | 4 | `directory_size` | 与清单 size 比对（`CMP X8,X25`） |
| 0x0C | 4 | `flags` | 本作 `0x10000` |
| 0x10 | 8 | `total_size` | 须落在 `[dir+96, 映射长度]` 内 |
| 0x18 | 4 | `chunk_count` | 资产条目数（**不是** AEAD 块数） |
| 0x20 | 24 | `nonce` | 与清单槽 5 比对 |
| 0x38 | 16 | `tag` | 与清单槽 6 比对 |
| 0x48 | 16 | `directory_digest` | 目录区独立摘要前缀 |
| 0x58 | 8 | 保留 | |
| 0x60 | — | 加密目录起始 | |

`total_size = (96 + directory_size + 15) & ~15`，即分块数据区的起点。
清单摘要只覆盖 `file[:total_size]`。

### 6.2 目录（CBOR，并行数组）

```
{ "version": …, "keys": [u32…], "offsets": [u64…], "sizes": [u64…],
  "tag_starts": [u32…], "tags": bytes, "digests": bytes }
```

`keys` 是资源路径的 FNV-1a 32。`tags`/`digests` 是拼接的字节串，
`tag_starts[i]` 给出第 i 个资产的第一个块在其中的下标。每个资产按 64 KiB
分块（`BUNDLE_BLOCK_SIZE = 0x10000`，来自 `read_sealed` 的 `MOV W8,#0x10000`），
块数 = `ceil(size / 64KiB)`，空资产按 1 块计。

实测：

| 文件 | 总大小 | 目录 | 条目 | tags | digests | 资产合计 |
|---|---|---|---|---|---|---|
| `audio.pak` | 758,144,112 | 961,973 | 9,898 | 264,384 B | 528,768 B | 757,107,515 |
| `images.pak` | 132,325,952 | 285,902 | 3,546 | 75,824 B | 151,648 B | 132,014,687 |

`264384 / 16 = 16524` 块，`528768 / 32 = 16524` 摘要——数量吻合，确认
`tags` 每块 16 字节、`digests` 每块 32 字节。

### 6.3 分块 AEAD

```
nonce = salt(16) ‖ u64le(block_index)                # release_chunk_nonce 0x356958
aad   = build_id(16) ‖ u32le(asset_key) ‖ u64le(decoded_size)
        ‖ u64le(block_index) ‖ u32le(chunk_size)     # 40B 定长, release_chunk_aad 0x35689C
key   = crypto_derive(content_key, "release bundle chunk key",
                      info = build_id ‖ logical_name ‖ 0x00)
```

nonce 把 salt 与块号拼起来，所以同一文件内的块永不重用 nonce；AAD 把资产 key、
块号、块长都绑进去，块之间不能互换位置。整包只需一次密钥派生，逐块解密时
只重算 nonce 和 AAD——这是为随机读取设计的。

## 7. 往返一致性策略

难点在于：AEAD 与哈希都要求逐字节复现，但压缩不要求。

**可复现的部分**——XChaCha20-Poly1305 在给定 (key, nonce, plaintext, AAD) 下
是确定性的，BLAKE2b 和 FNV-1a 也是。所以解包时只需保存 nonce 和不可推导的
元数据（`stored_name`、`version`、`flavour`、保留字节），密文、tag、摘要、
校验和全部可以重新算出来。

**不可复现的部分**——LZ4 是唯一例外。原始数据由某个具体的 LZ4 编码器产生，
不同实现的输出字节不同，即使解压结果一致。实测 `00_kaguya_inc.script.vpk`
的目标压缩长度 31292 落在 python-lz4 `acc=10` 的 28039 和 `acc=11` 的
34306 之间，无法通过调参命中。

解法是**未改动的包按原始载荷字节回写**：`write_package` 的 `raw_stored`
参数直接写入原压缩字节，跳过压缩与加密。修改过的包才走真正的压缩路径，
此时字节不再与原文件相同，但引擎照样能加载（因为它只校验解压结果的
FNV-1a）。同理 `write_seal` 的 `plaintext` 参数保留原清单明文，避免 CBOR
键序重编码带来的差异。

签名是另一个约束：Ed25519 私钥不在 APK 里，所以重签名不可能。`write_seal`
的 `signature` 参数原样写回原签名——只要清单密文没变，签名依然有效。改动
清单内容就必须自行提供私钥，或者 patch 掉引擎的验签。

**验证结果**：`python vetool.py verify base/assets/build` → 153 通过 / 0 失败。

## 8. 佐证地址索引

| 函数 | 地址 | 源文件行 |
|---|---|---|
| `ve::release_store_activate` | 0x356A60 | ReleaseStore.cpp:121-221 |
| 清单 CBOR 解析 `sub_3571D8` | 0x3571D8 | ReleaseStore.cpp:49-64 |
| `ve::release_store_unseal_package` | 0x358498 | ReleaseStore.cpp:318-356 |
| `ve::release_root_signature_message` | 0x3567CC | |
| `ve::release_manifest_aad` | 0x3561F8 | 预留 0x28 = 40 |
| `ve::release_file_aad` | 0x356254 | 预留 name_len + 57 |
| `ve::release_store_file_key` | 0x3583C0 | |
| `ve::release_key_context` | 0x355F8C | 预留 name_len + 17 |
| `ve::release_chunk_nonce` | 0x356958 | |
| `ve::release_chunk_aad` | 0x35689C | |
| `ve::release_content_key` | 0x356970 | |
| `ve::Package::parse` | 0x348D0C | Pack.cpp:94-161 |
| `ve::crypto_digest` | 0x345444 | |
| `ve::crypto_derive` | 0x345274 | |
| `ve::crypto_decrypt` | 0x345668 | |
| `ve::cipher_xor` | 0x344FAC | |
| bundle 头解析 | 0x1FBC80 | |
