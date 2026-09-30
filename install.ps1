# osu!coach installer for Windows.
#
# Sets up everything the app needs: Python 3.11+ (installed with winget if missing), a private virtual environment
# with the libraries, the shared map data (the map type guesser), OpenTabletDriver if you want it, and shortcuts.
# Then it opens osu!coach, whose setup wizard asks for the rest: the osu! folder, the osu! API credentials, your
# tablet or mouse and your keyboard. Run it again at any time: every step is skipped when already done.
#
#   install.bat                       (double-click)
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-NoShortcut] [-NoLaunch]

param([switch]$NoShortcut, [switch]$NoLaunch)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".venv"
$Cache = Join-Path $env:LOCALAPPDATA "osu-coach"
# the shared map data, attached to the project's GitHub releases (see README, "Releasing")
$DataUrl = "https://github.com/OWNER/osu-coach/releases/latest/download/osu-coach-data.zip"

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Magenta }
function Info($text) { Write-Host "    $text" }
function Ask($question, $default) {
    $hint = if ($default) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "    $question $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith("y")
}

function Find-Python {
    # py launcher first (newest supported), then python on PATH; the Microsoft Store stub fails the check
    $candidates = @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("python"), @("python3"))
    foreach ($c in $candidates) {
        try {
            $rest = @($c | Select-Object -Skip 1)
            $out = & $c[0] @rest -c "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out.Count -ge 2 -and [version]$out[0] -ge [version]"3.11") { return $out[1] }
        } catch { }
    }
    # a Python just installed by winget isn't on this session's PATH yet
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

Write-Host "osu!coach installer" -ForegroundColor Magenta
Info "Folder: $Root"

# --- Python ---------------------------------------------------------------------------------------------------
Step "Python 3.11 or newer"
$python = Find-Python
if (-not $python) {
    Info "Python wasn't found."
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install Python 3.12 with winget?" $true)) {
        winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
        $python = Find-Python
    }
    if (-not $python) {
        Write-Host "    Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again." -ForegroundColor Yellow
        exit 1
    }
}
Info "Using $python"

# --- virtual environment and libraries ------------------------------------------------------------------------
Step "Libraries (numpy, scikit-learn) in a private environment"
$venvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & $python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "could not create the virtual environment in $Venv" }
}
& $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "could not install the libraries (see the messages above)" }
Info "Done."

# --- shared map data --------------------------------------------------------------------------------------------
Step "Map type guesser (makes the search on the osu! site much faster)"
if (Test-Path (Join-Path $Cache "typeguess.pkl")) {
    Info "Already there."
} elseif ($DataUrl -match "/OWNER/") {
    Info "No download address set in install.ps1: skipped (the site search still works, only slower)."
} else {
    try {
        New-Item -ItemType Directory -Force $Cache | Out-Null
        $zip = Join-Path $env:TEMP "osu-coach-data.zip"
        Invoke-WebRequest -Uri $DataUrl -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $Cache -Force
        Remove-Item $zip
        Info "Downloaded."
    } catch {
        Info "Couldn't download it ($($_.Exception.Message)): skipped, the site search still works, only slower."
    }
}

# --- tablet driver ------------------------------------------------------------------------------------------------
Step "Tablet driver (optional)"
$otdSettings = Join-Path $env:LOCALAPPDATA "OpenTabletDriver\settings.json"
if (Test-Path $otdSettings) {
    Info "OpenTabletDriver found: osu!coach reads your tablet area from it."
} else {
    Info "OpenTabletDriver wasn't found. osu!coach reads the tablet area from it (with another driver you type it in)."
    Info "Mouse players and players happy with their driver can skip this. Uninstall other tablet drivers first."
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install OpenTabletDriver with winget?" $false)) {
        winget install --id OpenTabletDriver.OpenTabletDriver -e --accept-package-agreements --accept-source-agreements
        Info "Open OpenTabletDriver once, set your area and save: osu!coach will pick it up."
    }
}

# --- shortcuts ------------------------------------------------------------------------------------------------------
if (-not $NoShortcut) {
    Step "Shortcuts"
    $shell = New-Object -ComObject WScript.Shell
    $places = @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) ""))
    foreach ($place in $places) {
        $link = $shell.CreateShortcut((Join-Path $place "osu!coach.lnk"))
        $link.TargetPath = Join-Path $Venv "Scripts\pythonw.exe"
        $link.Arguments = "-m osu_coach ui"
        $link.WorkingDirectory = $Root
        $link.IconLocation = (Join-Path $Root "osu_coach\ui\icon.ico") + ",0"
        $link.Description = "osu!coach: profile, replay analysis, beatmap search"
        $link.Save()
    }
    Info "osu!coach is on the desktop and in the Start menu."
}

# --- first start ----------------------------------------------------------------------------------------------------
Step "Starting osu!coach"
if ($NoLaunch) {
    Info "Start it from the shortcut; the setup wizard opens the first time."
} else {
    Start-Process -FilePath (Join-Path $Venv "Scripts\pythonw.exe") -ArgumentList "-m", "osu_coach", "ui" -WorkingDirectory $Root
    Info "The window opens with the setup wizard: osu! folder, osu! API, tablet or mouse, keyboard."
}
Write-Host ""
Write-Host "All done." -ForegroundColor Green
