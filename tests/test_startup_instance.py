"""回归 Windows 虚拟环境包装器与实际服务 PID 不同造成的误报。"""
import json
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import manage
from trader.api import create_app
from trader.config import Settings


INSTANCE = 'isolated-launch-instance-6c37d96257204303a82b'


def http_response(headers, status=200, body=b'AI Quant Trader Private'):
    """创建完整响应上下文替身，不连接本机服务或交易所。"""
    response = Mock(status=status, headers=headers)
    response.read.return_value = body
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


def install_response(monkeypatch, response):
    """将 HTTP 探测定向到受控响应，保留实际就绪判定逻辑。"""
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(manage.urllib.request, 'build_opener', lambda *_: opener)
    return opener


def test_wrapped_python_accepts_server_pid_with_matching_instance(monkeypatch):
    """包装器 PID 不同于实际服务 PID 时，同次启动仍应正常打开网页。"""
    install_response(monkeypatch, http_response({'X-AQ-Pid': '2002', 'X-AQ-Instance': INSTANCE}))
    assert manage.service_ready(1001, INSTANCE)


@pytest.mark.parametrize('returned_instance', [None, '', 'another-launch-instance'])
def test_missing_or_other_instance_never_counts_as_ready(monkeypatch, returned_instance):
    """即使响应 PID 恰好相同，缺失或错误启动标识也不能冒充本次服务。"""
    headers = {'X-AQ-Pid': '1001'}
    if returned_instance is not None:
        headers['X-AQ-Instance'] = returned_instance
    install_response(monkeypatch, http_response(headers))
    assert not manage.service_ready(1001, INSTANCE)


@pytest.mark.parametrize('returned_pid', [None, '', '0', '-1', 'not-a-pid'])
def test_instance_check_still_requires_valid_server_pid(monkeypatch, returned_pid):
    """同一启动标识不能让缺失或无效的实际服务 PID 通过检查。"""
    headers = {'X-AQ-Instance': INSTANCE}
    if returned_pid is not None:
        headers['X-AQ-Pid'] = returned_pid
    install_response(monkeypatch, http_response(headers))
    assert not manage.service_ready(1001, INSTANCE)


@pytest.mark.parametrize('returned_pid, expected', [('1001', True), ('2002', False)])
def test_legacy_record_keeps_exact_pid_check(monkeypatch, returned_pid, expected):
    """旧启动记录没有实例标识时，仍须严格核对原来的 PID。"""
    install_response(monkeypatch, http_response({'X-AQ-Pid': returned_pid, 'X-AQ-Instance': INSTANCE}))
    assert manage.service_ready(1001) is expected


@pytest.mark.parametrize('status, body', [(503, b'AI Quant Trader Private'), (200, b'Other service')])
def test_matching_instance_does_not_bypass_application_readiness(monkeypatch, status, body):
    """实例匹配仍必须验证成功状态和真实助手首页，不能跳过应用就绪检查。"""
    install_response(monkeypatch, http_response({'X-AQ-Pid': '2002', 'X-AQ-Instance': INSTANCE}, status, body))
    assert not manage.service_ready(1001, INSTANCE)


@pytest.fixture
def app_settings(tmp_path):
    """只在临时目录创建演练配置，不读取用户密钥或开发者的配置文件。"""
    return Settings('', '', 'isolated-password-for-testing', False, Decimal('5'), Decimal('10'),
                    5, 8, tmp_path / 'db.sqlite3', tmp_path / 'trader.log')


def test_homepage_reports_managed_instance_without_exchange_calls(app_settings):
    """真实首页响应带回启动标识和实际 PID，访问首页不触碰交易所。"""
    exchange = Mock()
    with TestClient(create_app(app_settings, exchange=exchange, instance_id=INSTANCE)) as client:
        response = client.get('/')
    assert response.status_code == 200
    assert response.headers['X-AQ-Instance'] == INSTANCE
    assert response.headers['X-AQ-Pid'] == str(os.getpid())
    assert 'AI Quant Trader Private' in response.text
    assert exchange.mock_calls == []


def test_unmanaged_app_instance_is_stable_and_unique(app_settings):
    """直接运行的服务也应有稳定且独立的标识，避免不同启动相互认错。"""
    with TestClient(create_app(app_settings, exchange=Mock())) as first:
        instance_one = first.get('/').headers['X-AQ-Instance']
        assert instance_one
        assert first.get('/').headers['X-AQ-Instance'] == instance_one
    with TestClient(create_app(app_settings, exchange=Mock())) as second:
        assert second.get('/').headers['X-AQ-Instance'] != instance_one


def test_new_launch_shares_instance_and_opens_child_server(tmp_path, monkeypatch):
    """完整启动流程共享实例标识，即使包装器和 HTTP 服务 PID 不同也能成功。"""
    runtime = tmp_path / 'runtime'
    monkeypatch.setattr(manage, 'ROOT', tmp_path)
    monkeypatch.setattr(manage, 'RUNTIME', runtime)
    monkeypatch.setattr(manage, 'PIDFILE', runtime / 'server.json')
    monkeypatch.setattr(manage, 'configuration_ok', lambda: True)
    monkeypatch.setattr(manage, 'port_open', lambda: False)
    browser = Mock()
    monkeypatch.setattr(manage.webbrowser, 'open', browser)
    launch = Mock(return_value=SimpleNamespace(pid=1001, poll=lambda: None))
    monkeypatch.setattr(manage.subprocess, 'Popen', launch)
    opener = Mock()

    def serve_homepage(*args, **kwargs):
        """模拟子进程从启动环境读取标识后返回首页，保留真实 HTTP 校验分支。"""
        instance = launch.call_args.kwargs['env']['AQ_STARTUP_INSTANCE']
        return http_response({'X-AQ-Pid': '2002', 'X-AQ-Instance': instance})

    opener.open.side_effect = serve_homepage
    monkeypatch.setattr(manage.urllib.request, 'build_opener', lambda *_: opener)
    assert manage.start() == 0
    saved = json.loads((runtime / 'server.json').read_text(encoding='utf-8'))
    assert saved['pid'] == 1001
    assert saved['instance_id'] == launch.call_args.kwargs['env']['AQ_STARTUP_INSTANCE']
    assert saved['instance_id']
    browser.assert_called_once_with('http://127.0.0.1:8000')


def test_existing_launch_checks_instance_before_reopening_browser(tmp_path, monkeypatch):
    """再次启动复用已核实的服务时，必须传回已保存的实例标识。"""
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    path = runtime / 'server.json'
    path.write_text(json.dumps({'pid': 1001, 'instance_id': INSTANCE}), encoding='utf-8')
    monkeypatch.setattr(manage, 'ROOT', tmp_path)
    monkeypatch.setattr(manage, 'RUNTIME', runtime)
    monkeypatch.setattr(manage, 'PIDFILE', path)
    monkeypatch.setattr(manage, 'configuration_ok', lambda: True)
    monkeypatch.setattr(manage, 'process_state', lambda _: 'match')
    browser = Mock()
    monkeypatch.setattr(manage.webbrowser, 'open', browser)
    install_response(monkeypatch, http_response({'X-AQ-Pid': '2002', 'X-AQ-Instance': INSTANCE}))
    assert manage.start() == 0
    browser.assert_called_once_with('http://127.0.0.1:8000')
