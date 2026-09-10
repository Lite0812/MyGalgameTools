import sys
import os
import io
import copy
import ctypes
import shutil
import subprocess
import time
import traceback
import threading

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTextEdit, QTabWidget, QFileDialog, QRadioButton, 
                             QCheckBox, QComboBox, QButtonGroup, 
                             QMessageBox, QSplitter, QFrame, QDialog, QListWidget, QGroupBox, QSizePolicy, QGridLayout)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor, QDropEvent

class KeyFinderThread(QThread):
    finished = pyqtSignal(bool, str)
    log = pyqtSignal(str)

    def __init__(self, game_exe_path):
        super().__init__()
        self.game_exe_path = game_exe_path

    def run(self):
        process = None
        found_key = None
        fail_reason = None
        log_file = None
        target_dll = None
        target_ini = None
        key_output = None
        backup_map = {}
        try:
            game_dir = os.path.dirname(self.game_exe_path)
            tool_dll = os.path.join(os.getcwd(), "tools", "version.dll")
            target_dll = os.path.join(game_dir, "version.dll")
            target_ini = os.path.join(game_dir, "version.ini")
            key_output = os.path.join(game_dir, "siglus_key.txt")
            log_file = os.path.join(game_dir, "HookFont.log")

            for path in (target_dll, target_ini, key_output):
                if os.path.exists(path):
                    backup_path = path + ".siglus_bak"
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                    shutil.move(path, backup_path)
                    backup_map[path] = backup_path
                    self.log.emit(f"备份同名文件: {backup_path}")

            if not os.path.exists(tool_dll):
                raise FileNotFoundError(f"找不到工具 DLL: {tool_dll}")

            self.log.emit(f"复制 DLL 到: {target_dll}")
            shutil.copy2(tool_dll, target_dll)

            self.log.emit(f"创建配置文件: {target_ini}")
            with open(target_ini, 'w', encoding='utf-8') as f:
                f.write("[SiglusKeyExtract]\n")
                f.write("Enable=true\n")
                f.write("GameexePath=Gameexe.dat\n")
                f.write(f"KeyOutputPath={os.path.basename(key_output)}\n")
                f.write("ShowMessageBox=false\n")
                f.write("DebugMode=false\n")
                f.write("[HookFont]\n")
                f.write("Charset=0x00\n")
                f.write("Font=SimHei\n")
                f.write("[FilePatch]\n")
                f.write("Enable=false\n")
                f.write("EnableLog=false\n")
                f.write("PatchFolder=patch\n")
                f.write("[LoadMode]\n")
                f.write("Mode=proxy\n")

            self.log.emit("启动游戏...")
            # 使用 shell=False 启动，不阻塞
            process = subprocess.Popen([self.game_exe_path], cwd=game_dir)
            
            self.log.emit("等待密钥生成...")
            found_key = None
            
            # 等待最多 5 秒
            for _ in range(5):
                # 1. 检查游戏进程是否已退出
                if process.poll() is not None:
                    self.log.emit("检测到游戏已关闭")
                    break

                # 2. 检查密钥文件
                if os.path.exists(key_output):
                    try:
                        with open(key_output, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                        if content:
                            found_key = content
                            break
                    except:
                        pass
                
                # 3. 检查日志文件判断是否失败 (根据 HookFont 日志)
                if os.path.exists(log_file):
                    try:
                        with open(log_file, 'r', encoding='utf-8', errors='ignore') as lf:
                            log_content = lf.read()
                            # 检查关键错误信息 (根据实际情况调整)
                            if "SiglusKeyExtract: Failed" in log_content or "Pattern not found" in log_content:
                                self.log.emit("检测到提取失败日志")
                                break
                    except:
                        pass

                time.sleep(1)
           
            if not found_key:
                fail_reason = "未能在5秒内获取到密钥"
                if log_file and os.path.exists(log_file):
                    try:
                        with open(log_file, 'r', encoding='utf-8', errors='ignore') as lf:
                            log_tail = lf.read()[-200:]
                            fail_reason += f"\n日志末尾: {log_tail}"
                    except:
                        pass

        except Exception as e:
            fail_reason = str(e)
        finally:
            self.log.emit("关闭游戏...")
            try:
                if process and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2)
            except:
                try:
                    if process:
                        process.kill()
                except:
                    pass

            self.log.emit("清理临时文件...")
            try:
                if target_dll and os.path.exists(target_dll): os.remove(target_dll)
                if target_ini and os.path.exists(target_ini): os.remove(target_ini)
                if key_output and os.path.exists(key_output): os.remove(key_output)
                if log_file and os.path.exists(log_file): os.remove(log_file)
                patch_dir = os.path.join(game_dir, "patch")
                if os.path.exists(patch_dir) and not os.listdir(patch_dir):
                    os.rmdir(patch_dir)
            except Exception as e:
                self.log.emit(f"清理失败 (可忽略): {e}")

            for original_path, backup_path in backup_map.items():
                try:
                    if os.path.exists(backup_path):
                        if os.path.exists(original_path):
                            os.remove(original_path)
                        shutil.move(backup_path, original_path)
                except Exception as e:
                    self.log.emit(f"恢复备份失败 (可忽略): {e}")

        if found_key:
            self.finished.emit(True, found_key)
        else:
            self.finished.emit(False, fail_reason or "未能在5秒内获取到密钥")


# 尝试导入同目录下的脚本模块
try:
    import ssTextExtractor
    import ssTextPacker
    import siglus_decrypt_unpack
except ImportError:
    pass

# ============================================================
# 【核心修复】防爆日志重定向器 (Ultra Safe Version v2)
# ============================================================
class StreamRedirector(object):
    def __init__(self, signal=None):
        self.signal = signal
        self._original_stdout = sys.__stdout__
        base_stdout = sys.__stdout__ if sys.__stdout__ else sys.stdout
        self._console_encoding = getattr(base_stdout, "encoding", None) or 'utf-8'
        self.encoding = self._console_encoding

    def write(self, text):
        # 过滤烦人的 libpng 警告
        if "libpng warning" in str(text) and "iCCP" in str(text):
            return

        if self.signal:
            try:
                msg = str(text)
                if "打包场景中" in msg and '\n' not in msg and '\r' not in msg:
                    idx = msg.rfind("打包场景中")
                    self.signal.emit('\r' + msg[idx:])
                elif '\r' in msg:
                    parts = msg.split('\r')
                    for part in parts[:-1]:
                        if part:
                            self.signal.emit(part)
                    if parts[-1]:
                        self.signal.emit('\r' + parts[-1])
                elif msg.strip():
                    self.signal.emit(msg)
                elif msg == '\n':
                    self.signal.emit('\n')
            except:
                pass

        if self._original_stdout:
            try:
                if hasattr(self._original_stdout, 'buffer'):
                    encoded_bytes = text.encode(self._console_encoding, errors='replace')
                    self._original_stdout.buffer.write(encoded_bytes)
                    self._original_stdout.buffer.flush()
                else:
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
# 工作线程
# ============================================================
class WorkerThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, task_type, args):
        super().__init__()
        self.task_type = task_type
        self.args = args

    def run(self):
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        
        redirector = StreamRedirector(self.log_signal)
        sys.stdout = redirector
        sys.stderr = redirector

        success = False
        msg = ""

        try:
            self.log_signal.emit(f"--- 🚀 开始任务: {self.task_type} ---\n")
            
            argv = ['GUI_CALL'] + copy.deepcopy(self.args)

            if self.task_type == "extract":
                if 'ssTextExtractor' in sys.modules:
                    result = ssTextExtractor.main(argv)
                    if result is not False: 
                        success = True
                        msg = "提取成功完成"
                    else:
                        msg = "提取过程中出现错误"
                else:
                    msg = "找不到 ssTextExtractor.py 模块"

            elif self.task_type == "pack":
                if 'ssTextPacker' in sys.modules:
                    result = ssTextPacker.main(argv)
                    if result is True or result == 0:
                        success = True
                        msg = "封回成功完成"
                    else:
                        msg = "封回完成，但存在错误 (或未找到文件)"
                else:
                    msg = "找不到 ssTextPacker.py 模块"

            elif self.task_type == "decrypt_gameexe":
                if 'siglus_decrypt_unpack' in sys.modules:
                    private_key = None
                    if len(self.args) >= 2 and self.args[1]:
                        # args[1] 可能是字符串路径或bytes对象
                        if isinstance(self.args[1], bytes):
                            private_key = self.args[1]
                        elif isinstance(self.args[1], str):
                            private_key = siglus_decrypt_unpack.read_key_file(self.args[1])
                    result = siglus_decrypt_unpack.decrypt_gameexe(self.args[0], private_key, self.args[2] if len(self.args) >= 3 else None)
                    if result:
                        success = True
                        msg = "Gameexe.dat 解密成功"
                    else:
                        msg = "Gameexe.dat 解密失败"
                else:
                    msg = "找不到 siglus_decrypt_unpack.py 模块"

            elif self.task_type == "decrypt_scene":
                if 'siglus_decrypt_unpack' in sys.modules:
                    private_key = None
                    if len(self.args) >= 2 and self.args[1]:
                        # args[1] 可能是字符串路径或bytes对象
                        if isinstance(self.args[1], bytes):
                            private_key = self.args[1]
                        elif isinstance(self.args[1], str):
                            private_key = siglus_decrypt_unpack.read_key_file(self.args[1])
                    result = siglus_decrypt_unpack.unpack_scene_pck(self.args[0], private_key, self.args[2] if len(self.args) >= 3 else None)
                    if result:
                        success = True
                        msg = "Scene.pck 解包成功"
                    else:
                        msg = "Scene.pck 解包失败"
                else:
                    msg = "找不到 siglus_decrypt_unpack.py 模块"

            elif self.task_type == "pack_gameexe":
                if 'siglus_decrypt_unpack' in sys.modules:
                    private_key = None
                    if len(self.args) >= 2 and self.args[1]:
                        # args[1] 可能是字符串路径或bytes对象
                        if isinstance(self.args[1], bytes):
                            private_key = self.args[1]
                        elif isinstance(self.args[1], str):
                            private_key = siglus_decrypt_unpack.read_key_file(self.args[1])
                    use_key = not (len(self.args) >= 4 and self.args[3] == '--no-key')
                    compression_level = int(self.args[4]) if len(self.args) >= 5 else 17
                    result = siglus_decrypt_unpack.encrypt_gameexe(self.args[0], private_key, self.args[2] if len(self.args) >= 3 else None, use_key, compression_level)
                    if result:
                        success = True
                        msg = "Gameexe.dat 打包成功"
                    else:
                        msg = "Gameexe.dat 打包失败"
                else:
                    msg = "找不到 siglus_decrypt_unpack.py 模块"

            elif self.task_type == "pack_scene":
                if 'siglus_decrypt_unpack' in sys.modules:
                    private_key = None
                    if len(self.args) >= 3 and self.args[2]:
                        # args[2] 可能是字符串路径或bytes对象
                        if isinstance(self.args[2], bytes):
                            private_key = self.args[2]
                        elif isinstance(self.args[2], str):
                            private_key = siglus_decrypt_unpack.read_key_file(self.args[2])
                    use_key = not (len(self.args) >= 5 and self.args[4] == '--no-key')
                    compression_level = int(self.args[5]) if len(self.args) >= 6 else 17
                    result = siglus_decrypt_unpack.pack_scene_pck(self.args[0], self.args[1], private_key, self.args[3] if len(self.args) >= 4 else None, use_key, compression_level)
                    if result:
                        success = True
                        msg = "Scene.pck 打包成功"
                    else:
                        msg = "Scene.pck 打包失败"
                else:
                    msg = "找不到 siglus_decrypt_unpack.py 模块"

        except Exception as e:
            import traceback
            err_msg = traceback.format_exc()
            self.log_signal.emit(f"\n❌ 发生严重错误:\n{err_msg}\n")
            msg = f"异常中止: {str(e)}"
            success = False
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            self.finished_signal.emit(success, msg)

# ============================================================
# 自定义 UI 组件
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
        self.setMinimumHeight(30) # 减小按钮高度

class DragDropLineEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
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

class KeySelector(QWidget):
    key_sync_signal = pyqtSignal(int, str)
    exe_path_sync_signal = pyqtSignal(str) # 新增 EXE 路径同步信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keys = []
        self.key_file = "SiglusKey.txt"
        self.init_ui()
        self.load_keys()
        
    def init_ui(self):
        # 标签式分组设计 - 紧凑型
        self.group_box = QGroupBox("🔑 密钥设置 (Key Settings)")
        # 使用网格布局以获得更好的对齐和空间利用，确保密钥能完整显示
        layout = QGridLayout(self.group_box)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setVerticalSpacing(5)
        layout.setHorizontalSpacing(10)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.group_box)
        
        # 0. 游戏主程序选择 (新增)
        exe_label = QLabel("游戏主程序:")
        self.exe_path_edit = DragDropLineEdit()
        self.exe_path_edit.setPlaceholderText("拖入 SiglusEngine.exe 或点击浏览...")
        self.exe_path_edit.textChanged.connect(self.on_exe_path_changed) # 连接信号
        
        browse_btn = ModernButton("浏览")
        browse_btn.setFixedWidth(60)
        browse_btn.clicked.connect(self.browse_exe)
        
        # 寻找密钥按钮 (移动到这里)
        self.btn_find_key = ModernButton("🔍 寻找密钥")
        self.btn_find_key.setToolTip("选择游戏EXE启动并自动提取密钥")
        self.btn_find_key.clicked.connect(self.find_key)
        self.btn_find_key.setFixedWidth(100)

        layout.addWidget(exe_label, 0, 0)
        layout.addWidget(self.exe_path_edit, 0, 1)
        
        # 按钮容器布局
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(5)
        btn_layout.addWidget(browse_btn)
        btn_layout.addWidget(self.btn_find_key)
        layout.addLayout(btn_layout, 0, 2) # 放在第0行第2列

        # 1. 预设游戏选择
        game_label = QLabel("预设游戏:")
        self.game_combo = QComboBox()
        self.game_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.game_combo.currentIndexChanged.connect(self.on_game_selected)
        
        layout.addWidget(game_label, 1, 0)
        layout.addWidget(self.game_combo, 1, 1, 1, 2) # 跨两列

        # 2. 密钥显示/输入
        key_label = QLabel("密钥 (Hex):")
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("格式: 0x73, 0x5C, ...")
        self.key_edit.textChanged.connect(self.on_key_edited)
        
        layout.addWidget(key_label, 2, 0)
        layout.addWidget(self.key_edit, 2, 1, 1, 2) # 跨两列

    def browse_exe(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择游戏主程序 (SiglusEngine.exe)", "", "Executable (*.exe)")
        if file_path:
            self.exe_path_edit.setText(file_path)

    def on_exe_path_changed(self, text):
        self.exe_path_sync_signal.emit(text)

    def apply_exe_path_sync(self, text):
        """接收 EXE 路径同步信号并应用"""
        if self.exe_path_edit.text() != text:
            self.exe_path_edit.blockSignals(True)
            self.exe_path_edit.setText(text)
            self.exe_path_edit.blockSignals(False)

    def find_key(self):

        file_path = self.exe_path_edit.text().strip()
        if not file_path:
            self.browse_exe()
            file_path = self.exe_path_edit.text().strip()
            
        if not file_path or not os.path.exists(file_path):
            QMessageBox.warning(self, "提示", "请先选择有效的游戏主程序路径 (SiglusEngine.exe)")
            return
            
        self.btn_find_key.setEnabled(False)
        self.btn_find_key.setText("提取中...")
        
        self.finder_thread = KeyFinderThread(file_path)
        # 将日志输出到主窗口的状态栏或打印（暂时打印）
        self.finder_thread.log.connect(lambda msg: print(f"[KeyFinder] {msg}")) 
        self.finder_thread.finished.connect(self.on_find_key_finished)
        self.finder_thread.start()

    def on_find_key_finished(self, success, result):
        self.btn_find_key.setEnabled(True)
        self.btn_find_key.setText("🔍 寻找密钥")
        
        if success:
            QMessageBox.information(self, "成功", f"成功提取密钥") # 不在弹窗中显示冗长的结果
            
            # 解析密钥
            import re
            extracted_key = result
            
            # 1. 尝试匹配逗号分隔的 Hex 格式: 0xXX, 0xXX, ...
            # 这种格式通常比较长，包含16个字节
            hex_array_pattern = r"((?:0x[0-9A-Fa-f]{2},\s*){15}0x[0-9A-Fa-f]{2})"
            match = re.search(hex_array_pattern, result)
            if match:
                extracted_key = match.group(1)
            else:
                # 2. 尝试匹配纯 Hex 字符串 (32 chars)
                # 排除可能出现在注释中的 hex 字符串，通常密钥是独立的
                # 查找连续的32个十六进制字符
                hex_pure_pattern = r"\b([0-9A-Fa-f]{32})\b"
                match_pure = re.search(hex_pure_pattern, result)
                if match_pure:
                    raw_hex = match_pure.group(1)
                    # 格式化为 0xXX, ...
                    extracted_key = ", ".join([f"0x{raw_hex[i:i+2]}" for i in range(0, 32, 2)])
            
            self.key_edit.setReadOnly(False)
            self.key_edit.blockSignals(True)
            self.key_edit.setText(extracted_key)
            self.key_edit.blockSignals(False)

            if not self.check_match_preset(extracted_key):
                self.game_combo.blockSignals(True)
                self.game_combo.setCurrentIndex(0)
                self.game_combo.blockSignals(False)
                self.key_sync_signal.emit(0, extracted_key)
        else:
            QMessageBox.warning(self, "失败", f"提取失败: {result}")


    def load_keys(self):
        self.game_combo.blockSignals(True)
        self.game_combo.clear()
        
        # 1. 自定义
        self.game_combo.addItem("自定义 (Custom)", None)
        
        # 2. 上次使用 (如果存在)
        last_key = self.load_last_key_from_file()
        if last_key:
             self.game_combo.addItem(f"上次使用 ({last_key[:100]})", last_key)

        # 3. 预设列表
        if 'siglus_decrypt_unpack' in sys.modules:
            self.keys = siglus_decrypt_unpack.load_key_list()
            for name, key_bytes in self.keys:
                # 去除冒号后缀
                clean_name = name.strip().rstrip(':').rstrip('：')
                self.game_combo.addItem(clean_name, key_bytes)
                
        # 默认选中上次使用
        if last_key:
             self.game_combo.setCurrentIndex(1)
             self.key_edit.setText(last_key)
             # self.key_edit.setReadOnly(True) # 移除只读

        self.game_combo.blockSignals(False)
        
    def on_game_selected(self, index):
        if index == 0: # Custom
            self.key_edit.setReadOnly(False)
            # self.key_edit.clear() # 移除清空，保留当前内容
        else:
            data = self.game_combo.currentData()
            if data:
                if isinstance(data, bytes):
                    # 转换为 0xXX, 0xXX 格式
                    key_str = ', '.join(f'0x{b:02X}' for b in data)
                else:
                    key_str = str(data)
                self.key_edit.setText(key_str)
                # self.key_edit.setReadOnly(True) # 移除只读，允许编辑
        
        # 发送同步信号
        self.key_sync_signal.emit(index, self.key_edit.text())

    def on_key_edited(self, text):
        # 1. 尝试匹配预设密钥
        if self.check_match_preset(text):
            return
        
        # 2. 如果不匹配任何预设，且当前不是自定义模式，则切换到自定义模式
        if self.game_combo.currentIndex() != 0:
            self.game_combo.blockSignals(True)
            self.game_combo.setCurrentIndex(0)
            self.game_combo.blockSignals(False)
        
        self.key_sync_signal.emit(0, text)

    def check_match_preset(self, text):
        """检查当前输入的密钥是否与某个预设密钥匹配"""
        try:
            # 解析当前输入为 bytes
            current_bytes = self.parse_key_to_bytes(text)
            if not current_bytes:
                return False
            
            # 遍历 combo items (比遍历 self.keys 更直接，因为包含了 index)
            count = self.game_combo.count()
            for i in range(count):
                # 跳过自定义(0) 和 上次使用(可能为1)
                # 其实只要比较 data 是 bytes 类型的即可
                item_data = self.game_combo.itemData(i)
                if isinstance(item_data, bytes) and item_data == current_bytes:
                    # 匹配成功！
                    if self.game_combo.currentIndex() != i:
                        self.game_combo.blockSignals(True)
                        self.game_combo.setCurrentIndex(i)
                        self.game_combo.blockSignals(False)
                        
                        # self.key_edit.setReadOnly(True) # 移除只读
                        # 发送同步信号 (index, text)
                        self.key_sync_signal.emit(i, text)
                    return True
        except:
            pass
        return False

    def parse_key_to_bytes(self, text):
        """尝试将各种格式的密钥字符串解析为 bytes"""
        text = text.strip()
        if not text: return None
        
        # 移除常见分隔符和前缀
        clean = text.replace(' ', '').replace('0x', '').replace(',', '').replace('，', '')
        
        try:
            # 标准 16 字节密钥是 32 个 hex 字符
            if len(clean) == 32:
                return bytes.fromhex(clean)
        except:
            pass
        return None

    def apply_sync(self, index, text):
        """接收同步信号并应用状态"""
        self.game_combo.blockSignals(True)
        self.key_edit.blockSignals(True)
        
        if self.game_combo.currentIndex() != index:
            self.game_combo.setCurrentIndex(index)
            
        # 始终允许编辑
        self.key_edit.setReadOnly(False)

        if self.key_edit.text() != text:
            self.key_edit.setText(text)
            
        self.game_combo.blockSignals(False)
        self.key_edit.blockSignals(False)

    def get_key_str(self):
        return self.key_edit.text().strip()

    def load_last_key_from_file(self):
        """仅读取文件内容，不修改UI"""
        if os.path.exists(self.key_file):
            try:
                with open(self.key_file, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except:
                pass
        return None

    def save_current_key_as_last(self):
        """保存当前密钥为上次使用"""
        key_str = self.key_edit.text().strip()
        if not key_str: return
        try:
            with open(self.key_file, 'w', encoding='utf-8') as f:
                f.write(key_str)
            
            # 更新下拉框中的"上次使用"
            self.update_last_used_item(key_str)
        except:
            pass

    def update_last_used_item(self, key_str):
        # 检查是否已有"上次使用"项
        # 假设"上次使用"总是在索引1 (如果有)
        # 或者遍历查找
        found = -1
        for i in range(self.game_combo.count()):
            if "上次使用" in self.game_combo.itemText(i):
                found = i
                break
        
        self.game_combo.blockSignals(True)
        if found != -1:
            self.game_combo.setItemText(found, f"💾 上次使用 ({key_str[:20]}...)")
            self.game_combo.setItemData(found, key_str)
        else:
            self.game_combo.insertItem(1, f"💾 上次使用 ({key_str[:20]}...)", key_str)
        
        # 保持选中状态
        if self.game_combo.currentIndex() == found or found == -1:
             self.game_combo.setCurrentIndex(1 if found == -1 else found)
             
        self.game_combo.blockSignals(False)

# ============================================================
# 主窗口 GUI
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._global_redirector = StreamRedirector()
        sys.stdout = self._global_redirector
        sys.stderr = self._global_redirector
        self.setWindowTitle("Siglus Script Toolkit - SS 脚本处理工具")
        self.resize(850, 700) # 调整默认大小为更紧凑的尺寸
        
        if 'ssTextExtractor' not in sys.modules or 'ssTextPacker' not in sys.modules or 'siglus_decrypt_unpack' not in sys.modules:
            QMessageBox.critical(self, "文件缺失", 
                "无法找到核心脚本。\n请确保 ssTextExtractor.py, ssTextPacker.py, siglus_decrypt_unpack.py 和 Decryption.py 在同一目录下。")

        self.init_ui()
        self.apply_theme("明亮清爽 (默认)")

    def init_ui(self):
        main_widget = QWidget()
        main_widget.setObjectName("MainBackground")
        self.setCentralWidget(main_widget)
        
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)

        # --- 标题栏 ---
        header_layout = QHBoxLayout()
        title_label = QLabel("Siglus Script Toolkit - SS 脚本处理工具")
        title_label.setObjectName("AppTitle")
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        lbl_style = QLabel("界面风格:")
        lbl_style.setObjectName("SubLabel")
        header_layout.addWidget(lbl_style)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["明亮清爽 (默认)", "专业暗黑", "赛博朋克"])
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        self.theme_combo.setFixedWidth(120)
        header_layout.addWidget(self.theme_combo)
        
        main_layout.addLayout(header_layout)

        # --- 分割器 ---
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(2)
        main_layout.addWidget(splitter)

        # 1. 功能 Tabs
        self.tabs = QTabWidget()
        self.setup_gameexe_tab()
        self.setup_scene_tab()
        
        # 同步两个密钥选择器的状态
        self.gameexe_key_selector.key_sync_signal.connect(self.scene_key_selector.apply_sync)
        self.scene_key_selector.key_sync_signal.connect(self.gameexe_key_selector.apply_sync)

        # 同步两个游戏主程序路径的状态
        self.gameexe_key_selector.exe_path_sync_signal.connect(self.scene_key_selector.apply_exe_path_sync)
        self.scene_key_selector.exe_path_sync_signal.connect(self.gameexe_key_selector.apply_exe_path_sync)

        self.setup_extractor_tab()
        self.setup_packer_tab()
        splitter.addWidget(self.tabs)

        # 2. 日志控制台
        log_card = CardFrame()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(0, 0, 0, 0)
        
        log_header = QWidget()
        log_header.setObjectName("LogHeader")
        lh_layout = QHBoxLayout(log_header)
        lh_layout.setContentsMargins(10, 5, 10, 5)
        lh_layout.addWidget(QLabel("📝 运行日志"))
        lh_layout.addStretch()
        clear_btn = QPushButton("清除")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet("border:none; font-weight:bold;")
        clear_btn.setObjectName("ClearButton")
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        lh_layout.addWidget(clear_btn)
        log_layout.addWidget(log_header)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("LogConsole")
        self._last_log_blank = False
        log_layout.addWidget(self.log_view)
        
        splitter.addWidget(log_card)
        # 设置初始分割比例，给日志更多空间
        splitter.setSizes([600, 300])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

    def auto_fill_output(self, mode):
        """根据输入路径自动填充输出路径 (参考 SiglusEngine 逻辑)"""
        if mode == 'dec_gameexe':
            input_path = self.dec_gameexe_file_edit.text().strip()
            if not input_path: return
            
            # Gameexe.dat -> Gameexe.ini
            if input_path.lower().endswith("gameexe.dat"):
                # 保持原目录，只改文件名
                new_path = input_path[:-4] + ".ini"
            else:
                new_path = input_path + ".ini"
            self.dec_gameexe_output_edit.setText(new_path)
            
        elif mode == 'rep_gameexe':
            input_path = self.rep_gameexe_file_edit.text().strip()
            if not input_path: return
            
            # Gameexe.ini/txt -> Gameexe.dat
            if input_path.lower().endswith(".ini"):
                new_path = input_path[:-4] + ".new.dat"
            elif input_path.lower().endswith(".txt"):
                new_path = input_path[:-4] + ".new.dat"
            else:
                new_path = input_path + ".new.dat"
            self.rep_gameexe_output_edit.setText(new_path)
            
        elif mode == 'dec_scene':
            input_path = self.dec_scene_file_edit.text().strip()
            if not input_path: return
            
            # Scene.pck -> Scene (folder)
            if input_path.lower().endswith(".pck"):
                new_path = input_path[:-4]
            else:
                new_path = input_path + "_out"
            self.dec_scene_output_edit.setText(new_path)
            
        elif mode == 'rep_scene':
            input_dir = self.rep_scene_dir_edit.text().strip()
            if not input_dir: return
            
            if input_dir.endswith("/") or input_dir.endswith("\\"):
                input_dir = input_dir[:-1]
            
            # 自动填充输出路径 Scene (folder) -> Scene.pck
            new_path = input_dir + ".new.pck"
            self.rep_scene_output_edit.setText(new_path)

            # 尝试寻找原始 Scene.pck
            # 逻辑：如果目录名为 Scene_out，尝试找 Scene.pck
            pck_name = input_dir
            if input_dir.lower().endswith("_out"):
                pck_name = input_dir[:-4]
            elif input_dir.lower().endswith("_text"):
                pck_name = input_dir[:-5]
            
            potential_pck = pck_name + ".pck"
            if os.path.exists(potential_pck):
                self.rep_scene_pck_edit.setText(potential_pck)
            elif os.path.exists(input_dir + ".pck"):
                 self.rep_scene_pck_edit.setText(input_dir + ".pck")

        elif mode == 'extract':
            input_path = self.ext_input_edit.text().strip()
            if not input_path: return
            
            # .ss -> _text (folder) or .xlsx
            if input_path.lower().endswith(".ss"):
                new_path = input_path[:-3] + "_text"
            elif os.path.isdir(input_path):
                # 如果输入是目录，输出也是目录 + _text
                if input_path.endswith("/") or input_path.endswith("\\"):
                    input_path = input_path[:-1]
                new_path = input_path + "_text"
            else:
                new_path = input_path + "_text"
            self.ext_output_edit.setText(new_path)

    def setup_gameexe_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # 密钥选择器
        self.gameexe_key_selector = KeySelector()
        main_layout.addWidget(self.gameexe_key_selector)

        # 操作区域
        ops_layout = QHBoxLayout()
        ops_layout.setSpacing(10)

        # 左侧：解密
        dec_card = CardFrame()
        dec_layout = QVBoxLayout(dec_card)
        dec_layout.setContentsMargins(10, 10, 10, 10) # 紧凑内边距
        dec_layout.addWidget(QLabel("🔓 解密 Gameexe.dat"))
        dec_layout.addSpacing(5)
        dec_layout.addLayout(self.create_modern_file_selector("📂 输入 Gameexe", "dec_gameexe_file", is_folder=False))
        dec_layout.addLayout(self.create_modern_file_selector("💾 输出路径", "dec_gameexe_output", is_folder=False))
        self.dec_gameexe_file_edit.textChanged.connect(lambda: self.auto_fill_output('dec_gameexe'))
        dec_layout.addSpacing(10)
        self.btn_decrypt_gameexe = ModernButton("执行解密", is_primary=True)
        self.btn_decrypt_gameexe.clicked.connect(self.run_decrypt_gameexe)
        dec_layout.addWidget(self.btn_decrypt_gameexe)
        dec_layout.addStretch()
        ops_layout.addWidget(dec_card, 1)

        # 右侧：打包
        rep_card = CardFrame()
        rep_layout = QVBoxLayout(rep_card)
        rep_layout.setContentsMargins(10, 10, 10, 10) # 紧凑内边距
        rep_layout.addWidget(QLabel("🔒 打包 Gameexe.dat"))
        rep_layout.addSpacing(5)
        rep_layout.addLayout(self.create_modern_file_selector("📂 输入文本", "rep_gameexe_file", is_folder=False))
        rep_layout.addLayout(self.create_modern_file_selector("💾 输出路径", "rep_gameexe_output", is_folder=False))
        self.rep_gameexe_file_edit.textChanged.connect(lambda: self.auto_fill_output('rep_gameexe'))
        rep_layout.addSpacing(5)
        
        opt_layout = QHBoxLayout() # 改为水平
        self.rep_gameexe_use_key_cb = QCheckBox("使用私钥加密")
        self.rep_gameexe_use_key_cb.setChecked(True)
        opt_layout.addWidget(self.rep_gameexe_use_key_cb)
        
        opt_layout.addWidget(QLabel("压缩级别:"))
        self.rep_gameexe_level_edit = QLineEdit("17")
        self.rep_gameexe_level_edit.setFixedWidth(40)
        opt_layout.addWidget(self.rep_gameexe_level_edit)
        opt_layout.addStretch()
        
        rep_layout.addLayout(opt_layout)
        rep_layout.addSpacing(5)
        
        self.btn_repack_gameexe = ModernButton("执行打包", is_primary=True)
        self.btn_repack_gameexe.clicked.connect(self.run_repack_gameexe)
        rep_layout.addWidget(self.btn_repack_gameexe)
        rep_layout.addStretch()
        ops_layout.addWidget(rep_card, 1)

        main_layout.addLayout(ops_layout)
        self.tabs.addTab(tab, "Gameexe 处理")

    def setup_scene_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # 密钥选择器
        self.scene_key_selector = KeySelector()
        main_layout.addWidget(self.scene_key_selector)

        # 操作区域
        ops_layout = QHBoxLayout()
        ops_layout.setSpacing(10)

        # 左侧：解包
        dec_card = CardFrame()
        dec_layout = QVBoxLayout(dec_card)
        dec_layout.setContentsMargins(10, 10, 10, 10)
        dec_layout.addWidget(QLabel("📦 解包 Scene.pck"))
        dec_layout.addSpacing(5)
        dec_layout.addLayout(self.create_modern_file_selector("📂 输入 Scene", "dec_scene_file", is_folder=False))
        dec_layout.addLayout(self.create_modern_file_selector("💾 输出目录", "dec_scene_output", is_folder=True))
        self.dec_scene_file_edit.textChanged.connect(lambda: self.auto_fill_output('dec_scene'))
        dec_layout.addSpacing(10)
        self.btn_decrypt_scene = ModernButton("执行解包", is_primary=True)
        self.btn_decrypt_scene.clicked.connect(self.run_decrypt_scene)
        dec_layout.addWidget(self.btn_decrypt_scene)
        dec_layout.addStretch()
        ops_layout.addWidget(dec_card, 1)

        # 右侧：打包
        rep_card = CardFrame()
        rep_layout = QVBoxLayout(rep_card)
        rep_layout.setContentsMargins(10, 10, 10, 10)
        rep_layout.addWidget(QLabel("📦 打包 Scene.pck"))
        rep_layout.addSpacing(5)
        rep_layout.addLayout(self.create_modern_file_selector("📂 输入 .ss 目录", "rep_scene_dir", is_folder=True))
        rep_layout.addLayout(self.create_modern_file_selector("📂 原始 Scene", "rep_scene_pck", is_folder=False))
        rep_layout.addLayout(self.create_modern_file_selector("💾 输出路径", "rep_scene_output", is_folder=False))
        self.rep_scene_dir_edit.textChanged.connect(lambda: self.auto_fill_output('rep_scene'))
        rep_layout.addSpacing(5)
        
        opt_layout = QHBoxLayout()
        self.rep_scene_use_key_cb = QCheckBox("使用私钥加密")
        self.rep_scene_use_key_cb.setChecked(True)
        opt_layout.addWidget(self.rep_scene_use_key_cb)
        
        opt_layout.addWidget(QLabel("压缩级别:"))
        self.rep_scene_level_edit = QLineEdit("17")
        self.rep_scene_level_edit.setFixedWidth(40)
        opt_layout.addWidget(self.rep_scene_level_edit)
        opt_layout.addStretch()
        
        rep_layout.addLayout(opt_layout)
        rep_layout.addSpacing(5)
        
        self.btn_repack_scene = ModernButton("执行打包", is_primary=True)
        self.btn_repack_scene.clicked.connect(self.run_repack_scene)
        rep_layout.addWidget(self.btn_repack_scene)
        rep_layout.addStretch()
        ops_layout.addWidget(rep_card, 1)

        main_layout.addLayout(ops_layout)
        self.tabs.addTab(tab, "Scene 处理")

    def setup_extractor_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        path_card = CardFrame()
        pc_layout = QVBoxLayout(path_card)
        pc_layout.setContentsMargins(10, 10, 10, 10)
        pc_layout.addLayout(self.create_modern_file_selector("📂 输入路径", "ext_input", is_folder=False))
        pc_layout.addLayout(self.create_modern_file_selector("💾 输出路径", "ext_output", is_folder=True))
        self.ext_input_edit.textChanged.connect(lambda: self.auto_fill_output('extract'))
        layout.addWidget(path_card)

        opt_card = CardFrame()
        oc_layout = QVBoxLayout(opt_card)
        oc_layout.setContentsMargins(10, 10, 10, 10)
        oc_layout.addWidget(QLabel("提取模式:"))
        
        self.ext_mode_bg = QButtonGroup(self)
        mode_grid = QHBoxLayout()
        
        modes = [
            ("纯净 JSON", "-p", "仅 name/message"),
            ("完整 JSON", "", "全数据"),
            ("翻译 TXT", "-T", "原文/译文"),
            ("全量 TXT", "-t", "所有字符串"),
            ("反汇编 ASM", "-d", "调试用")
        ]
        
        for i, (name, val, tip) in enumerate(modes):
            rb_container = QWidget()
            rb_layout = QVBoxLayout(rb_container)
            rb_layout.setContentsMargins(0,0,0,0)
            rb = QRadioButton(name)
            rb.setToolTip(tip)
            rb.setProperty("arg_val", val)
            if val == "-p": rb.setChecked(True)
            self.ext_mode_bg.addButton(rb, i)
            rb.toggled.connect(self.update_ruby_checkbox_state)
            rb_layout.addWidget(rb)
            # 移除提示标签以节省空间，只保留 ToolTip
            # tip_lbl = QLabel(tip)
            # ...
            mode_grid.addWidget(rb_container)
            
        oc_layout.addLayout(mode_grid)
        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine); line.setObjectName("Divider")
        oc_layout.addWidget(line)
        
        cb_layout = QHBoxLayout() # Checkboxes horizontal
        self.ext_verbose_cb = QCheckBox("显示详细日志 (-v)")
        cb_layout.addWidget(self.ext_verbose_cb)
        self.ext_remove_ruby_cb = QCheckBox("去除注音 (-r)")
        self.ext_remove_ruby_cb.setToolTip("支持 JSON 和 翻译 TXT 模式")
        cb_layout.addWidget(self.ext_remove_ruby_cb)
        cb_layout.addStretch()
        oc_layout.addLayout(cb_layout)
        
        layout.addWidget(opt_card)
        
        # 初始化时更新勾选框状态
        self.update_ruby_checkbox_state()

        self.btn_extract = ModernButton("开始提取", is_primary=True)
        self.btn_extract.clicked.connect(self.run_extractor)
        layout.addWidget(self.btn_extract)
        layout.addStretch()
        self.tabs.addTab(tab, "Ss文本提取")

    def update_ruby_checkbox_state(self):
        """根据当前选中的模式更新去除注音勾选框的启用状态"""
        selected_btn = self.ext_mode_bg.checkedButton()
        if selected_btn:
            arg_val = selected_btn.property("arg_val")
            # 反汇编(-d)和全量TXT(-t)模式不支持去除注音
            if arg_val in ("-d", "-t"):
                self.ext_remove_ruby_cb.setEnabled(False)
                self.ext_remove_ruby_cb.setChecked(False)
            else:
                self.ext_remove_ruby_cb.setEnabled(True)

    def setup_packer_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        path_card = CardFrame()
        pc_layout = QVBoxLayout(path_card)
        pc_layout.setContentsMargins(10, 10, 10, 10)
        
        pc_layout.addLayout(self.create_modern_file_selector("📂 原始 SS 目录", "pack_scene", is_folder=True))
        
        pc_layout.addWidget(QLabel("📄 封回数据文件/目录"))
        
        type_layout = QHBoxLayout()
        self.pack_type_bg = QButtonGroup(self)
        
        self.rb_pack_json = QRadioButton("JSON")
        self.rb_pack_json.setChecked(True)
        self.rb_pack_trans = QRadioButton("翻译 TXT")
        self.rb_pack_raw = QRadioButton("全量 TXT")
        self.rb_pack_asm = QRadioButton("ASM")

        self.pack_type_bg.addButton(self.rb_pack_json)
        self.pack_type_bg.addButton(self.rb_pack_trans)
        self.pack_type_bg.addButton(self.rb_pack_raw)
        self.pack_type_bg.addButton(self.rb_pack_asm)
        
        type_layout.addWidget(self.rb_pack_json)
        type_layout.addWidget(self.rb_pack_trans)
        type_layout.addWidget(self.rb_pack_raw)
        type_layout.addWidget(self.rb_pack_asm)
        type_layout.addStretch()
        pc_layout.addLayout(type_layout)
        
        data_row = QHBoxLayout()
        self.pack_data_edit = DragDropLineEdit()
        btn_browse = ModernButton("浏览")
        btn_browse.setFixedWidth(70)
        btn_browse.clicked.connect(self.browse_pack_data)
        
        data_row.addWidget(self.pack_data_edit)
        data_row.addWidget(btn_browse)
        pc_layout.addLayout(data_row)
        
        pc_layout.addLayout(self.create_modern_file_selector("💾 封回保存", "pack_output", is_folder=True))
        
        layout.addWidget(path_card)

        opt_card = CardFrame()
        oc_layout = QHBoxLayout(opt_card) # Horizontal options
        oc_layout.setContentsMargins(10, 10, 10, 10)
        self.pack_verbose_cb = QCheckBox("显示详细日志 (-v)")
        oc_layout.addWidget(self.pack_verbose_cb)
        oc_layout.addStretch()
        # Tip label removed or moved to tooltip to save space
        
        layout.addWidget(opt_card)

        self.btn_pack = ModernButton("开始封回", is_primary=True)
        self.btn_pack.clicked.connect(self.run_packer)
        layout.addWidget(self.btn_pack)
        layout.addStretch()

        self.tabs.addTab(tab, "Ss文本封回")

    def create_modern_file_selector(self, label_text, var_prefix, is_folder=True):
        layout = QHBoxLayout() # 改为水平布局
        layout.setContentsMargins(0, 0, 0, 0)
        
        lbl = QLabel(label_text)
        lbl.setMinimumWidth(120) # 设置最小宽度以对齐
        layout.addWidget(lbl)
        
        path_edit = DragDropLineEdit()
        setattr(self, f"{var_prefix}_edit", path_edit)
        
        btn = ModernButton("浏览")
        btn.setFixedWidth(70)
        btn.clicked.connect(lambda: self.browse_path(path_edit, is_folder))
        
        layout.addWidget(path_edit)
        layout.addWidget(btn)
        return layout

    def browse_path(self, line_edit, is_folder):
        if is_folder:
            path = QFileDialog.getExistingDirectory(self, "选择目录")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "SS Files (*.ss);;All Files (*.*)")
        if path:
            line_edit.setText(os.path.normpath(path))

    def browse_pack_data(self):
        if self.rb_pack_json.isChecked():
            filter_str = "JSON Files (*.json)"
        elif self.rb_pack_asm.isChecked():
            filter_str = "ASM Files (*.asm)"
        elif self.rb_pack_trans.isChecked() or self.rb_pack_raw.isChecked():
            filter_str = "Text Files (*.txt)"
        else:
            filter_str = "All Files (*.*)"

        path = QFileDialog.getExistingDirectory(self, "选择包含数据的文件夹 (自动扫描)")
        if path:
            self.pack_data_edit.setText(os.path.normpath(path))

    def toggle_ui(self, enabled):
        self.btn_extract.setEnabled(enabled)
        self.btn_pack.setEnabled(enabled)
        self.btn_decrypt_gameexe.setEnabled(enabled)
        self.btn_decrypt_scene.setEnabled(enabled)
        self.btn_repack_gameexe.setEnabled(enabled)
        self.btn_repack_scene.setEnabled(enabled)
        self.tabs.setEnabled(enabled)

    def run_extractor(self):
        input_path = self.ext_input_edit.text().strip()
        output_path = self.ext_output_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, "缺少参数", "请选择输入路径！")
            return
        args = [input_path]
        if output_path:
            args.append(output_path)
        else:
            args.append("")
        selected_btn = self.ext_mode_bg.checkedButton()
        if selected_btn:
            arg_val = selected_btn.property("arg_val")
            if arg_val: args.append(arg_val)
        if self.ext_verbose_cb.isChecked():
            args.append("-v")
        if self.ext_remove_ruby_cb.isChecked():
            args.append("-r")
        self.start_worker("extract", args)

    def run_packer(self):
        scene_path = self.pack_scene_edit.text().strip()
        data_path = self.pack_data_edit.text().strip()
        output_path = self.pack_output_edit.text().strip()
        if not scene_path or not data_path:
            QMessageBox.warning(self, "缺少参数", "必须指定 '原始 SS 目录' 和 '数据目录'！")
            return
        args = [scene_path, data_path]
        if output_path: args.append(output_path)
        if self.pack_verbose_cb.isChecked(): args.append("-v")
        self.start_worker("pack", args)

    def run_decrypt_gameexe(self):
        file_path = self.dec_gameexe_file_edit.text().strip()
        key_str = self.gameexe_key_selector.get_key_str()
        output_path = self.dec_gameexe_output_edit.text().strip()
        if not file_path:
            QMessageBox.warning(self, "缺少参数", "请选择 Gameexe.dat 文件！")
            return
        
        # 记录使用的密钥
        self.gameexe_key_selector.save_current_key_as_last()

        # 解析密钥
        private_key = None
        if key_str:
            private_key = siglus_decrypt_unpack.parse_key_string(key_str)
            if not private_key:
                QMessageBox.warning(self, "密钥格式错误", "密钥格式不正确！\n\n请输入32位十六进制字符串，例如：\n735CFC27018D67B6B568A070DC55B64B")
                return
        
        args = [file_path, private_key, output_path]
        self.start_worker("decrypt_gameexe", args)

    def run_decrypt_scene(self):
        file_path = self.dec_scene_file_edit.text().strip()
        key_str = self.scene_key_selector.get_key_str()
        output_path = self.dec_scene_output_edit.text().strip()
        if not file_path:
            QMessageBox.warning(self, "缺少参数", "请选择 Scene.pck 文件！")
            return
        
        # 记录使用的密钥
        self.scene_key_selector.save_current_key_as_last()

        # 解析密钥
        private_key = None
        if key_str:
            private_key = siglus_decrypt_unpack.parse_key_string(key_str)
            if not private_key:
                QMessageBox.warning(self, "密钥格式错误", "密钥格式不正确！\n\n请输入32位十六进制字符串，例如：\n735CFC27018D67B6B568A070DC55B64B")
                return
        
        args = [file_path, private_key, output_path]
        self.start_worker("decrypt_scene", args)

    def run_repack_gameexe(self):
        file_path = self.rep_gameexe_file_edit.text().strip()
        key_str = self.gameexe_key_selector.get_key_str()
        output_path = self.rep_gameexe_output_edit.text().strip()
        if not file_path:
            QMessageBox.warning(self, "缺少参数", "请选择已解密的文本文件！")
            return
        
        # 记录使用的密钥
        self.gameexe_key_selector.save_current_key_as_last()

        # 解析密钥
        private_key = None
        if key_str:
            private_key = siglus_decrypt_unpack.parse_key_string(key_str)
            if not private_key:
                QMessageBox.warning(self, "密钥格式错误", "密钥格式不正确！\n\n请输入32位十六进制字符串，例如：\n735CFC27018D67B6B568A070DC55B64B")
                return
        
        use_key = self.rep_gameexe_use_key_cb.isChecked()
        try:
            level = int(self.rep_gameexe_level_edit.text())
            if level < 0 or level > 17:
                QMessageBox.warning(self, "参数错误", "压缩级别必须在 0-17 之间！")
                return
        except ValueError:
            QMessageBox.warning(self, "参数错误", "压缩级别必须是数字！")
            return
        args = [file_path, private_key, output_path, '--no-key' if not use_key else '', str(level)]
        self.start_worker("pack_gameexe", args)

    def run_repack_scene(self):
        dir_path = self.rep_scene_dir_edit.text().strip()
        pck_path = self.rep_scene_pck_edit.text().strip()
        key_str = self.scene_key_selector.get_key_str()
        output_path = self.rep_scene_output_edit.text().strip()
        if not dir_path or not pck_path:
            QMessageBox.warning(self, "缺少参数", "必须指定 '.ss 文件目录' 和 '原始 Scene.pck 文件'！")
            return
        
        # 记录使用的密钥
        self.scene_key_selector.save_current_key_as_last()

        # 解析密钥
        private_key = None
        if key_str:
            private_key = siglus_decrypt_unpack.parse_key_string(key_str)
            if not private_key:
                QMessageBox.warning(self, "密钥格式错误", "密钥格式不正确！\n\n请输入32位十六进制字符串，例如：\n735CFC27018D67B6B568A070DC55B64B")
                return
        
        use_key = self.rep_scene_use_key_cb.isChecked()
        try:
            level = int(self.rep_scene_level_edit.text())
            if level < 0 or level > 17:
                QMessageBox.warning(self, "参数错误", "压缩级别必须在 0-17 之间！")
                return
        except ValueError:
            QMessageBox.warning(self, "参数错误", "压缩级别必须是数字！")
            return
        args = [dir_path, pck_path, private_key, output_path, '--no-key' if not use_key else '', str(level)]
        self.start_worker("pack_scene", args)

    def start_worker(self, task_type, args):
        self.toggle_ui(False)
        self.log_view.clear()
        self.worker = WorkerThread(task_type, args)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.on_task_finished)
        self.worker.start()

    def replace_last_log_line(self, text):
        try:
            cursor = self.log_view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveOperation.KeepAnchor)
            cursor.removeSelectedText()
            cursor.insertText(text)
            self.log_view.setTextCursor(cursor)
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        except:
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)
            self.log_view.insertPlainText(text)
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def append_log(self, text):
        try:
            if "打包场景中" in text and '\n' not in text and '\r' not in text:
                idx = text.rfind("打包场景中")
                self.replace_last_log_line(text[idx:])
                self._last_log_blank = False
                return
            if '\r' in text:
                parts = text.split('\r')
                if parts[0]:
                    self.log_view.moveCursor(QTextCursor.MoveOperation.End)
                    self.log_view.insertPlainText(parts[0])
                    self.log_view.moveCursor(QTextCursor.MoveOperation.End)
                for part in parts[1:]:
                    self.replace_last_log_line(part)
                return

            if text.strip() == "":
                if self._last_log_blank:
                    return
                self._last_log_blank = True
            else:
                self._last_log_blank = False
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)
            self.log_view.insertPlainText(text)
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        except:
            pass

    def on_task_finished(self, success, msg):
        self.toggle_ui(True)
        if not success:
            QMessageBox.warning(self, "任务结束", msg)
        else:
            QMessageBox.information(self, "任务成功", msg)
        self.append_log(f"\n[{'成功' if success else '失败'}] {msg}\n")

    # --------------------------------------------------------
    # 主题样式 (修复配色)
    # --------------------------------------------------------
    def apply_theme(self, theme_name):
        # 明亮模式：日志改为白底黑字，更和谐
        light_qss = """
        QWidget { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: 10pt; color: #333333; }
        QWidget#MainBackground { background-color: #f5f7fa; }
        QFrame#CardFrame { background-color: #ffffff; border: 1px solid #e1e4e8; border-radius: 8px; }
        QLabel#AppTitle { font-size: 18pt; font-weight: bold; color: #2c3e50; }
        QLabel#SubLabel { color: #555555; }
        QLabel#TipLabel { font-size: 9pt; color: #888888; font-style: italic; }
        QLineEdit { padding: 8px; border: 1px solid #ced4da; border-radius: 4px; background: #ffffff; color: #333; }
        QLineEdit:focus { border: 1px solid #3498db; }
        QPushButton#SecondaryButton { background-color: #ffffff; border: 1px solid #dcdfe6; border-radius: 4px; color: #606266; padding: 6px 12px; }
        QPushButton#SecondaryButton:hover { border-color: #c6e2ff; color: #409eff; background-color: #ecf5ff; }
        QPushButton#PrimaryButton { background-color: #3498db; border: 1px solid #3498db; border-radius: 4px; color: #ffffff; font-weight: bold; padding: 8px 16px; }
        QPushButton#PrimaryButton:hover { background-color: #5dade2; border-color: #5dade2; }
        QTabWidget::pane { border: 1px solid #e1e4e8; background: #fff; border-radius: 5px; }
        QTabBar::tab { background: #e8ebf0; color: #666; padding: 10px 20px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }
        QTabBar::tab:selected { background: #ffffff; color: #3498db; font-weight: bold; }
        
        /* 修复日志配色：白底深灰字 */
        QTextEdit#LogConsole { background-color: #fcfcfc; color: #333333; border: 1px solid #e1e4e8; font-family: 'Consolas', monospace; font-size: 9pt; }
        QWidget#LogHeader { background-color: #f1f1f1; border-bottom: 1px solid #ddd; }
        QFrame#Divider { color: #eeeeee; }
        
        /* 下拉框基本设置 */
        QComboBox { padding: 4px; color: #333; background: #fff; border: 1px solid #ced4da; border-radius: 4px; }
        QComboBox::drop-down { border: none; }
        /* 确保下拉列表可见 */
        QComboBox QAbstractItemView { background-color: #ffffff; color: #333333; selection-background-color: #e6f7ff; selection-color: #333333; }
        """

        # 暗黑模式：修复下拉框看不清问题，日志配色微调
        dark_qss = """
        QWidget { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: 10pt; color: #e0e0e0; }
        QWidget#MainBackground { background-color: #1e1e1e; }
        QFrame#CardFrame { background-color: #2d2d2d; border: 1px solid #444; border-radius: 8px; }
        QLabel { color: #e0e0e0; }
        QLabel#AppTitle { color: #ffffff; font-size: 18pt; font-weight: bold; }
        QLabel#SubLabel { color: #aaaaaa; }
        QLabel#TipLabel { color: #999999; font-size: 9pt; }
        QLineEdit { background: #1a1a1a; border: 1px solid #555; border-radius: 4px; color: #ffffff; padding: 8px; }
        QLineEdit:focus { border: 1px solid #bb86fc; }
        QPushButton#SecondaryButton { background: #333; border: 1px solid #555; color: #ddd; border-radius: 4px; }
        QPushButton#SecondaryButton:hover { background: #444; border-color: #777; }
        QPushButton#PrimaryButton { background: #bb86fc; border: 1px solid #bb86fc; color: #121212; border-radius: 4px; font-weight:bold; }
        QPushButton#PrimaryButton:hover { background: #d0aaff; }
        QTabWidget::pane { border: 1px solid #444; background: #2d2d2d; }
        QTabBar::tab { background: #1e1e1e; color: #999; padding: 10px 20px; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right:2px;}
        QTabBar::tab:selected { background: #2d2d2d; color: #bb86fc; font-weight:bold; }
        
        /* 日志：深灰底白字，不再是刺眼的绿字 */
        QTextEdit#LogConsole { background-color: #1a1a1a; color: #e0e0e0; border: 1px solid #444; font-family: 'Consolas', monospace; font-size: 9pt; }
        QWidget#LogHeader { background-color: #252525; border-bottom: 1px solid #444; }
        QFrame#Divider { color: #444; }

        /* 下拉框修复 */
        QComboBox { padding: 4px; color: #e0e0e0; background: #333; border: 1px solid #555; border-radius: 4px; }
        QComboBox::drop-down { border: none; }
        QComboBox QAbstractItemView { background-color: #2d2d2d; color: #e0e0e0; selection-background-color: #bb86fc; selection-color: #121212; }
        """

        # 赛博朋克：保持原样，微调日志
        cyber_qss = """
        QWidget { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: 10pt; color: #00ffcc; }
        QWidget#MainBackground { background-color: #0d0d15; }
        QFrame#CardFrame { background-color: #1a1a2e; border: 1px solid #00ffcc; border-radius: 8px; }
        QLabel { color: #00ffcc; }
        QLabel#AppTitle { color: #ff00ff; font-size: 18pt; font-weight: bold; text-shadow: 0 0 5px #ff00ff; }
        QLabel#SubLabel { color: #00ccff; }
        QLabel#TipLabel { color: #0088aa; font-size: 9pt; }
        QLineEdit { background: #0f0f1a; border: 1px solid #ff00ff; border-radius: 4px; color: #00ffcc; padding: 8px; }
        QLineEdit:focus { border: 1px solid #00ffcc; box-shadow: 0 0 5px #00ffcc; }
        QPushButton#SecondaryButton { background: #0f0f1a; border: 1px solid #00ffcc; color: #00ffcc; border-radius: 4px; }
        QPushButton#SecondaryButton:hover { background: #1a1a2e; box-shadow: 0 0 5px #00ffcc; }
        QPushButton#PrimaryButton { background: #ff00ff; border: 1px solid #ff00ff; color: #000; border-radius: 4px; font-weight:bold; }
        QPushButton#PrimaryButton:hover { background: #d000d0; box-shadow: 0 0 10px #ff00ff; }
        QTabWidget::pane { border: 1px solid #00ffcc; background: #1a1a2e; }
        QTabBar::tab { background: #0d0d15; color: #0088aa; padding: 10px 20px; border: 1px solid #004455; margin-right:2px;}
        QTabBar::tab:selected { background: #1a1a2e; color: #ff00ff; border: 1px solid #ff00ff; font-weight:bold; }
        
        QTextEdit#LogConsole { background-color: #050505; color: #00ff00; border: 1px solid #00ffcc; font-family: 'Consolas', monospace; font-size: 9pt; }
        QWidget#LogHeader { background-color: #111; border-bottom: 1px solid #00ffcc; }
        QFrame#Divider { color: #00ffcc; }

        QComboBox { padding: 4px; color: #00ffcc; background: #0f0f1a; border: 1px solid #ff00ff; border-radius: 4px; }
        QComboBox::drop-down { border: none; }
        QComboBox QAbstractItemView { background-color: #0d0d15; color: #00ffcc; selection-background-color: #ff00ff; selection-color: #000; }
        """

        if "暗黑" in theme_name:
            self.setStyleSheet(dark_qss)
        elif "赛博" in theme_name:
            self.setStyleSheet(cyber_qss)
        else:
            self.setStyleSheet(light_qss)

if __name__ == '__main__':
    def gui_excepthook(exctype, value, tb):
        error_text = "".join(traceback.format_exception(exctype, value, tb))
        try:
            with open("gui_crash.log", "a", encoding="utf-8") as f:
                f.write(error_text + "\n")
        except:
            pass
        try:
            QMessageBox.critical(None, "Unhandled Python exception", error_text[-2000:])
        except:
            pass
        sys.__excepthook__(exctype, value, tb)

    def thread_excepthook(args):
        gui_excepthook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = gui_excepthook
    threading.excepthook = thread_excepthook
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
