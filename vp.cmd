@echo off
rem Runs the video pipeline, for example: vp run ftx   (in PowerShell type .\vp run ftx)
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%;%PYTHONPATH%"
set "PYTHONUTF8=1"
rem Use the Python environment that setup.cmd made; fall back to the py launcher or python on PATH.
set "PY=%HERE%.venv\Scripts\python.exe"
set "PYARG="
if exist "%PY%" goto run
set "PY=py"
set "PYARG=-3"
where py >nul 2>nul && goto run
set "PY=python"
set "PYARG="
:run
"%PY%" %PYARG% -m vidpipe %*
endlocal & exit /b %ERRORLEVEL%
