"""零突变验证：对目录下每个 CBOR 载荷做 反汇编 -> 汇编 -> 比对。

用法::

    python verify_roundtrip.py [载荷目录] [--work 中间目录] [--encoding utf-8]

逐字节比对并核对 sha256。任何一个不一致都会打印差异位置。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import assembler
import disassembler
import opcodelist as OL
import ve_cbor

def _default_pkg() -> str:
    """默认载荷目录：先找本脚本同级的 unpacked/，再往上一级找。

    工具在 vetool/ 下，而解包产物在仓库根，所以两处都要试。
    """
    for base in (_HERE, os.path.dirname(_HERE)):
        cand = os.path.join(base, "unpacked", "_packages")
        if os.path.isdir(cand):
            return cand
    return os.path.join(_HERE, "unpacked", "_packages")


DEFAULT_PKG = _default_pkg()


def _first_diff(a: bytes, b: bytes) -> str:
    if len(a) != len(b):
        tail = f"（长度 {len(a)} vs {len(b)}）"
    else:
        tail = ""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"偏移 0x{i:X} 处 {x:02X} != {y:02X}{tail}"
    return f"前缀相同，长度不同{tail}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="反汇编/汇编往返一致性验证")
    ap.add_argument("pkg_dir", nargs="?", default=DEFAULT_PKG)
    ap.add_argument("--work", help="中间文件目录，默认用临时目录并清理")
    ap.add_argument("--encoding", default=OL.DEFAULT_ENCODING)
    ap.add_argument("--keep", action="store_true", help="保留中间文件")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.pkg_dir):
        print(f"错误: 找不到目录 {args.pkg_dir}", file=sys.stderr)
        return 2

    work = args.work or tempfile.mkdtemp(prefix="ve_asm_rt_")
    os.makedirs(work, exist_ok=True)
    cleanup = args.work is None and not args.keep

    names = sorted(n for n in os.listdir(args.pkg_dir) if n.endswith(".cbor"))
    print(f"载荷 {len(names)} 个，中间目录 {work}\n")

    nm = disassembler.Names(args.pkg_dir)
    # 共享角色名册：反汇编器批处理时会抽出来，验证也走同一条路径
    roster = disassembler.shared_roster(
        [os.path.join(args.pkg_dir, n) for n in names], nm, work)
    if roster is not None:
        print(f"共享角色名册 {len(roster[1])} 名 -> {roster[0]}\n")
    ok = bad = err = 0
    total_asm = 0
    try:
        for name in names:
            stem = name[: -len(".cbor")]
            src = os.path.join(args.pkg_dir, name)
            with open(src, "rb") as fh:
                original = fh.read()
            try:
                text, _ = disassembler.disassemble(
                    original, stem, work, args.encoding, nm, False, roster)
                asm_path = os.path.join(work, stem + ".asm.txt")
                with open(asm_path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                total_asm += len(text.encode("utf-8"))
                value, _ = assembler.assemble(text, work, args.encoding)
                rebuilt = ve_cbor.dumps(value)
            except (OL.AsmError, ve_cbor.CborError) as exc:
                print(f"[出错] {stem}: {exc}")
                err += 1
                continue
            if rebuilt == original:
                ok += 1
            else:
                bad += 1
                print(f"[不一致] {stem}: {_first_diff(original, rebuilt)}")
                print(f"    原始 sha256 {hashlib.sha256(original).hexdigest()}")
                print(f"    重建 sha256 {hashlib.sha256(rebuilt).hexdigest()}")
    finally:
        if cleanup:
            shutil.rmtree(work, ignore_errors=True)

    print(f"\n一致 {ok} / 不一致 {bad} / 出错 {err}")
    print(f"asm 文本合计 {total_asm / 1048576:.1f} MiB")
    return 0 if bad == 0 and err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
