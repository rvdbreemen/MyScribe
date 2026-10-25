@echo off
rem myscribe: the command line of an installed MyScribe (TASK-108).
rem
rem Installed by myscribe.iss into {app}\bin, not {app}: Windows tries .EXE
rem before .CMD, so beside MyScribe.exe "myscribe" would start the launcher.
rem It runs `python -m scribe.cli` with the Python of the environment the
rem launcher installed. That environment lives in MyScribe's home, found the
rem way the launcher finds it (myscribe_launcher.home_dir): MYSCRIBE_HOME,
rem then "home" in %LOCALAPPDATA%\MyScribe.location, then %LOCALAPPDATA%\MyScribe.
rem The program files are this install's own: {app}\_internal\payload\app.
setlocal EnableExtensions DisableDelayedExpansion

set "MYSCRIBE_APP=%~dp0..\_internal\payload\app"
if not exist "%MYSCRIBE_APP%\scribe\cli.py" goto :no_app

set "MYSCRIBE_AT=%MYSCRIBE_HOME%"
if defined MYSCRIBE_AT goto :found_home
set "MYSCRIBE_AT=%LOCALAPPDATA%\MyScribe"
set "MYSCRIBE_POINTER=%LOCALAPPDATA%\MyScribe.location"
if not exist "%MYSCRIBE_POINTER%" goto :found_home
rem The pointer is JSON with escaped backslashes: PowerShell reads it, not cmd.
for /f "usebackq delims=" %%H in (`powershell.exe -NoProfile -NonInteractive -Command "try { $h = (Get-Content -Raw -LiteralPath $env:MYSCRIBE_POINTER | ConvertFrom-Json).home; if ($h -is [string] -and $h.Trim()) { $h.Trim() } } catch { }"`) do set "MYSCRIBE_AT=%%H"

:found_home
set "MYSCRIBE_PYTHON=%MYSCRIBE_AT%\env\Scripts\python.exe"
if not exist "%MYSCRIBE_PYTHON%" goto :no_env

set "PYTHONHOME="
set "VIRTUAL_ENV="
set "PYTHONPATH=%MYSCRIBE_APP%"
set "PYTHONUTF8=1"
set "PYTHONPYCACHEPREFIX=%MYSCRIBE_AT%\pycache"
rem -P: never a scribe package from the folder the command is typed in.
"%MYSCRIBE_PYTHON%" -P -m scribe.cli %*
exit /b %ERRORLEVEL%

:no_app
>&2 echo myscribe: MyScribe's program files are not next to this command: "%MYSCRIBE_APP%" is missing. Reinstall MyScribe.
exit /b 1

:no_env
>&2 echo myscribe: MyScribe's engine is not installed in "%MYSCRIBE_AT%". Start MyScribe once, then try again.
exit /b 3
