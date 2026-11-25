@echo off
rem Starts the School Paging System from source using the project virtual environment.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Virtual environment not found. Run: py -3 -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" main.py %*
