"""
Light.VN Mcdat 工具箱 - 现代化 UI 重制版 (v2.4 修复版)
支持解包、重打包和智能增量补丁制作
更新日志 v2.4:
- 修正补丁输出路径：所有 mcdat 文件直接扁平化输出到 Patch 根目录。
- 同步修正 JSON 映射路径，确保 Patch/0.mcdat 中的路径指向正确。
- 修复键名大小写问题：补丁映射表键统一为小写，匹配引擎查找逻辑。
"""

import sys
import os
import json
import shutil
import argparse
import filecmp
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Callable

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QTextEdit, QProgressBar,
    QGroupBox, QMessageBox, QFrame, QLineEdit, QComboBox, 
    QGridLayout, QSizePolicy, QScrollArea, QCheckBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QDragEnterEvent, QDropEvent, QTextCursor, QColor, QPalette, QIcon

# ==================== 主题配色方案 ====================
THEMES = {
    "默认蓝 (Light)": {
        "bg": "#F8F9FA",
        "surface": "#FFFFFF",
        "text_primary": "#212529",
        "text_secondary": "#6C757D",
        "border": "#DEE2E6",
        "accent": "#0D6EFD",
        "accent_hover": "#0B5ED7",
        "success": "#198754",
        "warning": "#FFC107",
        "error": "#DC3545",
        "console_bg": "#212529",
        "console_text": "#F8F9FA",
        "drop_zone_bg": "#E9ECEF"
    },
    "暗黑模式 (Dark)": {
        "bg": "#121212",
        "surface": "#1E1E1E",
        "text_primary": "#E0E0E0",
        "text_secondary": "#A0A0A0",
        "border": "#333333",
        "accent": "#BB86FC",
        "accent_hover": "#9965f4",
        "success": "#03DAC6",
        "warning": "#CF6679",
        "error": "#CF6679",
        "console_bg": "#000000",
        "console_text": "#00FF00",
        "drop_zone_bg": "#2C2C2C"
    },
}


# ==================== 核心处理类 ====================
class McdatProcessor:
    """处理Light.VN引擎的.mcdat文件 - XOR加解密与补丁制作"""
    
    # 加密密钥: d6c5fKI3GgBWpZF3Tz6ia3kF0
    KEY = bytes([0x64, 0x36, 0x63, 0x35, 0x66, 0x4B, 0x49, 0x33,
                 0x47, 0x67, 0x42, 0x57, 0x70, 0x5A, 0x46, 0x33,
                 0x54, 0x7A, 0x36, 0x69, 0x61, 0x33, 0x6B, 0x46, 0x30])
    
    def __init__(self):
        self.file_name_list: Dict[str, str] = {}
        
    def unpack(self, in_dir: str, create_backup: bool = True, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> bool:
        try:
            if_recover_name = True
            out_dir = os.path.join(in_dir, "output")
            os.makedirs(out_dir, exist_ok=True)
            
            if not os.path.exists(in_dir):
                raise FileNotFoundError(f"未找到目录: {in_dir}")
            
            # 处理文件名映射表
            name_list_path = os.path.join(in_dir, "0.mcdat")
            if os.path.exists(name_list_path):
                with open(name_list_path, 'rb') as f:
                    enc_name_list = f.read()
                dec_name_list = self._xor_zero_mcdat(enc_name_list)
                with open(os.path.join(out_dir, "0.mcdat.json"), 'wb') as f:
                    f.write(dec_name_list)
                self.file_name_list = self._parse_json(dec_name_list)
            else:
                if_recover_name = False
                if progress_callback:
                    progress_callback(0, 0, "警告: 未找到0.mcdat，文件名将不会恢复")
            
            mcdat_files = list(Path(in_dir).glob("*.mcdat"))
            total = len(mcdat_files)
            
            for count, mc_path in enumerate(mcdat_files, 1):
                name = mc_path.name
                # 如果有映射表，恢复原始目录结构
                if if_recover_name and name in self.file_name_list:
                    relative_path = self.file_name_list[name]
                    out_path = os.path.join(out_dir, relative_path)
                else:
                    out_path = os.path.join(out_dir, name)
                
                if name != "0.mcdat":
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with open(mc_path, 'rb') as f:
                        enc_data = f.read()
                    dec_data = self._xor_mcdat(enc_data)
                    with open(out_path, 'wb') as f:
                        f.write(dec_data)
                
                if progress_callback:
                    progress_callback(count, total, f"解包: {name}")

            # === 备份逻辑 ===
            if create_backup:
                if progress_callback:
                    progress_callback(total, total, "正在创建备份 (output_bak)...")
                bak_dir = os.path.join(in_dir, "output_bak")
                if os.path.exists(bak_dir):
                    shutil.rmtree(bak_dir)
                shutil.copytree(out_dir, bak_dir)
                if progress_callback:
                    progress_callback(total, total, "备份创建完成")

            return True
        except Exception as e:
            if progress_callback:
                progress_callback(0, 0, f"错误: {str(e)}")
            return False
    
    def repack(self, in_dir: str, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> bool:
        try:
            process_name = True
            parent_dir = str(Path(in_dir).parent)
            out_dir = os.path.join(parent_dir, "Newmcdat")
            os.makedirs(out_dir, exist_ok=True)
            
            # 读取映射表
            name_list_path = os.path.join(in_dir, "0.mcdat.json")
            if os.path.exists(name_list_path):
                with open(name_list_path, 'rb') as f:
                    json_data = f.read()
                file_map = json.loads(json_data.decode('utf-8'))
                # 建立反向索引：相对路径 -> 加密文件名
                self.file_name_list = {k.lower(): os.path.basename(v) for k, v in file_map.items()}
                
                enc_data = self._xor_zero_mcdat(json_data)
                with open(os.path.join(out_dir, "0.mcdat"), 'wb') as f:
                    f.write(enc_data)
            else:
                process_name = False
            
            all_files = []
            for root, dirs, files in os.walk(in_dir):
                for file in files:
                    if file.lower() != "0.mcdat.json":
                        all_files.append(os.path.join(root, file))
            
            total = len(all_files)
            for count, filepath in enumerate(all_files, 1):
                # 获取相对路径并转为小写 (用于匹配 key)
                relative_path = os.path.relpath(filepath, in_dir).replace('\\', '/').lower()
                
                if process_name and relative_path in self.file_name_list:
                    file_name = self.file_name_list[relative_path]
                else:
                    file_name = os.path.basename(filepath)
                
                with open(filepath, 'rb') as f:
                    data = f.read()
                
                if file_name == "0.mcdat":
                    enc_data = self._xor_zero_mcdat(data)
                else:
                    enc_data = self._xor_mcdat(data)
                
                with open(os.path.join(out_dir, file_name), 'wb') as f:
                    f.write(enc_data)
                if progress_callback:
                    progress_callback(count, total, f"重打包: {file_name}")
            return True
        except Exception as e:
            if progress_callback:
                progress_callback(0, 0, f"错误: {str(e)}")
            return False
    
    def make_patch(self, mod_dir: str, orig_dir: Optional[str] = None, 
                   auto_update_json: bool = True,
                   progress_callback: Optional[Callable[[int, int, str], None]] = None) -> bool:
        """
        制作补丁 (v2.4 修复版)：
        1. 预处理：扫描 mod_dir 中的新文件，直接更新 mod_dir/0.mcdat.json 中的映射。
        2. 打包：根据最终映射，将差异文件打包到 Patch 根目录（扁平化）。
        3. 修正：生成 Patch/0.mcdat 时，所有路径指向 Patch/ 目录，键统一为小写。
        """
        try:
            parent_dir = str(Path(mod_dir).parent)
            out_dir = os.path.join(parent_dir, "Patch")
            os.makedirs(out_dir, exist_ok=True)
            
            json_path = os.path.join(mod_dir, "0.mcdat.json")
            if not os.path.exists(json_path):
                if progress_callback:
                    progress_callback(0, 0, "错误: 无法读取 0.mcdat.json")
                return False
            
            with open(json_path, 'rb') as f:
                json_data_bytes = f.read()
            full_file_map = json.loads(json_data_bytes.decode('utf-8'))
            
            # === 1. 预处理 - 更新源目录的 JSON ===
            if auto_update_json:
                if progress_callback:
                    progress_callback(0, 0, "正在检查新增文件并更新 0.mcdat.json...")
                
                # 分析现有结构 (前缀和最大ID)
                mcdat_path_prefix = "Data/_/" 
                max_id = 0
                pattern = re.compile(r"(.+?)/(\d+)\.mcdat$", re.IGNORECASE)
                
                for v in full_file_map.values():
                    match = pattern.match(v)
                    if match:
                        mcdat_path_prefix = match.group(1) + "/" 
                        current_id = int(match.group(2))
                        if current_id > max_id:
                            max_id = current_id
                
                existing_keys_lower = {k.lower() for k in full_file_map.keys()}
                new_files_detected = False
                
                # 扫描新文件
                for root, dirs, files in os.walk(mod_dir):
                    for file in files:
                        if file.lower() == "0.mcdat.json" or file.lower().endswith('.mcdat'):
                            continue
                            
                        filepath = os.path.join(root, file)
                        rel_path = os.path.relpath(filepath, mod_dir).replace('\\', '/')
                        
                        if rel_path.lower() not in existing_keys_lower:
                            new_files_detected = True
                            max_id += 1
                            # 逻辑路径：Data/_/xxx.mcdat (游戏引擎读取用)
                            new_mcdat_path = f"{mcdat_path_prefix}{max_id}.mcdat"
                            
                            full_file_map[rel_path] = new_mcdat_path
                            existing_keys_lower.add(rel_path.lower())
                            
                            if progress_callback:
                                progress_callback(0, 0, f"注册新文件: {rel_path} -> {new_mcdat_path}")

                # 保存更改回源文件
                if new_files_detected:
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(full_file_map, f, indent=2, ensure_ascii=False)
                    if progress_callback:
                        progress_callback(0, 0, "0.mcdat.json 已更新。")
                else:
                    if progress_callback:
                        progress_callback(0, 0, "未发现需注册的新文件。")

            # === 2. 开始常规补丁流程 ===
            self.file_name_list = {k.lower(): os.path.basename(v) for k, v in full_file_map.items()}

            target_files = []
            for root, dirs, files in os.walk(mod_dir):
                for file in files:
                    if file.lower() != "0.mcdat.json":
                        target_files.append(os.path.join(root, file))
            
            files_to_pack = []
            
            # 比对
            if orig_dir and os.path.exists(orig_dir):
                if progress_callback:
                    progress_callback(0, 0, "正在对比文件差异...")
                
                for filepath in target_files:
                    rel_path = os.path.relpath(filepath, mod_dir)
                    orig_filepath = os.path.join(orig_dir, rel_path)
                    
                    if not os.path.exists(orig_filepath):
                        files_to_pack.append(filepath)
                    else:
                        if not filecmp.cmp(filepath, orig_filepath, shallow=False):
                            files_to_pack.append(filepath)
            else:
                files_to_pack = target_files

            if not files_to_pack:
                if progress_callback:
                    progress_callback(0, 0, "未发现差异文件。")
                return True

            # === 3. 打包并构建补丁映射表 ===
            patch_map = {}  # v2.4 修复：专门存储补丁路径映射
            total = len(files_to_pack)
            
            for count, filepath in enumerate(files_to_pack, 1):
                rel_path_raw = os.path.relpath(filepath, mod_dir).replace('\\', '/')
                rel_path_lower = rel_path_raw.lower()  # 预计算小写版本
                
                if rel_path_lower in self.file_name_list:
                    # 获取加密文件名 (如 "841.mcdat")
                    mcdat_filename = self.file_name_list[rel_path_lower]
                    
                    # 计算输出路径（扁平化）
                    out_file_path = os.path.join(out_dir, mcdat_filename)
                    
                    # 构建补丁映射：使用小写键确保引擎匹配
                    patch_map[rel_path_lower] = f"Patch/{mcdat_filename}"
                    
                    # 读取并加密
                    with open(filepath, 'rb') as f:
                        data = f.read()
                    enc_data = self._xor_mcdat(data)
                    
                    with open(out_file_path, 'wb') as f:
                        f.write(enc_data)
                else:
                    if progress_callback:
                        progress_callback(count, total, f"警告: 文件未注册，跳过: {rel_path_raw}")
                        continue

                if progress_callback:
                    progress_callback(count, total, f"补丁打包: {rel_path_raw}")
            
            # === 4. 生成 Patch/0.mcdat ===
            # v2.4 修复：使用 patch_map 而非 full_file_map，确保路径指向 Patch/
            patch_json = json.dumps(patch_map, indent=2, ensure_ascii=False).encode('utf-8')
            enc_patch_data = self._xor_zero_mcdat(patch_json)
            with open(os.path.join(out_dir, "0.mcdat"), 'wb') as f:
                f.write(enc_patch_data)
            
            if progress_callback:
                progress_callback(total, total, f"完成: 打包 {total} 个文件 (位于 Patch 根目录)")
            return True
            
        except Exception as e:
            if progress_callback:
                progress_callback(0, 0, f"错误: {str(e)}")
            import traceback
            print(traceback.format_exc())
            return False
    
    def _xor_zero_mcdat(self, enc_data: bytes) -> bytes:
        buffer = bytearray(enc_data) + bytearray(1)
        idx_j = len(enc_data)
        idx_i = 0
        for i in range(len(enc_data)):
            stream = self.KEY[i % len(self.KEY)]
            buffer[idx_i] ^= stream
            buffer[idx_j] ^= stream
            idx_j -= 1
            idx_i += 1
        return bytes(buffer[:len(enc_data)])
    
    def _xor_mcdat(self, buffer: bytes) -> bytes:
        buffer = bytearray(buffer)
        reversed_key = self.KEY[::-1]
        if len(buffer) < 100:
            if len(buffer) > 0:
                return self._xor_zero_mcdat(bytes(buffer))
            return bytes(buffer)
        else:
            for i in range(100):
                buffer[i] ^= self.KEY[i % len(self.KEY)]
            index = len(buffer) - 99
            for i in range(99):
                buffer[i + index] ^= reversed_key[i % len(reversed_key)]
        return bytes(buffer)
    
    def _parse_json(self, data: bytes) -> Dict[str, str]:
        file_map = json.loads(data.decode('utf-8'))
        result = {}
        for key, value in file_map.items():
            enc_file_name = os.path.basename(value)
            result[enc_file_name] = key
        return result


class WorkerThread(QThread):
    progress_updated = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, operation: str, directory: str, 
                 original_dir: Optional[str] = None,
                 create_backup: bool = True,
                 auto_update_json: bool = True):
        super().__init__()
        self.operation = operation
        self.directory = directory
        self.original_dir = original_dir
        self.create_backup = create_backup
        self.auto_update_json = auto_update_json
        self.processor = McdatProcessor()
        
    def run(self):
        try:
            success = False
            if self.operation == 'unpack':
                success = self.processor.unpack(
                    self.directory, 
                    self.create_backup,
                    self._progress_callback
                )
                msg_backup = " (已创建备份)" if self.create_backup else ""
                message = f"解包成功！文件已保存至 output 目录{msg_backup}" if success else "解包失败"
                
            elif self.operation == 'repack':
                success = self.processor.repack(self.directory, self._progress_callback)
                message = "重打包成功！文件已保存至 Newmcdat 目录" if success else "重打包失败"
                
            elif self.operation == 'patch':
                success = self.processor.make_patch(
                    self.directory, 
                    self.original_dir, 
                    self.auto_update_json,
                    self._progress_callback
                )
                message = "补丁制作成功！文件已保存至 Patch 目录" if success else "补丁制作失败"
            else:
                message = "未知操作"
            self.finished.emit(success, message)
        except Exception as e:
            self.finished.emit(False, f"系统错误: {str(e)}")
    
    def _progress_callback(self, current: int, total: int, message: str):
        self.progress_updated.emit(current, total, message)


# ==================== GUI 组件 ====================

class DropZone(QFrame):
    """现代化拖拽区域组件"""
    path_dropped = pyqtSignal(str)
    
    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        self.setObjectName("DropZone")
        
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.icon_label = QLabel("📂")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 48px; background: transparent;")
        
        self.text_label = QLabel("拖拽游戏目录或文件到此处")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {theme['text_secondary']}; background: transparent;")
        
        self.path_label = QLabel("或点击选择目录")
        self.path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path_label.setStyleSheet(f"font-size: 12px; color: {theme['text_secondary']}; margin-top: 5px; background: transparent;")
        
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        layout.addWidget(self.path_label)
        
        self.update_style()

    def update_style(self, active=False):
        border_color = self.theme['accent'] if active else self.theme['border']
        bg_color = self.theme['drop_zone_bg']
        if active:
            bg_color = self.theme['surface']
            
        self.setStyleSheet(f"""
            QFrame#DropZone {{
                background-color: {bg_color};
                border: 2px dashed {border_color};
                border-radius: 15px;
            }}
            QFrame#DropZone:hover {{
                border-color: {self.theme['accent_hover']};
                background-color: {self.theme['surface']};
            }}
        """)
        
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.update_style(active=True)
            self.text_label.setText("释放以加载")
        else:
            event.ignore()
            
    def dragLeaveEvent(self, event):
        self.update_style(active=False)
        self.text_label.setText("拖拽游戏目录或文件到此处")
        
    def dropEvent(self, event: QDropEvent):
        self.update_style(active=False)
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isfile(path):
                path = os.path.dirname(path)
            self.path_dropped.emit(path)
            
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            directory = QFileDialog.getExistingDirectory(self, "选择目录")
            if directory:
                self.path_dropped.emit(directory)
    
    def set_current_path(self, path):
        self.path_label.setText(path if path else "或点击选择目录")
        if path:
            self.icon_label.setText("📁")
            self.text_label.setText("已加载目录")
            self.text_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {self.theme['accent']}; background: transparent;")
        else:
            self.icon_label.setText("📂")
            self.text_label.setText("拖拽游戏目录或文件到此处")
            self.text_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {self.theme['text_secondary']}; background: transparent;")


class ActionCard(QFrame):
    """功能卡片组件"""
    clicked = pyqtSignal()
    
    def __init__(self, title, desc, icon, theme):
        super().__init__()
        self.theme = theme
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("ActionCard")
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 顶部布局（图标 + 标题）
        top_layout = QHBoxLayout()
        
        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 24px; background: transparent;")
        top_layout.addWidget(icon_label)
        
        title_label = QLabel(title)
        title_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {theme['text_primary']}; background: transparent;")
        top_layout.addWidget(title_label)
        top_layout.addStretch()
        
        layout.addLayout(top_layout)
        
        # 描述文本
        desc_label = QLabel(desc)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet(f"font-size: 12px; color: {theme['text_secondary']}; margin-top: 10px; background: transparent;")
        layout.addWidget(desc_label)
        
        # 状态指示器
        self.status_bar = QFrame()
        self.status_bar.setFixedHeight(4)
        self.status_bar.setStyleSheet("background-color: transparent; border-radius: 2px;")
        layout.addWidget(self.status_bar)
        
        self._enabled = False
        self.update_style()
        
    def set_enabled(self, enabled):
        self._enabled = enabled
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ForbiddenCursor)
        self.update_style()
        
    def update_style(self):
        border = self.theme['accent'] if self._enabled else self.theme['border']
        bg = self.theme['surface']
        
        self.setStyleSheet(f"""
            QFrame#ActionCard {{
                background-color: {bg};
                border: 1px solid {self.theme['border']};
                border-radius: 12px;
            }}
            QFrame#ActionCard:hover {{
                border: 1px solid {border};
                background-color: {bg};
            }}
        """)
        if not self._enabled:
             self.setStyleSheet(f"""
                QFrame#ActionCard {{
                    background-color: {self.theme['bg']};
                    border: 1px solid {self.theme['border']};
                    border-radius: 12px;
                    color: {self.theme['text_secondary']};
                }}
            """)

    def mousePressEvent(self, event):
        if self._enabled and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            self.setStyleSheet(f"""
                QFrame#ActionCard {{
                    background-color: {self.theme['border']};
                    border: 2px solid {self.theme['accent']};
                    border-radius: 12px;
                }}
            """)
            
    def mouseReleaseEvent(self, event):
        if self._enabled:
            self.update_style()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_theme_name = "默认蓝 (Light)"
        self.current_theme = THEMES[self.current_theme_name]
        self.input_directory = ""
        self.worker = None
        
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("Light.VN Mcdat 工具箱 - Dashboard v2.4")
        self.resize(1000, 750)
        self.setMinimumSize(850, 600)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)
        
        self.create_header()
        
        self.drop_zone = DropZone(self.current_theme)
        self.drop_zone.path_dropped.connect(self.on_path_loaded)
        self.main_layout.addWidget(self.drop_zone)
        
        self.create_action_cards()
        self.create_options_area() 
        self.create_log_area()
        
        self.apply_theme()
        self.log("系统就绪。请拖拽目录或点击上方区域开始。")

    def create_header(self):
        header_layout = QHBoxLayout()
        
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Light.VN 工具箱")
        title.setFont(QFont("Microsoft YaHei UI", 16, QFont.Weight.Bold))
        subtitle = QLabel("MCDAT 文件解包、打包与补丁制作")
        subtitle.setObjectName("Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box)
        
        header_layout.addStretch()
        
        theme_label = QLabel("主题:")
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES.keys()))
        self.theme_combo.currentTextChanged.connect(self.change_theme)
        
        header_layout.addWidget(theme_label)
        header_layout.addWidget(self.theme_combo)
        
        self.main_layout.addLayout(header_layout)
        
    def create_action_cards(self):
        grid = QHBoxLayout()
        grid.setSpacing(20)
        
        self.card_unpack = ActionCard(
            "解包 (Unpack)", 
            "将 .mcdat 文件解密并导出。\n如果有 0.mcdat，将自动恢复文件名。\n输出到output文件夹，可自动备份。",
            "📦", self.current_theme
        )
        self.card_unpack.clicked.connect(lambda: self.start_operation('unpack'))
        
        self.card_repack = ActionCard(
            "重打包 (Repack)", 
            "将 output 目录下的文件加密回包。\n需要 0.mcdat.json 。\n输出到Newmcdat文件夹。",
            "🔄", self.current_theme
        )
        self.card_repack.clicked.connect(lambda: self.start_operation('repack'))
        
        self.card_patch = ActionCard(
            "制作补丁 (Patch)", 
            "对比文件差异，生成增量补丁包。\n仅提取差异文件。输出到Patch文件夹。\n把Patch文件夹移到游戏目录下既可进行补丁。",
            "🔧", self.current_theme
        )
        self.card_patch.clicked.connect(lambda: self.start_operation('patch'))
        
        grid.addWidget(self.card_unpack)
        grid.addWidget(self.card_repack)
        grid.addWidget(self.card_patch)
        
        self.main_layout.addLayout(grid)

    def create_options_area(self):
        options_layout = QHBoxLayout()
        options_layout.setContentsMargins(10, 0, 10, 0)
        
        self.chk_backup = QCheckBox("解包后自动备份 (Output -> Output_bak)")
        self.chk_backup.setChecked(True)
        self.chk_backup.setToolTip("开启后，解包完成会自动复制一份 output 文件夹并命名为 output_bak，\n方便后续制作补丁时作为【原版目录】使用。")
        
        self.chk_auto_json = QCheckBox("补丁模式：自动注册新文件 (直接修改源0.mcdat.json)")
        self.chk_auto_json.setChecked(True)
        self.chk_auto_json.setToolTip("开启后，如果检测到 output 文件夹中有新文件，\n程序会先将新文件信息写入 output/0.mcdat.json 中（分配新 ID），\n然后再进行打包。")
        
        options_layout.addWidget(self.chk_backup)
        options_layout.addSpacing(20)
        options_layout.addWidget(self.chk_auto_json)
        options_layout.addStretch()
        
        self.main_layout.addLayout(options_layout)
        
    def create_log_area(self):
        log_frame = QFrame()
        log_frame.setObjectName("LogFrame")
        layout = QVBoxLayout(log_frame)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(0)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self.progress_bar)
        
        log_header = QFrame()
        log_header.setObjectName("LogHeader")
        lh_layout = QHBoxLayout(log_header)
        lh_layout.setContentsMargins(10, 5, 10, 5)
        lh_label = QLabel("终端输出 (Console)")
        lh_label.setStyleSheet("font-weight: bold; font-size: 11px;")
        
        clear_btn = QPushButton("清除")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(lambda: self.log_text.clear())
        clear_btn.setFixedSize(50, 20)
        clear_btn.setStyleSheet("border: none; font-size: 10px; text-align: right;")
        
        lh_layout.addWidget(lh_label)
        lh_layout.addStretch()
        lh_layout.addWidget(clear_btn)
        layout.addWidget(log_header)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log_text)
        
        self.main_layout.addWidget(log_frame)

    def apply_theme(self):
        t = self.current_theme
        
        self.setStyleSheet(f"QMainWindow {{ background-color: {t['bg']}; }}")
        
        for label in self.findChildren(QLabel):
            if label.objectName() == "Subtitle":
                 label.setStyleSheet(f"color: {t['text_secondary']}; font-size: 12px;")
            elif "font-size" not in label.styleSheet():
                label.setStyleSheet(f"color: {t['text_primary']};")
        
        self.theme_combo.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {t['border']};
                border-radius: 5px;
                padding: 5px;
                background: {t['surface']};
                color: {t['text_primary']};
            }}
            QComboBox::drop-down {{ border: none; }}
        """)
        
        self.chk_backup.setStyleSheet(f"color: {t['text_primary']};")
        self.chk_auto_json.setStyleSheet(f"color: {t['text_primary']};")
        
        self.log_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: {t['console_bg']};
                color: {t['console_text']};
                border: none;
                padding: 10px;
            }}
        """)
        
        self.findChild(QFrame, "LogFrame").setStyleSheet(f"""
            QFrame#LogFrame {{
                border: 1px solid {t['border']};
                border-radius: 8px;
            }}
        """)
        
        self.findChild(QFrame, "LogHeader").setStyleSheet(f"""
            QFrame#LogHeader {{
                background-color: {t['surface']};
                border-bottom: 1px solid {t['border']};
            }}
            QLabel {{ color: {t['text_secondary']}; }}
            QPushButton {{ color: {t['accent']}; }}
            QPushButton:hover {{ color: {t['accent_hover']}; }}
        """)

        self.drop_zone.theme = t
        self.drop_zone.update_style()
        
        self.card_unpack.theme = t
        self.card_unpack.update_style()
        self.card_repack.theme = t
        self.card_repack.update_style()
        self.card_patch.theme = t
        self.card_patch.update_style()
        
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {t['bg']};
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {t['accent']};
            }}
        """)

    def change_theme(self, name):
        self.current_theme_name = name
        self.current_theme = THEMES[name]
        self.apply_theme()
        
    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = self.current_theme['console_text']
        
        if "成功" in message or "完成" in message:
            color = self.current_theme['success']
        elif "错误" in message or "失败" in message:
            color = self.current_theme['error']
        elif "警告" in message:
            color = self.current_theme['warning']
            
        html = f'<div style="margin-bottom: 2px;"><span style="color: #666;">[{timestamp}]</span> <span style="color: {color}">{message}</span></div>'
        self.log_text.append(html)
        
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def on_path_loaded(self, path):
        if not os.path.exists(path):
            return
            
        self.input_directory = path
        self.drop_zone.set_current_path(path)
        self.log(f"已加载路径: {path}")
        self.validate_path()
        
    def validate_path(self):
        files = os.listdir(self.input_directory)
        has_mcdat = any(f.lower().endswith('.mcdat') for f in files)
        has_json = os.path.exists(os.path.join(self.input_directory, '0.mcdat.json'))
        
        self.card_unpack.set_enabled(False)
        self.card_repack.set_enabled(False)
        self.card_patch.set_enabled(False)
        
        status_msg = []
        
        if has_mcdat:
            self.card_unpack.set_enabled(True)
            status_msg.append("发现 .mcdat 文件 (可解包)")
            
        if has_json:
            self.card_repack.set_enabled(True)
            self.card_patch.set_enabled(True)
            status_msg.append("发现 0.mcdat.json (可打包/补丁)")
            
        if not status_msg:
            self.log("⚠️ 警告: 该目录下未找到支持的文件 (.mcdat 或 0.mcdat.json)")
        else:
            self.log("✓ " + "，".join(status_msg))

    def start_operation(self, op_type):
        if not self.input_directory:
            QMessageBox.warning(self, "提示", "请先选择目录")
            return
            
        original_dir = None
        
        if op_type == 'patch':
            default_orig = os.path.join(os.path.dirname(self.input_directory), "output_bak")
            
            reply = QMessageBox.question(
                self, 
                "制作补丁", 
                "是否需要与【原版目录】进行比对？\n\n"
                "Yes (推荐): 仅打包修改过的文件（增量补丁）。\n"
                "No: 强制打包当前目录下所有文件。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                start_dir = default_orig if os.path.exists(default_orig) else os.path.dirname(self.input_directory)
                original_dir = QFileDialog.getExistingDirectory(
                    self, 
                    "请选择【原版/未修改】的 Output 解包目录 (如 output_bak)",
                    start_dir
                )
                if not original_dir:
                    self.log("用户取消了补丁制作操作")
                    return
                self.log(f"对比原版目录: {original_dir}")
        
        self.card_unpack.set_enabled(False)
        self.card_repack.set_enabled(False)
        self.card_patch.set_enabled(False)
        self.drop_zone.setEnabled(False)
        
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {self.current_theme['accent']}; }}")
        self.log("----------------------------------------")
        self.log(f"开始任务: {op_type} ...")
        
        do_backup = self.chk_backup.isChecked()
        do_auto_json = self.chk_auto_json.isChecked()
        
        self.worker = WorkerThread(
            op_type, 
            self.input_directory, 
            original_dir,
            create_backup=do_backup,
            auto_update_json=do_auto_json
        )
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()
        
    def update_progress(self, current, total, msg):
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
        self.log(f"正在处理 ({current}/{total}): {msg}")
        
    def on_finished(self, success, msg):
        self.validate_path()
        self.drop_zone.setEnabled(True)
        
        if success:
            self.progress_bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {self.current_theme['success']}; }}")
            self.log(f"🎉 {msg}")
            QMessageBox.information(self, "完成", msg)
        else:
            self.progress_bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {self.current_theme['error']}; }}")
            self.log(f"❌ {msg}")
            QMessageBox.critical(self, "错误", msg)
        self.log("----------------------------------------")


# ==================== CLI 逻辑 ====================
def cli_progress_callback(current: int, total: int, message: str):
    if total > 0:
        percentage = (current / total) * 100
        print(f"[{current}/{total}] ({percentage:.1f}%) {message}")
    else:
        print(message)

def run_cli(args):
    processor = McdatProcessor()
    try:
        if args.unpack:
            print("="*60 + "\n开始解包操作...\n" + "="*60)
            success = processor.unpack(args.unpack, create_backup=True, progress_callback=cli_progress_callback)
            return 0 if success else 1
        elif args.repack:
            print("="*60 + "\n开始重打包操作...\n" + "="*60)
            success = processor.repack(args.repack, progress_callback=cli_progress_callback)
            return 0 if success else 1
        elif args.makepatch:
            print("="*60 + "\n开始制作补丁...\n" + "="*60)
            if args.original:
                print(f"对比原版目录: {args.original}")
            success = processor.make_patch(args.makepatch, args.original, auto_update_json=True, progress_callback=cli_progress_callback)
            return 0 if success else 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"\n✗ 错误: {str(e)}")
        return 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Light.VN Mcdat Tool Modern v2.4')
    parser.add_argument('-u', '--unpack', metavar='DIR', help="解包目录")
    parser.add_argument('-p', '--repack', metavar='DIR', help="重打包目录")
    parser.add_argument('-patch', '--makepatch', metavar='MOD_DIR', help="制作补丁(修改后的目录)")
    parser.add_argument('-orig', '--original', metavar='ORIG_DIR', help="[可选] 原版目录，用于补丁增量比对")
    
    if len(sys.argv) > 1:
        sys.exit(run_cli(parser.parse_args()))
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    font = QFont("Microsoft YaHei UI", 9)
    if sys.platform == "win32":
        font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())