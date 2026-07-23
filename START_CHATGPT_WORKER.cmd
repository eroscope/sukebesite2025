@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Indanya ChatGPT Worker
set "VPY=.article-studio\chatgpt-worker-venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo Setup has not been completed.
  echo Run SETUP_CHATGPT_WORKER.cmd first.
  pause
  exit /b 1
)
"%VPY%" tools\chatgpt_worker.py
set "CODE=%errorlevel%"
echo.
echo Worker stopped. Exit code: %CODE%
pause
exit /b %CODE%
