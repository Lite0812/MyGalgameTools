"""VersaEngine 三层容器格式的读写。

层次结构::

    game.vseal  "VSER"  144B 头 + Ed25519 签名 + AEAD 清单 (CBOR)
        └─ *.vpk  "VESF"  64B 头 + AEAD 载荷
               └─ "VePK"  32B 头 + [splitmix64 XOR] + [LZ4] + 载荷 + bulk 区

依据 (libmain.so arm64-v8a)::

    ve::release_store_activate         0x356A60  ReleaseStore.cpp:121-221
    sub_3571D8  清单 CBOR 解析          0x3571D8  ReleaseStore.cpp:49-64
    ve::release_store_unseal_package   0x358498  ReleaseStore.cpp:318-356
    ve::release_root_signature_message 0x3567CC
    ve::release_manifest_aad           0x3561F8
    ve::release_file_aad               0x356254
    ve::release_store_file_key         0x3583C0
    ve::release_key_context            0x355F8C
    ve::Package::parse                 0x348D0C  Pack.cpp:94-161
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

import ve_cbor
import ve_lz4
from ve_cbor import CborMap
from ve_crypto import (
    ABYTES,
    BUILD_ID,
    CONTENT_KEY,
    CTX_BUNDLE_CHUNK_KEY,
    CTX_BUNDLE_DIR_KEY,
    CTX_PACKAGE_KEY,
    NPUBBYTES,
    SALTBYTES,
    SIGNING_PUBLIC_KEY,
    VeAuthError,
    cipher_xor,
    crypto_decrypt,
    crypto_derive,
    crypto_digest,
    crypto_encrypt,
    crypto_sign_message,
    crypto_verify_message,
    fnv1a32,
    pack_stream_seed,
)

MAGIC_VSEAL = b"VESR"  # CMP 1381188950 = 0x52455356 小端
MAGIC_VESF = b"VESF"  # CMP 1179862358 = 0x46534556 小端
MAGIC_VEPK = b"VEPK"  # CMP 1263551830 = 0x4B505356 小端

VSEAL_HEADER_SIZE = 144
VESF_HEADER_SIZE = 64
VEPK_HEADER_SIZE = 32

VSEAL_VERSION = 2
VESF_VERSION = 2
VEPK_VERSION = 1

MANIFEST_MAX = 0x400000  # ReleaseStore.cpp:144

VEPK_FLAG_COMPRESSED = 0x1
VEPK_FLAG_ENCRYPTED = 0x2

# AAD / 签名 / 派生用的上下文串，长度取自反编译中的立即数
CTX_ROOT_SIGNATURE = b"VersaEngine root signature v2"  # 0x1D = 29
CTX_MANIFEST_AAD = b"VersaEngine manifest v2"  # 0x17 = 23
CTX_FILE_AAD = b"VersaEngine release file v2"  # 0x1B = 27
CTX_MANIFEST_KEY = b"release manifest key"  # 0x14 = 20

FILE_TYPE_PACKAGE = 1
FILE_TYPE_BUNDLE_DIR = 2
FILE_TYPE_BUNDLE_CHUNK = 3
FILE_TYPE_AUX = 4

# release_store_file_key 的标签串按文件类型选取
FILE_TYPE_KEY_LABEL = {
    FILE_TYPE_PACKAGE: CTX_PACKAGE_KEY,
    FILE_TYPE_BUNDLE_DIR: CTX_BUNDLE_DIR_KEY,
    FILE_TYPE_BUNDLE_CHUNK: CTX_BUNDLE_CHUNK_KEY,
    FILE_TYPE_AUX: CTX_PACKAGE_KEY,
}


class VeFormatError(Exception):
    pass


# ---------------------------------------------------------------------------
# AAD / 密钥派生
# ---------------------------------------------------------------------------


def release_key_context(build_id: bytes, logical_name: str) -> bytes:
    """ve::release_key_context (0x355F8C)。

    容量预留 name_len + 17 → build_id(16) ‖ name ‖ 0x00。
    """
    return build_id + logical_name.encode("utf-8") + b"\x00"


def release_store_file_key(
    logical_name: str,
    label: bytes,
    build_id: bytes = BUILD_ID,
    content_key: bytes = CONTENT_KEY,
) -> bytes:
    """ve::release_store_file_key (0x3583C0)。

    调用序为 release_key_context(build_id, name) → crypto_derive(
    content_key, label, context)，label 由调用方按文件类型给出。
    注意 salt 不参与派生 —— 它只是清单里的冗余校验字段。
    """
    return crypto_derive(content_key, label, release_key_context(build_id, logical_name))


def manifest_key(build_id: bytes = BUILD_ID, content_key: bytes = CONTENT_KEY) -> bytes:
    """清单密钥 (ReleaseStore.cpp:~178)。

    crypto_derive(content_key, "release manifest key", info=header[0x10:0x20])
    —— 该 16 字节字段既是 build_id 校验值，也充当派生 salt。
    """
    return crypto_derive(content_key, CTX_MANIFEST_KEY, build_id)


def manifest_aad(build_id: bytes = BUILD_ID) -> bytes:
    """ve::release_manifest_aad (0x3561F8) —— 预留 0x28 = 23+1+16。"""
    aad = CTX_MANIFEST_AAD + b"\x00" + build_id
    if len(aad) != 0x28:
        raise VeFormatError(f"清单 AAD 应为 40 字节，实为 {len(aad)}")
    return aad


def file_aad(
    logical_name: str,
    file_type: int,
    version: int,
    size: int,
    build_id: bytes = BUILD_ID,
) -> bytes:
    """ve::release_file_aad (0x356254) —— 预留 name_len + 57。

    追加顺序取自 0x3562B8-0x35653C::

        "VersaEngine release file v2"(27) ‖ 0x00
          ‖ build_id(16) ‖ u16le(type) ‖ u16le(version) ‖ u64le(size)
          ‖ name ‖ 0x00
    """
    name = logical_name.encode("utf-8")
    aad = bytearray()
    aad += CTX_FILE_AAD
    aad += b"\x00"
    aad += build_id
    aad += struct.pack("<H", file_type)
    aad += struct.pack("<H", version)
    aad += struct.pack("<Q", size)
    aad += name
    aad += b"\x00"
    if len(aad) != len(name) + 57:
        raise VeFormatError(f"文件 AAD 应为 {len(name) + 57} 字节，实为 {len(aad)}")
    return bytes(aad)


def root_signature_message(
    build_id: bytes, nonce: bytes, tag: bytes, manifest_len: int, digest: bytes
) -> bytes:
    """ve::release_root_signature_message (0x3567CC) —— 预留 0x7E = 126。

    "VersaEngine root signature v2"(29) ‖ 0x00 ‖ build_id(16)
      ‖ nonce(24) ‖ tag(16) ‖ u64le(manifest_len) ‖ digest(32)
    """
    msg = bytearray()
    msg += CTX_ROOT_SIGNATURE
    msg += b"\x00"
    msg += build_id
    msg += nonce
    msg += tag
    msg += struct.pack("<Q", manifest_len)
    msg += digest
    if len(msg) != 126:
        raise VeFormatError(f"签名消息应为 126 字节，实为 {len(msg)}")
    return bytes(msg)


# ---------------------------------------------------------------------------
# 清单
# ---------------------------------------------------------------------------


@dataclass
class ManifestFile:
    """清单 files[] 中的一条，对应 136 字节运行时结构体。

    CBOR 为 8 元素定长数组，槽位与结构体偏移取自 sub_3571D8::

        0 logical_name text    → +0x00 std::string
        1 stored_name  text    → +0x18 std::string
        2 file_type    u16     → +0x30
        3 version      u16     → +0x32
        4 size         u64     → +0x38
        5 nonce        bytes24 → +0x40   (sub_358A78 检查 len==0x18)
        6 tag          bytes16 → +0x58   (sub_358AD8 检查 len==0x10)
        7 digest       bytes32 → +0x68   (sub_358B30 检查 type==3 且 len==0x20)

    槽位 6 是 AEAD 认证标签，不是 salt —— unseal_package 中
    crypto_decrypt 的第 4 参数取 v6+88 (=+0x58)，即该字段。
    """

    logical_name: str
    stored_name: str
    file_type: int
    version: int
    size: int
    nonce: bytes
    tag: bytes
    digest: bytes

    def to_cbor(self) -> list:
        return [
            self.logical_name,
            self.stored_name,
            self.file_type,
            self.version,
            self.size,
            self.nonce,
            self.tag,
            self.digest,
        ]

    @classmethod
    def from_cbor(cls, arr: Any) -> ManifestFile:
        if not isinstance(arr, list) or len(arr) != 8:
            got = len(arr) if isinstance(arr, list) else type(arr).__name__
            raise VeFormatError(f"文件描述符必须是 8 元素数组，收到 {got}")
        name, stored, ftype, ver, size, nonce, tag, digest = arr
        if not isinstance(name, str) or not isinstance(stored, str):
            raise VeFormatError("文件描述符前两项必须是文本")
        if not isinstance(nonce, bytes) or len(nonce) != NPUBBYTES:
            raise VeFormatError(f"'{name}' nonce 必须 24 字节")
        if not isinstance(tag, bytes) or len(tag) != ABYTES:
            raise VeFormatError(f"'{name}' tag 必须 16 字节")
        if not isinstance(digest, bytes) or len(digest) != 32:
            raise VeFormatError(f"'{name}' digest 必须 32 字节")
        if not 1 <= ftype <= 4:
            raise VeFormatError(f"'{name}' file_type {ftype} 超出 1..4")
        return cls(name, stored, ftype, ver, size, nonce, tag, digest)

    def key_label(self) -> bytes:
        return FILE_TYPE_KEY_LABEL.get(self.file_type, CTX_PACKAGE_KEY)


@dataclass
class Manifest:
    """解密后的清单 (CBOR map)。extra 保留未识别键以维持往返一致。"""

    version: int = VSEAL_VERSION
    files: list[ManifestFile] = field(default_factory=list)
    extra: list[tuple] = field(default_factory=list)

    def find(self, logical_name: str) -> ManifestFile | None:
        for f in self.files:
            if f.logical_name == logical_name:
                return f
        return None

    def to_cbor(self) -> CborMap:
        pairs: list[tuple] = [
            ("version", self.version),
            ("files", [f.to_cbor() for f in self.files]),
        ]
        pairs.extend(self.extra)
        return CborMap(pairs)

    @classmethod
    def from_cbor(cls, value: Any) -> Manifest:
        if not isinstance(value, CborMap):
            raise VeFormatError(f"清单根必须是 map，收到 {type(value).__name__}")
        version = value.get("version")
        if version != VSEAL_VERSION:
            raise VeFormatError(f"清单版本 {version!r}，期望 {VSEAL_VERSION}")
        raw = value.get("files")
        if not isinstance(raw, list):
            raise VeFormatError("清单缺少 files 数组")
        files = [ManifestFile.from_cbor(item) for item in raw]

        seen_logical: set[str] = set()
        seen_stored: set[str] = set()
        for f in files:
            if f.logical_name in seen_logical:
                raise VeFormatError(f"清单中逻辑名重复: '{f.logical_name}'")
            if f.stored_name in seen_stored:
                raise VeFormatError(f"清单中存储名重复: '{f.stored_name}'")
            seen_logical.add(f.logical_name)
            seen_stored.add(f.stored_name)

        extra = [(k, v) for k, v in value.pairs if k not in ("version", "files")]
        return cls(version=version, files=files, extra=extra)


# ---------------------------------------------------------------------------
# game.vseal (VSER)
# ---------------------------------------------------------------------------


@dataclass
class SealHeader:
    """VSER 144 字节头。偏移取自 release_store_activate 的字段访问::

        0x00 magic "VSER"       0x04 u16 version=2
        0x06 u16 header_size    0x08 u64 manifest_len
        0x10 build_id(16)       0x20 nonce(24)
        0x38 tag(16)            0x48 signature(64)
        0x88 保留 8 字节         0x90 密文起始
    """

    version: int = VSEAL_VERSION
    header_size: int = VSEAL_HEADER_SIZE
    manifest_len: int = 0
    build_id: bytes = BUILD_ID
    nonce: bytes = b"\x00" * NPUBBYTES
    tag: bytes = b"\x00" * ABYTES
    signature: bytes = b"\x00" * 64
    reserved: bytes = b"\x00" * 8

    def pack(self) -> bytes:
        out = bytearray(VSEAL_HEADER_SIZE)
        out[0:4] = MAGIC_VSEAL
        struct.pack_into("<H", out, 4, self.version)
        struct.pack_into("<H", out, 6, self.header_size)
        struct.pack_into("<Q", out, 8, self.manifest_len)
        out[0x10:0x20] = self.build_id
        out[0x20:0x38] = self.nonce
        out[0x38:0x48] = self.tag
        out[0x48:0x88] = self.signature
        out[0x88:0x90] = self.reserved
        return bytes(out)

    @classmethod
    def unpack(cls, blob: bytes) -> SealHeader:
        if len(blob) < VSEAL_HEADER_SIZE:
            raise VeFormatError(f"vseal 头需 144 字节，收到 {len(blob)}")
        if blob[0:4] != MAGIC_VSEAL:
            raise VeFormatError(f"vseal 魔数错误: {blob[0:4]!r}")
        version, header_size = struct.unpack_from("<HH", blob, 4)
        if version != VSEAL_VERSION:
            raise VeFormatError(f"vseal 版本 {version}，期望 {VSEAL_VERSION}")
        if header_size != VSEAL_HEADER_SIZE:
            raise VeFormatError(f"vseal 头长 {header_size}，期望 144")
        (manifest_len,) = struct.unpack_from("<Q", blob, 8)
        if manifest_len > MANIFEST_MAX:
            raise VeFormatError(f"清单长度 {manifest_len} 超过上限 {MANIFEST_MAX}")
        return cls(
            version=version,
            header_size=header_size,
            manifest_len=manifest_len,
            build_id=blob[0x10:0x20],
            nonce=blob[0x20:0x38],
            tag=blob[0x38:0x48],
            signature=blob[0x48:0x88],
            reserved=blob[0x88:0x90],
        )


def read_seal(
    blob: bytes,
    content_key: bytes = CONTENT_KEY,
    public_key: bytes = SIGNING_PUBLIC_KEY,
    expect_build_id: bytes | None = BUILD_ID,
    verify_signature: bool = True,
) -> tuple[SealHeader, Manifest, bytes]:
    """解析并验证 game.vseal，返回 (头, 清单, 清单明文)。"""
    hdr = SealHeader.unpack(blob)
    if expect_build_id is not None and hdr.build_id != expect_build_id:
        raise VeAuthError(
            f"数据属于其它构建: 文件 {hdr.build_id.hex()} vs 运行时 {expect_build_id.hex()}"
        )
    body = blob[VSEAL_HEADER_SIZE:]
    if len(body) != hdr.manifest_len:
        raise VeFormatError(
            f"清单长度不符: 头声明 {hdr.manifest_len}，实际 {len(body)}"
        )

    # ReleaseStore.cpp:~150 —— 摘要只覆盖密文 (base+0x90, manifest_len)
    digest = crypto_digest(body)
    if verify_signature:
        msg = root_signature_message(
            hdr.build_id, hdr.nonce, hdr.tag, hdr.manifest_len, digest
        )
        if not crypto_verify_message(hdr.signature, msg, public_key):
            raise VeAuthError("根签名无效")

    key = manifest_key(hdr.build_id, content_key)
    plain = crypto_decrypt(body, hdr.tag, manifest_aad(hdr.build_id), hdr.nonce, key)
    return hdr, Manifest.from_cbor(ve_cbor.loads(plain)), plain


def write_seal(
    manifest: Manifest,
    nonce: bytes,
    build_id: bytes = BUILD_ID,
    signing_key: bytes | None = None,
    signature: bytes | None = None,
    content_key: bytes = CONTENT_KEY,
    plaintext: bytes | None = None,
    reserved: bytes = b"\x00" * 8,
) -> bytes:
    """构造 game.vseal。

    plaintext 非空时直接采用该清单明文，跳过重新编码 —— 这是往返一致的
    保障。signature 非空时原样写入；否则用 signing_key 现场签名；两者
    皆空则留全零签名（引擎会拒绝，仅用于离线检查结构）。
    """
    body_plain = plaintext if plaintext is not None else ve_cbor.dumps(manifest.to_cbor())
    key = manifest_key(build_id, content_key)
    cipher, tag = crypto_encrypt(body_plain, manifest_aad(build_id), nonce, key)

    if signature is None and signing_key is not None:
        digest = crypto_digest(cipher)
        msg = root_signature_message(build_id, nonce, tag, len(cipher), digest)
        signature = crypto_sign_message(msg, signing_key)

    hdr = SealHeader(
        manifest_len=len(cipher),
        build_id=build_id,
        nonce=nonce,
        tag=tag,
        signature=signature if signature is not None else b"\x00" * 64,
        reserved=reserved,
    )
    return hdr.pack() + cipher


# ---------------------------------------------------------------------------
# *.vpk (VESF)
# ---------------------------------------------------------------------------


@dataclass
class SealedFileHeader:
    """VESF 64 字节头。字段比对取自 unseal_package 0x3585D4-0x358700::

        0x00 magic "VESF"       0x04 u16 format_version=2
        0x06 u16 content_version 0x08 u64 payload_size
        0x10 nonce(24)          0x28 tag(16)
        0x38 保留 8 字节         0x40 密文起始

    0x06 与清单槽位 3 (version) 比对，0x10 起 5 个 qword 与清单
    槽位 5/6 逐一比对 (0x3585F8-0x358684 的 LDP/CCMP 序列)。
    """

    format_version: int = VESF_VERSION
    content_version: int = 0
    payload_size: int = 0
    nonce: bytes = b"\x00" * NPUBBYTES
    tag: bytes = b"\x00" * ABYTES
    reserved: bytes = b"\x00" * 8

    def pack(self) -> bytes:
        out = bytearray(VESF_HEADER_SIZE)
        out[0:4] = MAGIC_VESF
        struct.pack_into("<H", out, 4, self.format_version)
        struct.pack_into("<H", out, 6, self.content_version)
        struct.pack_into("<Q", out, 8, self.payload_size)
        out[0x10:0x28] = self.nonce
        out[0x28:0x38] = self.tag
        out[0x38:0x40] = self.reserved
        return bytes(out)

    @classmethod
    def unpack(cls, blob: bytes) -> SealedFileHeader:
        if len(blob) < VESF_HEADER_SIZE:
            raise VeFormatError(f"vpk 头需 64 字节，收到 {len(blob)}")
        if blob[0:4] != MAGIC_VESF:
            raise VeFormatError(f"vpk 魔数错误: {blob[0:4]!r}")
        format_version, content_version = struct.unpack_from("<HH", blob, 4)
        if format_version != VESF_VERSION:
            raise VeFormatError(f"vpk 格式版本 {format_version}，期望 {VESF_VERSION}")
        (payload_size,) = struct.unpack_from("<Q", blob, 8)
        return cls(
            format_version=format_version,
            content_version=content_version,
            payload_size=payload_size,
            nonce=blob[0x10:0x28],
            tag=blob[0x28:0x38],
            reserved=blob[0x38:0x40],
        )


def read_sealed_file(
    blob: bytes,
    entry: ManifestFile,
    content_key: bytes = CONTENT_KEY,
    build_id: bytes = BUILD_ID,
    check_digest: bool = True,
) -> tuple[SealedFileHeader, bytes]:
    """解封单个 vpk。entry 提供权威的 type/version/size/nonce/salt/digest。"""
    hdr = SealedFileHeader.unpack(blob)

    # ReleaseStore.cpp:~332 头字段必须与清单逐一吻合
    if hdr.content_version != entry.version:
        raise VeAuthError(
            f"'{entry.logical_name}' 版本不符: 头 {hdr.content_version} "
            f"vs 清单 {entry.version}"
        )
    if hdr.payload_size != entry.size:
        raise VeAuthError(
            f"'{entry.logical_name}' 大小不符: 头 {hdr.payload_size} vs 清单 {entry.size}"
        )
    if len(blob) - VESF_HEADER_SIZE != entry.size:
        raise VeAuthError(
            f"'{entry.logical_name}' 文件长度不符: 实际载荷 "
            f"{len(blob) - VESF_HEADER_SIZE} vs 清单 {entry.size}"
        )
    if hdr.tag != entry.tag:
        raise VeAuthError(f"'{entry.logical_name}' tag 与清单不符")
    if hdr.nonce != entry.nonce:
        raise VeAuthError(f"'{entry.logical_name}' nonce 与清单不符")

    # ReleaseStore.cpp:~338 摘要覆盖整个文件（含头）
    if check_digest:
        actual = crypto_digest(blob)
        if actual != entry.digest:
            raise VeAuthError(
                f"'{entry.logical_name}' 摘要无效: {actual.hex()} != {entry.digest.hex()}"
            )

    key = release_store_file_key(
        entry.logical_name, entry.key_label(), build_id, content_key
    )
    aad = file_aad(
        entry.logical_name, entry.file_type, entry.version, entry.size, build_id
    )
    # crypto_decrypt(out, blob+64, size, v6+88, aad, aad_len, nonce, key)
    # 第 4 参数 v6+88 (=+0x58) 即清单槽位 6，作为 detached tag。
    plain = crypto_decrypt(blob[VESF_HEADER_SIZE:], entry.tag, aad, entry.nonce, key)
    return hdr, plain


def write_sealed_file(
    plaintext: bytes,
    logical_name: str,
    file_type: int,
    version: int,
    nonce: bytes,
    content_key: bytes = CONTENT_KEY,
    build_id: bytes = BUILD_ID,
    reserved: bytes = b"\x00" * 8,
) -> tuple[bytes, ManifestFile]:
    """封装单个 vpk，返回 (文件字节, 对应清单条目)。

    stored_name 需由调用方填入（清单里的物理文件名），此处留空。
    """
    label = FILE_TYPE_KEY_LABEL.get(file_type, CTX_PACKAGE_KEY)
    key = release_store_file_key(logical_name, label, build_id, content_key)
    # AAD 中的 size 是密文长度，XChaCha20 为流密码故等于明文长度。
    aad = file_aad(logical_name, file_type, version, len(plaintext), build_id)
    cipher, tag = crypto_encrypt(plaintext, aad, nonce, key)

    hdr = SealedFileHeader(
        content_version=version,
        payload_size=len(cipher),
        nonce=nonce,
        tag=tag,
        reserved=reserved,
    )
    blob = hdr.pack() + cipher
    entry = ManifestFile(
        logical_name=logical_name,
        stored_name="",
        file_type=file_type,
        version=version,
        size=len(cipher),
        nonce=nonce,
        tag=tag,
        digest=crypto_digest(blob),
    )
    return blob, entry


# ---------------------------------------------------------------------------
# VePK 内层包
# ---------------------------------------------------------------------------


@dataclass
class PackHeader:
    """VePK 32 字节头。偏移取自 0x348E00-0x348E2C 的加载序列::

        0x00 magic "VePK"        (CMP 1263551830 = 0x4B506556)
        0x04 u16 version=1       (CMP #1)
        0x06 u16 flavour         → this+0x84
        0x08 u32 flags           → this+0x80  (bit0 压缩, bit1 加密)
        0x0C u32 original_size   (v11, lz4 解压目标长度)
        0x10 u32 stored_size     (v10, 载荷实际长度)
        0x14 u32 checksum        (v12, FNV-1a 32 覆盖解压后数据)
        0x18 u64 key_hint        → this+0x78  (参与 splitmix64 种子混合)
        0x20 载荷起始

    bulk 区起点为 (stored_size + 47) & ~15，即载荷后对齐到 16 字节
    再跳过 32 字节头 —— 见 0x348D0C 中 v33 的计算。
    """

    version: int = VEPK_VERSION
    flavour: int = 0
    flags: int = 0
    original_size: int = 0
    stored_size: int = 0
    checksum: int = 0
    key_hint: int = 0

    @property
    def compressed(self) -> bool:
        return bool(self.flags & VEPK_FLAG_COMPRESSED)

    @property
    def encrypted(self) -> bool:
        return bool(self.flags & VEPK_FLAG_ENCRYPTED)

    def pack(self) -> bytes:
        out = bytearray(VEPK_HEADER_SIZE)
        out[0:4] = MAGIC_VEPK
        struct.pack_into("<H", out, 4, self.version)
        struct.pack_into("<H", out, 6, self.flavour)
        struct.pack_into("<I", out, 8, self.flags)
        struct.pack_into("<I", out, 12, self.original_size)
        struct.pack_into("<I", out, 16, self.stored_size)
        struct.pack_into("<I", out, 20, self.checksum)
        struct.pack_into("<Q", out, 24, self.key_hint)
        return bytes(out)

    @classmethod
    def unpack(cls, blob: bytes) -> PackHeader:
        if len(blob) < VEPK_HEADER_SIZE:
            raise VeFormatError(f"包头需 32 字节，收到 {len(blob)}")
        if blob[0:4] != MAGIC_VEPK:
            raise VeFormatError(f"包魔数错误: {blob[0:4]!r}")
        version, flavour = struct.unpack_from("<HH", blob, 4)
        if version != VEPK_VERSION:
            raise VeFormatError(f"包版本 {version}，期望 {VEPK_VERSION}")
        flags, original_size, stored_size, checksum = struct.unpack_from("<IIII", blob, 8)
        (key_hint,) = struct.unpack_from("<Q", blob, 24)
        if VEPK_HEADER_SIZE + stored_size > len(blob):
            raise VeFormatError(
                f"{stored_size} 字节载荷放不进 {len(blob)} 字节 (Pack.cpp:117)"
            )
        return cls(
            version=version,
            flavour=flavour,
            flags=flags,
            original_size=original_size,
            stored_size=stored_size,
            checksum=checksum,
            key_hint=key_hint,
        )


def bulk_offset(stored_size: int) -> int:
    """bulk 区在文件中的起始偏移: (stored_size + 47) & ~15。"""
    return (stored_size + 47) & ~15


@dataclass
class Package:
    """解开后的 VePK 包。"""

    header: PackHeader
    payload: bytes  # 解密解压后的索引数据 (通常是 CBOR)
    bulk: bytes  # 紧随其后的原始大块数据区
    raw_stored: bytes  # 原始载荷字节（未解密解压），用于无损回写


def read_package(blob: bytes, key: int = 0) -> Package:
    """Package::parse (0x348D0C)。key 对应 open() 的第二参数。"""
    hdr = PackHeader.unpack(blob)
    stored = blob[VEPK_HEADER_SIZE : VEPK_HEADER_SIZE + hdr.stored_size]
    data = stored

    if hdr.encrypted:
        if not key:
            raise VeFormatError("包已加密但未提供密钥 (Pack.cpp:127)")
        seed = pack_stream_seed(hdr.key_hint, key)
        data = cipher_xor(data, seed, 0)

    if hdr.compressed:
        out = ve_lz4.decompress(data, hdr.original_size)
        if len(out) != hdr.original_size:
            raise VeFormatError(
                f"解压得到 {len(out)} 字节，头声明 {hdr.original_size} (Pack.cpp:141)"
            )
        data = out
    elif hdr.stored_size != hdr.original_size:
        raise VeFormatError(
            f"未压缩包大小不符: {hdr.stored_size} != {hdr.original_size} (Pack.cpp:148)"
        )

    actual = fnv1a32(data)
    if actual != hdr.checksum:
        raise VeFormatError(
            f"校验和不符，包已损坏: {actual:#010x} != {hdr.checksum:#010x} (Pack.cpp:161)"
        )

    off = bulk_offset(hdr.stored_size)
    bulk = blob[off:] if len(blob) > off else b""
    return Package(header=hdr, payload=data, bulk=bulk, raw_stored=stored)


def write_package(
    payload: bytes,
    bulk: bytes = b"",
    flavour: int = 0,
    key: int = 0,
    key_hint: int = 0,
    compress: bool = False,
    encrypt: bool = False,
    raw_stored: bytes | None = None,
    pad_byte: int = 0,
) -> bytes:
    """构造 VePK 包。

    raw_stored 非空时直接写入该载荷字节，跳过压缩/加密 —— 用于
    保证未改动的包按原字节回写（LZ4 编码器实现差异不会破坏往返）。
    """
    if raw_stored is not None:
        stored = raw_stored
        flags = (VEPK_FLAG_COMPRESSED if compress else 0) | (
            VEPK_FLAG_ENCRYPTED if encrypt else 0
        )
    else:
        stored = payload
        flags = 0
        if compress:
            stored = ve_lz4.compress(stored)
            flags |= VEPK_FLAG_COMPRESSED
        if encrypt:
            if not key:
                raise VeFormatError("要求加密但未提供密钥")
            stored = cipher_xor(stored, pack_stream_seed(key_hint, key), 0)
            flags |= VEPK_FLAG_ENCRYPTED

    hdr = PackHeader(
        flavour=flavour,
        flags=flags,
        original_size=len(payload),
        stored_size=len(stored),
        checksum=fnv1a32(payload),
        key_hint=key_hint,
    )

    out = bytearray(hdr.pack())
    out += stored
    if bulk:
        # bulk 区起点须为 (stored_size+47)&~15，中间以 pad_byte 填充
        target = bulk_offset(len(stored))
        if len(out) > target:
            raise VeFormatError(f"载荷已越过 bulk 起点 {target}")
        out += bytes([pad_byte]) * (target - len(out))
        out += bulk
    return bytes(out)


# ---------------------------------------------------------------------------
# *.pak 分块包 (VESB)
# ---------------------------------------------------------------------------

MAGIC_VESB = b"VESB"  # CMP 0x42534556 小端
VESB_HEADER_SIZE = 96


@dataclass
class BundleHeader:
    """VESB 96 字节头。字段读取序列取自 0x1FBC80-0x1FBD58::

        0x00 magic "VESB"
        0x04 u16 format_version=2   (CMP #2)
        0x06 u16 header_size=96     (CMP #0x60)
        0x08 u32 directory_size     → 与清单 size 比对 (CMP X8,X25)
        0x0C u32 flags              (CMP #0x10<<12 = 0x10000)
        0x10 u64 total_size         → 须在 [dir+96, 映射长度] 内
        0x18 u32 chunk_count        资产条目数（非 AEAD 块数）
        0x20 nonce(24)              → 与清单槽位 5 比对
        0x38 tag(16)                → 与清单槽位 6 比对
        0x48 directory_digest(24)   → 目录区独立摘要前缀
        0x58 保留 8 字节
        0x60 加密目录起始

    与 vpk 不同: 清单里的 size 只覆盖目录区，其后的分块数据不参与
    根摘要 —— 故 images.pak 有 132 MB 而清单只记 285902。
    """

    format_version: int = VESF_VERSION
    header_size: int = VESB_HEADER_SIZE
    directory_size: int = 0
    flags: int = 0
    total_size: int = 0
    chunk_count: int = 0
    nonce: bytes = b"\x00" * NPUBBYTES
    tag: bytes = b"\x00" * ABYTES
    directory_digest: bytes = b"\x00" * 24
    reserved: bytes = b"\x00" * 8

    def pack(self) -> bytes:
        out = bytearray(VESB_HEADER_SIZE)
        out[0:4] = MAGIC_VESB
        struct.pack_into("<H", out, 4, self.format_version)
        struct.pack_into("<H", out, 6, self.header_size)
        struct.pack_into("<I", out, 8, self.directory_size)
        struct.pack_into("<I", out, 12, self.flags)
        struct.pack_into("<Q", out, 16, self.total_size)
        struct.pack_into("<I", out, 24, self.chunk_count)
        out[0x20:0x38] = self.nonce
        out[0x38:0x48] = self.tag
        out[0x48:0x58] = self.directory_digest[:16]
        out[0x58:0x60] = self.reserved
        return bytes(out)

    @classmethod
    def unpack(cls, blob: bytes) -> BundleHeader:
        if len(blob) < VESB_HEADER_SIZE:
            raise VeFormatError(f"bundle 头需 96 字节，收到 {len(blob)}")
        if blob[0:4] != MAGIC_VESB:
            raise VeFormatError(f"bundle 魔数错误: {blob[0:4]!r}")
        fmt, hsize = struct.unpack_from("<HH", blob, 4)
        if fmt != VESF_VERSION:
            raise VeFormatError(f"bundle 格式版本 {fmt}，期望 2")
        if hsize != VESB_HEADER_SIZE:
            raise VeFormatError(f"bundle 头长 {hsize}，期望 96")
        dir_size, flags = struct.unpack_from("<II", blob, 8)
        (total_size,) = struct.unpack_from("<Q", blob, 16)
        (chunk_count,) = struct.unpack_from("<I", blob, 24)
        return cls(
            format_version=fmt,
            header_size=hsize,
            directory_size=dir_size,
            flags=flags,
            total_size=total_size,
            chunk_count=chunk_count,
            nonce=blob[0x20:0x38],
            tag=blob[0x38:0x48],
            directory_digest=blob[0x48:0x58],
            reserved=blob[0x58:0x60],
        )


def read_bundle_directory(
    blob: bytes,
    entry: ManifestFile,
    content_key: bytes = CONTENT_KEY,
    build_id: bytes = BUILD_ID,
    check_digest: bool = True,
) -> tuple[BundleHeader, bytes]:
    """解密 .pak 的目录区，返回 (头, 目录明文)。

    分块数据区不在此解密 —— 每块用 "release bundle chunk key" 单独派生。
    """
    hdr = BundleHeader.unpack(blob)
    if hdr.directory_size != entry.size:
        raise VeAuthError(
            f"'{entry.logical_name}' 目录大小不符: 头 {hdr.directory_size} "
            f"vs 清单 {entry.size}"
        )
    if hdr.nonce != entry.nonce:
        raise VeAuthError(f"'{entry.logical_name}' nonce 与清单不符")
    if hdr.tag != entry.tag:
        raise VeAuthError(f"'{entry.logical_name}' tag 与清单不符")
    if hdr.total_size < hdr.directory_size + VESB_HEADER_SIZE:
        raise VeFormatError(
            f"'{entry.logical_name}' total_size {hdr.total_size} 小于目录末端"
        )
    if hdr.total_size > len(blob):
        raise VeFormatError(
            f"'{entry.logical_name}' total_size {hdr.total_size} 超过文件长度 {len(blob)}"
        )

    # 与 vpk 不同：清单摘要只覆盖前 total_size 字节（头 + 目录 + 对齐填充），
    # 其后的分块数据区由每块自己的 digests 数组保护。
    if check_digest:
        actual = crypto_digest(blob[: hdr.total_size])
        if actual != entry.digest:
            raise VeAuthError(
                f"'{entry.logical_name}' 目录区摘要无效: "
                f"{actual.hex()} != {entry.digest.hex()}"
            )

    key = release_store_file_key(
        entry.logical_name, CTX_BUNDLE_DIR_KEY, build_id, content_key
    )
    aad = file_aad(
        entry.logical_name, entry.file_type, entry.version, entry.size, build_id
    )
    cipher = blob[VESB_HEADER_SIZE : VESB_HEADER_SIZE + hdr.directory_size]
    plain = crypto_decrypt(cipher, entry.tag, aad, entry.nonce, key)
    return hdr, plain


BUNDLE_BLOCK_SIZE = 0x10000  # 64 KiB，read_sealed 中的 MOV W8,#0x10000


def bundle_total_size(directory_size: int) -> int:
    """分块区起点：头 + 目录后按 16 字节对齐。"""
    return (VESB_HEADER_SIZE + directory_size + 15) & ~15


@dataclass
class BundleEntry:
    """.pak 目录中的一个资产。"""

    key: int
    offset: int
    size: int
    tag_start: int

    @property
    def block_count(self) -> int:
        if self.size == 0:
            return 1
        return (self.size + BUNDLE_BLOCK_SIZE - 1) // BUNDLE_BLOCK_SIZE


@dataclass
class BundleDirectory:
    """.pak 目录，CBOR 中为并行数组。"""

    version: int
    entries: list[BundleEntry]
    tags: bytes
    digests: bytes
    extra: list[tuple] = field(default_factory=list)

    def find(self, key: int) -> BundleEntry | None:
        for e in self.entries:
            if e.key == key:
                return e
        return None

    @classmethod
    def from_cbor(cls, value: Any) -> BundleDirectory:
        if not isinstance(value, CborMap):
            raise VeFormatError("目录根必须是 map")
        d = {k: v for k, v in value.pairs}
        version = d.get("version")
        keys = d.get("keys")
        offsets = d.get("offsets")
        sizes = d.get("sizes")
        tag_starts = d.get("tag_starts")
        tags = d.get("tags")
        digests = d.get("digests")
        for label, v in (
            ("keys", keys),
            ("offsets", offsets),
            ("sizes", sizes),
            ("tag_starts", tag_starts),
        ):
            if not isinstance(v, list):
                raise VeFormatError(f"目录缺少 {label} 数组")
        if not isinstance(tags, bytes) or not isinstance(digests, bytes):
            raise VeFormatError("目录 tags/digests 必须是字节串")
        n = len(keys)
        if not (len(offsets) == len(sizes) == len(tag_starts) == n):
            raise VeFormatError("目录并行数组长度不一致")
        entries = [
            BundleEntry(keys[i], offsets[i], sizes[i], tag_starts[i]) for i in range(n)
        ]
        extra = [
            (k, v)
            for k, v in value.pairs
            if k
            not in ("version", "keys", "offsets", "sizes", "tag_starts", "tags", "digests")
        ]
        return cls(version, entries, tags, digests, extra)

    def to_cbor(self) -> CborMap:
        pairs: list[tuple] = [
            ("version", self.version),
            ("keys", [e.key for e in self.entries]),
            ("offsets", [e.offset for e in self.entries]),
            ("sizes", [e.size for e in self.entries]),
            ("tag_starts", [e.tag_start for e in self.entries]),
            ("tags", self.tags),
            ("digests", self.digests),
        ]
        pairs.extend(self.extra)
        # 还原原始键序（CBOR 中按字母序）
        order = {
            "version": 0,
            "keys": 1,
            "offsets": 2,
            "sizes": 3,
            "tag_starts": 4,
            "tags": 5,
            "digests": 6,
        }
        pairs.sort(key=lambda kv: order.get(kv[0], 99))
        return CborMap(pairs)


def chunk_nonce(salt: bytes, block_index: int) -> bytes:
    """ve::release_chunk_nonce (0x356958) —— salt(16) ‖ u64le(block_index)。"""
    return salt + struct.pack("<Q", block_index)


def chunk_aad(
    build_id: bytes, asset_key: int, decoded_size: int, block_index: int, chunk_size: int
) -> bytes:
    """ve::release_chunk_aad (0x35689C) —— 40 字节定长结构::

        +0x00 build_id(16)
        +0x10 u32 asset_key
        +0x14 u64 decoded_size
        +0x1C u64 block_index
        +0x24 u32 chunk_size
    """
    aad = bytearray(40)
    aad[0:16] = build_id
    struct.pack_into("<I", aad, 16, asset_key & 0xFFFFFFFF)
    struct.pack_into("<Q", aad, 20, decoded_size)
    struct.pack_into("<Q", aad, 28, block_index)
    struct.pack_into("<I", aad, 36, chunk_size & 0xFFFFFFFF)
    return bytes(aad)


def read_bundle_chunk(
    blob: bytes,
    hdr: BundleHeader,
    directory: BundleDirectory,
    entry: BundleEntry,
    logical_name: str,
    content_key: bytes = CONTENT_KEY,
    build_id: bytes = BUILD_ID,
    check_digest: bool = True,
) -> bytes:
    """解密单个资产（可能跨多个 64 KiB AEAD 块）。

    分块数据区起点为 hdr.total_size —— 目录区之后按 16 字节对齐。
    """
    key = release_store_file_key(
        logical_name, CTX_BUNDLE_CHUNK_KEY, build_id, content_key
    )
    salt = hdr.directory_digest[:16]
    base = hdr.total_size

    out = bytearray()
    remaining = entry.size
    pos = entry.offset
    for blk in range(entry.block_count):
        n = min(BUNDLE_BLOCK_SIZE, remaining)
        data = blob[base + pos : base + pos + n]
        if len(data) != n:
            raise VeFormatError(
                f"资产 {entry.key} 块 {blk} 数据截断: 需 {n}，得 {len(data)}"
            )
        ti = entry.tag_start + blk
        tag = directory.tags[ti * 16 : ti * 16 + 16]
        if check_digest:
            expect = directory.digests[ti * 32 : ti * 32 + 32]
            actual = crypto_digest(data)
            if actual != expect:
                raise VeAuthError(f"资产 {entry.key} 块 {blk} 摘要无效")
        plain = crypto_decrypt(
            data,
            tag,
            # decoded_size 是资产总长度，chunk_size 是本块长度
            chunk_aad(build_id, entry.key, entry.size, blk, n),
            chunk_nonce(salt, ti),
            key,
        )
        out += plain
        pos += n
        remaining -= n
    return bytes(out)
