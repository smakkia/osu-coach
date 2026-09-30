@echo off
rem osu-coach: without arguments opens the window; with arguments runs the command line
rem   osu-coach.bat                      the window
rem   osu-coach.bat analyze              any command, as python -m osu_coach analyze
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found in "%~dp0.venv": create it first.
    pause
    exit /b 1
)
if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" -m osu_coach ui
) else (
    ".venv\Scripts\python.exe" -m osu_coach %*
)
