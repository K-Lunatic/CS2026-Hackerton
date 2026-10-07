@echo off
setlocal
set "ROOT=%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
  py -3 %*
  exit /b %errorlevel%
)

where python >nul 2>&1
if not errorlevel 1 (
  python %*
  exit /b %errorlevel%
)

set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" (
  where uv >nul 2>&1
  if not errorlevel 1 set "UV=uv"
)

if "%UV%"=="%USERPROFILE%\.local\bin\uv.exe" if not exist "%UV%" (
  echo 터틀넥이 필요한 Python 실행 환경을 한 번 준비할게요…
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)

if not exist "%UV%" if not "%UV%"=="uv" (
  echo Python 실행 환경을 준비하지 못했어요. 인터넷 연결을 확인한 뒤 다시 실행해 주세요.
  exit /b 1
)

"%UV%" run --python 3.11 python %*
exit /b %errorlevel%
