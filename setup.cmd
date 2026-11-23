@echo off
rem Double-click to install or repair the video pipeline. Safe to run again: it only does what is missing.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (echo Setup OK. You can close this window.) else (echo Setup found problems; see the messages above.)
pause
endlocal & exit /b %RC%
