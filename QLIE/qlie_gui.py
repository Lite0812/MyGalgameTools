import sys
import os
import traceback
import threading
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QTextEdit, 
                             QFileDialog, QProgressBar, QMessageBox, QFrame,
                             QComboBox, QTabWidget, QRadioButton, QButtonGroup, QStackedWidget)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QDropEvent

# Try to import darkdetect for system theme detection
try:
    import darkdetect
    HAS_DARKDETECT = True
except ImportError:
    HAS_DARKDETECT = False

# Import qlie_tool functionality
try:
    import qlie_tool
except ImportError:
    qlie_tool = None

class Logger(QObject):
    log_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.terminal = sys.stdout

    def write(self, message):
        # self.terminal.write(message)
        self.log_signal.emit(message)

    def flush(self):
        # self.terminal.flush()
        pass

class WorkerThread(QObject):
    finished = pyqtSignal()
    log_signal = pyqtSignal(str)
    
    def __init__(self, mode, input_path, output_path, **kwargs):
        super().__init__()
        self.mode = mode
        self.input_path = input_path
        self.output_path = output_path
        self.kwargs = kwargs
        self.should_stop = False
        
    def run(self):
        try:
            if not qlie_tool:
                raise ImportError("找不到 qlie_tool 模块")
                
            if self.mode == 'unpack':
                self.run_unpack()
            elif self.mode == 'repack':
                self.run_repack()
        except Exception as e:
            msg = f"发生异常: {str(e)}"
            self.log_signal.emit(msg)
            self.log_signal.emit(traceback.format_exc())
        finally:
            self.finished.emit()

    def run_unpack(self):
        input_path = self.input_path
        output_path = self.output_path
        
        files = []
        if os.path.isfile(input_path):
            files = [input_path]
        elif os.path.isdir(input_path):
            for root, _, filenames in os.walk(input_path):
                for name in filenames:
                    if name.lower().endswith(".b"):
                        files.append(os.path.join(root, name))
        else:
             self.log_signal.emit(f"输入路径不存在: {input_path}")
             return

        if not files:
            self.log_signal.emit(f"未找到 .b 文件: {input_path}")
            return

        total = len(files)
        self.log_signal.emit(f"找到 {total} 个 .b 文件，开始解包...")
        
        for i, file_path in enumerate(files):
            if self.should_stop: break
            self.log_signal.emit(f"[{i+1}/{total}] 解包: {os.path.basename(file_path)}")
            
            # Determine output directory
            fname = os.path.basename(file_path)
            # 用户要求：解包不再加 _out 后缀
            folder_name = os.path.splitext(fname)[0]
            
            if output_path:
                # If user specified an output directory, create subfolder inside it
                current_out = os.path.join(output_path, folder_name)
            else:
                # Default: create folder next to input file (same name as file without extension)
                current_out = os.path.splitext(file_path)[0]
            
            try:
                # qlie_tool.unpack takes (filepath, output_dir)
                qlie_tool.unpack(file_path, current_out)
                self.log_signal.emit(f"  -> 输出: {current_out}")
            except Exception as e:
                self.log_signal.emit(f"  -> 失败: {str(e)}")
                self.log_signal.emit(traceback.format_exc())
                
        self.log_signal.emit("解包任务完成！")

    def run_repack(self):
        input_path = self.input_path
        output_path = self.output_path
        
        # Identify tasks: list of (input_dir, output_file)
        tasks = []
        
        if os.path.isfile(input_path):
            self.log_signal.emit("错误: 打包模式需要输入目录")
            return
            
        # Check if input_path itself is a package directory (has metadata.json)
        if os.path.exists(os.path.join(input_path, "metadata.json")):
            # Single directory mode
            dir_name = os.path.basename(input_path.rstrip(os.sep))
            if dir_name.endswith("_out"):
                out_name = dir_name[:-4] + ".b"
            else:
                out_name = dir_name + ".b"
                
            if output_path:
                if os.path.isdir(output_path) or not os.path.splitext(output_path)[1]:
                    # 如果 output_path 是目录（或者看起来像目录）
                    final_out = os.path.join(output_path, out_name)
                else:
                    # 如果 output_path 看起来像文件，直接使用
                    final_out = output_path 
            else:
                final_out = input_path + ".b"
                
            tasks.append((input_path, final_out))
        else:
            # Batch mode: scan subdirectories
            with os.scandir(input_path) as it:
                for entry in it:
                    if entry.is_dir():
                        meta_path = os.path.join(entry.path, "metadata.json")
                        if os.path.exists(meta_path):
                            dir_name = entry.name
                            if dir_name.endswith("_out"):
                                out_name = dir_name[:-4] + ".b"
                            else:
                                out_name = dir_name + ".b"
                                
                            if output_path:
                                final_out = os.path.join(output_path, out_name)
                            else:
                                # 如果用户没有指定 output_path（通常 GUI 会自动填一个），
                                # 但如果真的空了，默认还是放在原处
                                final_out = os.path.join(input_path, out_name)
                            
                            tasks.append((entry.path, final_out))
                            
        if not tasks:
            self.log_signal.emit(f"在 {input_path} 未找到包含 metadata.json 的目录")
            return

        total = len(tasks)
        self.log_signal.emit(f"找到 {total} 个待打包目录，开始打包...")
        
        if output_path:
            os.makedirs(output_path, exist_ok=True)

        for i, (in_dir, out_file) in enumerate(tasks):
            if self.should_stop: break
            self.log_signal.emit(f"[{i+1}/{total}] 打包: {os.path.basename(in_dir)}")
            try:
                qlie_tool.repack(in_dir, out_file)
                self.log_signal.emit(f"  -> 生成: {out_file}")
            except Exception as e:
                self.log_signal.emit(f"  -> 失败: {str(e)}")
                self.log_signal.emit(traceback.format_exc())

        self.log_signal.emit("打包任务完成！")

class DragDropLineEdit(QLineEdit):
    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()
            
    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self.setText(path)
            self.file_dropped.emit(path)

class ModernButton(QPushButton):
    def __init__(self, text, is_primary=False):
        super().__init__(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("PrimaryButton" if is_primary else "SecondaryButton")
        self.setMinimumHeight(35)

class QLIEGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QLIE .B Toolkit GUI")
        self.resize(700, 650)
        self.setObjectName("MainBackground")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        self.init_ui()
        
        # Redirect stdout
        self.logger = Logger()
        self.logger.log_signal.connect(self.append_log)
        sys.stdout = self.logger
        sys.stderr = self.logger
        
        # Detect system theme on startup
        self.detect_system_theme()

    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        self.setLayout(main_layout)
        
        # --- Header ---
        header_layout = QHBoxLayout()
        title_label = QLabel("QLIE .B Toolkit")
        title_label.setObjectName("AppTitle") 
        title_label.setStyleSheet("font-size: 18pt; font-weight: bold;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
        # Theme Selector
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["跟随系统", "现代浅色", "现代深色", "赛博朋克"])
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        header_layout.addWidget(QLabel("主题:"))
        header_layout.addWidget(self.theme_combo)
        
        main_layout.addLayout(header_layout)
        
        # --- Tabs ---
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        # --- Unpack Tab ---
        unpack_tab = QWidget()
        self.setup_unpack_tab(unpack_tab)
        self.tabs.addTab(unpack_tab, "解包 (Unpack)")
        
        # --- Repack Tab ---
        repack_tab = QWidget()
        self.setup_repack_tab(repack_tab)
        self.tabs.addTab(repack_tab, "打包 (Repack)")
        
        # --- Log ---
        log_label = QLabel("日志:")
        main_layout.addWidget(log_label)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("LogConsole")
        main_layout.addWidget(self.log_text)
        
        # Status
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0) # Indeterminate
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

    def setup_unpack_tab(self, tab):
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        tab.setLayout(layout)
        
        # Input
        self.unpack_input_edit = self.create_file_selector(
            layout, 
            "输入文件/目录 (.b):", 
            is_input=True,
            on_change=lambda: self.auto_fill_output('unpack')
        )
        
        # Output
        self.unpack_output_edit = self.create_file_selector(layout, "输出目录 (可选):", is_input=False)
        
        layout.addSpacing(20)
        
        # Action Button
        btn_layout = QHBoxLayout()
        self.btn_unpack = ModernButton("执行解包", is_primary=True)
        self.btn_unpack.clicked.connect(self.run_unpack)
        btn_layout.addWidget(self.btn_unpack)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        layout.addStretch()

    def setup_repack_tab(self, tab):
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        tab.setLayout(layout)
        
        # Input
        self.repack_input_edit = self.create_file_selector(
            layout, 
            "输入目录 (包含 metadata.json):", 
            is_input=True,
            on_change=lambda: self.auto_fill_output('repack')
        )
        
        # Output
        self.repack_output_edit = self.create_file_selector(layout, "输出目录 (可选):", is_input=False)
        
        layout.addSpacing(20)
        
        # Action Button
        btn_layout = QHBoxLayout()
        self.btn_repack = ModernButton("执行打包", is_primary=True)
        self.btn_repack.clicked.connect(self.run_repack)
        btn_layout.addWidget(self.btn_repack)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        layout.addStretch()

    def create_file_selector(self, parent_layout, label_text, is_input=True, on_change=None):
        # Vertical container for Label + Controls
        container = QVBoxLayout()
        container.setSpacing(5)
        
        label = QLabel(label_text)
        container.addWidget(label)
        
        # Horizontal row for Edit + Buttons
        row = QHBoxLayout()
        row.setSpacing(8)
        
        edit = DragDropLineEdit()
        edit.setPlaceholderText("拖拽文件/文件夹到此处...")
        if on_change:
            edit.file_dropped.connect(on_change)
        row.addWidget(edit)
        
        if is_input:
            btn_file = ModernButton("文件", is_primary=False)
            btn_file.clicked.connect(lambda: self.browse_file(edit, on_change))
            row.addWidget(btn_file)
            
            btn_folder = ModernButton("目录", is_primary=False)
            btn_folder.clicked.connect(lambda: self.browse_folder(edit, on_change))
            row.addWidget(btn_folder)
        else:
            # Output is typically a directory
            btn_folder = ModernButton("目录", is_primary=False)
            btn_folder.clicked.connect(lambda: self.browse_folder(edit))
            row.addWidget(btn_folder)
            
        container.addLayout(row)
        parent_layout.addLayout(container)
        return edit

    def browse_folder(self, line_edit, on_change=None):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            line_edit.setText(os.path.normpath(path))
            if on_change:
                on_change()

    def browse_file(self, line_edit, on_change=None):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if path:
            line_edit.setText(os.path.normpath(path))
            if on_change:
                on_change()

    def auto_fill_output(self, mode):
        if mode == 'unpack':
            input_path = self.unpack_input_edit.text().strip()
            if input_path:
                base_path = os.path.splitext(input_path)[0]
                # 用户要求：解包输出目录（容器）加 _out 后缀
                self.unpack_output_edit.setText(f"{base_path}_out")
        elif mode == 'repack':
            input_path = self.repack_input_edit.text().strip()
            if input_path:
                # 用户要求：打包输出目录自动加 _repack 后缀
                # 如果输入是 "Folder"，输出就是 "Folder_repack"
                # 如果输入是 "Folder_out"，输出可以是 "Folder_out_repack" 或 "Folder_repack"
                # 这里简单直接加 _repack，确保唯一性
                if input_path.endswith("/") or input_path.endswith("\\"):
                    input_path = input_path[:-1]
                
                out_path = f"{input_path}_repack"
                self.repack_output_edit.setText(out_path)

    def run_unpack(self):
        input_path = self.unpack_input_edit.text().strip()
        output_path = self.unpack_output_edit.text().strip()
        
        if not input_path:
            QMessageBox.warning(self, "提示", "请选择输入文件或目录")
            return
            
        self.start_worker('unpack', input_path, output_path)

    def run_repack(self):
        input_path = self.repack_input_edit.text().strip()
        output_path = self.repack_output_edit.text().strip()
        
        if not input_path:
            QMessageBox.warning(self, "提示", "请选择输入目录")
            return
            
        self.start_worker('repack', input_path, output_path)

    def start_worker(self, mode, input_path, output_path):
        self.set_ui_enabled(False)
        self.progress_bar.show()
        self.log_text.clear()
        
        # Use QThread
        self.worker_thread = QThread()
        self.worker = WorkerThread(mode, input_path, output_path)
        self.worker.moveToThread(self.worker_thread)
        
        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(self.on_finished)
        
        self.worker.log_signal.connect(self.append_log)
        
        self.worker_thread.start()

    def on_finished(self):
        self.progress_bar.hide()
        self.set_ui_enabled(True)
        QMessageBox.information(self, "完成", "任务已完成")

    def set_ui_enabled(self, enabled):
        self.btn_unpack.setEnabled(enabled)
        self.btn_repack.setEnabled(enabled)
        self.unpack_input_edit.setEnabled(enabled)
        self.unpack_output_edit.setEnabled(enabled)
        self.repack_input_edit.setEnabled(enabled)
        self.repack_output_edit.setEnabled(enabled)

    def append_log(self, text):
        self.log_text.append(text)
        
    def detect_system_theme(self):
        self.theme_combo.setCurrentText("跟随系统")
        self.apply_theme("跟随系统")

    def apply_theme(self, theme_name):
        real_theme = theme_name
        if theme_name == "跟随系统":
            if HAS_DARKDETECT and darkdetect.isDark():
                real_theme = "现代深色"
            else:
                real_theme = "现代浅色"
        
        # Styles from GUI_ws2.py
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
        """
        
        if real_theme == "现代深色":
            self.setStyleSheet(dark_qss)
        elif real_theme == "赛博朋克":
            self.setStyleSheet(cyber_qss)
        else:
            self.setStyleSheet(light_qss)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = QLIEGUI()
    window.show()
    sys.exit(app.exec())
