#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进度工具类 - Siglus G00 Toolkit

提供进度回调、限速更新、日志缓冲、命令行进度条等功能。
参考 Softpal_PGD_Toolkit 的优化实现。

主要组件：
  - ConsoleProgressBar：命令行进度条（不依赖 tqdm）
  - BatchProgressManager：批量处理进度管理器
  - ProgressCallback：统一进度回调管理器
  - ThrottledUpdater：限速更新器
  - BufferedLogListener：缓冲日志监听器
  - TaskController：任务控制器
"""

import sys
import time
import threading
from typing import Callable, Optional, List
from dataclasses import dataclass


@dataclass
class ProgressConfig:
    """进度配置"""
    progress_update_interval: float = 0.1   # 进度更新最小间隔（秒）
    batch_update_interval: float = 0.1      # 批量进度更新最小间隔（秒）
    log_flush_interval: float = 0.2         # 日志刷新间隔（秒）
    max_log_buffer: int = 50                # 最大日志缓冲条数
    console_width: int = 50                 # 命令行进度条宽度


class ConsoleProgressBar:
    """
    命令行进度条 - 不依赖外部库
    
    提供在终端显示进度条的功能，支持速度和 ETA 显示。
    参考 Softpal_PGD_Toolkit 的实现。
    
    属性:
        total: 总项数
        desc: 描述文本
        width: 进度条宽度，默认 50
        show_speed: 是否显示速度
        show_eta: 是否显示 ETA
    
    使用示例:
        >>> with ConsoleProgressBar(100, desc="处理") as pbar:
        ...     for i in range(100):
        ...         pbar.update(1)
        
        >>> pbar = ConsoleProgressBar(10, show_speed=True, show_eta=True)
        >>> for i in range(10):  
        ...     pbar.update(1)
        >>> pbar.close()
    
    性能特性:
        - 自动节流更新，避免频繁刷新
        - 支持上下文管理器
        - 线程安全
    """
    
    def __init__(self, total: int, desc: str = "", width: int = 50,
                 show_speed: bool = True, show_eta: bool = True):
        self.total = total
        self.desc = desc
        self.width = width
        self.show_speed = show_speed
        self.show_eta = show_eta
        self.current = 0
        self.start_time = time.time()
        self._lock = threading.Lock()
        self._closed = False
        self._last_update_time = self.start_time
        self._last_update_value = 0
    
    def update(self, n: int = 1) -> None:
        """更新进度"""
        if self._closed:
            return
            
        with self._lock:
            self.current += n
            now = time.time()
            
            # 限制更新频率（但 100% 必须立即更新）
            if (now - self._last_update_time < 0.1) and (self.current < self.total):
                return
                
            self._render(now)
            self._last_update_time = now
            self._last_update_value = self.current
    
    def set_description(self, desc: str) -> None:
        """设置描述"""
        self.desc = desc
    
    def _render(self, current_time: float) -> None:
        """渲染进度条"""
        if self.total <= 0:
            return
            
        progress = min(self.current / self.total, 1.0)
        elapsed = current_time - self.start_time
        
        # 计算速度
        speed = 0
        if elapsed > 0:
            speed = self.current / elapsed
        
        # 计算 ETA
        eta = 0
        if self.current > 0 and speed > 0:
            remaining = self.total - self.current
            eta = remaining / speed
        
        # 构建进度条（使用 ASCII 字符，兼容 GBK 编码）
        filled = int(self.width * progress)
        bar = '=' * filled + '-' * (self.width - filled)
        
        # 格式化信息
        info_parts = []
        info_parts.append(f"{progress*100:5.1f}%")
        info_parts.append(f"[{self.current}/{self.total}]")
        
        if self.show_speed:
            info_parts.append(f"Speed: {self._format_speed(speed)}")
        
        if self.show_eta and eta > 0:
            info_parts.append(f"ETA: {self._format_time(eta)}")
        
        info_str = " ".join(info_parts)
        
        # 输出
        desc_str = f"{self.desc}: " if self.desc else ""
        sys.stdout.write(f"\r{desc_str}|{bar}| {info_str}")
        sys.stdout.flush()
    
    def _format_speed(self, speed: float) -> str:
        """格式化速度显示"""
        if speed < 1024:
            return f"{speed:.1f} B/s"
        elif speed < 1024 * 1024:
            return f"{speed/1024:.1f} KB/s"
        else:
            return f"{speed/(1024*1024):.1f} MB/s"
    
    def _format_time(self, seconds: float) -> str:
        """格式化时间显示"""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}h"
    
    def close(self) -> None:
        """关闭进度条"""
        if not self._closed:
            self._closed = True
            # 显示最终状态
            self._render(time.time())
            print()  # 换行
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class ThrottledUpdater:
    """
    限速更新器
    
    限制回调函数的调用频率，避免过于频繁的UI更新。
    """
    
    def __init__(self, min_interval: float = 0.2):
        """
        初始化
        
        Args:
            min_interval: 最小调用间隔（秒）
        """
        self.min_interval = min_interval
        self.last_update = 0.0
        self._lock = threading.Lock()
        
    def __call__(self, func: Callable) -> Callable:
        """装饰器方式使用"""
        def wrapper(*args, **kwargs):
            now = time.time()
            # 快速路径检查（无锁）
            if now - self.last_update < self.min_interval:
                return
                
            with self._lock:
                # 双重检查
                now = time.time()
                if now - self.last_update >= self.min_interval:
                    func(*args, **kwargs)
                    self.last_update = now
                    
        return wrapper
        
    def should_update(self) -> bool:
        """检查是否应该更新"""
        now = time.time()
        if now - self.last_update >= self.min_interval:
            with self._lock:
                if now - self.last_update >= self.min_interval:
                    self.last_update = now
                    return True
        return False
        
    def force_update(self):
        """强制更新（重置计时器）"""
        with self._lock:
            self.last_update = 0.0


class ProgressCallback:
    """
    进度回调管理器
    
    统一管理文件进度和批量进度的回调，支持限速更新。
    """
    
    def __init__(
        self,
        file_progress_cb: Optional[Callable[[int, int], None]] = None,
        batch_progress_cb: Optional[Callable[[int, int, float], None]] = None,
        config: Optional[ProgressConfig] = None
    ):
        """
        初始化
        
        Args:
            file_progress_cb: 文件进度回调 (current, total)
            batch_progress_cb: 批量进度回调 (current, total, elapsed)
            config: 进度配置
        """
        self.config = config or ProgressConfig()
        self._file_cb = file_progress_cb
        self._batch_cb = batch_progress_cb
        
        self._file_throttle = ThrottledUpdater(self.config.progress_update_interval)
        self._batch_throttle = ThrottledUpdater(self.config.batch_update_interval)
        
        self._file_total = 0
        self._file_current = 0
        self._batch_total = 0
        self._batch_current = 0
        self._start_time = 0.0
        self._lock = threading.Lock()
        
    def reset(self):
        """重置状态"""
        self._file_total = 0
        self._file_current = 0
        self._batch_total = 0
        self._batch_current = 0
        self._start_time = time.time()
        self._file_throttle.force_update()
        self._batch_throttle.force_update()
        
    def update_file_progress(self, current: int, total: int):
        """
        更新文件进度（带限速）
        
        Args:
            current: 当前进度
            total: 总进度
        """
        self._file_current = current
        self._file_total = total
        
        # 100% 时强制更新
        if current >= total:
            if self._file_cb:
                self._file_cb(current, total)
            return
            
        # 限速更新
        if self._file_throttle.should_update():
            if self._file_cb:
                self._file_cb(current, total)
                
    def update_batch_progress(self, current: int, total: int, elapsed: Optional[float] = None):
        """
        更新批量进度（带限速）
        
        Args:
            current: 当前处理数
            total: 总文件数
            elapsed: 已用时间（可选，不传则自动计算）
        """
        self._batch_current = current
        self._batch_total = total
        
        if elapsed is None:
            elapsed = time.time() - self._start_time
            
        # 完成时强制更新
        if current >= total:
            if self._batch_cb:
                self._batch_cb(current, total, elapsed)
            return
            
        # 限速更新
        if self._batch_throttle.should_update():
            if self._batch_cb:
                self._batch_cb(current, total, elapsed)
                
    def get_file_progress(self) -> tuple:
        """获取当前文件进度"""
        return (self._file_current, self._file_total)
        
    def get_batch_progress(self) -> tuple:
        """获取当前批量进度"""
        return (self._batch_current, self._batch_total)


class BatchProgressManager:
    """
    批量处理进度管理器
    
    功能:
        - 统一管理单文件和批量进度
        - 自动节流 GUI 回调，避免界面卡顿
        - 支持 CLI 进度条和日志输出
        - 支持 GUI 和命令行同时显示（force_cli_progress=True）
    
    使用示例:
        >>> manager = BatchProgressManager(
        ...     total_files=10,
        ...     file_progress_cb=update_file_progress,
        ...     batch_progress_cb=update_batch_progress,
        ...     use_cli_progress=True
        ... )
        >>> 
        >>> for file in files:
        ...     manager.start_file(file)
        ...     # 处理文件，使用 manager.get_file_callback() 获取回调
        ...     process_file(file, progress_cb=manager.get_file_callback())
        ...     manager.finish_file(success=True)
        >>> 
        >>> manager.close()
    """
    
    def __init__(self,
                 total_files: int,
                 file_progress_cb: Optional[Callable[[int, int], None]] = None,
                 batch_progress_cb: Optional[Callable[[int, int, float], None]] = None,
                 log_cb: Optional[Callable[[str], None]] = None,
                 use_cli_progress: bool = False,
                 cli_desc: str = "批量处理",
                 force_cli_progress: bool = False):
        """
        Args:
            total_files: 总文件数
            file_progress_cb: 单文件进度回调 (done, total)
            batch_progress_cb: 批量进度回调 (processed, total, elapsed)
            log_cb: 日志回调函数
            use_cli_progress: 是否使用 CLI 进度条
            cli_desc: CLI 进度条描述
            force_cli_progress: 强制启用 CLI 进度条（即使 GUI 模式也显示）
        """
        self.total_files = total_files
        self.file_progress_cb = file_progress_cb
        self.batch_progress_cb = batch_progress_cb
        self.log_cb = log_cb
        
        self.processed_count = 0
        self.start_time = time.time()
        self.current_file = None
        
        # 节流控制 - GUI 模式下避免过于频繁的回调
        self._last_file_update = 0
        self._last_batch_update = 0
        self._file_throttle_interval = 0.05  # 50ms - GUI 优化
        self._batch_throttle_interval = 0.1  # 100ms
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 定时器线程 - 每 0.1s 强制更新批量进度时间
        self._timer_thread = None
        self._timer_running = False
        if batch_progress_cb:
            self._timer_running = True
            self._timer_thread = threading.Thread(target=self._time_update_loop, daemon=True)
            self._timer_thread.start()
        
        # CLI 进度条 - GUI 模式下也显示（如果 force_cli_progress=True）
        self.cli_pbar = None
        should_show_cli = use_cli_progress or (force_cli_progress and batch_progress_cb is not None)
        if should_show_cli:
            try:
                self.cli_pbar = ConsoleProgressBar(
                    total=total_files,
                    desc=cli_desc,
                    width=50,
                    show_speed=True,
                    show_eta=True
                )
            except Exception:
                pass
        
        # 初始化批量进度
        if self.batch_progress_cb:
            self.batch_progress_cb(0, total_files, 0.0)
    
    def _time_update_loop(self):
        """定时器线程：每 0.1s 强制更新批量进度的时间显示"""
        while self._timer_running:
            time.sleep(0.1)  # 每 0.1 秒更新一次
            if self._timer_running and self.batch_progress_cb:
                elapsed = time.time() - self.start_time
                # 直接更新，不经过节流检查（因为已经是 0.1s 间隔）
                self.batch_progress_cb(self.processed_count, self.total_files, elapsed)
    
    def get_file_callback(self) -> Optional[Callable[[int, int], None]]:
        """
        获取带节流优化的单文件进度回调
        
        Returns:
            节流后的进度回调函数
        """
        if not self.file_progress_cb:
            return None
        
        def _throttled_callback(done: int, total: int):
            """GUI 优化:节流单文件进度回调"""
            now = time.time()
            with self._lock:
                # 只在间隔足够时才回调，或者已完成(done==total)
                if (now - self._last_file_update >= self._file_throttle_interval) or (done >= total):
                    self.file_progress_cb(done, total)
                    self._last_file_update = now
        
        return _throttled_callback
    
    def start_file(self, filename: str):
        """开始处理一个文件"""
        self.current_file = filename
        self._last_file_update = 0  # 重置节流计时器
    
    def finish_file(self, success: bool = True):
        """完成一个文件的处理"""
        with self._lock:
            self.processed_count += 1
            
            # 更新批量进度
            if self.cli_pbar:
                self.cli_pbar.update(1)
            
            # 批量进度由定时器线程自动更新，这里不需要重复调用
            # 只在最后一个文件完成时强制更新一次
            if self.batch_progress_cb and self.processed_count >= self.total_files:
                elapsed = time.time() - self.start_time
                self.batch_progress_cb(self.processed_count, self.total_files, elapsed)
    
    def log(self, message: str):
        """输出日志"""
        if self.log_cb:
            self.log_cb(message)
        elif not self.cli_pbar:  # CLI 进度条模式下不输出日志
            print(message)
    
    def close(self):
        """关闭进度管理器"""
        # 停止定时器线程
        if self._timer_thread:
            self._timer_running = False
            self._timer_thread.join(timeout=0.5)  # 等待线程结束，最多 0.5 秒
        
        # 确保最终单文件进度为 100%
        if self.file_progress_cb:
            self.file_progress_cb(100, 100)
        
        # 确保最终批量进度为 100%
        if self.batch_progress_cb:
            elapsed = time.time() - self.start_time
            self.batch_progress_cb(self.total_files, self.total_files, elapsed)
        
        # 关闭 CLI 进度条
        if self.cli_pbar:
            self.cli_pbar.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class BufferedLogListener:
    """
    缓冲日志监听器
    
    将日志消息缓冲起来，定期批量刷新到UI，减少UI更新开销。
    """
    
    def __init__(
        self,
        flush_callback: Callable[[str], None],
        flush_interval: float = 0.2,
        max_buffer: int = 50
    ):
        """
        初始化
        
        Args:
            flush_callback: 刷新回调函数（接收拼接后的日志文本）
            flush_interval: 刷新间隔（秒）
            max_buffer: 最大缓冲条数
        """
        self.flush_callback = flush_callback
        self.flush_interval = flush_interval
        self.max_buffer = max_buffer
        
        self.buffer: List[str] = []
        self.last_flush = time.time()
        self._lock = threading.Lock()
        
    def __call__(self, line: str):
        """
        添加日志行
        
        Args:
            line: 日志行
        """
        with self._lock:
            self.buffer.append(line)
            now = time.time()
            
            # 满足任一条件则刷新
            if len(self.buffer) >= self.max_buffer or now - self.last_flush > self.flush_interval:
                self._flush()
                
    def _flush(self):
        """执行刷新"""
        if not self.buffer:
            return
            
        text = "\n".join(self.buffer)
        self.buffer.clear()
        self.last_flush = time.time()
        
        try:
            self.flush_callback(text)
        except Exception:
            pass
            
    def flush(self):
        """手动刷新"""
        with self._lock:
            self._flush()


class TaskController:
    """
    任务控制器
    
    提供暂停/停止任务的控制机制。
    """
    
    def __init__(self):
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self._condition = threading.Condition()
        
    def reset(self):
        """重置状态"""
        with self._condition:
            self.pause_event.clear()
            self.stop_event.clear()
            self._condition.notify_all()
            
    def pause(self):
        """暂停"""
        self.pause_event.set()
        
    def resume(self):
        """恢复"""
        with self._condition:
            self.pause_event.clear()
            self._condition.notify_all()
            
    def stop(self):
        """停止"""
        with self._condition:
            self.stop_event.set()
            self.pause_event.clear()
            self._condition.notify_all()
            
    def is_paused(self) -> bool:
        """是否暂停"""
        return self.pause_event.is_set()
        
    def is_stopped(self) -> bool:
        """是否停止"""
        return self.stop_event.is_set()
        
    def wait_if_paused(self, timeout: float = 0.1):
        """如果暂停则等待"""
        with self._condition:
            while self.pause_event.is_set() and not self.stop_event.is_set():
                self._condition.wait(timeout)
                
    def check_stop(self):
        """检查停止，若已停止则抛出异常"""
        if self.stop_event.is_set():
            raise UserStopException("用户停止处理")
            
    def check_state(self):
        """检查状态（暂停等待 + 停止检查）"""
        self.wait_if_paused()
        self.check_stop()


class UserStopException(Exception):
    """用户停止异常"""
    pass


# ===================== 辅助函数 =====================
def format_time(seconds: float) -> str:
    """格式化时间"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m {int(s)}s"
    else:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h)}h {int(m)}m {int(s)}s"


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    else:
        return f"{size_bytes / 1024 / 1024 / 1024:.2f} GB"


def format_speed(speed: float) -> str:
    """格式化速度"""
    if speed < 1024:
        return f"{speed:.1f} B/s"
    elif speed < 1024 * 1024:
        return f"{speed / 1024:.1f} KB/s"
    else:
        return f"{speed / 1024 / 1024:.1f} MB/s"


def calculate_eta(processed: int, total: int, elapsed: float) -> str:
    """计算预计剩余时间"""
    if processed <= 0 or elapsed <= 0:
        return "计算中..."
        
    speed = processed / elapsed
    remaining = total - processed
    
    if speed <= 0:
        return "计算中..."
        
    eta_seconds = remaining / speed
    return format_time(eta_seconds)
