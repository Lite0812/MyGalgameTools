#!/usr/bin/env python3
"""Inspect, unpack, edit, and repack Director text-cast CXT files.

Each unpacked HTML member contains an editable UTF-8 text view. A matching
``.xmed.json`` sidecar contains the semantic Paige/XMED fields; no original
XMED payload is retained. An unchanged unpack/pack cycle reproduces the source
byte-for-byte, while edited text is encoded as CP932 and serialized anew.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import struct
import sys
from typing import Any, Iterable
import xml.etree.ElementTree as ET

import paige_xmed


FORMAT_VERSION = "director-cxt-text-v5"
INLINE_XML_FORMAT_VERSION = "director-cxt-text-v4"
INLINE_JSON_FORMAT_VERSION = "director-cxt-text-v3"
EXTERNAL_FIELDS_FORMAT_VERSION = "director-cxt-text-v2"
LEGACY_FORMAT_VERSION = "director-cxt-text-v1"
MEMBER_FORMAT_VERSION = "director-xmed-html-v1"
SEMANTIC_FORMAT_VERSION = "director-xmed-fields-v1"
TEXT_ENCODING = "cp932"
HEX_BYTES = frozenset(b"0123456789ABCDEF")
JSON_INLINE_ARRAY_LIMIT = 160

XML_LIST_ITEM_NAMES = {
    "records": "record",
    "text_blocks": "text_block",
    "style_runs": "style_run",
    "paragraph_runs": "paragraph_run",
    "styles": "style",
    "paragraphs": "paragraph",
    "fonts": "font",
    "visible_shape": "rectangle",
    "page_shape": "rectangle",
    "exclusion_shape": "rectangle",
    "selections": "selection",
    "hyperlinks": "hyperlink",
    "hyperlink_targets": "hyperlink_target",
    "subreferences": "subreference",
    "style_values": "style_value",
    "tab_stops": "tab_stop",
    "table_columns": "table_column",
    "future": "future_value",
    "version4_table_extension": "extension_value",
}


class CxtError(Exception):
    pass


def format_semantic_json(value: Any, level: int = 0) -> str:
    """Pretty-print objects while keeping short scalar arrays on one line."""

    padding = "  " * level
    child_padding = "  " * (level + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = []
        for key, item in value.items():
            rendered = format_semantic_json(item, level + 1)
            encoded_key = json.dumps(key, ensure_ascii=False)
            items.append(f"{child_padding}{encoded_key}: {rendered}")
        return "{\n" + ",\n".join(items) + f"\n{padding}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(item is None or isinstance(item, (bool, int, float, str)) for item in value):
            inline = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
            if len(padding) + len(inline) <= JSON_INLINE_ARRAY_LIMIT:
                return inline
        items = [
            child_padding + format_semantic_json(item, level + 1)
            for item in value
        ]
        return "[\n" + ",\n".join(items) + f"\n{padding}]"
    return json.dumps(value, ensure_ascii=False)


def xml_list_item_name(field_name: str) -> str:
    if field_name in XML_LIST_ITEM_NAMES:
        return XML_LIST_ITEM_NAMES[field_name]
    if field_name.endswith("ies"):
        return field_name[:-3] + "y"
    if field_name.endswith("s") and not field_name.endswith("ss"):
        return field_name[:-1]
    return field_name + "_value"


def semantic_to_xml_element(name: str, value: Any) -> ET.Element:
    element = ET.Element(name)
    if isinstance(value, dict):
        for child_name, child_value in value.items():
            element.append(semantic_to_xml_element(child_name, child_value))
    elif isinstance(value, list):
        element.set("type", "array")
        item_name = xml_list_item_name(name)
        for item in value:
            element.append(semantic_to_xml_element(item_name, item))
    elif value is None:
        element.set("type", "null")
    elif isinstance(value, bool):
        element.set("type", "boolean")
        element.text = "true" if value else "false"
    elif isinstance(value, int):
        element.set("type", "integer")
        element.text = str(value)
    elif isinstance(value, float):
        element.set("type", "number")
        element.text = repr(value)
    elif isinstance(value, str):
        element.set("type", "string")
        element.text = value
    else:
        raise CxtError(f"Cannot represent {name!r} value as semantic XML")
    return element


def semantic_to_xml(value: dict[str, Any]) -> str:
    root = semantic_to_xml_element("xmed_fields", value)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def semantic_from_xml_element(element: ET.Element) -> Any:
    value_type = element.get("type")
    text_value = element.text or ""
    if value_type == "array":
        return [semantic_from_xml_element(child) for child in element]
    if value_type == "null":
        if len(element):
            raise CxtError(f"Null XML field {element.tag!r} cannot have children")
        return None
    if value_type == "boolean":
        if text_value not in ("true", "false"):
            raise CxtError(f"Invalid boolean XML field {element.tag!r}")
        return text_value == "true"
    if value_type == "integer":
        try:
            return int(text_value, 10)
        except ValueError as exc:
            raise CxtError(f"Invalid integer XML field {element.tag!r}") from exc
    if value_type == "number":
        try:
            return float(text_value)
        except ValueError as exc:
            raise CxtError(f"Invalid numeric XML field {element.tag!r}") from exc
    if value_type == "string":
        if len(element):
            raise CxtError(f"String XML field {element.tag!r} cannot have children")
        return text_value
    if value_type is not None:
        raise CxtError(f"Unsupported XML type {value_type!r} on {element.tag!r}")
    if not len(element):
        return {}
    result: dict[str, Any] = {}
    for child in element:
        if child.tag in result:
            raise CxtError(f"Duplicate XML field {child.tag!r} in {element.tag!r}")
        result[child.tag] = semantic_from_xml_element(child)
    return result


def semantic_from_xml(source: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise CxtError(f"Invalid embedded semantic XML: {exc}") from exc
    if root.tag != "xmed_fields":
        raise CxtError(f"Expected <xmed_fields>, found <{root.tag}>")
    value = semantic_from_xml_element(root)
    if not isinstance(value, dict):
        raise CxtError("Semantic XML root must contain named fields")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def logical_tag(raw_tag: bytes, little_endian: bool) -> bytes:
    if little_endian and raw_tag not in (b"XFIR", b"RIFX"):
        return raw_tag[::-1]
    return raw_tag


def read_u16(data: bytes | bytearray, offset: int, endian: str) -> int:
    try:
        return struct.unpack_from(endian + "H", data, offset)[0]
    except struct.error as exc:
        raise CxtError(f"Truncated 16-bit value at 0x{offset:X}") from exc


def read_u32(data: bytes | bytearray, offset: int, endian: str) -> int:
    try:
        return struct.unpack_from(endian + "I", data, offset)[0]
    except struct.error as exc:
        raise CxtError(f"Truncated 32-bit value at 0x{offset:X}") from exc


def write_u32(data: bytearray, offset: int, value: int, endian: str) -> None:
    try:
        struct.pack_into(endian + "I", data, offset, value)
    except (struct.error, OverflowError) as exc:
        raise CxtError(f"Cannot write 32-bit value at 0x{offset:X}") from exc


def parse_cxt(data: bytes | bytearray) -> dict[str, Any]:
    if len(data) < 44:
        raise CxtError("File is too small to be a Director container")

    root_tag = bytes(data[0:4])
    if root_tag == b"XFIR":
        little_endian = True
        endian = "<"
    elif root_tag == b"RIFX":
        little_endian = False
        endian = ">"
    else:
        raise CxtError(f"Unsupported root signature {root_tag!r}")

    declared_root_size = read_u32(data, 4, endian)
    if declared_root_size + 8 != len(data):
        raise CxtError(
            f"Root size mismatch: header says {declared_root_size + 8}, "
            f"file has {len(data)} bytes"
        )

    imap_offset = 12
    if logical_tag(bytes(data[imap_offset : imap_offset + 4]), little_endian) != b"imap":
        raise CxtError("Expected imap chunk at file offset 0xC")
    imap_size = read_u32(data, imap_offset + 4, endian)
    if imap_offset + 8 + imap_size > len(data) or imap_size < 8:
        raise CxtError("Invalid imap chunk size")

    mmap_offset = read_u32(data, imap_offset + 12, endian)
    if mmap_offset + 32 > len(data):
        raise CxtError(f"Invalid mmap offset 0x{mmap_offset:X}")
    if logical_tag(bytes(data[mmap_offset : mmap_offset + 4]), little_endian) != b"mmap":
        raise CxtError(f"Expected mmap chunk at 0x{mmap_offset:X}")

    mmap_size = read_u32(data, mmap_offset + 4, endian)
    mmap_payload = mmap_offset + 8
    mmap_end = mmap_payload + mmap_size
    if mmap_end > len(data):
        raise CxtError("mmap chunk extends beyond the file")

    mmap_header_size = read_u16(data, mmap_payload, endian)
    entry_size = read_u16(data, mmap_payload + 2, endian)
    capacity = read_u32(data, mmap_payload + 4, endian)
    used = read_u32(data, mmap_payload + 8, endian)
    if mmap_header_size < 20 or entry_size < 12:
        raise CxtError("Unsupported mmap header or entry size")

    entries_offset = mmap_payload + mmap_header_size
    if entries_offset + capacity * entry_size > mmap_end:
        raise CxtError("mmap entry table exceeds the mmap chunk")

    entries: list[dict[str, Any]] = []
    xmed_entries: list[dict[str, Any]] = []
    for resource_id in range(capacity):
        entry_offset = entries_offset + resource_id * entry_size
        raw_tag = bytes(data[entry_offset : entry_offset + 4])
        size = read_u32(data, entry_offset + 4, endian)
        chunk_offset = read_u32(data, entry_offset + 8, endian)
        entry = {
            "resource_id": resource_id,
            "entry_offset": entry_offset,
            "raw_tag": raw_tag,
            "tag": logical_tag(raw_tag, little_endian),
            "size": size,
            "offset": chunk_offset,
        }
        entries.append(entry)
        if entry["tag"] != b"XMED":
            continue
        if chunk_offset + 8 + size > len(data):
            raise CxtError(f"XMED resource {resource_id} extends beyond the file")
        chunk_tag = bytes(data[chunk_offset : chunk_offset + 4])
        chunk_size = read_u32(data, chunk_offset + 4, endian)
        if chunk_tag != raw_tag or chunk_size != size:
            raise CxtError(
                f"XMED resource {resource_id} mmap/chunk header mismatch "
                f"at 0x{chunk_offset:X}"
            )
        xmed_entries.append(entry)

    if not xmed_entries:
        raise CxtError("No XMED resources found")

    xmed_entries.sort(key=lambda item: item["offset"])
    cursor = xmed_entries[0]["offset"]
    for entry in xmed_entries:
        if entry["offset"] != cursor:
            raise CxtError(
                "This tool currently requires XMED chunks to form one contiguous "
                "tail region"
            )
        cursor += 8 + entry["size"] + (entry["size"] & 1)
    if cursor != len(data):
        raise CxtError(
            "This tool currently requires the contiguous XMED region to end at EOF"
        )

    root_entries = [entry for entry in entries if entry["tag"] == root_tag]
    if not root_entries:
        root_entries = [entry for entry in entries if entry["resource_id"] == 0]

    return {
        "root_tag": root_tag,
        "little_endian": little_endian,
        "endian": endian,
        "declared_root_size": declared_root_size,
        "imap_offset": imap_offset,
        "mmap_offset": mmap_offset,
        "mmap_size": mmap_size,
        "mmap_header_size": mmap_header_size,
        "entry_size": entry_size,
        "capacity": capacity,
        "used": used,
        "entries_offset": entries_offset,
        "entries": entries,
        "root_entry_offset": root_entries[0]["entry_offset"],
        "xmed_entries": xmed_entries,
        "xmed_start": xmed_entries[0]["offset"],
    }


def is_probable_text_blob(blob: bytes) -> bool:
    if not blob or b"\r" not in blob:
        return False
    printable = sum(
        byte == 13 or 32 <= byte <= 126 or byte >= 128 for byte in blob
    )
    if printable / len(blob) < 0.95:
        return False
    try:
        decoded = blob.decode(TEXT_ENCODING)
    except UnicodeDecodeError:
        return False
    return decoded.encode(TEXT_ENCODING) == blob


def find_text_segments(payload: bytes) -> list[dict[str, int]]:
    segments: list[dict[str, int]] = []
    for comma_offset, byte in enumerate(payload):
        if byte != ord(","):
            continue
        marker_offset = comma_offset - 1
        while marker_offset >= 0 and payload[marker_offset] in HEX_BYTES:
            marker_offset -= 1
        marker_offset += 1
        if (
            marker_offset == comma_offset
            or marker_offset == 0
            or payload[marker_offset - 1] != 0
        ):
            continue
        marker = payload[marker_offset:comma_offset]
        try:
            length = int(marker, 16)
        except ValueError:
            continue
        data_offset = comma_offset + 1
        data_end = data_offset + length
        if length <= 0 or data_end > len(payload):
            continue
        if not is_probable_text_blob(payload[data_offset:data_end]):
            continue
        segments.append(
            {
                "marker_offset": marker_offset,
                "marker_length": comma_offset - marker_offset,
                "data_offset": data_offset,
                "byte_length": length,
            }
        )

    if not segments:
        raise CxtError("Could not locate CP932 text segments in an XMED payload")

    previous_end = -1
    for segment in segments:
        if segment["marker_offset"] < previous_end:
            raise CxtError("Overlapping XMED text segments detected")
        previous_end = segment["data_offset"] + segment["byte_length"]
    return segments


def original_text_bytes(payload: bytes, segments: Iterable[dict[str, int]]) -> bytes:
    return b"".join(
        payload[segment["data_offset"] : segment["data_offset"] + segment["byte_length"]]
        for segment in segments
    )


def find_total_length_fields(
    payload: bytes, segments: list[dict[str, int]]
) -> list[dict[str, int]]:
    total = sum(segment["byte_length"] for segment in segments)
    needle = f"{total:X}".encode("ascii")
    first_marker = segments[0]["marker_offset"]
    fields: list[dict[str, int]] = []
    cursor = 0
    while True:
        offset = payload.find(needle, cursor, first_marker)
        if offset < 0:
            break
        before = payload[offset - 1] if offset else None
        after_offset = offset + len(needle)
        after = payload[after_offset] if after_offset < len(payload) else None
        if before not in HEX_BYTES and after not in HEX_BYTES:
            fields.append({"offset": offset, "length": len(needle)})
        cursor = offset + 1
    if not fields:
        raise CxtError("Could not locate the serialized XMED total text length")
    return fields


class MemberHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_text = False
        self.in_metadata = False
        self.in_fields = False
        self.text_parts: list[str] = []
        self.metadata_parts: list[str] = []
        self.fields_parts: list[str] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key.lower(): value or "" for key, value in attrs}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_map = self._attrs(attrs)
        if tag.lower() == "textarea" and attr_map.get("id") == "xmed-text":
            self.in_text = True
        elif tag.lower() == "script" and attr_map.get("id") == "xmed-metadata":
            self.in_metadata = True
        elif tag.lower() == "script" and attr_map.get("id") == "xmed-fields":
            self.in_fields = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "textarea" and self.in_text:
            self.in_text = False
        elif tag.lower() == "script" and self.in_metadata:
            self.in_metadata = False
        elif tag.lower() == "script" and self.in_fields:
            self.in_fields = False

    def handle_data(self, data: str) -> None:
        if self.in_text:
            self.text_parts.append(data)
        elif self.in_metadata:
            self.metadata_parts.append(data)
        elif self.in_fields:
            self.fields_parts.append(data)


def read_member_html(path: Path) -> tuple[str, dict[str, Any] | None]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CxtError(f"Cannot read UTF-8 member HTML {path}: {exc}") from exc
    parser = MemberHtmlParser()
    parser.feed(source)
    if not parser.text_parts:
        raise CxtError(f"Missing #xmed-text textarea in {path}")
    if parser.metadata_parts and parser.fields_parts:
        raise CxtError(f"Both legacy metadata and semantic fields are present in {path}")
    embedded: dict[str, Any] | None = None
    embedded_parts = parser.fields_parts or parser.metadata_parts
    if embedded_parts:
        embedded_source = "".join(embedded_parts).strip()
        if embedded_source.startswith("<"):
            try:
                embedded = semantic_from_xml(embedded_source)
            except CxtError as exc:
                raise CxtError(f"Invalid embedded metadata in {path}: {exc}") from exc
        else:
            try:
                embedded = json.loads(embedded_source)
            except json.JSONDecodeError as exc:
                raise CxtError(f"Invalid embedded metadata in {path}: {exc}") from exc
        if embedded.get("format") not in (
            MEMBER_FORMAT_VERSION,
            SEMANTIC_FORMAT_VERSION,
        ):
            raise CxtError(f"Unsupported member HTML format in {path}")
    text = "".join(parser.text_parts)
    return text, embedded


def make_member_html(
    member_number: int,
    resource_id: int,
    member_text: str,
) -> str:
    visible_text = html.escape(member_text.replace("\r\n", "\n").replace("\r", "\n"))
    filename = f"{member_number}.htm"
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SCRIPT {member_number:03d}</title>
<style>
:root {{ color-scheme: light; font-family: "Yu Gothic UI", Meiryo, sans-serif; }}
* {{ box-sizing: border-box; }}
html, body {{ height: 100%; margin: 0; background: #f4f5f7; color: #202428; }}
body {{ display: grid; grid-template-rows: 48px minmax(0, 1fr); }}
header {{ display: flex; align-items: center; gap: 12px; padding: 8px 14px; background: #ffffff; border-bottom: 1px solid #cdd2d8; }}
h1 {{ min-width: 0; margin: 0; font-size: 15px; font-weight: 650; letter-spacing: 0; }}
.spacer {{ flex: 1; }}
#state {{ color: #616970; font-size: 12px; }}
button {{ min-height: 32px; padding: 5px 13px; border: 1px solid #276749; border-radius: 5px; background: #2f855a; color: #ffffff; font: inherit; cursor: pointer; }}
button:hover {{ background: #276749; }}
main {{ min-height: 0; padding: 10px; }}
textarea {{ width: 100%; height: 100%; resize: none; overflow: auto; padding: 14px 16px; border: 1px solid #b9c0c7; border-radius: 6px; outline: none; background: #ffffff; color: #171a1d; font: 15px/1.65 "MS Gothic", "Yu Gothic", Meiryo, monospace; letter-spacing: 0; tab-size: 4; white-space: pre; }}
textarea:focus {{ border-color: #2f855a; box-shadow: 0 0 0 2px #c6f6d5; }}
</style>
</head>
<body>
<header><h1>Member {member_number:03d} / XMED {resource_id}</h1><span class="spacer"></span><span id="state">Saved source</span><button id="save" type="button" title="Save edited HTML">Save HTML</button></header>
<main><textarea id="xmed-text" spellcheck="false" wrap="off">{visible_text}</textarea></main>
<script>
(() => {{
  const area = document.getElementById("xmed-text");
  const state = document.getElementById("state");
  const save = () => {{
    const clone = document.documentElement.cloneNode(true);
    clone.querySelector("#xmed-text").textContent = area.value;
    clone.querySelector("#state").textContent = "Saved source";
    const source = "<!doctype html>\\n" + clone.outerHTML;
    const url = URL.createObjectURL(new Blob([source], {{type: "text/html;charset=utf-8"}}));
    const link = document.createElement("a");
    link.href = url;
    link.download = {json.dumps(filename)};
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    state.textContent = "Saved source";
  }};
  area.addEventListener("input", () => state.textContent = "Modified");
  document.getElementById("save").addEventListener("click", save);
  document.addEventListener("keydown", event => {{
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {{
      event.preventDefault();
      save();
    }}
  }});
}})();
</script>
</body>
</html>
"""


def normalize_edited_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r")


def split_encoded_text(text: str, target_lengths: list[int]) -> list[bytes]:
    encoded_chars: list[bytes] = []
    for index, char in enumerate(text):
        try:
            encoded_chars.append(char.encode(TEXT_ENCODING))
        except UnicodeEncodeError as exc:
            raise CxtError(
                f"Character {char!r} at text position {index} cannot be encoded "
                f"as {TEXT_ENCODING}"
            ) from exc

    chunks: list[bytes] = []
    char_index = 0
    for target in target_lengths[:-1]:
        chunk = bytearray()
        while char_index < len(encoded_chars):
            encoded = encoded_chars[char_index]
            if chunk and len(chunk) + len(encoded) > target:
                break
            if not chunk and len(encoded) > target:
                break
            chunk.extend(encoded)
            char_index += 1
        chunks.append(bytes(chunk))
    chunks.append(b"".join(encoded_chars[char_index:]))
    return chunks


def apply_original_offset_patches(
    payload: bytes, patches: list[tuple[int, int, bytes]]
) -> bytes:
    output = bytearray(payload)
    last_start = len(payload) + 1
    for start, end, replacement in sorted(patches, reverse=True):
        if not (0 <= start <= end <= len(payload)):
            raise CxtError("Embedded XMED patch range is invalid")
        if end > last_start:
            raise CxtError("Embedded XMED patch ranges overlap")
        output[start:end] = replacement
        last_start = start
    return bytes(output)


def rebuild_payload(edited_text: str, metadata: dict[str, Any]) -> tuple[bytes, bool]:
    try:
        payload = base64.b64decode(metadata["payload_base64"], validate=True)
        segments = metadata["segments"]
        total_fields = metadata["total_length_fields"]
    except (KeyError, TypeError, ValueError) as exc:
        raise CxtError("Member HTML has incomplete or invalid XMED metadata") from exc
    if sha256(payload) != metadata.get("payload_sha256"):
        raise CxtError("Embedded XMED payload checksum mismatch")

    original = original_text_bytes(payload, segments)
    normalized = normalize_edited_text(edited_text)
    try:
        edited = normalized.encode(TEXT_ENCODING)
    except UnicodeEncodeError as exc:
        bad = normalized[exc.start : exc.end]
        raise CxtError(
            f"Text contains {bad!r}, which cannot be encoded as {TEXT_ENCODING}"
        ) from exc
    if edited == original:
        return payload, False

    target_lengths = [segment["byte_length"] for segment in segments]
    chunks = split_encoded_text(normalized, target_lengths)
    patches: list[tuple[int, int, bytes]] = []
    for segment, chunk in zip(segments, chunks):
        start = segment["marker_offset"]
        end = segment["data_offset"] + segment["byte_length"]
        replacement = f"{len(chunk):X},".encode("ascii") + chunk
        patches.append((start, end, replacement))

    old_total = sum(target_lengths)
    old_total_hex = f"{old_total:X}".encode("ascii")
    new_total_hex = f"{len(edited):X}".encode("ascii")
    for field in total_fields:
        start = field["offset"]
        end = start + field["length"]
        if payload[start:end] != old_total_hex:
            raise CxtError("Serialized XMED total-length field does not match metadata")
        patches.append((start, end, new_total_hex))

    return apply_original_offset_patches(payload, patches), True


def rebuild_semantic_payload(
    edited_text: str,
    fields: dict[str, Any],
    member_path: Path,
) -> tuple[bytes, bytes, bool]:
    normalized = normalize_edited_text(edited_text)
    try:
        encoded = normalized.encode(TEXT_ENCODING)
    except UnicodeEncodeError as exc:
        bad = normalized[exc.start : exc.end]
        raise CxtError(
            f"Text in {member_path} contains {bad!r}, which cannot be "
            f"encoded as {TEXT_ENCODING}"
        ) from exc

    try:
        document = fields["document"]
        changed = sha256(encoded) != document.get("source_text_sha256")
        if changed:
            document = paige_xmed.update_document_text(document, encoded)
        payload = paige_xmed.serialize_xmed(
            document, encoded, preserve_size=not changed
        )
        padding = fields.get("padding_byte")
        padding_byte = b"" if padding is None else bytes((int(padding),))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise CxtError(f"Invalid semantic XMED fields for {member_path}") from exc
    except paige_xmed.PaigeError as exc:
        raise CxtError(f"Cannot serialize {member_path}: {exc}") from exc
    return payload, padding_byte, changed


def read_semantic_fields_file(path: Path) -> dict[str, Any]:
    try:
        fields = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CxtError(f"Cannot read semantic XMED fields {path}: {exc}") from exc
    if not isinstance(fields, dict) or fields.get("format") != SEMANTIC_FORMAT_VERSION:
        raise CxtError(f"Unsupported or missing semantic XMED field format in {path}")
    return fields


def command_inspect(input_path: Path) -> None:
    data = input_path.read_bytes()
    parsed = parse_cxt(data)
    text_count = 0
    text_bytes = 0
    segment_count = 0
    errors: list[str] = []
    for entry in parsed["xmed_entries"]:
        start = entry["offset"] + 8
        payload = data[start : start + entry["size"]]
        try:
            segments = find_text_segments(payload)
        except CxtError as exc:
            errors.append(f"XMED {entry['resource_id']}: {exc}")
            continue
        text_count += 1
        segment_count += len(segments)
        text_bytes += sum(segment["byte_length"] for segment in segments)

    print(f"File: {input_path}")
    print(f"Size: {len(data)} bytes")
    print(f"SHA-256: {sha256(data)}")
    print(
        f"Container: {parsed['root_tag'].decode('ascii')} / "
        f"{'little' if parsed['little_endian'] else 'big'} endian"
    )
    print(
        f"mmap: offset 0x{parsed['mmap_offset']:X}, "
        f"capacity {parsed['capacity']}, used {parsed['used']}"
    )
    print(
        f"XMED: {len(parsed['xmed_entries'])} resources, "
        f"tail starts at 0x{parsed['xmed_start']:X}"
    )
    print(
        f"Text: {text_count} members, {segment_count} serialized segments, "
        f"{text_bytes} encoded bytes"
    )
    for error in errors:
        print(f"Warning: {error}", file=sys.stderr)


def ensure_new_output_dir(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise CxtError(f"Output path exists and is not a directory: {path}")
        if any(path.iterdir()):
            raise CxtError(f"Output directory is not empty: {path}")
    else:
        path.mkdir(parents=True)


def command_unpack(input_path: Path, output_dir: Path) -> None:
    data = input_path.read_bytes()
    parsed = parse_cxt(data)
    ensure_new_output_dir(output_dir)

    prefix = data[: parsed["xmed_start"]]
    (output_dir / "metadata.bin").write_bytes(prefix)
    members: list[dict[str, Any]] = []
    for member_number, entry in enumerate(parsed["xmed_entries"], start=1):
        payload_start = entry["offset"] + 8
        payload_end = payload_start + entry["size"]
        payload = data[payload_start:payload_end]
        padding = data[payload_end : payload_end + (entry["size"] & 1)]
        try:
            document, text_bytes = paige_xmed.parse_xmed(payload)
            rebuilt = paige_xmed.serialize_xmed(
                document, text_bytes, preserve_size=True
            )
        except paige_xmed.PaigeError as exc:
            raise CxtError(f"Cannot parse XMED {entry['resource_id']}: {exc}") from exc
        if rebuilt != payload:
            raise CxtError(
                f"Semantic XMED round-trip mismatch for resource {entry['resource_id']}"
            )
        try:
            member_text = text_bytes.decode(TEXT_ENCODING)
        except UnicodeDecodeError as exc:
            raise CxtError(
                f"XMED {entry['resource_id']} text is not valid {TEXT_ENCODING}: {exc}"
            ) from exc
        filename = f"{member_number}.htm"
        semantic_fields = {
            "format": SEMANTIC_FORMAT_VERSION,
            "member_number": member_number,
            "resource_id": entry["resource_id"],
            "padding_byte": padding[0] if padding else None,
            "document": document,
        }
        fields_filename = f"{member_number}.xmed.json"
        member_html = make_member_html(
            member_number,
            entry["resource_id"],
            member_text,
        )
        (output_dir / filename).write_text(member_html, encoding="utf-8", newline="\n")
        (output_dir / fields_filename).write_text(
            format_semantic_json(semantic_fields) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        members.append(
            {
                "member_number": member_number,
                "name": f"s{member_number:03d}",
                "resource_id": entry["resource_id"],
                "entry_offset": entry["entry_offset"],
                "original_offset": entry["offset"],
                "original_size": entry["size"],
                "raw_tag": entry["raw_tag"].decode("latin-1"),
                "file": filename,
                "fields_file": fields_filename,
            }
        )
    manifest = {
        "format": FORMAT_VERSION,
        "source_file": input_path.name,
        "source_size": len(data),
        "source_sha256": sha256(data),
        "prefix_sha256": sha256(prefix),
        "root_tag": parsed["root_tag"].decode("ascii"),
        "endian": parsed["endian"],
        "mmap_offset": parsed["mmap_offset"],
        "entries_offset": parsed["entries_offset"],
        "entry_size": parsed["entry_size"],
        "root_entry_offset": parsed["root_entry_offset"],
        "xmed_start": parsed["xmed_start"],
        "members": members,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with (output_dir / "Members.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Number", "Type", "Name", "XMED Resource", "File"])
        for member in members:
            writer.writerow(
                [
                    member["member_number"],
                    "text",
                    member["name"],
                    member["resource_id"],
                    member["file"],
                ]
            )

    print(f"Unpacked {len(members)} text members to {output_dir}")
    print("Each HTML has a matching .xmed.json file with named Paige/XMED fields")
    print("HTML contains editable text only; no raw XMED or base64 copy is retained")


def command_pack(input_dir: Path, output_path: Path, force: bool) -> None:
    if output_path.exists() and not force:
        raise CxtError(f"Output file already exists (use --force): {output_path}")
    try:
        manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
        prefix = (input_dir / "metadata.bin").read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CxtError(f"Cannot read unpacked CXT metadata: {exc}") from exc
    manifest_format = manifest.get("format")
    if manifest_format not in (
        FORMAT_VERSION,
        INLINE_XML_FORMAT_VERSION,
        INLINE_JSON_FORMAT_VERSION,
        EXTERNAL_FIELDS_FORMAT_VERSION,
        LEGACY_FORMAT_VERSION,
    ):
        raise CxtError("Unsupported or missing manifest format")
    if sha256(prefix) != manifest.get("prefix_sha256"):
        raise CxtError("metadata.bin checksum mismatch")
    if len(prefix) != manifest.get("xmed_start"):
        raise CxtError("metadata.bin length does not match the manifest")

    endian = manifest["endian"]
    if endian not in ("<", ">"):
        raise CxtError("Invalid manifest byte order")
    rebuilt_prefix = bytearray(prefix)
    tail = bytearray()
    changed_members: list[int] = []

    semantic_by_resource: dict[int, dict[str, Any]] = {}
    if manifest_format == EXTERNAL_FIELDS_FORMAT_VERSION:
        fields_name = manifest.get("xmed_fields_file", "xmed-fields.json")
        try:
            fields = json.loads((input_dir / fields_name).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CxtError(f"Cannot read semantic XMED fields: {exc}") from exc
        if fields.get("format") != SEMANTIC_FORMAT_VERSION:
            raise CxtError("Unsupported or missing semantic XMED field format")
        semantic_by_resource = {
            int(item["resource_id"]): item for item in fields["members"]
        }
        common_document_fields = fields.get("common_document_fields", {})

    for member in manifest["members"]:
        member_path = input_dir / member["file"]
        edited_text, embedded = read_member_html(member_path)
        if manifest_format == LEGACY_FORMAT_VERSION:
            if embedded is None or embedded.get("resource_id") != member["resource_id"]:
                raise CxtError(f"Resource ID mismatch in {member_path}")
            payload, changed = rebuild_payload(edited_text, embedded)
            try:
                padding_byte = base64.b64decode(
                    embedded.get("padding_base64", ""), validate=True
                )[:1]
            except ValueError as exc:
                raise CxtError(f"Invalid padding metadata in {member_path}") from exc
        elif manifest_format == EXTERNAL_FIELDS_FORMAT_VERSION:
            if embedded is not None:
                raise CxtError(
                    f"Semantic member {member_path} unexpectedly contains raw XMED metadata"
                )
            fields = semantic_by_resource.get(member["resource_id"])
            if fields is None:
                raise CxtError(f"Missing semantic fields for {member_path}")
            document = dict(common_document_fields)
            document.update(fields["document"])
            semantic_fields = dict(fields)
            semantic_fields["document"] = document
            payload, padding_byte, changed = rebuild_semantic_payload(
                edited_text, semantic_fields, member_path
            )
        elif manifest_format == FORMAT_VERSION:
            if embedded is not None:
                raise CxtError(
                    f"Sidecar member {member_path} unexpectedly embeds XMED metadata"
                )
            fields_name = member.get(
                "fields_file", f"{member['member_number']}.xmed.json"
            )
            fields_path = input_dir / fields_name
            semantic_fields = read_semantic_fields_file(fields_path)
            if int(semantic_fields.get("resource_id", -1)) != member["resource_id"]:
                raise CxtError(f"Resource ID mismatch in {fields_path}")
            if int(semantic_fields.get("member_number", -1)) != member["member_number"]:
                raise CxtError(f"Member number mismatch in {fields_path}")
            payload, padding_byte, changed = rebuild_semantic_payload(
                edited_text, semantic_fields, member_path
            )
        else:
            if embedded is None or embedded.get("format") != SEMANTIC_FORMAT_VERSION:
                raise CxtError(f"Missing semantic XMED fields in {member_path}")
            if int(embedded.get("resource_id", -1)) != member["resource_id"]:
                raise CxtError(f"Resource ID mismatch in {member_path}")
            if int(embedded.get("member_number", -1)) != member["member_number"]:
                raise CxtError(f"Member number mismatch in {member_path}")
            payload, padding_byte, changed = rebuild_semantic_payload(
                edited_text, embedded, member_path
            )
        if changed:
            changed_members.append(member["member_number"])

        chunk_offset = len(rebuilt_prefix) + len(tail)
        raw_tag = member["raw_tag"].encode("latin-1")
        if len(raw_tag) != 4:
            raise CxtError(f"Invalid raw FourCC for member {member['member_number']}")
        tail.extend(raw_tag)
        tail.extend(struct.pack(endian + "I", len(payload)))
        tail.extend(payload)
        if len(payload) & 1:
            tail.extend(padding_byte[:1] or b"\x00")

        entry_offset = member["entry_offset"]
        write_u32(rebuilt_prefix, entry_offset + 4, len(payload), endian)
        write_u32(rebuilt_prefix, entry_offset + 8, chunk_offset, endian)

    output = rebuilt_prefix + tail
    write_u32(output, 4, len(output) - 8, endian)
    write_u32(output, manifest["root_entry_offset"] + 4, len(output) - 8, endian)

    reparsed = parse_cxt(output)
    if len(reparsed["xmed_entries"]) != len(manifest["members"]):
        raise CxtError("Internal verification found an XMED resource count mismatch")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)
    output_hash = sha256(output)
    exact = output_hash == manifest.get("source_sha256")
    print(f"Packed {len(manifest['members'])} members to {output_path}")
    print(f"SHA-256: {output_hash}")
    if exact:
        print("Round trip: byte-identical to the source CXT")
    else:
        changed = ", ".join(str(number) for number in changed_members) or "metadata only"
        print(f"Round trip: modified (members: {changed})")
        print("Structure check: root, mmap, chunk offsets, and XMED sizes are valid")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lossless unpack/repack tool for Director text-cast CXT files"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="show CXT structure")
    inspect_parser.add_argument("input", type=Path)

    unpack_parser = subparsers.add_parser(
        "unpack", help="export editable lossless HTML members"
    )
    unpack_parser.add_argument("input", type=Path)
    unpack_parser.add_argument("output_dir", type=Path)

    pack_parser = subparsers.add_parser("pack", help="rebuild a CXT from HTML members")
    pack_parser.add_argument("input_dir", type=Path)
    pack_parser.add_argument("output", type=Path)
    pack_parser.add_argument(
        "--force", action="store_true", help="overwrite an existing output file"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            command_inspect(args.input)
        elif args.command == "unpack":
            command_unpack(args.input, args.output_dir)
        elif args.command == "pack":
            command_pack(args.input_dir, args.output, args.force)
        else:
            parser.error(f"Unknown command {args.command}")
    except (CxtError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
