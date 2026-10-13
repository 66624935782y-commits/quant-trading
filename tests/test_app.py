from pathlib import Path
from decimal import Decimal
from unittest.mock import Mock

import ccxt
from streamlit.testing.v1 import AppTest


APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_page_can_submit_virtual_buy_then_sell_without_exchange_calls(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No real order may be submitted"))
    monkeypatch.setattr(ccxt.binance, "create_order", forbidden)
    monkeypatch.setattr(ccxt.binanceusdm, "create_order", forbidden)
    page = AppTest.from_file(APP).run(timeout=15)
    assert not page.exception
    assert page.success[0].value.startswith("PAPER")
    assert "不是币安实时行情" in page.info[0].value
    assert page.metric[0].value == "10,000.00"
    page.button[0].click().run()
    assert not page.exception
    assert not page.error
    assert page.metric[0].value == "9,499.25"
    assert len(page.session_state["paper_broker"].fills) == 1
    # Form fields are sent together with submission, not with an intermediate rerun.
    page.selectbox[0].select("虚拟卖出")
    page.button[0].click().run()
    assert not page.exception
    assert page.metric[0].value == "9,998.50"
    assert page.metric[1].value == "0.0000"
    forbidden.assert_not_called()


def test_ui_rejects_excessive_buy_without_mutating_balance():
    page = AppTest.from_file(APP).run(timeout=15)
    page.number_input[0].set_value(0.1)
    page.button[0].click().run()
    assert not page.exception
    assert "仓位上限" in page.error[0].value
    assert page.metric[0].value == "10,000.00"
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
    page.number_input[0].set_value(0.012).increment()
    page.button[0].click().run()
    assert not page.exception
    assert not page.error
    assert page.session_state["paper_broker"].quantity == Decimal("0.013")
