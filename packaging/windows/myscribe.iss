; MyScribe's Windows installer (ADR-008). Built by packaging/build_release.py:
;   iscc /DMyAppVersion=.. /DMySource=.. /DMyOutputDir=.. /DMyOutputName=.. myscribe.iss
;
; Per user, so it needs no administrator: everything goes under
; %LOCALAPPDATA%\Programs\MyScribe. The app's own data - the database, media,
; models and the environment it installs on first start - lives in
; %LOCALAPPDATA%\MyScribe, which is deliberately *not* this directory: an
; uninstall or an update must not take a transcript with it.

#define MyAppName "MyScribe"
#define MyAppExe "MyScribe.exe"

[Setup]
AppId={{B7F6E1C2-2C54-4C33-9D0B-6E9E3C0A1F21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Robert van den Breemen
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExe}
SetupAppTitle={#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#MySource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Messages]
; The one thing a user has to know before the first start.
WelcomeLabel2=This installs [name/ver] for your account only.%n%nThe first start downloads the speech engine (about 3 GB) and shows its progress. Later starts skip that. Your recordings, transcripts and models are kept in %LOCALAPPDATA%\MyScribe and are left alone when you uninstall.
