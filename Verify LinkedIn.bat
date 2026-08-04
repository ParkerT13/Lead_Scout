@echo off
cd /d "%~dp0"
echo ============================================
echo   LinkedIn Employment Verifier
echo ============================================
echo.
echo Drag and drop your contacts CSV file into
echo this window, then press Enter.
echo.
set /p CSVFILE="CSV file path: "
echo.
echo Starting verification... A Chrome window will open.
echo Log into LinkedIn if prompted (only needed once).
echo.
python _verify_linkedin_browser.py %CSVFILE%
echo.
pause
