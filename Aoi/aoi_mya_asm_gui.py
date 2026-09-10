from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if sys.platform.startswith("win"):
    try:
        import winreg
    except ImportError:
        winreg = None
else:
    winreg = None

try:
    import darkdetect

    HAS_DARKDETECT = True
except ImportError:
    HAS_DARKDETECT = False

import aoi_archive_tool as archive_tool
from aoi_iph_tool import run_decode as run_iph_decode
from aoi_iph_tool import run_encode as run_iph_encode
from aoi_mya_asm import run_asm
from aoi_mya_disasm import run_disasm
from aoi_mya_json_tool import run_json_dump, run_json_inject
from aoi_mya_opcode_common import MYU_OPCODE_ARG_COUNTS, SCRIPT_ENCODING
from aoi_obj_json_tool import ObjParseError, dump_obj_file, inject_obj_file, iter_obj_files


ENCODING_ITEMS = ["cp932", "utf-8", "shift_jis", "gbk", "big5"]


class StreamRedirector:
    def __init__(self, signal=None):
        self.signal = signal
        self._original_stdout = sys.__stdout__

    def write(self, text):
        if self.signal:
            try:
                msg = str(text)
                if msg:
                    self.signal.emit(msg)
            except Exception:
                pass

        if self._original_stdout:
            try:
                self._original_stdout.write(text)
                self._original_stdout.flush()
            except Exception:
                pass

    def flush(self):
        if self._original_stdout:
            try:
                self._original_stdout.flush()
            except Exception:
                pass


class CardFrame(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("CardFrame")
        self.setFrameShape(QFrame.Shape.NoFrame)


class ModernButton(QPushButton):
    def __init__(self, text, is_primary=False):
        super().__init__(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("PrimaryButton" if is_primary else "SecondaryButton")
        self.setMinimumHeight(30)


class DragDropLineEdit(QLineEdit):
    def __init__(self, parent=None, is_folder=False):
        super().__init__(parent)
        self.is_folder = is_folder
        self.setAcceptDrops(True)
        self.setPlaceholderText("可直接拖入文件或文件夹...")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.setText(os.path.normpath(path))
            event.acceptProposedAction()


class WorkerThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, task: dict):
        super().__init__()
        self.task = task
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        redirector = StreamRedirector(self.log_signal)
        sys.stdout = redirector
        sys.stderr = redirector

        try:
            kind = self.task["kind"]
            if kind == "archive_extract":
                self.run_archive_extract()
            elif kind == "archive_pack":
                self.run_archive_pack()
            elif kind == "mya_asm_disasm":
                run_disasm(
                    input_path=Path(self.task["input_path"]),
                    output_root=Path(self.task["output_path"]),
                    input_script_encoding=self.task["script_encoding"],
                    recursive=bool(self.task.get("recursive", True)),
                    keep_structure=bool(self.task.get("keep_structure", True)),
                )
            elif kind == "mya_asm_asm":
                run_asm(
                    input_path=Path(self.task["input_path"]),
                    output_root=Path(self.task["output_path"]),
                    output_script_encoding=self.task["script_encoding"],
                    recursive=bool(self.task.get("recursive", True)),
                    keep_structure=bool(self.task.get("keep_structure", True)),
                )
            elif kind == "myu_asm_disasm":
                run_disasm(
                    input_path=Path(self.task["input_path"]),
                    output_root=Path(self.task["output_path"]),
                    input_script_encoding="utf-16le",
                    recursive=bool(self.task.get("recursive", True)),
                    keep_structure=bool(self.task.get("keep_structure", True)),
                    opcode_counts=MYU_OPCODE_ARG_COUNTS,
                )
            elif kind == "myu_asm_asm":
                run_asm(
                    input_path=Path(self.task["input_path"]),
                    output_root=Path(self.task["output_path"]),
                    output_script_encoding="utf-16le",
                    recursive=bool(self.task.get("recursive", True)),
                    keep_structure=bool(self.task.get("keep_structure", True)),
                    opcode_counts=MYU_OPCODE_ARG_COUNTS,
                )
            elif kind == "mya_json_dump":
                run_json_dump(
                    input_path=Path(self.task["input_path"]),
                    output_root=Path(self.task["output_path"]),
                    script_encoding=self.task["script_encoding"],
                    recursive=bool(self.task.get("recursive", True)),
                    keep_structure=bool(self.task.get("keep_structure", True)),
                )
            elif kind == "mya_json_inject":
                run_json_inject(
                    input_script_path=Path(self.task["input_path"]),
                    input_json_root=Path(self.task["json_path"]),
                    output_root=Path(self.task["output_path"]),
                    input_script_encoding=self.task["input_script_encoding"],
                    output_script_encoding=self.task["output_script_encoding"],
                    recursive=bool(self.task.get("recursive", True)),
                    keep_structure=bool(self.task.get("keep_structure", True)),
                    copy_extra_files=bool(self.task.get("copy_extra_files", False)),
                )
            elif kind == "iph_decode":
                run_iph_decode(
                    input_path=Path(self.task["input_path"]),
                    output_root=Path(self.task["output_path"]),
                    recursive=bool(self.task.get("recursive", True)),
                    keep_structure=bool(self.task.get("keep_structure", True)),
                )
            elif kind == "iph_encode":
                run_iph_encode(
                    input_path=Path(self.task["input_path"]),
                    output_root=Path(self.task["output_path"]),
                    recursive=bool(self.task.get("recursive", True)),
                    keep_structure=bool(self.task.get("keep_structure", True)),
                )
            elif kind == "obj_dump":
                self.run_obj_dump()
            elif kind == "obj_inject":
                self.run_obj_inject()
            else:
                raise RuntimeError(f"未知任务类型: {kind}")

            self.finished_signal.emit(True, "任务完成")
        except Exception as exc:
            self.log_signal.emit(f"\n错误详情:\n{traceback.format_exc()}")
            self.finished_signal.emit(False, str(exc))
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    def _iter_files(self, root: Path, recursive: bool = True) -> list[Path]:
        iterator = root.rglob("*") if recursive else root.iterdir()
        return sorted(path for path in iterator if path.is_file())

    def _iter_child_dirs(self, root: Path, recursive: bool = False) -> list[Path]:
        if not recursive:
            return sorted(path for path in root.iterdir() if path.is_dir())
        return sorted(path for path in root.rglob("*") if path.is_dir())

    def _is_dir_like_output(self, output_path: Path) -> bool:
        if output_path.exists():
            return output_path.is_dir()
        if str(output_path).endswith(("\\", "/")):
            return True
        return output_path.suffix == ""

    def _resolve_single_output_file(self, output_path: Path, default_path: Path, fallback_name: str) -> Path:
        if not str(output_path).strip():
            return default_path
        if self._is_dir_like_output(output_path):
            return output_path / fallback_name
        return output_path

    def _resolve_single_output_dir(self, output_path: Path, default_path: Path) -> Path:
        if not str(output_path).strip():
            return default_path
        return output_path

    def _build_output_file(
        self,
        source_file: Path,
        input_root: Path,
        output_root: Path,
        keep_structure: bool,
        transform,
    ) -> Path:
        target = transform(source_file)
        if keep_structure:
            relative_parent = source_file.relative_to(input_root).parent
            return output_root / relative_parent / target.name
        return output_root / target.name

    def _build_output_dir(
        self,
        source_path: Path,
        input_root: Path,
        output_root: Path,
        keep_structure: bool,
        name_provider,
    ) -> Path:
        target_name = name_provider(source_path)
        if keep_structure:
            relative_parent = source_path.relative_to(input_root).parent
            return output_root / relative_parent / target_name
        return output_root / target_name

    def run_obj_dump(self):
        input_path = Path(self.task["input_path"])
        output_root = Path(self.task["output_path"])
        recursive = bool(self.task.get("recursive", True))

        files = iter_obj_files(input_path, recursive=recursive)
        if not files:
            raise RuntimeError("未找到 OBJ 文件")

        print(f"共发现 {len(files)} 个 OBJ 文件。")
        for index, file_path in enumerate(files, start=1):
            if not self.is_running:
                break
            output_path = (
                output_root / f"{file_path.name}.json"
                if input_path.is_dir()
                else output_root
            )
            try:
                dump_obj_file(file_path, output_path)
                print(f"[{index}/{len(files)}] 已导出: {file_path.name}")
            except ObjParseError as exc:
                print(f"[{index}/{len(files)}] 失败: {file_path.name}: {exc}")

    def run_obj_inject(self):
        input_path = Path(self.task["input_path"])
        output_root = Path(self.task["output_path"])
        recursive = bool(self.task.get("recursive", True))

        json_files = []
        if input_path.is_file():
            json_files = [input_path]
        else:
            iterator = input_path.rglob("*.json") if recursive else input_path.glob("*.json")
            json_files = sorted(path for path in iterator if path.is_file())

        if not json_files:
            raise RuntimeError("未找到 JSON 文件")

        print(f"共发现 {len(json_files)} 个 JSON 文件。")
        for index, file_path in enumerate(json_files, start=1):
            if not self.is_running:
                break
            output_path = (
                output_root
                if input_path.is_file()
                else output_root / file_path.with_suffix("").with_suffix(".obj").name
            )
            try:
                inject_obj_file(file_path, output_path)
                print(f"[{index}/{len(json_files)}] 已注入: {file_path.name}")
            except ObjParseError as exc:
                print(f"[{index}/{len(json_files)}] 失败: {file_path.name}: {exc}")

    def run_archive_extract(self):
        input_path = Path(self.task["input_path"])
        output_path = Path(self.task["output_path"])
        recursive = bool(self.task.get("recursive", True))
        keep_structure = bool(self.task.get("keep_structure", True))

        if input_path.is_file():
            output_dir = self._resolve_single_output_dir(
                output_path,
                input_path.parent / f"{input_path.stem}_out",
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            print(f"正在解包: {input_path.name}")
            archive_type, file_count = archive_tool.choose_extract_mode(input_path, output_dir)
            print(f"解包完成。封包类型: {archive_type}，共解出 {file_count} 个文件。")
            return

        if not input_path.is_dir():
            raise RuntimeError(f"输入路径不存在: {input_path}")

        output_root = self._resolve_single_output_dir(
            output_path,
            input_path.parent / f"{input_path.name}_out",
        )
        output_root.mkdir(parents=True, exist_ok=True)

        files = self._iter_files(input_path, recursive=recursive)
        print(f"共发现 {len(files)} 个文件待尝试解包。")
        success = 0
        failed = 0
        total_output_files = 0
        archive_type_summary: dict[str, int] = {}

        for index, file_path in enumerate(files, start=1):
            if not self.is_running:
                break
            try:
                out_dir = self._build_output_dir(
                    file_path,
                    input_path,
                    output_root,
                    keep_structure,
                    lambda current: current.stem,
                )
                out_dir.mkdir(parents=True, exist_ok=True)
                print(f"[{index}/{len(files)}] 正在解包: {file_path}")
                archive_type, file_count = archive_tool.choose_extract_mode(file_path, out_dir)
                archive_type_summary[archive_type] = archive_type_summary.get(archive_type, 0) + 1
                total_output_files += file_count
                print(
                    f"[{index}/{len(files)}] 解包完成: {file_path.name}，"
                    f"封包类型: {archive_type}，解出 {file_count} 个文件。"
                )
                success += 1
            except Exception as exc:
                print(f"[{index}/{len(files)}] 跳过 {file_path.name}: {exc}")
                failed += 1

        if archive_type_summary:
            type_summary = "，".join(
                f"{archive_type} {count} 个"
                for archive_type, count in sorted(archive_type_summary.items())
            )
            print(f"封包类型汇总: {type_summary}。")
        print(f"批量解包完成。成功 {success}，失败 {failed}，共解出 {total_output_files} 个文件。")

    def run_archive_pack(self):
        input_path = Path(self.task["input_path"])
        output_path = Path(self.task["output_path"])
        pack_mode = self.task["pack_mode"]
        batch_subdirs = bool(self.task.get("batch_subdirs"))
        recursive = bool(self.task.get("recursive", False))
        keep_structure = bool(self.task.get("keep_structure", False))

        if not input_path.is_dir():
            raise RuntimeError("封包输入必须是目录。")

        if batch_subdirs:
            output_dir = self._resolve_single_output_dir(
                output_path,
                input_path.parent / f"{input_path.name}_pack",
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            child_dirs = self._iter_child_dirs(input_path, recursive=recursive)
            if not child_dirs:
                raise RuntimeError("没有找到可批量封包的子目录。")
            print(f"共发现 {len(child_dirs)} 个子目录待封包。")
            for index, child_dir in enumerate(child_dirs, start=1):
                if not self.is_running:
                    break
                ext = ".vfs" if pack_mode == "4" else ".box"
                out_file = self._build_output_file(
                    child_dir,
                    input_path,
                    output_dir,
                    keep_structure,
                    lambda current: current.with_name(f"{current.name}{ext}"),
                )
                print(f"[{index}/{len(child_dirs)}] 正在封包: {child_dir}")
                archive_tool.ensure_parent(out_file)
                archive_tool.choose_pack_mode(pack_mode, child_dir, out_file)
            print("批量封包完成。")
            return

        ext = ".vfs" if pack_mode == "4" else ".box"
        default_out = input_path.parent / f"{input_path.name}{ext}"
        out_file = self._resolve_single_output_file(output_path, default_out, f"{input_path.name}{ext}")
        archive_tool.ensure_parent(out_file)
        print(f"正在封包: {input_path}")
        archive_tool.choose_pack_mode(pack_mode, input_path, out_file)
        print("封包完成。")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AOI Tools GUI")
        self.resize(760, 760)
        self.setObjectName("MainBackground")

        self.worker = None

        self.init_ui()
        self.detect_system_theme()

    def init_ui(self):
        central_widget = QWidget()
        central_widget.setObjectName("MainBackground")
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)

        header_layout = QHBoxLayout()
        title_label = QLabel("AOI Tools")
        title_label.setObjectName("AppTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["跟随系统", "现代浅色", "现代深色", "赛博朋克"])
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        self.theme_combo.setFixedWidth(120)
        header_layout.addWidget(self.theme_combo)
        main_layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(2)

        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)

        self.setup_archive_tab()
        self.setup_mya_asm_tab()
        self.setup_myu_asm_tab()
        self.setup_iph_tab()
        self.setup_obj_tab()

        log_card = CardFrame()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        log_header = QWidget()
        log_header.setObjectName("LogHeader")
        log_header_layout = QHBoxLayout(log_header)
        log_header_layout.setContentsMargins(10, 5, 10, 5)
        log_header_layout.addWidget(QLabel("运行日志 (Log)"))
        log_header_layout.addStretch()

        clear_btn = QPushButton("清除")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet("border:none; font-weight:bold; color: #888;")
        clear_btn.clicked.connect(self.log_view_clear)
        log_header_layout.addWidget(clear_btn)
        log_layout.addWidget(log_header)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("LogConsole")
        log_layout.addWidget(self.log_view)

        splitter.addWidget(log_card)
        splitter.setSizes([470, 290])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        main_layout.addWidget(splitter)

    def setup_mya_asm_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        sub_tabs = QTabWidget()

        export_page = QWidget()
        export_page_layout = QVBoxLayout(export_page)
        export_page_layout.setContentsMargins(0, 0, 0, 0)
        export_page_layout.setSpacing(8)

        export_mode_row = QHBoxLayout()
        export_mode_row.setContentsMargins(0, 0, 0, 0)
        export_mode_row.setSpacing(6)
        export_mode_row.addWidget(QLabel("导出模式:"))
        self.export_mode_combo = QComboBox()
        self.export_mode_combo.addItems(["ASM 文本", "JSON"])
        self.export_mode_combo.setMaximumWidth(160)
        export_mode_row.addWidget(self.export_mode_combo)
        export_mode_row.addStretch(1)
        export_page_layout.addLayout(export_mode_row)

        self.export_stack = QStackedWidget()

        disasm_page = QWidget()
        disasm_page_layout = QVBoxLayout(disasm_page)
        disasm_page_layout.setContentsMargins(0, 0, 0, 0)
        disasm_page_layout.setSpacing(8)

        disasm_card = CardFrame()
        disasm_layout = QVBoxLayout(disasm_card)
        disasm_layout.setContentsMargins(10, 10, 10, 10)
        disasm_layout.setSpacing(5)
        disasm_layout.addWidget(QLabel("反汇编脚本 (Disassemble)"))

        self.disasm_input = self.create_path_selector(
            disasm_layout,
            "输入脚本/目录:",
            on_change=self.auto_fill_disasm_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.disasm_output = self.create_path_selector(
            disasm_layout,
            "输出 ASM/目录:",
            add_file_button=False,
            add_dir_button=True,
            is_output=True,
        )
        self.disasm_script_encoding = self.create_combo_row(
            disasm_layout,
            "输入脚本编码:",
            ENCODING_ITEMS,
            editable=True,
            default_text=SCRIPT_ENCODING,
            prioritize_cp932=True,
        )
        self.disasm_recursive, self.disasm_keep_structure = self.create_batch_options_row(
            disasm_layout,
            recursive_checked=True,
            keep_structure_checked=True,
        )
        disasm_tip = QLabel("原始脚本字符串编码可配置；中间 `.asm.txt` 始终使用 UTF-8。")
        disasm_tip.setObjectName("TipLabel")
        disasm_tip.setWordWrap(True)
        disasm_layout.addWidget(disasm_tip)

        disasm_btn = ModernButton("开始反汇编", is_primary=True)
        disasm_btn.clicked.connect(self.run_disasm_task)
        disasm_layout.addWidget(disasm_btn)
        disasm_page_layout.addWidget(disasm_card)
        disasm_page_layout.addStretch(1)
        self.export_stack.addWidget(disasm_page)

        json_dump_page = QWidget()
        json_dump_page_layout = QVBoxLayout(json_dump_page)
        json_dump_page_layout.setContentsMargins(0, 0, 0, 0)
        json_dump_page_layout.setSpacing(8)

        json_dump_card = CardFrame()
        json_dump_layout = QVBoxLayout(json_dump_card)
        json_dump_layout.setContentsMargins(10, 10, 10, 10)
        json_dump_layout.setSpacing(5)
        json_dump_layout.addWidget(QLabel("提取 JSON (Dump JSON)"))

        self.json_dump_input = self.create_path_selector(
            json_dump_layout,
            "输入脚本/目录:",
            on_change=self.auto_fill_json_dump_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.json_dump_output = self.create_path_selector(
            json_dump_layout,
            "输出 JSON/目录:",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.json_dump_script_encoding = self.create_combo_row(
            json_dump_layout,
            "脚本编码:",
            ENCODING_ITEMS,
            editable=True,
            default_text=SCRIPT_ENCODING,
            prioritize_cp932=True,
        )
        self.json_dump_recursive, self.json_dump_keep_structure = self.create_batch_options_row(
            json_dump_layout,
            recursive_checked=True,
            keep_structure_checked=True,
        )
        json_dump_tip = QLabel("JSON 固定为 UTF-8，只导出 `name` 和 `message` 字段。连续 TEXT 会合并为一个 message。")
        json_dump_tip.setObjectName("TipLabel")
        json_dump_tip.setWordWrap(True)
        json_dump_layout.addWidget(json_dump_tip)

        json_dump_btn = ModernButton("开始提取 JSON", is_primary=True)
        json_dump_btn.clicked.connect(self.run_json_dump_task)
        json_dump_layout.addWidget(json_dump_btn)
        json_dump_page_layout.addWidget(json_dump_card)
        json_dump_page_layout.addStretch(1)
        self.export_stack.addWidget(json_dump_page)

        self.export_mode_combo.currentIndexChanged.connect(self.export_stack.setCurrentIndex)
        export_page_layout.addWidget(self.export_stack)
        sub_tabs.addTab(export_page, "导出")

        import_page = QWidget()
        import_page_layout = QVBoxLayout(import_page)
        import_page_layout.setContentsMargins(0, 0, 0, 0)
        import_page_layout.setSpacing(8)

        import_mode_row = QHBoxLayout()
        import_mode_row.setContentsMargins(0, 0, 0, 0)
        import_mode_row.setSpacing(6)
        import_mode_row.addWidget(QLabel("生成模式:"))
        self.import_mode_combo = QComboBox()
        self.import_mode_combo.addItems(["ASM 文本", "JSON"])
        self.import_mode_combo.setMaximumWidth(160)
        import_mode_row.addWidget(self.import_mode_combo)
        import_mode_row.addStretch(1)
        import_page_layout.addLayout(import_mode_row)

        self.import_stack = QStackedWidget()

        asm_page = QWidget()
        asm_page_layout = QVBoxLayout(asm_page)
        asm_page_layout.setContentsMargins(0, 0, 0, 0)
        asm_page_layout.setSpacing(8)

        asm_card = CardFrame()
        asm_layout = QVBoxLayout(asm_card)
        asm_layout.setContentsMargins(10, 10, 10, 10)
        asm_layout.setSpacing(5)
        asm_layout.addWidget(QLabel("汇编脚本 (Assemble)"))

        self.asm_input = self.create_path_selector(
            asm_layout,
            "输入 ASM/目录:",
            on_change=self.auto_fill_asm_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.asm_output = self.create_path_selector(
            asm_layout,
            "输出脚本/目录:",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.asm_script_encoding = self.create_combo_row(
            asm_layout,
            "输出脚本编码:",
            ENCODING_ITEMS,
            editable=True,
            default_text=SCRIPT_ENCODING,
            prioritize_cp932=True,
        )
        self.asm_recursive, self.asm_keep_structure = self.create_batch_options_row(
            asm_layout,
            recursive_checked=True,
            keep_structure_checked=True,
        )
        asm_tip = QLabel("读取 `.asm.txt` 固定为 UTF-8；回编后的原始脚本字符串编码可配置。")
        asm_tip.setObjectName("TipLabel")
        asm_tip.setWordWrap(True)
        asm_layout.addWidget(asm_tip)

        asm_btn = ModernButton("开始汇编", is_primary=True)
        asm_btn.clicked.connect(self.run_asm_task)
        asm_layout.addWidget(asm_btn)
        asm_page_layout.addWidget(asm_card)
        asm_page_layout.addStretch(1)
        self.import_stack.addWidget(asm_page)

        json_inject_page = QWidget()
        json_inject_page_layout = QVBoxLayout(json_inject_page)
        json_inject_page_layout.setContentsMargins(0, 0, 0, 0)
        json_inject_page_layout.setSpacing(8)

        json_inject_card = CardFrame()
        json_inject_layout = QVBoxLayout(json_inject_card)
        json_inject_layout.setContentsMargins(10, 10, 10, 10)
        json_inject_layout.setSpacing(5)
        json_inject_layout.addWidget(QLabel("根据 JSON 生成脚本 (Inject JSON)"))

        self.json_inject_input = self.create_path_selector(
            json_inject_layout,
            "原始脚本/目录:",
            on_change=self.auto_fill_json_inject_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.json_inject_json = self.create_path_selector(
            json_inject_layout,
            "翻译 JSON/目录:",
            add_file_button=True,
            add_dir_button=True,
        )
        self.json_inject_output = self.create_path_selector(
            json_inject_layout,
            "输出脚本/目录:",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.json_inject_input_script_encoding, self.json_inject_output_script_encoding = self.create_dual_combo_row(
            json_inject_layout,
            "输入脚本编码:",
            "输出脚本编码:",
            ENCODING_ITEMS,
            editable=True,
        )
        self.json_inject_recursive, self.json_inject_keep_structure = self.create_batch_options_row(
            json_inject_layout,
            recursive_checked=True,
            keep_structure_checked=True,
        )
        self.json_inject_copy_extra_files = QCheckBox("复制原目录无关文件")
        self.json_inject_copy_extra_files.setChecked(True)
        json_inject_layout.addWidget(self.json_inject_copy_extra_files)
        json_inject_tip = QLabel("JSON 固定按 UTF-8 读取；回注时会自动处理连续 TEXT 的 `\\n` 数量变化，并重算后续跳转偏移。")
        json_inject_tip.setObjectName("TipLabel")
        json_inject_tip.setWordWrap(True)
        json_inject_layout.addWidget(json_inject_tip)

        json_inject_btn = ModernButton("开始 JSON 回注", is_primary=True)
        json_inject_btn.clicked.connect(self.run_json_inject_task)
        json_inject_layout.addWidget(json_inject_btn)
        json_inject_page_layout.addWidget(json_inject_card)
        json_inject_page_layout.addStretch(1)
        self.import_stack.addWidget(json_inject_page)

        self.import_mode_combo.currentIndexChanged.connect(self.import_stack.setCurrentIndex)
        import_page_layout.addWidget(self.import_stack)
        sub_tabs.addTab(import_page, "生成")

        layout.addWidget(sub_tabs)
        self.tabs.addTab(tab, "MYA ASM")

    def setup_myu_asm_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        sub_tabs = QTabWidget()

        disasm_page = QWidget()
        disasm_page_layout = QVBoxLayout(disasm_page)
        disasm_page_layout.setContentsMargins(0, 0, 0, 0)
        disasm_page_layout.setSpacing(8)

        disasm_card = CardFrame()
        disasm_layout = QVBoxLayout(disasm_card)
        disasm_layout.setContentsMargins(10, 10, 10, 10)
        disasm_layout.setSpacing(5)
        disasm_layout.addWidget(QLabel("MYU 反汇编脚本 (Disassemble)"))

        self.myu_disasm_input = self.create_path_selector(
            disasm_layout,
            "输入脚本/目录:",
            on_change=self.auto_fill_myu_disasm_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.myu_disasm_output = self.create_path_selector(
            disasm_layout,
            "输出 ASM/目录:",
            add_file_button=False,
            add_dir_button=True,
            is_output=True,
        )
        self.myu_disasm_recursive, self.myu_disasm_keep_structure = self.create_batch_options_row(
            disasm_layout,
            recursive_checked=True,
            keep_structure_checked=True,
        )
        disasm_tip = QLabel("MYU 脚本字符串固定使用 UTF-16LE；中间 `.asm.txt` 使用 UTF-8。")
        disasm_tip.setObjectName("TipLabel")
        disasm_tip.setWordWrap(True)
        disasm_layout.addWidget(disasm_tip)

        disasm_btn = ModernButton("开始反汇编", is_primary=True)
        disasm_btn.clicked.connect(self.run_myu_disasm_task)
        disasm_layout.addWidget(disasm_btn)
        disasm_page_layout.addWidget(disasm_card)
        disasm_page_layout.addStretch(1)
        sub_tabs.addTab(disasm_page, "反汇编")

        asm_page = QWidget()
        asm_page_layout = QVBoxLayout(asm_page)
        asm_page_layout.setContentsMargins(0, 0, 0, 0)
        asm_page_layout.setSpacing(8)

        asm_card = CardFrame()
        asm_layout = QVBoxLayout(asm_card)
        asm_layout.setContentsMargins(10, 10, 10, 10)
        asm_layout.setSpacing(5)
        asm_layout.addWidget(QLabel("MYU 汇编脚本 (Assemble)"))

        self.myu_asm_input = self.create_path_selector(
            asm_layout,
            "输入 ASM/目录:",
            on_change=self.auto_fill_myu_asm_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.myu_asm_output = self.create_path_selector(
            asm_layout,
            "输出脚本/目录:",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.myu_asm_recursive, self.myu_asm_keep_structure = self.create_batch_options_row(
            asm_layout,
            recursive_checked=True,
            keep_structure_checked=True,
        )
        asm_tip = QLabel("读取 `.asm.txt` 固定为 UTF-8；回编脚本字符串固定使用 UTF-16LE。")
        asm_tip.setObjectName("TipLabel")
        asm_tip.setWordWrap(True)
        asm_layout.addWidget(asm_tip)

        asm_btn = ModernButton("开始汇编", is_primary=True)
        asm_btn.clicked.connect(self.run_myu_asm_task)
        asm_layout.addWidget(asm_btn)
        asm_page_layout.addWidget(asm_card)
        asm_page_layout.addStretch(1)
        sub_tabs.addTab(asm_page, "汇编")

        layout.addWidget(sub_tabs)
        self.tabs.addTab(tab, "MYU ASM")

    def setup_archive_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        sub_tabs = QTabWidget()

        extract_page = QWidget()
        extract_page_layout = QVBoxLayout(extract_page)
        extract_page_layout.setContentsMargins(0, 0, 0, 0)
        extract_page_layout.setSpacing(8)

        extract_card = CardFrame()
        extract_layout = QVBoxLayout(extract_card)
        extract_layout.setContentsMargins(10, 10, 10, 10)
        extract_layout.setSpacing(5)
        extract_layout.addWidget(QLabel("解包 (Extract)"))

        self.arc_extract_input = self.create_path_selector(
            extract_layout,
            "输入归档/目录:",
            on_change=self.auto_fill_archive_extract,
            add_file_button=True,
            add_dir_button=True,
        )
        self.arc_extract_output = self.create_path_selector(
            extract_layout,
            "输出目录:",
            add_file_button=False,
            add_dir_button=True,
            is_output=True,
        )
        self.arc_extract_recursive, self.arc_extract_keep_structure = self.create_batch_options_row(
            extract_layout,
            recursive_checked=True,
            keep_structure_checked=True,
        )
        extract_tip = QLabel("单文件解包时输出到一个目录；目录批量解包时可递归查找归档，并按需要保持目录结构。")
        extract_tip.setObjectName("TipLabel")
        extract_tip.setWordWrap(True)
        extract_layout.addWidget(extract_tip)

        extract_btn = ModernButton("开始解包", is_primary=True)
        extract_btn.clicked.connect(self.run_archive_extract_task)
        extract_layout.addWidget(extract_btn)
        extract_page_layout.addWidget(extract_card)
        extract_page_layout.addStretch(1)
        sub_tabs.addTab(extract_page, "解包")

        pack_page = QWidget()
        pack_page_layout = QVBoxLayout(pack_page)
        pack_page_layout.setContentsMargins(0, 0, 0, 0)
        pack_page_layout.setSpacing(8)

        pack_card = CardFrame()
        pack_layout = QVBoxLayout(pack_card)
        pack_layout.setContentsMargins(10, 10, 10, 10)
        pack_layout.setSpacing(5)
        pack_layout.addWidget(QLabel("封包 (Pack)"))

        self.arc_pack_mode = self.create_combo_row(
            pack_layout,
            "封包模式:",
            [
                "1 - AOIMY01/Unicode",
                "2 - AOIMY01/ANSI",
                "3 - AOIBX9/ANSI",
                "4 - VFS",
            ],
            editable=False,
        )
        self.arc_pack_mode.currentIndexChanged.connect(self.auto_fill_archive_pack)

        self.arc_pack_input = self.create_path_selector(
            pack_layout,
            "输入目录:",
            on_change=self.auto_fill_archive_pack,
            add_file_button=False,
            add_dir_button=True,
        )
        self.arc_pack_output = self.create_path_selector(
            pack_layout,
            "输出归档/目录:",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.arc_pack_batch = QCheckBox("把输入目录下每个子目录分别封包")
        self.arc_pack_batch.toggled.connect(self.auto_fill_archive_pack)
        pack_layout.addWidget(self.arc_pack_batch)
        self.arc_pack_recursive, self.arc_pack_keep_structure = self.create_batch_options_row(
            pack_layout,
            recursive_checked=False,
            keep_structure_checked=False,
        )
        pack_tip = QLabel("单次封包时会按模式默认输出为 box 或 vfs 文件；批量封包时可递归查找子目录，并按需要保留目录结构。")
        pack_tip.setObjectName("TipLabel")
        pack_tip.setWordWrap(True)
        pack_layout.addWidget(pack_tip)

        pack_btn = ModernButton("开始封包", is_primary=True)
        pack_btn.clicked.connect(self.run_archive_pack_task)
        pack_layout.addWidget(pack_btn)
        pack_page_layout.addWidget(pack_card)
        pack_page_layout.addStretch(1)
        sub_tabs.addTab(pack_page, "封包")

        layout.addWidget(sub_tabs)
        self.tabs.addTab(tab, "归档工具")

    def setup_iph_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        sub_tabs = QTabWidget()

        decode_page = QWidget()
        decode_page_layout = QVBoxLayout(decode_page)
        decode_page_layout.setContentsMargins(0, 0, 0, 0)
        decode_page_layout.setSpacing(8)

        decode_card = CardFrame()
        decode_layout = QVBoxLayout(decode_card)
        decode_layout.setContentsMargins(10, 10, 10, 10)
        decode_layout.setSpacing(5)
        decode_layout.addWidget(QLabel("IPH 转 PNG"))

        self.iph_decode_input = self.create_path_selector(
            decode_layout,
            "输入 IPH/目录:",
            on_change=self.auto_fill_iph_decode_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.iph_decode_output = self.create_path_selector(
            decode_layout,
            "输出目录:",
            add_file_button=False,
            add_dir_button=True,
            is_output=True,
        )
        self.iph_decode_recursive, self.iph_decode_keep_structure = self.create_batch_options_row(
            decode_layout,
            recursive_checked=True,
            keep_structure_checked=True,
        )
        decode_tip = QLabel("支持单文件或目录批处理；目录模式下可递归扫描子目录，并可选择是否保留原始相对路径输出 PNG 与 `.iph.json`。")
        decode_tip.setObjectName("TipLabel")
        decode_tip.setWordWrap(True)
        decode_layout.addWidget(decode_tip)

        decode_btn = ModernButton("开始转 PNG", is_primary=True)
        decode_btn.clicked.connect(self.run_iph_decode_task)
        decode_layout.addWidget(decode_btn)
        decode_page_layout.addWidget(decode_card)
        decode_page_layout.addStretch(1)
        sub_tabs.addTab(decode_page, "导出 PNG")

        encode_page = QWidget()
        encode_page_layout = QVBoxLayout(encode_page)
        encode_page_layout.setContentsMargins(0, 0, 0, 0)
        encode_page_layout.setSpacing(8)

        encode_card = CardFrame()
        encode_layout = QVBoxLayout(encode_card)
        encode_layout.setContentsMargins(10, 10, 10, 10)
        encode_layout.setSpacing(5)
        encode_layout.addWidget(QLabel("PNG 转 IPH"))

        self.iph_encode_input = self.create_path_selector(
            encode_layout,
            "输入 PNG/目录:",
            on_change=self.auto_fill_iph_encode_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.iph_encode_output = self.create_path_selector(
            encode_layout,
            "输出目录:",
            add_file_button=False,
            add_dir_button=True,
            is_output=True,
        )
        self.iph_encode_recursive, self.iph_encode_keep_structure = self.create_batch_options_row(
            encode_layout,
            recursive_checked=True,
            keep_structure_checked=True,
        )
        encode_tip = QLabel("支持批量把 PNG 回写为 IPH；若存在同名 `.iph.json`，会自动读取其中的头部元数据，并可选择是否保留目录结构。")
        encode_tip.setObjectName("TipLabel")
        encode_tip.setWordWrap(True)
        encode_layout.addWidget(encode_tip)

        encode_btn = ModernButton("开始转 IPH", is_primary=True)
        encode_btn.clicked.connect(self.run_iph_encode_task)
        encode_layout.addWidget(encode_btn)
        encode_page_layout.addWidget(encode_card)
        encode_page_layout.addStretch(1)
        sub_tabs.addTab(encode_page, "生成 IPH")

        layout.addWidget(sub_tabs)
        self.tabs.addTab(tab, "IPH 工具")

    def setup_obj_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        sub_tabs = QTabWidget()

        # ---- 导出 ----
        dump_page = QWidget()
        dump_page_layout = QVBoxLayout(dump_page)
        dump_page_layout.setContentsMargins(0, 0, 0, 0)
        dump_page_layout.setSpacing(8)

        dump_card = CardFrame()
        dump_layout = QVBoxLayout(dump_card)
        dump_layout.setContentsMargins(10, 10, 10, 10)
        dump_layout.setSpacing(5)
        dump_layout.addWidget(QLabel("OBJ 导出 JSON"))

        self.obj_input = self.create_path_selector(
            dump_layout,
            "输入 OBJ/目录:",
            on_change=self.auto_fill_obj_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.obj_output = self.create_path_selector(
            dump_layout,
            "输出 JSON/目录:",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.obj_recursive, _ = self.create_batch_options_row(
            dump_layout,
            recursive_checked=True,
            keep_structure_checked=False,
        )
        dump_tip = QLabel("OBJ 内部使用 XOR 0xFF 反码存储；字符串固定为 UTF-16LE。回封时会从 records 自动重建字符串表。")
        dump_tip.setObjectName("TipLabel")
        dump_tip.setWordWrap(True)
        dump_layout.addWidget(dump_tip)

        dump_btn = ModernButton("开始导出", is_primary=True)
        dump_btn.clicked.connect(self.run_obj_dump_task)
        dump_layout.addWidget(dump_btn)
        dump_page_layout.addWidget(dump_card)
        dump_page_layout.addStretch(1)
        sub_tabs.addTab(dump_page, "导出 JSON")

        # ---- 注入 ----
        inject_page = QWidget()
        inject_page_layout = QVBoxLayout(inject_page)
        inject_page_layout.setContentsMargins(0, 0, 0, 0)
        inject_page_layout.setSpacing(8)

        inject_card = CardFrame()
        inject_layout = QVBoxLayout(inject_card)
        inject_layout.setContentsMargins(10, 10, 10, 10)
        inject_layout.setSpacing(5)
        inject_layout.addWidget(QLabel("JSON 注入 OBJ"))

        self.obj_inject_input = self.create_path_selector(
            inject_layout,
            "输入 JSON/目录:",
            on_change=self.auto_fill_obj_inject_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.obj_inject_output = self.create_path_selector(
            inject_layout,
            "输出 OBJ/目录:",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.obj_inject_recursive, _ = self.create_batch_options_row(
            inject_layout,
            recursive_checked=True,
            keep_structure_checked=False,
        )
        inject_tip = QLabel("字符串表会从 records 自动重建，并按原格式追加四个空格、填充到 8 字节边界。")
        inject_tip.setObjectName("TipLabel")
        inject_tip.setWordWrap(True)
        inject_layout.addWidget(inject_tip)

        inject_btn = ModernButton("开始注入", is_primary=True)
        inject_btn.clicked.connect(self.run_obj_inject_task)
        inject_layout.addWidget(inject_btn)
        inject_page_layout.addWidget(inject_card)
        inject_page_layout.addStretch(1)
        sub_tabs.addTab(inject_page, "注入 OBJ")

        layout.addWidget(sub_tabs)
        self.tabs.addTab(tab, "OBJ 工具")

    def create_path_selector(
        self,
        parent_layout,
        label_text=None,
        on_change=None,
        add_file_button=False,
        add_dir_button=True,
        is_output=False,
    ):
        row = QHBoxLayout()
        row.setSpacing(5)
        row.setContentsMargins(0, 0, 0, 0)

        if label_text:
            row.addWidget(QLabel(label_text))

        edit = DragDropLineEdit(is_folder=True)
        if on_change:
            edit.textChanged.connect(on_change)
        row.addWidget(edit)

        if add_file_button:
            btn_file = ModernButton("文件")
            btn_file.setFixedWidth(50)
            if is_output:
                btn_file.clicked.connect(lambda: self.browse_output_file(edit))
            else:
                btn_file.clicked.connect(lambda: self.browse_input_file(edit, on_change))
            row.addWidget(btn_file)

        if add_dir_button:
            btn_dir = ModernButton("目录")
            btn_dir.setFixedWidth(50)
            btn_dir.clicked.connect(lambda: self.browse_folder(edit, on_change))
            row.addWidget(btn_dir)

        parent_layout.addLayout(row)
        return edit

    def create_batch_options_row(self, parent_layout, recursive_checked=True, keep_structure_checked=True):
        row = QHBoxLayout()
        row.setSpacing(12)
        row.setContentsMargins(0, 0, 0, 0)

        recursive_box = QCheckBox("递归子目录")
        recursive_box.setChecked(recursive_checked)
        row.addWidget(recursive_box)

        keep_structure_box = QCheckBox("输出保持目录结构")
        keep_structure_box.setChecked(keep_structure_checked)
        row.addWidget(keep_structure_box)

        row.addStretch(1)
        parent_layout.addLayout(row)
        return recursive_box, keep_structure_box

    def _prepare_combo_items(self, items, prioritize_cp932=False):
        final_items = []
        for enc in items or []:
            text = str(enc).strip()
            if text and text not in final_items:
                final_items.append(text)
        if prioritize_cp932:
            if "cp932" in final_items:
                final_items.remove("cp932")
            final_items.insert(0, "cp932")
        return final_items

    def create_combo_row(self, parent_layout, label_text, items, editable=True, default_text="", prioritize_cp932=False):
        row = QHBoxLayout()
        row.setSpacing(3)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(label_text))

        combo = QComboBox()
        combo.setEditable(editable)
        combo.addItems(self._prepare_combo_items(items, prioritize_cp932=prioritize_cp932))
        combo.setMinimumWidth(160)
        combo.setMaximumWidth(230)
        combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if default_text:
            combo.setCurrentText(default_text)
        row.addWidget(combo)
        row.addStretch(1)
        parent_layout.addLayout(row)
        return combo

    def create_dual_combo_row(
        self,
        parent_layout,
        left_label,
        right_label,
        items,
        editable=True,
        left_items=None,
        right_items=None,
    ):
        left_item_list = self._prepare_combo_items(left_items or items, prioritize_cp932=True)
        right_item_list = self._prepare_combo_items(right_items or items, prioritize_cp932=True)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.setContentsMargins(0, 0, 0, 0)

        left_wrap = QHBoxLayout()
        left_wrap.setSpacing(3)
        left_wrap.setContentsMargins(0, 0, 0, 0)
        left_wrap.addWidget(QLabel(left_label))
        left_combo = QComboBox()
        left_combo.setEditable(editable)
        left_combo.addItems(left_item_list)
        left_combo.setCurrentText(SCRIPT_ENCODING)
        left_combo.setMinimumWidth(130)
        left_combo.setMaximumWidth(180)
        left_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        left_wrap.addWidget(left_combo)
        left_wrap.addStretch(1)

        right_wrap = QHBoxLayout()
        right_wrap.setSpacing(3)
        right_wrap.setContentsMargins(0, 0, 0, 0)
        right_wrap.addWidget(QLabel(right_label))
        right_combo = QComboBox()
        right_combo.setEditable(editable)
        right_combo.addItems(right_item_list)
        right_combo.setCurrentText(SCRIPT_ENCODING)
        right_combo.setMinimumWidth(130)
        right_combo.setMaximumWidth(180)
        right_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        right_wrap.addWidget(right_combo)
        right_wrap.addStretch(1)

        row.addLayout(left_wrap, 1)
        row.addLayout(right_wrap, 1)
        parent_layout.addLayout(row)
        return left_combo, right_combo

    def browse_folder(self, line_edit, on_change=None):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            line_edit.setText(os.path.normpath(path))
            if on_change:
                on_change()

    def browse_input_file(self, line_edit, on_change=None):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if path:
            line_edit.setText(os.path.normpath(path))
            if on_change:
                on_change()

    def browse_output_file(self, line_edit):
        initial_path = line_edit.text().strip()
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", initial_path)
        if path:
            line_edit.setText(os.path.normpath(path))

    def log_view_clear(self):
        self.log_view.clear()

    def append_log(self, text):
        cursor = self.log_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

    def auto_fill_disasm_output(self):
        path = self.disasm_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            suggested = input_path.parent / f"{input_path.stem}_asm"
        else:
            suggested = input_path.parent / f"{input_path.name}_asm"
        self.disasm_output.setText(os.path.normpath(str(suggested)))

    def auto_fill_myu_disasm_output(self):
        path = self.myu_disasm_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            suggested = input_path.parent / f"{input_path.stem}_asm"
        else:
            suggested = input_path.parent / f"{input_path.name}_asm"
        self.myu_disasm_output.setText(os.path.normpath(str(suggested)))

    def auto_fill_myu_asm_output(self):
        path = self.myu_asm_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            base_name = input_path.name[:-8] if input_path.name.lower().endswith(".asm.txt") else input_path.stem
            suggested = input_path.parent / f"{base_name}_build.txt"
        else:
            suggested = input_path.parent / f"{input_path.name}_build"
        self.myu_asm_output.setText(os.path.normpath(str(suggested)))

    def auto_fill_archive_extract(self):
        path = self.arc_extract_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            suggested = input_path.parent / f"{input_path.stem}_out"
        else:
            suggested = input_path.parent / f"{input_path.name}_out"
        self.arc_extract_output.setText(os.path.normpath(str(suggested)))

    def auto_fill_archive_pack(self):
        path = self.arc_pack_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        mode_text = self.arc_pack_mode.currentText().strip() if hasattr(self, "arc_pack_mode") else "2 - AOIMY01/ANSI"
        ext = ".vfs" if mode_text.startswith("4") else ".box"
        if self.arc_pack_batch.isChecked():
            suggested = input_path.parent / f"{input_path.name}_pack"
        else:
            suggested = input_path.parent / f"{input_path.name}{ext}"
        self.arc_pack_output.setText(os.path.normpath(str(suggested)))

    def auto_fill_asm_output(self):
        path = self.asm_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            base_name = input_path.name[:-8] if input_path.name.lower().endswith(".asm.txt") else input_path.stem
            suggested = input_path.parent / f"{base_name}_build.txt"
        else:
            suggested = input_path.parent / f"{input_path.name}_build"
        self.asm_output.setText(os.path.normpath(str(suggested)))

    def auto_fill_json_dump_output(self):
        path = self.json_dump_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            suggested = input_path.parent / f"{input_path.name}.json"
        else:
            suggested = input_path.parent / f"{input_path.name}_json"
        self.json_dump_output.setText(os.path.normpath(str(suggested)))

    def auto_fill_json_inject_output(self):
        path = self.json_inject_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            suggested = input_path.parent / f"{input_path.stem}_json_build{input_path.suffix}"
        else:
            suggested = input_path.parent / f"{input_path.name}_json_build"
        self.json_inject_output.setText(os.path.normpath(str(suggested)))

    def auto_fill_iph_decode_output(self):
        path = self.iph_decode_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            suggested = input_path.parent / f"{input_path.stem}_png"
        else:
            suggested = input_path.parent / f"{input_path.name}_png"
        self.iph_decode_output.setText(os.path.normpath(str(suggested)))

    def auto_fill_iph_encode_output(self):
        path = self.iph_encode_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            suggested = input_path.parent / f"{input_path.stem}_iph"
        else:
            suggested = input_path.parent / f"{input_path.name}_iph"
        self.iph_encode_output.setText(os.path.normpath(str(suggested)))

    def start_worker(self, task: dict):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "当前已有任务正在运行，请先等待其完成。")
            return

        self.append_log("\n" + "=" * 60 + "\n")
        self.worker = WorkerThread(task)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def on_finished(self, success, msg):
        self.append_log(f"\n{msg}\n")
        if success:
            QMessageBox.information(self, "完成", msg)
        else:
            QMessageBox.critical(self, "失败", msg)

    def run_disasm_task(self):
        input_path = self.disasm_input.text().strip()
        output_path = self.disasm_output.text().strip()
        if not input_path or not output_path:
            QMessageBox.warning(self, "提示", "请填写输入和输出路径。")
            return
        self.start_worker(
            {
                "kind": "mya_asm_disasm",
                "input_path": input_path,
                "output_path": output_path,
                "script_encoding": self.disasm_script_encoding.currentText().strip() or SCRIPT_ENCODING,
                "recursive": self.disasm_recursive.isChecked(),
                "keep_structure": self.disasm_keep_structure.isChecked(),
            }
        )

    def run_myu_disasm_task(self):
        input_path = self.myu_disasm_input.text().strip()
        output_path = self.myu_disasm_output.text().strip()
        if not input_path or not output_path:
            QMessageBox.warning(self, "提示", "请填写输入和输出路径。")
            return
        self.start_worker(
            {
                "kind": "myu_asm_disasm",
                "input_path": input_path,
                "output_path": output_path,
                "recursive": self.myu_disasm_recursive.isChecked(),
                "keep_structure": self.myu_disasm_keep_structure.isChecked(),
            }
        )

    def run_myu_asm_task(self):
        input_path = self.myu_asm_input.text().strip()
        output_path = self.myu_asm_output.text().strip()
        if not input_path or not output_path:
            QMessageBox.warning(self, "提示", "请填写输入和输出路径。")
            return
        self.start_worker(
            {
                "kind": "myu_asm_asm",
                "input_path": input_path,
                "output_path": output_path,
                "recursive": self.myu_asm_recursive.isChecked(),
                "keep_structure": self.myu_asm_keep_structure.isChecked(),
            }
        )

    def run_archive_extract_task(self):
        input_path = self.arc_extract_input.text().strip()
        output_path = self.arc_extract_output.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择归档文件或目录。")
            return
        self.start_worker(
            {
                "kind": "archive_extract",
                "input_path": input_path,
                "output_path": output_path,
                "recursive": self.arc_extract_recursive.isChecked(),
                "keep_structure": self.arc_extract_keep_structure.isChecked(),
            }
        )

    def run_archive_pack_task(self):
        input_path = self.arc_pack_input.text().strip()
        output_path = self.arc_pack_output.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择输入目录。")
            return
        self.start_worker(
            {
                "kind": "archive_pack",
                "input_path": input_path,
                "output_path": output_path,
                "pack_mode": self.arc_pack_mode.currentText().split(" ", 1)[0],
                "batch_subdirs": self.arc_pack_batch.isChecked(),
                "recursive": self.arc_pack_recursive.isChecked(),
                "keep_structure": self.arc_pack_keep_structure.isChecked(),
            }
        )

    def run_asm_task(self):
        input_path = self.asm_input.text().strip()
        output_path = self.asm_output.text().strip()
        if not input_path or not output_path:
            QMessageBox.warning(self, "提示", "请填写输入和输出路径。")
            return
        self.start_worker(
            {
                "kind": "mya_asm_asm",
                "input_path": input_path,
                "output_path": output_path,
                "script_encoding": self.asm_script_encoding.currentText().strip() or SCRIPT_ENCODING,
                "recursive": self.asm_recursive.isChecked(),
                "keep_structure": self.asm_keep_structure.isChecked(),
            }
        )

    def run_json_dump_task(self):
        input_path = self.json_dump_input.text().strip()
        output_path = self.json_dump_output.text().strip()
        if not input_path or not output_path:
            QMessageBox.warning(self, "提示", "请填写输入和输出路径。")
            return
        self.start_worker(
            {
                "kind": "mya_json_dump",
                "input_path": input_path,
                "output_path": output_path,
                "script_encoding": self.json_dump_script_encoding.currentText().strip() or SCRIPT_ENCODING,
                "recursive": self.json_dump_recursive.isChecked(),
                "keep_structure": self.json_dump_keep_structure.isChecked(),
            }
        )

    def run_json_inject_task(self):
        input_path = self.json_inject_input.text().strip()
        json_path = self.json_inject_json.text().strip()
        output_path = self.json_inject_output.text().strip()
        if not input_path or not json_path or not output_path:
            QMessageBox.warning(self, "提示", "请填写输入、JSON 和输出路径。")
            return
        self.start_worker(
            {
                "kind": "mya_json_inject",
                "input_path": input_path,
                "json_path": json_path,
                "output_path": output_path,
                "input_script_encoding": self.json_inject_input_script_encoding.currentText().strip() or SCRIPT_ENCODING,
                "output_script_encoding": self.json_inject_output_script_encoding.currentText().strip() or SCRIPT_ENCODING,
                "recursive": self.json_inject_recursive.isChecked(),
                "keep_structure": self.json_inject_keep_structure.isChecked(),
                "copy_extra_files": self.json_inject_copy_extra_files.isChecked(),
            }
        )

    def run_iph_decode_task(self):
        input_path = self.iph_decode_input.text().strip()
        output_path = self.iph_decode_output.text().strip()
        if not input_path or not output_path:
            QMessageBox.warning(self, "提示", "请填写输入和输出路径。")
            return
        self.start_worker(
            {
                "kind": "iph_decode",
                "input_path": input_path,
                "output_path": output_path,
                "recursive": self.iph_decode_recursive.isChecked(),
                "keep_structure": self.iph_decode_keep_structure.isChecked(),
            }
        )

    def run_iph_encode_task(self):
        input_path = self.iph_encode_input.text().strip()
        output_path = self.iph_encode_output.text().strip()
        if not input_path or not output_path:
            QMessageBox.warning(self, "提示", "请填写输入和输出路径。")
            return
        self.start_worker(
            {
                "kind": "iph_encode",
                "input_path": input_path,
                "output_path": output_path,
                "recursive": self.iph_encode_recursive.isChecked(),
                "keep_structure": self.iph_encode_keep_structure.isChecked(),
            }
        )

    def auto_fill_obj_output(self):
        path = self.obj_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            suggested = input_path.parent / f"{input_path.name}.json"
        else:
            suggested = input_path.parent / f"{input_path.name}_json"
        self.obj_output.setText(os.path.normpath(str(suggested)))

    def run_obj_dump_task(self):
        input_path = self.obj_input.text().strip()
        output_path = self.obj_output.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择 OBJ 文件或目录。")
            return
        self.start_worker(
            {
                "kind": "obj_dump",
                "input_path": input_path,
                "output_path": output_path,
                "recursive": self.obj_recursive.isChecked(),
            }
        )

    def auto_fill_obj_inject_output(self):
        path = self.obj_inject_input.text().strip()
        if not path:
            return
        input_path = Path(path)
        if input_path.is_file():
            suggested = input_path.parent / f"{input_path.stem}.obj"
        else:
            suggested = input_path.parent / f"{input_path.name}_obj"
        self.obj_inject_output.setText(os.path.normpath(str(suggested)))

    def run_obj_inject_task(self):
        input_path = self.obj_inject_input.text().strip()
        output_path = self.obj_inject_output.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择 JSON 文件或目录。")
            return
        self.start_worker(
            {
                "kind": "obj_inject",
                "input_path": input_path,
                "output_path": output_path,
                "recursive": self.obj_inject_recursive.isChecked(),
            }
        )

    def detect_system_theme(self):
        self.theme_combo.setCurrentText("跟随系统")
        self.apply_theme("跟随系统")

    def _is_system_dark_theme(self):
        if HAS_DARKDETECT:
            try:
                detected = darkdetect.isDark()
                if detected is not None:
                    return bool(detected)
            except Exception:
                pass
        if winreg is not None:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                ) as key:
                    value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(value) == 0
            except Exception:
                pass
        return self.palette().window().color().lightness() < 128

    def _resolve_theme_name(self, theme_name):
        if theme_name == "跟随系统":
            return "现代深色" if self._is_system_dark_theme() else "现代浅色"
        return theme_name

    def apply_theme(self, theme_name):
        real_theme = self._resolve_theme_name(theme_name)

        light_qss = """
        QWidget { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: 10pt; color: #333333; }
        QWidget#MainBackground { background-color: #f5f7fa; }
        QFrame#CardFrame { background-color: #ffffff; border: 1px solid #e1e4e8; border-radius: 8px; }
        QLabel#AppTitle { font-size: 18pt; font-weight: bold; color: #2c3e50; }
        QLineEdit { padding: 8px; border: 1px solid #ced4da; border-radius: 4px; background: #ffffff; color: #333; }
        QLineEdit:focus { border: 1px solid #3498db; }
        QPushButton#SecondaryButton { background-color: #ffffff; border: 1px solid #dcdfe6; border-radius: 4px; color: #606266; padding: 6px 12px; }
        QPushButton#SecondaryButton:hover { border-color: #c6e2ff; color: #409eff; background-color: #ecf5ff; }
        QPushButton#PrimaryButton { background-color: #3498db; border: 1px solid #3498db; border-radius: 4px; color: #ffffff; font-weight: bold; padding: 8px 16px; }
        QPushButton#PrimaryButton:hover { background-color: #5dade2; border-color: #5dade2; }
        QTabWidget::pane { border: 1px solid #e1e4e8; background: #fff; border-radius: 5px; }
        QTabBar::tab { background: #e8ebf0; color: #666; padding: 10px 20px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }
        QTabBar::tab:selected { background: #ffffff; color: #3498db; font-weight: bold; }
        QTextEdit#LogConsole { background-color: #fcfcfc; color: #333333; border: 1px solid #e1e4e8; font-family: 'Consolas', monospace; font-size: 9pt; }
        QWidget#LogHeader { background-color: #f1f1f1; border-bottom: 1px solid #ddd; }
        QComboBox { padding: 4px; color: #333; background: #fff; border: 1px solid #ced4da; border-radius: 4px; }
        QComboBox QAbstractItemView { background-color: #ffffff; color: #333333; selection-background-color: #e6f7ff; selection-color: #333333; }
        QLabel#TipLabel { font-size: 9pt; color: #888888; }
        """

        dark_qss = """
        QWidget { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: 10pt; color: #e0e0e0; }
        QWidget#MainBackground { background-color: #1e1e1e; }
        QFrame#CardFrame { background-color: #2d2d2d; border: 1px solid #444; border-radius: 8px; }
        QLabel { color: #e0e0e0; }
        QLabel#AppTitle { color: #ffffff; font-size: 18pt; font-weight: bold; }
        QLineEdit { background: #1a1a1a; border: 1px solid #555; border-radius: 4px; color: #ffffff; padding: 8px; }
        QLineEdit:focus { border: 1px solid #bb86fc; }
        QPushButton#SecondaryButton { background: #333; border: 1px solid #555; color: #ddd; border-radius: 4px; }
        QPushButton#SecondaryButton:hover { background: #444; border-color: #777; }
        QPushButton#PrimaryButton { background: #bb86fc; border: 1px solid #bb86fc; color: #121212; border-radius: 4px; font-weight:bold; }
        QPushButton#PrimaryButton:hover { background: #d0aaff; }
        QTabWidget::pane { border: 1px solid #444; background: #2d2d2d; }
        QTabBar::tab { background: #1e1e1e; color: #999; padding: 10px 20px; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right:2px;}
        QTabBar::tab:selected { background: #2d2d2d; color: #bb86fc; font-weight:bold; }
        QTextEdit#LogConsole { background-color: #1a1a1a; color: #e0e0e0; border: 1px solid #444; font-family: 'Consolas', monospace; font-size: 9pt; }
        QWidget#LogHeader { background-color: #252525; border-bottom: 1px solid #444; }
        QComboBox { padding: 4px; color: #e0e0e0; background: #333; border: 1px solid #555; border-radius: 4px; }
        QComboBox QAbstractItemView { background-color: #2d2d2d; color: #e0e0e0; selection-background-color: #bb86fc; selection-color: #121212; }
        QLabel#TipLabel { color: #999999; font-size: 9pt; }
        """

        cyber_qss = """
        QWidget { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: 10pt; color: #00ffcc; }
        QWidget#MainBackground { background-color: #0d0d15; }
        QFrame#CardFrame { background-color: #1a1a2e; border: 1px solid #00ffcc; border-radius: 8px; }
        QLabel { color: #00ffcc; }
        QLabel#AppTitle { color: #ff00ff; font-size: 18pt; font-weight: bold; }
        QLineEdit { background: #0f0f1a; border: 1px solid #ff00ff; border-radius: 4px; color: #00ffcc; padding: 8px; }
        QPushButton#SecondaryButton { background: #0b0b19; border: 1px solid #00ffcc; color: #00ffcc; }
        QPushButton#PrimaryButton { background: #ff0055; border: 1px solid #ff0055; color: #ffffff; font-weight: bold; }
        QTabWidget::pane { border: 1px solid #00ffcc; background: #0d0d15; }
        QTabBar::tab { background: #0d0d15; color: #008888; border: 1px solid #004444; padding: 10px; }
        QTabBar::tab:selected { color: #00ffcc; border: 1px solid #00ffcc; }
        QTextEdit#LogConsole { background-color: #0f0f1f; color: #00ffcc; border: 1px solid #00ffcc; }
        QWidget#LogHeader { background-color: #121225; border-bottom: 1px solid #00ffcc; }
        QComboBox { background: #0d0d15; color: #00ffcc; border: 1px solid #00ffcc; }
        QComboBox QAbstractItemView { background-color: #0d0d15; color: #00ffcc; selection-background-color: #ff0055; }
        QLabel#TipLabel { color: #0088aa; font-size: 9pt; }
        """

        if real_theme == "现代深色":
            self.setStyleSheet(dark_qss)
        elif real_theme == "赛博朋克":
            self.setStyleSheet(cyber_qss)
        else:
            self.setStyleSheet(light_qss)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
