@echo off
setlocal
cd /d "%~dp0"

set "CODEX_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CODEX_PYTHON%" (
  "%CODEX_PYTHON%" tools\article_studio.py %*
  exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 tools\article_studio.py %*
  exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
  python tools\article_studio.py %*
  exit /b %errorlevel%
)

echo Python was not found. Open this project in Codex and ask it to start the article studio.
pause
exit /b 1
