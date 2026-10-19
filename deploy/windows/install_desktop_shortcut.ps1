<#
  BDH HRIS - desktop shortcut (Batch 11, 2026-10-19)

  Puts a "BDH HRIS" icon (the BDH HRIS app icon) on the desktop.
  Double-clicking it opens the HRIS login page in the PC's default browser.
  It is only a shortcut: nothing is installed and no settings are changed.

  HOW TO USE (IT staff, once per PC)
    Easiest: double-click "Install BDH HRIS shortcut.bat" in this folder.
    Or, in PowerShell:
      powershell -ExecutionPolicy Bypass -File install_desktop_shortcut.ps1
    For every user of the PC (needs "Run as administrator"):
      powershell -ExecutionPolicy Bypass -File install_desktop_shortcut.ps1 -AllUsers

  THE ADDRESS
    The shortcut uses the server's computer NAME, not its IP address. The IP
    address can change by itself (the router hands them out), and every
    shortcut would then stop working; the name stays the same.
    AT GO-LIVE: change $DefaultUrl below to the production server's name
    (or pass -Url "http://hris.bdh.local/hris/login/") and run it again on
    each PC. Running it again simply replaces the old shortcut.
#>
param(
    [string]$Url = "",
    [switch]$AllUsers
)

# >>> The one line to change at go-live <<<
$DefaultUrl = "http://DESKTOP-HG74U4I:8000/hris/login/"

if (-not $Url) { $Url = $DefaultUrl }
$ErrorActionPreference = "Stop"

# Keep the icon in a fixed place, so the shortcut still shows it if this
# folder is moved or deleted later.
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

# Remove the first version of the shortcut (a .url file), if it is there.
$oldShortcut = Join-Path $desktop "BDH HRIS.url"
if (Test-Path $oldShortcut) { Remove-Item $oldShortcut -Force }

# A normal Windows shortcut (.lnk). Windows shows custom icons on these
# reliably. It asks Windows to open the address, which uses the default browser.
$shortcutPath = Join-Path $desktop "BDH HRIS.lnk"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($shortcutPath)
$lnk.TargetPath = Join-Path $env:WINDIR "explorer.exe"
$lnk.Arguments = $Url
$lnk.IconLocation = "$iconPath,0"
$lnk.Description = "BDH HRIS - Human Resource Information System"
$lnk.Save()

Write-Host "Done. 'BDH HRIS' is on the desktop: $shortcutPath"
Write-Host "It opens: $Url"
