@echo off
cd /d "%~dp0"
echo ============================================
echo   Full Email Pipeline
echo ============================================
echo.
echo Drag and drop your contacts CSV into this
echo window, then press Enter.
echo.
set /p CSVFILE="CSV file path: "
echo.
echo Running full pipeline...
echo   - Finding domains
echo   - Probing email formats
echo   - Generating and verifying all emails
echo   - Saving viable contacts
echo.
python _pipeline.py %CSVFILE%
echo.
pause
