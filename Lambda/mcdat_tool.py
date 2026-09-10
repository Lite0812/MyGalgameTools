#!/usr/bin/env python3
"""MC 游戏 CLS_FILELINK DAT 封包的无损解包与回包工具。

资源正文直接使用资源 ID 作为文件名。同名资源从第二个起使用
``.__dup2``、``.__dup3`` 后缀区分。

解包目录只生成一个最小索引 ``_mcdat_index.tsv``。索引按原始顺序记录
每项不可推导的 5 字节属性和资源 ID，不记录偏移、长度、哈希或填充。

回包时会重新计算偏移、长度、对齐填充、条目数以及规范封包头。
"""

from __future__ import annotations

import argparse
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path


RECORD_SIZE = 0x40
NAME_SIZE = 0x27
ATTR_SIZE = 5
ALIGNMENT = 0x10
MAGIC_FIELD = b"CLS_FILELINK\0\0\0\0"
MANIFEST_NAME = "_mcdat_index.tsv"
MANIFEST_MAGIC = b"MCDAT_INDEX_V1"
COPY_CHUNK = 1024 * 1024
UINT32_MAX = 0xFFFFFFFF
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class ArchiveError(Exception):
    pass


class ChineseArgumentParser(argparse.ArgumentParser):
    """将 argparse 自动生成的固定说明文字本地化为中文。"""

    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置参数"
        self._optionals.title = "选项"
        self.add_argument("-h", "--help", action="help", help="显示此帮助信息并退出")

    def format_usage(self) -> str:
        return super().format_usage().replace("usage: ", "用法：", 1)

    def format_help(self) -> str:
        return super().format_help().replace("usage: ", "用法：", 1)


@dataclass(frozen=True)
class Entry:
    index: int
    name: bytes
    attrs: bytes
    offset: int
    size: int


@dataclass(frozen=True)
class Archive:
    path: Path
    size: int
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class InputEntry:
    index: int
    name: bytes
    attrs: bytes
    path: Path
    size: int
    offset: int


def align_up(value: int, alignment: int = ALIGNMENT) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def read_exact(stream, size: int, description: str) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ArchiveError(
            f"{description} 已截断：应读取 {size} 字节，实际读取 {len(data)} 字节"
        )
    return data


def canonical_header(count: int) -> bytes:
    data_offset = RECORD_SIZE * (count + 1)
    if count > UINT32_MAX or data_offset > UINT32_MAX:
        raise ArchiveError("条目表超出 DAT 格式的 32 位范围")
    return (
        MAGIC_FIELD
        + struct.pack("<I4xII", count, RECORD_SIZE, data_offset)
        + bytes(32)
    )


def require_zero(data: bytes, description: str) -> None:
    if any(data):
        raise ArchiveError(f"{description} 含非零数据，若不增加额外元数据便无法无损表示")


def parse_archive(path: Path) -> Archive:
    try:
        total_size = path.stat().st_size
    except OSError as exc:
        raise ArchiveError(f"无法取得文件信息 {path}：{exc}") from exc

    if total_size < RECORD_SIZE:
        raise ArchiveError("文件小于 0x40 字节的 DAT 封包头")

    entries: list[Entry] = []
    try:
        with path.open("rb") as stream:
            header = read_exact(stream, RECORD_SIZE, "DAT 封包头")
            count = struct.unpack_from("<I", header, 0x10)[0]
            minimum_size = RECORD_SIZE * (count + 1)
            if minimum_size > total_size:
                raise ArchiveError(
                    f"条目表结束于 0x{minimum_size:X}，超出文件大小 0x{total_size:X}"
                )
            if header != canonical_header(count):
                raise ArchiveError(
                    "封包头不是规范布局；为避免在没有附加元数据时丢失信息，已拒绝解包"
                )

            expected_end = minimum_size
            for index in range(count):
                record = read_exact(stream, RECORD_SIZE, f"条目 {index} 的记录")
                name_field = record[:NAME_SIZE]
                nul = name_field.find(b"\0")
                if nul < 0:
                    raise ArchiveError(f"条目 {index} 的资源 ID 没有以 NUL 结尾")
                require_zero(name_field[nul:], f"条目 {index} 的资源 ID 填充区")
                name = name_field[:nul]
                attrs = record[NAME_SIZE : NAME_SIZE + ATTR_SIZE]
                offset, size = struct.unpack_from("<II", record, 0x2C)
                require_zero(record[0x34:], f"条目 {index} 的记录尾部")

                expected_offset = align_up(expected_end)
                if offset != expected_offset:
                    raise ArchiveError(
                        f"条目 {index} 的偏移为 0x{offset:X}，规范偏移应为 0x{expected_offset:X}"
                    )
                if offset + size > total_size:
                    raise ArchiveError(f"条目 {index} 的资源正文超出封包范围")
                entries.append(Entry(index, name, attrs, offset, size))
                expected_end = offset + size

            for entry in entries:
                gap_start = minimum_size if entry.index == 0 else (
                    entries[entry.index - 1].offset + entries[entry.index - 1].size
                )
                gap_size = entry.offset - gap_start
                if gap_size:
                    stream.seek(gap_start)
                    require_zero(
                        read_exact(stream, gap_size, f"条目 {entry.index} 之前的对齐填充"),
                        f"条目 {entry.index} 之前的对齐填充",
                    )

            canonical_size = align_up(expected_end)
            if total_size != canonical_size:
                raise ArchiveError(
                    f"文件大小为 0x{total_size:X}，规范大小应为 0x{canonical_size:X}"
                )
            if total_size > expected_end:
                stream.seek(expected_end)
                require_zero(
                    read_exact(stream, total_size - expected_end, "封包尾部填充"),
                    "封包尾部填充",
                )
    except OSError as exc:
        raise ArchiveError(f"无法读取 {path}：{exc}") from exc

    return Archive(path, total_size, tuple(entries))


def direct_resource_name(name: bytes, index: int) -> str:
    try:
        resource_id = name.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ArchiveError(f"条目 {index} 的资源 ID 不是 ASCII，无法直接用作文件名") from exc
    if not resource_id:
        raise ArchiveError(f"条目 {index} 的资源 ID 为空")
    if any(ord(char) < 32 or char in '<>:"/\\|?*' for char in resource_id):
        raise ArchiveError(f"条目 {index} 的资源 ID 含 Windows 文件名禁用字符")
    if resource_id.endswith((" ", ".")) or resource_id in {".", ".."}:
        raise ArchiveError(f"条目 {index} 的资源 ID 无法直接用作 Windows 文件名")
    if resource_id.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        raise ArchiveError(f"条目 {index} 的资源 ID 是 Windows 保留文件名")
    if resource_id.casefold() == MANIFEST_NAME.casefold():
        raise ArchiveError(f"条目 {index} 的资源 ID 与最小索引文件名冲突")
    return resource_id


def resource_file_names(entries) -> list[str]:
    occurrences: dict[bytes, int] = {}
    file_names: list[str] = []
    used_names: set[str] = set()
    for entry in entries:
        resource_id = direct_resource_name(entry.name, entry.index)
        occurrence = occurrences.get(entry.name, 0) + 1
        occurrences[entry.name] = occurrence
        file_name = resource_id if occurrence == 1 else f"{resource_id}.__dup{occurrence}"
        folded_name = file_name.casefold()
        if folded_name in used_names:
            raise ArchiveError(f"条目 {entry.index} 生成的文件名发生冲突：{file_name}")
        used_names.add(folded_name)
        file_names.append(file_name)
    return file_names


def write_manifest(output_dir: Path, entries: tuple[Entry, ...]) -> None:
    lines = [MANIFEST_MAGIC]
    for entry in entries:
        resource_id = direct_resource_name(entry.name, entry.index).encode("ascii")
        lines.append(entry.attrs.hex().encode("ascii") + b"\t" + resource_id)
    manifest_path = output_dir / MANIFEST_NAME
    with manifest_path.open("xb") as manifest:
        manifest.write(b"\n".join(lines) + b"\n")


def copy_range(source, destination, size: int) -> None:
    remaining = size
    while remaining:
        chunk = source.read(min(remaining, COPY_CHUNK))
        if not chunk:
            raise ArchiveError(f"资源正文提前结束，仍缺少 {remaining} 字节")
        destination.write(chunk)
        remaining -= len(chunk)


def ensure_empty_output_dir(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise ArchiveError(f"输出路径不是目录：{path}")
        try:
            next(path.iterdir())
        except StopIteration:
            return
        raise ArchiveError(f"输出目录不为空：{path}")
    path.mkdir(parents=True)


def unpack_archive(archive_path: Path, output_dir: Path) -> None:
    archive = parse_archive(archive_path)
    file_names = resource_file_names(archive.entries)
    ensure_empty_output_dir(output_dir)

    try:
        with archive.path.open("rb") as source:
            for entry, file_name in zip(archive.entries, file_names):
                destination_path = output_dir / file_name
                source.seek(entry.offset)
                with destination_path.open("xb") as destination:
                    copy_range(source, destination, entry.size)
        write_manifest(output_dir, archive.entries)
    except OSError as exc:
        raise ArchiveError(f"解包失败：{exc}") from exc

    payload_size = sum(entry.size for entry in archive.entries)
    print(
        f"已解包 {len(archive.entries)} 个条目（资源正文共 {payload_size} 字节）"
        f"到 {output_dir}"
    )


def parse_manifest(input_dir: Path) -> list[tuple[bytes, bytes]]:
    manifest_path = input_dir / MANIFEST_NAME
    try:
        lines = manifest_path.read_bytes().splitlines()
    except OSError as exc:
        raise ArchiveError(f"无法读取最小索引 {manifest_path}：{exc}") from exc
    if not lines or lines[0] != MANIFEST_MAGIC:
        raise ArchiveError(f"最小索引格式不正确：{manifest_path}")

    records: list[tuple[bytes, bytes]] = []
    for line_number, line in enumerate(lines[1:], 2):
        fields = line.split(b"\t")
        if len(fields) != 2 or len(fields[0]) != ATTR_SIZE * 2:
            raise ArchiveError(f"最小索引第 {line_number} 行格式不正确")
        try:
            attrs = bytes.fromhex(fields[0].decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ArchiveError(f"最小索引第 {line_number} 行的属性不是 10 位十六进制数") from exc
        name = fields[1]
        if b"\0" in name or len(name) >= NAME_SIZE:
            raise ArchiveError(
                f"最小索引第 {line_number} 行的资源 ID 必须为 1 至 {NAME_SIZE - 1} 字节，且不能包含 NUL"
            )
        direct_resource_name(name, line_number - 2)
        records.append((name, attrs))
    return records


def collect_input_entries(input_dir: Path) -> list[InputEntry]:
    if not input_dir.is_dir():
        raise ArchiveError(f"输入路径不是目录：{input_dir}")

    manifest_records = parse_manifest(input_dir)
    manifest_entries = [
        Entry(index, name, attrs, 0, 0)
        for index, (name, attrs) in enumerate(manifest_records)
    ]
    file_names = resource_file_names(manifest_entries)
    expected_names = {MANIFEST_NAME.casefold(), *(name.casefold() for name in file_names)}
    children = list(input_dir.iterdir())
    directories = [path.name for path in children if path.is_dir()]
    if directories:
        raise ArchiveError(f"输入目录不能含子目录，发现：{directories[0]}")
    for path in children:
        if not path.is_file():
            raise ArchiveError(f"发现无法识别的非文件输入：{path.name}")
        if path.name.casefold() not in expected_names:
            raise ArchiveError(f"输入目录中存在最小索引未记录的文件：{path.name}")
    if len(children) != len(file_names) + 1:
        raise ArchiveError("输入目录缺少最小索引中记录的资源文件")

    next_offset = RECORD_SIZE * (len(manifest_records) + 1)
    entries: list[InputEntry] = []
    for index, ((name, attrs), file_name) in enumerate(zip(manifest_records, file_names)):
        path = input_dir / file_name
        if not path.is_file():
            raise ArchiveError(f"缺少资源文件：{file_name}")
        size = path.stat().st_size
        if size > UINT32_MAX:
            raise ArchiveError(f"资源正文超出 DAT 格式的长度范围：{path.name}")
        offset = align_up(next_offset)
        if offset > UINT32_MAX or offset + size > UINT32_MAX:
            raise ArchiveError("封包超出 DAT 格式的 32 位偏移范围")
        entries.append(InputEntry(index, name, attrs, path, size, offset))
        next_offset = offset + size
    return entries


def build_record(entry: InputEntry) -> bytes:
    name_field = entry.name + bytes(NAME_SIZE - len(entry.name))
    return (
        name_field
        + entry.attrs
        + struct.pack("<II", entry.offset, entry.size)
        + bytes(12)
    )


def write_padding(stream, target_offset: int) -> None:
    current = stream.tell()
    if current > target_offset:
        raise ArchiveError(
            f"内部布局错误：当前位置 0x{current:X} 超过目标位置 0x{target_offset:X}"
        )
    stream.write(bytes(target_offset - current))


def pack_archive(input_dir: Path, output_path: Path) -> None:
    entries = collect_input_entries(input_dir)
    if output_path.exists():
        raise ArchiveError(f"输出文件已经存在：{output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    created = False
    try:
        with output_path.open("xb") as destination:
            created = True
            destination.write(canonical_header(len(entries)))
            for entry in entries:
                destination.write(build_record(entry))
            for entry in entries:
                write_padding(destination, entry.offset)
                with entry.path.open("rb") as source:
                    shutil.copyfileobj(source, destination, COPY_CHUNK)
            write_padding(destination, align_up(destination.tell()))
    except (OSError, ArchiveError):
        if created:
            try:
                output_path.unlink()
            except OSError:
                pass
        raise

    # 报告成功前，用与解包相同的严格规则重新校验生成结果。
    archive = parse_archive(output_path)
    payload_size = sum(entry.size for entry in entries)
    print(
        f"已回包 {len(archive.entries)} 个条目（资源正文共 {payload_size} 字节）"
        f"到 {output_path}"
    )


def check_archive(path: Path) -> None:
    archive = parse_archive(path)
    payload_size = sum(entry.size for entry in archive.entries)
    link_count = sum(entry.attrs[0] == 1 for entry in archive.entries)
    print(
        f"校验通过：{path} | 条目数={len(archive.entries)} | "
        f"正文大小={payload_size} | 封包大小={archive.size} | 外部链接={link_count}"
    )


def make_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="无损解包和回包 MC 游戏的 CLS_FILELINK DAT 封包。"
    )
    commands = parser.add_subparsers(
        dest="command", required=True, title="命令", parser_class=ChineseArgumentParser
    )

    unpack_parser = commands.add_parser("unpack", help="提取未经转换的资源正文")
    unpack_parser.add_argument("archive", type=Path, help="输入 DAT 封包")
    unpack_parser.add_argument("output_dir", type=Path, help="输出目录，必须为空")

    pack_parser = commands.add_parser("pack", help="从解包目录重建 DAT 封包")
    pack_parser.add_argument("input_dir", type=Path, help="解包所得目录")
    pack_parser.add_argument("archive", type=Path, help="输出 DAT 封包")

    check_parser = commands.add_parser("check", help="校验封包是否符合无损重建条件")
    check_parser.add_argument("archive", type=Path, help="待校验的 DAT 封包")
    return parser


def main() -> int:
    args = make_parser().parse_args()
    try:
        if args.command == "unpack":
            unpack_archive(args.archive, args.output_dir)
        elif args.command == "pack":
            pack_archive(args.input_dir, args.archive)
        else:
            check_archive(args.archive)
    except ArchiveError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
