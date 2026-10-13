@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto folder_error
echo === PAPER platform: Windows setup ===
echo First setup needs Internet access. No exchange API key is needed.
echo This script installs Python for your user if needed, then project dependencies.
if not exist "app.py" goto files_error
if not exist "requirements.lock" goto files_error
if exist ".venv\Scripts\python.exe" goto check_environment
if exist ".venv" goto environment_error

call :find_python
if not errorlevel 1 goto create_environment
where winget >nul 2>&1
if errorlevel 1 goto manual_python
echo Installing official Python 3.12 using Windows Package Manager...
winget install --id Python.Python.3.12 --exact --source winget --scope user --silent --disable-interactivity --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto python_install_error
call :find_python
if errorlevel 1 goto python_detection_error

:create_environment
echo Creating project-local Python environment...
%QT_PYTHON_COMMAND% -m venv .venv
if errorlevel 1 goto environment_error

:check_environment
".venv\Scripts\python.exe" -c "import sys; assert (3, 12) <= sys.version_info[:2] < (3, 14), 'Python 3.12 or 3.13 required'"
if errorlevel 1 goto environment_error
echo Installing hash-verified dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --require-hashes -r requirements.lock
if errorlevel 1 goto dependency_error
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto dependency_error
echo Running automated PAPER safety and account tests...
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto test_error
echo Setup and tests passed. Starting the PAPER page...
call "%~dp0start_windows.cmd"
exit /b %errorlevel%

:find_python
set "QT_PYTHON_COMMAND="
for %%V in (3.12 3.13) do (
    py -%%V -c "import sys; assert (3, 12) <= sys.version_info[:2] < (3, 14)" >nul 2>&1
    if not errorlevel 1 (
        set "QT_PYTHON_COMMAND=py -%%V"
        exit /b 0
    )
)
for %%V in (312 313) do (
    if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
        "%LocalAppData%\Programs\Python\Python%%V\python.exe" -c "import sys; assert (3, 12) <= sys.version_info[:2] < (3, 14)" >nul 2>&1
        if not errorlevel 1 (
            set "QT_PYTHON_COMMAND="%LocalAppData%\Programs\Python\Python%%V\python.exe""
            exit /b 0
        )
    )
)
exit /b 1

:manual_python
echo Windows Package Manager was not found.
echo Install Python 3.12 from https://www.python.org/downloads/windows/
echo Keep Python Launcher enabled, then double-click this script again.
goto failure

:python_install_error
echo Python installation failed. Read the message above.
echo Do not bypass signature or checksum verification.
echo You can install Python 3.12 from https://www.python.org/downloads/windows/
goto failure

:python_detection_error
echo Python was installed but cannot be detected in this window.
echo Close this window and double-click this script again.
goto failure

:environment_error
echo The project Python environment is incomplete or incompatible.
echo Existing files have been preserved. Read docs\windows.md and ask for help.
goto failure

:dependency_error
echo Dependency installation or validation failed. Read the message above.
echo Do not disable TLS or hash verification. The page will not be started.
goto failure

:test_error
echo Tests failed. The page will not be started. Save the error text for diagnosis.
goto failure

:files_error
echo Project files are missing. Extract the entire ZIP before running this script.
goto failure

:folder_error
echo Cannot open the project folder.

:failure
echo Setup stopped. No real trading or money operation was performed.
pause
exit /b 1
