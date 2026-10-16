@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo 这是只读认证检查，不会发送任何交易请求。
if not exist ".venv\Scripts\python.exe" goto missing
.venv\Scripts\python.exe -c "import ccxt, dotenv" >nul 2>nul
if errorlevel 1 goto missing
.venv\Scripts\python.exe -X utf8 -u -m trader.diagnostics
set "API_CHECK_EXIT=%errorlevel%"
pause
exit /b %API_CHECK_EXIT%
:missing
echo 请先在当前文件夹运行 install.bat 安装依赖，再双击本脚本。
pause
exit /b 1
