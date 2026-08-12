@echo off
echo Confirm Working Email Pattern
echo ==============================
echo.
echo This records a confirmed working email pattern for a company.
echo Use after an email has been verified to actually reach someone.
echo.
set /p COMPANY="Company name (e.g. Scout Energy Partners): "
set /p EMAIL="Confirmed email address (e.g. travis.moreland@scoutep.com): "
set /p FIRST="Contact first name (e.g. Travis): "
set /p LAST="Contact last name (e.g. Moreland): "
echo.
python "%~dp0_confirm_pattern.py" "%COMPANY%" "%EMAIL%" "%FIRST%" "%LAST%"
echo.
pause
