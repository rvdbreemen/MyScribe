# Stops a running MyScribe before an update or an uninstall replaces it
# (TASK-112.04, ADR-028). Run by myscribe.iss: extracted to {tmp} in
# PrepareToInstall, and from {app} in InitializeUninstall.
#
# Only what is this install's own is stopped:
#   * a server whose /health names a launcher_pid running from InstallDir is
#     asked through its stop route, so its running job goes back to the front
#     of the queue, and its app pid is waited for - not the port, which
#     uvicorn closes before the lifespan has stopped the runner;
#   * then every process whose executable lies under InstallDir is ended with
#     its tree: a windowed launcher that stays open after its app stopped, and
#     any 0.8.x launcher, whose server has no stop route (taskkill /T takes its
#     python child and that child's runners with it).
# A clone or another install answering the same port is left alone.
#
# Exit codes, read by the [Code] section:
#   0  nothing of this install was running
#   10 something was running and has stopped
#   1  something is still running, or this script failed (the reason is
#      in the log)
#
# Windows PowerShell 5.1: no `??`, no -NoProxy. What it did goes to
# MyScribe-launcher.log beside MyScribe.location, where the launcher writes
# what it says before it has a home (TASK-112.02).

param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [int]$Port = 4242,
    [int]$Wait = 60,
    [string]$Log = (Join-Path $env:LOCALAPPDATA 'MyScribe-launcher.log')
)

$ErrorActionPreference = 'Stop'

# Whatever goes wrong unforeseen is said before the exit, so "1" in the
# installer always has a line in the log behind it.
trap {
    Say "Stopping MyScribe failed: $($_.Exception.Message)"
    exit 1
}

function Say([string]$text) {
    $line = '{0} setup: {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Write-Output $line
    try { Add-Content -LiteralPath $Log -Value $line -Encoding UTF8 } catch { }
}

function Invoke-Local([string]$method, [string]$path) {
    # Never through a proxy: one set for this user would turn "not running"
    # into a timeout, the trap `loopback_opener` exists for in the launcher.
    $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port$path")
    $request.Method = $method
    $request.Proxy = $null
    $request.Timeout = 5000
    if ($method -eq 'POST') { $request.ContentLength = 0 }
    $response = $request.GetResponse()
    try {
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        return $reader.ReadToEnd()
    } finally {
        $response.Close()
    }
}

function Get-Health {
    try {
        $body = Invoke-Local 'GET' '/health' | ConvertFrom-Json
        if ($body.ok) { return $body }
    } catch { }
    return $null
}

function Test-Running([int]$id) {
    return [bool](Get-Process -Id $id -ErrorAction SilentlyContinue)
}

function Wait-Gone([int]$id, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-Running $id)) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return -not (Test-Running $id)
}

function Get-Ours {
    $root = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\') + '\'
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)
    })
}

function Get-OurAncestor([int]$id) {
    # The process of this install above the app, if there is one. The app's
    # parent is not the launcher on Windows: it is uv's trampoline,
    # env\Scripts\python.exe in the home, and 0.9.0 names that trampoline as
    # launcher_pid (TASK-117). So the chain is walked, a few steps at most.
    $ours = Get-Ours
    $all = @{}
    foreach ($process in (Get-CimInstance Win32_Process)) { $all[[int]$process.ProcessId] = $process }
    for ($step = 0; $step -lt 8 -and $all.ContainsKey($id); $step++) {
        $mine = $ours | Where-Object { $_.ProcessId -eq $id }
        if ($mine) { return $mine }
        $id = [int]$all[$id].ParentProcessId
    }
    return $null
}

$stopped = $false
$health = Get-Health
$launcher = $null
if ($health -and $health.launcher_pid) {
    $launcher = Get-Ours | Where-Object { $_.ProcessId -eq [int]$health.launcher_pid }
}
if (-not $launcher -and $health -and $health.pid) {
    $launcher = Get-OurAncestor ([int]$health.pid)
}
if ($launcher -and $health.pid) {
    Say "MyScribe is running (process $($health.pid)); asking it to stop."
    try {
        Invoke-Local 'POST' '/api/shutdown' | Out-Null
        if (Wait-Gone ([int]$health.pid) $Wait) {
            Say 'MyScribe stopped; a job that was running starts over at the next start.'
            $stopped = $true
        } else {
            Say "MyScribe did not stop within $Wait s; ending it."
        }
    } catch {
        Say "The stop route did not answer ($($_.Exception.Message)); ending MyScribe."
    }
}

$left = Get-Ours
foreach ($process in $left) {
    Say "Ending $($process.Name) (process $($process.ProcessId)) and what it started."
    # Through cmd, which swallows taskkill's stderr itself: Windows PowerShell
    # 5.1 turns a native command's stderr into a terminating error under
    # 'Stop', and taskkill says "not found" for a process its parent's tree
    # kill already took - an exit 1 for a MyScribe that had stopped.
    $null = & cmd.exe /c "taskkill /T /F /PID $($process.ProcessId) >nul 2>&1"
}
foreach ($process in $left) {
    if (-not (Wait-Gone ([int]$process.ProcessId) 15)) {
        Say "$($process.Name) (process $($process.ProcessId)) is still running."
        exit 1
    }
    $stopped = $true
}

if ($stopped) { exit 10 }
exit 0
