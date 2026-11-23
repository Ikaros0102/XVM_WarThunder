$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    Write-Host "[1/2] Building frontend SPA..."
    npm --prefix "$Root\frontend" run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }

    Write-Host "[2/2] Packaging WT-XVM.exe with PyInstaller..."
    & "$Root\.venv\Scripts\pyinstaller.exe" --clean --noconfirm "$Root\packaging\wt_xvm.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

    Write-Host "Done: $Root\dist\WT-XVM.exe"
}
finally {
    Pop-Location
}
