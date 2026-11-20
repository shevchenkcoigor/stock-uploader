@echo off
rem Builds the desktop app on Windows. Usage: install.bat [--zip] [--no-smoke]
cd /d "%~dp0"

py -3.12 --version >nul 2>&1
if not errorlevel 1 goto use_launcher
python --version >nul 2>&1
if not errorlevel 1 goto use_python
echo Python 3.12 or newer is required. Install it from https://www.python.org/downloads/
exit /b 1

:use_launcher
py -3.12 packaging\build.py %*
exit /b %errorlevel%

:use_python
python packaging\build.py %*
exit /b %errorlevel%
