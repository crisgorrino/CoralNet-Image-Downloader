@echo off
setlocal
cd /d "%~dp0"
title CoralNet Image Downloader

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -c "import tkinter" >nul 2>nul
    if %errorlevel%==0 (
        py -3 "%~dp0coralnet_image_downloader_gui.py"
        exit /b %errorlevel%
    )
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -c "import tkinter" >nul 2>nul
    if %errorlevel%==0 (
        python "%~dp0coralnet_image_downloader_gui.py"
        exit /b %errorlevel%
    )
)

echo.
echo Python 3 with Tkinter was not found.
echo Install Python 3 from https://www.python.org/downloads/
echo During installation, enable "Add Python to PATH".
echo.
pause
exit /b 1
