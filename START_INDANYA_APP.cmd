@echo off
setlocal
cd /d "%~dp0"

if exist "%CD%\dist\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

set "CODEX_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CODEX_PYTHON%" (
  start "" "%CODEX_PYTHON%" tools\indanya_desktop_app.py --site-root "%CD%" %*
  exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
  start "" py -3 tools\indanya_desktop_app.py --site-root "%CD%" %*
  exit /b 0
)

echo Pythonが見つかりません。Codexから一度アプリを起動してください。
pause
exit /b 1
