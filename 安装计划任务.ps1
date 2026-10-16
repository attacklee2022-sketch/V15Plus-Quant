# V15Plus 计划任务安装脚本
# 以管理员身份运行：右键 -> 用 PowerShell 以管理员身份运行

$V15_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$TASKS_DIR = "$V15_DIR\tasks"

Write-Host "════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  V15Plus 计划任务安装" -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "V15Plus 目录: $V15_DIR"
Write-Host "任务脚本目录: $TASKS_DIR"
Write-Host ""

# 确认管理员权限
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Write-Host "[警告] 没有管理员权限，建议右键 -> 用 PowerShell 以管理员身份运行" -ForegroundColor Yellow
    Write-Host ""
}

# ══════════════════════════════════════════════════════
# 任务 1: K 线监控 + 收盘自动扫描
# ══════════════════════════════════════════════════════
Write-Host "────────────────────────────────────────────" -ForegroundColor Cyan
Write-Host "  [任务 1] K 线监控 + 收盘自动扫描" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────────" -ForegroundColor Cyan
Write-Host "  说明: 15:30 后每 15 分钟检查通达信数据是否更新"
Write-Host "       检测到今日 K 线后自动跑 V15Plus 全流程并上传"
Write-Host ""

try {
    $task1Name = "V15Plus K线监控+收盘扫描"
    Unregister-ScheduledTask -TaskName $task1Name -Confirm:$false -ErrorAction SilentlyContinue

    $action1 = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$TASKS_DIR\kline_watch_task.bat`""
    $trigger1 = New-ScheduledTaskTrigger -Daily -At 15:30
    $settings1 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $task1Name -Trigger $trigger1 -Action $action1 -Settings $settings1 -RunLevel Limited -Force
    Write-Host "  [OK] 已安装: $task1Name" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] $task1Name : $_" -ForegroundColor Red
}

Write-Host ""

# ══════════════════════════════════════════════════════
# 任务 2: 实盘定时新闻上传
# ══════════════════════════════════════════════════════
Write-Host "────────────────────────────────────────────" -ForegroundColor Cyan
Write-Host "  [任务 2] 实盘定时新闻上传" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────────" -ForegroundColor Cyan
Write-Host "  说明: 配合云端采集节奏提前上传新闻"
Write-Host "       云端采集: 09:35 10:30 11:35 14:00 14:35 15:10"
Write-Host "       本地上传: 09:25 10:20 11:25 13:50 14:25 15:00"
Write-Host ""

try {
    $task2Name = "V15Plus 实盘新闻上传"
    Unregister-ScheduledTask -TaskName $task2Name -Confirm:$false -ErrorAction SilentlyContinue

    $action2 = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$TASKS_DIR\news_upload_task.bat`""

    $triggers2 = @(
        (New-ScheduledTaskTrigger -Daily -At 09:25),
        (New-ScheduledTaskTrigger -Daily -At 10:20),
        (New-ScheduledTaskTrigger -Daily -At 11:25),
        (New-ScheduledTaskTrigger -Daily -At 13:50),
        (New-ScheduledTaskTrigger -Daily -At 14:25),
        (New-ScheduledTaskTrigger -Daily -At 15:00)
    )
    $settings2 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $task2Name -Trigger $triggers2 -Action $action2 -Settings $settings2 -RunLevel Limited -Force
    Write-Host "  [OK] 已安装: $task2Name" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] $task2Name : $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  安装完成！" -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "任务列表:"
Write-Host "  [1] V15Plus K线监控+收盘扫描 -> 每天 15:30 启动监控"
Write-Host "  [2] V15Plus 实盘新闻上传 -> 09:25 10:20 11:25 13:50 14:25 15:00"
Write-Host ""
pause
