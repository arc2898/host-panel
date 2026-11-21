@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py") || (set "PY=python")

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  if exist .venv rmdir /s /q .venv
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Could not create the virtual environment. Install Python 3.10+ and try again.
    pause
    exit /b 1
  )
)

.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)
if not defined HOSTPANEL_BIND set HOSTPANEL_BIND=127.0.0.1
.venv\Scripts\python.exe start.py
pause
