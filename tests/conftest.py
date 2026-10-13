from pathlib import Path
import sys

import pytest


def pytest_addoption(parser):
    parser.addoption("--simulate-gbk", action="store_true", default=False,
                     help="Use GBK for Path.open text calls that omit an encoding")


def pytest_configure(config):
    if config.getoption("--simulate-gbk") and sys.flags.utf8_mode:
        raise pytest.UsageError("Run GBK simulation with: python -X utf8=0 -m pytest --simulate-gbk")


@pytest.fixture(autouse=True)
def simulate_gbk_default_text_encoding(request, monkeypatch):
    if not request.config.getoption("--simulate-gbk"):
        return
    original_open = Path.open

    def gbk_open(path, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if "b" not in mode and encoding in (None, "locale"):
            encoding = "gbk"
        return original_open(path, mode=mode, buffering=buffering, encoding=encoding,
                             errors=errors, newline=newline)

    monkeypatch.setattr(Path, "open", gbk_open)


@pytest.fixture(autouse=True)
def clean_safety_environment(monkeypatch):
    for name in (
        "QT_MODE", "QT_LIVE_ORDERS_ENABLED", "QT_WITHDRAWALS_ENABLED",
        "QT_LEVERAGE_ENABLED", "QT_TRANSFERS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
