@echo off
echo ============================================
echo   Lead Scout - First Time Setup
echo ============================================
echo.
echo Installing required components...
echo This will take 2-3 minutes. Please wait.
echo.

python -m pip install -r requirements.txt
python -m playwright install chromium

echo.
echo ============================================
echo   Setup complete!
echo   You can now double-click "Run Lead Scout.bat"
echo   to open the app.
echo ============================================
echo.
pause
