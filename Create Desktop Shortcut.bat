@echo off
echo ContactPuller - Create Desktop Shortcut
echo =========================================
echo.

cd /d "%~dp0"

REM Generate logo/icon if not already present
if not exist "assets\logo.ico" (
    echo Generating app icon...
    python generate_logo.py
    echo.
)

REM Create the desktop shortcut
python _create_shortcut.py

echo.
pause
