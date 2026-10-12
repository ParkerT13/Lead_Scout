@echo off
cd /d "%~dp0"
echo ============================================
echo   Schedule Lead Scout Autopilot (weekly)
echo ============================================
echo.
echo This creates a Windows Task Scheduler job that runs Autopilot on every
echo company in your master list each Sunday at 2:00 AM, free of charge.
echo Each run reports who is NEW since the last one (autopilot_master_new_DATE.csv).
echo.
echo To remove it later:  schtasks /delete /tn "LeadScout Autopilot" /f
echo.
pause
schtasks /create /tn "LeadScout Autopilot" /sc weekly /d SUN /st 02:00 /tr "cmd /c cd /d \"%~dp0\" && py -3 autopilot.py --from-master" /f
echo.
pause
