@echo off
chcp 65001 >nul
title V15Plus 全自动管线 - Hybrid Research System

echo ════════════════════════════════════════════
echo   V15Plus 全自动管线 v1.0
echo   本脚本完成以下操作：
echo   1. 读取自选股 (self_stock.txt)
echo   2. 跑 V15 个股评分 (逐只扫描)
echo   3. 跑板块动力评分
echo   4. 跑新闻蒸馏（如配置了 DeepSeek Key）
echo   5. 生成完整快照（含个股评分）
echo   6. 上传到 Hybrid Research System
echo   7. 触发融合评分 → Web面板有数据
echo ════════════════════════════════════════════
echo.

:: 检查依赖
echo [检查] Python 环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
)

pip list 2>nul | findstr "pandas" >nul
if %errorlevel% neq 0 (
    echo [安装] 安装 pandas...
    pip install pandas numpy
)

pip list 2>nul | findstr "requests" >nul
if %errorlevel% neq 0 (
    pip install requests
)

:: 检查 Hybrid 服务是否在运行
echo [检查] Hybrid Research System 服务状态...
python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" >nul 2>&1
if %errorlevel% neq 0 (
    echo [提示] Hybrid 服务未在 localhost:8000 上运行
    echo        上传步骤将被跳过，快照会保存到本地 data/cloud_outbox/
    echo.
    echo        要启动 Hybrid 服务：打开另一个终端窗口执行：
    echo        cd C:\Hybrid Intelligent Research System
    echo        set PYTHONPATH=src
    echo        python src\hybrid\api\server.py
    echo.
    set HYBRID_OFFLINE=1
) else (
    echo [OK] Hybrid 服务正在运行
    set HYBRID_OFFLINE=0
)

echo.
echo ════════════════════════════════════════════
echo  开始执行 V15Plus 全流程...
echo  自选股 410 只，预计耗时 3-8 分钟
echo ════════════════════════════════════════════
echo 注意：过程中如果看到 DeepSeek API 报错
echo       不影响核心评分流程，可忽略
echo.

:: 执行全流程
set PYTHONPATH=%cd%\_internal;%cd%\_plus_;%cd%

if "%HYBRID_OFFLINE%"=="1" (
    python scripts/v15plus_auto_pipeline.py --no-upload
) else (
    python scripts/v15plus_auto_pipeline.py
)

echo.
echo ════════════════════════════════════════════
if %errorlevel% equ 0 (
    echo  ✅ 全流程执行成功！
    echo.
    echo  下一步:
    if "%HYBRID_OFFLINE%"=="1" (
        echo   1. 打开 Hybrid 服务终端，启动服务
        echo   2. 然后重新运行本脚本
        echo   或执行: python _plus_\cloud_sync.py flush
    ) else (
        echo   打开浏览器访问 http://localhost:8000/web/
        echo   现在 Web 面板应该有数据了！
    )
) else (
    echo  ❌ 执行过程有错误，请检查上方输出
)
echo ════════════════════════════════════════════
echo.
pause
