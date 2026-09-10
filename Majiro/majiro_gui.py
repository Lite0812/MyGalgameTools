import sys
import os
import glob
import shutil
import traceback
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QFileDialog, QComboBox, QFrame, QMessageBox,
                             QSplitter, QCheckBox, QGroupBox, QLineEdit, QTabWidget, QTextEdit,
                             QSizePolicy)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QMimeData, QUrl
from PyQt6.QtGui import QIcon, QFont, QColor, QAction, QDragEnterEvent, QDropEvent, QTextCursor

# Try to import darkdetect for system theme detection
try:
    import darkdetect
    HAS_DARKDETECT = True
except ImportError:
    HAS_DARKDETECT = False

# Import Majiro tools
try:
    from majiro_disassembler import Disassembler
    from majiro_assembler import Assembler
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from majiro_disassembler import Disassembler
    from majiro_assembler import Assembler

# ============================================================
# Stream Redirector (Captured from GUI.py)
# ============================================================
class StreamRedirector(object):
    def __init__(self, signal=None):
        self.signal = signal
        self._original_stdout = sys.__stdout__
        base_stdout = sys.__stdout__ if sys.__stdout__ else sys.stdout
        self._console_encoding = getattr(base_stdout, "encoding", None) or 'utf-8'
        self.encoding = self._console_encoding

    def write(self, text):
        if self.signal:
            try:
                msg = str(text)
                if msg:
                    self.signal.emit(msg)
            except:
                pass

        if self._original_stdout:
            try:
                if hasattr(self._original_stdout, 'buffer'):
                    # Handle binary/encoding issues if necessary, but keep it simple for now
                    pass 
                self._original_stdout.write(text)
                self._original_stdout.flush()
            except Exception:
                pass

    def flush(self):
        if self._original_stdout:
            try:
                self._original_stdout.flush()
            except:
                pass

# ============================================================
# UI Components (Referencing GUI.py)
# ============================================================
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
        self.setMinimumHeight(35)

class DragDropLineEdit(QLineEdit):
    def __init__(self, parent=None, is_folder=False):
        super().__init__(parent)
        self.is_folder = is_folder
        self.setAcceptDrops(True)
        self.setPlaceholderText("可直接拖入文件或文件夹..." if is_folder else "可直接拖入文件...")

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

# ============================================================
# Worker Thread
# ============================================================
class WorkerThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, mode, input_path, output_path, encoding, encrypt=False, other_encoding=None):
        super().__init__()
        self.mode = mode
        self.input_path = input_path
        self.output_path = output_path
        self.encoding = encoding
        self.other_encoding = other_encoding or encoding
        self.encrypt = encrypt
        self.is_running = True

    def run(self):
        # Redirect stdout/stderr to capture prints from imported modules
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        redirector = StreamRedirector(self.log_signal)
        sys.stdout = redirector
        sys.stderr = redirector

        try:
            if os.path.isdir(self.input_path):
                self.process_directory()
            else:
                # Fallback if output_path is empty for single file
                out = self.output_path
                if not out:
                    if self.mode == 'disassemble':
                        out = os.path.splitext(self.input_path)[0] + ".mjil"
                    else:
                        out = os.path.splitext(self.input_path)[0] + ".mjo"
                
                # If user provided a directory as output path for a single file, append filename
                if out and os.path.isdir(out):
                    fname = os.path.basename(self.input_path)
                    if self.mode == 'disassemble':
                        fname = os.path.splitext(fname)[0] + ".mjil"
                    else:
                        fname = os.path.splitext(fname)[0] + ".mjo"
                    out = os.path.join(out, fname)

                # Ensure output directory exists for single file case too
                if out:
                    os.makedirs(os.path.dirname(out), exist_ok=True)

                self.process_single_file(self.input_path, out)
            
            self.finished_signal.emit(True, "任务完成")
        except Exception as e:
            self.log_signal.emit(f"\nError: {traceback.format_exc()}")
            self.finished_signal.emit(False, str(e))
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    def process_directory(self):
        if self.mode == 'disassemble':
            pattern = "**/*.mjo"
        else:
            pattern = "**/*.mjil"
            
        # Recursive search
        files = glob.glob(os.path.join(self.input_path, pattern), recursive=True)
        total = len(files)
        
        print(f"共发现 {total} 个文件待处理。")
        
        success_count = 0
        for i, fpath in enumerate(files):
            if not self.is_running: break
            
            try:
                # Determine output path
                if self.output_path:
                    # If output path is specified, mirror the directory structure
                    rel_path = os.path.relpath(fpath, self.input_path)
                    if self.mode == 'disassemble':
                        rel_path = os.path.splitext(rel_path)[0] + ".mjil"
                    else:
                        rel_path = os.path.splitext(rel_path)[0] + ".mjo"
                    
                    out = os.path.join(self.output_path, rel_path)
                    
                    # Ensure output directory exists
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                else:
                    # Default: same directory as input
                    if self.mode == 'disassemble':
                        out = os.path.splitext(fpath)[0] + ".mjil"
                    else:
                        out = os.path.splitext(fpath)[0] + ".mjo"
                
                print(f"[{i+1}/{total}] 正在处理: {os.path.basename(fpath)}")
                self.process_single_file(fpath, out)
                success_count += 1
            except Exception as e:
                print(f"处理失败 {os.path.basename(fpath)}: {e}")
        
        print(f"批量处理完成。成功: {success_count}/{total}")

    def process_single_file(self, infile, outfile):
        if self.mode == 'disassemble':
            with open(infile, 'rb') as f:
                disassembler = Disassembler(encoding=self.encoding)
                script = disassembler.disassemble_script(f)
            
            with open(outfile, 'w', encoding='utf-8') as f:
                disassembler.print_script(script, f)
                
        elif self.mode == 'assemble':
            with open(infile, 'r', encoding='utf-8') as f:
                text = f.read()
            
            assembler = Assembler(encoding=self.encoding, other_encoding=self.other_encoding)
            script, bytecode = assembler.assemble(text)
            
            with open(outfile, 'wb') as f:
                assembler.save(f, script, bytecode, encrypt=self.encrypt)

    def stop(self):
        self.is_running = False

# ============================================================
# Main Window
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Majiro Tools GUI")
        self.resize(900, 800)
        self.setObjectName("MainBackground")
        
        self.worker = None
        
        self.init_ui()
        self.detect_system_theme()

    def init_ui(self):
        central_widget = QWidget()
        central_widget.setObjectName("MainBackground")
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # === Header ===
        header_layout = QHBoxLayout()
        title_label = QLabel("Majiro Tools")
        title_label.setObjectName("AppTitle")
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["跟随系统", "现代浅色", "现代深色", "赛博朋克"])
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        self.theme_combo.setFixedWidth(120)
        header_layout.addWidget(self.theme_combo)
        
        main_layout.addLayout(header_layout)

        # === Tabs ===
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        self.setup_majiro_tab()
        
        # === Log Console ===
        log_card = CardFrame()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)
        
        log_header = QWidget()
        log_header.setObjectName("LogHeader")
        lh_layout = QHBoxLayout(log_header)
        lh_layout.setContentsMargins(10, 5, 10, 5)
        lh_layout.addWidget(QLabel("📝 运行日志 (Log)"))
        lh_layout.addStretch()
        clear_btn = QPushButton("清除")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet("border:none; font-weight:bold; color: #888;")
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        lh_layout.addWidget(clear_btn)
        log_layout.addWidget(log_header)
        
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("LogConsole")
        log_layout.addWidget(self.log_view)
        
        # Add log card to bottom
        main_layout.addWidget(log_card)
        main_layout.setStretch(1, 2) # Tabs
        main_layout.setStretch(2, 1) # Log

    def setup_majiro_tab(self):
        # --- Tab 1: Disassemble (Extract) ---
        dis_tab = QWidget()
        dis_tab_layout = QVBoxLayout(dis_tab)
        dis_tab_layout.setContentsMargins(15, 15, 15, 15)
        
        dis_card = CardFrame()
        dis_layout = QVBoxLayout(dis_card)
        dis_layout.setContentsMargins(15, 15, 15, 15)
        
        dis_layout.addWidget(QLabel("🔓 反汇编 (Disassemble / Extract)"))
        dis_layout.addSpacing(10)
        
        dis_layout.addWidget(QLabel("📂 输入文件/目录 (.mjo)"))
        self.dis_input_edit = DragDropLineEdit(is_folder=True)
        self.dis_input_edit.textChanged.connect(lambda: self.auto_fill_output('dis'))
        dis_layout.addWidget(self.dis_input_edit)
        
        dis_layout.addWidget(QLabel("💾 输出路径 (可选)"))
        self.dis_output_edit = DragDropLineEdit()
        dis_layout.addWidget(self.dis_output_edit)
        
        dis_layout.addSpacing(10)
        dis_layout.addWidget(QLabel("🔤 读取编码 (Read Encoding)"))
        self.dis_enc_combo = QComboBox()
        self.dis_enc_combo.setEditable(True)
        self.dis_enc_combo.addItems(["cp932", "shift_jis", "gbk", "gb18030", "big5", "big-5", "utf-8"])
        dis_layout.addWidget(self.dis_enc_combo)
        
        dis_layout.addStretch()
        self.btn_dis = ModernButton("执行反汇编 (Extract)", is_primary=True)
        self.btn_dis.clicked.connect(self.run_disassemble)
        dis_layout.addWidget(self.btn_dis)
        
        dis_tab_layout.addWidget(dis_card)
        self.tabs.addTab(dis_tab, "反汇编 (Extract)")
        
        # --- Tab 2: Assemble (Repack) ---
        asm_tab = QWidget()
        asm_tab_layout = QVBoxLayout(asm_tab)
        asm_tab_layout.setContentsMargins(15, 15, 15, 15)
        
        asm_card = CardFrame()
        asm_layout = QVBoxLayout(asm_card)
        asm_layout.setContentsMargins(15, 15, 15, 15)
        
        asm_layout.addWidget(QLabel("🔒 汇编 (Assemble / Repack)"))
        asm_layout.addSpacing(10)
        
        asm_layout.addWidget(QLabel("📂 输入文件/目录 (.mjil)"))
        self.asm_input_edit = DragDropLineEdit(is_folder=True)
        self.asm_input_edit.textChanged.connect(lambda: self.auto_fill_output('asm'))
        asm_layout.addWidget(self.asm_input_edit)
        
        asm_layout.addWidget(QLabel("💾 输出路径 (可选)"))
        self.asm_output_edit = DragDropLineEdit()
        asm_layout.addWidget(self.asm_output_edit)
        
        asm_layout.addSpacing(10)
        asm_layout.addWidget(QLabel("🔤 写入编码 (Write Encoding)"))
        self.asm_enc_combo = QComboBox()
        self.asm_enc_combo.setEditable(True)
        self.asm_enc_combo.addItems(["gbk", "cp932", "shift_jis", "gb18030", "big5", "big-5", "utf-8"])
        asm_layout.addWidget(self.asm_enc_combo)

        asm_layout.addSpacing(8)
        asm_layout.addWidget(QLabel("🔤 其它字符串编码 (非 text)"))
        self.asm_other_enc_combo = QComboBox()
        self.asm_other_enc_combo.setEditable(True)
        self.asm_other_enc_combo.addItems(["cp932", "shift_jis", "gbk", "gb18030", "big5", "big-5", "utf-8"])
        asm_layout.addWidget(self.asm_other_enc_combo)
        
        self.asm_encrypt_cb = QCheckBox("加密输出 (Encrypt .mjo)")
        self.asm_encrypt_cb.setChecked(True)
        asm_layout.addWidget(self.asm_encrypt_cb)
        
        asm_layout.addStretch()
        self.btn_asm = ModernButton("执行汇编 (Repack)", is_primary=True)
        self.btn_asm.clicked.connect(self.run_assemble)
        asm_layout.addWidget(self.btn_asm)
        
        asm_tab_layout.addWidget(asm_card)
        self.tabs.addTab(asm_tab, "汇编 (Repack)")

    def auto_fill_output(self, mode):
        if mode == 'dis':
            path = self.dis_input_edit.text()
            if path and not os.path.isdir(path) and path.lower().endswith('.mjo'):
                self.dis_output_edit.setText(os.path.splitext(path)[0] + ".mjil")
        elif mode == 'asm':
            path = self.asm_input_edit.text()
            if path and not os.path.isdir(path) and path.lower().endswith('.mjil'):
                self.asm_output_edit.setText(os.path.splitext(path)[0] + ".mjo")

    def run_disassemble(self):
        input_path = self.dis_input_edit.text().strip()
        if not input_path: return
        output_path = self.dis_output_edit.text().strip()
        encoding = self.dis_enc_combo.currentText()
        
        self.start_worker('disassemble', input_path, output_path, encoding)

    def run_assemble(self):
        input_path = self.asm_input_edit.text().strip()
        if not input_path: return
        output_path = self.asm_output_edit.text().strip()
        encoding = self.asm_enc_combo.currentText()
        other_encoding = self.asm_other_enc_combo.currentText()
        encrypt = self.asm_encrypt_cb.isChecked()
        
        self.start_worker('assemble', input_path, output_path, encoding, encrypt, other_encoding)

    def start_worker(self, mode, input_path, output_path, encoding, encrypt=False, other_encoding=None):
        self.toggle_ui(False)
        self.log_view.clear()
        mode_cn = "反汇编" if mode == 'disassemble' else "汇编"
        self.log_view.append(f"--- 开始任务: {mode_cn} ({mode}) ---")
        self.log_view.append(f"输入路径: {input_path}")
        self.log_view.append(f"使用编码: {encoding}")
        if mode == 'assemble':
            self.log_view.append(f"其它字符串编码: {other_encoding or encoding}")
        self.worker = WorkerThread(mode, input_path, output_path, encoding, encrypt, other_encoding)
        self.worker.log_signal.connect(self.log_message)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def log_message(self, msg):
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        # Avoid duplicate newlines if msg already has them
        if msg.endswith('\n'):
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
        self.btn_dis.setEnabled(enabled)
        self.btn_asm.setEnabled(enabled)
        self.tabs.setEnabled(enabled)

    def detect_system_theme(self):
        # Default to "跟随系统" which will internally check system theme
        self.theme_combo.setCurrentText("跟随系统")
        # Manually trigger apply_theme since setText might not fire signal if it's already first item
        self.apply_theme("跟随系统")

    def apply_theme(self, theme_name):
        real_theme = theme_name
        
        if theme_name == "跟随系统":
            if HAS_DARKDETECT and darkdetect.isDark():
                real_theme = "现代深色"
            else:
                real_theme = "现代浅色"

        # Referenced from GUI.py
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
