@echo off
setlocal
chcp 65001 >nul
title Plexus
cd /d "%~dp0"
if errorlevel 1 goto directory_error

py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 goto use_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 goto use_python
echo Python 3.10 or newer was not found.
echo Install Python from https://www.python.org/downloads/ and try again.
goto failed

:use_py
py -3 -X utf8 "%~dp0server.py" --allow-origin https://therookiegit.github.io %*
if errorlevel 1 goto failed
exit /b 0

:use_python
python -X utf8 "%~dp0server.py" --allow-origin https://therookiegit.github.io %*
if errorlevel 1 goto failed
exit /b 0

:directory_error
echo Cannot open the application folder: "%~dp0"

:failed
echo.
echo Plexus could not start. The error is shown above.
echo Keep this window open when reporting the problem.
pause
exit /b 1
