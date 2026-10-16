@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing
if not exist ".env" goto missing
.venv\Scripts\python.exe -X utf8 manage.py start
if errorlevel 1 goto failed
echo 浏览器关闭后服务仍在运行。需要停止时双击 stop.bat。
pause
exit /b 0
:missing
echo 请先双击 install.bat 安装并配置 .env。
pause
exit /b 1
:failed
echo 启动未成功。请双击 diagnose.bat，发送诊断摘要或本窗口最后几行，不要发送密钥。
pause
exit /b 1
