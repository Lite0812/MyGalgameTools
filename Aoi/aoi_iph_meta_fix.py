"""修复 chs 补丁目录中 IPH 图片丢失的元信息。

汉化重打包时只保留了 width/height/像素数据，头部 0x48..0x4F 的显示坐标、
单元尺寸以及 0x54 的透明色键全部被清零/置为 0xFFFF，导致：

1. 图片全部绘制在左上角（x/y 丢失）；
2. 精灵表按整图而非单元绘制（cell width/height 丢失）；
3. 原本透明的区域变成不透明的浅绿底色（transparency_key 丢失 + bit15 被清）。

本脚本以 IPH/ 目录下的日文原图为参照，把这些字段写回 chs 中的同名文件，
并按「原图透明且像素颜色未被改动」的保守规则恢复 bit15 透明标记，
避免误伤汉化时新绘制的文字笔画。
"""

from __future__ import annotations

import argparse
import shutil
import struct
import sys
from pathlib import Path

from aoi_iph_tool import HEADER_SIZE, decode_iph_buffer

# 头部中需要从原图回写的字段偏移
OFF_WIDTH = 0x40
OFF_HEIGHT = 0x42
OFF_POS_X = 0x48
OFF_POS_Y = 0x4A
OFF_CELL_W = 0x4C
OFF_CELL_H = 0x4E
OFF_COMPRESSION = 0x52
OFF_TRANSPARENCY_KEY = 0x54

NO_TRANSPARENCY = 0xFFFF


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def put_u16(buf: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<H", buf, offset, value)


def transparent_mask(pixels: memoryview, transparency_key: int) -> bytearray:
    """返回每像素是否透明（bit15 置位，或颜色等于透明色键）。"""
    mask = bytearray(len(pixels))
    for i, value in enumerate(pixels):
        if value & 0x8000:
            mask[i] = 1
        elif transparency_key != NO_TRANSPARENCY and (value & 0x7FFF) == transparency_key:
            mask[i] = 1
    return mask


def fix_one(chs_path: Path, orig_path: Path, restore_alpha: bool) -> dict[str, object]:
    """修复单个文件，返回本次改动的摘要。"""
    chs_data = bytearray(chs_path.read_bytes())
    orig_head = orig_path.read_bytes()[:HEADER_SIZE]

    chs_w, chs_h = u16(chs_data, OFF_WIDTH), u16(chs_data, OFF_HEIGHT)
    orig_w, orig_h = u16(orig_head, OFF_WIDTH), u16(orig_head, OFF_HEIGHT)
    orig_cell_w, orig_cell_h = u16(orig_head, OFF_CELL_W), u16(orig_head, OFF_CELL_H)
    orig_key = u16(orig_head, OFF_TRANSPARENCY_KEY)

    if chs_h != orig_h:
        raise RuntimeError(f"高度不一致，无法安全推导单元尺寸: chs={chs_h} orig={orig_h}")

    changed: list[str] = []

    for offset, name in ((OFF_POS_X, "x"), (OFF_POS_Y, "y"), (OFF_CELL_H, "cell_h")):
        want = u16(orig_head, offset)
        if u16(chs_data, offset) != want:
            put_u16(chs_data, offset, want)
            changed.append(name)

    # 单元宽度：原图按整幅宽度分格时跟随汉化后的新宽度，否则沿用原值。
    # 汉化图偶尔会比原图宽 1px（如 din_b02 65 -> 66），此时格子必须一起变宽，
    # 不然最后一列会被裁掉。
    want_cell_w = chs_w if orig_cell_w == orig_w else orig_cell_w
    if u16(chs_data, OFF_CELL_W) != want_cell_w:
        put_u16(chs_data, OFF_CELL_W, want_cell_w)
        changed.append("cell_w")

    if u16(chs_data, OFF_TRANSPARENCY_KEY) != orig_key:
        put_u16(chs_data, OFF_TRANSPARENCY_KEY, orig_key)
        changed.append("transparency_key")

    restored_px = 0
    skipped_px = 0
    if restore_alpha and u16(chs_data, OFF_COMPRESSION) == 0 and (chs_w, chs_h) == (orig_w, orig_h):
        _, orig_raw = decode_iph_buffer(orig_path.read_bytes())
        orig_px = memoryview(orig_raw).cast("H")
        chs_px = memoryview(chs_data)[HEADER_SIZE:].cast("H")
        if len(orig_px) == len(chs_px):
            was_transparent = transparent_mask(orig_px, orig_key)
            for i, flag in enumerate(was_transparent):
                if not flag or (chs_px[i] & 0x8000):
                    continue
                if (chs_px[i] & 0x7FFF) == (orig_px[i] & 0x7FFF):
                    # 像素未被改动，原本的透明属性可以安全恢复
                    chs_px[i] |= 0x8000
                    restored_px += 1
                elif orig_key == NO_TRANSPARENCY or (chs_px[i] & 0x7FFF) != orig_key:
                    # 汉化时在此处画了新内容，保持不透明
                    skipped_px += 1
            chs_px.release()
        orig_px.release()
        if restored_px:
            changed.append(f"alpha({restored_px}px)")

    return {
        "changed": changed,
        "data": bytes(chs_data),
        "restored_px": restored_px,
        "skipped_px": skipped_px,
    }


def run(chs_dir: Path, orig_dir: Path, backup_dir: Path | None, restore_alpha: bool, dry_run: bool) -> int:
    files = sorted(p for p in chs_dir.iterdir() if p.is_file() and p.suffix.lower() == ".iph")
    if not files:
        print(f"错误: {chs_dir} 下没有 .iph 文件")
        return 1

    fixed: list[tuple[str, list[str]]] = []
    untouched: list[str] = []
    missing: list[str] = []
    failed: list[tuple[str, str]] = []
    total_restored = 0
    total_skipped = 0

    for path in files:
        orig_path = orig_dir / path.name
        if not orig_path.is_file():
            missing.append(path.name)
            continue
        try:
            result = fix_one(path, orig_path, restore_alpha)
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不应中断整批
            failed.append((path.name, str(exc)))
            continue

        changed = result["changed"]
        assert isinstance(changed, list)
        total_restored += int(result["restored_px"])
        total_skipped += int(result["skipped_px"])
        if not changed:
            untouched.append(path.name)
            continue

        if not dry_run:
            if backup_dir is not None:
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_dir / path.name
                if not backup_path.exists():
                    shutil.copy2(path, backup_path)
            path.write_bytes(result["data"])  # type: ignore[arg-type]
        fixed.append((path.name, changed))

    print(f"{'[试运行] ' if dry_run else ''}共扫描 {len(files)} 个 IPH 文件")
    print(f"  已修复      : {len(fixed)}")
    print(f"  本就正确    : {len(untouched)}")
    print(f"  无原图参照  : {len(missing)}" + (f" -> {missing}" if missing else ""))
    if failed:
        print(f"  失败        : {len(failed)}")
        for name, reason in failed:
            print(f"    {name}: {reason}")
    if restore_alpha:
        print(f"  恢复透明像素: {total_restored}（保留汉化新绘制的 {total_skipped} 像素为不透明）")
    if backup_dir is not None and not dry_run and fixed:
        print(f"  原文件备份至: {backup_dir}")

    for name, changed in fixed[:15]:
        print(f"    {name}: {', '.join(changed)}")
    if len(fixed) > 15:
        print(f"    ... 其余 {len(fixed) - 15} 个略")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="用原始 IPH 修复 chs 补丁图片丢失的元信息")
    parser.add_argument("chs_dir", help="待修复的 chs 目录")
    parser.add_argument("orig_dir", help="原始 IPH 参照目录")
    parser.add_argument("--backup-dir", default=None, help="备份目录（默认 <chs_dir>/../chs_iph_backup）")
    parser.add_argument("--no-backup", action="store_true", help="不备份")
    parser.add_argument("--no-alpha", action="store_true", help="只修坐标/单元尺寸/色键，不恢复 bit15 透明标记")
    parser.add_argument("--dry-run", action="store_true", help="只报告不写入")
    args = parser.parse_args(argv[1:])

    chs_dir = Path(args.chs_dir)
    orig_dir = Path(args.orig_dir)
    if not chs_dir.is_dir():
        print(f"错误: 目录不存在 {chs_dir}")
        return 1
    if not orig_dir.is_dir():
        print(f"错误: 目录不存在 {orig_dir}")
        return 1

    if args.no_backup:
        backup_dir = None
    elif args.backup_dir:
        backup_dir = Path(args.backup_dir)
    else:
        backup_dir = chs_dir.parent / "chs_iph_backup"

    return run(chs_dir, orig_dir, backup_dir, restore_alpha=not args.no_alpha, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
