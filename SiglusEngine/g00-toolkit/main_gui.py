#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Siglus G00 Toolkit - PNG ⇔ G00 互转工具 (PyQt6 GUI 版)

功能介绍：
  支持 PNG 与 G00 格式的双向转换，支持多种 G00 类型：
  - Type 0: BGR24 格式（无透明通道）
  - Type 1: 索引色格式（带调色板）
  - Type 2: 分帧格式（多帧图像）
  - Type 3: 加密 JPEG 格式

现代化特性：
  1. 使用 PyQt6 构建现代化界面
  2. 多种配色方案可选（包括明亮/暗黑主题）
  3. 优化的进度显示和状态反馈
  4. 增强的拖放体验
  5. 实时日志输出
  6. 响应式布局设计

配色方案：
  明亮主题: 默认蓝、紫罗兰、翡翠绿、玫瑰金、琥珀橙、青色
  暗黑主题: 深空灰、暗夜紫、午夜蓝

依赖：
  必需：PyQt6, numpy, pillow
  可选：tqdm, xxhash, numba
"""

import os
import sys
import time
import threading
import traceback
from typing import Optional, List, Callable, Any
from dataclasses import dataclass
from enum import Enum

# PyQt6 导入
try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGridLayout, QLabel, QPushButton, QLineEdit, QComboBox,
        QTextEdit, QProgressBar, QCheckBox, QGroupBox, QFileDialog,
        QMessageBox, QSplitter, QFrame, QScrollArea, QSizePolicy,
        QSpacerItem, QSlider, QSpinBox, QRadioButton, QButtonGroup
    )
    from PyQt6.QtCore import (
        Qt, QThread, pyqtSignal, QMimeData, QUrl, QTimer, QSize
    )
    from PyQt6.QtGui import (
        QFont, QIcon, QDragEnterEvent, QDropEvent, QPalette, QColor,
        QTextCursor
    )
except ImportError:
    print("错误: 需要安装 PyQt6: pip install PyQt6")
    sys.exit(1)

# 导入 G00 处理模块
try:
    from g00_processor import G00Processor, G00Type, OperationMode, detect_g00_type, get_type_description
    PROCESSOR_AVAILABLE = True
except ImportError:
    PROCESSOR_AVAILABLE = False
    print("警告: 未找到 g00_processor.py 模块")

# 导入优化模块
try:
    from g00_optimizer import (
        get_optimizer_config, set_optimizer_config, OptimizerConfig,
        NUMBA_AVAILABLE, XXHASH_AVAILABLE
    )
    OPTIMIZER_AVAILABLE = True
except ImportError:
    OPTIMIZER_AVAILABLE = False
    NUMBA_AVAILABLE = False
    XXHASH_AVAILABLE = False

# 导入 GPU 加速模块
try:
    from g00_gpu_accelerator import CUDA_AVAILABLE, GPU_COUNT
    GPU_ACCELERATOR_AVAILABLE = True
except ImportError:
    GPU_ACCELERATOR_AVAILABLE = False
    CUDA_AVAILABLE = False
    GPU_COUNT = 0


# ===================== 常量和配置 =====================
# 多配色方案定义
COLOR_SCHEMES = {
    "默认蓝": {
        "bg": "#F5F5F5",
        "card_bg": "#FFFFFF",
        "input_bg": "#FFFFFF",
        "text": "#212121",
        "text_secondary": "#757575",
        "border": "#E0E0E0",
        "accent": "#1976D2",
        "accent_hover": "#1565C0",
        "success": "#388E3C",
        "success_light": "#4CAF50",
        "warning": "#F57C00",
        "error": "#D32F2F",
        "log_bg": "#FFFFFF",
        "progress_bg": "#E0E0E0",
        "disabled_bg": "#EEEEEE",
        "disabled_text": "#9E9E9E",
    },
    "紫罗兰": {
        "bg": "#F3F0F7",
        "card_bg": "#FFFFFF",
        "input_bg": "#FDFCFE",
        "text": "#2C1E3E",
        "text_secondary": "#7B6B8F",
        "border": "#DFD6EC",
        "accent": "#7C4DFF",
        "accent_hover": "#651FFF",
        "success": "#66BB6A",
        "success_light": "#81C784",
        "warning": "#FFA726",
        "error": "#EF5350",
        "log_bg": "#FDFCFE",
        "progress_bg": "#E8DEF8",
        "disabled_bg": "#F3F0F7",
        "disabled_text": "#B4A4C7",
    },
    "翡翠绿": {
        "bg": "#F0F7F4",
        "card_bg": "#FFFFFF",
        "input_bg": "#FCFEFD",
        "text": "#1E3A2E",
        "text_secondary": "#6B8F7B",
        "border": "#D6ECDF",
        "accent": "#00C853",
        "accent_hover": "#00B248",
        "success": "#43A047",
        "success_light": "#66BB6A",
        "warning": "#FB8C00",
        "error": "#E53935",
        "log_bg": "#FCFEFD",
        "progress_bg": "#C8E6C9",
        "disabled_bg": "#F0F7F4",
        "disabled_text": "#A4C7B4",
    },
    "玫瑰金": {
        "bg": "#FFF5F7",
        "card_bg": "#FFFFFF",
        "input_bg": "#FFFCFD",
        "text": "#3E1E2C",
        "text_secondary": "#8F6B7B",
        "border": "#FFD6E0",
        "accent": "#E91E63",
        "accent_hover": "#C2185B",
        "success": "#66BB6A",
        "success_light": "#81C784",
        "warning": "#FF7043",
        "error": "#F44336",
        "log_bg": "#FFFCFD",
        "progress_bg": "#F8BBD0",
        "disabled_bg": "#FFF5F7",
        "disabled_text": "#C7A4B4",
    },
    "琥珀橙": {
        "bg": "#FFF8F0",
        "card_bg": "#FFFFFF",
        "input_bg": "#FFFDFC",
        "text": "#3E2E1E",
        "text_secondary": "#8F7B6B",
        "border": "#FFE6D0",
        "accent": "#FF6F00",
        "accent_hover": "#E65100",
        "success": "#66BB6A",
        "success_light": "#81C784",
        "warning": "#FFA000",
        "error": "#E53935",
        "log_bg": "#FFFDFC",
        "progress_bg": "#FFE0B2",
        "disabled_bg": "#FFF8F0",
        "disabled_text": "#C7B4A4",
    },
    "青色": {
        "bg": "#F0F7F9",
        "card_bg": "#FFFFFF",
        "input_bg": "#FCFEFF",
        "text": "#1E3A3E",
        "text_secondary": "#6B8F8F",
        "border": "#D0E8EC",
        "accent": "#00ACC1",
        "accent_hover": "#00838F",
        "success": "#66BB6A",
        "success_light": "#81C784",
        "warning": "#FFA726",
        "error": "#EF5350",
        "log_bg": "#FCFEFF",
        "progress_bg": "#B2EBF2",
        "disabled_bg": "#F0F7F9",
        "disabled_text": "#A4C7C7",
    },
    "深空灰": {
        "bg": "#2C2C2C",
        "card_bg": "#383838",
        "input_bg": "#424242",
        "text": "#E0E0E0",
        "text_secondary": "#B0B0B0",
        "border": "#4F4F4F",
        "accent": "#42A5F5",
        "accent_hover": "#2196F3",
        "success": "#66BB6A",
        "success_light": "#81C784",
        "warning": "#FFA726",
        "error": "#EF5350",
        "log_bg": "#303030",
        "progress_bg": "#4F4F4F",
        "disabled_bg": "#3A3A3A",
        "disabled_text": "#707070",
    },
    "暗夜紫": {
        "bg": "#1E1428",
        "card_bg": "#2A1B3D",
        "input_bg": "#352647",
        "text": "#E8DFF5",
        "text_secondary": "#B8A0D9",
        "border": "#4A3564",
        "accent": "#9C27B0",
        "accent_hover": "#7B1FA2",
        "success": "#66BB6A",
        "success_light": "#81C784",
        "warning": "#FFA726",
        "error": "#EF5350",
        "log_bg": "#251A35",
        "progress_bg": "#4A3564",
        "disabled_bg": "#2D1F40",
        "disabled_text": "#6B5580",
    },
    "午夜蓝": {
        "bg": "#0D1B2A",
        "card_bg": "#1B263B",
        "input_bg": "#273649",
        "text": "#E0E1DD",
        "text_secondary": "#A8B5C8",
        "border": "#415A77",
        "accent": "#5E8FD8",
        "accent_hover": "#4A7DC2",
        "success": "#66BB6A",
        "success_light": "#81C784",
        "warning": "#FFA726",
        "error": "#EF5350",
        "log_bg": "#162330",
        "progress_bg": "#415A77",
        "disabled_bg": "#1F2E40",
        "disabled_text": "#5A6B7F",
    },
}

# 默认主题
CURRENT_THEME = COLOR_SCHEMES["默认蓝"]


# ===================== 拖放支持的输入框 =====================
class DragDropLineEdit(QLineEdit):
    """支持拖放的输入框"""
    
    def __init__(self, parent=None, accept_dirs=True, accept_files=True, 
                 file_filter: Optional[List[str]] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.accept_dirs = accept_dirs
        self.accept_files = accept_files
        self.file_filter = file_filter or []
        
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()
            
    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isdir(path) and self.accept_dirs:
                self.setText(path)
            elif os.path.isfile(path) and self.accept_files:
                if not self.file_filter:
                    self.setText(path)
                else:
                    ext = os.path.splitext(path)[1].lower()
                    if ext in self.file_filter:
                        self.setText(path)


# ===================== 工作线程 =====================
class WorkerThread(QThread):
    """后台工作线程"""
    
    progress_file = pyqtSignal(int, int)
    progress_batch = pyqtSignal(int, int, int, int, float)  # current, total, success, fail, elapsed
    log_message = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    current_file = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.processor: Optional[G00Processor] = None
        self._stop_flag = False
        self._pause_flag = False
        self._pause_condition = threading.Condition()
        
    def configure(self, processor: G00Processor):
        self.processor = processor
        self._stop_flag = False
        self._pause_flag = False
        
    def run(self):
        if not self.processor:
            self.finished_signal.emit(False, "处理器未配置")
            return
            
        try:
            self.processor.set_callbacks(
                file_progress_cb=self._on_file_progress,
                batch_progress_cb=self._on_batch_progress,
                log_cb=self._on_log,
                current_file_cb=self._on_current_file,
                check_stop_cb=self._check_stop,
                check_pause_cb=self._check_pause
            )
            
            success, message = self.processor.process()
            self.finished_signal.emit(success, message)
            
        except Exception as e:
            error_msg = f"处理过程中发生错误: {str(e)}\n{traceback.format_exc()}"
            self.log_message.emit(f"[ERROR] {error_msg}")
            self.finished_signal.emit(False, error_msg)
            
    def _on_file_progress(self, current: int, total: int):
        self.progress_file.emit(current, total)
        
    def _on_batch_progress(self, current: int, total: int, success: int, fail: int, elapsed: float):
        self.progress_batch.emit(current, total, success, fail, elapsed)
        
    def _on_log(self, message: str):
        self.log_message.emit(message)
        
    def _on_current_file(self, filename: str):
        self.current_file.emit(filename)
        
    def _check_stop(self) -> bool:
        return self._stop_flag
        
    def _check_pause(self):
        with self._pause_condition:
            while self._pause_flag and not self._stop_flag:
                self._pause_condition.wait(0.1)
                
    def stop(self):
        self._stop_flag = True
        self._pause_flag = False
        with self._pause_condition:
            self._pause_condition.notify_all()
            
    def pause(self):
        self._pause_flag = True
        
    def resume(self):
        with self._pause_condition:
            self._pause_flag = False
            self._pause_condition.notify_all()
            
    def is_paused(self) -> bool:
        return self._pause_flag


# ===================== 主窗口 =====================
class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        self.worker: Optional[WorkerThread] = None
        self.start_time = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_elapsed_time)
        self.current_theme_name = "默认蓝"  # 当前主题名称
        
        self._init_ui()
        self._apply_theme()
        self._connect_signals()
        self._update_ui_state()
        
    def _init_ui(self):
        """初始化界面"""
        self.setWindowTitle("Siglus G00 Toolkit - PNG ⇔ G00 互转工具")
        self.setMinimumSize(1100, 800)
        self.resize(1200, 900)
        
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)
        
        # 标题栏（居中显示）
        self._create_title_bar(main_layout)
        
        # 主内容区
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter, 1)
        
        # 左侧：控制面板
        left_panel = self._create_control_panel()
        splitter.addWidget(left_panel)
        
        # 右侧：日志和进度
        right_panel = self._create_log_panel()
        splitter.addWidget(right_panel)
        
        splitter.setSizes([580, 520])
        
    def _create_title_bar(self, parent_layout: QVBoxLayout):
        """创建标题栏（居中）"""
        title_frame = QFrame()
        title_frame.setObjectName("titleFrame")
        title_layout = QHBoxLayout(title_frame)
        title_layout.setContentsMargins(20, 12, 20, 12)
        
        # 使用弹簧使标题居中
        title_layout.addStretch()
        
        title_label = QLabel("🌈 Siglus G00 Toolkit - PNG ⇔ G00 互转工具")
        title_label.setObjectName("titleLabel")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_layout.addWidget(title_label)
        
        title_layout.addStretch()
        
        # 配色方案选择器
        theme_label = QLabel("🎨 配色:")
        theme_label.setObjectName("themeLabel")
        title_layout.addWidget(theme_label)
        
        self.combo_theme = QComboBox()
        self.combo_theme.setObjectName("themeCombo")
        self.combo_theme.addItems(list(COLOR_SCHEMES.keys()))
        self.combo_theme.setCurrentText(self.current_theme_name)
        self.combo_theme.setMinimumWidth(120)
        self.combo_theme.setMinimumHeight(32)
        self.combo_theme.currentTextChanged.connect(self._on_theme_changed)
        title_layout.addWidget(self.combo_theme)
        
        parent_layout.addWidget(title_frame)
        
    def _create_control_panel(self) -> QWidget:
        """创建控制面板"""
        panel = QFrame()
        panel.setObjectName("controlPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 10, 0)
        scroll_layout.setSpacing(10)
        
        # ==================== 1. 转换模式（放在最上方）====================
        mode_group = QGroupBox("🔄 转换模式")
        mode_group.setObjectName("groupBox")
        mode_layout = QHBoxLayout(mode_group)
        
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["G00 → PNG (提取)", "PNG → G00 (构建)"])
        self.combo_mode.setMinimumHeight(32)
        mode_layout.addWidget(self.combo_mode)
        scroll_layout.addWidget(mode_group)
        
        # ==================== 2. 输入/输出路径 ====================
        io_group = QGroupBox("📁 路径配置")
        io_group.setObjectName("groupBox")
        io_layout = QGridLayout(io_group)
        io_layout.setSpacing(8)
        
        # 输入路径
        io_layout.addWidget(QLabel("📥 输入路径:"), 0, 0)
        self.edit_input = DragDropLineEdit(file_filter=['.g00', '.png', '.G00', '.PNG'])
        self.edit_input.setPlaceholderText("拖放文件/文件夹到此处，或点击右侧按钮选择...")
        self.edit_input.setMinimumHeight(32)
        io_layout.addWidget(self.edit_input, 0, 1)
        
        input_btn_layout = QHBoxLayout()
        btn_input_file = QPushButton("📄 文件")
        btn_input_file.setMinimumHeight(32)
        btn_input_file.clicked.connect(self._select_input_file)
        input_btn_layout.addWidget(btn_input_file)
        
        btn_input_dir = QPushButton("📁 文件夹")
        btn_input_dir.setMinimumHeight(32)
        btn_input_dir.clicked.connect(self._select_input_dir)
        input_btn_layout.addWidget(btn_input_dir)
        io_layout.addLayout(input_btn_layout, 0, 2)
        
        # 输出路径
        io_layout.addWidget(QLabel("📤 输出路径:"), 1, 0)
        self.edit_output = DragDropLineEdit()
        self.edit_output.setPlaceholderText("输出目录...")
        self.edit_output.setMinimumHeight(32)
        io_layout.addWidget(self.edit_output, 1, 1)
        
        btn_output = QPushButton("📁 选择")
        btn_output.setMinimumHeight(32)
        btn_output.clicked.connect(self._select_output_dir)
        io_layout.addWidget(btn_output, 1, 2)
        
        # 递归选项
        self.check_recursive = QCheckBox("📂 递归处理子文件夹")
        io_layout.addWidget(self.check_recursive, 2, 0, 1, 3)
        
        scroll_layout.addWidget(io_group)
        
        # ==================== 3. 构建模式设置 ====================
        self.build_group = QGroupBox("🔧 构建模式设置")
        self.build_group.setObjectName("groupBox")
        build_layout = QGridLayout(self.build_group)
        build_layout.setSpacing(8)
        
        # 原始 G00 文件/文件夹
        build_layout.addWidget(QLabel("原始 G00:"), 0, 0)
        self.edit_orig_g00 = DragDropLineEdit(file_filter=['.g00', '.G00'])
        self.edit_orig_g00.setPlaceholderText("原始 G00 文件或文件夹...")
        self.edit_orig_g00.setMinimumHeight(32)
        build_layout.addWidget(self.edit_orig_g00, 0, 1)
        
        orig_btn_layout = QHBoxLayout()
        btn_orig_file = QPushButton("📄 文件")
        btn_orig_file.setMinimumHeight(30)
        btn_orig_file.clicked.connect(self._select_orig_g00_file)
        orig_btn_layout.addWidget(btn_orig_file)
        
        btn_orig_dir = QPushButton("📁 文件夹")
        btn_orig_dir.setMinimumHeight(30)
        btn_orig_dir.clicked.connect(self._select_orig_g00_dir)
        orig_btn_layout.addWidget(btn_orig_dir)
        build_layout.addLayout(orig_btn_layout, 0, 2)
        
        # JSON 配置
        build_layout.addWidget(QLabel("JSON 配置:"), 1, 0)
        json_input_layout = QHBoxLayout()
        self.check_use_json = QCheckBox("使用 JSON")
        self.check_use_json.setChecked(True)
        json_input_layout.addWidget(self.check_use_json)
        self.edit_json = DragDropLineEdit(file_filter=['.json', '.JSON'])
        self.edit_json.setPlaceholderText("JSON 配置文件（留空则自动查找）...")
        self.edit_json.setMinimumHeight(32)
        json_input_layout.addWidget(self.edit_json, 1)
        build_layout.addLayout(json_input_layout, 1, 1)
        
        btn_json = QPushButton("📄 选择")
        btn_json.setMinimumHeight(30)
        btn_json.clicked.connect(self._select_json)
        build_layout.addWidget(btn_json, 1, 2)
        
        # 提示文本
        hint_label = QLabel("提示: 批量构建时，程序会根据原始 G00 文件名在输入目录中自动查找同名子文件夹和 JSON 配置")
        hint_label.setObjectName("hintLabel")
        hint_label.setWordWrap(True)
        build_layout.addWidget(hint_label, 2, 0, 1, 3)
        
        # 完全重建 G00 结构
        self.check_rebuild = QCheckBox("完全重建 G00 结构（解决 tile 复用导致显示不全的问题）")
        self.check_rebuild.setChecked(True)  # 默认勾选
        self.check_rebuild.setToolTip("忽略原始 G00 结构，从 PNG 重新生成 Frame 和 Tile 数据")
        build_layout.addWidget(self.check_rebuild, 3, 0, 1, 3)
        
        scroll_layout.addWidget(self.build_group)
        
        # ==================== 4. G00 类型选择 ====================
        type_group = QGroupBox("📄 G00 类型")
        type_group.setObjectName("groupBox")
        type_layout = QHBoxLayout(type_group)
        
        self.combo_type = QComboBox()
        self.combo_type.addItems(["自动检测", "Type 0 (BGR24)", "Type 1 (索引色)", 
                                   "Type 2 (分帧)", "Type 3 (加密JPEG)"])
        self.combo_type.setMinimumHeight(32)
        type_layout.addWidget(self.combo_type)
        scroll_layout.addWidget(type_group)
        
        # ==================== 5. 压缩设置 ====================
        compress_group = QGroupBox("⚙️ 压缩与提取设置")
        compress_group.setObjectName("groupBox")
        compress_layout = QGridLayout(compress_group)
        compress_layout.setSpacing(8)
        
        self.lbl_preset = QLabel("压缩预设:")
        compress_layout.addWidget(self.lbl_preset, 0, 0)
        self.combo_preset = QComboBox()
        self.combo_preset.addItems(["fast (快速)", "normal (平衡)", "max (最佳)", "ultra_max (极限/混合)", "promax (极致/暴力搜索)"])
        self.combo_preset.setCurrentIndex(2)  # 默认为 max
        self.combo_preset.setMinimumHeight(30)
        compress_layout.addWidget(self.combo_preset, 0, 1)
        
        self.lbl_extract_label = QLabel("提取模式:")
        compress_layout.addWidget(self.lbl_extract_label, 1, 0)
        self.combo_extract_mode = QComboBox()
        self.combo_extract_mode.addItems(["psd (仅官方PSD)", "full (整张)", "bbox (最小)", "tiles (分块)", "both (全部)"])
        self.combo_extract_mode.setCurrentIndex(2)  # 默认为 bbox
        self.combo_extract_mode.setMinimumHeight(30)
        compress_layout.addWidget(self.combo_extract_mode, 1, 1)
        self.lbl_extract_hint = QLabel("(仅 Type 2 有效)")
        self.lbl_extract_hint.setObjectName("hintLabel")
        compress_layout.addWidget(self.lbl_extract_hint, 1, 2)
        
        self.check_export_psd = QCheckBox("生成 G00Pack 官方兼容 PSD")
        self.check_export_psd.setChecked(False)
        self.check_export_psd.setVisible(False)
        compress_layout.addWidget(self.check_export_psd, 2, 0, 1, 3)
        
        scroll_layout.addWidget(compress_group)
        
        # ==================== 6. Type 3 专用设置 ====================
        self.type3_group = QGroupBox("🖼️ Type 3 (JPEG) 设置")
        self.type3_group.setObjectName("groupBox")
        type3_layout = QGridLayout(self.type3_group)
        type3_layout.setSpacing(8)
        
        # 输出格式选择
        type3_layout.addWidget(QLabel("输出格式:"), 0, 0)
        format_widget = QWidget()
        format_layout = QHBoxLayout(format_widget)
        format_layout.setContentsMargins(0, 0, 0, 0)
        self.radio_png = QRadioButton("PNG (推荐)")
        self.radio_jpg = QRadioButton("JPG (原始)")
        self.radio_png.setChecked(True)
        format_layout.addWidget(self.radio_png)
        format_layout.addWidget(self.radio_jpg)
        format_layout.addStretch()
        type3_layout.addWidget(format_widget, 0, 1, 1, 2)
        
        # JPEG 质量
        type3_layout.addWidget(QLabel("JPEG 质量:"), 1, 0)
        self.slider_jpeg_quality = QSlider(Qt.Orientation.Horizontal)
        self.slider_jpeg_quality.setMinimum(1)
        self.slider_jpeg_quality.setMaximum(100)
        self.slider_jpeg_quality.setValue(95)
        self.slider_jpeg_quality.setMinimumWidth(150)
        type3_layout.addWidget(self.slider_jpeg_quality, 1, 1)
        
        self.spin_jpeg_quality = QSpinBox()
        self.spin_jpeg_quality.setMinimum(1)
        self.spin_jpeg_quality.setMaximum(100)
        self.spin_jpeg_quality.setValue(95)
        self.spin_jpeg_quality.setMinimumWidth(60)
        type3_layout.addWidget(self.spin_jpeg_quality, 1, 2)
        
        # 同步滑块和数值框
        self.slider_jpeg_quality.valueChanged.connect(self.spin_jpeg_quality.setValue)
        self.spin_jpeg_quality.valueChanged.connect(self.slider_jpeg_quality.setValue)
        
        scroll_layout.addWidget(self.type3_group)
        
        # ==================== 7. 并行处理设置 ====================
        parallel_group = QGroupBox("⚡ 并行处理设置")
        parallel_group.setObjectName("groupBox")
        parallel_layout = QGridLayout(parallel_group)
        parallel_layout.setSpacing(8)
        
        # 启用并行处理
        self.check_parallel = QCheckBox("启用并行处理（多线程）")
        self.check_parallel.setChecked(True)
        self.check_parallel.stateChanged.connect(self._on_parallel_toggle)
        parallel_layout.addWidget(self.check_parallel, 0, 0, 1, 3)
        
        # 线程数
        parallel_layout.addWidget(QLabel("线程数:"), 1, 0)
        self.spin_workers = QSpinBox()
        self.spin_workers.setMinimum(0)
        self.spin_workers.setMaximum(32)
        self.spin_workers.setValue(0)  # 0 = 自动
        self.spin_workers.setMinimumWidth(80)
        self.spin_workers.setToolTip("0 = 自动检测 (CPU核心数-1)")
        parallel_layout.addWidget(self.spin_workers, 1, 1)
        
        self.lbl_workers_hint = QLabel("(0 = 自动)")
        self.lbl_workers_hint.setObjectName("hintLabel")
        parallel_layout.addWidget(self.lbl_workers_hint, 1, 2)
        
        # 最小文件数
        parallel_layout.addWidget(QLabel("最小文件数:"), 2, 0)
        self.spin_min_files = QSpinBox()
        self.spin_min_files.setMinimum(2)
        self.spin_min_files.setMaximum(100)
        self.spin_min_files.setValue(4)
        self.spin_min_files.setMinimumWidth(80)
        self.spin_min_files.setToolTip("文件数达到此值才启用并行处理")
        parallel_layout.addWidget(self.spin_min_files, 2, 1)
        
        self.lbl_min_files_hint = QLabel("(文件数 >= 此值时启用并行)")
        self.lbl_min_files_hint.setObjectName("hintLabel")
        parallel_layout.addWidget(self.lbl_min_files_hint, 2, 2)
        
        scroll_layout.addWidget(parallel_group)
        
        # ==================== 8. 优化配置设置 ====================
        self.optimize_group = QGroupBox("🚀 性能优化设置")
        self.optimize_group.setObjectName("groupBox")
        optimize_layout = QGridLayout(self.optimize_group)
        optimize_layout.setSpacing(8)
        
        # 环境检测信息
        env_info_label = QLabel()
        env_parts = []
        if OPTIMIZER_AVAILABLE:
            env_parts.append(f"Numba: {'✓' if NUMBA_AVAILABLE else '✗'}")
            env_parts.append(f"xxhash: {'✓' if XXHASH_AVAILABLE else '✗'}")
        if GPU_ACCELERATOR_AVAILABLE:
            env_parts.append(f"CUDA: {'✓' if CUDA_AVAILABLE else '✗'} (GPU:{GPU_COUNT})")
        else:
            env_parts.append("CUDA: ✗")
        env_info_label.setText("环境: " + " | ".join(env_parts) if env_parts else "优化模块未加载")
        env_info_label.setObjectName("hintLabel")
        optimize_layout.addWidget(env_info_label, 0, 0, 1, 3)
        
        # 启用 Numba JIT 加速
        self.check_numba = QCheckBox("启用 Numba JIT 加速 (LZSS 解压/压缩 5-15x)")
        self.check_numba.setChecked(True)
        self.check_numba.setEnabled(NUMBA_AVAILABLE)
        if not NUMBA_AVAILABLE:
            self.check_numba.setToolTip("未安装 numba: pip install numba")
        optimize_layout.addWidget(self.check_numba, 1, 0, 1, 3)
        
        # 启用内存池
        self.check_memory_pool = QCheckBox("启用内存池优化 (减少 30-50% 内存分配开销)")
        self.check_memory_pool.setChecked(True)
        self.check_memory_pool.setEnabled(OPTIMIZER_AVAILABLE)
        optimize_layout.addWidget(self.check_memory_pool, 2, 0, 1, 3)
        
        # 启用 xxhash 快速哈希
        self.check_xxhash = QCheckBox("启用 xxhash 快速哈希 (匹配器加速)")
        self.check_xxhash.setChecked(True)
        self.check_xxhash.setEnabled(XXHASH_AVAILABLE)
        if not XXHASH_AVAILABLE:
            self.check_xxhash.setToolTip("未安装 xxhash: pip install xxhash")
        optimize_layout.addWidget(self.check_xxhash, 3, 0, 1, 3)
        
        # GPU 加速
        self.check_gpu = QCheckBox("启用 GPU 加速 (CUDA/CuPy)")
        self.check_gpu.setChecked(True)  # 默认启用
        self.check_gpu.setEnabled(CUDA_AVAILABLE)
        if not CUDA_AVAILABLE:
            self.check_gpu.setToolTip("未安装 CuPy 或无 CUDA GPU: pip install cupy-cuda12x")
        optimize_layout.addWidget(self.check_gpu, 4, 0, 1, 3)
        
        scroll_layout.addWidget(self.optimize_group)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)
        
        # 控制按钮
        self._create_control_buttons(layout)
        
        return panel
        
    def _create_control_buttons(self, parent_layout: QVBoxLayout):
        """创建控制按钮"""
        btn_frame = QFrame()
        btn_frame.setObjectName("controlButtons")
        btn_layout = QVBoxLayout(btn_frame)
        btn_layout.setContentsMargins(0, 10, 0, 0)
        btn_layout.setSpacing(8)
        
        # 开始按钮
        self.btn_start = QPushButton("▶️ 开始处理")
        self.btn_start.setObjectName("startButton")
        self.btn_start.setMinimumHeight(50)
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_font = QFont()
        btn_font.setPointSize(14)
        btn_font.setBold(True)
        self.btn_start.setFont(btn_font)
        btn_layout.addWidget(self.btn_start)
        
        # 暂停/停止按钮
        ctrl_btn_layout = QHBoxLayout()
        
        self.btn_pause = QPushButton("⏸️ 暂停")
        self.btn_pause.setObjectName("pauseButton")
        self.btn_pause.setMinimumHeight(40)
        self.btn_pause.setEnabled(False)
        ctrl_btn_layout.addWidget(self.btn_pause)
        
        self.btn_stop = QPushButton("⏹️ 停止")
        self.btn_stop.setObjectName("stopButton")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setEnabled(False)
        ctrl_btn_layout.addWidget(self.btn_stop)
        
        btn_layout.addLayout(ctrl_btn_layout)
        parent_layout.addWidget(btn_frame)
        
    def _create_log_panel(self) -> QWidget:
        """创建日志面板"""
        panel = QFrame()
        panel.setObjectName("logPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # 日志区域
        log_group = QGroupBox("📝 处理日志")
        log_group.setObjectName("groupBox")
        log_layout = QVBoxLayout(log_group)
        
        # 日志控制栏
        log_control = QHBoxLayout()
        log_control.addWidget(QLabel("日志级别:"))
        
        self.combo_log_level = QComboBox()
        self.combo_log_level.addItems(["简洁", "普通", "详细"])
        self.combo_log_level.setCurrentIndex(1)
        self.combo_log_level.setMinimumWidth(100)
        log_control.addWidget(self.combo_log_level)
        
        log_control.addStretch()
        
        btn_clear_log = QPushButton("🗑️ 清空日志")
        btn_clear_log.clicked.connect(self._clear_log)
        log_control.addWidget(btn_clear_log)
        
        log_layout.addLayout(log_control)
        
        # 日志文本框
        self.text_log = QTextEdit()
        self.text_log.setObjectName("logText")
        self.text_log.setReadOnly(True)
        self.text_log.setFont(QFont("Consolas", 10))
        self.text_log.setPlaceholderText("等待处理...")
        log_layout.addWidget(self.text_log, 1)
        
        layout.addWidget(log_group, 1)
        
        # 进度区域
        progress_group = QGroupBox("📊 处理进度")
        progress_group.setObjectName("groupBox")
        progress_layout = QVBoxLayout(progress_group)
        
        # 统计信息
        stats_layout = QHBoxLayout()
        self.lbl_stats = QLabel("总文件: 0 | 成功: 0 | 失败: 0")
        self.lbl_stats.setObjectName("statsLabel")
        stats_layout.addWidget(self.lbl_stats)
        stats_layout.addStretch()
        self.lbl_elapsed = QLabel("已用时间: 0.0s")
        stats_layout.addWidget(self.lbl_elapsed)
        progress_layout.addLayout(stats_layout)
        
        # 当前文件进度
        progress_layout.addWidget(QLabel("📄 当前文件:"))
        self.lbl_current_file = QLabel("等待处理...")
        self.lbl_current_file.setObjectName("currentFileLabel")
        progress_layout.addWidget(self.lbl_current_file)
        
        self.progress_file = QProgressBar()
        self.progress_file.setObjectName("fileProgress")
        self.progress_file.setMinimumHeight(20)
        self.progress_file.setValue(0)
        self.progress_file.setFormat("%p%")
        progress_layout.addWidget(self.progress_file)
        
        # 批量进度
        progress_layout.addWidget(QLabel("📈 批量进度:"))
        self.progress_batch = QProgressBar()
        self.progress_batch.setObjectName("batchProgress")
        self.progress_batch.setMinimumHeight(20)
        self.progress_batch.setValue(0)
        self.progress_batch.setFormat("%v / %m (%p%)")
        progress_layout.addWidget(self.progress_batch)
        
        layout.addWidget(progress_group)
        
        # 初始化日志
        self._write_initial_log()
        
        return panel
        
    def _write_initial_log(self):
        """写入初始日志"""
        init_text = """═══════════════════════════════════════════════════════════════════════════
                    Siglus G00 Toolkit — PNG ⇔ G00 互转工具                    
═══════════════════════════════════════════════════════════════════════════

【支持的格式】
  ■ Type 0: BGR24 格式（无透明通道）
  ■ Type 1: 索引色格式（带调色板）
  ■ Type 2: 分帧格式（多帧图像）
  ■ Type 3: 加密 JPEG 格式

【核心功能】
  ✓ 自动类型检测  ✓ 双向转换  ✓ 批量处理  ✓ 拖放支持  ✓ 实时进度

【使用步骤】
  1. 配置输入/输出路径
  2. 选择转换模式（G00 → PNG 或 PNG → G00）
  3. 选择 G00 类型（推荐'自动检测'）
  4. 点击'开始处理'

【提示】支持拖放文件/文件夹 | 提取模式仅对 Type 2 有效

═══════════════════════════════════════════════════════════════════════════
>> 等待用户操作...

"""
        self.text_log.setPlainText(init_text)
        
    def _connect_signals(self):
        """连接信号"""
        self.btn_start.clicked.connect(self._start_processing)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_stop.clicked.connect(self._stop_processing)
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.combo_type.currentIndexChanged.connect(self._on_type_changed)
        self.check_use_json.stateChanged.connect(self._on_json_toggle)
        
    def _on_theme_changed(self, theme_name: str):
        """配色方案切换"""
        self.current_theme_name = theme_name
        self._apply_theme()
        
    def _update_ui_state(self):
        """更新UI状态"""
        is_extract = self.combo_mode.currentIndex() == 0
        type_idx = self.combo_type.currentIndex()
        
        # 构建模式设置
        self.build_group.setVisible(not is_extract)
        
        # Type 3 设置
        is_type3 = type_idx == 4  # Type 3 (加密JPEG)
        self.type3_group.setVisible(is_type3 or type_idx == 0)  # 自动检测时也显示
        
        # 提取模式（仅 Type 2 有效）
        is_type2 = type_idx == 3  # Type 2 (分帧)
        is_auto = type_idx == 0
        self.combo_extract_mode.setEnabled(is_type2 or is_auto)
        self.lbl_extract_label.setVisible(is_extract)
        self.combo_extract_mode.setVisible(is_extract)
        self.lbl_extract_hint.setVisible(is_extract and not is_type2 and not is_auto)
        self.lbl_preset.setVisible(not is_extract)
        self.combo_preset.setVisible(not is_extract)
        
        # JSON 配置开关
        use_json = self.check_use_json.isChecked()
        self.edit_json.setEnabled(use_json)
        
    def _on_mode_changed(self, index: int):
        """模式改变"""
        self._update_ui_state()
        
    def _on_type_changed(self, index: int):
        """类型改变"""
        self._update_ui_state()
        
    def _on_json_toggle(self, state: int):
        """JSON 开关切换"""
        self._update_ui_state()
    
    def _on_parallel_toggle(self, state: int):
        """并行处理开关切换"""
        enabled = self.check_parallel.isChecked()
        self.spin_workers.setEnabled(enabled)
        self.spin_min_files.setEnabled(enabled)
        
    def _apply_theme(self):
        """应用主题"""
        theme = COLOR_SCHEMES.get(self.current_theme_name, COLOR_SCHEMES["默认蓝"])
        
        stylesheet = f"""
            QMainWindow, QWidget {{
                background-color: {theme["card_bg"]};
                color: {theme["text"]};
            }}
            
            #titleFrame {{
                background-color: {theme["card_bg"]};
                border: 2px solid {theme["accent"]};
                border-radius: 10px;
            }}
            
            #titleLabel {{
                color: {theme["accent"]};
                background-color: transparent;
            }}
            
            #controlPanel, #logPanel {{
                background-color: {theme["card_bg"]};
                border: 1px solid {theme["border"]};
                border-radius: 10px;
            }}
            
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {theme["border"]};
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 12px;
                background-color: transparent;
            }}
            
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {theme["text"]};
                background-color: transparent;
            }}
            
            QLabel {{
                background-color: transparent;
            }}
            
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            
            QScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
            
            QLineEdit {{
                background-color: {theme["input_bg"]};
                border: 1px solid {theme["border"]};
                border-radius: 5px;
                padding: 5px 10px;
                color: {theme["text"]};
            }}
            
            QLineEdit:focus {{
                border: 2px solid {theme["accent"]};
            }}
            
            QLineEdit:disabled {{
                background-color: {theme["disabled_bg"]};
                color: {theme["disabled_text"]};
            }}
            
            QComboBox {{
                background-color: {theme["input_bg"]};
                border: 1px solid {theme["border"]};
                border-radius: 5px;
                padding: 5px 10px;
                color: {theme["text"]};
            }}
            
            QComboBox:hover {{
                border: 1px solid {theme["accent"]};
            }}
            
            QComboBox:disabled {{
                background-color: {theme["disabled_bg"]};
                color: {theme["disabled_text"]};
            }}
            
            QComboBox::drop-down {{
                border: none;
                width: 25px;
            }}
            
            QComboBox QAbstractItemView {{
                background-color: {theme["card_bg"]};
                border: 1px solid {theme["border"]};
                selection-background-color: {theme["accent"]};
                color: {theme["text"]};
            }}
            
            QPushButton {{
                background-color: {theme["accent"]};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 15px;
                font-weight: bold;
            }}
            
            QPushButton:hover {{
                background-color: {theme["accent_hover"]};
            }}
            
            QPushButton:disabled {{
                background-color: {theme["disabled_bg"]};
                color: {theme["disabled_text"]};
            }}
            
            #startButton {{
                background-color: {theme["success"]};
                font-size: 14px;
            }}
            
            #startButton:hover {{
                background-color: {theme["success_light"]};
            }}
            
            #pauseButton {{
                background-color: {theme["warning"]};
            }}
            
            #pauseButton:hover {{
                background-color: #EF6C00;
            }}
            
            #stopButton {{
                background-color: {theme["error"]};
            }}
            
            #stopButton:hover {{
                background-color: #C62828;
            }}
            
            QCheckBox, QRadioButton {{
                color: {theme["text"]};
                spacing: 10px;
                padding: 4px 0px;
            }}
            
            /* ========== 复选框样式 ========== */
            QCheckBox::indicator {{
                width: 20px;
                height: 20px;
                border-radius: 4px;
                border: 2px solid {theme["text_secondary"]};
                background-color: {theme["card_bg"]};
            }}
            
            QCheckBox::indicator:hover {{
                border: 2px solid {theme["accent"]};
            }}
            
            QCheckBox::indicator:checked {{
                border: 2px solid {theme["accent"]};
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.7, fx:0.5, fy:0.5,
                    stop:0 white, stop:0.35 white,
                    stop:0.4 {theme["accent"]}, stop:1 {theme["accent"]});
            }}
            
            QCheckBox::indicator:checked:hover {{
                border: 2px solid {theme["accent_hover"]};
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.7, fx:0.5, fy:0.5,
                    stop:0 white, stop:0.35 white,
                    stop:0.4 {theme["accent_hover"]}, stop:1 {theme["accent_hover"]});
            }}
            
            QCheckBox::indicator:disabled {{
                border: 2px solid {theme["disabled_text"]};
                background-color: {theme["disabled_bg"]};
            }}
            
            QCheckBox::indicator:checked:disabled {{
                border: 2px solid {theme["disabled_text"]};
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.7, fx:0.5, fy:0.5,
                    stop:0 {theme["disabled_bg"]}, stop:0.35 {theme["disabled_bg"]},
                    stop:0.4 {theme["disabled_text"]}, stop:1 {theme["disabled_text"]});
            }}
            
            /* ========== 单选按钮样式 ========== */
            QRadioButton::indicator {{
                width: 22px;
                height: 22px;
                border-radius: 11px;
                border: 2px solid {theme["text_secondary"]};
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {theme["card_bg"]}, stop:1 {theme["input_bg"]});
            }}
            
            QRadioButton::indicator:hover {{
                border: 2px solid {theme["accent"]};
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {theme["input_bg"]}, stop:1 {theme["bg"]});
            }}
            
            QRadioButton::indicator:checked {{
                border: 2px solid {theme["accent"]};
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                    stop:0 {theme["accent"]}, stop:0.45 {theme["accent"]},
                    stop:0.5 {theme["card_bg"]}, stop:1 {theme["card_bg"]});
            }}
            
            QRadioButton::indicator:checked:hover {{
                border: 2px solid {theme["accent_hover"]};
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                    stop:0 {theme["accent_hover"]}, stop:0.45 {theme["accent_hover"]},
                    stop:0.5 {theme["input_bg"]}, stop:1 {theme["input_bg"]});
            }}
            
            QRadioButton::indicator:disabled {{
                border: 2px solid {theme["disabled_text"]};
                background: {theme["disabled_bg"]};
            }}
            
            QRadioButton::indicator:checked:disabled {{
                border: 2px solid {theme["disabled_text"]};
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                    stop:0 {theme["disabled_text"]}, stop:0.45 {theme["disabled_text"]},
                    stop:0.5 {theme["disabled_bg"]}, stop:1 {theme["disabled_bg"]});
            }}
            
            #logText {{
                background-color: {theme["log_bg"]};
                border: 1px solid {theme["border"]};
                border-radius: 5px;
                color: {theme["text"]};
            }}
            
            QProgressBar {{
                background-color: {theme["progress_bg"]};
                border: none;
                border-radius: 10px;
                text-align: center;
                color: {theme["text"]};
            }}
            
            #fileProgress::chunk {{
                background-color: {theme["success"]};
                border-radius: 10px;
            }}
            
            #batchProgress::chunk {{
                background-color: {theme["accent"]};
                border-radius: 10px;
            }}
            
            QScrollBar:vertical {{
                background-color: {theme["bg"]};
                width: 12px;
                border-radius: 6px;
            }}
            
            QScrollBar::handle:vertical {{
                background-color: {theme["border"]};
                border-radius: 6px;
                min-height: 30px;
            }}
            
            QScrollBar::handle:vertical:hover {{
                background-color: {theme["accent"]};
            }}
            
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            
            #statsLabel {{
                font-weight: bold;
                color: {theme["success"]};
            }}
            
            #currentFileLabel {{
                color: {theme["text_secondary"]};
            }}
            
            #hintLabel {{
                color: {theme["text_secondary"]};
                font-size: 11px;
            }}
            
            QSlider::groove:horizontal {{
                height: 6px;
                background: {theme["progress_bg"]};
                border-radius: 3px;
            }}
            
            QSlider::handle:horizontal {{
                width: 16px;
                height: 16px;
                margin: -5px 0;
                background: {theme["accent"]};
                border-radius: 8px;
            }}
            
            QSlider::sub-page:horizontal {{
                background: {theme["accent"]};
                border-radius: 3px;
            }}
            
            QSpinBox {{
                background-color: {theme["input_bg"]};
                border: 1px solid {theme["border"]};
                border-radius: 5px;
                padding: 3px;
                color: {theme["text"]};
            }}
            
            QSplitter::handle {{
                background-color: {theme["border"]};
            }}
            
            QSplitter::handle:hover {{
                background-color: {theme["accent"]};
            }}
        """
        
        self.setStyleSheet(stylesheet)
        
    # ===================== 文件选择 =====================
    def _select_input_file(self):
        mode = self.combo_mode.currentIndex()
        if mode == 0:
            filter_str = "G00 文件 (*.g00 *.G00);;所有文件 (*.*)"
        else:
            filter_str = "PNG 文件 (*.png *.PNG);;所有文件 (*.*)"
            
        file_path, _ = QFileDialog.getOpenFileName(self, "选择输入文件", "", filter_str)
        if file_path:
            self.edit_input.setText(file_path)
            
    def _select_input_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择输入文件夹")
        if dir_path:
            self.edit_input.setText(dir_path)
            
    def _select_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if dir_path:
            self.edit_output.setText(dir_path)
            
    def _select_orig_g00_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择原始 G00 文件", "", "G00 文件 (*.g00 *.G00);;所有文件 (*.*)"
        )
        if file_path:
            self.edit_orig_g00.setText(file_path)
            
    def _select_orig_g00_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择原始 G00 文件夹")
        if dir_path:
            self.edit_orig_g00.setText(dir_path)
            
    def _select_json(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择 JSON 配置文件", "", "JSON 文件 (*.json);;所有文件 (*.*)"
        )
        if file_path:
            self.edit_json.setText(file_path)
            
    # ===================== 日志操作 =====================
    def _clear_log(self):
        self.text_log.clear()
        
    def _append_log(self, message: str):
        level = self.combo_log_level.currentText()
        show = False
        
        if "[ERROR]" in message:
            show = True
        elif level == "详细":
            show = True
        elif level == "普通":
            if "[OK]" in message or "[WARNING]" in message or "[INFO]" in message:
                show = True
        elif level == "简洁":
            if "[OK]" in message or "[ERROR]" in message:
                show = True
                
        if show:
            self.text_log.append(message)
            cursor = self.text_log.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.text_log.setTextCursor(cursor)
            
    # ===================== 处理操作 =====================
    def _start_processing(self):
        if not PROCESSOR_AVAILABLE:
            QMessageBox.critical(self, "错误", "G00 处理模块未加载，请确保 g00_processor.py 存在。")
            return
            
        input_path = self.edit_input.text().strip()
        output_path = self.edit_output.text().strip()
        
        if not input_path:
            QMessageBox.warning(self, "警告", "请指定输入路径！")
            return
            
        if not os.path.exists(input_path):
            QMessageBox.warning(self, "警告", f"输入路径不存在：{input_path}")
            return
            
        if not output_path:
            QMessageBox.warning(self, "警告", "请指定输出路径！")
            return
            
        mode_idx = self.combo_mode.currentIndex()
        is_build = mode_idx == 1
        
        # 构建模式验证
        orig_g00_path = None
        json_path = None
        
        if is_build:
            orig_g00_path = self.edit_orig_g00.text().strip()
            if not orig_g00_path:
                QMessageBox.warning(self, "警告", "构建模式需要指定原始 G00 文件或文件夹！")
                return
            if not os.path.exists(orig_g00_path):
                QMessageBox.warning(self, "警告", f"原始 G00 路径不存在：{orig_g00_path}")
                return
                
            if self.check_use_json.isChecked():
                json_path = self.edit_json.text().strip()
                # JSON 路径可以为空，程序会自动查找
        
        try:
            # 解析类型
            type_idx = self.combo_type.currentIndex()
            g00_type = None if type_idx == 0 else G00Type(type_idx - 1)
            
            # 解析模式
            op_mode = OperationMode.EXTRACT if mode_idx == 0 else OperationMode.BUILD
            
            # 解析预设
            preset_map = {0: "fast", 1: "normal", 2: "max", 3: "ultra_max", 4: "promax"}
            preset = preset_map.get(self.combo_preset.currentIndex(), "max")
            
            # 解析提取模式
            extract_modes = ["psd", "full", "bbox", "tiles", "both"]
            extract_mode = extract_modes[self.combo_extract_mode.currentIndex()]
            
            # Type 3 设置
            export_jpg = self.radio_jpg.isChecked()
            jpeg_quality = self.spin_jpeg_quality.value()
            
            # 并行处理设置
            enable_parallel = self.check_parallel.isChecked()
            max_workers = self.spin_workers.value()
            min_files_for_parallel = self.spin_min_files.value()
            
            # 创建处理器
            processor = G00Processor(
                input_path=input_path,
                output_path=output_path,
                operation_mode=op_mode,
                g00_type=g00_type,
                preset=preset,
                recursive=self.check_recursive.isChecked(),
                extract_mode=extract_mode,
                orig_g00_path=orig_g00_path,
                json_path=json_path,
                jpeg_quality=jpeg_quality,
                export_jpg=export_jpg,
                create_subfolders=(op_mode == OperationMode.EXTRACT and extract_mode != "psd"),
                auto_find_json=self.check_use_json.isChecked() and not json_path,
                export_psd=False,
                rebuild=self.check_rebuild.isChecked(),
                # 并行处理参数
                enable_parallel=enable_parallel,
                max_workers=max_workers,
                min_files_for_parallel=min_files_for_parallel
            )
            
            self.worker = WorkerThread()
            self.worker.configure(processor)
            
            self.worker.progress_file.connect(self._on_file_progress)
            self.worker.progress_batch.connect(self._on_batch_progress)
            self.worker.log_message.connect(self._append_log)
            self.worker.current_file.connect(self._on_current_file)
            self.worker.finished_signal.connect(self._on_finished)
            
            self._set_processing_state(True)
            
            self.progress_file.setValue(0)
            self.progress_batch.setValue(0)
            self.progress_batch.setMaximum(100)
            self.lbl_current_file.setText("正在启动...")
            
            self.start_time = time.time()
            self.timer.start(100)
            
            self._append_log(f"\n[INFO] 开始处理: {input_path}")
            self._append_log(f"[INFO] 输出路径: {output_path}")
            self._append_log(f"[INFO] 模式: {'提取' if op_mode == OperationMode.EXTRACT else '构建'}")
            self._append_log(f"[INFO] 压缩预设: {preset}")
            if g00_type:
                self._append_log(f"[INFO] 指定类型: {get_type_description(g00_type)}")
            else:
                self._append_log("[INFO] 类型: 自动检测")
            if enable_parallel:
                workers_str = "自动" if max_workers == 0 else str(max_workers)
                self._append_log(f"[INFO] 并行处理: 启用, 线程数: {workers_str}, 最小文件数: {min_files_for_parallel}")
            else:
                self._append_log("[INFO] 并行处理: 禁用")
            
            # 记录 PSD 导出设置
            if op_mode == OperationMode.EXTRACT and extract_mode == "psd":
                self._append_log("[INFO] 提取模式: 仅官方 PSD")
            
            # 应用优化配置
            self._apply_optimizer_config()
            
            # 记录优化状态
            if OPTIMIZER_AVAILABLE:
                opt_status = []
                if self.check_numba.isChecked() and NUMBA_AVAILABLE:
                    opt_status.append("Numba JIT")
                if self.check_memory_pool.isChecked():
                    opt_status.append("内存池")
                if self.check_xxhash.isChecked() and XXHASH_AVAILABLE:
                    opt_status.append("xxhash")
                if self.check_gpu.isChecked() and CUDA_AVAILABLE:
                    opt_status.append("GPU")
                
                if opt_status:
                    self._append_log(f"[INFO] 优化启用: {', '.join(opt_status)}")
                else:
                    self._append_log("[INFO] 优化: 未启用")
            
            self._append_log("-" * 60)
            
            self.worker.start()
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动处理失败：{str(e)}")
            traceback.print_exc()
            
    def _toggle_pause(self):
        if self.worker:
            if self.worker.is_paused():
                self.worker.resume()
                self.btn_pause.setText("⏸️ 暂停")
                self._append_log("[INFO] 已恢复处理")
            else:
                self.worker.pause()
                self.btn_pause.setText("▶️ 继续")
                self._append_log("[INFO] 已暂停处理")
                
    def _stop_processing(self):
        if self.worker:
            self.worker.stop()
            self._append_log("[WARNING] 用户请求停止处理...")
            
    def _set_processing_state(self, processing: bool):
        self.btn_start.setEnabled(not processing)
        self.btn_pause.setEnabled(processing)
        self.btn_stop.setEnabled(processing)
        
        self.edit_input.setEnabled(not processing)
        self.edit_output.setEnabled(not processing)
        self.edit_orig_g00.setEnabled(not processing)
        self.edit_json.setEnabled(not processing and self.check_use_json.isChecked())
        self.combo_mode.setEnabled(not processing)
        self.combo_type.setEnabled(not processing)
        self.combo_preset.setEnabled(not processing)
        self.combo_extract_mode.setEnabled(not processing)
        self.check_recursive.setEnabled(not processing)
        self.check_use_json.setEnabled(not processing)
        self.check_export_psd.setEnabled(not processing)
        # 并行处理设置
        self.check_parallel.setEnabled(not processing)
        self.spin_workers.setEnabled(not processing and self.check_parallel.isChecked())
        self.spin_min_files.setEnabled(not processing and self.check_parallel.isChecked())
        
    def _on_file_progress(self, current: int, total: int):
        """GUI进度更新 - 优化：减少频繁更新造成的界面卡顿"""
        if total > 0:
            pct = int(current * 100 / total)
            # 只在百分比变化时才更新，避免过度刷新
            if pct != self.progress_file.value():
                self.progress_file.setValue(pct)
            
    def _on_batch_progress(self, current: int, total: int, success: int, fail: int, elapsed: float):
        """GUI批量进度更新 - 优化：减少频繁更新造成的界面卡顿"""
        # 只在当前值变化时才更新
        if self.progress_batch.value() != current or self.progress_batch.maximum() != total:
            self.progress_batch.setMaximum(total)
            self.progress_batch.setValue(current)
        # 更新统计标签
        self.lbl_stats.setText(f"总文件: {total} | 成功: {success} | 失败: {fail}")
        
    def _on_current_file(self, filename: str):
        self.lbl_current_file.setText(f"正在处理: {filename}")
        
    def _update_elapsed_time(self):
        elapsed = time.time() - self.start_time
        self.lbl_elapsed.setText(f"已用时间: {elapsed:.1f}s")
        
    def _on_finished(self, success: bool, message: str):
        self.timer.stop()
        self._set_processing_state(False)
        self.btn_pause.setText("⏸️ 暂停")
        
        elapsed = time.time() - self.start_time
        self.lbl_elapsed.setText(f"已用时间: {elapsed:.1f}s")
        
        if success:
            self._append_log("-" * 60)
            self._append_log(f"[OK] 处理完成！总耗时: {elapsed:.2f}s")
            self._append_log(message)
            self.progress_file.setValue(100)
            QMessageBox.information(self, "完成", f"处理完成！\n{message}")
        else:
            self._append_log("-" * 60)
            self._append_log(f"[ERROR] 处理失败: {message}")
            QMessageBox.warning(self, "失败", f"处理失败：\n{message}")
            
    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "确认退出",
                "处理正在进行中，确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.worker.stop()
                self.worker.wait(3000)
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
    
    # ===================== 优化配置操作 =====================
    def _apply_optimizer_config(self):
        """应用当前优化配置"""
        if not OPTIMIZER_AVAILABLE:
            return
        
        # 获取当前压缩预设
        preset_map = {0: "fast", 1: "normal", 2: "max", 3: "ultra_max", 4: "promax"}
        current_preset = preset_map.get(self.combo_preset.currentIndex(), "max")
        
        config = OptimizerConfig(
            enable_numba=self.check_numba.isChecked(),
            enable_memory_pool=self.check_memory_pool.isChecked(),
            enable_xxhash=self.check_xxhash.isChecked(),
            compression_preset=current_preset
        )
        set_optimizer_config(config)


# ===================== 主函数 =====================
def main():
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
        
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
