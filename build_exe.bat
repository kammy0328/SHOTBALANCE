@echo off
rem Builds dist\ShotBalance.exe (single file, no Python needed on the target PC).
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv 2>nul || py -3.10 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || goto :fail
".venv\Scripts\python.exe" tools\get_models.py || goto :fail
".venv\Scripts\python.exe" -m PyInstaller packaging\balance.spec --noconfirm --clean --distpath dist --workpath build || goto :fail
echo.
echo Done: dist\ShotBalance.exe
pause
exit /b 0
:fail
echo Build failed. See the messages above.
pause
exit /b 1
