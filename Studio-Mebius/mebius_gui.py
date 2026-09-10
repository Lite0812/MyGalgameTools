
from __future__ import annotations

import os
import pathlib
import sys
import traceback
from typing import TYPE_CHECKING

if sys.platform.startswith("win"):
    try:
        import winreg
    except ImportError:
        winreg = None
else:
    winreg = None

if TYPE_CHECKING:
    from PyQt5.QtCore import Qt, QThread, pyqtSignal
    from PyQt5.QtGui import QTextCursor
    from PyQt5.QtWidgets import (
        QApplication,
        QButtonGroup,
        QComboBox,
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QRadioButton,
        QSplitter,
        QStackedWidget,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    QT_VERTICAL = Qt.Vertical
    QT_POINTING_CURSOR = Qt.PointingHandCursor
    QT_END = QTextCursor.End
    QT_NO_FRAME = QFrame.NoFrame
else:
    try:
        from PyQt6.QtCore import Qt, QThread, pyqtSignal
        from PyQt6.QtGui import QTextCursor
        from PyQt6.QtWidgets import (
            QApplication,
            QButtonGroup,
            QComboBox,
            QFileDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QRadioButton,
            QSplitter,
            QStackedWidget,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )

        QT_VERTICAL = Qt.Orientation.Vertical
        QT_POINTING_CURSOR = Qt.CursorShape.PointingHandCursor
        QT_END = QTextCursor.MoveOperation.End
        QT_NO_FRAME = QFrame.Shape.NoFrame
    except ImportError:
        from PyQt5.QtCore import Qt, QThread, pyqtSignal
        from PyQt5.QtGui import QTextCursor
        from PyQt5.QtWidgets import (
            QApplication,
            QButtonGroup,
            QComboBox,
            QFileDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QRadioButton,
            QSplitter,
            QStackedWidget,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )

        QT_VERTICAL = Qt.Vertical
        QT_POINTING_CURSOR = Qt.PointingHandCursor
        QT_END = QTextCursor.End
        QT_NO_FRAME = QFrame.NoFrame

try:
    import darkdetect

    HAS_DARKDETECT = True
except ImportError:
    HAS_DARKDETECT = False

try:
    import mebius_dialog_json
    import mebius_dialog_txt
    import mebius_txt_asm
    import mebius_txt_disasm
    import mebius_txt_op
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import mebius_dialog_json
    import mebius_dialog_txt
    import mebius_txt_asm
    import mebius_txt_disasm
    import mebius_txt_op


TEXT_ENCODING_CHOICES = [
    mebius_txt_op.DEFAULT_TEXT_ENCODING,
    "shift_jis",
    "utf-8",
    "gbk",
    "big5",
]


class CardFrame(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("CardFrame")
        self.setFrameShape(QT_NO_FRAME)


class ModernButton(QPushButton):
    def __init__(self, text: str, is_primary: bool = False) -> None:
        super().__init__(text)
        self.setCursor(QT_POINTING_CURSOR)
        self.setObjectName("PrimaryButton" if is_primary else "SecondaryButton")
        self.setMinimumHeight(30)


class DragDropLineEdit(QLineEdit):
    def __init__(self, parent: QWidget | None = None, is_folder: bool = False) -> None:
        super().__init__(parent)
        self.is_folder = is_folder
        self.setAcceptDrops(True)
        self.setPlaceholderText("可直接拖入文件或文件夹..." if is_folder else "可直接拖入文件...")

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return
        path = urls[0].toLocalFile()
        if path:
            self.setText(os.path.normpath(path))
            event.acceptProposedAction()
            return
        event.ignore()


def default_disassemble_output(input_path: str) -> str:
    if os.path.isdir(input_path):
        return os.path.normpath(input_path) + "_out"
    return input_path + ".asm.txt"


def default_assemble_output(input_path: str) -> str:
    if os.path.isdir(input_path):
        return os.path.normpath(input_path) + "_build"
    if input_path.endswith(".asm.txt"):
        return input_path[:-8]
    return os.path.splitext(input_path)[0]


def default_extract_output(input_path: str, suffix: str) -> str:
    if os.path.isdir(input_path):
        return os.path.normpath(input_path) + "_out"
    return os.path.splitext(input_path)[0] + suffix


def imported_output_name(source: pathlib.Path, suffix: str) -> str:
    return f"{source.stem}{suffix}{source.suffix}"


def default_import_output(input_path: str, suffix: str) -> str:
    if os.path.isdir(input_path):
        return os.path.normpath(input_path) + "_build"
    source = pathlib.Path(input_path)
    return str(source.with_name(imported_output_name(source, suffix)))


class WorkerThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(
        self,
        mode: str,
        input_path: str = "",
        output_path: str = "",
        text_encoding: str = mebius_txt_op.DEFAULT_TEXT_ENCODING,
        dialog_path: str = "",
        source_encoding: str | None = None,
    ) -> None:
        super().__init__()
        self.mode = mode
        self.input_path = input_path.strip()
        self.output_path = output_path.strip()
        self.dialog_path = dialog_path.strip()
        self.text_encoding = mebius_txt_op.normalize_encoding(text_encoding)
        self.source_encoding = mebius_txt_op.normalize_encoding(source_encoding or text_encoding)
        self._reset_stats()

    def _reset_stats(self) -> None:
        self.stats = {
            "processed": 0,
            "success": 0,
            "failed": 0,
        }

    def _log(self, text: str) -> None:
        self.log_signal.emit(text)

    def _collect_files(self) -> list[pathlib.Path]:
        root = pathlib.Path(self.input_path)
        if self.mode in {"disassemble", "json_extract", "txt_extract", "json_import", "txt_import"}:
            return sorted(
                path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".txt"
            )
        if self.mode == "assemble":
            return sorted(
                path for path in root.rglob("*") if path.is_file() and path.name.lower().endswith(".asm.txt")
            )
        return []

    def _is_import_mode(self) -> bool:
        return self.mode in {"json_import", "txt_import"}

    def _dialog_extension(self) -> str:
        if self.mode == "json_import":
            return ".json"
        if self.mode == "txt_import":
            return ".txt"
        raise ValueError(f"dialog extension is not defined for mode: {self.mode}")

    def _default_output_root(self) -> pathlib.Path:
        if self.mode == "disassemble":
            return pathlib.Path(default_disassemble_output(self.input_path))
        if self.mode == "assemble":
            return pathlib.Path(default_assemble_output(self.input_path))
        if self.mode == "json_extract":
            return pathlib.Path(default_extract_output(self.input_path, ".json"))
        if self.mode == "txt_extract":
            return pathlib.Path(default_extract_output(self.input_path, ".txt"))
        if self.mode == "json_import":
            return pathlib.Path(default_import_output(self.input_path, "_jsonimp"))
        if self.mode == "txt_import":
            return pathlib.Path(default_import_output(self.input_path, "_txtimp"))
        raise ValueError(f"unsupported mode: {self.mode}")

    def _single_target_path(self, source: pathlib.Path) -> pathlib.Path:
        if self.output_path:
            output = pathlib.Path(self.output_path)
            if output.exists() and output.is_dir():
                if self.mode == "disassemble":
                    return output / f"{source.name}.asm.txt"
                if self.mode == "assemble":
                    name = source.name[:-8] if source.name.endswith(".asm.txt") else source.stem
                    return output / name
                if self.mode == "json_extract":
                    return output / f"{source.stem}.json"
                if self.mode == "txt_extract":
                    return output / f"{source.stem}.txt"
                if self.mode == "json_import":
                    return output / imported_output_name(source, "_jsonimp")
                if self.mode == "txt_import":
                    return output / imported_output_name(source, "_txtimp")
            return output
        if self.mode == "disassemble":
            return source.with_suffix(source.suffix + ".asm.txt")
        if self.mode == "assemble":
            if source.name.endswith(".asm.txt"):
                return source.with_name(source.name[:-8])
            return source.with_suffix("")
        if self.mode == "json_extract":
            return source.with_suffix(".json")
        if self.mode == "txt_extract":
            return source.with_suffix(".txt")
        if self.mode == "json_import":
            return source.with_name(imported_output_name(source, "_jsonimp"))
        if self.mode == "txt_import":
            return source.with_name(imported_output_name(source, "_txtimp"))
        raise ValueError(f"unsupported mode: {self.mode}")

    def _batch_target_path(self, source_root: pathlib.Path, output_root: pathlib.Path, source: pathlib.Path) -> pathlib.Path:
        relative = source.relative_to(source_root)
        if self.mode == "disassemble":
            return output_root / relative.parent / f"{relative.name}.asm.txt"
        if self.mode == "assemble":
            name = relative.name[:-8] if relative.name.endswith(".asm.txt") else relative.stem
            return output_root / relative.parent / name
        if self.mode == "json_extract":
            return output_root / relative.parent / f"{relative.stem}.json"
        if self.mode == "txt_extract":
            return output_root / relative.parent / f"{relative.stem}.txt"
        if self.mode in {"json_import", "txt_import"}:
            return output_root / relative
        raise ValueError(f"unsupported mode: {self.mode}")

    def _single_dialog_path(self, source: pathlib.Path) -> pathlib.Path | None:
        if not self._is_import_mode():
            return None
        if not self.dialog_path:
            need_type = "JSON" if self.mode == "json_import" else "TXT"
            raise ValueError(f"请先选择{need_type}输入路径")
        dialog_path = pathlib.Path(self.dialog_path)
        if dialog_path.is_dir():
            return dialog_path / f"{source.stem}{self._dialog_extension()}"
        return dialog_path

    def _batch_dialog_path(self, source_root: pathlib.Path, source: pathlib.Path, total: int) -> pathlib.Path | None:
        if not self._is_import_mode():
            return None
        if not self.dialog_path:
            need_type = "JSON" if self.mode == "json_import" else "TXT"
            raise ValueError(f"请先选择{need_type}输入路径")
        dialog_path = pathlib.Path(self.dialog_path)
        if dialog_path.is_dir():
            relative = source.relative_to(source_root)
            return dialog_path / relative.parent / f"{relative.stem}{self._dialog_extension()}"
        if total > 1:
            raise ValueError("目录批处理导回时，文本输入路径必须是目录")
        return dialog_path

    def _process_file(
        self,
        source: pathlib.Path,
        target: pathlib.Path,
        dialog_source: pathlib.Path | None = None,
    ) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "disassemble":
            mebius_txt_disasm.disassemble_file(source, target, text_encoding=self.text_encoding)
            return f"输出: {target}"
        if self.mode == "assemble":
            mebius_txt_asm.assemble_file(source, target, text_encoding=self.text_encoding)
            return f"输出: {target}"
        if self.mode == "json_extract":
            count = mebius_dialog_json.extract_dialog_json_from_script(source, target, encoding=self.text_encoding)
            return f"已提取对话 {count} 条 -> {target}"
        if self.mode == "txt_extract":
            count, units = mebius_dialog_txt.extract_dialog_txt_from_script(source, target, encoding=self.text_encoding)
            return f"已提取对话 {count} 条，文本单元 {units} 条 -> {target}"
        if self.mode == "json_import":
            if dialog_source is None or not dialog_source.is_file():
                raise ValueError(f"JSON 文件不存在: {dialog_source}")
            count, applied = mebius_dialog_json.import_dialog_json_to_script(
                source,
                dialog_source,
                target,
                encoding=self.text_encoding,
                source_encoding=self.source_encoding,
            )
            return f"已导回对话 {applied} 项（共 {count} 条）-> {target}"
        if self.mode == "txt_import":
            if dialog_source is None or not dialog_source.is_file():
                raise ValueError(f"TXT 文件不存在: {dialog_source}")
            count, applied, units = mebius_dialog_txt.import_dialog_txt_to_script(
                source,
                dialog_source,
                target,
                encoding=self.text_encoding,
                source_encoding=self.source_encoding,
            )
            return f"已导回文本 {applied} 项（对话 {count} 条 / 文本单元 {units} 条）-> {target}"
        raise ValueError(f"unsupported mode: {self.mode}")

    def _print_summary(self) -> str:
        lines = [
            "处理汇总:",
            f"  文件总数: {self.stats['processed']}",
            f"  成功: {self.stats['success']}",
            f"  失败: {self.stats['failed']}",
        ]
        text = "\n".join(lines)
        self._log(text)
        return text

    def run(self) -> None:  # type: ignore[override]
        self._reset_stats()
        try:
            if self.mode in {"disassemble", "assemble", "json_extract", "txt_extract", "json_import", "txt_import"}:
                if not self.input_path:
                    raise ValueError("请先选择输入路径")
                source = pathlib.Path(self.input_path)
                if source.is_dir():
                    files = self._collect_files()
                    output_root = pathlib.Path(self.output_path) if self.output_path else self._default_output_root()
                    self._log(f"共发现 {len(files)} 个文件待处理。")
                    if not files:
                        summary = self._print_summary()
                        self.finished_signal.emit(True, summary)
                        return
                    for index, file_path in enumerate(files, 1):
                        self.stats["processed"] += 1
                        target = self._batch_target_path(source, output_root, file_path)
                        dialog_file = self._batch_dialog_path(source, file_path, len(files))
                        self._log(f"[{index}/{len(files)}] 正在处理: {file_path.name}")
                        try:
                            message = self._process_file(file_path, target, dialog_file)
                            self.stats["success"] += 1
                            self._log(f"    {message}")
                        except Exception as exc:
                            self.stats["failed"] += 1
                            self._log(f"    失败: {file_path}\n{exc}")
                    summary = self._print_summary()
                    ok = self.stats["failed"] == 0
                    self.finished_signal.emit(ok, summary)
                    return

                self.stats["processed"] += 1
                target = self._single_target_path(source)
                dialog_file = self._single_dialog_path(source)
                self._log(f"正在处理: {source.name}")
                message = self._process_file(source, target, dialog_file)
                self.stats["success"] += 1
                summary = self._print_summary() + f"\n{message}"
                self.finished_signal.emit(True, summary)
                return

            raise ValueError(f"未知模式: {self.mode}")
        except Exception:
            self.stats["failed"] += 1
            self._log(traceback.format_exc())
            summary = self._print_summary()
            self.finished_signal.emit(False, summary)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MEBIUS TXT Tools GUI")
        self.resize(750, 700)
        self.setObjectName("MainBackground")
        self.worker: WorkerThread | None = None
        self.init_ui()
        self.detect_system_theme()

    def init_ui(self) -> None:
        central_widget = QWidget()
        central_widget.setObjectName("MainBackground")
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)

        header_layout = QHBoxLayout()
        title_label = QLabel("MEBIUS TXT Tools")
        title_label.setObjectName("AppTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["跟随系统", "现代浅色", "现代深色", "赛博朋克"])
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        self.theme_combo.setFixedWidth(120)
        header_layout.addWidget(self.theme_combo)
        main_layout.addLayout(header_layout)

        splitter = QSplitter(QT_VERTICAL)
        splitter.setHandleWidth(2)

        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        self.setup_extract_tab()
        self.setup_build_tab()

        log_card = CardFrame()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        log_header = QWidget()
        log_header.setObjectName("LogHeader")
        log_header_layout = QHBoxLayout(log_header)
        log_header_layout.setContentsMargins(10, 5, 10, 5)
        log_header_layout.addWidget(QLabel("📝 运行日志 (Log)"))
        log_header_layout.addStretch()
        clear_btn = QPushButton("清除")
        clear_btn.setCursor(QT_POINTING_CURSOR)
        clear_btn.setStyleSheet("border:none; font-weight:bold; color: #888;")
        clear_btn.clicked.connect(self.clear_log)
        log_header_layout.addWidget(clear_btn)
        log_layout.addWidget(log_header)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("LogConsole")
        log_layout.addWidget(self.log_view)
        splitter.addWidget(log_card)
        splitter.setSizes([400, 300])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        main_layout.addWidget(splitter)

    def setup_extract_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        card = CardFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(5)
        card_layout.addWidget(QLabel("🔓 提取 (Extract)"))

        card_layout.addWidget(QLabel("提取类型:"))
        self.extract_type_bg = QButtonGroup(self)
        mode_row = QHBoxLayout()
        extract_modes = [
            ("ASM 文本", "asm", "脚本 -> asm.txt 文本"),
            ("JSON 对话", "json", "脚本 -> JSON 对话"),
            ("TXT 对话", "txt", "脚本 -> ☆/★ 双行文本"),
        ]
        for index, (name, value, tip) in enumerate(extract_modes):
            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(2)
            button = QRadioButton(name)
            button.setToolTip(tip)
            button.setProperty("arg_val", value)
            if index == 0:
                button.setChecked(True)
            self.extract_type_bg.addButton(button, index)
            container_layout.addWidget(button)
            tip_label = QLabel(tip)
            tip_label.setObjectName("TipLabel")
            tip_label.setWordWrap(True)
            container_layout.addWidget(tip_label)
            mode_row.addWidget(container)
        card_layout.addLayout(mode_row)

        self.extract_desc_label = QLabel("")
        self.extract_desc_label.setWordWrap(True)
        self.extract_desc_label.setObjectName("TipLabel")
        card_layout.addWidget(self.extract_desc_label)

        self.extract_stack = QStackedWidget()

        disassemble_page = QWidget()
        disassemble_layout = QVBoxLayout(disassemble_page)
        disassemble_layout.setContentsMargins(0, 0, 0, 0)
        disassemble_layout.setSpacing(8)
        self.dis_input_edit = self.create_path_selector(
            disassemble_layout,
            "📂 输入脚本:",
            on_change=self.auto_fill_disassemble_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.dis_output_edit = self.create_path_selector(
            disassemble_layout,
            "💾 输出 asm.txt(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.dis_encoding_combo = self.create_combo_selector(
            disassemble_layout,
            "🔤 文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
            editable=True,
        )
        self.dis_btn = ModernButton("执行 ASM 提取", is_primary=True)
        self.dis_btn.clicked.connect(self.run_disassemble)
        disassemble_layout.addWidget(self.dis_btn)
        self.extract_stack.addWidget(disassemble_page)

        json_page = QWidget()
        json_layout = QVBoxLayout(json_page)
        json_layout.setContentsMargins(0, 0, 0, 0)
        json_layout.setSpacing(8)
        self.json_extract_input_edit = self.create_path_selector(
            json_layout,
            "📂 输入脚本:",
            on_change=self.auto_fill_json_extract_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.json_extract_output_edit = self.create_path_selector(
            json_layout,
            "💾 输出 JSON(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.json_extract_encoding_combo = self.create_combo_selector(
            json_layout,
            "🔤 原脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
            editable=True,
        )
        self.json_extract_btn = ModernButton("执行 JSON 提取", is_primary=True)
        self.json_extract_btn.clicked.connect(self.run_json_extract)
        json_layout.addWidget(self.json_extract_btn)
        self.extract_stack.addWidget(json_page)

        txt_page = QWidget()
        txt_layout = QVBoxLayout(txt_page)
        txt_layout.setContentsMargins(0, 0, 0, 0)
        txt_layout.setSpacing(8)
        self.txt_extract_input_edit = self.create_path_selector(
            txt_layout,
            "📂 输入脚本:",
            on_change=self.auto_fill_txt_extract_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.txt_extract_output_edit = self.create_path_selector(
            txt_layout,
            "💾 输出 TXT(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.txt_extract_encoding_combo = self.create_combo_selector(
            txt_layout,
            "🔤 原脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
            editable=True,
        )
        self.txt_extract_btn = ModernButton("执行 TXT 提取", is_primary=True)
        self.txt_extract_btn.clicked.connect(self.run_txt_extract)
        txt_layout.addWidget(self.txt_extract_btn)
        self.extract_stack.addWidget(txt_page)

        card_layout.addWidget(self.extract_stack)
        card_layout.addStretch()
        layout.addWidget(card)
        self.tabs.addTab(tab, "提取（Extract）")

        for button in self.extract_type_bg.buttons():
            button.toggled.connect(self.on_extract_mode_changed)
        self.on_extract_mode_changed()

    def setup_build_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        card = CardFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(5)
        card_layout.addWidget(QLabel("🔒 构建 (Build)"))

        card_layout.addWidget(QLabel("构建类型:"))
        self.build_type_bg = QButtonGroup(self)
        mode_row = QHBoxLayout()
        build_modes = [
            ("ASM 构建", "asm", "asm.txt -> 脚本"),
            ("JSON 导回", "json", "脚本 + JSON -> 新脚本"),
            ("TXT 导回", "txt", "脚本 + TXT -> 新脚本"),
        ]
        for index, (name, value, tip) in enumerate(build_modes):
            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(2)
            button = QRadioButton(name)
            button.setToolTip(tip)
            button.setProperty("arg_val", value)
            if index == 0:
                button.setChecked(True)
            self.build_type_bg.addButton(button, index)
            container_layout.addWidget(button)
            tip_label = QLabel(tip)
            tip_label.setObjectName("TipLabel")
            tip_label.setWordWrap(True)
            container_layout.addWidget(tip_label)
            mode_row.addWidget(container)
        card_layout.addLayout(mode_row)

        self.build_desc_label = QLabel("")
        self.build_desc_label.setWordWrap(True)
        self.build_desc_label.setObjectName("TipLabel")
        card_layout.addWidget(self.build_desc_label)

        self.build_stack = QStackedWidget()

        assemble_page = QWidget()
        assemble_layout = QVBoxLayout(assemble_page)
        assemble_layout.setContentsMargins(0, 0, 0, 0)
        assemble_layout.setSpacing(8)
        self.asm_input_edit = self.create_path_selector(
            assemble_layout,
            "📂 输入 asm.txt:",
            on_change=self.auto_fill_assemble_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.asm_output_edit = self.create_path_selector(
            assemble_layout,
            "💾 输出脚本(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.asm_encoding_combo = self.create_combo_selector(
            assemble_layout,
            "🔤 输出脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
            editable=True,
        )
        self.asm_btn = ModernButton("执行 ASM 构建", is_primary=True)
        self.asm_btn.clicked.connect(self.run_assemble)
        assemble_layout.addWidget(self.asm_btn)
        self.build_stack.addWidget(assemble_page)

        json_page = QWidget()
        json_layout = QVBoxLayout(json_page)
        json_layout.setContentsMargins(0, 0, 0, 0)
        json_layout.setSpacing(8)
        self.json_import_input_edit = self.create_path_selector(
            json_layout,
            "📂 原始脚本:",
            on_change=self.auto_fill_json_import_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.json_import_dialog_edit = self.create_path_selector(
            json_layout,
            "🧾 JSON 输入:",
            add_file_button=True,
            add_dir_button=True,
        )
        self.json_import_output_edit = self.create_path_selector(
            json_layout,
            "💾 输出脚本(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.json_import_source_encoding_combo, self.json_import_encoding_combo = self.create_dual_combo_selector(
            json_layout,
            "🔤 原脚本文本编码:",
            "📤 输出脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
            editable=True,
        )
        self.json_import_btn = ModernButton("执行 JSON 导回", is_primary=True)
        self.json_import_btn.clicked.connect(self.run_json_import)
        json_layout.addWidget(self.json_import_btn)
        self.build_stack.addWidget(json_page)

        txt_page = QWidget()
        txt_layout = QVBoxLayout(txt_page)
        txt_layout.setContentsMargins(0, 0, 0, 0)
        txt_layout.setSpacing(8)
        self.txt_import_input_edit = self.create_path_selector(
            txt_layout,
            "📂 原始脚本:",
            on_change=self.auto_fill_txt_import_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.txt_import_dialog_edit = self.create_path_selector(
            txt_layout,
            "🧾 TXT 输入:",
            add_file_button=True,
            add_dir_button=True,
        )
        self.txt_import_output_edit = self.create_path_selector(
            txt_layout,
            "💾 输出脚本(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.txt_import_source_encoding_combo, self.txt_import_encoding_combo = self.create_dual_combo_selector(
            txt_layout,
            "🔤 原脚本文本编码:",
            "📤 输出脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
            editable=True,
        )
        self.txt_import_btn = ModernButton("执行 TXT 导回", is_primary=True)
        self.txt_import_btn.clicked.connect(self.run_txt_import)
        txt_layout.addWidget(self.txt_import_btn)
        self.build_stack.addWidget(txt_page)

        card_layout.addWidget(self.build_stack)
        card_layout.addStretch()
        layout.addWidget(card)
        self.tabs.addTab(tab, "构建（Build）")

        for button in self.build_type_bg.buttons():
            button.toggled.connect(self.on_build_mode_changed)
        self.on_build_mode_changed()

    def get_extract_mode(self) -> str:
        button = self.extract_type_bg.checkedButton()
        if button is None:
            return "asm"
        return str(button.property("arg_val"))

    def get_build_mode(self) -> str:
        button = self.build_type_bg.checkedButton()
        if button is None:
            return "asm"
        return str(button.property("arg_val"))

    def on_extract_mode_changed(self, _checked: bool = False) -> None:
        mode = self.get_extract_mode()
        index_map = {"asm": 0, "json": 1, "txt": 2}
        desc_map = {
            "asm": "说明: 将原始 TXT 脚本反汇编为 asm.txt，适合底层分析与手工编辑。",
            "json": "说明: 提取 text_ab/text_aa/define_string_table 为 JSON，对话会按 name / message 结构输出。",
            "txt": "说明: 提取为与 BGI 工具一致的 ☆/★ 双行文本，类型分为 N/T/S。",
        }
        self.extract_stack.setCurrentIndex(index_map.get(mode, 0))
        self.extract_desc_label.setText(desc_map.get(mode, ""))

    def on_build_mode_changed(self, _checked: bool = False) -> None:
        mode = self.get_build_mode()
        index_map = {"asm": 0, "json": 1, "txt": 2}
        desc_map = {
            "asm": "说明: 将 asm.txt 构建回脚本文件。",
            "json": "说明: 用原始脚本 + JSON 导回生成新脚本；空 JSON 也会执行一遍转码并输出。",
            "txt": "说明: 用原始脚本 + TXT 导回生成新脚本；空 TXT 也会执行一遍转码并输出。",
        }
        self.build_stack.setCurrentIndex(index_map.get(mode, 0))
        self.build_desc_label.setText(desc_map.get(mode, ""))

    def setup_disassemble_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        card = CardFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)
        card_layout.addWidget(QLabel("🔓 反汇编 (Disassemble)"))
        desc = QLabel("说明: 支持单文件或目录批量反汇编；目录模式会递归处理所有 .TXT 文件。")
        desc.setWordWrap(True)
        desc.setObjectName("TipLabel")
        card_layout.addWidget(desc)

        self.dis_input_edit = self.create_path_selector(
            card_layout,
            "📂 输入脚本:",
            on_change=self.auto_fill_disassemble_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.dis_output_edit = self.create_path_selector(
            card_layout,
            "💾 输出路径(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.dis_encoding_combo = self.create_combo_selector(
            card_layout,
            "🔤 文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
        )
        self.dis_btn = ModernButton("执行反汇编", is_primary=True)
        self.dis_btn.clicked.connect(self.run_disassemble)
        card_layout.addWidget(self.dis_btn)
        card_layout.addStretch()

        layout.addWidget(card)
        self.tabs.addTab(tab, "反汇编（Disassemble）")

    def setup_assemble_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        card = CardFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)
        card_layout.addWidget(QLabel("🔒 汇编 (Assemble)"))
        desc = QLabel("说明: 支持单文件或目录批量汇编；目录模式会递归处理所有 .asm.txt 文件。")
        desc.setWordWrap(True)
        desc.setObjectName("TipLabel")
        card_layout.addWidget(desc)

        self.asm_input_edit = self.create_path_selector(
            card_layout,
            "📂 输入 asm.txt:",
            on_change=self.auto_fill_assemble_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.asm_output_edit = self.create_path_selector(
            card_layout,
            "💾 输出路径(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.asm_encoding_combo = self.create_combo_selector(
            card_layout,
            "🔤 文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
        )
        self.asm_btn = ModernButton("执行汇编", is_primary=True)
        self.asm_btn.clicked.connect(self.run_assemble)
        card_layout.addWidget(self.asm_btn)
        card_layout.addStretch()

        layout.addWidget(card)
        self.tabs.addTab(tab, "汇编（Assemble）")

    def setup_json_extract_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        card = CardFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)
        card_layout.addWidget(QLabel("📤 JSON 提取"))
        desc = QLabel("说明: 输入原始 TXT 脚本，提取 text_ab/text_aa/define_string_table 为 JSON；支持单文件或目录批量处理。")
        desc.setWordWrap(True)
        desc.setObjectName("TipLabel")
        card_layout.addWidget(desc)

        self.json_extract_input_edit = self.create_path_selector(
            card_layout,
            "📂 输入脚本:",
            on_change=self.auto_fill_json_extract_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.json_extract_output_edit = self.create_path_selector(
            card_layout,
            "💾 输出 JSON(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.json_extract_encoding_combo = self.create_combo_selector(
            card_layout,
            "🔤 原脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
        )
        self.json_extract_btn = ModernButton("执行 JSON 提取", is_primary=True)
        self.json_extract_btn.clicked.connect(self.run_json_extract)
        card_layout.addWidget(self.json_extract_btn)
        card_layout.addStretch()

        layout.addWidget(card)
        self.tabs.addTab(tab, "JSON 提取")

    def setup_txt_extract_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        card = CardFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)
        card_layout.addWidget(QLabel("📝 TXT 提取"))
        desc = QLabel("说明: 输出与 BGI 工具一致的 ☆/★ 双行文本，类型分为 N/T/S；支持单文件或目录批量处理。")
        desc.setWordWrap(True)
        desc.setObjectName("TipLabel")
        card_layout.addWidget(desc)

        self.txt_extract_input_edit = self.create_path_selector(
            card_layout,
            "📂 输入脚本:",
            on_change=self.auto_fill_txt_extract_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.txt_extract_output_edit = self.create_path_selector(
            card_layout,
            "💾 输出 TXT(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.txt_extract_encoding_combo = self.create_combo_selector(
            card_layout,
            "🔤 原脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
        )
        self.txt_extract_btn = ModernButton("执行 TXT 提取", is_primary=True)
        self.txt_extract_btn.clicked.connect(self.run_txt_extract)
        card_layout.addWidget(self.txt_extract_btn)
        card_layout.addStretch()

        layout.addWidget(card)
        self.tabs.addTab(tab, "TXT 提取")

    def setup_json_import_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        card = CardFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)
        card_layout.addWidget(QLabel("📥 JSON 导回"))
        desc = QLabel("说明: 输入原始脚本和已编辑 JSON，输出新的脚本文件；即使 JSON 为空，也会执行一次转码并输出新脚本。")
        desc.setWordWrap(True)
        desc.setObjectName("TipLabel")
        card_layout.addWidget(desc)

        self.json_import_input_edit = self.create_path_selector(
            card_layout,
            "📂 原始脚本:",
            on_change=self.auto_fill_json_import_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.json_import_dialog_edit = self.create_path_selector(
            card_layout,
            "🧾 JSON 输入:",
            add_file_button=True,
            add_dir_button=True,
        )
        self.json_import_output_edit = self.create_path_selector(
            card_layout,
            "💾 输出脚本(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.json_import_source_encoding_combo = self.create_combo_selector(
            card_layout,
            "🔤 原脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
        )
        self.json_import_encoding_combo = self.create_combo_selector(
            card_layout,
            "📤 输出脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
        )
        self.json_import_btn = ModernButton("执行 JSON 导回", is_primary=True)
        self.json_import_btn.clicked.connect(self.run_json_import)
        card_layout.addWidget(self.json_import_btn)
        card_layout.addStretch()

        layout.addWidget(card)
        self.tabs.addTab(tab, "JSON 导回")

    def setup_txt_import_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        card = CardFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)
        card_layout.addWidget(QLabel("📥 TXT 导回"))
        desc = QLabel("说明: 输入原始脚本和已编辑 TXT，输出新的脚本文件；即使 TXT 为空，也会执行一次转码并输出新脚本。")
        desc.setWordWrap(True)
        desc.setObjectName("TipLabel")
        card_layout.addWidget(desc)

        self.txt_import_input_edit = self.create_path_selector(
            card_layout,
            "📂 原始脚本:",
            on_change=self.auto_fill_txt_import_output,
            add_file_button=True,
            add_dir_button=True,
        )
        self.txt_import_dialog_edit = self.create_path_selector(
            card_layout,
            "🧾 TXT 输入:",
            add_file_button=True,
            add_dir_button=True,
        )
        self.txt_import_output_edit = self.create_path_selector(
            card_layout,
            "💾 输出脚本(可选):",
            add_file_button=True,
            add_dir_button=True,
            is_output=True,
        )
        self.txt_import_source_encoding_combo = self.create_combo_selector(
            card_layout,
            "🔤 原脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
        )
        self.txt_import_encoding_combo = self.create_combo_selector(
            card_layout,
            "📤 输出脚本文本编码:",
            TEXT_ENCODING_CHOICES,
            mebius_txt_op.DEFAULT_TEXT_ENCODING,
        )
        self.txt_import_btn = ModernButton("执行 TXT 导回", is_primary=True)
        self.txt_import_btn.clicked.connect(self.run_txt_import)
        card_layout.addWidget(self.txt_import_btn)
        card_layout.addStretch()

        layout.addWidget(card)
        self.tabs.addTab(tab, "TXT 导回")

    def create_path_selector(
        self,
        parent_layout: QVBoxLayout,
        label_text: str,
        on_change=None,
        add_file_button: bool = False,
        add_dir_button: bool = True,
        is_output: bool = False,
    ) -> DragDropLineEdit:
        row = QHBoxLayout()
        row.setSpacing(5)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(label_text))
        edit = DragDropLineEdit(is_folder=True)
        if on_change is not None:
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

    def create_combo_selector(
        self,
        parent_layout: QVBoxLayout,
        label_text: str,
        values: list[str],
        current_value: str,
        editable: bool = True,
    ) -> QComboBox:
        row = QHBoxLayout()
        row.setSpacing(5)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(label_text))
        combo = QComboBox()
        combo.setEditable(editable)
        combo.addItems(values)
        combo.setCurrentText(current_value)
        row.addWidget(combo)
        row.addStretch()
        parent_layout.addLayout(row)
        return combo

    def create_dual_combo_selector(
        self,
        parent_layout: QVBoxLayout,
        left_label: str,
        right_label: str,
        values: list[str],
        left_current: str,
        right_current: str,
        editable: bool = True,
    ) -> tuple[QComboBox, QComboBox]:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.setContentsMargins(0, 0, 0, 0)

        left_wrap = QHBoxLayout()
        left_wrap.setSpacing(5)
        left_wrap.setContentsMargins(0, 0, 0, 0)
        left_wrap.addWidget(QLabel(left_label))
        left_combo = QComboBox()
        left_combo.setEditable(editable)
        left_combo.addItems(values)
        left_combo.setCurrentText(left_current)
        left_wrap.addWidget(left_combo)
        left_wrap.addStretch()

        right_wrap = QHBoxLayout()
        right_wrap.setSpacing(5)
        right_wrap.setContentsMargins(0, 0, 0, 0)
        right_wrap.addWidget(QLabel(right_label))
        right_combo = QComboBox()
        right_combo.setEditable(editable)
        right_combo.addItems(values)
        right_combo.setCurrentText(right_current)
        right_wrap.addWidget(right_combo)
        right_wrap.addStretch()

        row.addLayout(left_wrap, 1)
        row.addLayout(right_wrap, 1)
        parent_layout.addLayout(row)
        return left_combo, right_combo

    def browse_folder(self, line_edit: QLineEdit, on_change=None) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            line_edit.setText(os.path.normpath(path))
            if on_change is not None:
                on_change()

    def browse_input_file(self, line_edit: QLineEdit, on_change=None) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if path:
            line_edit.setText(os.path.normpath(path))
            if on_change is not None:
                on_change()

    def browse_output_file(self, line_edit: QLineEdit) -> None:
        initial = line_edit.text().strip()
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", initial)
        if path:
            line_edit.setText(os.path.normpath(path))

    def auto_fill_disassemble_output(self) -> None:
        input_path = self.dis_input_edit.text().strip()
        if input_path:
            self.dis_output_edit.setText(default_disassemble_output(input_path))

    def auto_fill_assemble_output(self) -> None:
        input_path = self.asm_input_edit.text().strip()
        if input_path:
            self.asm_output_edit.setText(default_assemble_output(input_path))

    def auto_fill_json_extract_output(self) -> None:
        input_path = self.json_extract_input_edit.text().strip()
        if input_path:
            self.json_extract_output_edit.setText(default_extract_output(input_path, ".json"))

    def auto_fill_txt_extract_output(self) -> None:
        input_path = self.txt_extract_input_edit.text().strip()
        if input_path:
            self.txt_extract_output_edit.setText(default_extract_output(input_path, ".txt"))

    def auto_fill_json_import_output(self) -> None:
        input_path = self.json_import_input_edit.text().strip()
        if input_path:
            self.json_import_output_edit.setText(default_import_output(input_path, "_jsonimp"))

    def auto_fill_txt_import_output(self) -> None:
        input_path = self.txt_import_input_edit.text().strip()
        if input_path:
            self.txt_import_output_edit.setText(default_import_output(input_path, "_txtimp"))

    def run_disassemble(self) -> None:
        input_path = self.dis_input_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择输入路径")
            return
        self.start_worker(
            "disassemble",
            input_path,
            self.dis_output_edit.text().strip(),
            self.dis_encoding_combo.currentText(),
        )

    def run_assemble(self) -> None:
        input_path = self.asm_input_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择输入路径")
            return
        self.start_worker(
            "assemble",
            input_path,
            self.asm_output_edit.text().strip(),
            self.asm_encoding_combo.currentText(),
        )

    def run_json_extract(self) -> None:
        input_path = self.json_extract_input_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择输入路径")
            return
        self.start_worker(
            "json_extract",
            input_path,
            self.json_extract_output_edit.text().strip(),
            self.json_extract_encoding_combo.currentText(),
        )

    def run_txt_extract(self) -> None:
        input_path = self.txt_extract_input_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择输入路径")
            return
        self.start_worker(
            "txt_extract",
            input_path,
            self.txt_extract_output_edit.text().strip(),
            self.txt_extract_encoding_combo.currentText(),
        )

    def run_json_import(self) -> None:
        input_path = self.json_import_input_edit.text().strip()
        dialog_path = self.json_import_dialog_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择原始脚本路径")
            return
        if not dialog_path:
            QMessageBox.warning(self, "提示", "请先选择 JSON 输入路径")
            return
        self.start_worker(
            "json_import",
            input_path,
            self.json_import_output_edit.text().strip(),
            self.json_import_encoding_combo.currentText(),
            dialog_path=dialog_path,
            source_encoding=self.json_import_source_encoding_combo.currentText(),
        )

    def run_txt_import(self) -> None:
        input_path = self.txt_import_input_edit.text().strip()
        dialog_path = self.txt_import_dialog_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择原始脚本路径")
            return
        if not dialog_path:
            QMessageBox.warning(self, "提示", "请先选择 TXT 输入路径")
            return
        self.start_worker(
            "txt_import",
            input_path,
            self.txt_import_output_edit.text().strip(),
            self.txt_import_encoding_combo.currentText(),
            dialog_path=dialog_path,
            source_encoding=self.txt_import_source_encoding_combo.currentText(),
        )

    def start_worker(
        self,
        mode: str,
        input_path: str = "",
        output_path: str = "",
        text_encoding: str = mebius_txt_op.DEFAULT_TEXT_ENCODING,
        dialog_path: str = "",
        source_encoding: str | None = None,
    ) -> None:
        try:
            normalized_encoding = mebius_txt_op.normalize_encoding(text_encoding)
            normalized_source_encoding = (
                mebius_txt_op.normalize_encoding(source_encoding) if source_encoding is not None else None
            )
        except Exception as exc:
            QMessageBox.warning(self, "编码错误", str(exc))
            return

        self.toggle_ui(False)
        self.clear_log()
        self.log_view.append(f"--- 开始任务: {mode} ---")
        if input_path:
            self.log_view.append(f"输入路径: {input_path}")
        if dialog_path:
            self.log_view.append(f"文本输入: {dialog_path}")
        if output_path:
            self.log_view.append(f"输出路径: {output_path}")
        if normalized_source_encoding is not None:
            self.log_view.append(f"原脚本文本编码: {normalized_source_encoding}")
        self.log_view.append(f"文本编码: {normalized_encoding}")
        self.worker = WorkerThread(
            mode,
            input_path,
            output_path,
            normalized_encoding,
            dialog_path,
            normalized_source_encoding,
        )
        self.worker.log_signal.connect(self.log_message)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def log_message(self, message: str) -> None:
        self.log_view.moveCursor(QT_END)
        if message.endswith("\n"):
            self.log_view.insertPlainText(message)
        else:
            self.log_view.insertPlainText(message + "\n")
        self.log_view.moveCursor(QT_END)

    def on_finished(self, success: bool, message: str) -> None:
        self.toggle_ui(True)
        if success:
            QMessageBox.information(self, "Success", message)
        else:
            QMessageBox.warning(self, "Error", message)

    def toggle_ui(self, enabled: bool) -> None:
        self.dis_btn.setEnabled(enabled)
        self.asm_btn.setEnabled(enabled)
        self.json_extract_btn.setEnabled(enabled)
        self.txt_extract_btn.setEnabled(enabled)
        self.json_import_btn.setEnabled(enabled)
        self.txt_import_btn.setEnabled(enabled)
        self.tabs.setEnabled(enabled)

    def clear_log(self) -> None:
        self.log_view.clear()

    def detect_system_theme(self) -> None:
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentText("跟随系统")
        self.theme_combo.blockSignals(False)
        self.apply_theme("跟随系统")

    def _is_system_dark_theme(self) -> bool:
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

    def _resolve_theme_name(self, theme_name: str) -> str:
        if theme_name == "跟随系统":
            return "现代深色" if self._is_system_dark_theme() else "现代浅色"
        return theme_name

    def apply_theme(self, theme_name: str) -> None:
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
        QLabel#AppTitle { color: #ff00ff; font-size: 18pt; font-weight: bold; text-shadow: 0 0 5px #ff00ff; }
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


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())