"""币安认证诊断回归测试；只使用替身，绝不读取密钥或连接交易所。"""
from dataclasses import replace
from decimal import Decimal
import json
from types import SimpleNamespace

import ccxt
import pytest

from trader.config import Settings
from trader.diagnostics import run_checks
from trader.exchange import (
    Binance,
    RejectedOrder,
    UserError,
    friendly_exchange_error,
    safe_error_details,
)


@pytest.fixture
def settings(tmp_path):
    """构造完全虚假的本地配置，所有测试禁用真实交易。"""
    return Settings(
        'fake-key-never-use', 'fake-secret-never-use', 'fake-password-for-tests',
        False, Decimal('5'), Decimal('10'), 5, 8,
        tmp_path / 'trader.sqlite3', tmp_path / 'trader.log',
    )


def auth_error(code=-2015, request_ip='203.0.113.42'):
    """构造带有故意敏感内容的币安异常，检查诊断是否正确隐藏。"""
    return ccxt.AuthenticationError(
        'binanceusdm ' + json.dumps({
            'code': code,
            'msg': 'Invalid API-key, IP, or permissions for action, request ip: ' + request_ip,
            'apiKey': 'RAW_PRIVATE_KEY_DO_NOT_SHOW',
            'secret': 'RAW_PRIVATE_SECRET_DO_NOT_SHOW',
            'url': 'https://example.invalid/private?signature=RAW_SIGNATURE_DO_NOT_SHOW',
        })
    )


def assert_no_raw_details(value):
    """确保日志或页面可用的结果只含白名单摘要，不包含异常原文。"""
    rendered = json.dumps(value, ensure_ascii=False)
    for forbidden in (
        'RAW_PRIVATE_KEY_DO_NOT_SHOW', 'RAW_PRIVATE_SECRET_DO_NOT_SHOW',
        'RAW_SIGNATURE_DO_NOT_SHOW', 'https://example.invalid',
        'Invalid API-key, IP, or permissions for action',
    ):
        assert forbidden not in rendered


@pytest.mark.parametrize('code,term', [
    (-2015, 'IP'), (-1022, '签名'), (-1021, '时间'), (-2014, '格式'),
])
def test_known_codes_get_specific_safe_chinese_guidance(code, term):
    """相似认证失败须保留安全错误码，区分格式、签名、时间与权限问题。"""
    exc = auth_error(code)
    details = safe_error_details(exc)
    message = friendly_exchange_error(exc)
    assert details['code'] == code
    assert str(code) in message
    assert term in message
    if code == -2015:
        assert '权限' in message
    assert_no_raw_details(message)
    assert_no_raw_details(details)


@pytest.mark.parametrize('request_ip', ['203.0.113.42', '2001:db8::42'])
def test_exchange_reported_ipv4_and_ipv6_are_validated(request_ip):
    """只提取币安报告的合法 IP，保留 IPv4 或 IPv6 供用户核对白名单。"""
    details = safe_error_details(auth_error(request_ip=request_ip))
    assert details == {'code': -2015, 'request_ip': request_ip}
    assert_no_raw_details(details)


@pytest.mark.parametrize('request_ip', ['999.999.999.999', 'not-an-ip', '<script>alert(1)</script>'])
def test_invalid_request_ip_is_not_displayed(request_ip):
    """无效 IP 或响应中的任意文本不能被当作安全网络信息回显。"""
    exc = auth_error(request_ip=request_ip)
    assert safe_error_details(exc)['request_ip'] is None
    assert request_ip not in friendly_exchange_error(exc)
    assert_no_raw_details(friendly_exchange_error(exc))


def test_unstructured_exception_has_no_invented_code_or_ip():
    """普通异常中的数字和 URL 不应被误认为币安返回码或请求 IP。"""
    exc = ccxt.AuthenticationError('untrusted request 2015 https://example.invalid/203.0.113.42')
    assert safe_error_details(exc) == {'code': None, 'request_ip': None}
    assert 'example.invalid' not in friendly_exchange_error(exc)


class ReadOnlyExchange:
    """记录只读诊断行为；一旦出现下单、撤单或配置修改调用就让测试失败。"""

    def __init__(self, fail_at=None, error=None):
        """选择某一步骤失败，模拟真实账户诊断中的首次错误。"""
        self.fail_at = fail_at
        self.error = error
        self.events = []

    def record(self, name):
        """记录步骤，并按指定位置抛出诊断需要处理的异常。"""
        self.events.append(name)
        if name == self.fail_at:
            raise self.error

    def call(self, method, *args):
        """只允许时间同步和收益流水 GET 方法，拒绝任何交易请求。"""
        assert method in ('load_time_difference', 'fapiPrivateGetIncome')
        self.record(method)
        if method == 'load_time_difference':
            return 17
        assert args == ({'limit': 1},)
        return [{'income': 'SENSITIVE_ACCOUNT_INCOME', 'asset': 'USDT'}]

    def balance(self):
        """返回不能被诊断文件保存的虚假余额。"""
        self.record('balance')
        return {'total': 'SENSITIVE_ACCOUNT_BALANCE', 'free': 'SENSITIVE_AVAILABLE_BALANCE'}

    def positions(self):
        """返回不能被诊断文件保存的虚假持仓内容。"""
        self.record('positions')
        return [{'symbol': 'SENSITIVE_POSITION_CONTENT', 'contracts': 7}]


def test_successful_diagnostics_are_read_only_and_hide_account_contents(settings):
    """成功诊断必须逐项只读验证，不返回余额、持仓、收益或任何配置秘密。"""
    ex = ReadOnlyExchange()
    report = run_checks(settings, exchange=ex)
    assert report['ok'] is True
    assert ex.events == ['load_time_difference', 'balance', 'positions', 'fapiPrivateGetIncome']
    assert report['checks']
    assert all(check['status'] == 'passed' for check in report['checks'])
    rendered = json.dumps(report, ensure_ascii=False)
    for value in (
        settings.api_key, settings.api_secret, settings.password,
        'SENSITIVE_ACCOUNT_BALANCE', 'SENSITIVE_AVAILABLE_BALANCE',
        'SENSITIVE_POSITION_CONTENT', 'SENSITIVE_ACCOUNT_INCOME',
    ):
        assert value not in rendered


@pytest.mark.parametrize('field,value', [
    ('api_key', ''), ('api_secret', ''),
    ('api_key', ' fake-key-never-use'), ('api_key', 'fake-key-never-use '),
    ('api_secret', '\tfake-secret-never-use'), ('api_secret', 'fake-secret-never-use\n'),
])
def test_invalid_local_credentials_stop_before_any_exchange_request(settings, field, value):
    """缺失或意外空白的凭据须在本机提示，不向币安发送错误认证请求。"""
    ex = ReadOnlyExchange()
    report = run_checks(replace(settings, **{field: value}), exchange=ex)
    assert report['ok'] is False
    assert any(check['status'] == 'failed' for check in report['checks'])
    assert ex.events == []
    assert settings.api_key not in json.dumps(report)
    assert settings.api_secret not in json.dumps(report)


@pytest.mark.parametrize('stage', ['load_time_difference', 'balance', 'positions', 'fapiPrivateGetIncome'])
def test_diagnostics_stop_on_first_failure_without_retries_or_later_calls(settings, stage):
    """首次失败后停止，避免继续查询、重复认证或误发后续交易操作。"""
    ex = ReadOnlyExchange(fail_at=stage, error=auth_error())
    report = run_checks(settings, exchange=ex)
    expected = ['load_time_difference', 'balance', 'positions', 'fapiPrivateGetIncome']
    assert ex.events == expected[:expected.index(stage) + 1]
    assert report['ok'] is False
    failure = report['checks'][-1]
    assert failure['status'] == 'failed'
    assert failure['code'] == -2015
    assert failure['request_ip'] == '203.0.113.42'
    assert_no_raw_details(report)


def test_safe_adapter_error_metadata_survives_diagnostic_report(settings):
    """适配器已清洗的异常仍应携带数字错误码和合法请求 IP。"""
    ex = ReadOnlyExchange(
        fail_at='balance',
        error=UserError('币安认证失败（-2015），请检查密钥、IP 和合约权限。',
                        code=-2015, request_ip='203.0.113.42'),
    )
    report = run_checks(settings, exchange=ex)
    assert report['ok'] is False
    assert report['checks'][-1]['code'] == -2015
    assert report['checks'][-1]['request_ip'] == '203.0.113.42'
    assert ex.events == ['load_time_difference', 'balance']


def fail_with(exc):
    """创建始终抛出指定异常的 CCXT 方法替身。"""
    def failing(*args, **kwargs):
        """抛出原异常，供适配器验证异常类型与安全信息。"""
        raise exc
    return failing


def test_adapter_preserves_network_error_for_uncertain_order_reconciliation(settings):
    """时间戳或网络异常保留原类型，让下单流程继续核对而非盲目重发。"""
    adapter = Binance(settings)
    exc = ccxt.InvalidNonce('binanceusdm {"code":-1021,"msg":"Timestamp outside recvWindow"}')
    adapter.client = SimpleNamespace(create_order=fail_with(exc))
    with pytest.raises(ccxt.InvalidNonce) as caught:
        adapter.call('create_order')
    assert caught.value is exc


@pytest.mark.parametrize('method,expected_type', [('fetch_balance', UserError), ('create_order', RejectedOrder)])
def test_adapter_auth_failure_is_safe_and_keeps_order_rejection_semantics(settings, method, expected_type):
    """认证拒绝须清洗内容，并保留下单明确被拒绝的业务语义。"""
    adapter = Binance(settings)
    adapter.client = SimpleNamespace(**{method: fail_with(auth_error())})
    with pytest.raises(expected_type) as caught:
        adapter.call(method)
    assert caught.value.code == -2015
    assert caught.value.request_ip == '203.0.113.42'
    assert_no_raw_details(str(caught.value))
