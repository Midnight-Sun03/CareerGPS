<#
.SYNOPSIS
    Install, inspect, disable or remove the CareerGPS v2 worker scheduled task.

.DESCRIPTION
    Wraps schtasks so the recurring worker is configured identically every time
    and can be inspected or removed without guesswork.

    Why Task Scheduler: the queue is database-backed and the claim is a
    compare-and-swap with a lease, so a second concurrent worker is safe for
    correctness.  It is still wasteful and makes the logs ambiguous, so the task
    is registered with "do not start a new instance" (see -MultipleInstances).

    The scheduled action runs the runner and WAITS for it.  Nothing is detached,
    so the recorded last result is the worker's real exit status.

.PARAMETER Action
    Install (default), Inspect, Enable, Disable or Remove.

.PARAMETER IntervalMinutes
    How often a pass runs.  The default is 5 minutes: short enough that a change
    is reflected promptly, long enough that a pass costs one short command.

.PARAMETER BatchLimit
    Maximum tasks claimed per pass.  Bounded so a single pass cannot run for an
    unbounded time and collide with the next trigger.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_v2_worker_task.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_v2_worker_task.ps1 -Action Inspect
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_v2_worker_task.ps1 -Action Remove
#>
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Inspect', 'Enable', 'Disable', 'Remove')]
    [string] $Action = 'Install',
    [string] $TaskName = 'CareerGPS v2 Evaluation Worker',
    [int] $IntervalMinutes = 5,
    [int] $BatchLimit = 25,
    [string] $RunAsUser = $env:USERNAME,
    # Interactive = only runs while the user is signed in, and is installable
    # without elevation.  S4U = runs whether or not anyone is signed in, which is
    # what an unattended worker really needs, but creating it requires an
    # elevated prompt.
    [ValidateSet('Interactive', 'S4U')]
    [string] $LogonType = 'Interactive'
)

$ErrorActionPreference = 'Stop'

$ScriptDirectory = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDirectory '..')).Path
$Runner = Join-Path $ScriptDirectory 'run_v2_worker.ps1'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $Runner)) { throw "Runner script not found: $Runner" }
if (-not (Test-Path $Python)) { throw "Worker interpreter not found: $Python" }

switch ($Action) {

    'Install' {
        # The script is invoked directly and the scheduled action simply does
        # not return until the script exits, so no extra switch is needed to
        # "wait".  -Wait must NOT be passed: it is a Start-Process parameter, not
        # a powershell.exe one, and powershell.exe documents it as unsupported
        # with -File.  Passing it makes the interpreter hang without running the
        # script, which is exactly the silent failure this comment prevents.
        $arguments = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', $Runner, '-BatchLimit', $BatchLimit,
            '-ProjectRoot', $ProjectRoot
        )
        # Register as XML so the overlap policy and the conditions can be set
        # explicitly; the schtasks command line cannot express them.
        $xmlLogonType = if ($LogonType -eq 'S4U') { 'S4U' } else { 'InteractiveToken' }
        $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Recurring bounded drain of the CareerGPS feed-v2 recompute queue.</Description>
    <Author>CareerGPS</Author>
  </RegistrationInfo>
  <Triggers>
    <!-- A TimeTrigger with an explicit StopAtDurationEnd is the shape Task
         Scheduler reliably dispatches on this host.  A CalendarTrigger
         constrained by ScheduleByDay is accepted and its NextRunTime advances,
         but the action is never launched: LastRunTime stays unset and the last
         result remains 0x41303 (SCHED_S_TASK_HAS_NOT_RUN). -->
    <TimeTrigger>
      <Repetition>
        <Interval>PT${IntervalMinutes}M</Interval>
        <StopAtDurationEnd>true</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK')</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>${RunAsUser}</UserId>
      <LogonType>${xmlLogonType}</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <!-- Never run a second instance while one is still running. -->
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <!-- Long enough for the slowest full generation rebuild, but bounded so a
         genuinely stuck pass is killed and its queue lease becomes reclaimable,
         rather than blocking every later trigger for hours. -->
    <!-- Long enough for the slowest full generation rebuild, but bounded so a
         genuinely stuck pass is killed and its queue lease becomes reclaimable,
         rather than blocking every later trigger for hours. -->
    <ExecutionTimeLimit>PT45M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>$($arguments -join ' ')</Arguments>
      <WorkingDirectory>$ProjectRoot</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
        $xmlFile = Join-Path $env:TEMP 'careergps_v2_worker_task.xml'
        # schtasks requires UTF-16 with a BOM for XML task definitions.
        [System.IO.File]::WriteAllText($xmlFile, $xml, [System.Text.Encoding]::Unicode)

        # Probing for an existing task makes schtasks print "cannot find the
        # file" to stderr on the normal first-run path.  Windows PowerShell turns
        # a native command's stderr into a terminating error whenever
        # ErrorActionPreference is Stop, even when the command itself succeeds or
        # fails cleanly, so preference is dropped for these calls only and the
        # exit status is used as the real signal.
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $probeOutput = & schtasks /query /tn $TaskName 2>&1
        $probeSucceeded = ($LASTEXITCODE -eq 0)
        if ($probeSucceeded) {
            Write-Host "Replacing existing task '$TaskName'."
            & schtasks /delete /tn $TaskName /f | Out-Null
        }
        else {
            Write-Host "No existing task named '$TaskName'; creating it."
        }
        $createOutput = & schtasks /create /tn $TaskName /xml $xmlFile /f 2>&1
        $createSucceeded = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $previousPreference
        if (-not $createSucceeded) {
            if ($xml -match 'S4U') {
                throw ("schtasks /create failed: $createOutput`n" +
                       "S4U needs an elevated prompt. Re-run from an " +
                       "Administrator shell, or install with " +
                       "-LogonType Interactive (which only runs while the " +
                       "user is signed in).")
            }
            throw "schtasks /create failed: $createOutput"
        }
        Remove-Item $xmlFile -Force -ErrorAction SilentlyContinue
        Write-Host "Installed '$TaskName'."
        Write-Host "  every ${IntervalMinutes} minute(s), batch limit $BatchLimit"
        Write-Host "  runner : $Runner"
        Write-Host "  cwd    : $ProjectRoot"
        Write-Host "  log    : $(Join-Path $ProjectRoot 'logs\v2-worker.log')"
    }

    'Inspect' {
        # Shows the last and next run times and the last result, which is the
        # worker's real exit status because the action waits for it.
        schtasks /query /tn $TaskName /fo LIST /v
    }

    'Enable'  { schtasks /change /tn $TaskName /enable  | Out-Null; Write-Host "Enabled '$TaskName'." }
    'Disable' { schtasks /change /tn $TaskName /disable | Out-Null; Write-Host "Disabled '$TaskName'." }
    'Remove'  {
        schtasks /delete /tn $TaskName /f | Out-Null
        Write-Host "Removed '$TaskName'. Queue rows are untouched; unscheduled work simply stops."
    }
}

