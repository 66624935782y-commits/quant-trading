"""CCXT USDT 永续适配器；真实行情和账户均来自币安，不提供演示数据。"""
from datetime import datetime, timezone
from decimal import Decimal
import ccxt

SYMBOLS = {'BTCUSDT': 'BTC/USDT:USDT', 'ETHUSDT': 'ETH/USDT:USDT', 'SOLUSDT': 'SOL/USDT:USDT'}

class UserError(Exception):
    """可直接展示的中文业务错误，禁止包含原始交易所异常文本。"""


class RejectedOrder(UserError):
    """交易所明确拒绝且未接受订单，可安全记录为失败而非结果未知。"""


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
        """使用 CCXT 官方签名，不启用调试日志，不读取任何代理环境配置。"""
        self.client = ccxt.binanceusdm({'apiKey': settings.api_key, 'secret': settings.api_secret,
            'enableRateLimit': True, 'timeout': 12000, 'maxRetriesOnFailure': 0, 'options': {'adjustForTimeDifference': True, 'fetchCurrencies': False, 'fetchOpenOrders': {'warnWithoutSymbol': False}}})
        self.credentials = bool(settings.api_key and settings.api_secret)

    def call(self, method, *args, **kwargs):
        """统一捕获 CCXT 错误，保留超时类型供订单核对，隐藏请求头及响应原文。"""
        try:
            return getattr(self.client, method)(*args, **kwargs)
        except ccxt.NetworkError:
            raise
        except ccxt.AuthenticationError:
            raise (RejectedOrder if method == 'create_order' else UserError)('币安认证失败，请检查密钥、合约权限和 IP 白名单。') from None
        except ccxt.InsufficientFunds:
            raise (RejectedOrder if method == 'create_order' else UserError)('币安可用保证金不足，订单被明确拒绝。') from None
        except ccxt.OrderNotFound:
            raise
        except (ccxt.InvalidOrder, ccxt.PermissionDenied):
            raise (RejectedOrder if method == 'create_order' else UserError)('币安明确拒绝请求，请检查订单规则或账户权限。') from None
        except ccxt.BaseError:
            raise UserError('币安拒绝请求，请检查交易规则、账户模式、权限及服务状态。') from None

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
