@echo off
setlocal
cd /d %~dp0
title PoE Craft Guard

rem ---------- 1. find Python ----------
call :findpy
if defined PY goto :havepy

echo Python not found. Installing Python 3.12 (one time)...
where winget >nul 2>&1
if %errorlevel%==0 (
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
) else (
  echo winget not available, downloading installer from python.org...
  curl.exe -L -o "%TEMP%\python-setup.exe" https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe
  "%TEMP%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1
)
call :findpy
if not defined PY (
  echo.
  echo Could not install Python automatically.
  echo Install it manually from https://www.python.org/downloads/ and check "Add python.exe to PATH".
  pause
  exit /b 1
)

:havepy
echo Using Python: %PY%

rem ---------- 2. libraries ----------
%PY% -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
  echo Failed to install libraries. Check your internet connection.
  pause
  exit /b 1
)

rem ---------- 3. English OCR pack ----------
%PY% check_ocr.py
if %errorlevel%==2 (
  echo.
  echo English text recognition pack is missing in Windows. Installing it - confirm the admin prompt...
  powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -Command Add-WindowsCapability -Online -Name Language.OCR~~~en-US~0.0.1.0'"
  %PY% check_ocr.py
  if errorlevel 1 (
    echo Still missing. Add English ^(United States^) in Settings - Time and language - Language, then run again.
    pause
    exit /b 1
  )
)

rem ---------- 4. start ----------
%PY% app.py
pause
exit /b 0

:findpy
set PY=
py -3 --version >nul 2>&1 && set "PY=py -3" && exit /b 0
python -c "import sys" >nul 2>&1 && set "PY=python" && exit /b 0
if exist "%LocalAppData%\Programs\Python\Launcher\py.exe" set "PY="%LocalAppData%\Programs\Python\Launcher\py.exe" -3" && exit /b 0
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY="%%D\python.exe""
exit /b 0
