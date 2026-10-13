import pytest


@pytest.fixture(autouse=True)
def clean_safety_environment(monkeypatch):
    for name in (
        "QT_MODE", "QT_LIVE_ORDERS_ENABLED", "QT_WITHDRAWALS_ENABLED",
        "QT_LEVERAGE_ENABLED", "QT_TRANSFERS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
