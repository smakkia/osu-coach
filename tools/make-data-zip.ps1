# Builds dist\osu-coach-data.zip, the release asset install.ps1 downloads: the map type guesser as plain arrays
# (typeguess_lite.pkl, no scikit-learn needed) and its guesses for the ranked maps (predicted_types.json), from this
# PC's %LOCALAPPDATA%\osu-coach (after `retrain`).

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Cache = Join-Path $env:LOCALAPPDATA "osu-coach"
foreach ($f in @("typeguess.pkl", "predicted_types.json")) {
    if (-not (Test-Path (Join-Path $Cache $f))) { throw "$f is missing: run osu-coach.bat retrain first" }
}
$Python = Join-Path $Root ".venv\Scripts\python.exe"
Push-Location $Root
try { & $Python -c "from osu_coach.typeguess import to_lite; to_lite()" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "could not convert typeguess.pkl (is scikit-learn installed? pip install -r requirements-dev.txt)" }
$Files = @("typeguess_lite.pkl", "predicted_types.json") | ForEach-Object { Join-Path $Cache $_ }
$Dist = Join-Path $Root "dist"
New-Item -ItemType Directory -Force $Dist | Out-Null
$Zip = Join-Path $Dist "osu-coach-data.zip"
Compress-Archive -Path $Files -DestinationPath $Zip -Force
Write-Host ("{0}: {1:N1} MB" -f $Zip, ((Get-Item $Zip).Length / 1MB))
