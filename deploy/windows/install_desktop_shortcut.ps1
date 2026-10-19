<#
  BDH HRIS - desktop shortcut (Batch 11, 2026-10-19)

  Puts a "BDH HRIS" icon (BDH logo) on the desktop. Double-clicking it opens
  the HRIS login page in the PC's default browser. It is only a shortcut:
  nothing is installed and no settings are changed.

  HOW TO USE (IT staff, once per PC)
    Easiest: double-click "Install BDH HRIS shortcut.bat" in this folder.
    Or, in PowerShell:
      powershell -ExecutionPolicy Bypass -File install_desktop_shortcut.ps1
    For every user of the PC (needs "Run as administrator"):
      powershell -ExecutionPolicy Bypass -File install_desktop_shortcut.ps1 -AllUsers

  AT GO-LIVE: change $DefaultUrl below to the production server address
  (or pass -Url "http://hris.bdh.local/hris/login/") and run it again on
  each PC. Running it again simply replaces the old shortcut.
#>
param(
    [string]$Url = "",
    [switch]$AllUsers
)

# >>> The one line to change at go-live <<<
$DefaultUrl = "http://192.168.44.169:8000/hris/login/"

if (-not $Url) { $Url = $DefaultUrl }
$ErrorActionPreference = "Stop"

# Keep the icon in a fixed place, so the shortcut still shows the logo if
# this folder is moved or deleted later.
$iconSource = Join-Path $PSScriptRoot "bdh-hris.ico"
if ($AllUsers) {
    $iconFolder = Join-Path $env:ProgramData "BDH-HRIS"
    $desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
} else {
    $iconFolder = Join-Path $env:LOCALAPPDATA "BDH-HRIS"
    $desktop = [Environment]::GetFolderPath("Desktop")
}
New-Item -ItemType Directory -Force -Path $iconFolder | Out-Null
$iconPath = Join-Path $iconFolder "bdh-hris.ico"
Copy-Item -Path $iconSource -Destination $iconPath -Force

# A Windows web shortcut (.url) with the BDH logo as its icon.
$shortcut = Join-Path $desktop "BDH HRIS.url"
$lines = @(
    "[InternetShortcut]",
    "URL=$Url",
    "IconFile=$iconPath",
    "IconIndex=0"
)
Set-Content -Path $shortcut -Value $lines -Encoding ASCII

Write-Host "Done. 'BDH HRIS' is on the desktop: $shortcut"
Write-Host "It opens: $Url"
