# Builds dist\osu-coach-data.zip, the release asset install.ps1 downloads: the map type guesser (typeguess.pkl) and
# its guesses for the ranked maps (predicted_types.json), from this PC's %LOCALAPPDATA%\osu-coach (after `retrain`).

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Cache = Join-Path $env:LOCALAPPDATA "osu-coach"
$Files = @("typeguess.pkl", "predicted_types.json") | ForEach-Object { Join-Path $Cache $_ }

foreach ($f in $Files) {
    if (-not (Test-Path $f)) { throw "$f is missing: run osu-coach.bat retrain first" }
}
$Dist = Join-Path $Root "dist"
New-Item -ItemType Directory -Force $Dist | Out-Null
$Zip = Join-Path $Dist "osu-coach-data.zip"
Compress-Archive -Path $Files -DestinationPath $Zip -Force
Write-Host ("{0}: {1:N1} MB" -f $Zip, ((Get-Item $Zip).Length / 1MB))
