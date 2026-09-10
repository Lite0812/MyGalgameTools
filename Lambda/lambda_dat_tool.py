#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lambda 引擎 DAT/CLS_FILELINK 封包工具。

用法:
  python lambda_dat_tool.py list MC_BASE.DAT
  python lambda_dat_tool.py unpack MC_BASE.DAT out_base
  python lambda_dat_tool.py pack out_base MC_BASE.repack.DAT
  python lambda_dat_tool.py verify MC_BASE.DAT _work/verify_base
  python lambda_dat_tool.py extract MC_BASE.DAT CID_ALL_FONT20_DAT CID_ALL_FONT20_DAT.out
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SIGNATURE = b"CLS_FILELINK"
HEADER_SIZE = 0x40
ENTRY_SIZE = 0x40
NAME_SIZE = 0x28
MANIFEST_NAME = "_lambda_dat_manifest.json"
ORDER_NAME = "_order.txt"
DEFAULT_ENCODING = "cp932"


class DatError(Exception):
    pass


@dataclass
class DatEntry:
    index: int
    name: str
    name_raw: bytes
    flag: int
    offset: int
    size: int
    tail: bytes
    disk_name: str = ""


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_exact_at(f, offset: int, size: int) -> bytes:
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise DatError(f"读取失败: offset=0x{offset:X}, size={size}")
    return data


def decode_name(raw: bytes, encoding: str = DEFAULT_ENCODING) -> str:
    raw = raw.split(b"\0", 1)[0]
    return raw.decode(encoding)


def encode_name(name: str, original_raw: bytes | None = None, encoding: str = DEFAULT_ENCODING) -> bytes:
    if original_raw is not None:
        base = original_raw.split(b"\0", 1)[0]
        if base.decode(encoding) == name and len(original_raw) == NAME_SIZE:
            return original_raw
    raw = name.encode(encoding)
    if len(raw) >= NAME_SIZE:
        raise DatError(f"文件名过长，不能写入 0x28 字节字段: {name}")
    return raw + b"\0" * (NAME_SIZE - len(raw))


def safe_disk_name(index: int, name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name).strip(" .")
    if not cleaned:
        cleaned = "unnamed"
    return f"{index:06d}_{cleaned}"


def parse_archive(path: Path, encoding: str = DEFAULT_ENCODING) -> tuple[bytearray, list[DatEntry]]:
    size = path.stat().st_size
    with path.open("rb") as f:
        header = bytearray(read_exact_at(f, 0, HEADER_SIZE))
        if header[: len(SIGNATURE)] != SIGNATURE:
            raise DatError(f"{path} 不是 CLS_FILELINK 封包")
        count = struct.unpack_from("<I", header, 0x10)[0]
        index_offset = struct.unpack_from("<I", header, 0x18)[0]
        if count <= 0 or count > 100000:
            raise DatError(f"异常条目数: {count}")
        if index_offset + count * ENTRY_SIZE > size:
            raise DatError("索引表越界")
        entries: list[DatEntry] = []
        for i in range(count):
            pos = index_offset + i * ENTRY_SIZE
            rec = read_exact_at(f, pos, ENTRY_SIZE)
            name_raw = rec[:NAME_SIZE]
            name = decode_name(name_raw, encoding)
            flag, offset, entry_size = struct.unpack_from("<III", rec, 0x28)
            tail = rec[0x34:0x40]
            if offset < 0 or entry_size < 0 or offset + entry_size > size:
                raise DatError(f"条目越界: #{i} {name} offset=0x{offset:X} size={entry_size}")
            entries.append(DatEntry(i, name, name_raw, flag, offset, entry_size, tail))
        return header, entries


class FastCopier:
    def __init__(self) -> None:
        self.dll = None
        dll_path = Path(__file__).with_name("lambda_dat_fast.dll")
        if dll_path.exists():
            try:
                dll = ctypes.WinDLL(str(dll_path))
                fn = dll.lambda_copy_range_w
                fn.argtypes = [
                    ctypes.c_wchar_p,
                    ctypes.c_wchar_p,
                    ctypes.c_uint64,
                    ctypes.c_uint64,
                    ctypes.c_uint32,
                ]
                fn.restype = ctypes.c_int
                self.dll = dll
            except OSError:
                self.dll = None

    @property
    def enabled(self) -> bool:
        return self.dll is not None

    def copy_range(self, archive: Path, out_path: Path, offset: int, size: int) -> None:
        if self.dll is not None:
            rc = self.dll.lambda_copy_range_w(str(archive), str(out_path), offset, size, 1024 * 1024)
            if rc == 0:
                return
            raise DatError(f"DLL 复制失败 rc={rc}: {out_path}")
        with archive.open("rb") as src, out_path.open("wb") as dst:
            src.seek(offset)
            left = size
            while left:
                chunk = src.read(min(left, 1024 * 1024))
                if not chunk:
                    raise DatError(f"复制条目时提前 EOF: {out_path}")
                dst.write(chunk)
                left -= len(chunk)


def manifest_from_entries(archive: Path, header: bytearray, entries: list[DatEntry], encoding: str) -> dict:
    archive_size = archive.stat().st_size
    gap_hex: dict[int, str] = {}
    with archive.open("rb") as f:
        for i, entry in enumerate(entries):
            end = entry.offset + entry.size
            next_offset = entries[i + 1].offset if i + 1 < len(entries) else archive_size
            if next_offset < end:
                raise DatError(f"条目重叠: #{entry.index} {entry.name}")
            gap_hex[entry.index] = read_exact_at(f, end, next_offset - end).hex()
    return {
        "tool": "lambda_dat_tool",
        "format": "Lambda CLS_FILELINK DAT",
        "version": 1,
        "source_archive": archive.name,
        "source_size": archive_size,
        "source_md5": md5_file(archive),
        "encoding": encoding,
        "header_hex": bytes(header).hex(),
        "entries": [
            {
                "index": e.index,
                "name": e.name,
                "name_raw_hex": e.name_raw.hex(),
                "disk_name": e.disk_name,
                "flag": e.flag,
                "offset": e.offset,
                "size": e.size,
                "tail_hex": e.tail.hex(),
                "gap_after_hex": gap_hex[e.index],
            }
            for e in entries
        ],
    }


def write_order(out_dir: Path, entries: Iterable[DatEntry]) -> None:
    with (out_dir / ORDER_NAME).open("w", encoding="utf-8", newline="\n") as f:
        f.write("# index\tdisk_name\toriginal_name\toffset\tsize\n")
        for e in entries:
            f.write(f"{e.index}\t{e.disk_name}\t{e.name}\t0x{e.offset:X}\t{e.size}\n")


def cmd_list(args: argparse.Namespace) -> int:
    archive = Path(args.archive)
    _, entries = parse_archive(archive, args.encoding)
    print(f"封包: {archive}")
    print(f"条目数: {len(entries)}")
    print(f"{'index':>6} {'offset':>10} {'size':>10} {'flag':>10} name")
    for e in entries:
        if args.filter and args.filter.lower() not in e.name.lower():
            continue
        print(f"{e.index:6d} 0x{e.offset:08X} {e.size:10d} 0x{e.flag:08X} {e.name}")
    return 0


def cmd_unpack(args: argparse.Namespace) -> int:
    archive = Path(args.archive).resolve()
    out_dir = Path(args.out_dir).resolve()
    header, entries = parse_archive(archive, args.encoding)
    out_dir.mkdir(parents=True, exist_ok=True)
    copier = FastCopier()
    print(f"开始解包: {archive}")
    print(f"输出目录: {out_dir}")
    print(f"条目数: {len(entries)}")
    print(f"加速 DLL: {'启用' if copier.enabled else '未启用，使用纯 Python'}")
    for e in entries:
        e.disk_name = safe_disk_name(e.index, e.name)
        copier.copy_range(archive, out_dir / e.disk_name, e.offset, e.size)
        if args.verbose:
            print(f"  [{e.index:06d}] {e.name} -> {e.disk_name} ({e.size} bytes)")
    write_order(out_dir, entries)
    manifest = manifest_from_entries(archive, header, entries, args.encoding)
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print("解包完成。已生成 _order.txt 和 _lambda_dat_manifest.json。")
    return 0


def load_manifest(in_dir: Path) -> dict:
    path = in_dir / MANIFEST_NAME
    if not path.exists():
        raise DatError(f"缺少 manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_pack(args: argparse.Namespace) -> int:
    in_dir = Path(args.in_dir).resolve()
    out_archive = Path(args.out_archive).resolve()
    manifest = load_manifest(in_dir)
    entries_meta = sorted(manifest["entries"], key=lambda x: x["index"])
    count = len(entries_meta)
    index_offset = HEADER_SIZE
    data_offset = HEADER_SIZE + count * ENTRY_SIZE
    header = bytearray.fromhex(manifest["header_hex"])
    if len(header) != HEADER_SIZE:
        raise DatError("manifest 中 header 长度异常")
    struct.pack_into("<I", header, 0x10, count)
    struct.pack_into("<I", header, 0x18, index_offset)
    struct.pack_into("<I", header, 0x1C, data_offset)

    print(f"开始封包: {in_dir} -> {out_archive}")
    out_archive.parent.mkdir(parents=True, exist_ok=True)
    records: list[bytes] = []
    offset = data_offset
    with out_archive.open("wb") as out:
        out.write(header)
        out.write(b"\0" * (count * ENTRY_SIZE))
        for m in entries_meta:
            disk_name = m["disk_name"]
            src_path = in_dir / disk_name
            if not src_path.exists():
                raise DatError(f"缺少条目文件: {src_path}")
            size = src_path.stat().st_size
            name_raw = encode_name(m["name"], bytes.fromhex(m["name_raw_hex"]), manifest.get("encoding", DEFAULT_ENCODING))
            rec = bytearray(ENTRY_SIZE)
            rec[:NAME_SIZE] = name_raw
            struct.pack_into("<III", rec, 0x28, int(m["flag"]), offset, size)
            rec[0x34:0x40] = bytes.fromhex(m["tail_hex"])
            records.append(bytes(rec))
            with src_path.open("rb") as src:
                shutil.copyfileobj(src, out, 1024 * 1024)
            gap = bytes.fromhex(m.get("gap_after_hex", ""))
            if gap:
                out.write(gap)
            if args.verbose:
                print(f"  [{m['index']:06d}] {m['name']} offset=0x{offset:X} size={size}")
            offset += size + len(gap)
        out.seek(index_offset)
        for rec in records:
            out.write(rec)
    print(f"封包完成: {out_archive}")
    print(f"MD5: {md5_file(out_archive)}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    archive = Path(args.archive).resolve()
    work = Path(args.work_dir).resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    rebuilt = work.with_suffix(".rebuild.DAT")
    try:
        unpack_args = argparse.Namespace(
            archive=str(archive),
            out_dir=str(work),
            encoding=args.encoding,
            verbose=False,
        )
        cmd_unpack(unpack_args)
        pack_args = argparse.Namespace(in_dir=str(work), out_archive=str(rebuilt), verbose=False)
        cmd_pack(pack_args)
        src_md5 = md5_file(archive)
        new_md5 = md5_file(rebuilt)
        print(f"原始 MD5: {src_md5}")
        print(f"重建 MD5: {new_md5}")
        if src_md5 == new_md5:
            print("verify 通过：bit-perfect。")
            return 0
        print("verify 失败：重建文件与原始文件不一致。")
        return 2
    finally:
        if args.keep_work:
            print(f"保留临时目录: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
            if rebuilt.exists() and not args.keep_rebuild:
                rebuilt.unlink()


def cmd_extract(args: argparse.Namespace) -> int:
    archive = Path(args.archive).resolve()
    _, entries = parse_archive(archive, args.encoding)
    matches = [e for e in entries if e.name == args.name or str(e.index) == args.name]
    if not matches:
        matches = [e for e in entries if args.name.lower() in e.name.lower()]
    if not matches:
        raise DatError(f"找不到条目: {args.name}")
    if len(matches) > 1 and not args.all:
        print("找到多个匹配项，请使用精确名称、序号或 --all：")
        for e in matches:
            print(f"  [{e.index:06d}] {e.name} offset=0x{e.offset:X} size={e.size}")
        return 3
    out_path = Path(args.out).resolve() if args.out else None
    copier = FastCopier()
    for e in matches:
        dst = out_path
        if dst is None or len(matches) > 1:
            dst_dir = out_path if out_path else Path.cwd()
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / safe_disk_name(e.index, e.name)
        copier.copy_range(archive, dst, e.offset, e.size)
        print(f"已提取 [{e.index:06d}] {e.name} -> {dst}")
        print(f"MD5: {md5_file(dst)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lambda DAT/CLS_FILELINK 解包封包工具")
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--encoding", default=DEFAULT_ENCODING, help="资源名编码，默认 cp932")
    parser.add_argument("--encoding", default=DEFAULT_ENCODING, help="资源名编码，默认 cp932")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", parents=[parent], help="列出封包内容")
    p.add_argument("archive")
    p.add_argument("--filter", help="按名称过滤")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("unpack", parents=[parent], help="解包 DAT 到目录")
    p.add_argument("archive")
    p.add_argument("out_dir")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("pack", parents=[parent], help="按 manifest 重新封包")
    p.add_argument("in_dir")
    p.add_argument("out_archive")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("verify", parents=[parent], help="解包再封包并做 MD5 验证")
    p.add_argument("archive")
    p.add_argument("work_dir")
    p.add_argument("--keep-work", action="store_true")
    p.add_argument("--keep-rebuild", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("extract", parents=[parent], help="按资源名或序号提取单项")
    p.add_argument("archive")
    p.add_argument("name")
    p.add_argument("out", nargs="?")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_extract)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DatError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
