@echo off
chcp 65001 >nul 2>&1
title Siglus G00 Toolkit - 安装依赖

echo ═══════════════════════════════════════════════════════════════
echo           Siglus G00 Toolkit - 依赖安装程序
echo ═══════════════════════════════════════════════════════════════
echo.

REM 检查 Python 是否可用
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请确保已安装 Python 并添加到 PATH
    echo.
    echo 请从 https://www.python.org/downloads/ 下载安装 Python
    echo 安装时请勾选 "Add Python to PATH" 选项
    pause
    exit /b 1
)

echo [信息] 检测到 Python:
python --version
echo.

echo ═══════════════════════════════════════════════════════════════
echo                     安装必需依赖
echo ═══════════════════════════════════════════════════════════════
echo.

echo [1/4] 安装 PyQt6 (GUI 框架)...
pip install PyQt6 --upgrade
if %errorlevel% neq 0 (
    echo [警告] PyQt6 安装可能失败
)
echo.

echo [2/4] 安装 NumPy (数值计算)...
pip install numpy --upgrade
if %errorlevel% neq 0 (
    echo [警告] NumPy 安装可能失败
)
echo.

echo [3/4] 安装 Pillow (图像处理)...
pip install pillow --upgrade
if %errorlevel% neq 0 (
    echo [警告] Pillow 安装可能失败
)
echo.

echo [4/4] 安装 xxhash (快速哈希)...
pip install xxhash --upgrade
if %errorlevel% neq 0 (
    echo [警告] xxhash 安装可能失败
)
echo.

echo ═══════════════════════════════════════════════════════════════
echo                     安装可选依赖
echo ═══════════════════════════════════════════════════════════════
echo.

echo [可选 1/2] 安装 tqdm (进度条)...
pip install tqdm --upgrade
echo.

echo [可选 2/2] 安装 numba (JIT 加速)...
pip install numba --upgrade
echo.

echo ═══════════════════════════════════════════════════════════════
echo                     安装完成
echo ═══════════════════════════════════════════════════════════════
echo.

echo [信息] 依赖安装已完成！
echo.
echo 已安装的包:
echo   ✓ PyQt6     - GUI 框架 (必需)
echo   ✓ NumPy     - 数值计算 (必需)
echo   ✓ Pillow    - 图像处理 (必需)
echo   ✓ xxhash    - 快速哈希 (推荐)
echo   ○ tqdm      - 进度条 (可选)
echo   ○ numba     - JIT 加速 (可选)
echo.
echo 现在可以运行 "启动GUI.bat" 来启动程序了！
echo.
pause
