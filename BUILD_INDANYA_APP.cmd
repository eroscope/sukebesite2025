@echo off
setlocal
cd /d "%~dp0"
set "BUILD_PYTHON=%CD%\.article-studio\chatgpt-worker-venv\Scripts\python.exe"
if not exist "%BUILD_PYTHON%" set "BUILD_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%BUILD_PYTHON%" set "BUILD_PYTHON=python"
"%BUILD_PYTHON%" -m PyInstaller --noconfirm --clean --distpath "%CD%\dist-growth-v33" --workpath "%CD%\build-growth-v33" IndanyaStudio.spec
if errorlevel 1 pause & exit /b 1
echo.
echo 完成: %CD%\dist-growth-v33\IndanyaStudio\IndanyaStudio.exe
pause
