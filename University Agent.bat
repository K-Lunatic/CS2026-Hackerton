@echo off
cd /d "%~dp0"
call run_with_python.bat skills\university-agent\scripts\update_turtleneck.py --auto
call run_with_python.bat -m server.local
if errorlevel 1 pause
