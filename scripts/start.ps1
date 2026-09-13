<#
.SYNOPSIS
Start MyScribe on Windows.

.DESCRIPTION
Three things this does that typing the command by hand does not:

  * It uses the venv's python. `python -m scribe` finds whatever is on PATH,
    which here is C:\Python312 without the dependencies, and the failure that
    produces names a missing module rather than the real mistake.
  * It refuses to start a second instance on a port that already answers. Two
    supervisors against one SQLite file is not a scenario this app is written
    for (ADR-002 coordinates processes, but the second one has no business
    existing).
  * With -Detached it survives the shell that started it. A long transcribe
    queue outlives a terminal window, and an agent's background task can be
    reaped by its harness mid-queue - that is what this flag is for. The
    supervisor picks the orphaned job back up on the next start and marks it
    interrupted, so nothing is lost, but the queue does stand still until
    someone notices.

Anything in -Args is handed to `python -m scribe`.

.EXAMPLE
scripts\start.ps1
Runs in this window; Ctrl+C stops it.

.EXAMPLE
scripts\start.ps1 -Detached
Starts hidden and keeps running after this window closes.

.EXAMPLE
scripts\start.ps1 -Args '--port','4299','--no-supervisor'
A second instance to poke at, without disturbing the one on 4242.
#>
[CmdletBinding()]
param(
    [switch]$Detached,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args = @()
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $py)) {
    Write-Error @"
No venv at $py
README.md has the install; the short version is:
  py -3.12 -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
  .venv\Scripts\pip install -r requirements-gpu.txt --index-url https://download.pytorch.org/whl/cu128 --extra-index-url https://pypi.org/simple
"@
    exit 1
}

# The port the app will actually use, so the check below asks about the right
# one. Both `--port N` and `--port=N` appear in the README.
$port = 4242
for ($i = 0; $i -lt $Args.Count; $i++) {
    if ($Args[$i] -eq '--port' -and ($i + 1) -lt $Args.Count) { $port = $Args[$i + 1] }
    elseif ($Args[$i] -like '--port=*') { $port = $Args[$i].Substring(7) }
}

$live = Test-NetConnection -ComputerName '127.0.0.1' -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue
if ($live) {
    Write-Output "Something already answers on http://127.0.0.1:$port - not starting a second one."
    exit 0
}

if ($Detached) {
    $proc = Start-Process -FilePath $py -ArgumentList (@('-m', 'scribe') + $Args) `
        -WorkingDirectory $repo -WindowStyle Hidden -PassThru
    Write-Output "MyScribe started detached, pid $($proc.Id)"
    Write-Output "  http://127.0.0.1:$port"
    Write-Output "  stop: Stop-Process -Id $($proc.Id)"
}
else {
    Push-Location $repo
    try { & $py -m scribe @Args }
    finally { Pop-Location }
}
