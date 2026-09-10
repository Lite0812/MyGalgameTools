#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

import yks_asm


@dataclass(slots=True)
class FileReport:
    path: str
    byte_identical: bool
    s1_identical: bool
    s2_identical: bool
    s4_identical: bool
    original_size: int
    rebuilt_size: int
    original_s1_len: int
    rebuilt_s1_len: int
    original_s2_len: int
    rebuilt_s2_len: int
    original_s4_len: int
    rebuilt_s4_len: int
    first_byte_diff: str | None
    first_byte_diff_offset: int | None
    first_s1_diff_index: int | None
    first_s2_diff_index: int | None
    first_s4_diff_offset: int | None
    byte_equal_prefix_len: int
    byte_equal_ratio: float
    detail: str


@dataclass(slots=True)
class ByteComparison:
    identical: bool
    first_diff_offset: int | None
    first_diff_text: str | None
    equal_prefix_len: int
    equal_ratio: float


def iter_yks_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(path for path in target.rglob("*") if path.is_file() and path.suffix.lower() == ".yks")


def first_sequence_diff_index(left, right) -> int | None:
    limit = min(len(left), len(right))
    for index in range(limit):
        if left[index] != right[index]:
            return index
    if len(left) != len(right):
        return limit
    return None


def compare_bytes(left: bytes, right: bytes) -> ByteComparison:
    limit = min(len(left), len(right))
    first_diff_offset: int | None = None
    same = 0
    for index in range(limit):
        if left[index] == right[index]:
            same += 1
        elif first_diff_offset is None:
            first_diff_offset = index
    if first_diff_offset is None and len(left) != len(right):
        first_diff_offset = limit

    identical = first_diff_offset is None
    if identical:
        first_diff_text = None
        equal_prefix_len = limit
    elif first_diff_offset == limit:
        first_diff_text = f"size mismatch: {len(left)} != {len(right)}"
        equal_prefix_len = limit
    else:
        first_diff_text = f"first diff: 0x{first_diff_offset:X} {left[first_diff_offset]:02X} != {right[first_diff_offset]:02X}"
        equal_prefix_len = first_diff_offset

    return ByteComparison(
        identical=identical,
        first_diff_offset=first_diff_offset,
        first_diff_text=first_diff_text,
        equal_prefix_len=equal_prefix_len,
        equal_ratio=same / max(len(left), len(right), 1),
    )


def process_file(path: Path) -> FileReport:
    original_bytes = path.read_bytes()
    original_yks = yks_asm.YKSFile.from_bytes(original_bytes, source=path)
    clean_text = yks_asm.disassemble_to_text(original_yks, include_exact_metadata=False)
    rebuilt_yks = yks_asm.assemble_text(clean_text)

    rebuilt_bytes = rebuilt_yks.to_bytes()
    byte_comparison = compare_bytes(original_bytes, rebuilt_bytes)
    s4_comparison = compare_bytes(original_yks.s4, rebuilt_yks.s4)

    byte_identical = byte_comparison.identical
    s1_identical = rebuilt_yks.s1 == original_yks.s1
    s2_identical = rebuilt_yks.s2 == original_yks.s2
    s4_identical = rebuilt_yks.s4 == original_yks.s4

    s1_diff = first_sequence_diff_index(original_yks.s1, rebuilt_yks.s1)
    s2_diff = first_sequence_diff_index(original_yks.s2, rebuilt_yks.s2)
    s4_diff = s4_comparison.first_diff_offset

    detail_parts: list[str] = []
    if byte_identical:
        detail_parts.append("bytes=identical")
    else:
        detail_parts.append(byte_comparison.first_diff_text or "bytes=different")
    detail_parts.append(f"s1={'same' if s1_identical else f'diff@{s1_diff}'}")
    detail_parts.append(f"s2={'same' if s2_identical else f'diff@{s2_diff}'}")
    detail_parts.append(f"s4={'same' if s4_identical else f'diff@0x{s4_diff:X}' if s4_diff is not None else 'diff'}")

    return FileReport(
        path=path.as_posix(),
        byte_identical=byte_identical,
        s1_identical=s1_identical,
        s2_identical=s2_identical,
        s4_identical=s4_identical,
        original_size=len(original_bytes),
        rebuilt_size=len(rebuilt_bytes),
        original_s1_len=len(original_yks.s1),
        rebuilt_s1_len=len(rebuilt_yks.s1),
        original_s2_len=len(original_yks.s2),
        rebuilt_s2_len=len(rebuilt_yks.s2),
        original_s4_len=len(original_yks.s4),
        rebuilt_s4_len=len(rebuilt_yks.s4),
        first_byte_diff=byte_comparison.first_diff_text,
        first_byte_diff_offset=byte_comparison.first_diff_offset,
        first_s1_diff_index=s1_diff,
        first_s2_diff_index=s2_diff,
        first_s4_diff_offset=s4_diff,
        byte_equal_prefix_len=byte_comparison.equal_prefix_len,
        byte_equal_ratio=byte_comparison.equal_ratio,
        detail=" | ".join(detail_parts),
    )


def default_report_path(target: Path) -> Path:
    if target.is_dir():
        return target.parent / f"{target.name}_clean_binary_similarity_report.json"
    return target.with_suffix(target.suffix + ".clean_binary_similarity.json")


def eta_text(started: float, done: int, total: int) -> str:
    elapsed = max(time.perf_counter() - started, 1e-9)
    if done <= 0 or done >= total:
        return "eta=0.0s"
    remaining = elapsed / done * (total - done)
    return f"eta={remaining:.1f}s"


def print_progress(done: int, total: int, started: float, report: FileReport) -> None:
    percent = (done / total) * 100 if total else 100.0
    state = "OK  " if report.byte_identical else "DIFF"
    print(
        f"[{done:>3}/{total:<3} {percent:6.2f}% {eta_text(started, done, total)}] "
        f"{state} {report.path} :: {report.detail}",
        flush=True,
    )


def summarize(reports: list[FileReport]) -> dict[str, object]:
    total = len(reports)
    byte_identical = sum(1 for item in reports if item.byte_identical)
    s1_identical = sum(1 for item in reports if item.s1_identical)
    s2_identical = sum(1 for item in reports if item.s2_identical)
    s4_identical = sum(1 for item in reports if item.s4_identical)
    diff_offset_counter = Counter(
        f"0x{item.first_byte_diff_offset:X}" for item in reports if item.first_byte_diff_offset is not None
    )
    s4_offset_counter = Counter(
        f"0x{item.first_s4_diff_offset:X}" for item in reports if item.first_s4_diff_offset is not None
    )
    mean_ratio = sum(item.byte_equal_ratio for item in reports) / total if total else 1.0
    mean_prefix = sum(item.byte_equal_prefix_len for item in reports) / total if total else 0.0
    return {
        "total": total,
        "byte_identical": byte_identical,
        "byte_identical_ratio": byte_identical / total if total else 1.0,
        "s1_identical": s1_identical,
        "s2_identical": s2_identical,
        "s4_identical": s4_identical,
        "mean_byte_equal_ratio": mean_ratio,
        "mean_equal_prefix_len": mean_prefix,
        "top_first_byte_diff_offsets": diff_offset_counter.most_common(20),
        "top_first_s4_diff_offsets": s4_offset_counter.most_common(20),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure how close clean-mode YKS asm->yks roundtrip is to original binary, with progress output."
    )
    parser.add_argument("target", type=Path, help="YKS file or directory to scan")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    files = iter_yks_files(args.target)
    if not files:
        print(f"No YKS files found under: {args.target}")
        return 1

    report_path = args.report or default_report_path(args.target)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    print(f"START clean_binary_similarity files={len(files)} workers={args.workers} report={report_path}", flush=True)

    reports: list[FileReport] = []
    if args.workers <= 1:
        for index, path in enumerate(files, start=1):
            report = process_file(path)
            reports.append(report)
            print_progress(index, len(files), started, report)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(process_file, path): path for path in files}
            completed = 0
            for future in as_completed(futures):
                report = future.result()
                reports.append(report)
                completed += 1
                print_progress(completed, len(files), started, report)

    reports.sort(key=lambda item: item.path)
    summary = summarize(reports)
    payload = {
        "target": str(args.target),
        "workers": args.workers,
        "elapsed_seconds": time.perf_counter() - started,
        "summary": summary,
        "reports": [asdict(item) for item in reports],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "SUMMARY "
        f"total={summary['total']} "
        f"byte_identical={summary['byte_identical']} "
        f"s1_identical={summary['s1_identical']} "
        f"s2_identical={summary['s2_identical']} "
        f"s4_identical={summary['s4_identical']} "
        f"mean_ratio={summary['mean_byte_equal_ratio']:.6f} "
        f"mean_prefix={summary['mean_equal_prefix_len']:.1f} "
        f"report={report_path}",
        flush=True,
    )
    print(f"TOP first-byte-diff offsets: {summary['top_first_byte_diff_offsets']}", flush=True)
    print(f"TOP first-s4-diff offsets: {summary['top_first_s4_diff_offsets']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())