@echo off
chcp 65001 >nul
cd /d "%~dp0.."

echo ============================================================
echo           微信工具全痕迹安全销毁向导
echo ============================================================
echo 警告：此操作将彻底删除所有微信解密密钥及缓存！
echo.
set /p CONFIRM="确认现在彻底销毁隐私数据吗？(输入 Y 确认，其他任意键取消): "
if /i "%CONFIRM%" neq "Y" (
    echo.
    echo [已取消] 未做任何修改。
    exit /b 0
)

echo.
set "USER_WECHAT_DIR=%USERPROFILE%\.wechat-cli"
if exist "%USER_WECHAT_DIR%" (
    echo [+] 正在删除密钥目录: %USER_WECHAT_DIR% ...
    rmdir /s /q "%USER_WECHAT_DIR%"
    echo [OK] 微信密钥已彻底删除。
)

if exist "last_sync.json" (
    del /f /q "last_sync.json"
    echo [OK] 同步状态已删除。
)

schtasks /delete /tn "微信群定时增量总结" /f >nul 2>&1

echo.
set /p DEL_SUMMARIES="是否连同历史生成的每日总结文档 (summaries 目录) 一起删除？(Y/N): "
if /i "%DEL_SUMMARIES%" equ "Y" (
    if exist "summaries" (
        rmdir /s /q "summaries"
        echo [OK] 历史群总结文档已彻底删除。
    )
)

echo.
set /p DEL_VENV="是否连同本项目的 Python 虚拟环境 (.venv) 一起删除？(Y/N): "
if /i "%DEL_VENV%" equ "Y" (
    if exist ".venv" (
        rmdir /s /q ".venv"
        echo [OK] Python 虚拟环境已彻底删除。
    )
)

echo.
echo ------------------------------------------------------------
echo [完成] 所有微信隐私数据及临时文件已彻底清理完毕！
echo ------------------------------------------------------------
