@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
py -3 -m venv .venv
if not errorlevel 1 goto install
python -m venv .venv
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 20 --retries 1 -r requirements.txt
if errorlevel 1 goto fail
echo Setup completed. Run launch.cmd.
pause
exit /b 0
:fail
echo Setup failed. Install Python 3.10 or later with tkinter, then retry.
pause
exit /b 1
