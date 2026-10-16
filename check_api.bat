@echo off
chcp 65001 >nul
setlocal DisableDelayedExpansion
cd /d "%~dp0"
echo 请在即将弹出的窗口中选择原来安装交易助手的文件夹。
echo 该文件夹中应有 start.bat、.env 和 .venv；例如 E:\虚拟量化。
echo 本工具只读配置并查询币安，不下单，不显示密钥，不修改交易设置。
set "TRADER_PROJECT_DIR="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -STA -Command "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = '选择原来安装交易助手的文件夹（含 start.bat 和 .env）'; if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::WriteLine($d.SelectedPath) }"`) do set "TRADER_PROJECT_DIR=%%D"
if not defined TRADER_PROJECT_DIR goto cancelled
if not exist "%TRADER_PROJECT_DIR%\.venv\Scripts\python.exe" goto wrongfolder
if not exist "%TRADER_PROJECT_DIR%\.env" goto wrongfolder
"%TRADER_PROJECT_DIR%\.venv\Scripts\python.exe" -c "import ccxt, dotenv" >nul 2>nul
if errorlevel 1 goto dependencies
"%TRADER_PROJECT_DIR%\.venv\Scripts\python.exe" -X utf8 -u "%~dp0api_check.py" "%TRADER_PROJECT_DIR%\.env"
set "API_CHECK_EXIT=%errorlevel%"
echo.
echo 可以把本窗口的诊断结果发来，不要发送 .env 或密钥截图。
pause
exit /b %API_CHECK_EXIT%
:cancelled
echo 没有选择文件夹，未运行任何币安请求。
pause
exit /b 1
:wrongfolder
echo 这个文件夹里没找到 .venv 或 .env。请重新双击本脚本，选择之前安装交易助手的文件夹。
pause
exit /b 1
:dependencies
echo 原程序依赖缺失。请从原文件夹运行 install.bat 后再试，不会覆盖已有 .env。
pause
exit /b 1
