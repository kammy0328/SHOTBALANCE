@echo off
rem Balance launcher (runs from source). First run creates .venv next to this file.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [Balance] First run: preparing the Python environment. This takes a minute.
  py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv 2>nul || py -3.10 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" goto :nopython
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)
".venv\Scripts\python.exe" tools\get_models.py
start "" ".venv\Scripts\pythonw.exe" -m balance %*
exit /b 0
:nopython
echo Python 3.10 - 3.12 (64-bit) was not found. Install it from python.org and run this again.
pause
exit /b 1
:fail
echo Installing the requirements failed. See the messages above.
pause
exit /b 1
