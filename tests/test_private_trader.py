"""隔离测试：覆盖订单、风控、认证和重启恢复，绝不访问真实账户。"""
from dataclasses import replace
from decimal import Decimal, ROUND_DOWN
from unittest.mock import Mock
import uuid
import ccxt
import pytest
from fastapi.testclient import TestClient
from trader.api import create_app
from trader.config import Settings, load_settings
from trader.db import Database
from trader.exchange import SYMBOLS, UserError
from trader.service import Trader

S = SYMBOLS['ETHUSDT']

@pytest.fixture
def ctx(tmp_path):
    """创建隔离账户、交易所替身和数据库。"""
    settings = Settings('test-key', 'test-secret', 'long-password-for-testing', False,
                        Decimal('5'), Decimal('10'), 5, 8, tmp_path/'db.sqlite3', tmp_path/'log.txt')
    ex = Mock()
    ex.client = ex
    ex.market.return_value = {'contractSize': 1, 'limits': {'amount': {'min': .001, 'max': 1000}, 'cost': {'min': 5}},
        'info': {'filters': [{'filterType': 'LOT_SIZE', 'minQty': '.001', 'maxQty': '1000', 'stepSize': '.001'}]}}
    ex.amount_to_precision.side_effect = lambda s, v: str(Decimal(v).quantize(Decimal('.001'), rounding=ROUND_DOWN))
    ex.price_to_precision.side_effect = lambda s, v: str(Decimal(v).quantize(Decimal('.01'), rounding=ROUND_DOWN))
    ex.balance.return_value = {'total': '100', 'free': '100'}
    ex.daily_pnl.return_value = {'net': '0', 'realized': '0'}
    ex.positions.return_value = []
    ex.open_orders.return_value = []
    ex.mode.return_value = False
    ex.quotes.return_value = [{'symbol': 'ETHUSDT', 'price': 1000}]
    def call(method, *args):
        """替身默认只产生可核对订单响应。"""
        if method == 'fetch_ticker':
            return {'last': 1000}
        if method in ('create_order', 'fetch_order'):
            return {'id': '42', 'symbol': S, 'status': 'closed', 'filled': .01, 'average': 1000}
        if method in ('set_margin_mode', 'set_leverage'):
            return {}
        if method in ('fetch_my_trades', 'fetch_orders'):
            return []
        raise AssertionError(method)
    ex.call.side_effect = call
    db = Database(settings.database)
    return settings, ex, db, Trader(settings, db, ex)


def order(**changes):
    """创建可通过校验的开仓表单。"""
    return {'request_id': str(uuid.uuid4()), 'symbol': 'ETHUSDT', 'direction': 'long', 'leverage': 5,
            'margin_mode': 'isolated', 'notional': '10', 'kind': 'market', 'price': None, **changes}


def pos(**changes):
    """创建按合约数量计量的逐仓多头。"""
    return {'symbol': S, 'side': 'long', 'contracts': .02, 'markPrice': 1000, 'entryPrice': 900,
            'unrealizedPnl': 2, 'leverage': 5, 'marginMode': 'isolated', **changes}


def live(ctx):
    """只给替身开启实盘代码分支。"""
    return Trader(replace(ctx[0], live=True), ctx[2], ctx[1])


def sent(ctx, name):
    """提取指定交易所调用记录。"""
    return [c.args for c in ctx[1].call.call_args_list if c.args[0] == name]


def test_dry_run_has_no_mutation(ctx):
    """默认演练模式不会发送订单或更改币安配置。"""
    result = ctx[3].open(order())
    assert result['state'] == 'dry_run'
    assert not sent(ctx, 'create_order') and not sent(ctx, 'set_leverage') and not sent(ctx, 'set_margin_mode')
    assert ctx[2].history()['orders'][0]['status'] == 'dry_run'


def test_live_idempotency_and_explicit_isolated(ctx):
    """同编号重复提交不重复发单，逐仓和杠杆来自明确选择。"""
    trader, request = live(ctx), order()
    assert trader.open(request)['state'] == 'confirmed'
    assert trader.open(request)['order_id'] == '42'
    assert len(sent(ctx, 'create_order')) == 1
    assert sent(ctx, 'set_margin_mode')[0] == ('set_margin_mode', 'isolated', S)
    assert sent(ctx, 'set_leverage')[0] == ('set_leverage', 5, S)
    with pytest.raises(UserError, match='其他操作'):
        trader.open({**request, 'direction': 'short'})

@pytest.mark.parametrize('amount', ['0', '-1', '10.01', 'NaN', 'Infinity', 'invalid'])
def test_invalid_amount_cannot_send(ctx, amount):
    """金额非法、非有限或超限时不能发单。"""
    with pytest.raises(UserError):
        live(ctx).open(order(notional=amount))
    assert not sent(ctx, 'create_order')

@pytest.mark.parametrize('net', ['-5', '-6'])
def test_daily_loss_stops_open(ctx, net):
    """亏损触及或超过后端上限时禁止开仓。"""
    ctx[1].daily_pnl.return_value = {'net': net}
    with pytest.raises(UserError, match='净亏损'):
        live(ctx).open(order())


def test_minimum_and_available_margin(ctx):
    """最小名义价值与可用保证金分别校验。"""
    ctx[1].balance.return_value = {'free': '.1'}
    with pytest.raises(UserError, match='保证金不足'):
        live(ctx).open(order())
    ctx[1].balance.return_value = {'free': '100'}
    ctx[1].market.return_value['limits']['cost']['min'] = 100
    with pytest.raises(UserError, match='最小名义价值'):
        live(ctx).open(order())


def test_market_lot_filter(ctx):
    """市价也必须通过独立的 MARKET_LOT_SIZE 数量规则。"""
    ctx[1].market.return_value['info']['filters'].append({'filterType': 'MARKET_LOT_SIZE', 'minQty': '.1', 'maxQty': '1', 'stepSize': '.1'})
    with pytest.raises(UserError, match='数量步长'):
        live(ctx).open(order())


def test_timeout_reconciles_without_retry(ctx):
    """网络超时后按原编号查询，不发第二笔订单。"""
    original = ctx[1].call.side_effect
    def timeout(method, *args):
        """仅订单提交模拟超时。"""
        if method == 'create_order':
            raise ccxt.RequestTimeout('SECRET-CONTENT')
        return original(method, *args)
    ctx[1].call.side_effect = timeout
    trader, request = live(ctx), order()
    assert trader.open(request)['order_id'] == '42'
    assert trader.open(request)['order_id'] == '42'
    assert len(sent(ctx, 'create_order')) == 1
    assert sent(ctx, 'fetch_order')[0][3]['origClientOrderId'].startswith('aq')


def test_unresolved_order_blocks_after_restart(ctx):
    """结果未知时将锁保存在 SQLite，重启不能重复下单。"""
    original = ctx[1].call.side_effect
    def timeout(method, *args):
        """同时模拟发单超时及查单未找到。"""
        if method == 'create_order':
            raise ccxt.RequestTimeout('hidden')
        if method == 'fetch_order':
            raise ccxt.OrderNotFound('hidden')
        return original(method, *args)
    ctx[1].call.side_effect = timeout
    with pytest.raises(UserError, match='尚未确认'):
        live(ctx).open(order())
    restarted = Trader(replace(ctx[0], live=True), Database(ctx[2].path), ctx[1])
    with pytest.raises(UserError, match='未确认'):
        restarted.open(order())
    assert len(sent(ctx, 'create_order')) == 1

@pytest.mark.parametrize('hedged', [False, True])
def test_close_remains_allowed_after_loss_and_halt(ctx, hedged):
    """禁开仓期间仍可减仓，单双向持仓使用正确保护参数。"""
    ex = ctx[1]
    ex.positions.return_value = [pos()]
    ex.mode.return_value = hedged
    ex.daily_pnl.return_value = {'net': '-10'}
    ctx[2].halt(True)
    live(ctx).close({'request_id': str(uuid.uuid4()), 'symbol': S, 'side': 'long', 'percent': '50'})
    args = sent(ctx, 'create_order')[0]
    assert args[3:5] == ('sell', .01)
    assert args[6].get('positionSide') == 'LONG' if hedged else args[6]['reduceOnly'] is True
    assert not sent(ctx, 'set_margin_mode')

@pytest.mark.parametrize('changes,fragment', [({'marginMode': 'cross'}, '非逐仓'), ({'side': 'short'}, '反向仓位'), ({'leverage': 20}, '杠杆')])
def test_existing_position_not_silently_changed(ctx, changes, fragment):
    """已有全仓、反向或不同杠杆仓位均不能被开仓按钮暗中修改。"""
    ctx[1].positions.return_value = [pos(**changes)]
    with pytest.raises(UserError, match=fragment):
        live(ctx).open(order())
    assert not sent(ctx, 'create_order')


def test_existing_orders_block_open(ctx):
    """已有同合约挂单必须先核对，防止参数变化影响既有订单。"""
    ctx[1].open_orders.return_value = [{'symbol': S}]
    with pytest.raises(UserError, match='未成交挂单'):
        live(ctx).open(order())


def test_dry_emergency_persists_lock(ctx):
    """演练紧急停止只锁定开仓，不改变真实账户。"""
    ctx[1].positions.return_value = [pos()]
    ctx[1].open_orders.return_value = [{'symbol': S}]
    result = ctx[3].emergency()
    assert result['halted'] and not result['complete']
    assert Database(ctx[2].path).halted()
    assert not sent(ctx, 'create_order') and not sent(ctx, 'cancel_all_orders')
    with pytest.raises(UserError, match='紧急停止'):
        ctx[3].open(order())


def test_live_emergency_cancels_both_and_closes(ctx):
    """紧急停止撤销普通及条件挂单并验证最终持仓。"""
    ex = ctx[1]
    ex.positions.return_value = [pos()]
    ex.open_orders.return_value = [{'symbol': S, 'category': 'ordinary'}, {'symbol': S, 'category': 'conditional'}]
    original = ex.call.side_effect
    def mutate(method, *args):
        """模拟交易所撤单与平仓后的状态。"""
        if method == 'cancel_all_orders':
            category = 'conditional' if args[1].get('trigger') else 'ordinary'
            ex.open_orders.return_value = [o for o in ex.open_orders.return_value if o['category'] != category]
            return []
        if method == 'create_order':
            ex.positions.return_value = []
        return original(method, *args)
    ex.call.side_effect = mutate
    result = live(ctx).emergency()
    assert result['complete'] and result['halted']
    assert len(sent(ctx, 'cancel_all_orders')) == 2


def test_partial_emergency_is_not_success(ctx):
    """撤单失败时不能声称紧急操作全部成功。"""
    ctx[1].open_orders.return_value = [{'symbol': S}]
    original = ctx[1].call.side_effect
    def failure(method, *args):
        """仅撤单模拟超时。"""
        if method == 'cancel_all_orders':
            raise ccxt.RequestTimeout('hidden')
        return original(method, *args)
    ctx[1].call.side_effect = failure
    result = live(ctx).emergency()
    assert result['halted'] and not result['complete']


def test_http_auth_csrf_cookie_logout(ctx):
    """验证认证、跨站保护、会话 Cookie、格式校验及注销。"""
    with TestClient(create_app(ctx[0], ctx[1], ctx[2])) as client:
        assert client.get('/api/account').status_code == 401
        assert client.post('/api/login', json={'password': ctx[0].password}).status_code == 403
        h = {'X-Trader-Request': '1', 'Origin': 'http://evil.example'}
        assert client.post('/api/login', headers=h, json={'password': ctx[0].password}).status_code == 403
        h['Origin'] = 'http://localhost:8000'
        r = client.post('/api/login', headers=h, json={'password': ctx[0].password})
        assert r.status_code == 200 and 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
        assert client.get('/api/account').status_code == 200
        assert client.get('/api/quotes').json()[0]['symbol'] == 'ETHUSDT'
        assert client.post('/api/order', headers=h, json=order()).json()['state'] == 'dry_run'
        assert client.post('/api/order', headers=h, json=order(leverage=21)).status_code == 422
        assert client.post('/api/close', headers=h, json={'request_id': str(uuid.uuid4()), 'symbol': S, 'side': 'long', 'percent': '-1'}).status_code == 400
        assert client.post('/api/logout', headers=h, json={}).status_code == 200
        assert client.get('/api/history').status_code == 401


def test_http_login_rate_limit(ctx):
    """限制错误密码尝试频率。"""
    with TestClient(create_app(ctx[0], ctx[1], ctx[2])) as client:
        for _ in range(5):
            assert client.post('/api/login', headers={'X-Trader-Request': '1'}, json={'password': 'bad'}).status_code == 401
        assert client.post('/api/login', headers={'X-Trader-Request': '1'}, json={'password': 'bad'}).status_code == 429


def test_http_errors_hide_exchange_secrets(ctx):
    """不向网页返回包含签名或密钥的异常文本。"""
    ctx[1].quotes.side_effect = ccxt.RequestTimeout('SECRET-CONTENT')
    with TestClient(create_app(ctx[0], ctx[1], ctx[2])) as client:
        client.post('/api/login', headers={'X-Trader-Request': '1'}, json={'password': ctx[0].password})
        response = client.get('/api/quotes')
        assert response.status_code == 502 and 'SECRET' not in response.text


def test_config_rejects_insecure_defaults(tmp_path):
    """默认密码、含糊开关、无密钥实盘和无穷风控金额不能启动。"""
    path = tmp_path/'.env'
    for text in ['ADMIN_PASSWORD=CHANGE_ME_USE_A_LONG_RANDOM_PASSWORD',
                 'ADMIN_PASSWORD=0123456789abcdef\nLIVE_TRADING=maybe',
                 'ADMIN_PASSWORD=0123456789abcdef\nMAX_DAILY_LOSS_USDT=NaN',
                 'ADMIN_PASSWORD=0123456789abcdef\nLIVE_TRADING=true']:
        path.write_text(text, encoding='utf-8')
        with pytest.raises(ValueError):
            load_settings(path)


def test_fills_deduplicate_and_persist(ctx):
    """重复同步同一成交不重复记账。"""
    fill = {'id': '1', 'symbol': S, 'timestamp': 123, 'fee': '.01', 'realized_pnl': '-.1'}
    ctx[2].save_fills([fill, fill])
    assert Database(ctx[2].path).history()['fills'] == [fill]


def test_price_precision_and_filter_rejected(ctx):
    """价格精度或范围不合规时拒绝限价委托。"""
    with pytest.raises(UserError, match='价格步长'):
        live(ctx).open(order(kind='limit', price='1000.001'))
    ctx[1].market.return_value['info']['filters'].append({'filterType': 'PRICE_FILTER', 'minPrice': '10', 'maxPrice': '900', 'tickSize': '.01'})
    with pytest.raises(UserError, match='价格范围'):
        live(ctx).open(order(kind='limit', price='1000'))


def test_raw_min_notional_filter_enforced(ctx):
    """即使统一规则缺少最小金额，也检查币安原始规则。"""
    ctx[1].market.return_value['limits']['cost'] = {}
    ctx[1].market.return_value['info']['filters'].append({'filterType': 'MIN_NOTIONAL', 'notional': '100'})
    with pytest.raises(UserError, match='最小名义价值'):
        live(ctx).open(order())


def test_fill_capture_is_automatic_and_no_duplicate(ctx):
    """确认订单后自动存实际成交，并按成交编号去重。"""
    original = ctx[1].call.side_effect
    import time
    def filled(method, *args):
        """返回币安风格的费用和已实现盈亏。"""
        if method == 'fetch_my_trades':
            return [{'id': '1', 'order': '42', 'timestamp': int(time.time()*1000), 'side': 'buy', 'amount': .01,
                     'price': 1000, 'fee': {'cost': .005, 'currency': 'USDT'}, 'info': {'realizedPnl': '0', 'positionSide': 'BOTH'}}]
        return original(method, *args)
    ctx[1].call.side_effect = filled
    trader, request = live(ctx), order()
    trader.open(request)
    trader.open(request)
    history = ctx[2].history()
    assert len(history['fills']) == 1 and history['fills'][0]['fee'] == .005
    assert history['exchange_orders'][0]['order_id'] == '42'


def test_sync_captures_orders_and_fills_and_watermark(ctx):
    """历史订单分页同步落盘，成功后才前进水位。"""
    import time
    timestamp = int(time.time()*1000)-1000
    original = ctx[1].call.side_effect
    def history(method, *args):
        """每个测试时间窗口返回可过滤的历史订单。"""
        if method == 'fetch_orders':
            return [{'id': '99', 'timestamp': timestamp, 'symbol': args[0], 'side': 'buy', 'amount': .01,
                     'filled': 0, 'remaining': .01, 'status': 'canceled'}]
        return original(method, *args)
    ctx[1].call.side_effect = history
    result = ctx[3].sync()
    assert result['orders'] == 3
    assert len(ctx[2].history()['exchange_orders']) == 3
    assert ctx[2].state_value('sync:'+S)


def test_binance_adapter_requests_both_order_categories(ctx):
    """检查实际适配器调用 CCXT 规范参数，无需现货钱包权限。"""
    from trader.exchange import Binance
    adapter = Binance(ctx[0])
    assert adapter.client.options['fetchCurrencies'] is False
    assert adapter.client.options['fetchOpenOrders']['warnWithoutSymbol'] is False
    adapter.client.fetch_open_orders = Mock(side_effect=[[{'id': '1'}], [{'id': '2'}]])
    rows = adapter.open_orders()
    assert [r['category'] for r in rows] == ['ordinary', 'conditional']
    assert adapter.client.fetch_open_orders.call_args_list[1].args[-1] == {'trigger': True}


def test_net_income_counts_commission_funding_and_realized(ctx):
    """日亏损计算包含费用，排除入金等非交易流水。"""
    from trader.exchange import Binance
    adapter = Binance(ctx[0])
    adapter.client.fapiPrivateGetIncome = Mock(return_value=[
        {'asset': 'USDT', 'incomeType': 'REALIZED_PNL', 'income': '-4.8'},
        {'asset': 'USDT', 'incomeType': 'COMMISSION', 'income': '-.3'},
        {'asset': 'USDT', 'incomeType': 'FUNDING_FEE', 'income': '.05'},
        {'asset': 'USDT', 'incomeType': 'TRANSFER', 'income': '100'}])
    assert adapter.daily_pnl()['net'] == '-5.05'


def test_definitive_rejection_does_not_leave_unknown_lock(ctx):
    """交易所明确拒绝与超时结果未知必须区分。"""
    from trader.exchange import RejectedOrder
    original = ctx[1].call.side_effect
    def reject(method, *args):
        """仅模拟确定的保证金拒绝响应。"""
        if method == 'create_order':
            raise RejectedOrder('币安可用保证金不足，订单被明确拒绝。')
        return original(method, *args)
    ctx[1].call.side_effect = reject
    result = live(ctx).open(order())
    assert result['state'] == 'rejected' and not ctx[2].pending()
