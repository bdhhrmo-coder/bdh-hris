<#
.SYNOPSIS
    Stops and removes the BDH-HRIS Windows Service.

.DESCRIPTION
    Does not touch the database, uploaded files, or the project folder -
    only the Windows Service registration created by install_service.ps1.

.PARAMETER ServiceName
    Must match the name used when installing. Default: BDH-HRIS

.PARAMETER NssmPath
    Full path to nssm.exe. Default: assumes nssm.exe is on PATH.

.EXAMPLE
    # Run from an elevated (Administrator) PowerShell prompt:
    .\uninstall_service.ps1
#>

[CmdletBinding()]
param(
    [string]$ServiceName = "BDH-HRIS",
    [string]$NssmPath    = "nssm.exe"
)

$ErrorActionPreference = "Stop"

$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Please re-run this script from an elevated (Administrator) PowerShell prompt."
    exit 1
}

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Service '$ServiceName' is not installed - nothing to do."
    exit 0
}

$nssmCmd = Get-Command $NssmPath -ErrorAction SilentlyContinue
if (-not $nssmCmd) {
    Write-Error "nssm.exe not found ('$NssmPath'). Pass -NssmPath 'C:\path\to\nssm.exe' if it isn't on PATH."
    exit 1
}
$Nssm = $nssmCmd.Source

Write-Host "Stopping '$ServiceName'..."
& $Nssm stop $ServiceName confirm | Out-Null

Write-Host "Removing '$ServiceName'..."
& $Nssm remove $ServiceName confirm | Out-Null

Write-Host "Done. The project folder, database, and uploaded files were not touched."
