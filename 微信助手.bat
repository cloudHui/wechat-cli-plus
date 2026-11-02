@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

where python >nul 2>&1
if %errorlevel% neq 0 goto no_python

if not exist ".venv\Scripts\python.exe" goto init_venv

:run_app
if /i "%1"=="run" (
    call .venv\Scripts\python.exe auto_summary.py
    exit /b 0
)
call .venv\Scripts\python.exe scripts\menu.py
exit /b 0

:no_python
echo ============================================================
echo [ERROR] Python not found on this computer.
echo Please install Python 3.11+ and check 'Add python.exe to PATH'.
echo Download: https://repo.huaweicloud.com/python/3.11.9/python-3.11.9-amd64.exe
echo ============================================================
pause
exit /b 1

:init_venv
echo ============================================================
echo [INIT] Setting up Python virtual environment...
echo ============================================================
echo [1/3] Setting Tsinghua pip mirror...
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
echo [2/3] Creating virtual environment (.venv)...
python -m venv .venv
echo [3/3] Installing dependencies...
call .venv\Scripts\activate.bat
pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
echo ============================================================
echo [OK] Environment setup completed!
echo ============================================================
goto run_app
