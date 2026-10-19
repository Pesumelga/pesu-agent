@echo off
setlocal
set "PESU_ROOT=%~dp0"
if exist "%PESU_ROOT%release\PESU-1.0.0-rc5\PESU\PESU.exe" (
    start "" "%PESU_ROOT%release\PESU-1.0.0-rc5\PESU\PESU.exe" %*
    exit /b 0
)
set "PYTHONPATH=%PESU_ROOT%src;%PYTHONPATH%"
python -u -X utf8 "%PESU_ROOT%src\pesu_agent\cli.py" %*
