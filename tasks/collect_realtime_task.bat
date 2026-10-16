@echo off
cd /d "%~dp0.."
set PYTHONPATH=%cd%\_internal;%cd%\_plus_;%cd%
set NPX_CMD=C:\Program Files\nodejs\npx.cmd
python scripts/collect_realtime.py %*
