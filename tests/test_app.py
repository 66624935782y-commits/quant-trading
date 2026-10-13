from pathlib import Path
from decimal import Decimal
from unittest.mock import Mock

import ccxt
import pytest
import requests
from streamlit.testing.v1 import AppTest

from quant_trading import market_data, storage
from quant_trading.market_data import CandleRequest, MarketDataError, build_batch, make_demo_batch

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture(autouse=True)
def isolate_storage_and_forbid_network(monkeypatch, tmp_path):
    root = tmp_path / "data"
    original_save, original_list = storage.save_batch, storage.list_saved_batches
    monkeypatch.setattr(storage, "save_batch", lambda batch, _root: original_save(batch, root))
    monkeypatch.setattr(storage, "list_saved_batches", lambda _root: original_list(root))
    network = Mock(side_effect=AssertionError("UI tests must not use live network"))
    monkeypatch.setattr(requests.Session, "request", network)
    for exchange in (ccxt.binance, ccxt.binanceusdm):
        for name in ("create_order", "create_market_order", "withdraw", "transfer", "set_leverage"):
            monkeypatch.setattr(exchange, name, Mock(side_effect=AssertionError(name)))
    yield root
    network.assert_not_called()


def metric(page, label):
    return next(item.value for item in page.metric if item.label == label)


def public_batch(request):
    demo = make_demo_batch(request)
    return build_batch(request, demo.raw_rows, source="binance_public", fetched_at=demo.fetched_at)


def test_page_can_submit_virtual_buy_then_sell_without_exchange_calls():
    page = AppTest.from_file(APP).run(timeout=15)
    assert not page.exception
    assert page.success[0].value.startswith("PAPER")
    assert any("不是币安实时行情" in item.value for item in page.info)
    assert metric(page, "虚拟余额（USDT）") == "10,000.00"
    page.button(key="paper_submit").click().run()
    assert not page.exception
    assert not page.error
    assert metric(page, "虚拟余额（USDT）") == "9,499.25"
    assert len(page.session_state["paper_broker"].fills) == 1
    page.selectbox(key="paper_side").select("虚拟卖出")
    page.button(key="paper_submit").click().run()
    assert not page.exception
    assert metric(page, "虚拟余额（USDT）") == "9,998.50"
    assert metric(page, "虚拟持仓（BTC）") == "0.0000"


def test_ui_rejects_excessive_buy_without_mutating_balance():
    page = AppTest.from_file(APP).run(timeout=15)
    page.number_input(key="paper_quantity").set_value(0.1)
    page.button(key="paper_submit").click().run()
    assert not page.exception
    assert "仓位上限" in page.error[0].value
    assert metric(page, "虚拟余额（USDT）") == "10,000.00"
    assert not page.session_state["paper_broker"].fills


def test_page_stops_before_creating_account_in_live_mode(monkeypatch):
    monkeypatch.setenv("QT_MODE", "LIVE")
    page = AppTest.from_file(APP).run(timeout=15)
    assert not page.exception
    assert "安全检查未通过" in page.error[0].value
    assert len(page.metric) == 0
    assert len(page.button) == 0


def test_quantity_step_uses_displayed_decimal_precision():
    page = AppTest.from_file(APP).run(timeout=15)
    page.number_input(key="paper_quantity").set_value(0.012).increment()
    page.button(key="paper_submit").click().run()
    assert not page.exception
    assert not page.error
    assert page.session_state["paper_broker"].quantity == Decimal("0.013")


def test_initial_chart_is_explicit_demo_and_does_not_write_data(isolate_storage_and_forbid_network):
    page = AppTest.from_file(APP).run(timeout=15)
    assert not page.exception
    assert [item.label for item in page.tabs] == ["行情分析", "数据记录", "模拟账户"]
    assert any("人工生成" in item.value for item in page.warning)
    assert page.session_state["market_batch"].source == "synthetic_demo"
    assert len(page.get("plotly_chart")) == 1
    assert len(page.session_state["market_batch"].frame) == 200
    assert not isolate_storage_and_forbid_network.exists()


def test_form_changes_market_pair_timeframe_and_persists_explicit_demo(isolate_storage_and_forbid_network):
    page = AppTest.from_file(APP).run(timeout=15)
    before_account = page.session_state["paper_broker"].snapshot("50000")
    page.selectbox(key="market_kind").select("USDT 永续合约（仅行情）")
    page.selectbox(key="market_symbol").select("ETH/USDT")
    page.selectbox(key="market_interval").select("4h")
    page.selectbox(key="market_limit").select(500)
    page.button(key="market_demo").click().run()
    assert not page.exception
    assert not page.error
    batch = page.session_state["market_batch"]
    assert batch.request == CandleRequest("usdt_futures", "ETH/USDT", "4h", 500)
    assert batch.source == "synthetic_demo"
    assert len(batch.frame) == 500
    directory = page.session_state["market_saved_path"]
    assert directory.is_relative_to(isolate_storage_and_forbid_network)
    assert (directory / "raw.json").exists()
    assert (directory / "candles.csv").exists()
    assert storage.load_batch(directory).request == batch.request
    assert page.session_state["paper_broker"].snapshot("50000") == before_account


def test_fetch_and_restart_load_saved_public_data(monkeypatch):
    fetch = Mock(side_effect=lambda request: public_batch(request))
    monkeypatch.setattr(market_data.BinancePublicSource, "fetch", lambda _self, request: fetch(request))
    page = AppTest.from_file(APP).run(timeout=15)
    page.selectbox(key="market_symbol").select("SOL/USDT")
    page.button(key="market_fetch").click().run()
    assert not page.exception
    assert not page.error
    fetch.assert_called_once_with(CandleRequest("spot", "SOL/USDT", "1h", 200))
    saved = page.session_state["market_saved_path"]
    assert page.session_state["market_batch"].source == "binance_public"
    assert any("公共行情快照" in item.value for item in page.info)
    restarted = AppTest.from_file(APP).run(timeout=15)
    assert restarted.session_state["market_batch"].source == "synthetic_demo"
    restarted.selectbox(key="saved_batch_choice").set_value(str(saved))
    restarted.button(key="saved_load").click().run()
    assert not restarted.exception
    assert not restarted.error
    assert restarted.session_state["market_batch"].source == "binance_public"
    assert restarted.session_state["market_batch"].request.symbol == "SOL/USDT"
    assert restarted.session_state["market_saved_path"] == saved
    fetch.assert_called_once()


def test_network_failure_preserves_previous_batch_without_silent_demo(monkeypatch):
    generate = Mock(wraps=make_demo_batch)
    monkeypatch.setattr(market_data, "make_demo_batch", generate)
    page = AppTest.from_file(APP).run(timeout=15)
    previous = page.session_state["market_batch"]
    monkeypatch.setattr(market_data.BinancePublicSource, "fetch",
                        lambda _self, _request: (_ for _ in ()).throw(MarketDataError("网络受限")))
    page.button(key="market_fetch").click().run()
    assert not page.exception
    assert "网络受限" in page.error[0].value
    assert page.session_state["market_batch"] is previous
    assert generate.call_count == 1
    assert any("未自动切换" in item.value for item in page.warning)


def test_failed_disk_write_does_not_replace_displayed_batch(monkeypatch):
    page = AppTest.from_file(APP).run(timeout=15)
    previous = page.session_state["market_batch"]
    monkeypatch.setattr(storage, "save_batch",
                        lambda *_args: (_ for _ in ()).throw(storage.StorageError("磁盘写入失败")))
    page.selectbox(key="market_symbol").select("ETH/USDT")
    page.button(key="market_demo").click().run()
    assert not page.exception
    assert "磁盘写入失败" in page.error[0].value
    assert page.session_state["market_batch"] is previous
    assert page.session_state["market_saved_path"] is None


def test_distinct_archives_with_same_collection_time_have_distinct_labels():
    batch = make_demo_batch(CandleRequest(limit=3))
    storage.save_batch(batch, Path("ignored"))
    storage.save_batch(batch, Path("ignored"))
    page = AppTest.from_file(APP).run(timeout=15)
    assert not page.exception
    labels = page.selectbox(key="saved_batch_choice").options
    assert len(labels) == 2
    assert len(set(labels)) == 2
