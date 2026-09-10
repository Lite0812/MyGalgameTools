from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_WINMM = Path(os.environ["WINDIR"]) / "SysWOW64" / "winmm.dll"


def read_exports() -> list[tuple[int, str | None]]:
    output = subprocess.check_output(
        ["dumpbin", "/nologo", "/exports", str(SYSTEM_WINMM)],
        text=True,
        encoding="mbcs",
        errors="replace",
    )
    exports: dict[int, str | None] = {}
    named = re.compile(r"^\s*(\d+)\s+[0-9A-F]+\s+[0-9A-F]+\s+(\S+)\s*$")
    noname = re.compile(r"^\s*(\d+)\s+[0-9A-F]+\s+\[NONAME\]\s*$")
    for line in output.splitlines():
        match = named.match(line)
        if match:
            exports[int(match.group(1))] = match.group(2)
            continue
        match = noname.match(line)
        if match:
            exports[int(match.group(1))] = None
    expected = list(range(2, 195))
    if sorted(exports) != expected:
        missing = sorted(set(expected) - set(exports))
        raise RuntimeError(f"unexpected x86 winmm export table; missing ordinals: {missing}")
    return sorted(exports.items())


def write_asm(exports: list[tuple[int, str | None]]) -> None:
    lines = [
        ".386",
        ".model flat",
        "option casemap:none",
        "",
        "EXTERN _g_winmm_exports:DWORD",
        "",
        ".code",
        "PUBLIC WinMMOrdinal1",
        "WinMMOrdinal1 PROC",
        "    xor eax, eax",
        "    ret",
        "WinMMOrdinal1 ENDP",
        "",
    ]
    for ordinal, _ in exports:
        symbol = f"WinMMOrdinal{ordinal}"
        lines.extend(
            [
                f"PUBLIC {symbol}",
                f"{symbol} PROC",
                f"    jmp DWORD PTR [_g_winmm_exports + {(ordinal - 2) * 4}]",
                f"{symbol} ENDP",
                "",
            ]
        )
    lines.append("END")
    (ROOT / "src" / "winmm_proxy.asm").write_text("\n".join(lines) + "\n", encoding="ascii")


def write_def(exports: list[tuple[int, str | None]]) -> None:
    lines = ["LIBRARY winmm", "EXPORTS", "    CompatibilityOrdinal1=WinMMOrdinal1 @1 NONAME"]
    for ordinal, name in exports:
        symbol = f"WinMMOrdinal{ordinal}"
        if name is None:
            lines.append(f"    Ordinal{ordinal}={symbol} @{ordinal} NONAME")
        else:
            lines.append(f"    {name}={symbol} @{ordinal}")
    (ROOT / "src" / "winmm.def").write_text("\n".join(lines) + "\n", encoding="ascii")


def main() -> None:
    exports = read_exports()
    write_asm(exports)
    write_def(exports)
    print(f"Generated {len(exports)} x86 winmm proxy exports from {SYSTEM_WINMM}")


if __name__ == "__main__":
    main()
