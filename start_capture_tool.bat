@echo off
title Leather Capture Tool (PC Bridge)
echo ========================================================
echo Starting Leather Dual-Side Capture ^& Ingestion System
echo ========================================================

:: Check Python dependencies
python -c "import cv2, PIL" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] Installing required image packages (opencv-python, pillow)...
    python -m pip install opencv-python pillow
)

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

echo.
echo [INFO] PC Local IP Address for Wi-Fi connection:
for /f "tokens=4" %%a in ('route print ^| findstr 0.0.0.0 ^| findstr /v "0.0.0.0.*0.0.0.0"') do (
    echo   http://%%a:5000
)
echo.

echo [INFO] Launching Python Desktop Tool...
python leather_capture_tool_dual.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo An error occurred.
    pause
)
