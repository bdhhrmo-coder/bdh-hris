@echo off
rem BDH HRIS - puts the "BDH HRIS" icon on this PC's desktop (current user).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_desktop_shortcut.ps1"
pause
