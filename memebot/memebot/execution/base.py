"""Broker interface and the shared cost model.

The cost model is the most important honest thing in this codebase. A backtest or
paper run that ignores DEX fees, priority fees, and slippage will show a profit that
does not exist. Both brokers use the same arithmetic so paper results and live
results are comparable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..config import CostConfig
from ..models import Fill, Side


class OrderRejected(Exception):
    """The broker declined the order (no route, size clamp, insufficient balance)."""


class OrderFailed(Exception):
    """The order was attempted and did not land. Fees may still have been paid."""


@dataclass(slots=True)
class CostBreakdown:
    dex_fee_usd: float
    platform_fee_usd: float
    network_fee_usd: float
    slippage_bps: float
    effective_price_usd: float

    @property
    def total_fee_usd(self) -> float:
        return self.dex_fee_usd + self.platform_fee_usd + self.network_fee_usd


class CostModel:
    def __init__(self, config: CostConfig) -> None:
        self.config = config

    def network_fee_usd(self, sol_price_usd: float | None = None) -> float:
        cfg = self.config
        lamports = cfg.priority_fee_lamports + cfg.base_tx_fee_lamports
        price = sol_price_usd if sol_price_usd and sol_price_usd > 0 else cfg.sol_price_usd
        return lamports / 1e9 * price

    def apply(
        self,
        side: Side,
        quoted_price_usd: float,
        notional_usd: float,
        quoted_impact_bps: float = 0.0,
        sol_price_usd: float | None = None,
    ) -> CostBreakdown:
        """Slippage always moves against us: we pay up to buy and down to sell."""
        cfg = self.config
        slippage_bps = max(0.0, quoted_impact_bps) + cfg.extra_slippage_bps
        drift = slippage_bps / 10_000.0
        if side is Side.BUY:
            effective_price = quoted_price_usd * (1.0 + drift)
        else:
            effective_price = quoted_price_usd * (1.0 - drift)
        effective_price = max(effective_price, 0.0)

        return CostBreakdown(
            dex_fee_usd=notional_usd * cfg.dex_fee_bps / 10_000.0,
            platform_fee_usd=notional_usd * cfg.jupiter_platform_fee_bps / 10_000.0,
            network_fee_usd=self.network_fee_usd(sol_price_usd),
            slippage_bps=slippage_bps,
            effective_price_usd=effective_price,
        )


class Broker(Protocol):
    """Both PaperBroker and JupiterBroker satisfy this."""

    simulated: bool

    def buy(self, mint: str, notional_usd: float, quoted_price_usd: float, decimals: int) -> Fill:
        ...

    def sell(self, mint: str, qty: float, quoted_price_usd: float, decimals: int) -> Fill:
        ...
