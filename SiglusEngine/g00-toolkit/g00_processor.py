#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
G00 Processor - G00 文件处理器统一封装

该模块整合了所有 G00 类型（Type 0/1/2/3）的处理逻辑，
提供统一的接口供 GUI 和命令行使用。

功能：
  - 自动检测 G00 类型
  - 统一的提取/构建接口
  - 批量处理支持（支持并行处理）
  - 进度回调支持
  - 自动创建同名子文件夹
  - 自动查找 JSON 配置文件
  - 可配置的并行处理（多线程）
  - 性能优化支持（Numba JIT、GPU 加速）
"""

import os
import struct
import time
import tempfile
from enum import Enum, IntEnum
from dataclasses import dataclass
from typing import Optional, Callable, Tuple, List, Any

# 导入并行处理模块
try:
    from config import ParallelConfig, OptimizationConfig, apply_optimization_config
    from parallel_processor import (
        parallel_process_files, TaskController, UserStopException,
        ProgressAggregator, LogAggregator
    )
    PARALLEL_AVAILABLE = True
except ImportError:
    PARALLEL_AVAILABLE = False
    ParallelConfig = None
    OptimizationConfig = None
    apply_optimization_config = None

# 导入优化器模块
try:
    from g00_optimizer import (
        OptimizedLZSS, OptimizedMatcher, get_optimizer_config,
        set_optimizer_config, OptimizerConfig, NUMBA_AVAILABLE, XXHASH_AVAILABLE
    )
    OPTIMIZER_AVAILABLE = True
except ImportError:
    OPTIMIZER_AVAILABLE = False
    NUMBA_AVAILABLE = False
    XXHASH_AVAILABLE = False

# 导入 GPU 加速模块
try:
    from g00_gpu_accelerator import (
        GPUAccelerator, CUDA_AVAILABLE, GPU_COUNT
    )
    GPU_ACCELERATOR_AVAILABLE = True
except ImportError:
    GPU_ACCELERATOR_AVAILABLE = False
    CUDA_AVAILABLE = False
    GPU_COUNT = 0


# ===================== 枚举定义 =====================
class G00Type(IntEnum):
    """G00 类型枚举"""
    TYPE_0 = 0  # BGR24 格式
    TYPE_1 = 1  # 索引色格式
    TYPE_2 = 2  # 分帧格式
    TYPE_3 = 3  # 加密 JPEG 格式


class OperationMode(Enum):
    """操作模式"""
    EXTRACT = "extract"  # 提取 (G00 → PNG)
    BUILD = "build"      # 构建 (PNG → G00)


# ===================== 回调类型定义 =====================
FileProgressCallback = Callable[[int, int], None]
# (processed, total, success, fail, elapsed)
BatchProgressCallback = Callable[[int, int, int, int, float], None]
LogCallback = Callable[[str], None]
CurrentFileCallback = Callable[[str], None]
CheckStopCallback = Callable[[], bool]
CheckPauseCallback = Callable[[], None]


# ===================== 类型检测 =====================
def detect_g00_type(file_path: str) -> Optional[G00Type]:
    """
    检测 G00 文件类型
    
    通过读取文件头的第一个字节来判断类型。
    """
    try:
        with open(file_path, 'rb') as f:
            header = f.read(5)
            if len(header) < 1:
                return None
                
            type_byte = header[0]
            
            if type_byte == 0:
                return G00Type.TYPE_0
            elif type_byte == 1:
                return G00Type.TYPE_1
            elif type_byte == 2:
                return G00Type.TYPE_2
            elif type_byte == 3:
                return G00Type.TYPE_3
            else:
                return None
                
    except (IOError, OSError):
        return None


def get_type_description(g00_type: G00Type) -> str:
    """获取类型描述"""
    descriptions = {
        G00Type.TYPE_0: "Type 0 (BGR24 格式)",
        G00Type.TYPE_1: "Type 1 (索引色格式)",
        G00Type.TYPE_2: "Type 2 (分帧格式)",
        G00Type.TYPE_3: "Type 3 (加密 JPEG 格式)",
    }
    return descriptions.get(g00_type, "未知类型")


# ===================== 文件查找工具 =====================
def find_files(path: str, extensions: Tuple[str, ...], recursive: bool = False) -> List[str]:
    """查找指定扩展名的文件"""
    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower()
        if ext in [e.lower() for e in extensions]:
            return [path]
        return []
        
    if not os.path.isdir(path):
        return []
        
    results = []
    
    if recursive:
        for root, dirs, files in os.walk(path):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext in [e.lower() for e in extensions]:
                    results.append(os.path.join(root, name))
    else:
        try:
            for entry in os.scandir(path):
                if entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in [e.lower() for e in extensions]:
                        results.append(entry.path)
        except (PermissionError, OSError):
            pass
            
    return sorted(results)


def get_base_name(file_path: str) -> str:
    """获取文件基础名（不含扩展名）"""
    return os.path.splitext(os.path.basename(file_path))[0]


def find_json_in_folder(folder: str, base_name: str) -> Optional[str]:
    """在文件夹中查找与指定名称匹配的 JSON 文件"""
    # 尝试多种可能的 JSON 文件名
    candidates = [
        os.path.join(folder, f"{base_name}.json"),
        os.path.join(folder, f"{base_name}.JSON"),
    ]
    
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
            
    # 如果没有找到，尝试查找文件夹中的任何 JSON 文件
    try:
        for entry in os.scandir(folder):
            if entry.is_file() and entry.name.lower().endswith('.json'):
                return entry.path
    except (PermissionError, OSError):
        pass
        
    return None


# ===================== 处理器类 =====================
class G00Processor:
    """G00 文件处理器"""
    
    def __init__(
        self,
        input_path: str,
        output_path: str,
        operation_mode: OperationMode,
        g00_type: Optional[G00Type] = None,
        preset: str = "max",
        recursive: bool = False,
        extract_mode: str = "full",
        orig_g00_path: Optional[str] = None,
        json_path: Optional[str] = None,
        jpeg_quality: int = 95,
        export_jpg: bool = False,
        create_subfolders: bool = True,
        auto_find_json: bool = True,
        export_psd: bool = True,
        rebuild: bool = False,
        # 并行处理参数
        enable_parallel: bool = True,
        max_workers: int = 0,
        min_files_for_parallel: int = 4
    ):
        """
        初始化处理器
        
        Args:
            input_path: 输入文件或目录路径
            output_path: 输出目录路径
            operation_mode: 操作模式（提取或构建）
            g00_type: G00 类型（None 表示自动检测）
            preset: 压缩预设 (fast/normal/max)
            recursive: 是否递归处理子目录
            extract_mode: 提取模式 (full/bbox/tiles/both) - 仅 Type2
            orig_g00_path: 原始 G00 文件或文件夹路径（构建时需要）
            json_path: JSON 配置文件路径（构建时可选）
            jpeg_quality: JPEG 质量（Type3 构建时使用）
            export_jpg: 是否同时导出 JPG（Type3 提取时使用）
            create_subfolders: 是否在输出目录下创建同名子文件夹
            auto_find_json: 是否自动查找 JSON 配置文件
            export_psd: 是否生成 PSD 文件（所有类型均支持）
            rebuild: 是否完全重建 G00 结构（仅 Type 2 构建）
            enable_parallel: 是否启用并行处理
            max_workers: 最大工作线程数（0=自动检测）
            min_files_for_parallel: 启用并行的最小文件数
        """
        self.input_path = input_path
        self.output_path = output_path
        self.operation_mode = operation_mode
        self.g00_type = g00_type
        self.preset = preset
        self.recursive = recursive
        self.extract_mode = extract_mode
        self.orig_g00_path = orig_g00_path
        self.json_path = json_path
        self.jpeg_quality = jpeg_quality
        self.export_jpg = export_jpg
        self.create_subfolders = create_subfolders
        self.auto_find_json = auto_find_json
        self.export_psd = export_psd
        self.rebuild = rebuild
        
        # 并行处理配置
        self.enable_parallel = enable_parallel and PARALLEL_AVAILABLE
        self.max_workers = max_workers
        self.min_files_for_parallel = min_files_for_parallel
        
        # 创建并行配置对象
        if PARALLEL_AVAILABLE:
            self.parallel_config = ParallelConfig(
                max_workers=max_workers,
                enable_parallel=enable_parallel,
                min_files_for_parallel=min_files_for_parallel
            )
        else:
            self.parallel_config = None
        
        # 优化器配置
        self._init_optimizer()
        
        # 回调函数
        self._file_progress_cb: Optional[FileProgressCallback] = None
        self._batch_progress_cb: Optional[BatchProgressCallback] = None
        self._log_cb: Optional[LogCallback] = None
        self._current_file_cb: Optional[CurrentFileCallback] = None
        self._check_stop_cb: Optional[CheckStopCallback] = None
        self._check_pause_cb: Optional[CheckPauseCallback] = None
        
        # 统计信息
        self.total_files = 0
        self.processed_files = 0
        self.succeeded_files: List[str] = []
        self.failed_files: List[str] = []
        
        # 任务控制器（用于并行处理）
        self._task_controller: Optional[Any] = None
    
    def _init_optimizer(self):
        """初始化优化器配置"""
        if OPTIMIZER_AVAILABLE:
            try:
                # 根据 preset 设置优化器配置
                opt_config = OptimizerConfig(
                    enable_numba=True,
                    enable_memory_pool=True,
                    enable_xxhash=True,
                    compression_preset=self.preset
                )
                set_optimizer_config(opt_config)
            except Exception:
                pass
        
    def set_callbacks(
        self,
        file_progress_cb: Optional[FileProgressCallback] = None,
        batch_progress_cb: Optional[BatchProgressCallback] = None,
        log_cb: Optional[LogCallback] = None,
        current_file_cb: Optional[CurrentFileCallback] = None,
        check_stop_cb: Optional[CheckStopCallback] = None,
        check_pause_cb: Optional[CheckPauseCallback] = None
    ):
        """设置回调函数"""
        self._file_progress_cb = file_progress_cb
        self._batch_progress_cb = batch_progress_cb
        self._log_cb = log_cb
        self._current_file_cb = current_file_cb
        self._check_stop_cb = check_stop_cb
        self._check_pause_cb = check_pause_cb
        
    def _log(self, message: str):
        if self._log_cb:
            self._log_cb(message)
            
    def _update_file_progress(self, current: int, total: int):
        if self._file_progress_cb:
            self._file_progress_cb(current, total)
            
    def _update_batch_progress(self, current: int, total: int, elapsed: float):
        if self._batch_progress_cb:
            success = len(self.succeeded_files)
            fail = len(self.failed_files)
            self._batch_progress_cb(current, total, success, fail, elapsed)
            
    def _set_current_file(self, filename: str):
        if self._current_file_cb:
            self._current_file_cb(filename)
            
    def _check_stop(self) -> bool:
        if self._check_stop_cb:
            return self._check_stop_cb()
        return False
        
    def _check_pause(self):
        if self._check_pause_cb:
            self._check_pause_cb()
            
    def process(self) -> Tuple[bool, str]:
        """执行处理"""
        start_time = time.time()
        
        try:
            if self.operation_mode == OperationMode.EXTRACT:
                return self._process_extract(start_time)
            else:
                return self._process_build(start_time)
                
        except Exception as e:
            import traceback
            return False, f"处理过程中发生错误: {str(e)}\n{traceback.format_exc()}"
            
    def _process_extract(self, start_time: float) -> Tuple[bool, str]:
        """处理提取操作（支持并行处理）"""
        files = find_files(self.input_path, ('.g00', '.G00'), self.recursive)
        
        if not files:
            return False, f"未找到 G00 文件: {self.input_path}"
            
        self.total_files = len(files)
        self.processed_files = 0
        self.succeeded_files = []
        self.failed_files = []
        
        self._log(f"[INFO] 找到 {self.total_files} 个 G00 文件")
        self._update_batch_progress(0, self.total_files, 0)
        
        # 确保输出目录存在
        os.makedirs(self.output_path, exist_ok=True)
        
        # 判断是否使用并行处理
        use_parallel = (
            self.enable_parallel and 
            PARALLEL_AVAILABLE and 
            self.total_files >= self.min_files_for_parallel
        )
        
        if use_parallel:
            return self._process_extract_parallel(files, start_time)
        else:
            return self._process_extract_serial(files, start_time)
    
    def _process_extract_serial(self, files: List[str], start_time: float) -> Tuple[bool, str]:
        """串行提取处理"""
        for i, file_path in enumerate(files):
            if self._check_stop():
                self._log("[WARNING] 用户停止处理")
                break
                
            self._check_pause()
            
            filename = os.path.basename(file_path)
            base_name = get_base_name(file_path)
            self._set_current_file(filename)
            self._log(f"[INFO] 正在处理 ({i+1}/{self.total_files}): {filename}")
            
            try:
                # 检测类型
                file_type = self.g00_type
                if file_type is None:
                    file_type = detect_g00_type(file_path)
                    if file_type is None:
                        raise ValueError(f"无法检测文件类型: {file_path}")
                    self._log(f"[INFO] 检测到类型: {get_type_description(file_type)}")
                    
                # 确定输出目录
                if self.extract_mode == "psd":
                    out_dir = self.output_path
                elif self.create_subfolders:
                    out_dir = os.path.join(self.output_path, base_name)
                else:
                    out_dir = self.output_path
                    
                os.makedirs(out_dir, exist_ok=True)
                    
                # 执行提取
                self._extract_single(file_path, out_dir, file_type)
                
                self.succeeded_files.append(file_path)
                self._log(f"[OK] 提取完成: {filename} -> {out_dir}")
                
            except Exception as e:
                self.failed_files.append(file_path)
                self._log(f"[ERROR] 处理失败 {filename}: {str(e)}")
                
            self.processed_files = i + 1
            elapsed = time.time() - start_time
            self._update_batch_progress(self.processed_files, self.total_files, elapsed)
            
        elapsed = time.time() - start_time
        success_count = len(self.succeeded_files)
        fail_count = len(self.failed_files)
        
        message = f"处理完成: {success_count} 成功, {fail_count} 失败, 耗时 {elapsed:.2f}s"
        
        return fail_count == 0, message
    
    def _process_extract_parallel(self, files: List[str], start_time: float) -> Tuple[bool, str]:
        """并行提取处理"""
        # 创建任务控制器
        self._task_controller = TaskController()
        
        # 保存原始回调
        original_file_cb = self._file_progress_cb
        
        # 包装单文件处理函数 - 接受 file_progress_cb 参数
        def process_single_file(file_path: str, file_progress_cb=None) -> str:
            filename = os.path.basename(file_path)
            base_name = get_base_name(file_path)
            
            # 检测类型
            file_type = self.g00_type
            if file_type is None:
                file_type = detect_g00_type(file_path)
                if file_type is None:
                    raise ValueError(f"无法检测文件类型: {file_path}")
            
            # 确定输出目录
            if self.extract_mode == "psd":
                out_dir = self.output_path
            elif self.create_subfolders:
                out_dir = os.path.join(self.output_path, base_name)
            else:
                out_dir = self.output_path
                
            os.makedirs(out_dir, exist_ok=True)
            
            # 使用传入的回调或原始回调
            cb = file_progress_cb if file_progress_cb else original_file_cb
            
            # 执行提取（传递进度回调）
            self._extract_single_with_cb(file_path, out_dir, file_type, cb)
            
            return filename
        
        # 执行并行处理
        results, errors = parallel_process_files(
            files=files,
            process_func=process_single_file,
            config=self.parallel_config,
            file_progress_cb=self._file_progress_cb,
            batch_progress_cb=self._batch_progress_cb,
            log_cb=self._log_cb,
            current_file_cb=self._current_file_cb,
            task_controller=self._task_controller
        )
        
        # 统计结果
        self.succeeded_files = [r for r in results if r is not None]
        self.failed_files = [e[0] for e in errors]
        
        elapsed = time.time() - start_time
        success_count = len(self.succeeded_files)
        fail_count = len(self.failed_files)
        
        # 记录失败详情
        for file_path, error in errors:
            self._log(f"[ERROR] 处理失败 {os.path.basename(file_path)}: {error}")
        
        message = f"处理完成: {success_count} 成功, {fail_count} 失败, 耗时 {elapsed:.2f}s (并行模式)"
        
        return fail_count == 0, message
        
    def _process_build(self, start_time: float) -> Tuple[bool, str]:
        """处理构建操作"""
        if not self.orig_g00_path:
            return False, "构建模式需要指定原始 G00 文件或文件夹"
            
        if not os.path.exists(self.orig_g00_path):
            return False, f"原始 G00 路径不存在: {self.orig_g00_path}"
            
        # 判断是单文件还是批量
        if os.path.isfile(self.orig_g00_path):
            return self._build_single_file(start_time)
        else:
            return self._build_batch(start_time)
            
    def _build_single_file(self, start_time: float) -> Tuple[bool, str]:
        """构建单个文件"""
        orig_g00 = self.orig_g00_path
        base_name = get_base_name(orig_g00)
        
        # 检测类型
        file_type = self.g00_type
        if file_type is None:
            file_type = detect_g00_type(orig_g00)
            if file_type is None:
                return False, f"无法检测文件类型: {orig_g00}"
            self._log(f"[INFO] 检测到类型: {get_type_description(file_type)}")
            
        # 确定 JSON 路径
        json_path = self.json_path
        if not json_path and self.auto_find_json:
            # 尝试在输入目录中查找
            if os.path.isdir(self.input_path):
                # 首先尝试在同名子文件夹中查找
                subfolder = os.path.join(self.input_path, base_name)
                if os.path.isdir(subfolder):
                    json_path = find_json_in_folder(subfolder, base_name)
                    if json_path:
                        self._log(f"[INFO] 在子文件夹中找到 JSON: {json_path}")
                        
                # 如果没找到，尝试在输入目录根目录查找
                if not json_path:
                    json_path = find_json_in_folder(self.input_path, base_name)
                    if json_path:
                        self._log(f"[INFO] 在输入目录中找到 JSON: {json_path}")
                        
        if not json_path:
            return False, f"未找到 JSON 配置文件，请手动指定或确保存在 {base_name}.json"
            
        if not os.path.exists(json_path):
            return False, f"JSON 配置文件不存在: {json_path}"
            
        # 确定 PNG 目录
        if os.path.isdir(self.input_path):
            # 优先使用同名子文件夹
            subfolder = os.path.join(self.input_path, base_name)
            if os.path.isdir(subfolder):
                png_dir = subfolder
            else:
                png_dir = self.input_path
        else:
            png_dir = os.path.dirname(self.input_path) or "."
            
        # 确定输出路径
        if os.path.isdir(self.output_path):
            output_g00 = os.path.join(self.output_path, os.path.basename(orig_g00))
        else:
            output_g00 = self.output_path
            
        os.makedirs(os.path.dirname(output_g00) or ".", exist_ok=True)
        
        self.total_files = 1
        self.processed_files = 0
        self._update_batch_progress(0, 1, 0)
        
        filename = os.path.basename(orig_g00)
        self._set_current_file(filename)
        self._log(f"[INFO] 正在构建: {filename}")
        self._log(f"[INFO] PNG 目录: {png_dir}")
        self._log(f"[INFO] JSON 配置: {json_path}")
        
        try:
            self._build_single(orig_g00, json_path, png_dir, output_g00, file_type)
            
            elapsed = time.time() - start_time
            self._update_batch_progress(1, 1, elapsed)
            self._log(f"[OK] 构建完成: {output_g00}")
            
            return True, f"构建完成，耗时 {elapsed:.2f}s"
            
        except Exception as e:
            return False, f"构建失败: {str(e)}"
            
    def _build_batch(self, start_time: float) -> Tuple[bool, str]:
        """批量构建（支持并行处理）"""
        # 查找原始 G00 文件夹中的所有 G00 文件
        orig_files = find_files(self.orig_g00_path, ('.g00', '.G00'), self.recursive)
        
        if not orig_files:
            return False, f"在原始 G00 文件夹中未找到 G00 文件: {self.orig_g00_path}"
            
        self.total_files = len(orig_files)
        self.processed_files = 0
        self.succeeded_files = []
        self.failed_files = []
        
        self._log(f"[INFO] 找到 {self.total_files} 个原始 G00 文件")
        self._update_batch_progress(0, self.total_files, 0)
        
        os.makedirs(self.output_path, exist_ok=True)
        
        # 判断是否使用并行处理
        use_parallel = (
            self.enable_parallel and 
            PARALLEL_AVAILABLE and 
            self.total_files >= self.min_files_for_parallel
        )
        
        if use_parallel:
            return self._build_batch_parallel(orig_files, start_time)
        else:
            return self._build_batch_serial(orig_files, start_time)
    
    def _build_batch_serial(self, orig_files: List[str], start_time: float) -> Tuple[bool, str]:
        """串行批量构建"""
        for i, orig_g00 in enumerate(orig_files):
            if self._check_stop():
                self._log("[WARNING] 用户停止处理")
                break
                
            self._check_pause()
            
            filename = os.path.basename(orig_g00)
            base_name = get_base_name(orig_g00)
            self._set_current_file(filename)
            self._log(f"[INFO] 正在处理 ({i+1}/{self.total_files}): {filename}")
            
            try:
                # 检测类型
                file_type = self.g00_type
                if file_type is None:
                    file_type = detect_g00_type(orig_g00)
                    if file_type is None:
                        raise ValueError(f"无法检测文件类型: {orig_g00}")
                    self._log(f"[INFO] 检测到类型: {get_type_description(file_type)}")
                    
                    # 在输入目录中查找同名子文件夹或直接查找 JSON
                subfolder = os.path.join(self.input_path, base_name)
                json_path = None
                png_dir = None
                
                if os.path.isdir(subfolder):
                    found_json = find_json_in_folder(subfolder, base_name)
                    if found_json:
                        json_path = found_json
                        png_dir = subfolder
                
                if not json_path:
                    # 尝试在输入根目录下查找 base_name.json
                    potential_json = os.path.join(self.input_path, f"{base_name}.json")
                    if os.path.isfile(potential_json):
                        json_path = potential_json
                        png_dir = self.input_path
                
                if not json_path:
                    msg = f"未找到资源: 既无同名子文件夹 {os.path.basename(subfolder)}，也无 {base_name}.json"
                    raise FileNotFoundError(msg)
                    
                self._log(f"[INFO] 找到 JSON: {os.path.basename(json_path)}")
                
                # 构建输出路径
                output_g00 = os.path.join(self.output_path, filename)
                
                # 执行构建
                self._build_single(orig_g00, json_path, png_dir, output_g00, file_type)
                
                self.succeeded_files.append(orig_g00)
                self._log(f"[OK] 构建完成: {filename}")
                
            except Exception as e:
                self.failed_files.append(orig_g00)
                self._log(f"[ERROR] 处理失败 {filename}: {str(e)}")
                
            self.processed_files = i + 1
            elapsed = time.time() - start_time
            self._update_batch_progress(self.processed_files, self.total_files, elapsed)
            
        elapsed = time.time() - start_time
        success_count = len(self.succeeded_files)
        fail_count = len(self.failed_files)
        
        message = f"处理完成: {success_count} 成功, {fail_count} 失败, 耗时 {elapsed:.2f}s"
        
        return fail_count == 0, message
    
    def _build_batch_parallel(self, orig_files: List[str], start_time: float) -> Tuple[bool, str]:
        """并行批量构建"""
        # 创建任务控制器
        self._task_controller = TaskController()
        
        # 保存原始回调
        original_file_cb = self._file_progress_cb
        
        # 包装单文件处理函数 - 接受 file_progress_cb 参数
        def process_single_build(orig_g00: str, file_progress_cb=None) -> str:
            filename = os.path.basename(orig_g00)
            base_name = get_base_name(orig_g00)
            
            # 检测类型
            file_type = self.g00_type
            if file_type is None:
                file_type = detect_g00_type(orig_g00)
                if file_type is None:
                    raise ValueError(f"无法检测文件类型: {orig_g00}")
            
            # 在输入目录中查找同名子文件夹或直接查找 JSON
            subfolder = os.path.join(self.input_path, base_name)
            json_path = None
            png_dir = None
            
            if os.path.isdir(subfolder):
                found_json = find_json_in_folder(subfolder, base_name)
                if found_json:
                    json_path = found_json
                    png_dir = subfolder
            
            if not json_path:
                # 尝试在输入根目录下查找 base_name.json
                potential_json = os.path.join(self.input_path, f"{base_name}.json")
                if os.path.isfile(potential_json):
                    json_path = potential_json
                    png_dir = self.input_path
            
            if not json_path:
                msg = f"未找到资源: 既无同名子文件夹 {os.path.basename(subfolder)}，也无 {base_name}.json"
                raise FileNotFoundError(msg)
            
            # 构建输出路径
            output_g00 = os.path.join(self.output_path, filename)
            
            # 使用传入的回调或原始回调
            cb = file_progress_cb if file_progress_cb else original_file_cb
            
            # 执行构建（传递进度回调）
            self._build_single_with_cb(orig_g00, json_path, png_dir, output_g00, file_type, cb)
            
            return filename
        
        # 执行并行处理
        results, errors = parallel_process_files(
            files=orig_files,
            process_func=process_single_build,
            config=self.parallel_config,
            file_progress_cb=self._file_progress_cb,
            batch_progress_cb=self._batch_progress_cb,
            log_cb=self._log_cb,
            current_file_cb=self._current_file_cb,
            task_controller=self._task_controller
        )
        
        # 统计结果
        self.succeeded_files = [r for r in results if r is not None]
        self.failed_files = [e[0] for e in errors]
        
        elapsed = time.time() - start_time
        success_count = len(self.succeeded_files)
        fail_count = len(self.failed_files)
        
        # 记录失败详情
        for file_path, error in errors:
            self._log(f"[ERROR] 处理失败 {os.path.basename(file_path)}: {error}")
        
        message = f"处理完成: {success_count} 成功, {fail_count} 失败, 耗时 {elapsed:.2f}s (并行模式)"
        
        return fail_count == 0, message
        
    def _extract_single(self, g00_path: str, out_dir: str, g00_type: G00Type):
        """提取单个 G00 文件"""
        self._extract_single_with_cb(g00_path, out_dir, g00_type, self._file_progress_cb)
    
    def _extract_single_with_cb(self, g00_path: str, out_dir: str, g00_type: G00Type, 
                                 file_progress_cb):
        """提取单个 G00 文件（带进度回调）"""
        if g00_type == G00Type.TYPE_0:
            self._extract_type0_with_cb(g00_path, out_dir, file_progress_cb)
        elif g00_type == G00Type.TYPE_1:
            self._extract_type1_with_cb(g00_path, out_dir, file_progress_cb)
        elif g00_type == G00Type.TYPE_2:
            self._extract_type2_with_cb(g00_path, out_dir, file_progress_cb)
        elif g00_type == G00Type.TYPE_3:
            self._extract_type3_with_cb(g00_path, out_dir, file_progress_cb)
        else:
            raise ValueError(f"不支持的类型: {g00_type}")
            
    def _build_single(self, orig_g00: str, json_path: str, png_dir: str, 
                      output_g00: str, g00_type: G00Type):
        """构建单个 G00 文件"""
        self._build_single_with_cb(orig_g00, json_path, png_dir, output_g00, g00_type, 
                                   self._file_progress_cb)
    
    def _build_single_with_cb(self, orig_g00: str, json_path: str, png_dir: str, 
                               output_g00: str, g00_type: G00Type, file_progress_cb):
        """构建单个 G00 文件（带进度回调）"""
        if g00_type == G00Type.TYPE_0:
            self._build_type0_with_cb(orig_g00, json_path, png_dir, output_g00, file_progress_cb)
        elif g00_type == G00Type.TYPE_1:
            self._build_type1_with_cb(orig_g00, json_path, png_dir, output_g00, file_progress_cb)
        elif g00_type == G00Type.TYPE_2:
            self._build_type2_with_cb(orig_g00, json_path, png_dir, output_g00, file_progress_cb)
        elif g00_type == G00Type.TYPE_3:
            self._build_type3_with_cb(orig_g00, json_path, png_dir, output_g00, file_progress_cb)
        else:
            raise ValueError(f"不支持的类型: {g00_type}")
            
    # ===================== Type 0 处理 =====================
    def _extract_type0(self, g00_path: str, out_dir: str):
        self._extract_type0_with_cb(g00_path, out_dir, self._file_progress_cb)
    
    def _extract_type0_with_cb(self, g00_path: str, out_dir: str, file_progress_cb):
        try:
            from g00_type0 import extract_cmd, export_psd_cmd
            import numpy as np
            from PIL import Image
            try:
                from psd_tools import PSDImage
                PSD_TOOLS_AVAILABLE = True
            except ImportError:
                PSD_TOOLS_AVAILABLE = False
            if self.extract_mode == "psd":
                base_name = os.path.splitext(os.path.basename(g00_path))[0]
                psd_path = os.path.join(out_dir, f"{base_name}.psd")
                if self._log_cb:
                    self._log_cb(f"[INFO] 生成 PSD 文件: {psd_path}")
                if not PSD_TOOLS_AVAILABLE:
                    raise RuntimeError("需要安装 psd-tools 才能导出 PSD")
                from g00_type0 import G00Type0
                g0 = G00Type0.read(g00_path, show_progress=False, gui_callback=file_progress_cb)
                arr_bgr = np.frombuffer(g0.decompressed, dtype=np.uint8).reshape((g0.height, g0.width, 3))
                rgba = np.empty((g0.height, g0.width, 4), dtype=np.uint8)
                rgba[:, :, 0] = arr_bgr[:, :, 2]
                rgba[:, :, 1] = arr_bgr[:, :, 1]
                rgba[:, :, 2] = arr_bgr[:, :, 0]
                rgba[:, :, 3] = 255
                psd = PSDImage.new(mode='RGBA', size=(g0.width, g0.height), color=(0, 0, 0, 0))
                layer_name = f"{base_name}#000"
                pil_image = Image.fromarray(rgba)
                layer = psd.create_pixel_layer(pil_image, name=layer_name)
                psd.append(layer)
                psd.save(psd_path)
                if self._log_cb:
                    self._log_cb(f"[OK] PSD 已保存")
            else:
                extract_cmd(g00_path, out_dir, show_progress=False, 
                           file_progress_cb=file_progress_cb)
                if self.export_psd:
                    base_name = os.path.splitext(os.path.basename(g00_path))[0]
                    json_path = os.path.join(out_dir, f"{base_name}.json")
                    psd_path = os.path.join(out_dir, f"{base_name}.psd")
                    
                    if self._log_cb:
                        self._log_cb(f"[INFO] 生成 PSD 文件: {psd_path}")
                    
                    export_psd_cmd(json_path, out_dir, psd_path, show_progress=False)
                    
                    if self._log_cb:
                        self._log_cb(f"[OK] PSD 已保存")
        except ImportError:
            raise RuntimeError("未找到 g00_type0.py 模块")
            
    def _build_type0(self, orig_g00: str, json_path: str, png_dir: str, output_g00: str):
        self._build_type0_with_cb(orig_g00, json_path, png_dir, output_g00, self._file_progress_cb)
    
    def _build_type0_with_cb(self, orig_g00: str, json_path: str, png_dir: str, 
                              output_g00: str, file_progress_cb):
        try:
            from g00_type0 import build_cmd
            build_cmd(orig_g00, json_path, png_dir, output_g00, 
                     preset=self.preset, show_progress=False,
                     file_progress_cb=file_progress_cb)
        except ImportError:
            raise RuntimeError("未找到 g00_type0.py 模块")
            
    # ===================== Type 1 处理 =====================
    def _extract_type1(self, g00_path: str, out_dir: str):
        self._extract_type1_with_cb(g00_path, out_dir, self._file_progress_cb)
    
    def _extract_type1_with_cb(self, g00_path: str, out_dir: str, file_progress_cb):
        try:
            from g00_type1 import extract_cmd, export_psd_cmd
            import numpy as np
            from PIL import Image
            try:
                from psd_tools import PSDImage
                PSD_TOOLS_AVAILABLE = True
            except ImportError:
                PSD_TOOLS_AVAILABLE = False
            if self.extract_mode == "psd":
                base_name = os.path.splitext(os.path.basename(g00_path))[0]
                psd_path = os.path.join(out_dir, f"{base_name}.psd")
                if self._log_cb:
                    self._log_cb(f"[INFO] 生成 PSD 文件: {psd_path}")
                if not PSD_TOOLS_AVAILABLE:
                    raise RuntimeError("需要安装 psd-tools 才能导出 PSD")
                from g00_type1 import G00Type1
                g1 = G00Type1.read(g00_path, show_progress=False, gui_callback=file_progress_cb)
                pal_bytes_len = g1.colors * 4
                idx_off = 2 + pal_bytes_len
                idx = g1.decompressed[idx_off: idx_off + (g1.stride * g1.height)]
                if len(idx) < g1.stride * g1.height:
                    raise ValueError("索引区不足（文件可能损坏）")
                idx_arr = np.frombuffer(idx, dtype=np.uint8).reshape((g1.height, g1.stride))
                idx_arr = idx_arr[:, :g1.width]
                rgba = g1.palette_rgba[idx_arr]
                psd = PSDImage.new(mode='RGBA', size=(g1.width, g1.height), color=(0, 0, 0, 0))
                layer_name = f"{base_name}#000"
                pil_image = Image.fromarray(rgba.astype(np.uint8))
                layer = psd.create_pixel_layer(pil_image, name=layer_name)
                psd.append(layer)
                psd.save(psd_path)
                if self._log_cb:
                    self._log_cb(f"[OK] PSD 已保存")
            else:
                extract_cmd(g00_path, out_dir, show_progress=False,
                           file_progress_cb=file_progress_cb)
                if self.export_psd:
                    base_name = os.path.splitext(os.path.basename(g00_path))[0]
                    json_path = os.path.join(out_dir, f"{base_name}.json")
                    psd_path = os.path.join(out_dir, f"{base_name}.psd")
                    
                    if self._log_cb:
                        self._log_cb(f"[INFO] 生成 PSD 文件: {psd_path}")
                    
                    export_psd_cmd(json_path, out_dir, psd_path, show_progress=False)
                    
                    if self._log_cb:
                        self._log_cb(f"[OK] PSD 已保存")
        except ImportError:
            raise RuntimeError("未找到 g00_type1.py 模块")
            
    def _build_type1(self, orig_g00: str, json_path: str, png_dir: str, output_g00: str):
        self._build_type1_with_cb(orig_g00, json_path, png_dir, output_g00, self._file_progress_cb)
    
    def _build_type1_with_cb(self, orig_g00: str, json_path: str, png_dir: str, 
                              output_g00: str, file_progress_cb):
        try:
            from g00_type1 import build_cmd
            build_cmd(orig_g00, json_path, png_dir, output_g00,
                     preset=self.preset, show_progress=False,
                     file_progress_cb=file_progress_cb)
        except ImportError:
            raise RuntimeError("未找到 g00_type1.py 模块")
            
    # ===================== Type 2 处理 =====================
    def _extract_type2(self, g00_path: str, out_dir: str):
        self._extract_type2_with_cb(g00_path, out_dir, self._file_progress_cb)
    
    def _extract_type2_with_cb(self, g00_path: str, out_dir: str, file_progress_cb):
        try:
            from g00_type2 import extract_cmd, export_g00pack_psd_cmd
            if self.extract_mode == "psd":
                base_name = os.path.splitext(os.path.basename(g00_path))[0]
                psd_path = os.path.join(out_dir, f"{base_name}.psd")
                if self._log_cb:
                    self._log_cb(f"[INFO] 生成 G00Pack 官方兼容 PSD: {psd_path}")
                export_g00pack_psd_cmd(g00_path, psd_path, show_progress=False, gui_callback=None)
                if self._log_cb:
                    self._log_cb(f"[OK] PSD 已保存")
            else:
                extract_cmd(g00_path, out_dir, mode=self.extract_mode, show_progress=False,
                           file_progress_cb=file_progress_cb)
                if self.export_psd:
                    base_name = os.path.splitext(os.path.basename(g00_path))[0]
                    psd_path = os.path.join(out_dir, f"{base_name}.psd")
                    if self._log_cb:
                        self._log_cb(f"[INFO] 生成 G00Pack 官方兼容 PSD: {psd_path}")
                    export_g00pack_psd_cmd(g00_path, psd_path, show_progress=False, gui_callback=None)
                    if self._log_cb:
                        self._log_cb(f"[OK] PSD 已保存")
                
        except ImportError:
            raise RuntimeError("未找到 g00_type2.py 模块")
            
    def _build_type2(self, orig_g00: str, json_path: str, png_dir: str, output_g00: str):
        self._build_type2_with_cb(orig_g00, json_path, png_dir, output_g00, self._file_progress_cb)
    
    def _build_type2_with_cb(self, orig_g00: str, json_path: str, png_dir: str, 
                              output_g00: str, file_progress_cb):
        try:
            from g00_type2 import build_cmd
            build_cmd(orig_g00, json_path, png_dir, output_g00,
                     mode_arg="auto", preset=self.preset, show_progress=False,
                     file_progress_cb=file_progress_cb, rebuild=self.rebuild)
        except ImportError:
            raise RuntimeError("未找到 g00_type2.py 模块")
            
    # ===================== Type 3 处理 =====================
    def _extract_type3(self, g00_path: str, out_dir: str):
        self._extract_type3_with_cb(g00_path, out_dir, self._file_progress_cb)
    
    def _extract_type3_with_cb(self, g00_path: str, out_dir: str, file_progress_cb):
        try:
            from g00_type3 import extract_cmd, export_psd_cmd
            from io import BytesIO
            from PIL import Image
            try:
                from psd_tools import PSDImage
                PSD_TOOLS_AVAILABLE = True
            except ImportError:
                PSD_TOOLS_AVAILABLE = False
            if self.extract_mode == "psd":
                base_name = os.path.splitext(os.path.basename(g00_path))[0]
                psd_path = os.path.join(out_dir, f"{base_name}.psd")
                if self._log_cb:
                    self._log_cb(f"[INFO] 生成 PSD 文件: {psd_path}")
                if not PSD_TOOLS_AVAILABLE:
                    raise RuntimeError("需要安装 psd-tools 才能导出 PSD")
                from g00_type3 import read_g00_type3
                g3 = read_g00_type3(g00_path)
                img_rgba = Image.open(BytesIO(g3["jpeg"])).convert("RGBA")
                canvas_w = g3["width"]
                canvas_h = g3["height"]
                psd = PSDImage.new(mode='RGBA', size=(canvas_w, canvas_h), color=(0, 0, 0, 0))
                layer_name = f"{base_name}#000"
                layer = psd.create_pixel_layer(img_rgba, name=layer_name)
                psd.append(layer)
                psd.save(psd_path)
                if self._log_cb:
                    self._log_cb(f"[OK] PSD 已保存")
            else:
                extract_cmd(g00_path, out_dir, export_jpg=self.export_jpg,
                           file_progress_cb=file_progress_cb)
                if self.export_psd:
                    base_name = os.path.splitext(os.path.basename(g00_path))[0]
                    json_path = os.path.join(out_dir, f"{base_name}.json")
                    psd_path = os.path.join(out_dir, f"{base_name}.psd")
                    
                    if self._log_cb:
                        self._log_cb(f"[INFO] 生成 PSD 文件: {psd_path}")
                    
                    export_psd_cmd(json_path, out_dir, psd_path, show_progress=False)
                    
                    if self._log_cb:
                        self._log_cb(f"[OK] PSD 已保存")
        except ImportError:
            raise RuntimeError("未找到 g00_type3.py 模块")
            
    def _build_type3(self, orig_g00: str, json_path: str, png_dir: str, output_g00: str):
        self._build_type3_with_cb(orig_g00, json_path, png_dir, output_g00, self._file_progress_cb)
    
    def _build_type3_with_cb(self, orig_g00: str, json_path: str, png_dir: str, 
                              output_g00: str, file_progress_cb):
        try:
            from g00_type3 import build_cmd
            build_cmd(orig_g00, json_path, png_dir, output_g00,
                     src="auto", jpeg_quality=self.jpeg_quality,
                     file_progress_cb=file_progress_cb)
        except ImportError:
            raise RuntimeError("未找到 g00_type3.py 模块")


# ===================== 便捷函数 =====================
def extract_g00(
    input_path: str,
    output_path: str,
    g00_type: Optional[G00Type] = None,
    recursive: bool = False,
    extract_mode: str = "full",
    create_subfolders: bool = True,
    export_psd: bool = True,
    log_cb: Optional[LogCallback] = None
) -> Tuple[bool, str]:
    """便捷的 G00 提取函数"""
    processor = G00Processor(
        input_path=input_path,
        output_path=output_path,
        operation_mode=OperationMode.EXTRACT,
        g00_type=g00_type,
        recursive=recursive,
        extract_mode=extract_mode,
        create_subfolders=create_subfolders,
        export_psd=export_psd
    )
    
    if log_cb:
        processor.set_callbacks(log_cb=log_cb)
        
    return processor.process()


def build_g00(
    orig_g00_path: str,
    input_path: str,
    output_path: str,
    json_path: Optional[str] = None,
    g00_type: Optional[G00Type] = None,
    preset: str = "max",
    auto_find_json: bool = True,
    log_cb: Optional[LogCallback] = None
) -> Tuple[bool, str]:
    """便捷的 G00 构建函数"""
    processor = G00Processor(
        input_path=input_path,
        output_path=output_path,
        operation_mode=OperationMode.BUILD,
        g00_type=g00_type,
        preset=preset,
        orig_g00_path=orig_g00_path,
        json_path=json_path,
        auto_find_json=auto_find_json
    )
    
    if log_cb:
        processor.set_callbacks(log_cb=log_cb)
        
    return processor.process()


# ===================== 命令行接口 =====================
def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="G00 处理工具 - 支持 Type 0/1/2/3 的提取和构建"
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # 提取命令
    extract_parser = subparsers.add_parser("extract", help="提取 G00 文件")
    extract_parser.add_argument("input", help="输入 G00 文件或目录")
    extract_parser.add_argument("output", help="输出目录")
    extract_parser.add_argument("--type", type=int, choices=[0, 1, 2, 3],
                                help="强制指定类型（默认自动检测）")
    extract_parser.add_argument("--recursive", "-r", action="store_true",
                                help="递归处理子目录")
    extract_parser.add_argument("--mode", choices=["psd", "full", "bbox", "tiles", "both"],
                                default="full", help="提取模式（Type2）")
    extract_parser.add_argument("--no-subfolder", action="store_true",
                                help="不创建同名子文件夹")
    
    # 构建命令
    build_parser = subparsers.add_parser("build", help="构建 G00 文件")
    build_parser.add_argument("orig_g00", help="原始 G00 文件或目录")
    build_parser.add_argument("input", help="PNG 文件目录")
    build_parser.add_argument("output", help="输出目录或文件")
    build_parser.add_argument("--json", help="JSON 配置文件（可选，自动查找）")
    build_parser.add_argument("--type", type=int, choices=[0, 1, 2, 3],
                              help="强制指定类型（默认自动检测）")
    build_parser.add_argument("--preset", choices=["fast", "normal", "max"],
                              default="max", help="压缩预设")
    
    args = parser.parse_args()
    
    def log_print(msg: str):
        print(msg)
    
    if args.command == "extract":
        g00_type = G00Type(args.type) if args.type is not None else None
        success, message = extract_g00(
            args.input, args.output,
            g00_type=g00_type,
            recursive=args.recursive,
            extract_mode=args.mode,
            create_subfolders=not args.no_subfolder,
            log_cb=log_print
        )
        print(message)
        return 0 if success else 1
        
    elif args.command == "build":
        g00_type = G00Type(args.type) if args.type is not None else None
        success, message = build_g00(
            args.orig_g00, args.input, args.output,
            json_path=args.json,
            g00_type=g00_type,
            preset=args.preset,
            log_cb=log_print
        )
        print(message)
        return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
