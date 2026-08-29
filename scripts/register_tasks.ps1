# 注册 Windows 计划任务 - 本地模型记忆整理方案
# 以管理员权限运行此脚本
#
# 任务配置：
# - 每日 23:30  - daily
# - 每周日 23:00 - weekly
# - 每月 1 日 22:30 - monthly
# - 不管用户是否登录都要运行
# - 最高权限运行
# - 超时 2 小时
# - 允许按需手动运行

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $PSScriptRoot
if (-not $scriptRoot) { $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }

Write-Host "=== 注册计划任务 - 本地模型记忆整理 ===" -ForegroundColor Cyan
Write-Host ""

# ── 公共设置 ──
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

# ══════════════════════════════════════════
# 1. 每日任务 - 每天 23:30
# ══════════════════════════════════════════
$dailyName = "MemoryManager-Daily"
$dailyBat = Join-Path $scriptRoot "scripts\daily\run_daily.bat"
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At "23:30"
$dailyAction = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$dailyBat`"" `
    -WorkingDirectory (Split-Path $dailyBat)

try {
    Unregister-ScheduledTask -TaskName $dailyName -Confirm:$false -ErrorAction SilentlyContinue
} catch {}

Register-ScheduledTask `
    -TaskName $dailyName `
    -Description "每日记忆整理：会话chunking → 本地模型摘要 → 云端进化 USER/MEMORY/AGENTS" `
    -Trigger $dailyTrigger `
    -Action $dailyAction `
    -Principal $principal `
    -Settings $settings

Write-Host "✅ 已注册: $dailyName (每天 23:30)" -ForegroundColor Green

# ══════════════════════════════════════════
# 2. 每周任务 - 每周日 23:00
# ══════════════════════════════════════════
$weeklyName = "MemoryManager-Weekly"
$weeklyBat = Join-Path $scriptRoot "scripts\weekly\run_weekly.bat"
$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "23:00"
$weeklyAction = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$weeklyBat`"" `
    -WorkingDirectory (Split-Path $weeklyBat)

try {
    Unregister-ScheduledTask -TaskName $weeklyName -Confirm:$false -ErrorAction SilentlyContinue
} catch {}

Register-ScheduledTask `
    -TaskName $weeklyName `
    -Description "每周记忆整理：近7天摘要合并 → 本地模型周度摘要 → 云端进化" `
    -Trigger $weeklyTrigger `
    -Action $weeklyAction `
    -Principal $principal `
    -Settings $settings

Write-Host "✅ 已注册: $weeklyName (每周日 23:00)" -ForegroundColor Green

# ══════════════════════════════════════════
# 3. 每月任务 - 每月 1 日 22:30
# ══════════════════════════════════════════
$monthlyName = "MemoryManager-Monthly"
$monthlyBat = Join-Path $scriptRoot "scripts\monthly\run_monthly.bat"
# 每月触发器
$monthlyTrigger = New-ScheduledTaskTrigger -Daily -At "22:30"
# 通过脚本自身逻辑判断是否为每月1日（Task Scheduler 不直接支持每月触发）
# 或者使用 CIM 触发器
$monthlyTriggerCim = New-CimInstance -CimClass (Get-CimClass -Namespace "Root/Microsoft/Windows/TaskScheduler" -ClassName "MSFT_TaskEventTrigger") -ClientOnly
# 改用 AtStartup 方式，由脚本内部判断日期

# 简化方案：使用 Weekly 触发器 + 脚本内判断
# 但更好的方案是直接用 schtasks 命令创建月度触发器
# 先用 PowerShell 的 Register-ScheduledTask 配合自定义 XML

# 使用每日触发 + 脚本内判断每月1日
$monthlyAction = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$monthlyBat`"" `
    -WorkingDirectory (Split-Path $monthlyBat)

try {
    Unregister-ScheduledTask -TaskName $monthlyName -Confirm:$false -ErrorAction SilentlyContinue
} catch {}

Register-ScheduledTask `
    -TaskName $monthlyName `
    -Description "每月记忆整理：上月weekly摘要合并 → 本地模型月度摘要 → 云端进化（每月1日执行）" `
    -Trigger $monthlyTrigger `
    -Action $monthlyAction `
    -Principal $principal `
    -Settings $settings

Write-Host "✅ 已注册: $monthlyName (每天 22:30，脚本内部判断每月1日)" -ForegroundColor Green

Write-Host ""
Write-Host "=== 注册完成 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "注意：" -ForegroundColor Yellow
Write-Host "  - 每月任务注册为每天22:30触发，由脚本内部判断是否为每月1日" -ForegroundColor Yellow
Write-Host "  - 任务允许按需手动运行：在任务计划程序中右键 → 运行" -ForegroundColor Yellow
Write-Host "  - 日志位置：$scriptRoot\run_log\" -ForegroundColor Yellow

