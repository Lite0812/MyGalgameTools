from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


QT_MODULES = ("PyQt6", "PySide6")


def candidate_interpreters() -> list[Path]:
    candidates = [
        Path(sys.executable),
        Path(r"C:\Python314\python.exe"),
        Path(r"C:\Program Files\Python313\python.exe"),
        Path(r"c:\python314\python.exe"),
    ]

    seen: set[str] = set()
    resolved: list[Path] = []
    for candidate in candidates:
        normalized = str(candidate).lower()
        if normalized in seen or not candidate.is_file():
            continue
        seen.add(normalized)
        resolved.append(candidate)
    return resolved


def interpreter_has_qt(python_exe: Path) -> bool:
    for module_name in QT_MODULES:
        result = subprocess.run(
            [str(python_exe), "-c", f"import {module_name}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
    return False


def main() -> int:
    gui_script = Path(__file__).with_name("director_ls_gui.py")
    if not gui_script.is_file():
        print(f"找不到 GUI 文件: {gui_script}", file=sys.stderr)
        return 1

    for python_exe in candidate_interpreters():
        if not interpreter_has_qt(python_exe):
            continue
        command = [str(python_exe), str(gui_script), *sys.argv[1:]]
        return subprocess.run(command, env=os.environ.copy()).returncode

    print(
        "当前机器上找不到可用的 Qt Python 解释器。"
        "请安装 PyQt6 或 PySide6，或者直接使用已经带 PyQt6 的 Python 运行 director_ls_gui.py。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())