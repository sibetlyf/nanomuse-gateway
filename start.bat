@echo off
chcp 65001 >nul
title nanoMuse Gateway
cd /d "%~dp0"

echo ==========================================================
echo         nanoMuse Gateway 本地启动脚本 (Windows CMD)
echo ==========================================================

where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未检测到 uv 环境！
    echo 请在 PowerShell 中执行以下命令安装 uv:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo.
    pause
    exit /b 1
)

set PORT=8790
set HOST=0.0.0.0
set GATEWAY_DATA_DIR=%~dp0data

if not exist "%GATEWAY_DATA_DIR%" mkdir "%GATEWAY_DATA_DIR%"

echo [检查] 正在检查端口 %PORT% 占用状态...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    echo [提示] 检测到端口 %PORT% 被进程 PID %%a 占用，正在自动释放...
    taskkill /F /PID %%a >nul 2>nul
)

echo [启动中] 正在通过 uv 启动网关服务...
echo   ^> 访问地址: http://127.0.0.1:%PORT%
echo   ^> 健康检查: http://127.0.0.1:%PORT%/v1/gateway/status
echo.
echo 按 Ctrl + C 可停止运行。
echo ----------------------------------------------------------

uv run --no-project --python 3.12 --with fastapi --with "uvicorn[standard]" --with websockets python gateway.py
pause
