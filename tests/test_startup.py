"""启动器故障回归：不启动真实交易、不读取开发者配置、不停止真实进程。"""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.error
import pytest
import manage

@pytest.fixture
def launcher(tmp_path, monkeypatch):
    """将所有进程记录和诊断文件放进隔离临时目录。"""
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    monkeypatch.setattr(manage, 'ROOT', tmp_path)
    monkeypatch.setattr(manage, 'RUNTIME', runtime)
    monkeypatch.setattr(manage, 'PIDFILE', runtime/'server.json')
    monkeypatch.setattr(manage, 'configuration_ok', lambda: True)
    monkeypatch.setattr(manage.webbrowser, 'open', Mock())
    return runtime


def record(runtime, pid=123):
    """生成仅包含进程元数据的管理记录。"""
    data = {'pid': pid, 'script': str(runtime.parent/'run_server.py'), 'python': 'python'}
    (runtime/'server.json').write_text(json.dumps(data))
    return data


def test_live_pid_without_http_must_not_open_browser(launcher, monkeypatch):
    """进程存在但未监听，必须报失败而非打开拒连页面。"""
    record(launcher)
    monkeypatch.setattr(manage, 'process_state', lambda _: 'match')
    monkeypatch.setattr(manage, 'service_ready', lambda *_: False)
    monkeypatch.setattr(manage, 'diagnose', Mock())
    assert manage.start() == 1
    manage.webbrowser.open.assert_not_called()


def test_reuses_only_confirmed_healthy_process(launcher, monkeypatch):
    """只有身份和 HTTP 都通过，才打开同一 IPv4 地址。"""
    record(launcher)
    monkeypatch.setattr(manage, 'process_state', lambda _: 'match')
    monkeypatch.setattr(manage, 'service_ready', lambda *_: True)
    assert manage.start() == 0
    manage.webbrowser.open.assert_called_once_with('http://127.0.0.1:8000')


def test_reused_pid_is_not_killed_or_reported_ready(launcher, monkeypatch):
    """旧 PID 属于其他程序时只清理记录，端口占用则退出。"""
    record(launcher)
    monkeypatch.setattr(manage, 'process_state', lambda _: 'different')
    monkeypatch.setattr(manage, 'port_open', lambda: True)
    kill = Mock()
    monkeypatch.setattr(manage.os, 'kill', kill)
    assert manage.start() == 1
    assert not manage.PIDFILE.exists()
    kill.assert_not_called()
    manage.webbrowser.open.assert_not_called()


def test_unknown_identity_never_launches_duplicate(launcher, monkeypatch):
    """无法读取身份不能当作进程消失，避免重复运行。"""
    record(launcher)
    monkeypatch.setattr(manage, 'process_state', lambda _: 'unknown')
    popen = Mock()
    monkeypatch.setattr(manage.subprocess, 'Popen', popen)
    assert manage.start() == 1
    popen.assert_not_called()


def test_new_server_exits_cleans_record_without_opening_browser(launcher, monkeypatch):
    """新进程导入失败或崩溃时清理自己的记录并显示诊断。"""
    monkeypatch.setattr(manage, 'port_open', lambda: False)
    monkeypatch.setattr(manage.subprocess, 'Popen', Mock(return_value=SimpleNamespace(pid=12,poll=lambda:1)))
    diagnosis = Mock()
    monkeypatch.setattr(manage, 'diagnose', diagnosis)
    assert manage.start() == 1
    assert not manage.PIDFILE.exists()
    diagnosis.assert_called_once()
    manage.webbrowser.open.assert_not_called()


def test_ready_check_bypasses_proxy_and_requires_expected_pid(monkeypatch):
    """不能把别的服务首页当成功，也不能将 localhost 请求发往系统代理。"""
    response = Mock(status=200, headers={'X-AQ-Pid': '50'})
    response.read.return_value = b'AI Quant Trader Private'
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    build = Mock(return_value=opener)
    monkeypatch.setattr(manage.urllib.request, 'build_opener', build)
    assert manage.service_ready(50)
    assert build.call_args.args[0].proxies == {}
    assert opener.open.call_args.args[0] == 'http://127.0.0.1:8000/'
    assert not manage.service_ready(51)
    response.headers = {}
    assert not manage.service_ready(50)


def test_refused_connection_is_not_ready(monkeypatch):
    """连接拒绝不会误报启动成功。"""
    opener = Mock()
    opener.open.side_effect = urllib.error.URLError('connection refused')
    monkeypatch.setattr(manage.urllib.request, 'build_opener', lambda *_:opener)
    assert not manage.service_ready(50)


def test_diagnostic_never_leaks_env_or_log_text(launcher, monkeypatch, capsys):
    """诊断只能输出状态分类，不能回显配置或日志中的敏感内容。"""
    (launcher.parent/'.env').write_text('BINANCE_API_SECRET=SECRET_MUST_NOT_APPEAR\n')
    (launcher/'server-output.log').write_text('SECRET_MUST_NOT_APPEAR\nModuleNotFoundError: broken\n')
    monkeypatch.setattr(manage, 'port_open', lambda:False)
    assert manage.diagnose() == 0
    report = (launcher/'diagnosis.txt').read_text(encoding='utf-8')
    output = capsys.readouterr().out
    assert 'SECRET_MUST_NOT_APPEAR' not in output+report
    assert '无服务监听' in report and '依赖导入失败' in report


def test_stop_reused_pid_only_removes_stale_record(launcher, monkeypatch):
    """停止按钮不应终止编号被复用的其他进程。"""
    record(launcher)
    monkeypatch.setattr(manage, 'process_state', lambda _: 'different')
    kill = Mock()
    monkeypatch.setattr(manage.os, 'kill', kill)
    assert manage.stop() == 0 and not manage.PIDFILE.exists()
    kill.assert_not_called()


def test_malformed_record_does_not_open_browser(launcher):
    """损坏的进程记录必须提示诊断，不抛出原始堆栈或开网页。"""
    manage.PIDFILE.write_text('not-json')
    assert manage.start() == 1
    manage.webbrowser.open.assert_not_called()
