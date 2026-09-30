<#
.SYNOPSIS
    Installs BDH HRIS as a Windows Service (Waitress, supervised by NSSM).

.DESCRIPTION
    Per CLAUDE.md §2: the app server is Waitress, run as a Windows Service
    via NSSM, so it starts automatically on server boot/reboot and restarts
    itself if it crashes - without anyone needing to leave a console window
    open or remember to start it by hand.

    This script does NOT install NSSM itself - NSSM is a small third-party
    tool with no official installer, so download it once from
    https://nssm.cc/download, unzip it, and either:
      - put nssm.exe on the system PATH, or
      - pass its full path with -NssmPath.

    Safe to re-run: if the service already exists, it is removed first and
    re-created with the settings below (handy after moving the project
    folder or changing the venv path).

.PARAMETER ServiceName
    Windows service name. Default: BDH-HRIS

.PARAMETER ProjectDir
    Full path to the project checkout (the folder containing manage.py and
    serve.py). Default: the parent of this script's own location, i.e. this
    script is expected to stay at <project>\deploy\windows\.

.PARAMETER PythonExe
    Full path to python.exe INSIDE the project's virtualenv - not the
    system Python. Default: <ProjectDir>\.venv\Scripts\python.exe

.PARAMETER NssmPath
    Full path to nssm.exe. Default: assumes nssm.exe is on PATH.

.EXAMPLE
    # Run from an elevated (Administrator) PowerShell prompt:
    cd C:\BDH-HRIS\deploy\windows
    .\install_service.ps1

.EXAMPLE
    .\install_service.ps1 -NssmPath "C:\Tools\nssm-2.24\win64\nssm.exe"
#>

[CmdletBinding()]
param(
    [string]$ServiceName = "BDH-HRIS",
    [string]$ProjectDir  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$PythonExe   = $null,
    [string]$NssmPath    = "nssm.exe"
)

$ErrorActionPreference = "Stop"

# --- Must run elevated: NSSM needs to register a service with SCM --------
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Please re-run this script from an elevated (Administrator) PowerShell prompt."
    exit 1
}

if (-not $PythonExe) { $PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe" }
$ServeScript = Join-Path $ProjectDir "serve.py"
$EnvFile     = Join-Path $ProjectDir ".env"
$LogDir      = Join-Path $ProjectDir "logs"

Write-Host "Project dir : $ProjectDir"
Write-Host "Python exe  : $PythonExe"
Write-Host "serve.py    : $ServeScript"
Write-Host ""

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python not found at $PythonExe. Create the virtualenv first:`n  py -3 -m venv .venv`n  .venv\Scripts\pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path $ServeScript)) {
    Write-Error "serve.py not found at $ServeScript - is -ProjectDir correct?"
    exit 1
}
if (-not (Test-Path $EnvFile)) {
    Write-Warning "$EnvFile does not exist yet. serve.py will still start, but Waitress will run with defaults (SQLite, DEBUG on) until you create it - copy .env.example to .env and fill it in, then restart the service (nssm restart $ServiceName)."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# --- Confirm NSSM is reachable --------------------------------------------
$nssmCmd = Get-Command $NssmPath -ErrorAction SilentlyContinue
if (-not $nssmCmd) {
    Write-Error "nssm.exe not found ('$NssmPath'). Download it from https://nssm.cc/download, then either put it on PATH or re-run with -NssmPath 'C:\path\to\nssm.exe'."
    exit 1
}
$Nssm = $nssmCmd.Source

# --- Re-create the service cleanly ----------------------------------------
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Service '$ServiceName' already exists - stopping and removing it first..."
    & $Nssm stop $ServiceName confirm | Out-Null
    & $Nssm remove $ServiceName confirm | Out-Null
}

Write-Host "Installing service '$ServiceName'..."
# NSSM stores whatever we pass as the app's command-line arguments VERBATIM,
# and re-quoting it ourselves doesn't help - `nssm set` strips a
# surrounding quote pair itself before storing the value, so the quotes
# never survive to the command line NSSM builds at service start. If
# $ServeScript contains a space (a username like "C:\Users\Jane Doe\...",
# for instance), that unquoted path gets truncated at the space when
# Windows parses the service's command line, and Waitress fails to start.
#
# The reliable fix is to sidestep string-quoting entirely: AppDirectory is
# passed to CreateProcess as its own discrete parameter (the working
# directory), never pasted into the command-line string, so it's immune to
# this problem regardless of spaces. Point the service at the project
# folder via AppDirectory and launch serve.py by its bare relative name -
# with no spaces of its own, there's nothing left to split on.
& $Nssm install $ServiceName $PythonExe
& $Nssm set $ServiceName AppParameters "serve.py"
& $Nssm set $ServiceName AppDirectory $ProjectDir
& $Nssm set $ServiceName AppStdout (Join-Path $LogDir "service-stdout.log")
& $Nssm set $ServiceName AppStderr (Join-Path $LogDir "service-stderr.log")
& $Nssm set $ServiceName AppRotateFiles 1
& $Nssm set $ServiceName AppRotateOnline 1
& $Nssm set $ServiceName AppRotateBytes 10485760   # rotate each log at 10 MB
& $Nssm set $ServiceName Start SERVICE_AUTO_START
& $Nssm set $ServiceName DisplayName "BDH HRIS (Waitress)"
& $Nssm set $ServiceName Description "Bataraza District Hospital HRIS web application, served by Waitress."

# .env is loaded by serve.py itself (bdh_hris/envfile.py), not by NSSM.
# An earlier version of this script loaded .env into the service's
# environment here via NSSM's AppEnvironmentExtra - dropped after it was
# found, in testing on a real Windows machine, to silently lose one
# variable out of a real .env's worth of values (a reproducible bug in
# how NSSM re-parses its own command line for that setting, not something
# worth working around a second time). Letting the app own its own
# configuration loading is both simpler and more reliable than routing it
# through the service supervisor.
Write-Host ""
Write-Host "Starting service '$ServiceName'..."
& $Nssm start $ServiceName

Start-Sleep -Seconds 2
Get-Service -Name $ServiceName | Format-Table -AutoSize

Write-Host ""
Write-Host "Done. Logs: $LogDir"
Write-Host "If the service isn't running, check service-stderr.log there first."
Write-Host "Re-run this script any time after editing .env or moving the project - it's safe to re-run."
