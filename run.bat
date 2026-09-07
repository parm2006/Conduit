@echo off
setlocal
cd /d "%~dp0"
echo Setting up Conduit Environment...

set "CONDUIT_SETUP_NEEDED=0"
if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 goto failed
    set "CONDUIT_SETUP_NEEDED=1"
)

if /I "%~1"=="--test" if "%CONDUIT_SETUP_NEEDED%"=="0" goto tests
if /I "%~1"=="--smoke-test" if "%CONDUIT_SETUP_NEEDED%"=="0" goto smoke

echo Installing requirements...
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
if /I "%~1"=="--test" goto tests
if /I "%~1"=="--smoke-test" goto smoke

echo Starting Conduit...
venv\Scripts\python.exe run.py
if errorlevel 1 goto failed
pause
exit /b 0

:tests
venv\Scripts\python.exe -m unittest discover -s tests
exit /b %errorlevel%

:smoke
venv\Scripts\python.exe scripts\smoke_remote_ui.py
exit /b %errorlevel%

:failed
echo Conduit could not start. See the error above.
pause
exit /b 1
