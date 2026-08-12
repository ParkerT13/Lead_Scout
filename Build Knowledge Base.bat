@echo off
echo Build HubSpot Knowledge Base
echo ==============================
echo Drag and drop your HubSpot contacts export CSV onto this file,
echo or run: python _build_kb.py path\to\hubspot_contacts.csv
echo.
if "%~1"=="" (
    echo No file provided. Drag a CSV onto this batch file.
    pause
    exit /b 1
)
python "%~dp0_build_kb.py" "%~1"
echo.
pause
