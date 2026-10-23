<#
.SYNOPSIS
  Set up the Star Citizen -> SimHub motion pipeline on Windows (sc-motion-bridge).

.DESCRIPTION
  Without -Apply: shows the plan (read-only): the dependencies D1-D5 and every step as
                  DONE / TODO / BLOCKED, and every RSI Launcher, LIVE and SimHub location found.
  With -Apply:    installs the Python modules grpcio and protobuf for your user if they are
                  missing, then runs setup\windows\sc_install_win.py apply: when several
                  locations are found it shows a menu, saves the choice in
                  %APPDATA%\sc-motion\config.json, and runs the TODO steps only when D1-D5
                  are all DONE.
  -Select:        with -Apply, show the menus again even when a choice is saved.
  -RsiLauncher, -LiveDir, -SimHubDir: choose a location directly (no menu).
  -MotionHardware none|thanos|other: the optional motion hardware choice (step 12) directly.
                  Optional and never required: any platform SimHub supports works; without it,
                  -Apply asks once (Enter = none).

  Steps 1 and 7 write under C:\ProgramData\D-BOX, step 9 into the SimHub folder. If they
  report "permission denied", run this again from an elevated PowerShell (right-click > Run as administrator) with the
  SAME Windows account (the certificate files live in that account's user folder).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup\windows\Install-SCMotion.ps1
  powershell -ExecutionPolicy Bypass -File .\setup\windows\Install-SCMotion.ps1 -Apply
  powershell -ExecutionPolicy Bypass -File .\setup\windows\Install-SCMotion.ps1 -Apply -Select
  powershell -ExecutionPolicy Bypass -File .\setup\windows\Install-SCMotion.ps1 -Apply -SimHubDir 'D:\SimHub'
#>
param([switch]$Apply, [switch]$Select, [string]$RsiLauncher, [string]$LiveDir, [string]$SimHubDir,
      [ValidateSet('none', 'thanos', 'other')][string]$MotionHardware)
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$installer = Join-Path $here 'sc_install_win.py'

function Find-Python {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return @($py.Source, '-3') }
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p -and $p.Source -notlike '*WindowsApps*') { return @($p.Source) }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host 'Python 3 was not found. Install it, then open a new PowerShell and run this again:' -ForegroundColor Yellow
    Write-Host '  winget install -e --id Python.Python.3.13'
    exit 3
}
$exe = $python[0]
$opts = @()
if ($RsiLauncher) { $opts += @('--rsi-launcher', $RsiLauncher) }
if ($LiveDir)     { $opts += @('--live-dir', $LiveDir) }
if ($SimHubDir)   { $opts += @('--simhub-dir', $SimHubDir) }
if ($MotionHardware) { $opts += @('--motion-hw', $MotionHardware) }
if ($Select -and -not $Apply) {
    Write-Host '-Select only works together with -Apply.' -ForegroundColor Yellow
    exit 1
}
$pyArgs = @($python | Select-Object -Skip 1)

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($Apply) {
    & $exe @pyArgs -c "import grpc, google.protobuf" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing grpcio and protobuf for your user (pip) ...'
        & $exe @pyArgs -m pip install --user grpcio protobuf
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'pip failed (see above). Nothing else was changed.' -ForegroundColor Red
            exit 4
        }
    }
    if (-not $admin) {
        Write-Host 'Not elevated: steps that write under C:\ProgramData\D-BOX may fail with "permission denied".' -ForegroundColor Yellow
        Write-Host 'If they do, run this again from PowerShell opened with "Run as administrator".' -ForegroundColor Yellow
    }
    if ($Select) { $opts += '--select' }
    & $exe @pyArgs $installer apply @opts
} else {
    & $exe @pyArgs $installer plan @opts
}
exit $LASTEXITCODE
