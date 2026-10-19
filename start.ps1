# ==============================================================================
# nanoMuse Gateway 本地启动脚本 (Windows PowerShell)
# 运行环境: uv
# 用法:
#   .\start.ps1                (默认端口: 8790)
#   .\start.ps1 -Port 8790     (指定端口)
# ==============================================================================

param (
    [int]$Port = 8790,
    [string]$HostIP = "0.0.0.0"
)

# 确保进入脚本所在目录
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "         nanoMuse Gateway 本地网关启动器 (UV 环境)         " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. 检查 uv 是否安装
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[错误] 未检测到 uv 环境！" -ForegroundColor Red
    Write-Host "请先在 PowerShell 中安装 uv:" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`"" -ForegroundColor Green
    Write-Host ""
    Write-Host "安装完成后，请重新打开终端并运行本脚本。" -ForegroundColor Yellow
    Exit 1
}

# 2. 准备运行环境变量
$env:PORT = $Port
$env:HOST = $HostIP
$env:GATEWAY_DATA_DIR = "$ScriptDir\data"

# 确保 data 目录存在
if (-not (Test-Path "$ScriptDir\data")) {
    New-Item -ItemType Directory -Path "$ScriptDir\data" -Force | Out-Null
}

# 3. 端口占用检测与自动释放
$occupied = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($occupied) {
    $pids = $occupied.OwningProcess | Sort-Object -Unique
    foreach ($p in $pids) {
        if ($p -gt 0) {
            $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
            $procName = if ($proc) { $proc.ProcessName } else { "未知进程" }
            Write-Host "[提示] 检测到端口 $Port 被占用 (PID: $p, 进程: $procName)，正在自动释放端口..." -ForegroundColor Yellow
            try {
                Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
                Write-Host "  > 已终止占用进程 (PID: $p)" -ForegroundColor Green
            } catch {
                Write-Host "  > 终止进程 (PID: $p) 失败: $_" -ForegroundColor Red
            }
        }
    }
    Start-Sleep -Milliseconds 600
}

Write-Host "[1/2] 正在检查依赖并启动网关服务..." -ForegroundColor Gray
Write-Host "  > 监听地址: http://$($HostIP):$($Port)" -ForegroundColor DarkCyan
Write-Host "  > 本地测试: http://127.0.0.1:$($Port)/v1/gateway/status" -ForegroundColor DarkCyan
Write-Host "  > 数据目录: $env:GATEWAY_DATA_DIR" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "按 Ctrl + C 可随时停止服务。" -ForegroundColor DarkGray
Write-Host "----------------------------------------------------------" -ForegroundColor DarkGray

# 4. 通过 uv 启动 FastAPI 服务 (使用稳定的 Python 3.12 环境)
uv run --no-project --python 3.12 --with fastapi --with 'uvicorn[standard]' --with websockets python gateway.py
