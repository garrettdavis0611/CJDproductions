"""Copy-trade strategy: enter on wallet consensus, exit on the ladder plus wallet exits.

Why this is a different bet from momentum: it does not try to predict price from
price. The signal is that several people with a demonstrated, luck-filtered record
just bought the same thing, recently, at a price we can still get. Whether that
predicts returns is still an empirical question — but it is not the same crowded
question the momentum strategy was losing.

Exits reuse the momentum ladder, which is sound and independent of why we entered,
and add the strongest signal available in this design: **the wallets we followed are
selling.** They have our information plus whatever qualified them.
"""

from __future__ import annotations

import logging
from typing import Sequence

from ..config import SmartMoneyConfig, StrategyConfig
from ..models import Position, Side, Signal, TokenSnapshot
from ..smartmoney.tracker import SmartMoneyTracker
from .base import ExitDecision
from .momentum import MomentumStrategy

log = logging.getLogger(__name__)


class CopyTradeStrategy:
    name = "smart-money-copy"

    def __init__(
        self,
        strategy_config: StrategyConfig,
        smart_money_config: SmartMoneyConfig,
        tracker: SmartMoneyTracker,
    ) -> None:
        self.config = strategy_config
        self.smart_money = smart_money_config
        self.tracker = tracker
        # Composed rather than subclassed: the exit ladder is reused verbatim, and
        # nothing about entering on wallet consensus should be able to change it.
        self._ladder = MomentumStrategy(strategy_config)
        self.last_signal_wallets: dict[str, list[str]] = {}

    # ------------------------------------------------------------------- entries

    def entry_signal(self, history: Sequence[TokenSnapshot]) -> Signal | None:
        if not history:
            return None
        snapshot = history[-1]
        consensus = self.tracker.consensus(snapshot.mint, snapshot.price_usd, now=snapshot.ts)
        if consensus is None:
            return None
        if not consensus.accepted:
            log.debug("no copy signal for %s: %s", snapshot.symbol or snapshot.mint[:8], consensus.reason)
            return None

        # More independent wallets and less drift both raise conviction.
        cfg = self.smart_money
        wallet_component = min(
            1.0, consensus.wallet_count / max(1.0, cfg.min_wallets_consensus * 2.0)
        )
        freshness = 1.0 - min(1.0, consensus.age_seconds / max(1.0, cfg.max_signal_age_seconds))
        drift_room = 1.0 - min(1.0, max(0.0, consensus.drift_pct) / max(1e-9, cfg.max_price_drift_pct))
        score = 0.45 * wallet_component + 0.30 * freshness + 0.25 * drift_room

        self.last_signal_wallets[snapshot.mint] = list(consensus.wallets)
        return Signal(
            mint=snapshot.mint,
            side=Side.BUY,
            score=score,
            reasons=[
                f"{consensus.wallet_count} tracked wallets bought",
                f"{consensus.age_seconds:.0f}s old",
                f"{consensus.drift_pct:+.1f}% vs their entry",
            ],
        )

    def wallets_for(self, mint: str) -> list[str]:
        """Which wallets triggered the open signal, for attribution on exit."""
        return self.last_signal_wallets.get(mint, [])

    # -------------------------------------------------------------------- exits

    def exit_decision(
        self, position: Position, history: Sequence[TokenSnapshot], now: float
    ) -> ExitDecision:
        # Smart money leaving outranks everything except the pool itself draining,
        # which the ladder checks first.
        ladder = self._ladder.exit_decision(position, history, now)
        if ladder.should_exit and "liquidity drained" in ladder.reason:
            return ladder

        if self.smart_money.exit_on_wallet_exit:
            sellers, wallets = self.tracker.exit_pressure(position.mint, now=now)
            if sellers >= self.smart_money.min_wallets_selling:
                names = ", ".join(w[:8] for w in wallets[:3])
                return ExitDecision(
                    True, f"{sellers} tracked wallet(s) sold ({names})", 1.0
                )

        return ladder
