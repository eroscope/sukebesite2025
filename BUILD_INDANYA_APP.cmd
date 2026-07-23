@echo off
setlocal
cd /d "%~dp0"
set "CODEX_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%CODEX_PYTHON%" set "CODEX_PYTHON=python"
"%CODEX_PYTHON%" -m PyInstaller --noconfirm --clean IndanyaStudio.spec
if errorlevel 1 pause & exit /b 1
echo.
echo 完成: %CD%\dist\IndanyaStudio\IndanyaStudio.exe
pause
