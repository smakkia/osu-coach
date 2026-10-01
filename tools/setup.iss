; osu!coach installer (Inno Setup 6), built by tools\build-setup.ps1: one exe that installs the app for the current
; user (no administrator rights), with the map data and the shortcuts, and can install OpenTabletDriver.
; Your settings, profile and API credentials live in %LOCALAPPDATA%\osu-coach: updates and uninstalling keep them.

#ifndef AppVersion
  #define AppVersion "0.0"
#endif

[Setup]
AppId={{1ccc7f17-8fcf-4827-a241-d9103492783a}
AppName=osu!coach
AppVersion={#AppVersion}
AppVerName=osu!coach {#AppVersion}
AppPublisher=smakkia
AppPublisherURL=https://github.com/smakkia/osu-coach
AppSupportURL=https://github.com/smakkia/osu-coach/issues
AppUpdatesURL=https://github.com/smakkia/osu-coach/releases
DefaultDirName={localappdata}\Programs\osu-coach
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=osu-coach-Setup-{#AppVersion}-windows_x64
SetupIconFile=..\osu_coach\ui\icon.ico
UninstallDisplayIcon={app}\osu-coach.exe
UninstallDisplayName=osu!coach
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "otd"; Description: "Install OpenTabletDriver (osu!coach reads your tablet area from it; uninstall other tablet drivers first)"; GroupDescription: "Tablet (optional):"; Flags: unchecked; Check: CanInstallOtd

[InstallDelete]
; an update replaces the libraries: no leftovers of the old version
Type: filesandordirs; Name: "{app}\_internal"

[UninstallDelete]
; the in-app updates add and replace files the installer doesn't know about: all of them go
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\osu-coach\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; the map type guesser and its guesses for the ranked maps: shared data, replaced by every version
Source: "..\dist\data\*"; DestDir: "{localappdata}\osu-coach"; Flags: ignoreversion uninsneveruninstall

[Icons]
Name: "{autoprograms}\osu!coach"; Filename: "{app}\osu-coach.exe"; Comment: "Profile, replay analysis, beatmap search"
Name: "{autodesktop}\osu!coach"; Filename: "{app}\osu-coach.exe"; Comment: "Profile, replay analysis, beatmap search"; Tasks: desktopicon

[Run]
Filename: "{code:Winget}"; Parameters: "install --id OpenTabletDriver.OpenTabletDriver -e --accept-package-agreements --accept-source-agreements"; StatusMsg: "Installing OpenTabletDriver..."; Tasks: otd; Flags: waituntilterminated
Filename: "{app}\osu-coach.exe"; Description: "Start osu!coach"; Flags: nowait postinstall skipifsilent
; the in-app update installs silently: start the new version again
Filename: "{app}\osu-coach.exe"; Flags: nowait skipifnotsilent

[Code]
function Winget(Param: String): String;
begin
  Result := ExpandConstant('{localappdata}\Microsoft\WindowsApps\winget.exe');
end;

// offered only when OpenTabletDriver isn't set up yet and winget is there to install it
function CanInstallOtd: Boolean;
begin
  Result := (not FileExists(ExpandConstant('{localappdata}\OpenTabletDriver\settings.json')))
    and FileExists(Winget(''));
end;
