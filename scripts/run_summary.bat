@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist ".venv\Scripts\activate.bat" (
    echo [错误] 未检测到虚拟环境 .venv
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python auto_summary.py
