#!/usr/bin/env python3
"""在 MBT0/SAM 汇编文本与翻译 JSON 之间转换。

默认用法：

    python extract_messages.py MC_ADD_unpacked/DID_MES_00_DAT.asm.txt

导回用法：

    python extract_messages.py MC_ADD_unpacked/DID_MES_00_DAT.asm.txt \
        --import-json MC_ADD_unpacked/DID_MES_00_DAT.json

输出文件默认为输入文件同目录下的同名 ``.json``。MBT0 每条记录至少有
``message`` 字段；能识别出说话人的对白额外包含 ``name`` 字段。SAM 的每个
可翻译字符串对应一条只含 ``message`` 的记录。导回始终以源 asm 为模板并按
JSON 顺序回填，默认生成 ``*.imported.asm.txt``，未进入 JSON 的指令保持原样。
"""

from __future__ import annotations

import argparse
import codecs
import json
import re
from pathlib import Path
from typing import Iterator


TEXT_LINE_RE = re.compile(
    r'^(?P<prefix>TEXT\s+")(?P<value>.*)(?P<suffix>"(?:\s+\.zero\s+\S+)?\s*)$'
)
SAM_TEXT_LINE_RE = re.compile(
    r'^(?P<prefix>(?:NAME|DESCRIPTION(?:_[123])?|FORMULA_[123])\s+")'
    r'(?P<value>.*)(?P<suffix>"\s*)$'
)
VIDEO_TAG_RE = re.compile(r'^\[[^\]]*\]')
CONTROL_ONLY_RE = re.compile(r'^(?:\s*\{\{[0-9A-Fa-f:]+\}\})+\s*$')
RUNTIME_TOKEN_RE = re.compile(
    r'＃[^＃\r\n]+＃|\{\{[0-9A-Fa-f:]+\}\}|\\n|\\\\'
)


def detect_asm_format(text: str) -> str:
    """读取汇编头部格式，避免把指令行误当成可翻译文本。"""

    formats = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(".format "):
            formats.append(line[len(".format ") :].strip().upper())
    if len(formats) != 1 or formats[0] not in {"MBT0", "SAM"}:
        raise ValueError("汇编文本必须且只能声明一次 .format MBT0 或 .format SAM")
    return formats[0]


def iter_text_values(path: Path, encoding: str) -> Iterator[tuple[int, str]]:
    """逐行读取 ``TEXT "..."`` 的字符串值。"""

    with path.open("r", encoding=encoding, newline=None) as stream:
        for line_number, raw_line in enumerate(stream, 1):
            line = raw_line.rstrip("\r\n")
            if not line.startswith("TEXT "):
                continue
            match = TEXT_LINE_RE.fullmatch(line)
            if match is None:
                raise ValueError(f"第 {line_number} 行的 TEXT 格式无法解析：{line}")
            yield line_number, match.group("value")


def _looks_like_name(value: str) -> bool:
    """避免把叙述句中的日文引号误识别成说话人。"""

    value = value.strip()
    if not value or any(char.isspace() for char in value):
        return False
    return not any(char in value for char in "、。！？：:；;，,（）()……")


def split_speaker(value: str) -> tuple[str | None, str]:
    """拆分说话人和消息正文。

    支持 ``角色「对白」``、``角色『对白』`` 以及思考文本常见的
    ``角色：（内容）`` 格式。无法可靠识别说话人时返回 ``(None, value)``。
    """

    value = VIDEO_TAG_RE.sub("", value, count=1)

    for opener, closer in (("「", "」"), ("『", "』")):
        opener_index = value.find(opener)
        if opener_index <= 0 or not value.endswith(closer):
            continue
        name = value[:opener_index].strip()
        if _looks_like_name(name):
            return name, value[opener_index + 1 : -1]

    for separator in ("：", ":"):
        separator_index = value.find(separator)
        if separator_index <= 0:
            continue
        name = value[:separator_index].strip()
        message = value[separator_index + 1 :]
        if message.strip() and _looks_like_name(name):
            return name, message

    return None, value


def is_extractable_text(value: str) -> bool:
    """判断 TEXT 内容是否是需要进入 JSON 的可读文本。"""

    return (
        bool(value.strip())
        and not value.lstrip().startswith("#")
        and not CONTROL_ONLY_RE.fullmatch(value)
    )


def clean_message(value: str) -> str:
    """去掉消息两端空白（包括全角空格），保留正文内部空格。"""

    return value.strip()


def extract_mbt0_messages(path: Path, encoding: str) -> list[dict[str, str]]:
    """提取 MBT0 文件中的可读消息，保持源文件顺序。"""

    messages: list[dict[str, str]] = []
    for _line_number, value in iter_text_values(path, encoding):
        if not is_extractable_text(value):
            continue

        name, message = split_speaker(value)
        # 正文两端空格可能是游戏中的版式字符，提取与导回都必须原样保留。
        if not message.strip():
            continue

        record: dict[str, str] = {"message": message}
        if name is not None:
            record = {"name": clean_message(name), "message": message}
        messages.append(record)
    return messages


def extract_sam_messages(text: str) -> list[dict[str, str]]:
    """提取 SAM 的语义字符串字段，不输出任何结构或数值指令。"""

    messages: list[dict[str, str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.rstrip("\r\n")
        match = SAM_TEXT_LINE_RE.fullmatch(line)
        if match is not None:
            messages.append({"message": match.group("value")})
            continue
        mnemonic = line.split(maxsplit=1)[0] if line else ""
        if mnemonic in {
            "NAME",
            "DESCRIPTION",
            "DESCRIPTION_1",
            "DESCRIPTION_2",
            "DESCRIPTION_3",
            "FORMULA_1",
            "FORMULA_2",
            "FORMULA_3",
        }:
            raise ValueError(f"第 {line_number} 行的 SAM 字符串格式无法解析：{line}")
    return messages


def extract_messages(path: Path, encoding: str = "utf-8-sig") -> list[dict[str, str]]:
    """按汇编格式提取翻译记录，保持源文件顺序。"""

    text = path.read_text(encoding=encoding)
    format_name = detect_asm_format(text)
    if format_name == "MBT0":
        return extract_mbt0_messages(path, encoding)
    return extract_sam_messages(text)


def default_output_path(input_path: Path) -> Path:
    """将 ``foo.asm.txt`` 映射为 ``foo.json``。"""

    if input_path.name.endswith(".asm.txt"):
        return input_path.with_name(input_path.name[: -len(".asm.txt")] + ".json")
    return input_path.with_suffix(".json")


def default_import_output_path(input_path: Path) -> Path:
    """为反向导回模式生成不覆盖源文件的默认路径。"""

    if input_path.name.endswith(".asm.txt"):
        return input_path.with_name(input_path.name[: -len(".asm.txt")] + ".imported.asm.txt")
    return input_path.with_name(input_path.name + ".imported.asm.txt")


def load_records(path: Path, *, strip_text: bool = True) -> list[dict[str, str]]:
    """读取并验证 JSON 消息数组。"""

    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON 文件 {path}：{exc}") from exc
    if not isinstance(value, list):
        raise ValueError("JSON 顶层必须是数组")

    records: list[dict[str, str]] = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict) or set(item) - {"name", "message"} or "message" not in item:
            raise ValueError(f"JSON 第 {index} 条必须只包含 message，以及可选的 name")
        message = item["message"]
        name = item.get("name")
        if not isinstance(message, str) or (name is not None and not isinstance(name, str)):
            raise ValueError(f"JSON 第 {index} 条的 name/message 必须是字符串")
        if strip_text:
            message = clean_message(message)
        if not message:
            raise ValueError(f"JSON 第 {index} 条的 message 不能为空")
        record = {"message": message}
        if name is not None:
            if strip_text:
                name = clean_message(name)
            if not name:
                raise ValueError(f"JSON 第 {index} 条的 name 不能为空；无说话人时请省略 name")
            record = {"name": name, "message": message}
        records.append(record)
    return records


def render_imported_text(original: str, record: dict[str, str]) -> str:
    """用一条 JSON 记录替换源 TEXT 的正文，同时保留源对白格式。"""

    tag_match = VIDEO_TAG_RE.match(original)
    tag = tag_match.group(0) if tag_match else ""
    body = original[len(tag) :]
    message = record["message"]
    name = record.get("name")

    for opener, closer in (("「", "」"), ("『", "』")):
        opener_index = body.find(opener)
        if opener_index > 0 and body.endswith(closer):
            original_name = body[:opener_index].strip()
            if _looks_like_name(original_name):
                return f"{tag}{name or original_name}{opener}{message}{closer}"

    for separator in ("：", ":"):
        separator_index = body.find(separator)
        if separator_index > 0:
            original_name = body[:separator_index].strip()
            if _looks_like_name(original_name):
                return f"{tag}{name or original_name}{separator}{message}"

    if name is not None:
        return f"{tag}{name}「{message}」"
    return f"{tag}{message}"


def escape_asm_text(value: str) -> str:
    """转义汇编 TEXT 中不能直接出现的双引号和控制字符。"""

    result: list[str] = []
    position = 0
    for match in RUNTIME_TOKEN_RE.finditer(value):
        result.extend(escape_plain_asm_text(value[position : match.start()]))
        result.append(match.group(0))
        position = match.end()
    result.extend(escape_plain_asm_text(value[position:]))
    return "".join(result)


def escape_plain_asm_text(value: str) -> list[str]:
    """转义不含既有运行时标记的一段普通译文。"""

    result: list[str] = []
    for char in value:
        codepoint = ord(char)
        if char == '"':
            result.append("{{22}}")
        elif char == "\\":
            result.append("\\\\")
        elif codepoint < 0x20 or codepoint == 0x7F:
            result.append(f"{{{{{codepoint:02X}}}}}")
        else:
            result.append(char)
    return result


def runtime_tokens(value: str) -> tuple[str, ...]:
    """提取翻译时禁止改动的公式、换行和原始字节标记。"""

    return tuple(match.group(0) for match in RUNTIME_TOKEN_RE.finditer(value))


def validate_runtime_tokens(original: str, translated: str, index: int) -> None:
    """拒绝丢失、翻译或调换运行时标记的 JSON。"""

    before = runtime_tokens(original)
    after = runtime_tokens(translated)
    if before != after:
        raise ValueError(
            f"JSON 第 {index} 条改变了运行时标记；必须保持数量、内容和顺序完全一致"
        )


def replace_dat_encoding(lines: list[str], dat_encoding: str | None) -> list[str]:
    """按需更新汇编头部的 DAT 字符串编码声明。"""

    if dat_encoding is None:
        return lines
    try:
        dat_encoding = codecs.lookup(dat_encoding).name
    except LookupError as exc:
        raise ValueError(f"未知 DAT 字符串编码：{dat_encoding}") from exc
    matches = [index for index, line in enumerate(lines) if line.strip().startswith(".encoding ")]
    if len(matches) != 1:
        raise ValueError("汇编文本必须且只能包含一条 .encoding 指令")
    index = matches[0]
    raw_line = lines[index]
    line = raw_line.rstrip("\r\n")
    line_ending = raw_line[len(line) :]
    lines[index] = f'.encoding "{dat_encoding}"{line_ending}'
    return lines


def import_mbt0_messages(
    source_path: Path,
    json_path: Path,
    output_path: Path,
    encoding: str = "utf-8-sig",
    dat_encoding: str | None = None,
) -> int:
    """将 JSON 按顺序导回源 asm，返回替换条目数。"""

    # 导入必须忠实采用 JSON 文本，不能把有语义的全角首尾空格当成排版空白删除。
    records = load_records(json_path, strip_text=False)
    source_lines = source_path.read_text(encoding=encoding).splitlines(keepends=True)
    record_index = 0
    output_lines: list[str] = []

    for line_number, raw_line in enumerate(source_lines, 1):
        line = raw_line.rstrip("\r\n")
        line_ending = raw_line[len(line) :]
        if not line.startswith("TEXT "):
            output_lines.append(raw_line)
            continue
        match = TEXT_LINE_RE.fullmatch(line)
        if match is None:
            raise ValueError(f"第 {line_number} 行的 TEXT 格式无法解析：{line}")
        original_value = match.group("value")
        if not is_extractable_text(original_value):
            output_lines.append(raw_line)
            continue
        if record_index >= len(records):
            raise ValueError("JSON 条目数量少于源文件中的可提取文本数量")

        imported_value = escape_asm_text(render_imported_text(original_value, records[record_index]))
        output_lines.append(match.group("prefix") + imported_value + match.group("suffix") + line_ending)
        record_index += 1

    if record_index != len(records):
        raise ValueError("JSON 条目数量多于源文件中的可提取文本数量")

    output_lines = replace_dat_encoding(output_lines, dat_encoding)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(output_lines), encoding="utf-8", newline="")
    return record_index


def import_sam_messages(
    source_path: Path,
    json_path: Path,
    output_path: Path,
    encoding: str = "utf-8-sig",
    dat_encoding: str | None = None,
) -> int:
    """将 JSON 字符串导回 SAM，同时原样保留全部非字符串指令。"""

    records = load_records(json_path, strip_text=False)
    for index, record in enumerate(records, 1):
        if "name" in record:
            raise ValueError(f"SAM JSON 第 {index} 条只能包含 message")

    source_lines = source_path.read_text(encoding=encoding).splitlines(keepends=True)
    record_index = 0
    output_lines: list[str] = []

    for line_number, raw_line in enumerate(source_lines, 1):
        line = raw_line.rstrip("\r\n")
        line_ending = raw_line[len(line) :]
        match = SAM_TEXT_LINE_RE.fullmatch(line)
        if match is None:
            mnemonic = line.split(maxsplit=1)[0] if line else ""
            if mnemonic in {
                "NAME",
                "DESCRIPTION",
                "DESCRIPTION_1",
                "DESCRIPTION_2",
                "DESCRIPTION_3",
                "FORMULA_1",
                "FORMULA_2",
                "FORMULA_3",
            }:
                raise ValueError(f"第 {line_number} 行的 SAM 字符串格式无法解析：{line}")
            output_lines.append(raw_line)
            continue
        if record_index >= len(records):
            raise ValueError("JSON 条目数量少于源文件中的 SAM 字符串数量")

        original = match.group("value")
        translated = records[record_index]["message"]
        validate_runtime_tokens(original, translated, record_index + 1)
        imported = original if translated == original else escape_asm_text(translated)
        output_lines.append(match.group("prefix") + imported + match.group("suffix") + line_ending)
        record_index += 1

    if record_index != len(records):
        raise ValueError("JSON 条目数量多于源文件中的 SAM 字符串数量")

    output_lines = replace_dat_encoding(output_lines, dat_encoding)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(output_lines), encoding="utf-8", newline="")
    return record_index


def import_messages(
    source_path: Path,
    json_path: Path,
    output_path: Path,
    encoding: str = "utf-8-sig",
    dat_encoding: str | None = None,
) -> int:
    """按汇编格式选择对应导回逻辑。"""

    text = source_path.read_text(encoding=encoding)
    format_name = detect_asm_format(text)
    if format_name == "MBT0":
        return import_mbt0_messages(source_path, json_path, output_path, encoding, dat_encoding)
    return import_sam_messages(source_path, json_path, output_path, encoding, dat_encoding)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在 asm.txt 与 name/message JSON 之间转换",
        add_help=False,
    )
    parser._positionals.title = "位置参数"
    parser._optionals.title = "选项"
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出")
    parser.add_argument("input", type=Path, help="输入的 .asm.txt 文件")
    parser.add_argument("-o", "--output", type=Path, help="指定输出文件路径")
    parser.add_argument(
        "--import-json",
        type=Path,
        metavar="JSON",
        help="将 JSON 按原顺序导回 input asm；默认输出为 *.imported.asm.txt",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="输入文本编码，默认 utf-8-sig；如原始文件为日文编码可指定 cp932",
    )
    parser.add_argument(
        "--dat-encoding",
        help="导回时更新 asm 的 .encoding，例如简体中文使用 gbk；省略则原样保留",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_path: Path = args.input
    output_path: Path = args.output or default_output_path(input_path)

    if args.import_json:
        output_path = args.output or default_import_output_path(input_path)
        count = import_messages(
            input_path,
            args.import_json,
            output_path,
            args.encoding,
            args.dat_encoding,
        )
        print(f"已导回 {count} 条消息：{output_path}")
        return 0

    records = extract_messages(input_path, args.encoding)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"已提取 {len(records)} 条消息：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
