@echo off
cd /d "%~dp0"
echo ============================================
echo   Email Generator
echo ============================================
echo.
echo Drag and drop your verified contacts CSV
echo into this window, then press Enter.
echo.
set /p CSVFILE="CSV file path: "
echo.
echo Generating emails...
echo.
python _generate_emails.py %CSVFILE%
echo.
echo Output saved next to your input file with "_enriched" added to the name.
echo.
pause
