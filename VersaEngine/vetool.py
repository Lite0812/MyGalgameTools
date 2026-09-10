#!/usr/bin/env python3
"""VersaEngine 资源解包/封包工具。

    python vetool.py info    <build_dir>
    python vetool.py list    <build_dir>
    python vetool.py unpack  <build_dir> <out_dir> [--no-bundles]
    python vetool.py repack  <work_dir> <out_dir> --src <build_dir>
    python vetool.py verify  <build_dir>
    python vetool.py text    <pkg_dir> [<out_dir>]
    python vetool.py roundtrip <build_dir> [--work <dir>]

密钥默认取自 libmain.so 内置常量，可用 --content-key / --build-id 覆盖。
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import sys
import tempfile

import ve_archive
import ve_cbor
import ve_crypto as C
import ve_format as F


def _load_seal(build_dir: str, args) -> tuple:
    path = os.path.join(build_dir, "game.vseal")
    with open(path, "rb") as fh:
        blob = fh.read()
    return blob, F.read_seal(
        blob,
        content_key=args.content_key,
        public_key=args.public_key,
        expect_build_id=None if args.no_build_check else args.build_id,
        verify_signature=not args.no_verify,
    )


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def cmd_info(args) -> int:
    blob, (hdr, manifest, plain) = _load_seal(args.build_dir, args)
    print(f"game.vseal            {len(blob):,} 字节")
    print(f"  格式版本            {hdr.version}")
    print(f"  build_id            {hdr.build_id.hex()}")
    print(f"  内置 build_id       {C.BUILD_ID.hex()}"
          f"{'  (一致)' if hdr.build_id == C.BUILD_ID else '  (不一致)'}")
    print(f"  清单密文            {hdr.manifest_len:,} 字节")
    print(f"  nonce               {hdr.nonce.hex()}")
    print(f"  tag                 {hdr.tag.hex()}")

    digest = C.crypto_digest(blob[F.VSEAL_HEADER_SIZE :])
    msg = F.root_signature_message(
        hdr.build_id, hdr.nonce, hdr.tag, hdr.manifest_len, digest
    )
    valid = C.crypto_verify_message(hdr.signature, msg, args.public_key)
    print(f"  Ed25519 签名        {'有效' if valid else '无效'}")

    from collections import Counter

    types = Counter(f.file_type for f in manifest.files)
    names = {
        F.FILE_TYPE_PACKAGE: "包 (vpk)",
        F.FILE_TYPE_BUNDLE_DIR: "分块包 (pak)",
        F.FILE_TYPE_BUNDLE_CHUNK: "分块数据",
        F.FILE_TYPE_AUX: "附属",
    }
    print(f"\n清单条目              {len(manifest.files)}")
    for t, n in sorted(types.items()):
        print(f"  类型 {t} {names.get(t, '?'):16} {n}")
    total = sum(f.size for f in manifest.files)
    print(f"  密文合计            {total:,} 字节")
    if manifest.extra:
        print(f"  额外键              {[k for k, _ in manifest.extra]}")
    return 0


def cmd_list(args) -> int:
    _, (hdr, manifest, _) = _load_seal(args.build_dir, args)
    print(f"{'类型':<5}{'版本':<5}{'大小':>12}  {'逻辑名':<38} 存储名")
    print("-" * 100)
    for f in sorted(manifest.files, key=lambda x: x.logical_name):
        print(
            f"{f.file_type:<5}{f.version:<5}{f.size:>12,}  "
            f"{f.logical_name:<38} {f.stored_name}"
        )
    return 0


def cmd_verify(args) -> int:
    build_dir = args.build_dir
    _, (hdr, manifest, _) = _load_seal(build_dir, args)
    print(f"已验证 vseal 签名与清单 ({len(manifest.files)} 条)")

    ok = bad = 0
    for entry in manifest.files:
        path = os.path.join(build_dir, entry.stored_name)
        try:
            with open(path, "rb") as fh:
                blob = fh.read()
            if entry.file_type == F.FILE_TYPE_BUNDLE_DIR:
                bh, dir_plain = F.read_bundle_directory(
                    blob, entry, content_key=args.content_key
                )
                directory = F.BundleDirectory.from_cbor(ve_cbor.loads(dir_plain))
                n = 0
                for e in directory.entries:
                    F.read_bundle_chunk(
                        blob, bh, directory, e, entry.logical_name,
                        content_key=args.content_key,
                    )
                    n += 1
                print(f"  OK  {entry.logical_name:<38} 目录 + {n} 个资产")
            else:
                _, inner = F.read_sealed_file(
                    blob, entry, content_key=args.content_key
                )
                pkg = F.read_package(inner)
                flags = []
                if pkg.header.compressed:
                    flags.append("lz4")
                if pkg.header.encrypted:
                    flags.append("xor")
                tail = f" [{'+'.join(flags)}]" if flags else ""
                bulk = f" +bulk {len(pkg.bulk):,}" if pkg.bulk else ""
                print(
                    f"  OK  {entry.logical_name:<38} "
                    f"{pkg.header.original_size:>9,} 字节{tail}{bulk}"
                )
            ok += 1
        except Exception as exc:
            print(f"  失败 {entry.logical_name:<38} {type(exc).__name__}: {exc}")
            bad += 1

    print(f"\n通过 {ok}，失败 {bad}")
    return 0 if bad == 0 else 1


def cmd_unpack(args) -> int:
    meta = ve_archive.unpack_all(
        args.build_dir,
        args.out_dir,
        content_key=args.content_key,
        public_key=args.public_key,
        verify_signature=not args.no_verify,
        extract_bundles=not args.no_bundles,
    )
    pkgs = sum(1 for f in meta["files"] if f["kind"] == "package")
    bundles = [f for f in meta["files"] if f["kind"] == "bundle"]
    assets = sum(len(f["assets"]) for f in bundles)
    print(f"已解包到 {args.out_dir}")
    print(f"  包 {pkgs} 个 -> {ve_archive.PKG_DIR}/")
    print(f"  分块包 {len(bundles)} 个，{assets} 个资产已按原路径还原")
    print(f"  元信息 {ve_archive.META_NAME}")
    return 0


def cmd_repack(args) -> int:
    signing_key = _read_key_file(args.signing_key) if args.signing_key else None

    report = ve_archive.repack_all(
        args.work_dir,
        args.out_dir,
        src_dir=args.src,
        content_key=args.content_key,
        signing_key=signing_key,
        strict=not args.lenient,
    )
    print(f"已重建 {report['rebuilt']} 个文件到 {args.out_dir}")
    if report["changed"]:
        print(f"  内容改动 {report['changed']} 个 —— 清单摘要已更新")
        if report["signature_regenerated"]:
            print("  已用提供的私钥重新签名")
        else:
            print("  警告: 签名未更新，引擎将拒绝加载。需要 --signing-key")
    else:
        print("  全部原样重建，签名保持不变")
    return 0


def cmd_text(args) -> int:
    """把 _packages 里的 CBOR 载荷转成可读文本。"""
    import ve_text

    pkg_dir = args.pkg_dir
    if not os.path.isdir(pkg_dir):
        print(f"错误: 找不到目录 {pkg_dir}", file=sys.stderr)
        return 2

    out_dir = args.out_dir or pkg_dir + "_text"
    os.makedirs(out_dir, exist_ok=True)

    print("加载索引与字符串表…")
    res = ve_text.Resolver(pkg_dir, language=args.language)
    print(f"  资源 {len(res.assets)} 条，文本 "
          f"{len(res.table)} 条 + 内插 {len(res.pieces)} 条")

    names = sorted(n for n in os.listdir(pkg_dir) if n.endswith(".cbor"))
    if args.only:
        names = [n for n in names if any(k in n for k in args.only)]

    counts = {}
    for name in names:
        stem = name[: -len(".cbor")]
        with open(os.path.join(pkg_dir, name), "rb") as fh:
            payload = fh.read()

        dst = os.path.join(out_dir, stem + ".txt")
        with open(dst, "w", encoding="utf-8", newline="\n") as out:
            if stem.endswith(".script"):
                kind = "script"
                ve_text.dump_script(payload, res, out)
            elif stem.endswith(".strings"):
                kind = "strings"
                ve_text.dump_strings(payload, out)
            elif stem == "assets":
                kind = "assets"
                ve_text.dump_assets(payload, out)
            elif stem == "project":
                kind = "project"
                ve_text.dump_project(payload, res, out)
            elif stem.endswith(".font"):
                kind = "font"
                ve_text.dump_font(payload, out)
            else:
                kind = "other"
                ve_text.dump_generic(payload, res, out)
        counts[kind] = counts.get(kind, 0) + 1

    print(f"已输出 {len(names)} 个文本文件到 {out_dir}")
    for kind, n in sorted(counts.items()):
        print(f"  {kind:<8} {n}")
    return 0


def cmd_roundtrip(args) -> int:
    """解包再封包，逐字节比对全部输出。"""
    build_dir = args.build_dir
    tmp_root = args.work or tempfile.mkdtemp(prefix="vetool_rt_")
    work = os.path.join(tmp_root, "unpacked")
    rebuilt = os.path.join(tmp_root, "rebuilt")
    cleanup = args.work is None

    try:
        print(f"[1/3] 解包 {build_dir}")
        ve_archive.unpack_all(
            build_dir,
            work,
            content_key=args.content_key,
            public_key=args.public_key,
            verify_signature=not args.no_verify,
            extract_bundles=True,  # .pak 现在从资产完整重建，必须展开
        )

        print(f"[2/3] 封包 -> {rebuilt}")
        ve_archive.repack_all(
            work,
            rebuilt,
            src_dir=build_dir,
            content_key=args.content_key,
            strict=True,
        )

        print("[3/3] 逐字节比对")
        _, (_, manifest, _) = _load_seal(build_dir, args)
        targets = ["game.vseal"] + [f.stored_name for f in manifest.files]

        same = diff = missing = 0
        for name in targets:
            a = os.path.join(build_dir, name)
            b = os.path.join(rebuilt, name)
            if not os.path.isfile(b):
                print(f"  缺失 {name}")
                missing += 1
                continue
            if filecmp.cmp(a, b, shallow=False):
                same += 1
            else:
                sa, sb = os.path.getsize(a), os.path.getsize(b)
                print(f"  不一致 {name}  原 {sa:,} vs 新 {sb:,}")
                if sa == sb:
                    with open(a, "rb") as fa, open(b, "rb") as fb:
                        da, db = fa.read(), fb.read()
                    for i, (x, y) in enumerate(zip(da, db)):
                        if x != y:
                            print(f"      首个差异 @ {i} (0x{i:x}): {x:#04x} vs {y:#04x}")
                            break
                diff += 1

        print(f"\n一致 {same}，不一致 {diff}，缺失 {missing}")
        if diff == 0 and missing == 0:
            print("往返二进制完全一致")
            return 0
        return 1
    finally:
        if cleanup:
            shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def _read_key_file(path: str) -> bytes:
    """读取 Ed25519 私钥：32/64 裸字节，或其 hex 文本表示。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    if len(raw) in (32, 64):
        return raw
    text = raw.strip().decode("ascii", errors="strict")
    key = bytes.fromhex(text)
    if len(key) not in (32, 64):
        raise ValueError(f"私钥须为 32 或 64 字节，得到 {len(key)}")
    return key


def _hexarg(text: str, nbytes: int, label: str) -> bytes:
    data = bytes.fromhex(text)
    if len(data) != nbytes:
        raise argparse.ArgumentTypeError(f"{label} 须为 {nbytes} 字节十六进制")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="vetool",
        description="VersaEngine (vpk / pak / game.vseal) 解包封包工具",
    )
    ap.add_argument("--content-key", type=lambda s: _hexarg(s, 32, "content-key"),
                    default=C.CONTENT_KEY, help="32 字节根密钥（默认取内置常量）")
    ap.add_argument("--build-id", type=lambda s: _hexarg(s, 16, "build-id"),
                    default=C.BUILD_ID, help="16 字节 build id")
    ap.add_argument("--public-key", type=lambda s: _hexarg(s, 32, "public-key"),
                    default=C.SIGNING_PUBLIC_KEY, help="32 字节 Ed25519 公钥")
    ap.add_argument("--no-verify", action="store_true", help="跳过签名验证")
    ap.add_argument("--no-build-check", action="store_true", help="跳过 build_id 比对")

    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="显示 vseal 与清单概要")
    p.add_argument("build_dir")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("list", help="列出清单中的全部文件")
    p.add_argument("build_dir")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("verify", help="验证并解开每一层")
    p.add_argument("build_dir")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("unpack", help="解包到目录")
    p.add_argument("build_dir")
    p.add_argument("out_dir")
    p.add_argument("--no-bundles", action="store_true", help="不展开 .pak 内的资产")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("repack", help="从解包产物重建")
    p.add_argument("work_dir")
    p.add_argument("out_dir")
    p.add_argument("--src", help="原 build 目录，未改动的 LZ4 包从此取压缩字节")
    p.add_argument("--signing-key", help="Ed25519 私钥文件（32/64 字节，hex 或裸字节）")
    p.add_argument("--lenient", action="store_true", help="允许无法精确重建的情况")
    p.set_defaults(func=cmd_repack)

    p = sub.add_parser("roundtrip", help="解包+封包并逐字节比对")
    p.add_argument("build_dir")
    p.add_argument("--work", help="保留中间产物的目录")
    p.set_defaults(func=cmd_roundtrip)

    p = sub.add_parser("text", help="把 CBOR 载荷转成可读文本")
    p.add_argument("pkg_dir", help="unpack 产物里的 _packages 目录")
    p.add_argument("out_dir", nargs="?", help="输出目录，默认 <pkg_dir>_text")
    p.add_argument("--language", help="优先使用的语言字符串表，如 ja / zh-CN")
    p.add_argument("--only", nargs="+", metavar="子串",
                   help="只转换文件名含这些子串的载荷")
    p.set_defaults(func=cmd_text)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (ve_archive.ArchiveError, F.VeFormatError, C.VeAuthError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
