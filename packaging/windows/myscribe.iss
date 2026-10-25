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
; Overridden only by a test build (TASK-112.04), so that one never finds - and
; updates - the MyScribe installed on the machine it is tried on.
#ifndef MyAppId
  #define MyAppId "{{B7F6E1C2-2C54-4C33-9D0B-6E9E3C0A1F21}"
#endif

[Setup]
AppId={#MyAppId}
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
; Off, because what Setup starts inherits it (TASK-116). Inno Setup 6.7.1 turns
; Windows' RedirectionGuard on by default; its help says the setting is not
; inherited by child processes, but the MyScribe that Setup started - the
; checkbox below, and the headless restart in CurStepChanged - was measured
; on 2026-10-22 with ProcessRedirectionTrustPolicy enforced. uv then cannot
; follow the junction it made for the Python minor version, and the start
; fails with "untrusted mount point (os error 448)". Setup installs for this
; user only, unelevated, so the guard protects nothing a junction could not
; already reach.
RedirectionGuard=no
; The PATH task below changes HKCU\Environment; this tells Windows to reread
; it, so a new terminal finds myscribe without logging off.
ChangesEnvironment=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
; Off unless ticked, like the login item: most people never type a command.
; Inno Setup remembers the choice for the next update.
Name: "addtopath"; Description: "Add the myscribe command to my PATH, for scripts and AI agents"; GroupDescription: "Command line:"; Flags: unchecked

[Files]
Source: "{#MySource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Stops a running MyScribe before it is replaced (TASK-112.04). In {app} for
; the uninstaller; the installer extracts this same entry to {tmp} first.
Source: "stop-myscribe.ps1"; DestDir: "{app}"; Flags: ignoreversion
; The myscribe command (TASK-108): python -m scribe.cli in the environment the
; launcher installed. In bin\ and not beside MyScribe.exe, because Windows
; tries .EXE before .CMD, so there "myscribe" would start the launcher.
Source: "myscribe.cmd"; DestDir: "{app}\bin"; Flags: ignoreversion

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

[UninstallDelete]
; The launcher's one-MyScribe lock and the note a failed start at login leaves
; for the next start by hand (TASK-112.07): <home>.lock and
; <home>-at-login.txt, beside the home and not in it. Robert, 2026-10-24: the
; uninstaller removes both beside the default home only. Beside a home that was
; put elsewhere they stay, because this script does not read
; MyScribe.location - and MyScribe.location stays too. InitializeUninstall
; runs the stop script first, which also ends what still runs from {app}: the
; launcher that holds the lock is MyScribe.exe there.
; UNVERIFIED on a real uninstall: only compiled with ISCC so far.
Type: files; Name: "{localappdata}\MyScribe.lock"
Type: files; Name: "{localappdata}\MyScribe-at-login.txt"

[Code]
// A headless MyScribe (ADR-026) keeps serving the old code after an update,
// and its python.exe lives in the home, outside {app}, where Restart Manager
// does not look. stop-myscribe.ps1 asks it through its stop route, so its
// running job is requeued (ADR-028), and ends what still runs from {app} -
// which is all a 0.8.x server, without --stop or a route, can be stopped by.
// Exit codes: 0 nothing was running, 10 stopped, 1 still running.
const
  StopNothing = 0;
  StopDone = 10;

var
  StoppedOne: Boolean;

function RunStopScript(Script: String; var Code: Integer): Boolean;
begin
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + Script + '" -InstallDir "'
      + ExpandConstant('{app}') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  ExtractTemporaryFile('stop-myscribe.ps1');
  // No PowerShell at all leaves the file-in-use check to Restart Manager.
  if not RunStopScript(ExpandConstant('{tmp}\stop-myscribe.ps1'), Code) then
    Exit;
  if Code = StopDone then
    StoppedOne := True
  else if Code <> StopNothing then
    Result := 'MyScribe is still running and could not be stopped. Stop it - Settings > Start at '
      + 'login > Stop MyScribe - and run Setup again.';
end;

// The PATH task (TASK-108): one entry, {app}\bin, in this user's own PATH.
// [Registry] cannot do this: it can only append to a value, and taking that
// part back out on uninstall would need uninsdeletevalue, which deletes the
// whole Path. So both directions are here, and touch only that one entry.
function PathHas(Path, Dir: String): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Path) + ';') > 0;
end;

procedure AddToPath(Dir: String);
var
  Path: String;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Path) then
    Path := '';
  if PathHas(Path, Dir) then
    Exit;
  if (Path <> '') and (Copy(Path, Length(Path), 1) <> ';') then
    Path := Path + ';';
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Path + Dir);
end;

procedure RemoveFromPath(Dir: String);
var
  Path, Rest, Entry, Kept: String;
  Cut: Integer;
  First: Boolean;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Path) then
    Exit;
  if not PathHas(Path, Dir) then
    Exit;
  Kept := '';
  First := True;
  Rest := Path;
  while Rest <> '' do
  begin
    Cut := Pos(';', Rest);
    if Cut = 0 then
    begin
      Entry := Rest;
      Rest := '';
    end
    else
    begin
      Entry := Copy(Rest, 1, Cut - 1);
      Rest := Copy(Rest, Cut + 1, Length(Rest));
    end;
    if CompareText(Entry, Dir) <> 0 then
    begin
      if not First then
        Kept := Kept + ';';
      Kept := Kept + Entry;
      First := False;
    end;
  end;
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Kept);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  // Setup always ends with MyScribe open in the browser (Robert, 2026-10-23),
  // also when it runs silently - which is why this is code and not a [Run]
  // checkbox, which /SILENT skips. One place starts it, so two launchers
  // never race for the port.
  if CurStep <> ssPostInstall then
    Exit;
  // Ticked: on PATH. Unticked on an update: off again, so PATH says what the
  // last install was told.
  if WizardIsTaskSelected('addtopath') then
    AddToPath(ExpandConstant('{app}\bin'))
  else
    RemoveFromPath(ExpandConstant('{app}\bin'));
  if StoppedOne
    and RegValueExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'MyScribe') then
    // Back the way the login item starts it, headless (ADR-026), and only
    // when that item is there: an update must not switch on a login start
    // nobody chose. The browser is not switched off here, so the launcher
    // opens it itself once MyScribe serves.
    Exec(ExpandConstant('{app}\{#MyAppExe}'), '--at-login --headless', '',
      SW_HIDE, ewNoWait, Code)
  else
    // The launcher with its window: a first start asks where the files go and
    // shows the download, and it opens the browser once MyScribe serves - or
    // at once, when a MyScribe is already running.
    Exec(ExpandConstant('{app}\{#MyAppExe}'), '', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

function InitializeUninstall(): Boolean;
var
  Code: Integer;
begin
  Result := True;
  if RunStopScript(ExpandConstant('{app}\stop-myscribe.ps1'), Code) and (Code <> StopNothing)
    and (Code <> StopDone) then
  begin
    SuppressibleMsgBox('MyScribe is still running and could not be stopped. Stop it - Settings > '
      + 'Start at login > Stop MyScribe - and run the uninstaller again.', mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveFromPath(ExpandConstant('{app}\bin'));
end;

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
