from __future__ import annotations

import argparse
import base64
import json
import struct
from dataclasses import dataclass
from pathlib import Path


JSON_ENCODING = "utf-8"
STRING_COLUMN_MARKER = 0xFF


class ObjParseError(ValueError):
    pass


@dataclass(frozen=True)
class ObjLayout:
    column_count: int
    cell_count: int
    record_count: int
    string_data_offset: int
    column_types_offset: int
    column_types: bytes


def parse_layout(data: bytes) -> ObjLayout:
    if len(data) < 10:
        raise ObjParseError("文件长度不足 10 字节，无法读取 OBJ 尾部元数据")

    column_count, cell_count = struct.unpack_from("<II", data, len(data) - 8)
    if column_count == 0:
        raise ObjParseError("OBJ 的每条记录字段数为 0")
    if cell_count % column_count != 0:
        raise ObjParseError(
            f"总字段数 {cell_count} 不能被每条记录字段数 {column_count} 整除"
        )

    record_data_size = cell_count * 4
    column_types_offset = len(data) - 8 - column_count
    if record_data_size > column_types_offset:
        raise ObjParseError("字段区越过了 OBJ 尾部类型表")

    return ObjLayout(
        column_count=column_count,
        cell_count=cell_count,
        record_count=cell_count // column_count,
        string_data_offset=record_data_size,
        column_types_offset=column_types_offset,
        column_types=data[column_types_offset : column_types_offset + column_count],
    )


def decode_wide_string(data: bytes, offset: int, limit: int) -> str:
    if offset < 0 or offset >= limit:
        raise ObjParseError(f"字符串偏移 {offset} 不在字符串区内")

    end = offset
    while end + 1 < limit:
        if data[end : end + 2] == b"\x00\x00":
            raw = data[offset:end]
            try:
                return raw.decode("utf-16le")
            except UnicodeDecodeError as exc:
                raise ObjParseError(f"字符串偏移 {offset} 的 UTF-16LE 数据无效") from exc
        end += 2
    raise ObjParseError(f"字符串偏移 {offset} 缺少 UTF-16LE 终止符")


def parse_obj(data: bytes) -> dict[str, object]:
    layout = parse_layout(data)
    decoded = bytes(value ^ 0xFF for value in data[: layout.column_types_offset])
    values = struct.unpack(
        f"<{layout.cell_count}I", decoded[: layout.string_data_offset]
    )
    records: list[list[int | str]] = []

    for record_index in range(layout.record_count):
        start = record_index * layout.column_count
        record: list[int | str] = []
        for column_index, value in enumerate(values[start : start + layout.column_count]):
            if layout.column_types[column_index] == STRING_COLUMN_MARKER:
                record.append(
                    decode_wide_string(
                        decoded,
                        layout.string_data_offset + value,
                        len(decoded),
                    )
                )
            else:
                record.append(value)
        records.append(record)

    return {
        "column_count": layout.column_count,
        "cell_count": layout.cell_count,
        "record_count": layout.record_count,
        "column_types": list(layout.column_types),
        "records": records,
    }


def _write_obj_json(data: dict[str, object], fp) -> None:
    records = data.pop("records")
    column_types = data.pop("column_types")
    header = json.dumps(data, ensure_ascii=False, indent=2)
    # 去掉末尾的 \n}\n
    header = header.rstrip()
    if header.endswith("}"):
        header = header[:-1].rstrip()
    fp.write(header)
    # column_types 横排
    types_line = json.dumps(column_types, ensure_ascii=False)
    fp.write(f',\n  "column_types": {types_line}')
    fp.write(',\n  "records": [\n')
    total = len(records)
    for idx, record in enumerate(records):
        line = json.dumps(record, ensure_ascii=False)
        comma = "," if idx < total - 1 else ""
        fp.write(f"    {line}{comma}\n")
    fp.write("  ]\n}\n")


def _encode_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_base64(text: str) -> bytes:
    return base64.b64decode(text)


def build_obj(parsed: dict[str, object]) -> bytes:
    column_count = parsed["column_count"]
    cell_count = parsed["cell_count"]
    column_types = bytes(parsed["column_types"])
    records = parsed["records"]

    if len(column_types) != column_count:
        raise ObjParseError("JSON 中 column_types 数量与 column_count 不匹配")
    if len(records) != cell_count // column_count:
        raise ObjParseError("JSON 中记录数与 cell_count / column_count 不匹配")

    # ---- 从 records 自动重建字符串表 ----
    string_table_raw = parsed.get("_string_table")
    blob = parsed.get("_string_blob")
    if string_table_raw is not None and isinstance(string_table_raw, list):
        string_tail_raw = parsed.get("_string_tail")
        tail = bytes.fromhex(string_tail_raw) if isinstance(string_tail_raw, str) else b""
        string_data = _build_string_area(string_table_raw, tail)
    elif blob is not None and isinstance(blob, str):
        string_data = _decode_base64(blob)
    else:
        string_data = _build_string_area_from_records(records, column_types)

    # ---- 构建记录数据区 ----
    record_data = bytearray(cell_count * 4)
    for rec_idx, record in enumerate(records):
        for col_idx, value in enumerate(record):
            cell_offset = (rec_idx * column_count + col_idx) * 4
            if column_types[col_idx] == STRING_COLUMN_MARKER:
                if not isinstance(value, str):
                    raise ObjParseError(f"字符串列 {col_idx} 的值不是字符串: {value!r}")
                offset = _find_string_offset(string_data, value)
                if offset < 0:
                    raise ObjParseError(f"字符串表中找不到: {value!r}")
                struct.pack_into("<I", record_data, cell_offset, offset)
            else:
                struct.pack_into("<I", record_data, cell_offset, int(value))

    # ---- 合并记录区 + 字符串区，反码写入 ----
    plain = record_data + string_data
    xored = bytes(b ^ 0xFF for b in plain)

    return xored + column_types + struct.pack("<II", column_count, cell_count)


def _find_string_offset(string_data: bytes, text: str) -> int:
    encoded = text.encode("utf-16le") + b"\x00\x00"
    pos = 0
    while pos + len(encoded) <= len(string_data):
        end = pos
        while end + 1 < len(string_data):
            if string_data[end : end + 2] == b"\x00\x00":
                break
            end += 2
        if string_data[pos : end + 2] == encoded:
            return pos
        pos = end + 2
    return -1


def _build_string_area(table: list[str], tail: bytes) -> bytes:
    buf = bytearray()
    for text in table:
        buf.extend(text.encode("utf-16le"))
        buf.extend(b"\x00\x00")
    buf.extend(tail)
    return bytes(buf)


def _build_string_area_from_records(records, column_types) -> bytes:
    seen: dict[str, int] = {}
    buf = bytearray()
    for record in records:
        for col_idx, value in enumerate(record):
            if column_types[col_idx] != STRING_COLUMN_MARKER:
                continue
            if not isinstance(value, str):
                raise ObjParseError(f"字符串列 {col_idx} 的值不是字符串: {value!r}")
            if value in seen:
                continue
            seen[value] = len(buf)
            buf.extend(value.encode("utf-16le"))
            buf.extend(b"\x00\x00")

    # OBJ 编译器固定追加四个 UTF-16LE 空格，并零填充到 8 字节边界。
    buf.extend("    ".encode("utf-16le"))
    while len(buf) % 8:
        buf.append(0)
    return bytes(buf)


def inject_obj_file(input_path: Path, output_path: Path) -> None:
    parsed = json.loads(input_path.read_text(encoding=JSON_ENCODING))
    required = {"column_count", "cell_count", "record_count", "column_types", "records"}
    missing = required - set(parsed.keys())
    if missing:
        raise ObjParseError(f"JSON 缺少必要字段: {missing}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(build_obj(parsed))


def dump_obj_file(input_path: Path, output_path: Path) -> None:
    parsed = parse_obj(input_path.read_bytes())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding=JSON_ENCODING, newline="\n") as fp:
        _write_obj_json(parsed, fp)


def iter_obj_files(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    iterator = input_path.rglob("*.obj") if recursive else input_path.glob("*.obj")
    return sorted(path for path in iterator if path.is_file())


def output_path_for(input_path: Path, input_root: Path, output_root: Path) -> Path:
    if input_root.is_file():
        return output_root if output_root.suffix else output_root / f"{input_path.name}.json"
    return output_root / input_path.relative_to(input_root).with_name(f"{input_path.name}.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Aoi OBJ 数据文件与 JSON 互转")
    sub = parser.add_subparsers(dest="command", required=True)

    dump_parser = sub.add_parser("dump", help="OBJ → JSON")
    dump_parser.add_argument("input", type=Path, help="OBJ 文件或目录")
    dump_parser.add_argument("output", type=Path, help="JSON 文件或输出目录")
    dump_parser.add_argument("--recursive", action="store_true", help="递归处理子目录")

    inject_parser = sub.add_parser("inject", help="JSON → OBJ")
    inject_parser.add_argument("input", type=Path, help="JSON 文件或目录")
    inject_parser.add_argument("output", type=Path, help="OBJ 文件或输出目录")
    inject_parser.add_argument("--recursive", action="store_true", help="递归处理子目录")

    args = parser.parse_args()

    if args.command == "dump":
        return _run_dump(args)
    return _run_inject(args)


def _run_dump(args) -> int:
    input_path = args.input.resolve()
    if not input_path.exists():
        raise SystemExit(f"输入路径不存在: {input_path}")
    if input_path.is_file() and input_path.suffix.lower() != ".obj":
        raise SystemExit("输入文件必须是 .obj")

    files = iter_obj_files(input_path, args.recursive)
    if not files:
        raise SystemExit("未找到 OBJ 文件")

    output_root = args.output.resolve()
    if len(files) > 1 and output_root.suffix:
        raise SystemExit("批量导出时输出路径必须是目录")

    for file_path in files:
        output_path = output_path_for(file_path, input_path, output_root)
        try:
            dump_obj_file(file_path, output_path)
        except ObjParseError as exc:
            raise SystemExit(f"解析失败: {file_path}: {exc}") from exc
        print(f"已导出: {file_path} -> {output_path}")
    return 0


def _iter_json_files(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    iterator = input_path.rglob("*.json") if recursive else input_path.glob("*.json")
    return sorted(path for path in iterator if path.is_file())


def _run_inject(args) -> int:
    input_path = args.input.resolve()
    if not input_path.exists():
        raise SystemExit(f"输入路径不存在: {input_path}")
    if input_path.is_file() and input_path.suffix.lower() != ".json":
        raise SystemExit("输入文件必须是 .json")

    files = _iter_json_files(input_path, args.recursive)
    if not files:
        raise SystemExit("未找到 JSON 文件")

    output_root = args.output.resolve()
    if len(files) > 1 and output_root.suffix:
        raise SystemExit("批量注入时输出路径必须是目录")

    for file_path in files:
        if file_path.suffix.lower() != ".json":
            continue
        if input_path.is_file():
            output_path = output_root
        else:
            name = file_path.name
            if name.endswith(".obj.json"):
                base = name[:-10] + ".obj"
            else:
                base = name[:-5] + ".obj"
            output_path = output_root / base
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            inject_obj_file(file_path, output_path)
        except ObjParseError as exc:
            raise SystemExit(f"注入失败: {file_path}: {exc}") from exc
        print(f"已注入: {file_path} -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
