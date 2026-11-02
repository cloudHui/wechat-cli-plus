@echo off
chcp 65001 >nul
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未检测到 Python 虚拟环境 (.venv)！
    pause
    exit /b 1
)

tasklist /fi "imagename eq Weixin.exe" 2>nul | find /i "Weixin.exe" >nul
if %errorlevel% neq 0 (
    echo [提示] 检测到电脑端微信 (Weixin.exe) 尚未运行！
    echo 请先打开电脑微信并登录，登录完成后按任意键继续...
    pause >nul
)

echo.
echo 正在扫描微信内存并提取数据库解密密钥 (约 8~10 秒)...
echo ------------------------------------------------------------
set "PYTHONIOENCODING=utf-8"
".venv\Scripts\python.exe" entry.py init

if %errorlevel% equ 0 (
    echo.
    echo ------------------------------------------------------------
    echo [成功] 微信解密密钥提取完毕，环境已就绪！
    echo ------------------------------------------------------------
) else (
    echo.
    echo ------------------------------------------------------------
    echo [失败] 提取密钥异常，请确认微信已登录或尝试以管理员身份运行。
    echo ------------------------------------------------------------
)
