@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv
    if errorlevel 1 (
        echo Python is required to start Packing List Studio.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Could not install the required packages.
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=1)" >nul 2>&1
if not errorlevel 1 (
    start "" "http://127.0.0.1:8765/"
    exit /b 0
)
start "Packing List Studio server" /B ".venv\Scripts\python.exe" -m uvicorn packing_app.main:app --host 127.0.0.1 --port 8765
timeout /t 3 >nul
start "" "http://127.0.0.1:8765/"
echo Packing List Studio is running. Keep this window open while using it.
pause
