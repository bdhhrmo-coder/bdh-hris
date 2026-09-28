@echo off
REM Wrapper for Windows Task Scheduler - see DEPLOYMENT.md for how to
REM register this as a nightly task. Kept as a thin .bat instead of pointing
REM Task Scheduler at python.exe directly so the venv activation and working
REM directory are always right, no matter how the task is triggered.
REM
REM Task Scheduler action should be:
REM   Program/script:  C:\BDH-HRIS\scripts\run_backup.bat
REM   Start in:         C:\BDH-HRIS\scripts
REM (adjust the path if the project isn't at C:\BDH-HRIS)

setlocal

set PROJECT_DIR=%~dp0..
set PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe

if not exist "%PYTHON_EXE%" (
    echo Could not find %PYTHON_EXE% - is the virtualenv set up at %PROJECT_DIR%\.venv?
    exit /b 1
)

REM Load .env into this process's environment, same convention as
REM install_service.ps1 - this script has no third-party dependency on
REM python-dotenv, it just reads KEY=VALUE lines itself.
if exist "%PROJECT_DIR%\.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%PROJECT_DIR%\.env") do (
        set "%%A=%%B"
    )
)

"%PYTHON_EXE%" "%PROJECT_DIR%\scripts\backup_db.py"
exit /b %ERRORLEVEL%
