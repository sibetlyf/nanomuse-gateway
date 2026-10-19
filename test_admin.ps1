# ==============================================================================
# nanoMuse Gateway - admin 账号设备测试与 DSH 调试脚本 (PowerShell)
# ==============================================================================
param (
    [string]$Gateway = "http://127.0.0.1:8790",
    [string]$User = "admin",
    [string]$Password = "admin123",
    [string]$Action = "all",
    [string]$Prompt = ""
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$cmd = "uv run --no-project --python 3.12 --with websockets python test_admin_devices.py --gateway $Gateway --user $User --password $Password --action $Action"
if ($Prompt) {
    $cmd += " --prompt `"$Prompt`""
}

Invoke-Expression $cmd
