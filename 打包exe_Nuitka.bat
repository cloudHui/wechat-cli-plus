@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo ============================================================
echo         微信 AI 总结助手 - Nuitka 机器码安全编译工具
echo ============================================================
echo 目标：将 Python 源码翻译为 C/C++ 机器码并编译为原生 .exe
echo 效果：彻底防御 pyinstxtractor 解包与逆向，严格保护核心源码！
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未检测到 .venv 虚拟环境，请先运行 微信助手.bat 进行初始化。
    pause
    exit /b 1
)

.venv\Scripts\python.exe -c "import nuitka" >nul 2>&1
if %errorlevel% neq 0 (
    echo [+] 正在虚拟环境中安装 Nuitka 编译器...
    .venv\Scripts\pip.exe install nuitka -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo.
echo 请选择编译模式：
echo   [1] 生产发布版 (隐藏黑框控制台，界面纯净，推荐)
echo   [2] 调试测试版 (保留控制台黑框，方便查看实时报错)
echo.
set /p mode="请选择 [1 或 2，默认 1]: "
if "%mode%"=="" set mode=1

set CONSOLE_OPT=--windows-console-mode=disable
if "%mode%"=="2" set CONSOLE_OPT=--windows-console-mode=force

echo.
echo ============================================================
echo 开始执行 Nuitka 深度编译 (约需 2~5 分钟，请耐心等待)...
echo ============================================================

.venv\Scripts\python.exe -m nuitka ^
    --standalone ^
    --assume-yes-for-downloads ^
    --enable-plugin=tk-inter ^
    --include-package-data=customtkinter ^
    --include-package-data=wechat_cli ^
    --windows-icon-from-ico=app.ico ^
    %CONSOLE_OPT% ^
    --output-dir=dist ^
    --output-filename=微信AI助手.exe ^
    gui.py

if %errorlevel% neq 0 (
    echo.
    echo ============================================================
    echo [编译失败] 请查看上方日志中的具体报错原因。
    echo ============================================================
    pause
    exit /b 1
)

echo.
echo ============================================================
echo [编译成功！] 原生机器码程序已生成至：
echo %~dp0dist\gui.dist\微信AI助手.exe
echo.
echo 提示：
echo 1. 该程序已被彻底翻译为 C 语言原生二进制，pyinstxtractor 无法解包！
echo 2. 分发给其他人时，只需将整个 dist\gui.dist 文件夹压缩发给对方即可。
echo ============================================================
pause