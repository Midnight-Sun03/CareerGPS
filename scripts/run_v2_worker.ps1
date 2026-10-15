<#
.SYNOPSIS
    One recurring pass of the CareerGPS feed-v2 evaluation worker.

.DESCRIPTION
    Runs a single bounded drain of the persisted feed-v2 recompute queue and
    records the outcome to a log file.  This is the body of the Windows
    Scheduled Task installed by scripts\install_v2_worker_task.ps1.

    Design notes that matter operationally:

    * It does NOT use --all-profiles.  That flag asks for a full rebuild of
      every profile on every tick, which is a manual backfill action, not a
      recurring drain.  Recurring execution must consume only work that the
      write hooks have already enqueued (target edits, evidence edits and
      clean-revision activation), so a quiet tick costs almost nothing.
    * The process is NOT detached.  PowerShell waits for python and then exits
      with python's own exit code, so Task Scheduler sees the real result and
      never records a pass that silently did nothing.
    * Every pass appends start time, end time, duration, the processed-task
      summary and the exit status, so a scheduled run can always be matched to
      the work it performed.

    * Exactly one worker may run at a time on this machine.  A named Windows
      mutex is acquired immediately before the worker is launched and released
      in a finally block as soon as it returns, so the guard never outlives the
      worker invocation.  This matters because the scheduler's IgnoreNew policy
      only covers instances the scheduler launches: a manual drain or any other
      launcher could otherwise start a second worker beside a running one.
      That is not merely wasteful -- a second worker can reclaim a legitimately
      running task whose lease has expired and discard the first worker's
      completed build.
    * The mutex is released by Windows automatically if the owning process dies,
      so a crashed or killed worker cannot leave a stale lock behind.

    Exit codes:
      0 = pass completed (including a pass that found no work);
      3 = pass skipped because another worker already holds the mutex
          (nothing was started and nothing was written -- not a failure);
      non-zero otherwise = the worker itself failed, propagated from python.
#>
[CmdletBinding()]
param(
    [int] $BatchLimit = 25,
    [string] $WorkerId = 'careergps-scheduled-worker',
    # Left empty on purpose: Windows PowerShell evaluates param defaults before
    # $PSScriptRoot is populated, so defaults are resolved in the body below.
    [string] $LogDirectory = '',
    [string] $ProjectRoot = ''
)

$ErrorActionPreference = 'Stop'

# An unattended pass must never fail silently, and it must never fail before it
# can say why.  The log destination is resolved from the -ProjectRoot argument
# the installer always supplies, with $PSScriptRoot only as a fallback, because
# the scheduler's working directory is not guaranteed and a path built from an
# unset $PSScriptRoot fails before anything can be recorded.
$LogDirectory = if ($LogDirectory) {
    $LogDirectory
} elseif ($ProjectRoot) {
    Join-Path $ProjectRoot 'logs'
} elseif ($PSScriptRoot) {
    Join-Path $PSScriptRoot '..\logs'
} else {
    Join-Path (Get-Location).Path 'logs'
}
$LogFile = Join-Path $LogDirectory 'v2-worker.log'

function Write-WorkerLog([string] $Line) {
    # Appended with the pass timestamp so a scheduled pass can always be matched
    # to the work it performed.  The scheduled task is single-instance, but a
    # human running a manual drain at the same moment is legitimate.
    try {
        $directory = Split-Path -Parent $LogFile
        if (-not (Test-Path $directory)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
        Add-Content -Path $LogFile -Value "$Line" -Encoding UTF8
    }
    catch {
        # Never let logging be the reason a pass fails.
    }
}

# Written first, before anything else can fail, so an interpreter-level problem
# (bad arguments, execution policy, wrong interpreter) is still visible here.
Write-WorkerLog ("=== pass entered {0} pid={1} PSScriptRoot='{2}' cwd='{3}' ===" -f `
    (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $PID, $PSScriptRoot, (Get-Location).Path)

trap {
    Write-WorkerLog ("!!! pass FAILED {0} :: {1} :: {2}" -f `
        (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'),
        $_.Exception.GetType().Name, $_.Exception.Message)
    # 1 is the conventional non-zero status for an aborted pass.
    exit 1
}

# $PSScriptRoot is the scripts directory; the project root is its parent.  The
# log path is already resolved above and must not be recomputed here.
$ScriptDirectory = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
if (-not $ProjectRoot) { $ProjectRoot = Join-Path $ScriptDirectory '..' }

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw "Worker interpreter not found: $Python"
}
if (-not (Test-Path (Join-Path $ProjectRoot 'manage.py'))) {
    throw "manage.py not found under $ProjectRoot"
}

# The log directory is created here rather than assumed, so a fresh checkout
# works and so the scheduled task never fails on a missing folder.
if (-not (Test-Path $LogDirectory)) {
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
}
$LogDirectory = (Resolve-Path $LogDirectory).Path
$LogFile = Join-Path $LogDirectory 'v2-worker.log'

# ---------------------------------------------------------------- single
# instance guard ---------------------------------------------------------
# Acquired immediately before the worker is launched and released in the
# finally block directly after it returns, so the guard is never held for
# longer than the worker itself.  A zero timeout means a competing worker is
# never queued behind this one: it exits immediately and touches nothing.
$MutexName = 'Global\CareerGPS.v2.EvaluationWorker'
# A distinct, documented outcome: skipped because another worker holds the
# mutex.  Nothing was started, so nothing can have been written.
$MutexBusyExitCode = 3

$workerMutex = $null
$mutexHeld = $false
try {
    $workerMutex = New-Object System.Threading.Mutex($false, $MutexName)
    try {
        $mutexHeld = $workerMutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        # The previous owner died without releasing.  The name is free and this
        # process now owns it, so the pass proceeds rather than stalling.
        $mutexHeld = $true
    }
}
catch {
    # A guard that cannot be created must not silently allow concurrency, but it
    # must also not stop a legitimate pass.  Log it and run unguarded.
    Write-WorkerLog ("!!! mutex unavailable {0} :: {1} :: proceeding without the single-instance guard" -f `
        (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $_.Exception.Message)
    $workerMutex = $null
    $mutexHeld = $false
}

if ($workerMutex -and -not $mutexHeld) {
    Write-WorkerLog ("=== pass SKIPPED {0} pid={1} :: another v2 worker holds {2}; no worker started, queue and database untouched ===" -f `
        (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $PID, $MutexName)
    $workerMutex.Dispose()
    $workerMutex = $null
    exit $MutexBusyExitCode
}

$startedAt = Get-Date
Write-WorkerLog ("=== pass started {0} worker_id={1} limit={2} ===" -f `
    $startedAt.ToString('yyyy-MM-dd HH:mm:ss'), $WorkerId, $BatchLimit)

# Working directory is set explicitly: Django resolves the database and .env
# relative to the project root, so a wrong working directory would silently
# target a different database.
#
# ErrorActionPreference is lowered to 'Continue' for the worker call only.  Django
# writes ordinary system-check warnings to stderr, and Windows PowerShell turns a
# native command's stderr into a terminating NativeCommandError whenever
# ErrorActionPreference is Stop, which would abort a perfectly successful pass
# before its result is recorded.  The worker's real outcome is its exit status,
# which is read below and propagated.
$previousPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    Push-Location $ProjectRoot
    $output = & $Python 'manage.py' 'process_evaluation_queue' `
        '--limit' $BatchLimit '--worker-id' $WorkerId 2>&1
    $exitCode = $LASTEXITCODE
    Pop-Location
}
finally {
    $ErrorActionPreference = $previousPreference
    # Released here, immediately after the worker returns, and unconditionally:
    # a worker exception, a trap, or a hard failure still gives the mutex back.
    if ($mutexHeld -and $workerMutex) {
        try { $workerMutex.ReleaseMutex() } catch { }
        try { $workerMutex.Dispose() } catch { }
        $mutexHeld = $false
        $workerMutex = $null
    }
}

$output | ForEach-Object { Write-WorkerLog ("    $_") }
$finishedAt = Get-Date
$duration = [int]($finishedAt - $startedAt).TotalSeconds

# The summary line is the single field that proves what the pass did.
$summary = ($output | Where-Object { $_ -match 'Processed\s+\d+\s+feed-v2' } |
    Select-Object -First 1)
if (-not $summary) {
    $summary = '(no processed-task summary was reported)'
}
Write-WorkerLog ("=== pass finished {0} duration={1}s exit={2} :: {3} ===" -f `
    $finishedAt.ToString('yyyy-MM-dd HH:mm:ss'), $duration, $exitCode, $summary)

# Propagate the worker's real exit status so the scheduler records failure.
exit $exitCode
