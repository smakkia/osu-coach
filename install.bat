@echo off
rem osu!coach installer: double-click. It runs install.ps1 (Python, libraries, shortcuts, then the setup wizard).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
