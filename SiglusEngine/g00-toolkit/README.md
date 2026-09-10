# Siglus G00 Toolkit

- 引擎：SiglusEngine
- 测试游戏：《anemoi 体验版》

## 文件

| 文件 | 用途 |
| --- | --- |
| `main_gui.py` | G00 批量提取、构建和格式转换的 PyQt6 图形界面。 |
| `g00_processor.py` | 自动识别 G00 类型并统一调度提取、构建和批处理。 |
| `g00_type0.py` | Type 0 BGR24 G00 的解码与编码。 |
| `g00_type1.py` | Type 1 索引色 G00 的解码与编码。 |
| `g00_type2.py` | Type 2 分帧 G00 的提取、重建及 PNG/JSON/PSD 处理。 |
| `g00_type3.py` | Type 3 加密 JPEG G00 的解码与编码。 |
| `g00_optimizer.py` | LZSS、哈希、Numba 和压缩预设优化。 |
| `g00_gpu_accelerator.py` | 使用 CuPy/CUDA 加速可并行的图像处理。 |
| `parallel_processor.py` | 批量任务的线程池、暂停、停止和进度管理。 |
| `progress_utils.py` | 进度统计与回调辅助。 |
| `config.py` | 并行、优化器和 GPU 配置及环境检测。 |
| `requirements.txt` | 运行工具所需的 Python 依赖。 |
| `requirements-dev.txt` | 开发和测试所需的附加依赖。 |
| `安装依赖.bat` | 安装 Python 依赖。 |
| `启动GUI.bat` | 启动图形界面。 |
