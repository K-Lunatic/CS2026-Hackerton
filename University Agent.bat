@echo off
cd /d "%~dp0"
py -3 -m server.local
if errorlevel 1 pause
