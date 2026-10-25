; MyScribe's Windows installer (ADR-011). Built by packaging/build_release.py:
;   iscc /DMyAppVersion=.. /DMySource=.. /DMyOutputDir=.. /DMyOutputName=.. myscribe.iss
;
; Per user, so it needs no administrator: everything goes under
; %LOCALAPPDATA%\Programs\MyScribe. The environment the first start installs
; lives in %LOCALAPPDATA%\MyScribe; the library - the database, media, models
; and the app's log - goes where the library page below says, by default
; %LOCALAPPDATA%\MyScribe\data (TASK-097, ADR-036). Neither is ever this
; directory: an uninstall or an update must not take a transcript with it.

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

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#MySource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Stops a running MyScribe before it is replaced (TASK-112.04). In {app} for
; the uninstaller; the installer extracts this same entry to {tmp} first.
Source: "stop-myscribe.ps1"; DestDir: "{app}"; Flags: ignoreversion

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
  LibraryPage: TInputDirWizardPage;

// --- where the library goes (TASK-097, ADR-036) ---------------------------------
// The page records a request in MyScribe.location, the launcher's pointer file
// beside the default home; the launcher settles it at the first start, before
// anything is downloaded, and a folder that already holds a library is adopted
// by the engine there, its recordings shown first (ADR-019). Nothing here opens
// a database or decides an adoption.

function DefaultHome(): String;
begin
  Result := ExpandConstant('{localappdata}\MyScribe');
end;

function PointerFile(): String;
begin
  Result := ExpandConstant('{localappdata}\MyScribe.location');
end;

// Asked only where nothing says where yet: no pointer file at all, and no
// install in the default home. An update of an install that never moved
// anything has no pointer file, and its library must not be swapped for an
// empty one; the launcher refuses that too.
function AskForLibrary(): Boolean;
begin
  Result := not FileExists(PointerFile()) and not DirExists(DefaultHome() + '\env');
end;

// JSON in ASCII: every backslash doubled, a double quote escaped, and every
// character outside printable ASCII as \uXXXX, so the file's encoding cannot
// matter to the launcher's reader.
function JsonString(S: String): String;
var
  I: Integer;
  C: Char;
begin
  Result := '"';
  for I := 1 to Length(S) do
  begin
    C := S[I];
    if C = '\' then
      Result := Result + '\\'
    else if C = '"' then
      Result := Result + '\"'
    else if (Ord(C) < 32) or (Ord(C) > 126) then
      Result := Result + '\u' + Format('%.4x', [Ord(C)])
    else
      Result := Result + C;
  end;
  Result := Result + '"';
end;

procedure InitializeWizard();
var
  Given: String;
begin
  LibraryPage := CreateInputDirPage(wpSelectDir,
    'Where your library goes',
    'Where should MyScribe keep your recordings, transcripts and models?',
    'Keep this folder and the library goes where it always has, in its data folder. An empty '
      + 'or new folder becomes the library. A folder that holds other files gets a MyScribe '
      + 'folder inside it. A folder that already holds a MyScribe library is shown with its '
      + 'recordings at the first start, and used only when you choose it there.'#13#10#13#10
      + 'Not a network share. Uninstalling never deletes the library.',
    False, 'MyScribe');
  LibraryPage.Add('');
  // /LIBRARY= gives a silent install the same answer.
  Given := Trim(ExpandConstant('{param:LIBRARY|}'));
  if Given = '' then
    Given := DefaultHome();
  LibraryPage.Values[0] := Given;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = LibraryPage.ID) and not AskForLibrary();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Chosen: String;
begin
  Result := True;
  if CurPageID <> LibraryPage.ID then
    Exit;
  Chosen := Trim(LibraryPage.Values[0]);
  if Copy(Chosen, 1, 2) = '\\' then
  begin
    MsgBox('A folder on a network share cannot hold the library: MyScribe keeps it in SQLite '
      + 'with a write-ahead log, which does not work over a network filesystem. Choose a folder '
      + 'on a disk in this computer.', mbError, MB_OK);
    Result := False;
  end
  else if (Length(Chosen) < 3) or (Chosen[2] <> ':') or (Chosen[3] <> '\') then
  begin
    MsgBox(Chosen + ' is not a full path. Give the whole path, the drive included, for '
      + 'example D:\MyScribe.', mbError, MB_OK);
    Result := False;
  end
  else if Pos(Lowercase(AddBackslash(WizardDirValue())), Lowercase(AddBackslash(Chosen))) = 1 then
  begin
    MsgBox(Chosen + ' is inside the program folder, which the next update or uninstall '
      + 'deletes with everything in it. Choose a folder outside it.', mbError, MB_OK);
    Result := False;
  end;
end;

// Written once, before the launcher is started, and never over a pointer
// file that exists.
procedure WriteLibraryRequest();
begin
  if not AskForLibrary() then
    Exit;
  SaveStringToFile(PointerFile(), '{'#13#10'  "library_request": '
    + JsonString(Trim(LibraryPage.Values[0])) + #13#10'}'#13#10, False);
end;

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
  // Before either start below: the launcher reads the request at once.
  WriteLibraryRequest();
  if StoppedOne
    and RegValueExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'MyScribe') then
    // Back the way the login item starts it, headless (ADR-026), and only
    // when that item is there: an update must not switch on a login start
    // nobody chose. The browser is not switched off here, so the launcher
    // opens it itself once MyScribe serves.
    Exec(ExpandConstant('{app}\{#MyAppExe}'), '--at-login --headless', '',
      SW_HIDE, ewNoWait, Code)
  else
    // The launcher with its window: a first start settles the library page's
    // answer and shows the download, and it opens the browser once MyScribe serves - or
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

[Messages]
; SetupAppTitle is a message, not a [Setup] directive. iscc refused the
; whole script over it - "Unrecognized [Setup] directive" on this line -
; the first time the Windows build ever ran on a runner.
SetupAppTitle={#MyAppName}

; The one thing a user has to know before the first start.
; No size in gigabytes here: this text cannot read scribe/footprint.json, so a
; number in it would be one nobody updates, and the first start already shows
; the size it is about to download - per platform, per model (TASK-089.14).
WelcomeLabel2=This installs [name/ver] for your account only, into %LOCALAPPDATA%\Programs\MyScribe.%n%nA later page asks where your library goes - your recordings, transcripts and models; %LOCALAPPDATA%\MyScribe is only the default. The first start then downloads the speech engine with its progress, and asks a few questions you can skip. Later starts skip all of that.%n%nUninstalling removes the program and its login item, and leaves your library alone: it is yours. If MyScribe installed Ollama and a model for you, they stay too and belong to you; remove them with Ollama's own uninstaller under Windows Settings > Apps.
