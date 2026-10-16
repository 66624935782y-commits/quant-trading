"""CCXT USDT 永续适配器；真实行情和账户均来自币安，不提供演示数据。"""
from datetime import datetime, timezone
from decimal import Decimal
import ipaddress
import re
import ccxt

SYMBOLS = {'BTCUSDT': 'BTC/USDT:USDT', 'ETHUSDT': 'ETH/USDT:USDT', 'SOLUSDT': 'SOL/USDT:USDT'}

class UserError(Exception):
    """可直接展示的中文业务错误，禁止包含原始交易所异常文本。"""

    def __init__(self, message, code=None, request_ip=None):
        """只携带脱敏数值错误码与经验证的请求出口 IP。"""
        super().__init__(message)
        self.code, self.request_ip = code, request_ip


class RejectedOrder(UserError):
    """交易所明确拒绝且未接受订单，可安全记录为失败而非结果未知。"""


def safe_error_details(exc):
    """从异常中仅提取数字错误码和合法 IP，绝不返回原始响应、URL 或签名。"""
    text = str(exc)[:16384]
    code = getattr(exc, 'code', None)
    if type(code) is not int or not -999999 <= code < 0:
        match = re.search(r"[\"']code[\"']\s*:\s*(-[0-9]{1,6})(?![0-9])", text)
        code = int(match.group(1)) if match else None
    candidate = getattr(exc, 'request_ip', None)
    if not candidate:
        match = re.search(r'(?:request\s+ip|request_ip)\s*[:=]\s*([0-9a-fA-F:.]+)', text, re.IGNORECASE)
        candidate = match.group(1).rstrip('.') if match else None
    try:
        request_ip = str(ipaddress.ip_address(candidate)) if candidate else None
    except ValueError:
        request_ip = None
    return {'code': code, 'request_ip': request_ip}


def friendly_exchange_error(exc):
    """将币安常见错误码映射为中文；不把含密钥的原始异常拼接进结果。"""
    details = safe_error_details(exc)
    hints = {
        -1002: '币安未授权此读取操作，请检查密钥状态、账户类型与读取权限。',
        -1021: '请求时间不在币安允许窗口内。请在 Windows 设置中打开自动设置时间并点击立即同步，然后重启程序。',
        -1022: '币安签名校验失败。请确认 Key 和 Secret 来自同一组 API、没有填反或多余空格，并确认密钥类型。',
        -2014: '币安认为 API Key 格式不正确。请检查是否把 Key 填错行、漏复制或包含额外字符。',
        -2015: '币安认证被拒绝：可能是密钥无效、API 白名单不包含程序当前出口 IP，或合约读取权限不符；此错误无法单独确定是哪一种。',
        -4056: '币安不接受当前 API 密钥类型，请检查是否使用本工具配置方式支持的 API 类型。',
        -4057: '币安不接受当前 RSA 公钥配置，请检查 API 类型；不要把公钥当成 API Secret。',
    }
    code = details['code']
    if code in hints:
        message = hints[code]
    elif isinstance(exc, ccxt.NetworkError):
        message = '币安连接超时、网络受限或服务不可用，请先检查网络。'
    elif isinstance(exc, ccxt.AuthenticationError):
        message = '币安认证失败，请检查密钥状态、合约读取权限和 IP 白名单。'
    else:
        message = '币安请求失败，请检查账户类型、权限、交易所服务状态。'
    if code is not None:
        message += f'（币安错误码 {code}）'
    if details['request_ip']:
        message += ' 币安报告的请求出口 IP：' + details['request_ip'] + '，请与 API 白名单核对。'
    return message


def number(value):
    """安全转换交易所数值，缺失或非有限数据时拒绝继续。"""
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (ValueError, ArithmeticError):
        raise UserError('交易所返回缺失或无效数值，已停止操作。') from None


class Binance:
    def __init__(self, settings):
        """使用 CCXT 官方签名，不启用调试日志，不输出请求调试内容。"""
        self.client = ccxt.binanceusdm({'apiKey': settings.api_key, 'secret': settings.api_secret,
            'enableRateLimit': True, 'timeout': 12000, 'maxRetriesOnFailure': 0, 'options': {'adjustForTimeDifference': True, 'fetchCurrencies': False, 'fetchOpenOrders': {'warnWithoutSymbol': False}}})
        self.credentials = bool(settings.api_key and settings.api_secret)

    def call(self, method, *args, **kwargs):
        """统一捕获 CCXT 错误，保留超时类型供订单核对，隐藏请求头及响应原文。"""
        try:
            return getattr(self.client, method)(*args, **kwargs)
        except ccxt.NetworkError:
            raise
        except ccxt.AuthenticationError as exc:
            details = safe_error_details(exc)
            raise (RejectedOrder if method == 'create_order' else UserError)(friendly_exchange_error(exc), **details) from None
        except ccxt.InsufficientFunds:
            raise (RejectedOrder if method == 'create_order' else UserError)('币安可用保证金不足，订单被明确拒绝。') from None
        except ccxt.OrderNotFound:
            raise
        except (ccxt.InvalidOrder, ccxt.PermissionDenied):
            raise (RejectedOrder if method == 'create_order' else UserError)('币安明确拒绝请求，请检查订单规则或账户权限。') from None
        except ccxt.BaseError as exc:
            raise UserError(friendly_exchange_error(exc), **safe_error_details(exc)) from None

    def private(self):
        """读取账户前检查密钥配置，禁止以假余额替代错误。"""
        if not self.credentials:
            raise UserError('请先在 .env 填写币安 API Key 和 Secret，重启后读取真实账户。')

    def markets(self):
        """加载真实合约交易规则并复用 CCXT 缓存。"""
        return self.call('load_markets')

    def quotes(self):
        """从币安读取三个交易对的真实最新价、涨跌幅和 USDT 成交额。"""
        self.markets()
        tickers = self.call('fetch_tickers', list(SYMBOLS.values()))
        return [{'symbol': code, 'price': tickers[s]['last'], 'change': tickers[s]['percentage'],
                 'volume_usdt': tickers[s]['quoteVolume'], 'timestamp': tickers[s]['timestamp']}
                for code, s in SYMBOLS.items()]

    def positions(self):
        """读取所有未平 USDT 永续持仓，包括白名单以外的历史持仓。"""
        self.private()
        self.markets()
        rows = self.call('fetch_positions')
        return [row for row in rows if number(row.get('contracts') or 0) > 0 and row.get('symbol', '').endswith(':USDT')]

    def daily_pnl(self):
        """按 UTC 自然日分页汇总真实收益流水，风控净额包含手续费与资金费。"""
        self.private()
        now = datetime.now(timezone.utc)
        start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()*1000)
        end = int(now.timestamp()*1000)
        realized, net = Decimal('0'), Decimal('0')
        for page in range(1, 101):
            rows = self.call('fapiPrivateGetIncome', {'startTime': start, 'endTime': end, 'limit': 1000, 'page': page})
            for row in rows:
                if row.get('asset') != 'USDT':
                    continue
                amount = number(row['income'])
                if row['incomeType'] == 'REALIZED_PNL':
                    realized += amount
                if row['incomeType'] in ('REALIZED_PNL', 'COMMISSION', 'FUNDING_FEE'):
                    net += amount
            if len(rows) < 1000:
                return {'realized': str(realized), 'net': str(net), 'day': now.date().isoformat(), 'timezone': 'UTC'}
        raise UserError('今日流水过多，无法完成完整风控核对，已禁止开仓。')

    def balance(self):
        """读取合约 USDT 总余额与可用保证金，不包括现货余额。"""
        self.private()
        balance = self.call('fetch_balance')
        usdt = balance.get('USDT') or {}
        return {'total': str(number(usdt.get('total'))), 'free': str(number(usdt.get('free')))}

    def mode(self):
        """读取账户单向或双向持仓模式，不擅自切换。"""
        self.private()
        return bool(self.call('fetch_position_mode')['hedged'])

    def open_orders(self):
        """读取全部未成交挂单供展示与撤销，包含非白名单合约。"""
        self.private()
        ordinary = self.call('fetch_open_orders')
        conditional = self.call('fetch_open_orders', None, None, None, {'trigger': True})
        for row in ordinary:
            row['category'] = 'ordinary'
        for row in conditional:
            row['category'] = 'conditional'
        return ordinary + conditional

    def trades(self, symbol, since):
        """分页读取指定合约的实际成交；由服务层分批落盘。"""
        self.private()
        return self.call('fetch_my_trades', symbol, since, 1000)
