#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一配置管理模块

提供项目配置类的统一接口，包括：
- 并行处理配置
- 优化器配置（Numba JIT、内存池等）
- GPU 加速配置
"""

from dataclasses import dataclass, field
import multiprocessing
from typing import Optional


# ============ 并行处理配置 ============

@dataclass
class ParallelConfig:
    """
    并行处理配置
    
    属性:
        max_workers: 最大工作线程数，0表示自动检测(CPU核心数-1)
        enable_parallel: 是否启用并行处理
        min_files_for_parallel: 启用并行的最小文件数，默认4
    
    使用示例:
        >>> config = ParallelConfig(max_workers=4, min_files_for_parallel=2)
        >>> config.max_workers
        4
    """
    max_workers: int = 0  # 0 表示自动检测
    enable_parallel: bool = True
    min_files_for_parallel: int = 3  # 优化：降低阈值从4到3，更早启用并行
    
    def __post_init__(self):
        """初始化后自动计算最优workers数 - 优化：增加并行度"""
        if self.max_workers == 0:
            cpu_count = multiprocessing.cpu_count()
            # 优化：针对I/O密集型任务，使用CPU核心数，不再-1
            self.max_workers = min(cpu_count, 16)  # 限制最外16线程，避免过多线程造成开销


# ============ 辅助函数 ============

def get_optimal_workers(file_count: int, config: ParallelConfig = None) -> int:
    """根据文件数量获取最优工作线程数"""
    if config is None:
        config = ParallelConfig()
    
    if not config.enable_parallel or file_count < config.min_files_for_parallel:
        return 1
    
    # 限制最大workers数
    return min(config.max_workers, file_count, 16)


# ============ 优化器配置 ============

@dataclass
class OptimizationConfig:
    """
    优化器配置
    
    控制各种性能优化选项
    
    属性:
        enable_numba: 启用 Numba JIT 加速（需要安装 numba）
        enable_memory_pool: 启用内存池减少分配开销
        enable_xxhash: 启用 xxhash 快速哈希（需要安装 xxhash）
        compression_preset: 压缩预设 (fast/normal/max)
        enable_gpu: 启用 GPU 加速（需要安装 cupy）
        gpu_device_id: GPU 设备 ID
        gpu_min_data_size: 使用 GPU 的最小数据量（字节）
    
    使用示例:
        >>> config = OptimizationConfig(enable_numba=True, compression_preset="max")
        >>> config.enable_gpu = True
    """
    # CPU 优化
    enable_numba: bool = True
    enable_memory_pool: bool = True
    enable_xxhash: bool = True
    compression_preset: str = "normal"  # fast, normal, max, promax
    
    # GPU 优化
    enable_gpu: bool = False  # 默认关闭，需要用户显式启用
    gpu_device_id: int = 0
    gpu_min_data_size: int = 100 * 1024  # 100KB
    
    def __post_init__(self):
        """验证配置有效性"""
        if self.compression_preset not in ("fast", "normal", "max", "promax"):
            self.compression_preset = "normal"
        
        if self.gpu_device_id < 0:
            self.gpu_device_id = 0


# ============ 综合配置 ============

@dataclass
class G00ToolkitConfig:
    """
    G00 Toolkit 综合配置
    
    整合所有配置选项
    """
    parallel: ParallelConfig = field(default_factory=ParallelConfig)
    optimization: OptimizationConfig = field(default_factory=OptimizationConfig)
    
    # 日志和进度
    verbose: bool = False
    show_progress: bool = True


# ============ 全局配置实例 ============

_global_config: Optional[G00ToolkitConfig] = None


def get_global_config() -> G00ToolkitConfig:
    """获取全局配置"""
    global _global_config
    if _global_config is None:
        _global_config = G00ToolkitConfig()
    return _global_config


def set_global_config(config: G00ToolkitConfig):
    """设置全局配置"""
    global _global_config
    _global_config = config


def apply_optimization_config(opt_config: OptimizationConfig):
    """
    应用优化配置到优化器模块
    
    将配置传递给 g00_optimizer 和 g00_gpu_accelerator
    """
    try:
        from g00_optimizer import OptimizerConfig, set_optimizer_config
        
        optimizer_cfg = OptimizerConfig(
            enable_numba=opt_config.enable_numba,
            enable_memory_pool=opt_config.enable_memory_pool,
            enable_xxhash=opt_config.enable_xxhash,
            compression_preset=opt_config.compression_preset
        )
        set_optimizer_config(optimizer_cfg)
    except ImportError:
        pass
    
    try:
        from g00_gpu_accelerator import GPUConfig, set_gpu_config
        
        gpu_cfg = GPUConfig(
            enable_gpu=opt_config.enable_gpu,
            device_id=opt_config.gpu_device_id,
            min_data_size=opt_config.gpu_min_data_size
        )
        set_gpu_config(gpu_cfg)
    except ImportError:
        pass


# ============ 环境检测 ============

def detect_available_features() -> dict:
    """
    检测可用的优化功能
    
    Returns:
        可用功能字典
    """
    features = {
        'numba': False,
        'xxhash': False,
        'cupy': False,
        'gpu_count': 0,
    }
    
    try:
        import numba
        features['numba'] = True
    except ImportError:
        pass
    
    try:
        import xxhash
        features['xxhash'] = True
    except ImportError:
        pass
    
    try:
        import cupy as cp
        features['cupy'] = True
        features['gpu_count'] = cp.cuda.runtime.getDeviceCount()
    except:
        pass
    
    return features


def print_environment_info():
    """打印环境信息"""
    features = detect_available_features()
    
    print("G00 Toolkit 环境检测:")
    print(f"  Numba JIT:  {'✓' if features['numba'] else '✗'} "
          f"{'(pip install numba)' if not features['numba'] else ''}")
    print(f"  xxhash:     {'✓' if features['xxhash'] else '✗'} "
          f"{'(pip install xxhash)' if not features['xxhash'] else ''}")
    print(f"  CuPy/CUDA:  {'✓' if features['cupy'] else '✗'} "
          f"{'(pip install cupy-cuda11x)' if not features['cupy'] else ''}")
    if features['cupy']:
        print(f"  GPU 数量:   {features['gpu_count']}")
    print(f"  CPU 核心:   {multiprocessing.cpu_count()}")


if __name__ == "__main__":
    print("=" * 50)
    print("配置模块测试")
    print("=" * 50)
    
    # 环境检测
    print_environment_info()
    print()
    
    # 并行配置测试
    print("并行处理配置:")
    config = ParallelConfig()
    print(f"  max_workers={config.max_workers}, enable_parallel={config.enable_parallel}")
    
    for count in [1, 3, 5, 10, 20]:
        workers = get_optimal_workers(count, config)
        print(f"  {count} 个文件 → {workers} workers")
    
    print()
    
    # 优化配置测试
    print("优化配置:")
    opt_config = OptimizationConfig()
    print(f"  enable_numba={opt_config.enable_numba}")
    print(f"  enable_gpu={opt_config.enable_gpu}")
    print(f"  compression_preset={opt_config.compression_preset}")
    
    print()
    print("=" * 50)
