"""Fail closed: unsupported modes and enabled money-operation flags stop startup."""

from dataclasses import dataclass
import os
from typing import Mapping


class SafetyError(ValueError):
    """An unsupported trading mode or operation was requested."""


@dataclass(frozen=True)
class SafetyPolicy:
    mode: str = "PAPER"
    live_orders_enabled: bool = False
    withdrawals_enabled: bool = False
    leverage_enabled: bool = False
    transfers_enabled: bool = False

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.mode != "PAPER":
            raise SafetyError("当前版本只允许 PAPER 模式。")
        for field in (
            "live_orders_enabled", "withdrawals_enabled",
            "leverage_enabled", "transfers_enabled",
        ):
            if getattr(self, field) is not False:
                raise SafetyError(f"当前版本禁止开启资金操作：{field}")

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "SafetyPolicy":
        values = os.environ if env is None else env
        flags = {}
        for field in (
            "live_orders_enabled", "withdrawals_enabled",
            "leverage_enabled", "transfers_enabled",
        ):
            name = "QT_" + field.upper()
            raw = values.get(name, "false").strip().lower()
            if raw not in {"false", "0"}:
                raise SafetyError(f"{name} 必须关闭（false 或 0）。")
            flags[field] = False
        return cls(mode=values.get("QT_MODE", "PAPER"), **flags)
