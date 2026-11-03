@echo off
REM Wrapper for Windows Task Scheduler: runs one manage.py command from the
REM project folder and appends its output and exit code to
REM logs\scheduled-jobs.log. manage.py loads .env from its own folder, so
REM this script does not read .env itself. See DEPLOYMENT.md section 8a.
REM
REM Usage:  run_manage.bat cto_year_end

setlocal

REM Task Scheduler starts in C:\Windows\System32: move to the project folder.
cd /d "%~dp0.."
set LOG_FILE=logs\scheduled-jobs.log

if "%~1"=="" (
    echo Usage: run_manage.bat ^<manage.py command^>
    exit /b 2
)
if not exist ".venv\Scripts\python.exe" (
    echo Could not find .venv\Scripts\python.exe in %CD% - is the virtualenv set up?
    exit /b 1
)
if not exist logs mkdir logs

echo [%DATE% %TIME%] manage.py %* >> "%LOG_FILE%"
".venv\Scripts\python.exe" manage.py %* >> "%LOG_FILE%" 2>&1
set RESULT=%ERRORLEVEL%
echo [%DATE% %TIME%] exit %RESULT% >> "%LOG_FILE%"
exit /b %RESULT%
