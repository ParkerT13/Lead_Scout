@echo off
cd /d "%~dp0"
echo ============================================
echo   Lead Scout Autopilot  (free, unattended)
echo ============================================
echo.
echo Finds contacts AND emails for every company you give it.
echo   - Free sources only. Nothing is ever billed.
echo   - Safe to close and re-run: it resumes where it stopped.
echo   - Results: %USERPROFILE%\LeadScout_Output\autopilot_*.csv
echo.
echo Drag a text file (one company per line) onto this window and press Enter,
echo or just press Enter to use every company already in your master list.
echo.
set /p LISTFILE="Company list file (optional): "
echo.
if "%LISTFILE%"=="" (
    py -3 autopilot.py --from-master
) else (
    py -3 autopilot.py %LISTFILE%
)
echo.
pause
