@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Indanya ChatGPT Worker Setup

echo ========================================
echo  Indanya ChatGPT Worker - Initial Setup
echo ========================================
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if %errorlevel%==0 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if %errorlevel%==0 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo [ERROR] Python was not found.
  echo Install Python 3.11 or later, then run this file again.
  pause
  exit /b 1
)

where git >nul 2>nul
if not %errorlevel%==0 (
  echo [ERROR] Git was not found.
  echo Git is required to publish review data and articles.
  pause
  exit /b 1
)

set "VENV=.article-studio\chatgpt-worker-venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo [1/4] Creating a private Python environment...
  %PYTHON_CMD% -m venv "%VENV%"
  if not %errorlevel%==0 goto :failed
)

set "VPY=%VENV%\Scripts\python.exe"
echo [2/4] Installing browser automation components...
"%VPY%" -m pip install --upgrade pip
if not %errorlevel%==0 goto :failed
"%VPY%" -m pip install playwright pillow
if not %errorlevel%==0 goto :failed

echo [3/4] Saving the Apps Script connection settings...
"%VPY%" tools\chatgpt_worker.py --configure
if not %errorlevel%==0 goto :failed

echo [4/4] Setup completed.
echo.
echo Next, replace the Apps Script code with the supplied completed code,
echo deploy a new version, and then run START_CHATGPT_WORKER.cmd.
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Setup failed. The window will stay open.
echo Log folder: .article-studio\chatgpt-worker
pause
exit /b 1
