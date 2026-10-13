"""Read-only public Binance candles and explicitly labelled offline examples.

Only the two raw public K-line endpoints are used. This module never discovers
markets through authenticated APIs and has no order or account operations.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Callable

import ccxt
import pandas as pd


MARKETS = ("spot", "usdt_futures")
SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
FRAME_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
SOURCES = ("binance_public", "synthetic_demo")


class MarketDataError(ValueError):
    """A public market request or candle dataset is unusable."""


@dataclass(frozen=True)
class CandleRequest:
    market: str = "spot"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    limit: int = 200

    def __post_init__(self) -> None:
        if not isinstance(self.market, str) or self.market not in MARKETS:
            raise MarketDataError("市场仅支持现货 spot 或 USDT 本位合约 usdt_futures。")
        if not isinstance(self.symbol, str) or self.symbol not in SYMBOLS:
            raise MarketDataError("交易对仅支持 BTC/USDT、ETH/USDT 或 SOL/USDT。")
        if not isinstance(self.timeframe, str) or self.timeframe not in TIMEFRAME_MS:
            raise MarketDataError("K 线周期仅支持 1m、5m、15m、1h、4h 或 1d。")
        if type(self.limit) is not int or not 1 <= self.limit <= 1000:
            raise MarketDataError("K 线数量必须是 1 至 1000 的整数。")


@dataclass
class MarketBatch:
    request: CandleRequest
    raw_rows: list[list[Any]]
    fetched_at: datetime
    source: str
    frame: pd.DataFrame
    warnings: tuple[str, ...]


def _utc_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError("采集时间必须是带时区的日期时间。")
    return value.astimezone(timezone.utc)


def _number(value: Any, *, positive: bool, row_number: int, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise MarketDataError(f"第 {row_number} 根 K 线的 {field} 不是有效数值。")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise MarketDataError(f"第 {row_number} 根 K 线的 {field} 不是有效数值。") from None
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        label = "有限正数" if positive else "有限非负数"
        raise MarketDataError(f"第 {row_number} 根 K 线的 {field} 必须是{label}。")
    return number


def build_batch(
    request: CandleRequest,
    raw_rows: list[list[Any]],
    *,
    source: str = "binance_public",
    fetched_at: datetime | None = None,
) -> MarketBatch:
    """Validate all original rows, then retain only sorted, closed candles.

    Raw API rows are preserved separately, including excluded candles and extra
    exchange fields. No prices or missing periods are filled in.
    """
    if not isinstance(request, CandleRequest):
        raise MarketDataError("必须提供有效的 CandleRequest 行情请求。")
    if not isinstance(source, str) or source not in SOURCES:
        raise MarketDataError("行情来源必须是 binance_public 或 synthetic_demo。")
    collected = _utc_time(fetched_at)
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MarketDataError("行情响应为空或不是 K 线数组。")
    original = deepcopy(raw_rows)
    step = TIMEFRAME_MS[request.timeframe]
    cutoff_ms = int(collected.timestamp() * 1000)
    by_time: dict[int, tuple[list[Any], list[Any]]] = {}
    duplicate_count = 0
    for row_number, row in enumerate(original, 1):
        if not isinstance(row, list) or len(row) < 6:
            raise MarketDataError(f"第 {row_number} 根 K 线缺少时间、OHLC 或成交量。")
        timestamp = row[0]
        if type(timestamp) is not int or timestamp < 0:
            raise MarketDataError(f"第 {row_number} 根 K 线时间必须是非负整数毫秒。")
        values = [
            _number(row[index], positive=index < 5, row_number=row_number, field=field)
            for index, field in enumerate(FRAME_COLUMNS[1:], 1)
        ]
        open_price, high, low, close, _volume = values
        if not low <= open_price <= high or not low <= close <= high:
            raise MarketDataError(f"第 {row_number} 根 K 线的 OHLC 高低价格关系不合理。")
        if timestamp in by_time:
            if row != by_time[timestamp][0]:
                raise MarketDataError("同一时间存在冲突的 K 线，已拒绝该批数据。")
            duplicate_count += 1
            continue
        by_time[timestamp] = (row, [timestamp, *values])

    ordered = [entry[1] for _timestamp, entry in sorted(by_time.items())]
    closed = [row for row in ordered if row[0] + step <= cutoff_ms]
    excluded_count = len(ordered) - len(closed)
    if not closed:
        raise MarketDataError("没有可用的已收盘 K 线；未收盘或未来数据不会用于分析。")
    try:
        frame = pd.DataFrame(closed, columns=FRAME_COLUMNS)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        frame[list(FRAME_COLUMNS[1:])] = frame[list(FRAME_COLUMNS[1:])].astype("float64")
    except (ValueError, OverflowError, TypeError):
        raise MarketDataError("行情时间超出可处理范围。") from None

    warnings: list[str] = []
    if source == "synthetic_demo":
        warnings.append("此数据为算法生成的离线示例，不是交易所历史行情。")
    if duplicate_count:
        warnings.append(f"已去除 {duplicate_count} 根完全相同的重复 K 线。")
    if excluded_count:
        warnings.append(f"已排除 {excluded_count} 根未收盘或未来 K 线。")
    intervals = [right[0] - left[0] for left, right in zip(closed, closed[1:])]
    gap_count = sum(interval > step for interval in intervals)
    irregular_count = sum(interval < step for interval in intervals)
    if gap_count:
        warnings.append(f"检测到 {gap_count} 处 K 线时间间隔缺口，未填补行情。")
    if irregular_count:
        warnings.append(f"检测到 {irregular_count} 处短于所选周期的时间间隔，请检查原始行情。")
    return MarketBatch(request, original, collected, source, frame, tuple(warnings))


class BinancePublicSource:
    """Fetch only raw, public Binance K lines without exchange credentials."""

    def __init__(self, client_factory: Callable[..., Any] | None = None) -> None:
        self._client_factory = client_factory

    def fetch(self, request: CandleRequest) -> MarketBatch:
        if not isinstance(request, CandleRequest):
            raise MarketDataError("必须提供有效的 CandleRequest 行情请求。")
        factory = self._client_factory or ccxt.binance
        client = factory({"enableRateLimit": True, "timeout": 12_000})
        # Fixed Binance endpoints, rather than a user-supplied URL or private API.
        client.urls["api"]["public"] = "https://data-api.binance.vision/api/v3"
        client.urls["api"]["fapiPublic"] = "https://fapi.binance.com/fapi/v1"
        params = {
            "symbol": request.symbol.replace("/", ""),
            "interval": request.timeframe,
            "limit": request.limit,
        }
        try:
            if request.market == "spot":
                rows = client.public_get_klines(params)
            else:
                # CCXT's generated snake-case name does not separate "fapiPublic".
                rows = client.fapipublic_get_klines(params)
        except (ccxt.BaseError, OSError) as exc:
            raise MarketDataError(
                f"无法读取币安公共行情（{type(exc).__name__}）。"
                "请检查网络、服务可用性与所在地区访问限制；不会自动切换为演示数据。"
            ) from None
        return build_batch(request, rows, fetched_at=datetime.now(timezone.utc))


def make_demo_batch(request: CandleRequest, *, now: datetime | None = None) -> MarketBatch:
    """Generate deterministic closed example candles without any network access."""
    if not isinstance(request, CandleRequest):
        raise MarketDataError("必须提供有效的 CandleRequest 行情请求。")
    collected = _utc_time(now)
    step = TIMEFRAME_MS[request.timeframe]
    last_open = int(collected.timestamp() * 1000) // step * step - step
    first_open = last_open - (request.limit - 1) * step
    if first_open < 0:
        raise MarketDataError("示例时间过早，无法生成所需数量的非负时间 K 线。")
    base = {"BTC/USDT": 50_000.0, "ETH/USDT": 3_000.0, "SOL/USDT": 150.0}[request.symbol]
    if request.market == "usdt_futures":
        base *= 1.001
    rows = []
    for index in range(request.limit):
        opened = first_open + index * step
        open_price = base * (1 + 0.02 * math.sin(index / 8) + index * 0.00005)
        close = base * (1 + 0.02 * math.sin((index + 1) / 8) + (index + 1) * 0.00005)
        high = max(open_price, close) * 1.002
        low = min(open_price, close) * 0.998
        volume = 20 + 8 * (1 + math.sin(index / 5))
        rows.append([
            opened, f"{open_price:.8f}", f"{high:.8f}", f"{low:.8f}",
            f"{close:.8f}", f"{volume:.8f}", opened + step - 1,
            f"{volume * close:.8f}", 100, "0", "0", "0",
        ])
    return build_batch(request, rows, source="synthetic_demo", fetched_at=collected)
