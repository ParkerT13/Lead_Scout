@echo off
cd /d "%~dp0"
echo ============================================
echo   Bounce List Importer
echo ============================================
echo.
echo Drag and drop your bounce CSV from your email
echo tool into this window, then press Enter.
echo.
set /p CSVFILE="Bounce file path: "
echo.
set /p CAMPAIGN="Campaign name (optional, press Enter to skip): "
echo.
python _import_bounces.py %CSVFILE% "%CAMPAIGN%"
echo.
pause
