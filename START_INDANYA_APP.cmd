@echo off
setlocal
cd /d "%~dp0"

if exist "%CD%\dist-growth-v33\IndanyaStudio\IndanyaStudio.exe" (
  powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%CD%\tools\indanya_watchdog.ps1" -Executable "%CD%\dist-growth-v33\IndanyaStudio\IndanyaStudio.exe" -SiteRoot "%CD%" -Show
  exit /b 0
)

if exist "%CD%\dist-growth-v32\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-growth-v32\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-growth-v31\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-growth-v31\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-growth-v30\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-growth-v30\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-growth-v29\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-growth-v29\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-growth-v28\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-growth-v28\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-growth-v27\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-growth-v27\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-growth-v26\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-growth-v26\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-growth-v25\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-growth-v25\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-outreach-v24\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-outreach-v24\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-outreach-v22\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-outreach-v22\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-source-mix-v16\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-source-mix-v16\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

if exist "%CD%\dist-x-growth-v15\IndanyaStudio\IndanyaStudio.exe" (
  start "" "%CD%\dist-x-growth-v15\IndanyaStudio\IndanyaStudio.exe" --site-root "%CD%" %*
  exit /b 0
)

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
