param()

$V15_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$TASKS_DIR = "$V15_DIR\tasks"

Write-Host "========================================"
Write-Host "  V15Plus Scheduled Tasks Installer"
Write-Host "========================================"
Write-Host ""

# --- Task 1: Real-time Market Data Collect (bb-browser) ---
Write-Host "[1/3] Installing Market Data Collect (bb-browser)..."

try {
    Unregister-ScheduledTask -TaskName "V15Plus Market Collect" -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$TASKS_DIR\collect_realtime_task.bat`""

    $triggers = @(
        (New-ScheduledTaskTrigger -Daily -At 09:40),
        (New-ScheduledTaskTrigger -Daily -At 10:50),
        (New-ScheduledTaskTrigger -Daily -At 11:35),
        (New-ScheduledTaskTrigger -Daily -At 13:25),
        (New-ScheduledTaskTrigger -Daily -At 14:25),
        (New-ScheduledTaskTrigger -Daily -At 15:10)
    )
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "V15Plus Market Collect" -Trigger $triggers -Action $action -Settings $settings -RunLevel Limited -Force

    Write-Host "  [OK] V15Plus Market Collect" -ForegroundColor Green
    Write-Host "       Runs at 09:40, 10:50, 11:35, 13:25, 14:25, 15:10"
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
}

Write-Host ""

# --- Task 2: Real-time News Upload ---
Write-Host "[2/3] Installing News Upload..."

try {
    Unregister-ScheduledTask -TaskName "V15Plus News Upload" -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$TASKS_DIR\news_upload_task.bat`""

    $triggers = @(
        (New-ScheduledTaskTrigger -Daily -At 09:40),
        (New-ScheduledTaskTrigger -Daily -At 10:50),
        (New-ScheduledTaskTrigger -Daily -At 11:35),
        (New-ScheduledTaskTrigger -Daily -At 13:25),
        (New-ScheduledTaskTrigger -Daily -At 14:25),
        (New-ScheduledTaskTrigger -Daily -At 15:10)
    )
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "V15Plus News Upload" -Trigger $triggers -Action $action -Settings $settings -RunLevel Limited -Force

    Write-Host "  [OK] V15Plus News Upload" -ForegroundColor Green
    Write-Host "       Runs at 09:40, 10:50, 11:35, 13:25, 14:25, 15:10 (sync with bb-browser)"
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
}

Write-Host ""

# --- Task 3: Kline Watcher + Close Scan ---
Write-Host "[3/3] Installing Kline Watcher + Close Scan..."

try {
    Unregister-ScheduledTask -TaskName "V15Plus Kline Watcher" -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$TASKS_DIR\kline_watch_task.bat`""
    $trigger = New-ScheduledTaskTrigger -Daily -At 15:30
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "V15Plus Kline Watcher" -Trigger $trigger -Action $action -Settings $settings -RunLevel Limited -Force

    Write-Host "  [OK] V15Plus Kline Watcher" -ForegroundColor Green
    Write-Host "       Runs daily at 15:30"
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================"
Write-Host "  Done!" -ForegroundColor Green
Write-Host "========================================"
Write-Host ""
Write-Host "Daily Schedule:"
Write-Host ""
Write-Host "  09:40  News Upload + Market Collect (bb-browser)"
Write-Host "  ------------- 09:50  market-bot Push"
Write-Host "  10:50  News Upload + Market Collect (bb-browser)"
Write-Host "  ------------- 11:00  market-bot Push"
Write-Host "  11:35  News Upload + Market Collect (bb-browser)"
Write-Host "  ------------- 11:45  market-bot Push"
Write-Host "  13:25  News Upload + Market Collect (bb-browser)"
Write-Host "  ------------- 13:35  market-bot Push"
Write-Host "  14:25  News Upload + Market Collect (bb-browser)"
Write-Host "  ------------- 14:35  market-bot Push"
Write-Host "  15:10  News Upload + Market Collect (bb-browser)"
Write-Host "  ------------- 15:20  market-bot Push (close)"
Write-Host "  15:30  Kline Watcher → V15 Full Scan"
Write-Host ""
pause
