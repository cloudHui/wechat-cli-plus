@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

where python >nul 2>&1
if %errorlevel% neq 0 goto no_python

if not exist ".venv\Scripts\python.exe" goto init_venv

.venv\Scripts\python.exe -c "import customtkinter" >nul 2>&1
if %errorlevel% neq 0 (
    echo [提示] 正在安装 CustomTkinter 界面依赖...
    .venv\Scripts\pip.exe install customtkinter -i https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
)

:run_app
if /i "%1"=="build" goto do_build
if /i "%1"=="--build" goto do_build
if /i "%1"=="cli" goto do_cli
if /i "%1"=="--cli" goto do_cli
if /i "%1"=="run" goto do_run

REM 启动桌面端界面后，自动秒关当前黑框控制台
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0gui.py"
exit

:do_cli
call .venv\Scripts\python.exe scripts\menu.py
exit /b 0

:do_run
call .venv\Scripts\python.exe auto_summary.py
exit /b 0

:do_build
if exist "打包exe_Nuitka.bat" (
    call 打包exe_Nuitka.bat
)
exit /b 0

:no_python
echo ============================================================
echo [错误] 未在电脑上检测到 Python 环境。
echo 请先安装 Python 3.10+ 并勾选 'Add python.exe to PATH'。
echo ============================================================
pause
exit /b 1

:init_venv
echo ============================================================
echo [初始化] 正在配置 Python 虚拟环境 (.venv)...
echo ============================================================
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
python -m venv .venv
call .venv\Scripts\activate.bat
pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install customtkinter -i https://pypi.tuna.tsinghua.edu.cn/simple
goto run_app