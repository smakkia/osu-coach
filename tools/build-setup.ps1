# Builds dist\osu-coach-Setup-<version>.exe, the installer to attach to a GitHub release:
#   1. the app with PyInstaller (dist\osu-coach: osu-coach.exe and its libraries, without scikit-learn)
#   2. the map data from this PC's %LOCALAPPDATA%\osu-coach (the type guesser as plain arrays, and its guesses)
#   3. the installer with Inno Setup 6 (winget install JRSoftware.InnoSetup)
#
#   powershell -ExecutionPolicy Bypass -File tools\build-setup.ps1
# Needs the build libraries: .venv\Scripts\python -m pip install -r requirements-dev.txt

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Cache = Join-Path $env:LOCALAPPDATA "osu-coach"
$Dist = Join-Path $Root "dist"
Push-Location $Root
try {
    $Version = (& $Python -c "import osu_coach; print(osu_coach.__version__)").Trim()
    Write-Host "osu!coach $Version" -ForegroundColor Magenta

    Write-Host "==> The app (PyInstaller)" -ForegroundColor Magenta
    & $Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath (Join-Path $Root "build") tools\osu-coach.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    Write-Host "==> The map data" -ForegroundColor Magenta
    foreach ($f in @("typeguess.pkl", "predicted_types.json")) {
        if (-not (Test-Path (Join-Path $Cache $f))) { throw "$f is missing from $Cache`: run osu-coach.bat retrain first" }
    }
    & $Python -c "from osu_coach.typeguess import to_lite; to_lite()"
    if ($LASTEXITCODE -ne 0) { throw "could not convert typeguess.pkl" }
    $Data = Join-Path $Dist "data"
    if (Test-Path $Data) { Remove-Item -Recurse -Force $Data }
    New-Item -ItemType Directory -Force $Data | Out-Null
    foreach ($f in @("typeguess_lite.pkl", "predicted_types.json")) { Copy-Item (Join-Path $Cache $f) $Data }

    Write-Host "==> The installer (Inno Setup)" -ForegroundColor Magenta
    $Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) { throw "Inno Setup 6 not found: winget install JRSoftware.InnoSetup" }
    & $Iscc /Q "/DAppVersion=$Version" tools\setup.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    $Setup = Join-Path $Dist "osu-coach-Setup-$Version.exe"
    Write-Host ("{0}: {1:N1} MB" -f $Setup, ((Get-Item $Setup).Length / 1MB)) -ForegroundColor Green
} finally {
    Pop-Location
}
