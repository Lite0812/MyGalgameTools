@echo off
chcp 65001 >nul 2>&1
title "Siglus G00 Toolkit - GUI"

echo ===============================================================
echo           Siglus G00 Toolkit - PNG To/From G00 Converter
echo ===============================================================
echo.

REM 检查 Python 是否可用
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请确保已安装 Python 并添加到 PATH
    echo.
    echo 请从 https://www.python.org/downloads/ 下载安装 Python
    pause
    exit /b 1
)

REM 检查 PyQt6 是否安装
python -c "import PyQt6" >nul 2>&1
if %errorlevel% neq 0 (
    echo [提示] 未检测到 PyQt6，正在安装...
    pip install PyQt6 -q
    if %errorlevel% neq 0 (
        echo [错误] PyQt6 安装失败，请手动运行: pip install PyQt6
        pause
        exit /b 1
    )
)

echo [信息] 正在启动 GUI...
echo.

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 启动 GUI
python main_gui.py

if %errorlevel% neq 0 (
    echo.
    echo [错误] 程序异常退出，错误码: %errorlevel%
    pause
)
