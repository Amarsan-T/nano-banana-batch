@echo off
setlocal

cd /d "%~dp0"
if errorlevel 1 (
    echo ERROR: Could not open the Nano Banana Batch project directory.
    pause
    exit /b 1
)

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo ERROR: Missing .venv\Scripts\python.exe. Set up the project environment first.
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%~dp0app.py" launcher
set "LAUNCHER_EXIT=%ERRORLEVEL%"

if not "%LAUNCHER_EXIT%"=="0" (
    echo.
    echo Launcher exited with code %LAUNCHER_EXIT%.
    pause
)

exit /b %LAUNCHER_EXIT%
