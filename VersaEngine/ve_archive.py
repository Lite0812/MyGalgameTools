"""解包/封包的高层编排。

解包直接还原原始目录结构：

    unpacked/
      ve_repack.json          最小元信息
      images/bg/bg_black.webp     ← .pak 里的资产，按 assets.vpk 的路径表还原
      audio/bgm/BGM02.ogg
      _packages/boot.script.cbor  ← .vpk 的 CBOR 载荷
      _packages/main.font.bin

不保留任何原文件副本。往返一致靠三点：

1. 确定性重建。XChaCha20-Poly1305 在相同 (key, nonce, 明文, AAD) 下输出
   确定，nonce 与派生参数都记在元信息里，故密文逐字节可复现。
   .pak 的分块区（每资产按 16 字节对齐、每块 64 KiB）已验证可完整重算。
2. 可推导的不存。目录的 keys/offsets/sizes/tag_starts/tags/digests 全部
   由资产内容与路径哈希决定，重算即可；填充字节恒为零。
3. 唯一的例外是 LZ4。引擎压缩器与 python-lz4 任何档位都不一致，所以
   未改动的 .vpk 载荷需要原始 build 目录（--src）取回压缩字节；改动过的
   重新压缩，此时字节本就会变。
"""

from __future__ import annotations

import base64
import json
import os
import posixpath
from typing import Any

import ve_cbor
import ve_format as F
from ve_crypto import (
    BUILD_ID,
    CONTENT_KEY,
    SIGNING_PUBLIC_KEY,
    crypto_digest,
    crypto_encrypt,
    fnv1a32,
)

META_NAME = "ve_repack.json"
PKG_DIR = "_packages"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


class ArchiveError(Exception):
    pass


# ---------------------------------------------------------------------------
# 资产路径表
# ---------------------------------------------------------------------------


def _load_path_map(payload: bytes) -> dict[int, str]:
    """从 assets.vpk 载荷取 {FNV-1a32(path): path}。"""
    value = ve_cbor.loads(payload)
    if not isinstance(value, ve_cbor.CborMap):
        raise ArchiveError("assets.vpk 载荷不是 map")
    d = {k: v for k, v in value.pairs}
    paths, ids = d.get("paths"), d.get("path_ids")
    if not isinstance(paths, list) or not isinstance(ids, list):
        raise ArchiveError("assets.vpk 缺少 paths/path_ids")
    if len(paths) != len(ids):
        raise ArchiveError("paths 与 path_ids 长度不一致")
    out: dict[int, str] = {}
    for path, key in zip(paths, ids):
        if fnv1a32(path.encode("utf-8")) != key:
            raise ArchiveError(f"路径哈希不符: {path}")
        out[key] = path
    return out


def _safe_rel(path: str) -> str:
    """把资产路径规范成安全的相对路径，杜绝 .. 逃逸。"""
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if not parts:
        raise ArchiveError(f"非法资产路径: {path!r}")
    return posixpath.join(*parts)


_MAGIC_EXT = [
    (b"RIFF", ".webp"),
    (b"\x89PNG", ".png"),
    (b"OggS", ".ogg"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"VEPK", ".vepk"),
    (b"ID3", ".mp3"),
]


def _guess_ext(data: bytes) -> str:
    for magic, ext in _MAGIC_EXT:
        if data.startswith(magic):
            return ext
    return ".bin"


# ---------------------------------------------------------------------------
# 解包
# ---------------------------------------------------------------------------


def unpack_all(
    build_dir: str,
    out_dir: str,
    content_key: bytes = CONTENT_KEY,
    public_key: bytes = SIGNING_PUBLIC_KEY,
    verify_signature: bool = True,
    extract_bundles: bool = True,
) -> dict:
    """解开 build 目录下的 game.vseal 及其引用的全部文件。"""
    seal_path = os.path.join(build_dir, "game.vseal")
    if not os.path.isfile(seal_path):
        raise ArchiveError(f"找不到 {seal_path}")

    with open(seal_path, "rb") as fh:
        seal_blob = fh.read()

    hdr, manifest, manifest_plain = F.read_seal(
        seal_blob,
        content_key=content_key,
        public_key=public_key,
        verify_signature=verify_signature,
    )

    os.makedirs(out_dir, exist_ok=True)
    pkg_root = os.path.join(out_dir, PKG_DIR)
    os.makedirs(pkg_root, exist_ok=True)

    meta: dict[str, Any] = {
        "tool": "vetool/2.0",
        "build_id": hdr.build_id.hex(),
        "seal": {
            "version": hdr.version,
            "nonce": _b64(hdr.nonce),
            "signature": _b64(hdr.signature),
            "manifest_sha256": crypto_digest(manifest_plain).hex(),
        },
        "files": [],
    }
    if hdr.reserved.strip(b"\x00"):
        meta["seal"]["reserved"] = _b64(hdr.reserved)

    # 先解 assets.vpk 拿到路径表，再解 .pak
    path_map: dict[int, str] = {}
    packages: list[tuple[F.ManifestFile, bytes]] = []
    bundles: list[F.ManifestFile] = []

    for entry in manifest.files:
        src = os.path.join(build_dir, entry.stored_name)
        if not os.path.isfile(src):
            raise ArchiveError(f"清单引用的文件缺失: {entry.stored_name}")
        if entry.file_type == F.FILE_TYPE_BUNDLE_DIR:
            bundles.append(entry)
            continue
        with open(src, "rb") as fh:
            blob = fh.read()
        rec, payload = _unpack_package(blob, entry, out_dir, content_key)
        meta["files"].append(rec)
        packages.append((entry, payload))
        if entry.logical_name == "assets.vpk":
            path_map = _load_path_map(payload)

    for entry in bundles:
        with open(os.path.join(build_dir, entry.stored_name), "rb") as fh:
            blob = fh.read()
        meta["files"].append(
            _unpack_bundle(
                blob, entry, out_dir, content_key, path_map, extract_bundles
            )
        )

    with open(os.path.join(out_dir, META_NAME), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1, ensure_ascii=False)

    return meta


def _payload_name(logical: str) -> str:
    """boot.script.vpk -> boot.script.cbor；非 CBOR 载荷用 .bin。"""
    base = logical[:-4] if logical.endswith(".vpk") else logical
    return base.replace("/", "__").replace("\\", "__")


def _unpack_package(
    blob: bytes,
    entry: F.ManifestFile,
    out_dir: str,
    content_key: bytes,
) -> tuple[dict, bytes]:
    """解开一个 vpk：VESF 外壳 → VEPK 内层 → CBOR 载荷。"""
    vesf_hdr, inner = F.read_sealed_file(blob, entry, content_key=content_key)
    pkg = F.read_package(inner)

    name = _payload_name(entry.logical_name)
    # loads() 要求恰好消耗全部输入，能过就是货真价实的 CBOR
    try:
        ve_cbor.loads(pkg.payload)
        ext = "cbor"
    except Exception:
        ext = "bin"
    rel = f"{PKG_DIR}/{name}.{ext}"

    with open(os.path.join(out_dir, rel), "wb") as fh:
        fh.write(pkg.payload)

    rec: dict[str, Any] = {
        "kind": "package",
        "logical_name": entry.logical_name,
        "stored_name": entry.stored_name,
        "file_type": entry.file_type,
        "version": entry.version,
        "nonce": _b64(entry.nonce),
        "digest": entry.digest.hex(),
        "payload": rel,
        "payload_sha256": crypto_digest(pkg.payload).hex(),
        "pack": {
            "version": pkg.header.version,
            "flavour": pkg.header.flavour,
            "flags": pkg.header.flags,
            "key_hint": pkg.header.key_hint,
        },
    }
    if vesf_hdr.reserved.strip(b"\x00"):
        rec["vesf_reserved"] = _b64(vesf_hdr.reserved)

    if pkg.bulk:
        bulk_rel = f"{PKG_DIR}/{name}.bulk"
        with open(os.path.join(out_dir, bulk_rel), "wb") as fh:
            fh.write(pkg.bulk)
        rec["bulk"] = bulk_rel
        pad_start = F.VEPK_HEADER_SIZE + pkg.header.stored_size
        pad = inner[pad_start : F.bulk_offset(pkg.header.stored_size)]
        if pad.strip(b"\x00"):
            rec["bulk_padding"] = _b64(pad)

    return rec, pkg.payload


def _unpack_bundle(
    blob: bytes,
    entry: F.ManifestFile,
    out_dir: str,
    content_key: bytes,
    path_map: dict[int, str],
    extract: bool,
) -> dict:
    """解开一个 .pak：VESB 目录 + 64 KiB 分块资产，按原路径落盘。"""
    hdr, dir_plain = F.read_bundle_directory(blob, entry, content_key=content_key)
    directory = F.BundleDirectory.from_cbor(ve_cbor.loads(dir_plain))

    rec: dict[str, Any] = {
        "kind": "bundle",
        "logical_name": entry.logical_name,
        "stored_name": entry.stored_name,
        "file_type": entry.file_type,
        "version": entry.version,
        "nonce": _b64(entry.nonce),
        "digest": entry.digest.hex(),
        "dir_version": directory.version,
        "flags": hdr.flags,
        "chunk_salt": _b64(hdr.directory_digest),
        "tail_pad": len(blob) - hdr.total_size - _region_size(directory),
        "assets": [],
    }
    if hdr.reserved.strip(b"\x00"):
        rec["reserved"] = _b64(hdr.reserved)

    if not extract:
        return rec

    unmapped = os.path.join(out_dir, "_unmapped", _payload_name(entry.logical_name))
    for e in directory.entries:
        data = F.read_bundle_chunk(
            blob, hdr, directory, e, entry.logical_name, content_key=content_key
        )
        path = path_map.get(e.key)
        if path is None:
            os.makedirs(unmapped, exist_ok=True)
            rel = f"_unmapped/{_payload_name(entry.logical_name)}/{e.key:08x}{_guess_ext(data)}"
        else:
            rel = _safe_rel(path)
        dst = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(data)
        # key 由路径哈希决定，故只记路径；已映射的连 key 都不必存
        rec["assets"].append(rel if path is not None else [rel, e.key])

    return rec


def _region_size(directory: F.BundleDirectory) -> int:
    """分块区实际占用长度（末尾资产结束处）。"""
    if not directory.entries:
        return 0
    last = directory.entries[-1]
    return last.offset + last.size


# ---------------------------------------------------------------------------
# 封包
# ---------------------------------------------------------------------------


def repack_all(
    work_dir: str,
    out_dir: str,
    src_dir: str | None = None,
    content_key: bytes = CONTENT_KEY,
    signing_key: bytes | None = None,
    strict: bool = True,
) -> dict:
    """由解包产物重建 build 目录。

    src_dir 指向原始 build 目录：未改动的 .vpk 从那里取回 LZ4 压缩字节
    （引擎压缩器无法复现，见模块文档）。.pak 完全从解包产物重建，不需要它。
    """
    meta_path = os.path.join(work_dir, META_NAME)
    if not os.path.isfile(meta_path):
        raise ArchiveError(f"找不到 {meta_path}")
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)

    os.makedirs(out_dir, exist_ok=True)
    build_id = bytes.fromhex(meta["build_id"])
    report: dict[str, Any] = {"files": [], "rebuilt": 0, "changed": 0}

    entries: list[F.ManifestFile] = []
    for rec in meta["files"]:
        if rec["kind"] == "package":
            blob, entry = _repack_package(
                rec, work_dir, src_dir, build_id, content_key, strict
            )
            digest_len = len(blob)
        else:
            blob, entry, digest_len = _repack_bundle(
                rec, work_dir, build_id, content_key
            )

        with open(os.path.join(out_dir, entry.stored_name), "wb") as fh:
            fh.write(blob)

        # 记录的 digest 是权威判据：重建结果与它一致即未改动
        entry.digest = crypto_digest(blob[:digest_len])
        changed = entry.digest.hex() != rec["digest"]
        entries.append(entry)
        report["files"].append(
            {"logical_name": entry.logical_name, "changed": changed, "size": len(blob)}
        )
        report["rebuilt"] += 1
        if changed:
            report["changed"] += 1

    # 清单条目按逻辑名排序（与引擎输出一致，故顺序无须记录）
    entries.sort(key=lambda e: e.logical_name)
    manifest = F.Manifest(version=F.VSEAL_VERSION, files=entries)
    manifest_plain = ve_cbor.dumps(manifest.to_cbor())

    seal = meta["seal"]
    identical = crypto_digest(manifest_plain).hex() == seal["manifest_sha256"]
    if strict and not identical and report["changed"] == 0:
        raise ArchiveError(
            "无改动但清单重编码结果不同 —— 往返未能保持一致\n"
            f"  期望 {seal['manifest_sha256']}\n"
            f"  实得 {crypto_digest(manifest_plain).hex()}"
        )

    seal_blob = F.write_seal(
        manifest,
        nonce=_unb64(seal["nonce"]),
        build_id=build_id,
        signing_key=signing_key if not identical else None,
        signature=_unb64(seal["signature"]) if identical else None,
        content_key=content_key,
        plaintext=manifest_plain,
        reserved=_unb64(seal["reserved"]) if "reserved" in seal else b"\x00" * 8,
    )
    with open(os.path.join(out_dir, "game.vseal"), "wb") as fh:
        fh.write(seal_blob)

    report["manifest_changed"] = not identical
    report["signature_regenerated"] = bool(not identical and signing_key)
    if not identical and not signing_key:
        report["warning"] = "清单已变但无签名私钥，game.vseal 签名将校验失败"
    return report


def _repack_package(
    rec: dict,
    work_dir: str,
    src_dir: str | None,
    build_id: bytes,
    content_key: bytes,
    strict: bool,
) -> tuple[bytes, F.ManifestFile]:
    """重建一个 vpk。返回 (文件字节, 清单条目)。"""
    with open(os.path.join(work_dir, rec["payload"]), "rb") as fh:
        payload = fh.read()
    changed = crypto_digest(payload).hex() != rec["payload_sha256"]

    bulk = b""
    if "bulk" in rec:
        with open(os.path.join(work_dir, rec["bulk"]), "rb") as fh:
            bulk = fh.read()

    pk = rec["pack"]
    if changed:
        inner = F.write_package(
            payload,
            bulk=bulk,
            flavour=pk["flavour"],
            key=0,
            key_hint=pk["key_hint"],
            compress=bool(pk["flags"] & F.VEPK_FLAG_COMPRESSED),
            encrypt=bool(pk["flags"] & F.VEPK_FLAG_ENCRYPTED),
        )
    else:
        inner = _original_inner(rec, src_dir, build_id, content_key, payload, bulk, strict)

    blob, entry = F.write_sealed_file(
        inner,
        rec["logical_name"],
        rec["file_type"],
        rec["version"],
        nonce=_unb64(rec["nonce"]),
        content_key=content_key,
        build_id=build_id,
        reserved=_unb64(rec["vesf_reserved"]) if "vesf_reserved" in rec else b"\x00" * 8,
    )
    entry.logical_name = rec["logical_name"]
    entry.stored_name = rec["stored_name"]
    return blob, entry


def _original_inner(
    rec: dict,
    src_dir: str | None,
    build_id: bytes,
    content_key: bytes,
    payload: bytes,
    bulk: bytes,
    strict: bool,
) -> bytes:
    """未改动的包：从原 build 目录取回 VEPK 内层，保住 LZ4 字节。"""
    pk = rec["pack"]
    if not (pk["flags"] & F.VEPK_FLAG_COMPRESSED):
        # 未压缩，可精确重建
        return F.write_package(
            payload,
            bulk=bulk,
            flavour=pk["flavour"],
            key=0,
            key_hint=pk["key_hint"],
            compress=False,
            encrypt=bool(pk["flags"] & F.VEPK_FLAG_ENCRYPTED),
        )

    if src_dir is None:
        if strict:
            raise ArchiveError(
                f"'{rec['logical_name']}' 是 LZ4 压缩包且内容未改动，"
                "需要 --src 指向原 build 目录才能保证字节一致"
            )
        return F.write_package(
            payload,
            bulk=bulk,
            flavour=pk["flavour"],
            key=0,
            key_hint=pk["key_hint"],
            compress=True,
            encrypt=bool(pk["flags"] & F.VEPK_FLAG_ENCRYPTED),
        )

    src = os.path.join(src_dir, rec["stored_name"])
    if not os.path.isfile(src):
        raise ArchiveError(f"找不到源文件: {src}")
    with open(src, "rb") as fh:
        blob = fh.read()
    probe = F.ManifestFile(
        logical_name=rec["logical_name"],
        stored_name=rec["stored_name"],
        file_type=rec["file_type"],
        version=rec["version"],
        size=len(blob) - F.VESF_HEADER_SIZE,
        nonce=_unb64(rec["nonce"]),
        tag=F.SealedFileHeader.unpack(blob[: F.VESF_HEADER_SIZE]).tag,
        digest=crypto_digest(blob),
    )
    _, inner = F.read_sealed_file(blob, probe, content_key=content_key)
    # 源文件须与解包时同一份，否则取回的压缩字节对不上现在的载荷
    src_pkg = F.read_package(inner)
    if crypto_digest(src_pkg.payload).hex() != rec["payload_sha256"]:
        raise ArchiveError(
            f"源文件 '{rec['stored_name']}' 与解包时不一致，无法取回压缩字节"
        )
    if src_pkg.bulk != bulk:
        raise ArchiveError(f"'{rec['logical_name']}' bulk 区与源文件不一致")
    return inner


def _repack_bundle(
    rec: dict,
    work_dir: str,
    build_id: bytes,
    content_key: bytes,
) -> tuple[bytes, F.ManifestFile, int]:
    """从解包产物完整重建一个 .pak，不依赖原文件。

    目录的 keys/offsets/sizes/tag_starts/tags/digests 全部重算：key 是路径
    的 FNV-1a32，条目按 key 升序，每个资产在分块区里按 16 字节对齐。
    """
    logical = rec["logical_name"]
    salt = _unb64(rec["chunk_salt"])
    key = F.release_store_file_key(
        logical, F.CTX_BUNDLE_CHUNK_KEY, build_id, content_key
    )

    # 收集资产：(key, 相对路径)
    items: list[tuple[int, str]] = []
    for item in rec["assets"]:
        if isinstance(item, list):
            rel, akey = item[0], item[1]
        else:
            rel = item
            akey = fnv1a32(rel.encode("utf-8"))
        items.append((akey, rel))
    items.sort(key=lambda kv: kv[0])

    region = bytearray()
    dir_entries: list[F.BundleEntry] = []
    tags = bytearray()
    digests = bytearray()
    tag_index = 0

    for akey, rel in items:
        path = os.path.join(work_dir, rel)
        if not os.path.isfile(path):
            raise ArchiveError(f"资产缺失: {rel}")
        with open(path, "rb") as fh:
            data = fh.read()

        # 每个资产在分块区里按 16 字节对齐
        pad = (-len(region)) % 16
        region += b"\x00" * pad
        offset = len(region)
        dir_entries.append(F.BundleEntry(akey, offset, len(data), tag_index))

        blocks = max(1, (len(data) + F.BUNDLE_BLOCK_SIZE - 1) // F.BUNDLE_BLOCK_SIZE)
        for blk in range(blocks):
            chunk = data[blk * F.BUNDLE_BLOCK_SIZE : (blk + 1) * F.BUNDLE_BLOCK_SIZE]
            cipher, tag = crypto_encrypt(
                chunk,
                F.chunk_aad(build_id, akey, len(data), blk, len(chunk)),
                F.chunk_nonce(salt, tag_index),
                key,
            )
            region += cipher
            tags += tag
            digests += crypto_digest(cipher)
            tag_index += 1

    directory = F.BundleDirectory(
        version=rec["dir_version"],
        entries=dir_entries,
        tags=bytes(tags),
        digests=bytes(digests),
    )
    dir_plain = ve_cbor.dumps(directory.to_cbor())

    aad = F.file_aad(logical, rec["file_type"], rec["version"], len(dir_plain), build_id)
    dkey = F.release_store_file_key(
        logical, F.CTX_BUNDLE_DIR_KEY, build_id, content_key
    )
    nonce = _unb64(rec["nonce"])
    cipher, dtag = crypto_encrypt(dir_plain, aad, nonce, dkey)

    # 目录区之后按 16 字节对齐，得到分块区起点
    total_size = F.bundle_total_size(len(cipher))
    hdr = F.BundleHeader(
        directory_size=len(cipher),
        flags=rec["flags"],
        total_size=total_size,
        chunk_count=len(dir_entries),  # 资产数，非 AEAD 块数
        nonce=nonce,
        tag=dtag,
        directory_digest=salt,
        reserved=_unb64(rec["reserved"]) if "reserved" in rec else b"\x00" * 8,
    )

    out = bytearray(hdr.pack())
    out += cipher
    out += b"\x00" * (total_size - len(out))
    out += region
    out += b"\x00" * rec.get("tail_pad", 0)

    entry = F.ManifestFile(
        logical_name=logical,
        stored_name=rec["stored_name"],
        file_type=rec["file_type"],
        version=rec["version"],
        size=len(cipher),
        nonce=nonce,
        tag=dtag,
        digest=b"",
    )
    return bytes(out), entry, total_size
