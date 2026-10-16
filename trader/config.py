"""从项目 .env 读取配置，不显示或记录任何密钥片段。"""
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    password: str = field(repr=False)
    live: bool
    daily_loss: Decimal
    max_notional: Decimal
    leverage: int
    session_hours: int
    database: Path
    logfile: Path


def load_settings(path: Path = ROOT / '.env') -> Settings:
    """读取并严格校验本地配置，拒绝默认密码与不明确的交易开关。"""
    values = dotenv_values(path)
    password = values.get('ADMIN_PASSWORD') or ''
    if len(password) < 16 or password.startswith('CHANGE_ME'):
        raise ValueError('请在 .env 设置至少 16 位的独立管理员密码。')
    flag = (values.get('LIVE_TRADING') or 'false').lower().strip()
    if flag not in ('true', 'false'):
        raise ValueError('LIVE_TRADING 必须为 true 或 false。')
    try:
        daily = Decimal(values.get('MAX_DAILY_LOSS_USDT') or '5')
        maximum = Decimal(values.get('MAX_ORDER_NOTIONAL_USDT') or '10')
        leverage = int(values.get('DEFAULT_LEVERAGE') or '5')
        hours = int(values.get('SESSION_HOURS') or '8')
        if not daily.is_finite() or not maximum.is_finite() or daily <= 0 or maximum <= 0:
            raise ValueError()
        if not 1 <= leverage <= 20 or not 1 <= hours <= 24:
            raise ValueError()
    except (ValueError, ArithmeticError):
        raise ValueError('风控金额必须大于零，杠杆须为 1–20，会话有效期须为 1–24 小时。') from None
    key, secret = values.get('BINANCE_API_KEY') or '', values.get('BINANCE_API_SECRET') or ''
    if flag == 'true' and not (key and secret):
        raise ValueError('实盘模式需要在 .env 配置币安 API Key 和 Secret。')
    return Settings(key, secret, password, flag == 'true', daily, maximum, leverage, hours,
                    ROOT / (values.get('DATABASE_PATH') or 'runtime/trader.sqlite3'),
                    ROOT / (values.get('LOG_PATH') or 'runtime/trader.log'))
