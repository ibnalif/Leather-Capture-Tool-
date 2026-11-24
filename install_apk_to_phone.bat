@echo off
title Install Leather Capture APK to Phone
echo ========================================================
echo Installing CameraL APK to Connected Android Device
echo ========================================================

set ADB_BIN=adb
where adb >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" (
        set ADB_BIN="%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
    ) else (
        echo [ERROR] ADB not found. Make sure Android SDK platform-tools is installed.
        pause
        exit /b 1
    )
)

set APK_PATH=C:\Users\Inteshar\AndroidStudioProjects\cameraL\app\build\outputs\apk\debug\app-debug.apk

if not exist "%APK_PATH%" (
    echo [ERROR] APK not found at %APK_PATH%
    pause
    exit /b 1
)

echo [INFO] Detected APK: %APK_PATH%
echo [INFO] Checking connected devices...
%ADB_BIN% devices
echo.
echo [INFO] Installing APK to device...
%ADB_BIN% install -r "%APK_PATH%"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================================
    echo [SUCCESS] App successfully installed on your phone!
    echo Look for 'cameraL' on your phone screen.
    echo ========================================================
) else (
    echo.
    echo [FAIL] Install failed. Please check:
    echo 1. USB Cable is connected.
    echo 2. USB Debugging is turned ON in Developer Options.
    echo 3. Unlock phone and tap "Allow USB Debugging" if prompted.
)
pause
