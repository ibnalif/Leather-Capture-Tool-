@echo off
title Leather Capture Tool (PC Bridge)
echo ========================================================
echo Starting Leather Dual-Side Capture ^& Grading System
echo ========================================================

:: Check ADB and set up reverse port forward for USB
where adb >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [INFO] Setting up USB port forward over ADB (Port 5000)...
    adb reverse tcp:5000 tcp:5000
) else (
    if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" (
        echo [INFO] Setting up USB port forward using Android SDK ADB...
        "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" reverse tcp:5000 tcp:5000
    ) else (
        echo [WARN] ADB not found on PATH or Android SDK.
    )
)

echo [INFO] Launching Python Desktop Tool...
python leather_capture_tool_dual.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo An error occurred. Make sure dependencies are installed:
    echo   pip install -r requirements.txt
    pause
)
