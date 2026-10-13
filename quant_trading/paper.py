"""Single-asset spot simulation, deliberately independent of exchange clients."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext

from quant_trading.config import SafetyPolicy


def number(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}必须是有限数字。") from None
    if not result.is_finite():
        raise ValueError(f"{label}必须是有限数字。")
    if result == 0:
        return Decimal("0")
    if result.copy_abs() > Decimal("1e12") or result.as_tuple().exponent < -12:
        raise ValueError(f"{label}超出支持范围：绝对值不超过 1e12，最多 12 位小数。")
    return result


@dataclass(frozen=True)
class Fill:
    sequence: int
    timestamp: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal

    def to_dict(self) -> dict:
        return {key: str(value) if isinstance(value, Decimal) else value
                for key, value in asdict(self).items()}


class PaperBroker:
    """Local BTC/USDT demo; no network, credentials, shorts or leverage."""

    def __init__(
        self, initial_cash: object = "10000", *,
        fee_rate: object = "0.001", slippage_rate: object = "0.0005",
        max_position_fraction: object = "0.25", policy: SafetyPolicy | None = None,
    ) -> None:
        self._policy = policy if policy is not None else SafetyPolicy.from_environment()
        self._policy.validate()
        self._cash = number(initial_cash, "初始资金")
        self._fee_rate = number(fee_rate, "手续费率")
        self._slippage_rate = number(slippage_rate, "滑点率")
        self._max_position_fraction = number(max_position_fraction, "仓位上限")
        if self._cash <= 0:
            raise ValueError("初始资金必须大于零。")
        if not (0 <= self._fee_rate < 1 and 0 <= self._slippage_rate < 1):
            raise ValueError("手续费率和滑点率必须在 [0, 1) 范围。")
        if not 0 < self._max_position_fraction <= 1:
            raise ValueError("仓位上限必须在 (0, 1] 范围。")
        self._initial_cash = self._cash
        self._quantity = Decimal("0")
        self._fills: list[Fill] = []

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def quantity(self) -> Decimal:
        return self._quantity

    @property
    def fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills)

    def equity(self, market_price: object) -> Decimal:
        price = number(market_price, "价格")
        if price <= 0:
            raise ValueError("价格必须大于零。")
        with localcontext(prec=80) as context:
            context.traps[Inexact] = True
            return self._cash + self._quantity * price

    def submit_order(self, side: str, quantity: object, market_price: object) -> Fill:
        # Bounded inputs + local precision prevent silent loss of very small costs.
        with localcontext(prec=80) as context:
            context.traps[Inexact] = True
            return self._fill_order(side, quantity, market_price)

    def _fill_order(self, side: str, quantity: object, market_price: object) -> Fill:
        self._policy.validate()
        if side not in {"buy", "sell"}:
            raise ValueError("只支持 buy 或 sell。")
        amount, mark = number(quantity, "数量"), number(market_price, "价格")
        if amount <= 0 or mark <= 0:
            raise ValueError("数量和价格必须大于零。")
        direction = Decimal("1") if side == "buy" else Decimal("-1")
        price = mark * (1 + direction * self._slippage_rate)
        notional = amount * price
        fee = notional * self._fee_rate
        if side == "buy":
            cash = self._cash - notional - fee
            units = self._quantity + amount
            if cash < 0:
                raise ValueError("虚拟资金不足（含手续费和滑点）。")
            # Use post-fill equity marked at the supplied price, including trading costs.
            if units * mark > (cash + units * mark) * self._max_position_fraction:
                raise ValueError("买入后超过仓位上限。")
        else:
            if amount > self._quantity:
                raise ValueError("卖出数量超过持仓，禁止做空。")
            cash, units = self._cash + notional - fee, self._quantity - amount
        fill = Fill(len(self._fills) + 1, datetime.now(timezone.utc).isoformat(),
                    side, amount, price, fee)
        self._cash, self._quantity = cash, units
        self._fills.append(fill)
        return fill

    def snapshot(self, market_price: object) -> dict:
        return {
            "schema_version": 1, "mode": "PAPER", "symbol": "BTC/USDT",
            "price_source": "offline_demo", "market_price": str(number(market_price, "价格")),
            "initial_cash": str(self._initial_cash), "cash": str(self.cash),
            "quantity": str(self.quantity), "equity": str(self.equity(market_price)),
            "fee_rate": str(self._fee_rate), "slippage_rate": str(self._slippage_rate),
            "max_position_fraction": str(self._max_position_fraction),
            "fills": [fill.to_dict() for fill in self.fills],
        }
