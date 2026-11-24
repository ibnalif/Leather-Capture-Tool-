@echo off
pushd "%~dp0"
title Leather Capture Tool (PC Bridge)

echo ========================================================
echo Starting Leather Dual-Side Capture Tool
echo ========================================================
echo Directory: %~dp0
echo.

:: Setup USB reverse port forward using ADB if available
where adb >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [INFO] Setting up USB port forward over ADB...
    adb reverse tcp:5000 tcp:5000
) else (
    if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" (
        echo [INFO] Setting up USB port forward using Android SDK ADB...
        "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" reverse tcp:5000 tcp:5000
    ) else (
        echo [INFO] ADB not found on PATH. If using USB, enable USB Debugging.
    )
)

echo.
echo [INFO] PC Wi-Fi IP is: http://192.168.1.4:5000
echo.
echo [INFO] Launching Python Desktop Tool...
echo.

python "%~dp0leather_capture_tool_dual.py"

echo.
echo ========================================================
echo Tool exited.
echo ========================================================
pause
popd
