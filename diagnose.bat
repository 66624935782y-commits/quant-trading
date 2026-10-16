@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo 正在检查本机启动状态，不读取或显示密钥，不连接币安...
if not exist ".venv\Scripts\python.exe" goto missing
.venv\Scripts\python.exe -X utf8 manage.py diagnose
pause
exit /b 0
:missing
echo 未找到虚拟环境。请完整解压安装包，然后先双击 install.bat。
echo 如果已安装过，请确认当前运行的是同一个文件夹中的脚本。
pause
exit /b 1
