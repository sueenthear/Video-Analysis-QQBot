@echo off
setlocal
cd /d "%~dp0"

echo Starting Video-Analysis-QQBot...
uv run python LoginCenter.py

if errorlevel 1 (
    echo.
    echo Startup failed. Error code: %errorlevel%
    pause
)
endlocal
