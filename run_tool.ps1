Set-Location -Path $PSScriptRoot
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Starting Leather Dataset Capture & Ingestion Suite" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

# Setup ADB reverse for USB if available
$adb = (Get-Command adb -ErrorAction SilentlyContinue).Source
if (-not $adb -and (Test-Path "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe")) {
    $adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
}
if ($adb) {
    Write-Host "[INFO] Setting up USB port forward over ADB (Port 5000)..." -ForegroundColor Green
    & $adb reverse tcp:5000 tcp:5000
}

Write-Host "[INFO] Launching Desktop Capture Tool..." -ForegroundColor Green
python "$PSScriptRoot\leather_capture_tool.py"
Read-Host "Press Enter to exit"
