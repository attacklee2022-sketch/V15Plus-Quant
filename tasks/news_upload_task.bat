@echo off
cd /d "%~dp0.."
set PYTHONPATH=%cd%\_internal;%cd%\_plus_;%cd%
python scripts/upload_news.py %*
