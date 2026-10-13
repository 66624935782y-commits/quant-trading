from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import socket
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import ccxt
import pandas as pd
import pytest
import requests

from quant_trading.market_data import (
    BinancePublicSource,
    CandleRequest,
    FRAME_COLUMNS,
    MarketDataError,
    TIMEFRAME_MS,
    build_batch,
    make_demo_batch,
)


START = 1_700_000_000_000
HOUR = TIMEFRAME_MS["1h"]
COLLECTED = datetime.fromtimestamp((START + 5 * HOUR) / 1000, timezone.utc)


def candle(timestamp=START, **values):
    close_time = timestamp + HOUR - 1 if isinstance(timestamp, (int, float)) else START + HOUR - 1
    row = [timestamp, "100", "110", "90", "105", "12.5", close_time, "1250"]
    for field, value in values.items():
        row[FRAME_COLUMNS.index(field)] = value
    return row


@pytest.fixture(autouse=True)
def disallow_network_and_money_operations(monkeypatch):
    forbidden = []
    for name in (
        "create_order", "create_market_order", "create_limit_order", "withdraw",
        "transfer", "set_leverage", "set_margin_mode", "borrow_margin", "repay_margin",
        "fetch_balance", "load_markets", "fetch_ohlcv", "private_get_account",
    ):
        spy = Mock(side_effect=AssertionError(f"Forbidden exchange call: {name}"))
        monkeypatch.setattr(ccxt.binance, name, spy, raising=False)
        forbidden.append(spy)
    network = Mock(side_effect=AssertionError("Tests must not use the network"))
    monkeypatch.setattr(socket.socket, "connect", network)
    monkeypatch.setattr(requests.Session, "request", network)
    yield
    for spy in [*forbidden, network]:
        spy.assert_not_called()


def test_request_defaults_are_frozen_and_valid():
    request = CandleRequest()
    assert (request.market, request.symbol, request.timeframe, request.limit) == (
        "spot", "BTC/USDT", "1h", 200,
    )
    with pytest.raises(FrozenInstanceError):
        request.market = "LIVE"


@pytest.mark.parametrize("kwargs", [
    {"market": "margin"}, {"market": []}, {"market": True},
    {"symbol": "BTC/USDT:USDT"}, {"symbol": "BTC/BUSD"}, {"symbol": []},
    {"timeframe": "1s"}, {"timeframe": "1M"}, {"timeframe": []},
    {"limit": True}, {"limit": "200"}, {"limit": 200.0}, {"limit": 0},
    {"limit": -1}, {"limit": 1001},
])
def test_request_rejects_unsupported_or_ambiguous_values(kwargs):
    with pytest.raises(MarketDataError):
        CandleRequest(**kwargs)


def test_sorting_and_normalization_preserve_raw_response_and_do_not_share_mutations():
    rows = [candle(START + HOUR), candle(START)]
    original = deepcopy(rows)
    batch = build_batch(CandleRequest(), rows, fetched_at=COLLECTED)
    assert list(batch.frame.columns) == list(FRAME_COLUMNS)
    assert str(batch.frame["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert all(str(batch.frame[name].dtype) == "float64" for name in FRAME_COLUMNS[1:])
    assert batch.frame["timestamp"].tolist() == [
        pd.to_datetime(START, unit="ms", utc=True),
        pd.to_datetime(START + HOUR, unit="ms", utc=True),
    ]
    assert batch.raw_rows == original
    assert batch.fetched_at == COLLECTED
    assert batch.source == "binance_public"
    assert batch.warnings == ()
    rows[0][1] = "9"
    batch.frame.loc[0, "close"] = 1
    assert batch.raw_rows == original


def test_complete_duplicate_is_deduplicated_but_preserved_in_raw_rows():
    rows = [candle(), candle()]
    batch = build_batch(CandleRequest(), rows, fetched_at=COLLECTED)
    assert len(batch.raw_rows) == 2
    assert len(batch.frame) == 1
    assert batch.warnings == ("已去除 1 根完全相同的重复 K 线。",)


@pytest.mark.parametrize("other", [candle(close="106"), [*candle(), "different extra field"]])
def test_conflicting_duplicate_rejected_instead_of_arbitrary_selection(other):
    with pytest.raises(MarketDataError, match="冲突"):
        build_batch(CandleRequest(), [candle(), other], fetched_at=COLLECTED)


def test_ohlc_equality_after_float_conversion_does_not_make_raw_rows_identical():
    with pytest.raises(MarketDataError, match="冲突"):
        build_batch(CandleRequest(), [candle(), candle(open=100.0)], fetched_at=COLLECTED)


def test_open_and_future_rows_excluded_with_exact_close_boundary_included():
    rows = [candle(START + index * HOUR) for index in range(7)]
    batch = build_batch(CandleRequest(), rows, fetched_at=COLLECTED)
    assert len(batch.raw_rows) == 7
    assert len(batch.frame) == 5
    assert batch.frame["timestamp"].iloc[-1] == pd.to_datetime(START + 4 * HOUR, unit="ms", utc=True)
    assert batch.warnings == ("已排除 2 根未收盘或未来 K 线。",)


def test_gap_is_reported_without_fabricating_missing_candles_or_volume():
    batch = build_batch(CandleRequest(), [candle(), candle(START + 2 * HOUR)], fetched_at=COLLECTED)
    assert len(batch.frame) == 2
    assert batch.frame["volume"].tolist() == [12.5, 12.5]
    assert batch.warnings == ("检测到 1 处 K 线时间间隔缺口，未填补行情。",)


def test_short_interval_is_reported_without_silently_reindexing():
    batch = build_batch(CandleRequest(), [candle(), candle(START + 1)], fetched_at=COLLECTED)
    assert len(batch.frame) == 2
    assert "短于所选周期" in batch.warnings[0]


@pytest.mark.parametrize("rows", [None, {}, [], [[START]], ["not a row"]])
def test_invalid_response_shapes_rejected(rows):
    with pytest.raises(MarketDataError):
        build_batch(CandleRequest(), rows, fetched_at=COLLECTED)


@pytest.mark.parametrize("timestamp", [True, -1, "1700000000000", START + 0.5, float("nan")])
def test_timestamp_requires_nonnegative_integer_milliseconds(timestamp):
    with pytest.raises(MarketDataError, match="整数毫秒"):
        build_batch(CandleRequest(), [candle(timestamp)], fetched_at=COLLECTED)


@pytest.mark.parametrize("field,value", [
    ("open", "NaN"), ("close", "Infinity"), ("high", "-Infinity"),
    ("low", "0"), ("close", "-1"), ("open", "bad"), ("open", True),
    ("volume", "-1"), ("volume", "NaN"), ("volume", "Infinity"),
    ("volume", None), ("open", "1e99999"),
])
def test_invalid_price_or_volume_rejected(field, value):
    with pytest.raises(MarketDataError):
        build_batch(CandleRequest(), [candle(**{field: value})], fetched_at=COLLECTED)


@pytest.mark.parametrize("values", [
    {"open": "111"}, {"close": "89"}, {"low": "106"}, {"high": "89"},
])
def test_impossible_ohlc_relationships_rejected(values):
    with pytest.raises(MarketDataError, match="高低价格"):
        build_batch(CandleRequest(), [candle(**values)], fetched_at=COLLECTED)


def test_zero_volume_is_valid():
    batch = build_batch(CandleRequest(), [candle(volume="0")], fetched_at=COLLECTED)
    assert batch.frame["volume"].iloc[0] == 0


def test_no_closed_candle_is_an_error_and_does_not_become_fake_prices():
    with pytest.raises(MarketDataError, match="已收盘"):
        build_batch(CandleRequest(), [candle(START + 5 * HOUR)], fetched_at=COLLECTED)


def test_invalid_open_candle_does_not_escape_validation():
    with pytest.raises(MarketDataError, match="有效数值"):
        build_batch(CandleRequest(), [candle(), candle(START + 5 * HOUR, open="bad")], fetched_at=COLLECTED)


def test_collection_time_is_utc_and_naive_datetime_rejected():
    shanghai = timezone(timedelta(hours=8))
    batch = build_batch(CandleRequest(), [candle()], fetched_at=COLLECTED.astimezone(shanghai))
    assert batch.fetched_at == COLLECTED
    assert batch.fetched_at.tzinfo is timezone.utc
    with pytest.raises(MarketDataError, match="时区"):
        build_batch(CandleRequest(), [candle()], fetched_at=COLLECTED.replace(tzinfo=None))


def test_unknown_source_label_rejected():
    with pytest.raises(MarketDataError, match="来源"):
        build_batch(CandleRequest(), [candle()], source="real_trading", fetched_at=COLLECTED)


def test_invalid_request_type_rejected_in_all_public_entry_points():
    for operation in (
        lambda: build_batch({}, [candle()], fetched_at=COLLECTED),
        lambda: BinancePublicSource().fetch({}),
        lambda: make_demo_batch({}, now=COLLECTED),
    ):
        with pytest.raises(MarketDataError, match="CandleRequest"):
            operation()


@pytest.mark.parametrize("market,endpoint,other", [
    ("spot", "public_get_klines", "fapipublic_get_klines"),
    ("usdt_futures", "fapipublic_get_klines", "public_get_klines"),
])
def test_public_source_only_calls_correct_raw_endpoint_without_credentials(monkeypatch, market, endpoint, other):
    client = ccxt.binance()
    target = Mock(return_value=[candle()])
    unselected = Mock(side_effect=AssertionError("Wrong market endpoint"))
    monkeypatch.setattr(client, endpoint, target)
    monkeypatch.setattr(client, other, unselected)
    factory = Mock(return_value=client)
    monkeypatch.setattr(ccxt, "binance", factory)
    batch = BinancePublicSource().fetch(CandleRequest(market=market, symbol="ETH/USDT", timeframe="15m", limit=7))
    factory.assert_called_once_with({"enableRateLimit": True, "timeout": 12_000})
    assert not client.apiKey
    assert not client.secret
    assert client.urls["api"]["public"] == "https://data-api.binance.vision/api/v3"
    assert client.urls["api"]["fapiPublic"] == "https://fapi.binance.com/fapi/v1"
    target.assert_called_once_with({"symbol": "ETHUSDT", "interval": "15m", "limit": 7})
    unselected.assert_not_called()
    assert batch.source == "binance_public"
    assert batch.raw_rows == [candle()]


@pytest.mark.parametrize("error", [
    ccxt.NetworkError("network failed"), ccxt.RequestTimeout("timed out"),
    ccxt.ExchangeNotAvailable("HTTP 451"), ccxt.RateLimitExceeded("HTTP 429"),
    PermissionError("network denied"),
])
def test_public_network_failure_is_explicit_and_never_silently_replaced(monkeypatch, error):
    client = ccxt.binance()
    monkeypatch.setattr(client, "public_get_klines", Mock(side_effect=error))
    demo = Mock(side_effect=AssertionError("Never silently change source"))
    monkeypatch.setattr("quant_trading.market_data.make_demo_batch", demo)
    with pytest.raises(MarketDataError, match="不会自动切换"):
        BinancePublicSource(client_factory=lambda config: client).fetch(CandleRequest())
    demo.assert_not_called()


@pytest.mark.parametrize("market", ["spot", "usdt_futures"])
@pytest.mark.parametrize("timeframe", list(TIMEFRAME_MS))
def test_demo_candles_are_deterministic_explicitly_labelled_and_fully_closed(market, timeframe):
    request = CandleRequest(market=market, symbol="SOL/USDT", timeframe=timeframe, limit=12)
    first = make_demo_batch(request, now=COLLECTED)
    second = make_demo_batch(request, now=COLLECTED)
    assert first.source == "synthetic_demo"
    assert "不是交易所历史行情" in first.warnings[0]
    assert first.raw_rows == second.raw_rows
    pd.testing.assert_frame_equal(first.frame, second.frame)
    assert len(first.frame) == 12
    step = TIMEFRAME_MS[timeframe]
    assert all(row[0] + step <= int(COLLECTED.timestamp() * 1000) for row in first.raw_rows)
    assert all(right[0] - left[0] == step for left, right in zip(first.raw_rows, first.raw_rows[1:]))


def test_demo_never_generates_negative_unix_timestamps():
    with pytest.raises(MarketDataError, match="时间过早"):
        make_demo_batch(CandleRequest(), now=datetime(1970, 1, 1, tzinfo=timezone.utc))


def test_saved_collection_time_rebuild_produces_same_frame_and_warnings():
    rows = [candle(), candle(), candle(START + 2 * HOUR), candle(START + 5 * HOUR)]
    original = build_batch(CandleRequest(), rows, fetched_at=COLLECTED)
    rebuilt = build_batch(original.request, original.raw_rows, fetched_at=original.fetched_at, source=original.source)
    pd.testing.assert_frame_equal(original.frame, rebuilt.frame)
    assert original.warnings == rebuilt.warnings


@pytest.mark.parametrize("market,host,path", [
    ("spot", "data-api.binance.vision", "/api/v3/klines"),
    ("usdt_futures", "fapi.binance.com", "/fapi/v1/klines"),
])
def test_real_ccxt_routes_only_to_public_get_without_authentication(monkeypatch, market, host, path):
    calls = []

    def public_transport(client, url, method="GET", headers=None, body=None):
        assert not client.apiKey and not client.secret
        assert method == "GET" and body is None
        assert not headers or not any("api" in key.lower() for key in headers)
        calls.append(url)
        return [candle()]

    credentials = Mock(side_effect=AssertionError("Public candles must not require credentials"))
    monkeypatch.setattr(ccxt.binance, "fetch", public_transport)
    monkeypatch.setattr(ccxt.binance, "check_required_credentials", credentials)
    batch = BinancePublicSource().fetch(CandleRequest(market=market, limit=3))
    assert len(batch.frame) == 1
    assert len(calls) == 1
    url = urlsplit(calls[0])
    assert url.scheme == "https" and url.hostname == host and url.path == path
    assert parse_qs(url.query) == {"symbol": ["BTCUSDT"], "interval": ["1h"], "limit": ["3"]}
    credentials.assert_not_called()
