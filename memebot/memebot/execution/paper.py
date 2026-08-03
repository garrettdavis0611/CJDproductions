"""Paper broker — simulated fills with real costs.

Deliberately pessimistic, because an optimistic simulator is worse than none:
  * fees and slippage are charged on both legs
  * a configurable share of swaps fail, charging the network fee and filling nothing
    (exactly what a real congested Solana swap does)
  * price impact from the live quote is added on top of a fixed slippage allowance
"""

from __future__ import annotations

import logging
import random
import time

from ..config import CostConfig
from ..models import Fill, Side
from .base import CostModel, OrderFailed, OrderRejected

log = logging.getLogger(__name__)


class PaperBroker:
    simulated = True

    def __init__(
        self,
        config: CostConfig,
        rng: random.Random | None = None,
        clock=time.time,
    ) -> None:
        self.config = config
        self.costs = CostModel(config)
        self._rng = rng or random.Random()
        self._clock = clock
        self.fees_paid_usd = 0.0

    def _maybe_fail(self, mint: str, side: Side) -> None:
        if self._rng.random() >= self.config.failed_tx_probability:
            return
        fee = self.costs.network_fee_usd()
        self.fees_paid_usd += fee
        log.info("simulated %s on %s failed to land; burned $%.4f in fees", side.value, mint[:8], fee)
        raise OrderFailed(f"simulated tx failure ({side.value} {mint[:8]}), fee ${fee:.4f} lost")

    def buy(
        self,
        mint: str,
        notional_usd: float,
        quoted_price_usd: float,
        decimals: int = 9,
        quoted_impact_bps: float = 0.0,
    ) -> Fill:
        if quoted_price_usd <= 0:
            raise OrderRejected("no price")
        if notional_usd <= 0:
            raise OrderRejected("non-positive notional")
        self._maybe_fail(mint, Side.BUY)

        costs = self.costs.apply(Side.BUY, quoted_price_usd, notional_usd, quoted_impact_bps)
        spendable = notional_usd - costs.dex_fee_usd - costs.platform_fee_usd
        if spendable <= 0:
            raise OrderRejected("fees exceed order size")
        qty = spendable / costs.effective_price_usd
        total_fees = costs.total_fee_usd
        self.fees_paid_usd += total_fees

        return Fill(
            mint=mint,
            side=Side.BUY,
            qty=qty,
            price_usd=costs.effective_price_usd,
            notional_usd=notional_usd + costs.network_fee_usd,
            fee_usd=total_fees,
            slippage_bps=costs.slippage_bps,
            ts=self._clock(),
            simulated=True,
        )

    def sell(
        self,
        mint: str,
        qty: float,
        quoted_price_usd: float,
        decimals: int = 9,
        quoted_impact_bps: float = 0.0,
    ) -> Fill:
        if quoted_price_usd <= 0:
            raise OrderRejected("no price")
        if qty <= 0:
            raise OrderRejected("non-positive quantity")
        self._maybe_fail(mint, Side.SELL)

        gross = qty * quoted_price_usd
        costs = self.costs.apply(Side.SELL, quoted_price_usd, gross, quoted_impact_bps)
        proceeds_before_fees = qty * costs.effective_price_usd
        total_fees = (
            proceeds_before_fees * (self.config.dex_fee_bps + self.config.jupiter_platform_fee_bps) / 10_000.0
            + costs.network_fee_usd
        )
        net = proceeds_before_fees - total_fees
        self.fees_paid_usd += total_fees

        return Fill(
            mint=mint,
            side=Side.SELL,
            qty=qty,
            price_usd=costs.effective_price_usd,
            notional_usd=net,
            fee_usd=total_fees,
            slippage_bps=costs.slippage_bps,
            ts=self._clock(),
            simulated=True,
        )
