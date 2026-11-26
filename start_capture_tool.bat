@echo off
pushd "%~dp0"
title Leather Dataset Capture Suite

echo ========================================================
echo Starting Leather Dataset Capture Suite
echo ========================================================
echo.

:: Try setting up ADB reverse port forward for USB
set "ADB_BIN="
for /f "usebackq delims=" %%I in (`python -c "import json; print(json.load(open('config.json')).get('adb_path',''))" 2^>nul`) do set "CFG_ADB=%%I"
if defined CFG_ADB if exist "%CFG_ADB%" set "ADB_BIN=%CFG_ADB%"

if not defined ADB_BIN (
    where adb >nul 2>&1
    if not errorlevel 1 set "ADB_BIN=adb"
)
if not defined ADB_BIN if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" set "ADB_BIN=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if not defined ADB_BIN if exist "%USERPROFILE%\Downloads\Plugcam_0.2.1_x64-portable\resources\adb.exe" set "ADB_BIN=%USERPROFILE%\Downloads\Plugcam_0.2.1_x64-portable\resources\adb.exe"

if defined ADB_BIN (
    echo [INFO] Setting up USB port forward with ADB: %ADB_BIN%
    "%ADB_BIN%" reverse tcp:5000 tcp:5000
    goto :LAUNCH
)

echo [INFO] ADB not found. Continuing in Wi-Fi mode.

:LAUNCH
echo.
echo [INFO] Launching Desktop Capture Tool...
echo.

python "%~dp0leather_capture_tool.py"

echo.
echo ========================================================
echo Desktop application has closed.
echo ========================================================
pause
popd
