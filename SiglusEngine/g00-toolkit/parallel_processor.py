#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
并行处理模块 - 多线程批量处理能力

功能介绍：
  提供可配置的多线程批量处理能力
  
  主要组件：
  - parallel_process_files：并行处理文件列表
  - ProgressAggregator：进度聚合器（线程安全）
  - LogAggregator：日志聚合器（线程安全）
  - TaskController：任务控制器（暂停/停止/检查）
  - ParallelConfig：并行配置

用法：
  from parallel_processor import parallel_process_files, ParallelConfig, TaskController
  
  config = ParallelConfig(max_workers=4)
  controller = TaskController()
  
  results, failures = parallel_process_files(
      files=['file1.g00', 'file2.g00'],
      process_func=my_process_function,
      config=config,
      task_controller=controller
  )

依赖：
  必需：无（仅需 Python 标准库 concurrent.futures）
  推荐：config.py（统一配置管理）
"""

import os
import time
import threading
import gc
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from typing import Callable, List, Optional, Any, Dict, Tuple
import multiprocessing

# 导入统一配置
from config import ParallelConfig


class ProgressAggregator:
    """
    进度聚合器 - 线程安全的进度收集与回调
    
    优化特性:
        - 线程安全的进度更新
        - 自动节流回调，避免频繁更新导致 UI 卡顿
        - 支持实时聚合多个 worker 的进度
        - 跟踪进度最快的文件（并行模式）
        - 实时跟踪成功/失败计数
        - 批量更新减少锁竞争
    """
    
    # 回调节流间隔（秒）- 优化后减少GUI更新频率，防止界面卡顿
    BATCH_UPDATE_INTERVAL = 0.3  # 批量进度更新（原0.2s，优化后减少更新频率）
    FILE_UPDATE_INTERVAL = 0.1   # 单文件进度更新（原0.05s，优化后减少更新频率）
    
    def __init__(self, total_files: int,
                 file_progress_cb: Optional[Callable[[int, int], None]] = None,
                 batch_progress_cb: Optional[Callable[[int, int, int, int, float], None]] = None,
                 current_file_cb: Optional[Callable[[str], None]] = None):
        self.total_files = total_files
        self.file_progress_cb = file_progress_cb
        self.batch_progress_cb = batch_progress_cb
        self.current_file_cb = current_file_cb
        self.start_time = time.time()
        
        # 线程安全状态
        self._lock = threading.Lock()
        self._processed = 0
        self._success_count = 0
        self._fail_count = 0
        self._current_file_progress: Dict[int, tuple] = {}  # {worker_id: (done, total, file_name)}
        self._fastest_file = ''
        self._fastest_progress = 0.0
        
        # 节流控制 - 使用类常量
        self._last_batch_update = 0.0
        self._batch_update_interval = self.BATCH_UPDATE_INTERVAL
        self._last_file_update = 0.0
        self._file_update_interval = self.FILE_UPDATE_INTERVAL
    
    def update_file_progress(self, worker_id: int, done: int, total: int, file_name: str = ''):
        """更新单个文件的进度"""
        now = time.time()
        should_update = False
        update_done = done
        update_total = total
        
        with self._lock:
            self._current_file_progress[worker_id] = (done, total, file_name)
            
            current_pct = (done / total * 100.0) if total > 0 else 0.0
            
            if current_pct > self._fastest_progress:
                self._fastest_progress = current_pct
                self._fastest_file = file_name
                
                if self.current_file_cb and file_name:
                    self.current_file_cb(file_name)
                
                update_done = done
                update_total = total
            elif file_name == self._fastest_file:
                self._fastest_progress = current_pct
                update_done = done
                update_total = total
            else:
                update_done = int(self._fastest_progress * total / 100.0)
                update_total = total
            
            if (now - self._last_file_update >= self._file_update_interval) or (current_pct >= 100.0):
                should_update = True
                self._last_file_update = now
        
        if should_update and self.file_progress_cb:
            self.file_progress_cb(update_done, update_total)
    
    def _find_fastest_file(self):
        """查找当前进度最快的文件"""
        if not self._current_file_progress:
            return '', 0.0
        
        fastest_file = ''
        fastest_pct = 0.0
        
        for worker_id, (done, total, file_name) in self._current_file_progress.items():
            if total > 0:
                pct = done / total * 100.0
                if pct > fastest_pct:
                    fastest_pct = pct
                    fastest_file = file_name
        
        return fastest_file, fastest_pct
    
    def mark_file_completed(self, worker_id: int, success: bool = True):
        """标记一个文件处理完成"""
        with self._lock:
            self._processed += 1
            if success:
                self._success_count += 1
            else:
                self._fail_count += 1
            processed = self._processed
            success_count = self._success_count
            fail_count = self._fail_count
            now = time.time()
            
            if worker_id in self._current_file_progress:
                completed_file = self._current_file_progress[worker_id][2]
                del self._current_file_progress[worker_id]
                
                if completed_file == self._fastest_file:
                    self._fastest_file, self._fastest_progress = self._find_fastest_file()
                    
                    if self.current_file_cb and self._fastest_file:
                        self.current_file_cb(self._fastest_file)
            
            if self.batch_progress_cb:
                elapsed = now - self.start_time
                if (now - self._last_batch_update >= self._batch_update_interval) or (processed >= self.total_files):
                    self.batch_progress_cb(processed, self.total_files, success_count, fail_count, elapsed)
                    self._last_batch_update = now
    
    def finalize(self):
        """最终更新（确保100%）"""
        if self.file_progress_cb:
            self.file_progress_cb(100, 100)
        
        if self.batch_progress_cb:
            elapsed = time.time() - self.start_time
            with self._lock:
                self.batch_progress_cb(self.total_files, self.total_files, 
                                      self._success_count, self._fail_count, elapsed)


class LogAggregator:
    """日志聚合器 - 线程安全的日志收集"""
    
    def __init__(self, log_cb: Optional[Callable[[str], None]] = None):
        self.log_cb = log_cb
        self._lock = threading.Lock()
    
    def log(self, message: str):
        """线程安全的日志输出"""
        if self.log_cb:
            with self._lock:
                self.log_cb(message)


class TaskController:
    """任务控制器 - 支持暂停/停止/检查"""
    
    def __init__(self):
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self._state_condition = threading.Condition()
    
    def reset(self):
        """重置状态"""
        with self._state_condition:
            self.pause_event.clear()
            self.stop_event.clear()
            self._state_condition.notify_all()
    
    def pause(self):
        """暂停"""
        self.pause_event.set()
    
    def resume(self):
        """继续"""
        with self._state_condition:
            self.pause_event.clear()
            self._state_condition.notify_all()
    
    def stop(self):
        """停止"""
        with self._state_condition:
            self.stop_event.set()
            self.pause_event.clear()
            self._state_condition.notify_all()
    
    def is_paused(self) -> bool:
        return self.pause_event.is_set()
    
    def is_stopped(self) -> bool:
        return self.stop_event.is_set()
    
    def wait_if_paused(self, timeout: float = 0.1):
        """如果暂停则等待"""
        with self._state_condition:
            while self.pause_event.is_set() and not self.stop_event.is_set():
                self._state_condition.wait(timeout)
    
    def check_stop(self):
        """检查是否停止（抛出异常）"""
        if self.stop_event.is_set():
            raise UserStopException("用户停止处理")


class UserStopException(Exception):
    """用户停止异常"""
    pass


def parallel_process_files(
    files: List[str],
    process_func: Callable[[str, Any], Any],
    config: Optional[ParallelConfig] = None,
    file_progress_cb: Optional[Callable[[int, int], None]] = None,
    batch_progress_cb: Optional[Callable[[int, int, float], None]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    current_file_cb: Optional[Callable[[str], None]] = None,
    task_controller: Optional[TaskController] = None,
    **process_kwargs
) -> Tuple[List[Any], List[Tuple[str, Exception]]]:
    """
    并行处理文件列表
    
    参数：
        files: 文件路径列表
        process_func: 处理函数，签名为 func(file_path, **kwargs) -> result
        config: 并行配置
        file_progress_cb: 单文件进度回调
        batch_progress_cb: 批量进度回调
        log_cb: 日志回调
        current_file_cb: 当前文件名回调
        task_controller: 任务控制器
        **process_kwargs: 传递给 process_func 的额外参数
    
    返回：
        (成功结果列表, 失败列表[(文件路径, 异常)])
    
    优化：
        - 按文件大小排序，大文件优先处理，平衡负载
        - 减少进度更新频率，降低GUI卡顿
        - 增加内存回收，减少内存占用
    """
    if config is None:
        config = ParallelConfig()
    
    total = len(files)
    
    # 优化：按文件大小排序，大文件优先处理
    files = sort_files_for_processing(files)
    
    # 判断是否启用并行
    use_parallel = (
        config.enable_parallel and 
        total >= config.min_files_for_parallel and
        config.max_workers > 1
    )
    
    max_workers = config.max_workers
    
    # 创建聚合器
    progress_agg = ProgressAggregator(total, file_progress_cb, batch_progress_cb, current_file_cb)
    log_agg = LogAggregator(log_cb)
    
    # 初始化批量进度
    if batch_progress_cb:
        batch_progress_cb(0, total, 0, 0, 0.0)
    
    results = []
    errors = []
    
    def _process_wrapper(file_path: str, worker_id: int):
        """包装处理函数，添加进度和日志"""
        file_name = os.path.basename(file_path)
        try:
            # 检查中断
            if task_controller:
                task_controller.wait_if_paused()
                task_controller.check_stop()
            
            # 输出开始处理日志
            if log_cb:
                log_cb(f"[INFO] 正在处理: {file_name}")
            
            # 创建单文件进度回调
            def _file_cb(done, total):
                progress_agg.update_file_progress(worker_id, done, total, file_name)
            
            # 执行处理（注入进度回调）
            kwargs = process_kwargs.copy()
            
            # 检查函数签名，动态注入回调
            try:
                import inspect
                sig = inspect.signature(process_func)
                if 'file_progress_cb' in sig.parameters:
                    kwargs['file_progress_cb'] = _file_cb
                if 'log_cb' in sig.parameters:
                    kwargs['log_cb'] = log_agg.log
            except (ValueError, TypeError):
                pass
            
            result = process_func(file_path, **kwargs)
            
            # 输出完成日志
            if log_cb:
                log_cb(f"[OK] 处理完成: {file_name}")
            
            # 标记完成（成功）
            progress_agg.mark_file_completed(worker_id, success=True)
            return (file_path, result, None)
            
        except Exception as e:
            # 输出失败日志
            if log_cb:
                log_cb(f"[ERROR] 处理失败 {file_name}: {e}")
            # 标记完成（失败）
            progress_agg.mark_file_completed(worker_id, success=False)
            return (file_path, None, e)
    
    if use_parallel:
        # 并行处理
        if log_cb:
            log_cb(f"[INFO] 启用并行处理, 线程数: {max_workers}")
        
        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            # 提交所有任务
            futures = {
                executor.submit(_process_wrapper, file_path, i): (file_path, i)
                for i, file_path in enumerate(files)
            }
            
            # 收集结果
            for future in as_completed(futures):
                try:
                    file_path, result, error = future.result(timeout=300)
                    if error:
                        errors.append((file_path, error))
                    else:
                        results.append(result)
                except Exception as e:
                    file_path, _ = futures[future]
                    errors.append((file_path, e))
                    if log_cb:
                        log_cb(f"[ERROR] 处理失败 {os.path.basename(file_path)}: {e}")
                
                # 检查中断
                if task_controller and task_controller.is_stopped():
                    if log_cb:
                        log_cb("[WARNING] 用户停止处理，正在取消剩余任务...")
                    break
        finally:
            if task_controller and task_controller.is_stopped():
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    executor.shutdown(wait=False)
            else:
                executor.shutdown(wait=True)
    else:
        # 串行处理
        if log_cb and total > 0:
            log_cb(f"[INFO] 使用串行处理, 文件数: {total}")
        
        for i, file_path in enumerate(files):
            file_path_result, result, error = _process_wrapper(file_path, i)
            if error:
                errors.append((file_path, error))
            else:
                results.append(result)
            
            # 检查中断
            if task_controller:
                try:
                    task_controller.wait_if_paused()
                    task_controller.check_stop()
                except UserStopException:
                    if log_cb:
                        log_cb("[WARNING] 用户停止处理")
                    break
    
    # 最终更新
    progress_agg.finalize()
    
    # 处理完成后触发垃圾回收，释放内存
    gc.collect()
    
    return results, errors


def get_optimal_workers(file_count: int, config: Optional[ParallelConfig] = None, 
                        io_bound: bool = True, file_sizes: Optional[List[int]] = None) -> int:
    """
    获取最优工作线程数
    
    Args:
        file_count: 文件数量
        config: 并行配置
        io_bound: 是否为 I/O 密集型任务
        file_sizes: 文件大小列表（用于大文件优化）
    
    Returns:
        最优线程数
    """
    if config is None:
        config = ParallelConfig()
    
    if not config.enable_parallel or file_count < config.min_files_for_parallel:
        return 1
    
    if config.max_workers > 0:
        base_workers = config.max_workers
    else:
        cpu_count = multiprocessing.cpu_count()
        if io_bound:
            base_workers = min(cpu_count * 2, 32)
        else:
            base_workers = max(1, cpu_count - 1)
    
    # 根据文件数量调整
    if file_count < 4:
        return 1
    elif file_count < 10:
        workers = min(2, base_workers)
    elif file_count < 50:
        workers = min(max(4, base_workers // 2), base_workers)
    else:
        workers = base_workers
    
    # 大文件优化：减少并行度以避免内存压力
    if file_sizes:
        avg_size = sum(file_sizes) / len(file_sizes)
        max_size = max(file_sizes)
        
        # 平均文件超过 5MB 或最大文件超过 20MB，减少并行度
        if avg_size > 5 * 1024 * 1024 or max_size > 20 * 1024 * 1024:
            workers = max(1, workers // 2)
    
    return workers


def sort_files_for_processing(files: List[str]) -> List[str]:
    """
    排序文件以优化处理顺序
    
    策略：大文件优先处理，以平衡负载
    
    Args:
        files: 文件路径列表
    
    Returns:
        排序后的文件列表
    """
    try:
        # 按文件大小降序排列
        file_sizes = [(f, os.path.getsize(f)) for f in files if os.path.exists(f)]
        file_sizes.sort(key=lambda x: x[1], reverse=True)
        return [f for f, _ in file_sizes]
    except Exception:
        return files


def get_file_sizes(files: List[str]) -> List[int]:
    """获取文件大小列表"""
    sizes = []
    for f in files:
        try:
            sizes.append(os.path.getsize(f))
        except Exception:
            sizes.append(0)
    return sizes


if __name__ == "__main__":
    # 测试代码
    print("并行处理模块测试")
    
    def test_process(file_path: str) -> str:
        import time
        time.sleep(0.1)  # 模拟处理
        return f"Processed: {file_path}"
    
    test_files = [f"test_{i}.g00" for i in range(10)]
    config = ParallelConfig(max_workers=4)
    
    def on_progress(done, total, success, fail, elapsed):
        print(f"进度: {done}/{total}, 成功: {success}, 失败: {fail}, 耗时: {elapsed:.2f}s")
    
    results, errors = parallel_process_files(
        files=test_files,
        process_func=test_process,
        config=config,
        batch_progress_cb=on_progress
    )
    
    print(f"完成: {len(results)} 成功, {len(errors)} 失败")
