; MyScribe's Windows installer (ADR-011). Built by packaging/build_release.py:
;   iscc /DMyAppVersion=.. /DMySource=.. /DMyOutputDir=.. /DMyOutputName=.. myscribe.iss
;
; Per user, so it needs no administrator: everything goes under
; %LOCALAPPDATA%\Programs\MyScribe. The app's own data - the database, media,
; models and the environment it installs on first start - lives in
; %LOCALAPPDATA%\MyScribe by default, or wherever the first start was told to
; put it (TASK-089.14), which is deliberately *not* this directory: an
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

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#MySource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; The login item Settings > Start at login writes (scribe/autostart.py,
; TASK-089.21): a value named MyScribe under this user's own Run key. It is
; removed on uninstall and never written here (TASK-089.23). Inno Setup's
; help, [Registry] section, read 2026-09-23: "If none (the default setting)
; is specified, Setup will create the key but not a value"; dontcreatekey:
; "Setup will not attempt to create the key or any value if the key did not
; already exist"; uninsdeletevalue: "Delete the value when the program is
; uninstalled." So an install writes nothing - a login item is a choice the
; person makes, default No (TASK-089.22) - and an uninstall takes it away.
; UNVERIFIED on a real uninstall until TASK-089.23 criterion 5 is run.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "MyScribe"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Messages]
; SetupAppTitle is a message, not a [Setup] directive. iscc refused the
; whole script over it - "Unrecognized [Setup] directive" on this line -
; the first time the Windows build ever ran on a runner.
SetupAppTitle={#MyAppName}

; The one thing a user has to know before the first start.
; No size in gigabytes here: this text cannot read scribe/footprint.json, so a
; number in it would be one nobody updates, and the first start already shows
; the size it is about to download - per platform, per model (TASK-089.14).
WelcomeLabel2=This installs [name/ver] for your account only, into %LOCALAPPDATA%\Programs\MyScribe.%n%nThe first start asks where your recordings, transcripts and models should go - %LOCALAPPDATA%\MyScribe is only the default - then downloads the speech engine with its progress, and asks a few questions you can skip. Later starts skip all of that.%n%nUninstalling removes the program and its login item, and leaves that folder alone: it is yours. If MyScribe installed Ollama and a model for you, they stay too and belong to you; remove them with Ollama's own uninstaller under Windows Settings > Apps.
