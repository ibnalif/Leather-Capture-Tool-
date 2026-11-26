Set-Location -Path $PSScriptRoot
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Starting Leather Dataset Capture & Ingestion Suite" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

# Setup ADB reverse for USB if available
$adb = $null
$configPath = Join-Path $PSScriptRoot "config.json"
if (Test-Path $configPath) {
    try {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        if ($cfg.adb_path -and (Test-Path $cfg.adb_path)) {
            $adb = $cfg.adb_path
        }
    } catch {}
}
if (-not $adb) {
    $adb = (Get-Command adb -ErrorAction SilentlyContinue).Source
}
if (-not $adb -and (Test-Path "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe")) {
    $adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
}
if (-not $adb -and (Test-Path "$env:USERPROFILE\Downloads\Plugcam_0.2.1_x64-portable\resources\adb.exe")) {
    $adb = "$env:USERPROFILE\Downloads\Plugcam_0.2.1_x64-portable\resources\adb.exe"
}
if ($adb) {
    Write-Host "[INFO] Setting up USB port forward over ADB (Port 5000)..." -ForegroundColor Green
    & $adb reverse tcp:5000 tcp:5000
} else {
    Write-Host "[WARN] ADB not found. USB reverse skipped." -ForegroundColor Yellow
}

Write-Host "[INFO] Launching Desktop Capture Tool..." -ForegroundColor Green
python "$PSScriptRoot\leather_capture_tool.py"
Read-Host "Press Enter to exit"
