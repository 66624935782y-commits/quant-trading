@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto failure
if not exist ".venv\Scripts\python.exe" (
    echo Run setup_windows.cmd first.
    goto failure
)
".venv\Scripts\python.exe" -c "import sys; assert (3, 12) <= sys.version_info[:2] < (3, 14); from quant_trading.config import SafetyPolicy; SafetyPolicy.from_environment().validate(); print('PAPER safety check OK')"
if errorlevel 1 goto failure
echo Starting your local PAPER page. Keep this window open.
echo If the browser does not open, use the local address printed below.
echo Press Ctrl+C to stop. No real exchange orders are supported.
".venv\Scripts\python.exe" -m streamlit run app.py --browser.gatherUsageStats=false --server.address=127.0.0.1 --server.headless=false
set "QT_SERVER_EXIT=%errorlevel%"
echo The page has stopped. If an error was printed, keep that message for diagnosis.
pause
exit /b %QT_SERVER_EXIT%

:failure
echo Startup stopped. Run setup_windows.cmd or read docs\windows.md for help.
pause
exit /b 1
