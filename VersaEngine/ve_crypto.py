"""VersaEngine 密码学原语。

全部依据 libmain.so (arm64-v8a) 反编译结果实现：
  ve::crypto_digest   0x345444  blake2b-256, 无 key
  ve::crypto_derive   0x345274  blake2b-256, key=32B, 域分隔 0x00
  ve::crypto_decrypt  0x345668  XChaCha20-Poly1305-IETF, detached tag
  ve::cipher_xor      0x344FAC  splitmix64 流 XOR
"""

from __future__ import annotations

import hashlib
import struct

from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
)
from nacl.exceptions import CryptoError
from nacl.signing import SigningKey, VerifyKey

# ---------------------------------------------------------------------------
# 内置常量 (.rodata)
# ---------------------------------------------------------------------------

# ve::release_content_key @ 0x356970 —— 由两个 16 字节常量池条目拼接。
# 注意二者在文件中不相邻: xmmword_BFFC0 与 xmmword_BE790。
CONTENT_KEY = bytes.fromhex(
    "facdbb6400279582ce690604e53e5512"  # off 0x0BFFC0
    "704bd425a383953bb2dab5bc60e4f669"  # off 0x0BE790
)

# ve::generated_release_identity::build_id @ 0x14074F
BUILD_ID = bytes.fromhex("ac73847d53c986e74f7692dbf3faf34d")

# ve::generated_release_identity::signing_public_key @ 0x14070F
SIGNING_PUBLIC_KEY = bytes.fromhex(
    "ee12ce97a675486e45f9b0f34828bbe3c8a339bd5d4ce779b64d1186d86e2184"
)

# ve::generated_release_identity::certificate_sha256 @ 0x14072F
CERTIFICATE_SHA256 = bytes.fromhex(
    "9eba83f6728de41eb72d315ebcf818341848c855794663e78ab2c8e0b3088a83"
)

# crypto_derive 的上下文串 (strings.txt 明文)
CTX_ROOT_SIGNATURE = b"VersaEngine root signature v2"
CTX_RELEASE_FILE = b"VersaEngine release file v2"
CTX_MANIFEST_KEY = b"release manifest key"
CTX_PACKAGE_KEY = b"release package key"
CTX_BUNDLE_DIR_KEY = b"release bundle directory key"
CTX_BUNDLE_CHUNK_KEY = b"release bundle chunk key"

KEYBYTES = 32
NPUBBYTES = 24
ABYTES = 16
SALTBYTES = 16


# ---------------------------------------------------------------------------
# 摘要与派生
# ---------------------------------------------------------------------------


def crypto_digest(data: bytes) -> bytes:
    """ve::crypto_digest — crypto_generichash(out=32, in, key=NULL)."""
    return hashlib.blake2b(data, digest_size=32).digest()


def crypto_derive(key: bytes, context: bytes, info: bytes = b"") -> bytes:
    """ve::crypto_derive — keyed blake2b-256。

    反编译对应 (0x345274)::

        crypto_generichash_init(s, key, 32, 32)
        crypto_generichash_update(s, context, context_len)
        crypto_generichash_update(s, "\\0", 1)      # 域分隔符
        if info_len: crypto_generichash_update(s, info, info_len)
        crypto_generichash_final(s, out, 32)
    """
    if len(key) != KEYBYTES:
        raise ValueError(f"派生密钥必须 32 字节，收到 {len(key)}")
    h = hashlib.blake2b(key=key, digest_size=32)
    h.update(context)
    h.update(b"\x00")
    if info:
        h.update(info)
    return h.digest()


# ---------------------------------------------------------------------------
# AEAD
# ---------------------------------------------------------------------------


def crypto_decrypt(
    ciphertext: bytes, tag: bytes, aad: bytes, nonce: bytes, key: bytes
) -> bytes:
    """ve::crypto_decrypt — XChaCha20-Poly1305-IETF, tag 分离存放。

    引擎调用 ..._decrypt_detached(m, NULL, c, clen, mac, ad, adlen, npub, k)，
    PyNaCl 只暴露组合式接口，故此处把 tag 追加到密文尾部。
    """
    if len(nonce) != NPUBBYTES:
        raise ValueError(f"nonce 必须 24 字节，收到 {len(nonce)}")
    if len(tag) != ABYTES:
        raise ValueError(f"tag 必须 16 字节，收到 {len(tag)}")
    if len(key) != KEYBYTES:
        raise ValueError(f"密钥必须 32 字节，收到 {len(key)}")
    try:
        return crypto_aead_xchacha20poly1305_ietf_decrypt(
            ciphertext + tag, aad, nonce, key
        )
    except CryptoError as exc:
        raise VeAuthError("AEAD 认证失败") from exc


def crypto_encrypt(
    plaintext: bytes, aad: bytes, nonce: bytes, key: bytes
) -> tuple[bytes, bytes]:
    """ve::crypto_encrypt 的逆向对应物，返回 (密文, tag)。"""
    if len(nonce) != NPUBBYTES:
        raise ValueError(f"nonce 必须 24 字节，收到 {len(nonce)}")
    if len(key) != KEYBYTES:
        raise ValueError(f"密钥必须 32 字节，收到 {len(key)}")
    blob = crypto_aead_xchacha20poly1305_ietf_encrypt(plaintext, aad, nonce, key)
    return blob[:-ABYTES], blob[-ABYTES:]


class VeAuthError(Exception):
    """认证/校验失败。"""


# ---------------------------------------------------------------------------
# Ed25519
# ---------------------------------------------------------------------------


def crypto_verify_message(signature: bytes, message: bytes, public_key: bytes) -> bool:
    """ve::crypto_verify_message — Ed25519 detached 验签。"""
    try:
        VerifyKey(public_key).verify(message, signature)
        return True
    except Exception:
        return False


def crypto_sign_message(message: bytes, secret_key: bytes) -> bytes:
    """ve::crypto_sign_message — 返回 64 字节 detached 签名。

    secret_key 为 libsodium 风格 64 字节 (seed‖pk) 或 32 字节 seed。
    """
    if len(secret_key) == 64:
        seed = secret_key[:32]
    elif len(secret_key) == 32:
        seed = secret_key
    else:
        raise ValueError(f"私钥必须 32 或 64 字节，收到 {len(secret_key)}")
    return SigningKey(seed).sign(message).signature


def sign_keypair(seed: bytes) -> tuple[bytes, bytes]:
    """由 32 字节种子生成 (公钥32, 私钥64)。"""
    sk = SigningKey(seed)
    return bytes(sk.verify_key), seed + bytes(sk.verify_key)


# ---------------------------------------------------------------------------
# splitmix64 流密码 (ve::cipher_xor)
# ---------------------------------------------------------------------------

_M64 = 0xFFFFFFFFFFFFFFFF
_GAMMA = 0x9E3779B97F4A7C15
_MIX1 = 0xBF58476D1CE4E5B9
_MIX2 = 0x94D049BB133111EB


def splitmix64(state: int) -> int:
    """标准 splitmix64 终混。

    反编译中 seed 递减 0x61C8864680B583EB，即 -0x9E3779B97F4A7C15 的
    二补数，因此第 i 个 64 位字使用 seed + i*GAMMA。
    """
    z = state & _M64
    z = ((z ^ (z >> 30)) * _MIX1) & _M64
    z = ((z ^ (z >> 27)) * _MIX2) & _M64
    return z ^ (z >> 31)


def cipher_xor(data: bytes, seed: int, offset: int = 0) -> bytes:
    """ve::cipher_xor(dst, src, len, seed, offset)。

    按 8 字节小端字生成流，字索引为 (offset+i)//8，字内起始字节
    (offset+i)%8。offset 非 8 对齐时首个部分字被正确跳过 —— 这使得
    随机访问与顺序解密结果一致。对合运算，加解密同一函数。
    """
    out = bytearray(len(data))
    pos = 0
    n = len(data)
    while pos < n:
        abs_off = offset + pos
        word_index = abs_off >> 3
        byte_in_word = abs_off & 7
        stream = splitmix64((seed + word_index * _GAMMA) & _M64)
        chunk = struct.pack("<Q", stream)
        take = min(8 - byte_in_word, n - pos)
        for i in range(take):
            out[pos + i] = data[pos + i] ^ chunk[byte_in_word + i]
        pos += take
    return bytes(out)


def pack_stream_seed(salt: int, key_hint: int) -> int:
    """ve::Package::parse 中的 seed 混合 (0x348D0C)。

    v24 = splitmix64_partial(GAMMA*salt ^ key_hint) 后取 |1 ——
    反编译里两级 mix 完整展开，等价于 splitmix64(GAMMA*salt ^ key) | 1。
    """
    mixed = ((_GAMMA * salt) & _M64) ^ (key_hint & _M64)
    return splitmix64(mixed) | 1


# ---------------------------------------------------------------------------
# FNV-1a 32 (Package 校验和)
# ---------------------------------------------------------------------------


def fnv1a32(data: bytes) -> int:
    """Package::parse 使用的 FNV-1a: h=2166136261, h=(h^b)*16777619。"""
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h
