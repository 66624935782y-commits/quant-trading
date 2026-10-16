"""Windows 一键初始化、启动、停止与脱敏诊断；本模块只依赖标准库。"""
import errno
import contextlib
import io
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / 'runtime'
PIDFILE = RUNTIME / 'server.json'
URL = 'http://127.0.0.1:8000'


def init():
    """初始化默认 SQLite 文件，不需要密钥或管理员密码。"""
    from trader.db import Database
    Database(RUNTIME / 'trader.sqlite3')
    print('数据库初始化完成。')


def process_state(record):
    """核对进程身份而非只看 PID 存在，防止旧编号被其他进程复用。"""
    try:
        pid = int(record['pid'])
        if pid <= 0 or record['script'] != str(ROOT / 'run_server.py'):
            return 'different'
        if os.name == 'nt':
            script = '''$ErrorActionPreference = 'Stop'
$r = [Console]::In.ReadToEnd() | ConvertFrom-Json
try {
  $p = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$r.pid)
  if (!$p) { exit 1 }
  if (!$p.ExecutablePath -or !$p.CommandLine) { exit 3 }
  if ($p.ExecutablePath -eq $r.python -and $p.CommandLine.Contains($r.script)) { exit 0 }
  exit 2
} catch { exit 3 }
'''
            result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                                    input=json.dumps(record), text=True, capture_output=True, timeout=10)
            return {0: 'match', 1: 'missing', 2: 'different'}.get(result.returncode, 'unknown')
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if not command or not any(command):
            return 'missing'
        executable = os.readlink(f'/proc/{pid}/exe')
        return 'match' if (record['script'].encode() in command and
                          os.path.realpath(record['python']) == executable) else 'different'
    except (KeyError, ValueError, TypeError):
        return 'different'
    except OSError as exc:
        return 'missing' if exc.errno == errno.ENOENT else 'unknown'
    except subprocess.SubprocessError:
        return 'unknown'


def service_ready(pid):
    """绕过系统代理直连 IPv4，并确认 HTTP 响应确实来自指定助手进程。"""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(URL + '/', timeout=1) as response:
            return (response.status == 200 and response.headers.get('X-AQ-Pid') == str(pid)
                    and b'AI Quant Trader Private' in response.read(128000))
    except (OSError, ValueError, urllib.error.URLError):
        return False


def port_open():
    """检测本机端口是否已被占用，不尝试停止未知程序。"""
    try:
        with socket.create_connection(('127.0.0.1', 8000), timeout=.5):
            return True
    except OSError:
        return False


def configuration_ok():
    """只输出受控的配置诊断，不显示密码、密钥或配置原文。"""
    if not (ROOT / '.env').is_file():
        print('缺少 .env：请先运行 install.bat 并设置管理员密码。')
        if (ROOT / '.env.txt').exists():
            print('发现 .env.txt，请在资源管理器显示扩展名，将文件名改为 .env。')
        return False
    try:
        from trader.config import load_settings
        load_settings()
        return True
    except ImportError:
        print('运行依赖缺失，请重新双击 install.bat；不会覆盖已有 .env。')
    except (ValueError, OSError):
        print('配置无效：请设置至少 16 位非默认管理员密码，检查正数风控金额及 LIVE_TRADING=true/false。')
        print('若开启实盘，还需完整填写 Key 和 Secret；不必为打开首页开启实盘。')
    return False


def diagnose():
    """生成可安全分享的本地诊断摘要，不连币安、不读取账户或打印 .env 内容。"""
    lines = ['AI Quant Trader Private 启动诊断', 'Python: ' + sys.version.split()[0]]
    for name in ('fastapi', 'uvicorn', 'ccxt', 'dotenv'):
        try:
            importlib.import_module(name)
            lines.append(name + ': 可导入')
        except Exception:
            lines.append(name + ': 导入失败，请重新运行 install.bat')
    lines.append('.env: ' + ('存在（内容不输出）' if (ROOT / '.env').is_file() else '缺失'))
    if (ROOT / '.env.txt').exists():
        lines.append('发现 .env.txt：请修正文件扩展名')
    lines.append('端口 8000: ' + ('有监听' if port_open() else '无服务监听，浏览器会拒绝连接'))
    if PIDFILE.exists():
        try:
            record = json.loads(PIDFILE.read_text(encoding='utf-8'))
            state = process_state(record)
            labels = {'match': '助手进程存在', 'missing': '进程已退出', 'different': '旧编号或非本项目进程', 'unknown': '无法核实进程身份'}
            lines.append('启动记录: ' + labels[state])
            lines.append('指定助手 HTTP 检查: ' + ('通过' if service_ready(record.get('pid')) else '未通过'))
        except (OSError, ValueError, TypeError):
            lines.append('启动记录: 无法读取')
    else:
        lines.append('启动记录: 不存在')
    path = RUNTIME / 'server-output.log'
    if path.exists():
        try:
            with path.open('rb') as log:
                log.seek(0, 2)
                log.seek(max(0, log.tell()-16000))
                text = log.read(16000).decode('utf-8', errors='replace')
            # 只匹配已知类别，不展示原始启动日志或异常参数。
            if 'ModuleNotFoundError' in text or 'ImportError' in text:
                lines.append('近期日志提示: 依赖导入失败，请重新运行 install.bat')
            if '10048' in text or 'address already in use' in text.lower():
                lines.append('近期日志提示: 端口已占用，请检查其他服务')
            if '配置无效' in text:
                lines.append('近期日志提示: 配置无效，请检查密码及交易开关')
            if '已有助手进程' in text:
                lines.append('近期日志提示: 另一助手进程持有运行锁')
            if 'PermissionError' in text or 'readonly database' in text:
                lines.append('近期日志提示: 文件或数据库不可写，请解压到自己的可写文件夹')
        except OSError:
            lines.append('启动日志: 不可读取')
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        valid_config = configuration_ok()
    lines.append('配置检查: ' + ('通过（值不输出）' if valid_config else '未通过'))
    lines.extend(captured.getvalue().splitlines())
    print('\n'.join(lines))
    try:
        RUNTIME.mkdir(exist_ok=True)
        (RUNTIME / 'diagnosis.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')
        print('已保存 runtime/diagnosis.txt。可以发送此摘要；不要发送 .env 或密钥。')
    except OSError:
        print('无法写入诊断摘要，请查看本窗口提示。')
    return 0


def start():
    """确认进程身份与 HTTP 就绪后才打开浏览器，所有失败返回非零状态。"""
    if not configuration_ok():
        return 1
    if PIDFILE.exists():
        try:
            old = json.loads(PIDFILE.read_text(encoding='utf-8'))
            state = process_state(old)
        except (ValueError, OSError, TypeError):
            print('启动记录异常，请双击 diagnose.bat 查看摘要。')
            return 1
        if state == 'match':
            if service_ready(old['pid']):
                print('已有助手服务且 HTTP 检查通过，正在打开浏览器。')
                webbrowser.open(URL)
                return 0
            print('助手进程存在，但网页未就绪；不会误报启动成功。请先运行 stop.bat，再重试。')
            diagnose()
            return 1
        if state == 'unknown':
            print('无法核实旧进程身份，为避免重复运行，请先运行 diagnose.bat。')
            return 1
        # 过期记录只移除记录，不停止被复用 PID 对应的其他程序。
        PIDFILE.unlink(missing_ok=True)
    if port_open():
        print('端口 8000 已被其他或未受管理的服务占用；没有停止任何程序。')
        print('请先关闭原服务窗口或使用原文件夹的 stop.bat，再重试。')
        return 1
    RUNTIME.mkdir(exist_ok=True)
    try:
        with open(RUNTIME / 'server-output.log', 'ab') as output:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if os.name == 'nt' else 0
            process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'run_server.py')], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=output, stderr=output, creationflags=flags,
                start_new_session=os.name != 'nt', env={**os.environ, 'PYTHONUTF8': '1'})
        PIDFILE.write_text(json.dumps({'pid': process.pid, 'script': str(ROOT / 'run_server.py'),
                                      'python': sys.executable}), encoding='utf-8')
    except OSError:
        print('无法启动或保存进程记录，请确认已完整解压到自己的可写文件夹。')
        return 1
    for _ in range(60):
        if process.poll() is not None:
            print('助手服务启动后退出。以下为脱敏诊断：')
            PIDFILE.unlink(missing_ok=True)
            diagnose()
            return 1
        if service_ready(process.pid):
            print('助手服务与 HTTP 检查通过。请在浏览器输入管理员密码。')
            webbrowser.open(URL)
            return 0
        time.sleep(.5)
    print('服务启动超时，尚未就绪。请查看以下摘要，勿反复启动。')
    diagnose()
    return 1


def stop():
    """仅停止身份匹配的助手进程，保留其他程序及无法确认身份的进程。"""
    if not PIDFILE.exists():
        print('没有受管理的助手进程。')
        return 0
    try:
        record = json.loads(PIDFILE.read_text(encoding='utf-8'))
        state = process_state(record)
    except (ValueError, OSError, TypeError):
        print('进程记录无效，不执行停止。')
        return 1
    if state in ('missing', 'different'):
        PIDFILE.unlink(missing_ok=True)
        print('旧助手进程已不存在或编号已失效；只清理记录，没有停止其他程序。')
        return 0
    if state != 'match':
        print('无法核实进程身份，未停止任何程序。请双击 diagnose.bat。')
        return 1
    pid = int(record['pid'])
    if os.name == 'nt':
        # 停止前再次核对身份，避免查询和执行之间编号被复用。
        script = '''$ErrorActionPreference = 'Stop'
$r = [Console]::In.ReadToEnd() | ConvertFrom-Json
try {
  $p = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$r.pid)
  if ($p -and $p.ExecutablePath -eq $r.python -and $p.CommandLine.Contains($r.script)) {
    Stop-Process -Id ([int]$r.pid) -ErrorAction Stop
  } else { exit 2 }
} catch { exit 3 }
'''
        try:
            result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                                    input=json.dumps(record), text=True, capture_output=True, timeout=10)
            if result.returncode:
                print('进程身份不匹配或停止失败，未强杀其他程序。')
                return 1
        except (OSError, subprocess.SubprocessError):
            print('无法执行停止命令，请检查 PowerShell 可用性。')
            return 1
    else:
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            print('停止失败，请检查进程状态。')
            return 1
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        if process_state(record) in ('missing', 'different'):
            PIDFILE.unlink(missing_ok=True)
            print('助手服务已停止。停止服务不会平仓或撤单，请在币安核对仓位。')
            return 0
        time.sleep(.1)
    print('已请求停止，但尚未确认进程退出；保留进程记录，请运行 diagnose.bat。')
    return 1

if __name__ == '__main__':
    commands = {'init': init, 'start': start, 'stop': stop, 'diagnose': diagnose}
    command = sys.argv[1] if len(sys.argv)>1 else 'diagnose'
    sys.exit(commands[command]() or 0 if command in commands else 1)
