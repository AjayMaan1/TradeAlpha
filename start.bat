@echo off
echo Starting Historify - Stock Historical Data Management App...

rem Historify needs Python 3.12+ (3.14 recommended)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 (
    echo Python 3.12 or newer is required ^(3.14 recommended^). Install it from https://www.python.org/downloads/
    pause
    exit /b 1
)

rem Check if virtual environment exists, if not create one
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate
    echo Installing dependencies...
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate
)

rem Binds to 127.0.0.1:5001 without the debugger (see SECURITY.md)
echo Starting Historify at http://127.0.0.1:5001
python run.py

pause
