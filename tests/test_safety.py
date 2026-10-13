from dataclasses import FrozenInstanceError
from unittest.mock import Mock
import socket

import ccxt
import pytest
import requests

from quant_trading.config import SafetyError, SafetyPolicy
from quant_trading.paper import PaperBroker


def test_default_is_paper_with_all_money_operations_disabled():
    policy = SafetyPolicy.from_environment({})
    assert policy.mode == "PAPER"
    assert not any((policy.live_orders_enabled, policy.withdrawals_enabled,
                    policy.leverage_enabled, policy.transfers_enabled))
    with pytest.raises(FrozenInstanceError):
        policy.mode = "LIVE"


@pytest.mark.parametrize("mode", ["LIVE", "TESTNET", "paper", "", "unknown"])
def test_unsupported_modes_fail_closed(mode):
    with pytest.raises(SafetyError):
        SafetyPolicy.from_environment({"QT_MODE": mode})


@pytest.mark.parametrize("field", [
    "live_orders_enabled", "withdrawals_enabled", "leverage_enabled", "transfers_enabled",
])
def test_each_money_operation_has_an_independent_locked_switch(field):
    with pytest.raises(SafetyError):
        SafetyPolicy(**{field: True})
    with pytest.raises(SafetyError):
        SafetyPolicy.from_environment({"QT_" + field.upper(): "true"})


@pytest.mark.parametrize("value", ["yes", "1", "", "typo"])
def test_invalid_switch_values_fail_closed(value):
    with pytest.raises(SafetyError):
        SafetyPolicy.from_environment({"QT_LIVE_ORDERS_ENABLED": value})


def test_broker_constructor_honours_environment_guard(monkeypatch):
    monkeypatch.setenv("QT_MODE", "LIVE")
    with pytest.raises(SafetyError):
        PaperBroker()


def test_real_order_and_network_interfaces_never_called_during_paper_trading(monkeypatch):
    # Fail immediately if an actual PAPER order path ever reaches an exchange or network.
    forbidden = []
    for exchange in (ccxt.binance, ccxt.binanceusdm):
        for name in ("create_order", "create_market_order", "withdraw", "transfer", "set_leverage"):
            spy = Mock(side_effect=AssertionError(f"Forbidden exchange call: {name}"))
            monkeypatch.setattr(exchange, name, spy)
            forbidden.append(spy)
    network = Mock(side_effect=AssertionError("PAPER must work offline"))
    monkeypatch.setattr(socket.socket, "connect", network)
    monkeypatch.setattr(requests.Session, "request", network)
    broker = PaperBroker()
    broker.submit_order("buy", "0.01", "50000")
    broker.submit_order("sell", "0.01", "50000")
    with pytest.raises(ValueError):
        broker.submit_order("sell", "0.01", "50000")
    assert len(broker.snapshot("50000")["fills"]) == 2
    for spy in [*forbidden, network]:
        spy.assert_not_called()
