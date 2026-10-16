@echo off
chcp 65001 >nul
title V15Plus 上传到云端服务器

:: 云端配置从环境变量读取（见 .env.example），未设置时使用占位符
if not defined CLOUD_URL set CLOUD_URL=http://your-server.example.com/research
if not defined HYBRID_HMAC_SECRET set HYBRID_HMAC_SECRET=

echo ════════════════════════════════════════════
echo   V15Plus → 云服务器 自动上传
echo   目标: %CLOUD_URL%
echo ════════════════════════════════════════════
echo.

:: 检查依赖
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python
    pause
    exit /b 1
)

:: 执行全流程
echo [启动] V15Plus 全自动管线...
echo.

set PYTHONPATH=%cd%\_internal;%cd%\_plus_;%cd%

python scripts/v15plus_auto_pipeline.py ^
    --hybrid-url %CLOUD_URL% ^
    --phase close

echo.
echo ════════════════════════════════════════════
if errorlevel 0 (
    echo 完成！数据已上传到云服务器。
    echo 访问 %CLOUD_URL%/web/ 查看
) else (
    echo 执行过程有错误，请检查上方输出
)
echo ════════════════════════════════════════════
echo.
pause
