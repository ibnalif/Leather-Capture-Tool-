Set-Location -Path $PSScriptRoot
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Starting Leather Dual-Side Capture Tool" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

# Setup ADB reverse for USB if available
$adb = (Get-Command adb -ErrorAction SilentlyContinue).Source
if (-not $adb -and (Test-Path "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe")) {
    $adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
}
if ($adb) {
    Write-Host "[INFO] Setting up USB port forward over ADB..." -ForegroundColor Green
    & $adb reverse tcp:5000 tcp:5000
}

Write-Host "[INFO] PC Wi-Fi IP Address: http://192.168.1.4:5000" -ForegroundColor Yellow
Write-Host "[INFO] Launching Python Desktop Tool..." -ForegroundColor Green
python "$PSScriptRoot\leather_capture_tool_dual.py"
Read-Host "Press Enter to exit"
