@echo off
pushd "%~dp0"
title Leather Dataset Capture Suite

echo ========================================================
echo Starting Leather Dataset Capture Suite
echo ========================================================
echo.

:: Try setting up ADB reverse port forward for USB
where adb >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Setting up USB port forward with ADB...
    adb reverse tcp:5000 tcp:5000
    goto :LAUNCH
)

if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" (
    echo [INFO] Setting up USB port forward with Android SDK ADB...
    "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" reverse tcp:5000 tcp:5000
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
