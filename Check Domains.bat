@echo off
cd /d "%~dp0"
echo ============================================
echo   Domain + Pattern Checker
echo ============================================
echo.
echo Drag and drop your contacts CSV into this
echo window, then press Enter.
echo.
set /p CSVFILE="CSV file path: "
echo.
python _check_domains.py %CSVFILE%
echo.
pause
