from decimal import Decimal, localcontext
import json

import pytest

from quant_trading.paper import PaperBroker


def test_buy_sell_accounting_includes_adverse_slippage_and_both_fees():
    broker = PaperBroker()
    buy = broker.submit_order("buy", "0.01", "50000")
    assert buy.price == Decimal("50025")
    assert buy.fee == Decimal("0.50025")
    assert broker.cash == Decimal("9499.24975")
    assert broker.equity("50000") == Decimal("9999.24975")
    sell = broker.submit_order("sell", "0.01", "50000")
    assert sell.price == Decimal("49975")
    assert sell.fee == Decimal("0.49975")
    assert broker.cash == Decimal("9998.5")
    assert broker.quantity == 0
    assert [fill.sequence for fill in broker.fills] == [1, 2]


@pytest.mark.parametrize("side,quantity,price", [
    ("buy", "0", "50000"), ("buy", "-1", "50000"),
    ("buy", "NaN", "50000"), ("buy", "Infinity", "50000"),
    ("buy", "0.01", "NaN"), ("buy", "0.01", "Infinity"),
    ("buy", "0.01", "0"), ("buy", "0.01", "-1"),
    ("buy", "garbage", "50000"), ("buy", True, "50000"),
    ("sell", "0.1", "50000"), ("buy", "1", "50000"),
    ("other", "0.01", "50000"),
    ("buy", "0.01", "1e-1000000"), ("buy", "1e13", "50000"),
])
def test_rejected_order_does_not_change_account(side, quantity, price):
    broker = PaperBroker()
    before = broker.snapshot("50000")
    with pytest.raises(ValueError):
        broker.submit_order(side, quantity, price)
    assert broker.snapshot("50000") == before


def test_position_limit_uses_post_fill_equity_after_costs():
    broker = PaperBroker()
    # Exactly 25% of the INITIAL balance exceeds 25% of equity after costs.
    with pytest.raises(ValueError, match="仓位上限"):
        broker.submit_order("buy", "0.05", "50000")
    broker.submit_order("buy", "0.04998", "50000")
    assert broker.quantity * Decimal("50000") <= broker.equity("50000") * Decimal("0.25")


def test_repeated_buys_cannot_bypass_position_limit():
    broker = PaperBroker()
    broker.submit_order("buy", "0.03", "50000")
    before = broker.snapshot("50000")
    with pytest.raises(ValueError, match="仓位上限"):
        broker.submit_order("buy", "0.03", "50000")
    assert broker.snapshot("50000") == before


def test_reducing_position_allowed_even_if_market_move_exceeds_limit():
    broker = PaperBroker()
    broker.submit_order("buy", "0.04", "50000")
    broker.submit_order("sell", "0.01", "200000")
    assert broker.quantity == Decimal("0.03")


@pytest.mark.parametrize("kwargs", [
    {"initial_cash": "0"}, {"initial_cash": "NaN"},
    {"fee_rate": "-0.01"}, {"fee_rate": "1"},
    {"slippage_rate": "-0.01"}, {"slippage_rate": "1"},
    {"max_position_fraction": "0"}, {"max_position_fraction": "1.01"},
])
def test_invalid_account_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        PaperBroker(**kwargs)


def test_export_is_json_serializable_with_exact_amounts_and_demo_label():
    broker = PaperBroker()
    broker.submit_order("buy", "0.01", "50000")
    exported = json.loads(json.dumps(broker.snapshot("50000")))
    assert exported["mode"] == "PAPER"
    assert exported["price_source"] == "offline_demo"
    assert Decimal(exported["cash"]) == Decimal("9499.24975")
    assert Decimal(exported["fills"][0]["fee"]) == Decimal("0.50025")


def test_supported_small_costs_are_preserved_even_with_low_ambient_precision():
    with localcontext(prec=4):
        broker = PaperBroker()
        broker.submit_order("buy", "0.000000000001", "0.000000000001")
        assert broker.cash < Decimal("10000")
        assert broker.quantity == Decimal("0.000000000001")
