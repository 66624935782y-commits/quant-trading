@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo 正在检查 Python 3.10 或更新版本...
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 -c "import sys; raise SystemExit(sys.version_info < (3,10))" >nul 2>nul
if errorlevel 1 goto usepython
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
goto install
:usepython
python -c "import sys; raise SystemExit(sys.version_info < (3,10))" >nul 2>nul
if errorlevel 1 goto missing
if not exist ".venv\Scripts\python.exe" python -m venv .venv
:install
if not exist ".venv\Scripts\python.exe" goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
if not exist ".env" copy /y .env.example .env >nul
.venv\Scripts\python.exe manage.py init
if errorlevel 1 goto failed
echo 安装完成。请在记事本中配置 .env 并保存，然后双击 start.bat。
notepad .env
pause
exit /b 0
:missing
echo 未找到 Python 3.10+，尝试通过 Windows WinGet 安装官方 Python 3.12...
where winget >nul 2>nul
if errorlevel 1 goto manualpython
winget install --id Python.Python.3.12 --exact --source winget --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
if errorlevel 1 goto manualpython
if not exist "%LocalAppData%\Programs\Python\Python312\python.exe" goto manualpython
"%LocalAppData%\Programs\Python\Python312\python.exe" -m venv .venv
if errorlevel 1 goto failed
goto install
:manualpython
echo 无法自动安装。请从 https://www.python.org/downloads/windows/ 安装 Python 3.12 64 位，勾选 Add Python to PATH，然后重新双击 install.bat。
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1
:failed
echo 安装失败，请保留本窗口错误内容，检查网络与 Python 版本。不要发送 .env 或密钥截图。
pause
exit /b 1
