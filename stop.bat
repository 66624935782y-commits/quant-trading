@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing
.venv\Scripts\python.exe -X utf8 manage.py stop
if errorlevel 1 goto failed
pause
exit /b 0
:missing
echo 未发现安装的虚拟环境。
pause
exit /b 1
:failed
echo 停止失败，请查看上方提示。不要按端口强杀其他程序。
pause
exit /b 1
