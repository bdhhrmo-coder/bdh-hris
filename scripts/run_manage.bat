@echo off
REM Wrapper for Windows Task Scheduler: runs one manage.py command with the
REM BDH_HRIS_* settings from .env loaded (Task Scheduler does not read .env,
REM so without this the command would quietly use a local SQLite database).
REM Same .env convention as run_backup.bat. Output is appended to
REM logs\scheduled-jobs.log. See DEPLOYMENT.md section 8a.
REM
REM Usage:  run_manage.bat cto_year_end

setlocal

set PROJECT_DIR=%~dp0..
set PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe
set LOG_FILE=%PROJECT_DIR%\logs\scheduled-jobs.log

if "%~1"=="" (
    echo Usage: run_manage.bat ^<manage.py command^>
    exit /b 2
)
if not exist "%PYTHON_EXE%" (
    echo Could not find %PYTHON_EXE% - is the virtualenv set up at %PROJECT_DIR%\.venv?
    exit /b 1
)
if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"

if exist "%PROJECT_DIR%\.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%PROJECT_DIR%\.env") do (
        set "%%A=%%B"
    )
)

echo [%DATE% %TIME%] manage.py %* >> "%LOG_FILE%"
"%PYTHON_EXE%" "%PROJECT_DIR%\manage.py" %* >> "%LOG_FILE%" 2>&1
set RESULT=%ERRORLEVEL%
echo [%DATE% %TIME%] exit %RESULT% >> "%LOG_FILE%"
exit /b %RESULT%
