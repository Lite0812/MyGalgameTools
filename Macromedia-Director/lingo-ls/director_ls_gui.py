from __future__ import annotations

import contextlib
import os
import sys
import traceback
from pathlib import Path

try:
    from PyQt6.QtCore import Qt, QThread, pyqtSignal as Signal
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
except ModuleNotFoundError:
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import (
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

try:
    import director_ls_roundtrip as director_tools
    import director_ls_dialog_json as director_json_tools
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import director_ls_roundtrip as director_tools
    import director_ls_dialog_json as director_json_tools


class StreamRedirector:
    def __init__(self, signal=None):
        self.signal = signal
        self._original_stdout = sys.__stdout__

    def write(self, text):
        message = str(text)
        if self.signal and message:
            try:
                self.signal.emit(message)
            except Exception:
                pass

        if self._original_stdout:
            try:
                self._original_stdout.write(message)
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
    def __init__(self, parent=None, is_folder=False, placeholder_text=""):
        super().__init__(parent)
        self.is_folder = is_folder
        self.setAcceptDrops(True)
        if placeholder_text:
            self.setPlaceholderText(placeholder_text)
        else:
            self.setPlaceholderText("可直接拖入文件或文件夹..." if is_folder else "可直接拖入文件...")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return

        path = urls[0].toLocalFile()
        if path:
            self.setText(os.path.normpath(path))
        event.acceptProposedAction()


class WorkerThread(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(bool, str)

    def __init__(self, mode, input_path, output_path, secondary_path=""):
        super().__init__()
        self.mode = mode
        self.input_path = input_path
        self.output_path = output_path
        self.secondary_path = secondary_path

    def run(self):
        redirector = StreamRedirector(self.log_signal)
        try:
            with contextlib.redirect_stdout(redirector), contextlib.redirect_stderr(redirector):
                if self.mode == "export":
                    director_tools.export_scripts(Path(self.input_path), Path(self.output_path))
                    self.finished_signal.emit(True, f"导出完成: {self.output_path}")
                    return
                if self.mode == "import":
                    edited_path = Path(self.secondary_path)
                    if edited_path.is_dir():
                        director_tools.import_ls_folder(
                            Path(self.input_path),
                            edited_path,
                            Path(self.output_path),
                        )
                    else:
                        director_tools.import_ls_script(
                            Path(self.input_path),
                            edited_path,
                            Path(self.output_path),
                        )
                    self.finished_signal.emit(True, f"导回完成: {self.output_path}")
                    return
                if self.mode == "json-export":
                    file_count, message_count = director_json_tools.extract_json_from_cst(
                        Path(self.input_path),
                        Path(self.output_path),
                    )
                    json_dir = Path(self.output_path) / Path(self.input_path).stem
                    self.finished_signal.emit(
                        True,
                        f"JSON 提取完成: {json_dir} ({file_count} 个文件, {message_count} 条文本)",
                    )
                    return
                if self.mode == "json-import-cst":
                    file_count, message_count = director_json_tools.import_json_dir_to_cst(
                        Path(self.input_path),
                        Path(self.secondary_path),
                        Path(self.output_path),
                    )
                    self.finished_signal.emit(
                        True,
                        f"JSON 一键导回完成: {self.output_path} ({file_count} 个 JSON 文件, {message_count} 条文本)",
                    )
                    return

                raise ValueError(f"未知任务模式: {self.mode}")
        except Exception as exc:
            traceback.print_exc()
            self.finished_signal.emit(False, str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Director LS Tools GUI")
        self.resize(750, 700)
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
        title_label = QLabel("Director LS Tools")
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
        self.setup_extract_tab()
        self.setup_build_tab()

        log_card = CardFrame()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)

        log_header = QWidget()
        log_header.setObjectName("LogHeader")
        header_inner = QHBoxLayout(log_header)
        header_inner.setContentsMargins(10, 5, 10, 5)
        header_inner.addWidget(QLabel("📝 运行日志 (Log)"))
        header_inner.addStretch()

        clear_btn = QPushButton("清除")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet("border:none; font-weight:bold; color: #888;")
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        header_inner.addWidget(clear_btn)
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

    def setup_extract_tab(self):
        extract_tab = QWidget()
        extract_layout = QVBoxLayout(extract_tab)
        extract_layout.setContentsMargins(5, 5, 5, 5)
        extract_layout.setSpacing(5)

        extract_card = CardFrame()
        ec_layout = QVBoxLayout(extract_card)
        ec_layout.setContentsMargins(10, 10, 10, 10)
        ec_layout.setSpacing(5)
        ec_layout.addWidget(QLabel("🔓 提取 (Extract)"))

        ec_layout.addWidget(QLabel("提取类型:"))
        self.extract_type_bg = QButtonGroup(self)
        extract_mode_row = QHBoxLayout()
        extract_modes = [
            ("LS 导出", "ls", "原始 CST -> 全量 LS 目录"),
            ("JSON 提取", "json", "原始 CST -> JSON 文本目录"),
        ]
        for index, (name, value, tip) in enumerate(extract_modes):
            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(2)
            radio_button = QRadioButton(name)
            radio_button.setToolTip(tip)
            radio_button.setProperty("arg_val", value)
            if index == 0:
                radio_button.setChecked(True)
            self.extract_type_bg.addButton(radio_button, index)
            container_layout.addWidget(radio_button)
            tip_label = QLabel(tip)
            tip_label.setObjectName("TipLabel")
            tip_label.setWordWrap(True)
            container_layout.addWidget(tip_label)
            extract_mode_row.addWidget(container)
        ec_layout.addLayout(extract_mode_row)

        self.extract_desc_label = QLabel("")
        self.extract_desc_label.setWordWrap(True)
        self.extract_desc_label.setObjectName("TipLabel")
        ec_layout.addWidget(self.extract_desc_label)

        self.extract_stack = QStackedWidget()

        extract_ls_page = QWidget()
        ls_layout = QVBoxLayout(extract_ls_page)
        ls_layout.setContentsMargins(0, 0, 0, 0)
        ls_layout.setSpacing(8)
        self.export_input_edit = self.create_path_selector(
            ls_layout,
            "📂 输入 CST:",
            on_change=lambda: self.auto_fill_output("ext_ls"),
            add_file_button=True,
            add_dir_button=False,
            file_filter="Director CST (*.cst);;All Files (*.*)",
        )
        self.export_output_edit = self.create_path_selector(
            ls_layout,
            "💾 输出目录(可选):",
            add_file_button=False,
            add_dir_button=True,
            is_output=True,
            expect_folder=True,
        )
        ls_tip = QLabel("拖入 cst 后会自动补全输出目录，默认目录名为 *_ls_all。")
        ls_tip.setObjectName("TipLabel")
        ls_tip.setWordWrap(True)
        ls_layout.addWidget(ls_tip)
        self.btn_export = ModernButton("执行全量 LS 导出", is_primary=True)
        self.btn_export.clicked.connect(self.run_export)
        ls_layout.addWidget(self.btn_export)
        self.extract_stack.addWidget(extract_ls_page)

        extract_json_page = QWidget()
        json_layout = QVBoxLayout(extract_json_page)
        json_layout.setContentsMargins(0, 0, 0, 0)
        json_layout.setSpacing(8)
        self.json_extract_cst_edit = self.create_path_selector(
            json_layout,
            "📂 输入 CST:",
            on_change=lambda: self.auto_fill_output("ext_json"),
            add_file_button=True,
            add_dir_button=False,
            file_filter="Director CST (*.cst);;All Files (*.*)",
        )
        self.json_extract_output_edit = self.create_path_selector(
            json_layout,
            "💾 输出 JSON 目录(可选):",
            add_file_button=False,
            add_dir_button=True,
            is_output=True,
            expect_folder=True,
        )
        json_tip = QLabel("文本末尾的 ▼ 或全角空格不会写入 JSON，导回时会自动保留。默认目录名为 *_json。")
        json_tip.setObjectName("TipLabel")
        json_tip.setWordWrap(True)
        json_layout.addWidget(json_tip)
        self.btn_json_extract = ModernButton("执行 JSON 提取", is_primary=True)
        self.btn_json_extract.clicked.connect(self.run_json_extract)
        json_layout.addWidget(self.btn_json_extract)
        self.extract_stack.addWidget(extract_json_page)

        ec_layout.addWidget(self.extract_stack)
        ec_layout.addStretch()
        extract_layout.addWidget(extract_card)
        self.tabs.addTab(extract_tab, "提取（Extract）")

        for button in self.extract_type_bg.buttons():
            button.toggled.connect(lambda _: self.on_extract_mode_changed())
        self.on_extract_mode_changed()

    def setup_build_tab(self):
        build_tab = QWidget()
        build_layout = QVBoxLayout(build_tab)
        build_layout.setContentsMargins(5, 5, 5, 5)
        build_layout.setSpacing(5)

        build_card = CardFrame()
        bc_layout = QVBoxLayout(build_card)
        bc_layout.setContentsMargins(10, 10, 10, 10)
        bc_layout.setSpacing(5)
        bc_layout.addWidget(QLabel("🔒 构建 (Build)"))

        bc_layout.addWidget(QLabel("构建类型:"))
        self.build_type_bg = QButtonGroup(self)
        build_mode_row = QHBoxLayout()
        build_modes = [
            ("LS 导回", "ls", "原始 CST + LS -> 新 CST"),
            ("JSON 导回", "json", "原始 CST + JSON 目录 -> 新 CST"),
        ]
        for index, (name, value, tip) in enumerate(build_modes):
            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(2)
            radio_button = QRadioButton(name)
            radio_button.setToolTip(tip)
            radio_button.setProperty("arg_val", value)
            if index == 0:
                radio_button.setChecked(True)
            self.build_type_bg.addButton(radio_button, index)
            container_layout.addWidget(radio_button)
            tip_label = QLabel(tip)
            tip_label.setObjectName("TipLabel")
            tip_label.setWordWrap(True)
            container_layout.addWidget(tip_label)
            build_mode_row.addWidget(container)
        bc_layout.addLayout(build_mode_row)

        self.build_desc_label = QLabel("")
        self.build_desc_label.setWordWrap(True)
        self.build_desc_label.setObjectName("TipLabel")
        bc_layout.addWidget(self.build_desc_label)

        self.build_stack = QStackedWidget()

        build_ls_page = QWidget()
        build_ls_layout = QVBoxLayout(build_ls_page)
        build_ls_layout.setContentsMargins(0, 0, 0, 0)
        build_ls_layout.setSpacing(8)
        self.import_cst_edit = self.create_path_selector(
            build_ls_layout,
            "📂 原始 CST:",
            on_change=lambda: self.auto_fill_output("build_ls"),
            add_file_button=True,
            add_dir_button=False,
            file_filter="Director CST (*.cst);;All Files (*.*)",
        )
        self.import_ls_edit = self.create_path_selector(
            build_ls_layout,
            "📝 修改后的 LS / 文件夹:",
            on_change=lambda: self.auto_fill_output("build_ls"),
            add_file_button=True,
            add_dir_button=True,
            file_filter="Lingo Script (*.ls);;All Files (*.*)",
            placeholder_text="可直接拖入 LS 文件或文件夹...",
        )
        self.import_output_edit = self.create_path_selector(
            build_ls_layout,
            "💾 输出 CST(可选):",
            add_file_button=True,
            add_dir_button=False,
            is_output=True,
            file_filter="Director CST (*.cst);;All Files (*.*)",
        )
        build_ls_tip = QLabel("支持单个 LS 文件或整个 LS 文件夹导回；未修改时会保持与原始 cst 二进制完全一致。")
        build_ls_tip.setObjectName("TipLabel")
        build_ls_tip.setWordWrap(True)
        build_ls_layout.addWidget(build_ls_tip)
        self.btn_import = ModernButton("执行 LS 导回", is_primary=True)
        self.btn_import.clicked.connect(self.run_import)
        build_ls_layout.addWidget(self.btn_import)
        self.build_stack.addWidget(build_ls_page)

        build_json_page = QWidget()
        build_json_layout = QVBoxLayout(build_json_page)
        build_json_layout.setContentsMargins(0, 0, 0, 0)
        build_json_layout.setSpacing(8)
        self.json_import_cst_edit = self.create_path_selector(
            build_json_layout,
            "📂 原始 CST:",
            on_change=lambda: self.auto_fill_output("build_json"),
            add_file_button=True,
            add_dir_button=False,
            file_filter="Director CST (*.cst);;All Files (*.*)",
        )
        self.json_import_dir_edit = self.create_path_selector(
            build_json_layout,
            "🧾 JSON 输入目录:",
            add_file_button=False,
            add_dir_button=True,
            placeholder_text="可直接拖入 JSON 目录...",
            expect_folder=True,
        )
        self.json_import_output_edit = self.create_path_selector(
            build_json_layout,
            "💾 输出 CST(可选):",
            add_file_button=True,
            add_dir_button=False,
            is_output=True,
            file_filter="Director CST (*.cst);;All Files (*.*)",
        )
        build_json_tip = QLabel("会自动执行 原始 CST -> 临时 LS -> 应用 JSON -> 新 CST。末尾 ▼ / 全角空格会自动保留。")
        build_json_tip.setObjectName("TipLabel")
        build_json_tip.setWordWrap(True)
        build_json_layout.addWidget(build_json_tip)
        self.btn_json_import = ModernButton("执行 JSON 导回到 CST", is_primary=True)
        self.btn_json_import.clicked.connect(self.run_json_import)
        build_json_layout.addWidget(self.btn_json_import)
        self.build_stack.addWidget(build_json_page)

        bc_layout.addWidget(self.build_stack)
        bc_layout.addStretch()
        build_layout.addWidget(build_card)
        self.tabs.addTab(build_tab, "构建（Build）")

        for button in self.build_type_bg.buttons():
            button.toggled.connect(lambda _: self.on_build_mode_changed())
        self.on_build_mode_changed()

    def on_extract_mode_changed(self):
        mode = self.get_extract_mode()
        index_map = {"ls": 0, "json": 1}
        desc_map = {
            "ls": "说明: 直接从原始 CST 提取全部 LS 文件，包含空脚本，并输出 Members.csv。",
            "json": "说明: 直接从原始 CST 提取 stn / .text 文本为 JSON；表达式会转成 {{expr}} 占位符。",
        }
        self.extract_stack.setCurrentIndex(index_map.get(mode, 0))
        self.extract_desc_label.setText(desc_map.get(mode, ""))

    def on_build_mode_changed(self):
        mode = self.get_build_mode()
        index_map = {"ls": 0, "json": 1}
        desc_map = {
            "ls": "说明: 用原始 CST + 修改后的 LS 文件或目录导回，生成新的 CST。",
            "json": "说明: 用原始 CST + JSON 目录一键导回新的 CST，中间临时 LS 由程序自动处理。",
        }
        self.build_stack.setCurrentIndex(index_map.get(mode, 0))
        self.build_desc_label.setText(desc_map.get(mode, ""))

    def get_extract_mode(self):
        button = self.extract_type_bg.checkedButton()
        if not button:
            return "ls"
        return button.property("arg_val")

    def get_build_mode(self):
        button = self.build_type_bg.checkedButton()
        if not button:
            return "ls"
        return button.property("arg_val")

    def create_path_selector(
        self,
        parent_layout,
        label_text,
        on_change=None,
        add_file_button=False,
        add_dir_button=True,
        is_output=False,
        file_filter="All Files (*.*)",
        expect_folder=False,
        placeholder_text="",
    ):
        row = QHBoxLayout()
        row.setSpacing(5)
        row.setContentsMargins(0, 0, 0, 0)

        row.addWidget(QLabel(label_text))
        edit = DragDropLineEdit(is_folder=expect_folder, placeholder_text=placeholder_text)
        if on_change:
            edit.textChanged.connect(on_change)
        row.addWidget(edit)

        if add_file_button:
            btn_file = ModernButton("文件")
            btn_file.setFixedWidth(50)
            if is_output:
                btn_file.clicked.connect(lambda: self.browse_output_file(edit, file_filter))
            else:
                btn_file.clicked.connect(lambda: self.browse_input_file(edit, on_change, file_filter))
            row.addWidget(btn_file)

        if add_dir_button:
            btn_dir = ModernButton("目录")
            btn_dir.setFixedWidth(50)
            btn_dir.clicked.connect(lambda: self.browse_folder(edit, on_change))
            row.addWidget(btn_dir)

        parent_layout.addLayout(row)
        return edit

    def browse_folder(self, line_edit, on_change=None):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            line_edit.setText(os.path.normpath(path))
            if on_change:
                on_change()

    def browse_input_file(self, line_edit, on_change=None, file_filter="All Files (*.*)"):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", line_edit.text().strip(), file_filter)
        if path:
            line_edit.setText(os.path.normpath(path))
            if on_change:
                on_change()

    def browse_output_file(self, line_edit, file_filter="All Files (*.*)"):
        initial_path = line_edit.text().strip()
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", initial_path, file_filter)
        if path:
            line_edit.setText(os.path.normpath(path))

    def auto_fill_output(self, mode):
        if mode == "ext_ls":
            input_path = self.export_input_edit.text().strip()
            if not input_path or os.path.isdir(input_path):
                return
            base = os.path.splitext(os.path.normpath(input_path))[0]
            self.export_output_edit.setText(base + "_ls_all")
            return

        if mode == "build_ls":
            input_path = self.import_cst_edit.text().strip()
            if not input_path or os.path.isdir(input_path):
                return
            base = os.path.splitext(os.path.normpath(input_path))[0]
            self.import_output_edit.setText(base + "_patched.cst")
            return

        if mode == "ext_json":
            input_path = self.json_extract_cst_edit.text().strip()
            if not input_path or os.path.isdir(input_path):
                return
            base = os.path.splitext(os.path.normpath(input_path))[0]
            self.json_extract_output_edit.setText(base + "_json")
            return

        if mode == "build_json":
            input_path = self.json_import_cst_edit.text().strip()
            if not input_path or os.path.isdir(input_path):
                return
            base = os.path.splitext(os.path.normpath(input_path))[0]
            self.json_import_output_edit.setText(base + "_json_patched.cst")

    def run_export(self):
        input_path = self.export_input_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, "提示", "请先选择输入 CST 文件")
            return

        output_path = self.export_output_edit.text().strip()
        if not output_path:
            self.auto_fill_output("ext_ls")
            output_path = self.export_output_edit.text().strip()
        if not output_path:
            QMessageBox.warning(self, "提示", "请先选择输出目录")
            return

        self.start_worker("export", input_path, output_path)

    def run_import(self):
        input_cst = self.import_cst_edit.text().strip()
        if not input_cst:
            QMessageBox.warning(self, "提示", "请先选择原始 CST 文件")
            return

        edited_ls = self.import_ls_edit.text().strip()
        if not edited_ls:
            QMessageBox.warning(self, "提示", "请先选择修改后的 LS 文件")
            return

        output_cst = self.import_output_edit.text().strip()
        if not output_cst:
            self.auto_fill_output("build_ls")
            output_cst = self.import_output_edit.text().strip()
        if not output_cst:
            QMessageBox.warning(self, "提示", "请先选择输出 CST 文件")
            return

        self.start_worker("import", input_cst, output_cst, edited_ls)

    def run_json_extract(self):
        input_cst = self.json_extract_cst_edit.text().strip()
        if not input_cst:
            QMessageBox.warning(self, "提示", "请先选择输入 CST 文件")
            return

        output_dir = self.json_extract_output_edit.text().strip()
        if not output_dir:
            self.auto_fill_output("ext_json")
            output_dir = self.json_extract_output_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "提示", "请先选择输出 JSON 目录")
            return

        self.start_worker("json-export", input_cst, output_dir)

    def run_json_import(self):
        input_cst = self.json_import_cst_edit.text().strip()
        if not input_cst:
            QMessageBox.warning(self, "提示", "请先选择原始 CST 文件")
            return

        input_json_dir = self.json_import_dir_edit.text().strip()
        if not input_json_dir:
            QMessageBox.warning(self, "提示", "请先选择修改后的 JSON 目录")
            return

        output_cst = self.json_import_output_edit.text().strip()
        if not output_cst:
            self.auto_fill_output("build_json")
            output_cst = self.json_import_output_edit.text().strip()
        if not output_cst:
            QMessageBox.warning(self, "提示", "请先选择输出 CST 文件")
            return

        self.start_worker("json-import-cst", input_cst, output_cst, input_json_dir)

    def start_worker(self, mode, input_path, output_path, secondary_path=""):
        self.toggle_ui(False)
        self.log_view.clear()

        mode_name = {
            "export": "LS 导出",
            "import": "LS 导回",
            "json-export": "JSON 提取",
            "json-import-cst": "JSON 一键导回",
        }.get(mode, mode)
        self.log_view.append(f"--- 开始任务: {mode_name} ({mode}) ---")
        self.log_view.append(f"输入路径: {input_path}")
        if secondary_path:
            secondary_label = "输入 LS / 目录" if mode == "import" else "输入 JSON 目录"
            self.log_view.append(f"{secondary_label}: {secondary_path}")
        self.log_view.append(f"输出路径: {output_path}")

        self.worker = WorkerThread(mode, input_path, output_path, secondary_path)
        self.worker.log_signal.connect(self.log_message)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def log_message(self, msg):
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        if msg.endswith("\n"):
            self.log_view.insertPlainText(msg)
        else:
            self.log_view.insertPlainText(msg + "\n")
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def on_finished(self, success, msg):
        self.toggle_ui(True)
        if success:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.warning(self, "Error", msg)

    def toggle_ui(self, enabled):
        self.btn_export.setEnabled(enabled)
        self.btn_import.setEnabled(enabled)
        self.btn_json_extract.setEnabled(enabled)
        self.btn_json_import.setEnabled(enabled)
        self.tabs.setEnabled(enabled)

    def detect_system_theme(self):
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentText("跟随系统")
        self.theme_combo.blockSignals(False)
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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())