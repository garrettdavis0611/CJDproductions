"""Early-momentum continuation strategy.

Thesis, stated plainly so it can be falsified: among tokens that have already
survived a rug screen and are 30 minutes to 3 days old, those showing *accelerating*
buy-side flow on rising liquidity continue for long enough to clear round-trip costs
more often than a coin flip would suggest.

That is a hypothesis, not a fact. `memebot backtest` exists to test it on recorded
data before you fund it. If the backtest is not clearly profitable *after* the cost
model, the correct action is to not trade it.

Exit logic does the real work:
  * hard stop      — caps the loss on any single trade
  * trailing stop  — lets a winner run but banks the move
  * partial TP     — takes half off at target, removing the "round-tripped a 2x" failure
  * time stop      — meme momentum decays; dead money is still risk
  * liquidity exit — pool draining is the earliest rug signal available to us
"""

from __future__ import annotations

from typing import Sequence

from ..config import StrategyConfig
from ..models import Position, Side, Signal, TokenSnapshot
from .base import ExitDecision


class MomentumStrategy:
    name = "early-momentum"

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------- entries

    def entry_signal(self, history: Sequence[TokenSnapshot]) -> Signal | None:
        if not history:
            return None
        snapshot = history[-1]
        cfg = self.config
        reasons: list[str] = []
        blockers: list[str] = []

        m5, h1 = snapshot.price_change_m5, snapshot.price_change_h1
        if not cfg.min_price_change_m5 <= m5 <= cfg.max_price_change_m5:
            blockers.append(f"5m change {m5:+.1f}% outside [{cfg.min_price_change_m5}, {cfg.max_price_change_m5}]")
        else:
            reasons.append(f"5m {m5:+.1f}%")

        if not cfg.min_price_change_h1 <= h1 <= cfg.max_price_change_h1:
            blockers.append(f"1h change {h1:+.1f}% outside [{cfg.min_price_change_h1}, {cfg.max_price_change_h1}]")
        else:
            reasons.append(f"1h {h1:+.1f}%")

        pressure = snapshot.buy_pressure_m5
        if pressure < cfg.min_buy_pressure_m5:
            blockers.append(f"buy pressure {pressure:.0%} < {cfg.min_buy_pressure_m5:.0%}")
        else:
            reasons.append(f"buy pressure {pressure:.0%}")

        trades = snapshot.buys_m5 + snapshot.sells_m5
        if trades < cfg.min_trades_m5:
            blockers.append(f"only {trades} trades in 5m (< {cfg.min_trades_m5})")
        else:
            reasons.append(f"{trades} trades/5m")

        trend = liquidity_trend_pct(history)
        if trend is not None:
            if trend < cfg.min_liquidity_trend_pct:
                blockers.append(f"liquidity {trend:+.1f}% (< {cfg.min_liquidity_trend_pct}%)")
            else:
                reasons.append(f"liquidity {trend:+.1f}%")

        if blockers:
            return None

        score = self._score(snapshot, trend)
        if score < cfg.min_score:
            return None
        return Signal(mint=snapshot.mint, side=Side.BUY, score=score, reasons=reasons)

    def _score(self, snapshot: TokenSnapshot, liquidity_trend: float | None) -> float:
        """Weighted 0-1 conviction score. Components are each clamped to [0, 1]."""
        cfg = self.config

        def band(value: float, low: float, high: float) -> float:
            if high <= low:
                return 0.0
            return min(1.0, max(0.0, (value - low) / (high - low)))

        momentum_5m = band(snapshot.price_change_m5, cfg.min_price_change_m5, cfg.min_price_change_m5 + 15.0)
        # Prefer early in the 1h move, not late: peak score at ~2x the entry floor.
        h1_sweet = cfg.min_price_change_h1 * 6.0
        momentum_1h = 1.0 - band(abs(snapshot.price_change_h1 - h1_sweet), 0.0, h1_sweet * 3.0)
        pressure = band(snapshot.buy_pressure_m5, cfg.min_buy_pressure_m5, 0.80)
        activity = band(float(snapshot.buys_m5 + snapshot.sells_m5), float(cfg.min_trades_m5), float(cfg.min_trades_m5) * 5.0)
        turnover = band(snapshot.vol_liq_ratio_h1, 0.3, 6.0)
        liquidity = 0.5 if liquidity_trend is None else band(liquidity_trend, -5.0, 15.0)

        return (
            0.25 * momentum_5m
            + 0.20 * momentum_1h
            + 0.20 * pressure
            + 0.15 * activity
            + 0.10 * turnover
            + 0.10 * liquidity
        )

    # -------------------------------------------------------------------- exits

    def exit_decision(
        self, position: Position, history: Sequence[TokenSnapshot], now: float
    ) -> ExitDecision:
        cfg = self.config
        latest = history[-1] if history else None

        # 1. Liquidity drain beats everything else — this is the rug in progress.
        if latest is not None and position.entry_liquidity_usd > 0 and latest.liquidity_usd >= 0:
            drop = 1.0 - (latest.liquidity_usd / position.entry_liquidity_usd)
            if drop >= cfg.liquidity_drain_exit_pct:
                return ExitDecision(True, f"liquidity drained {drop:.0%} since entry", 1.0)

        pnl = position.unrealized_pnl_pct

        # 2. Hard stop.
        if pnl <= -cfg.stop_loss_pct:
            return ExitDecision(True, f"stop loss hit ({pnl:+.1%})", 1.0)

        # 3. Trailing stop, armed only once we are in profit so it cannot pre-empt
        #    the hard stop on a position that never worked.
        if position.peak_price_usd > position.entry_price_usd:
            drawdown = position.drawdown_from_peak_pct
            if drawdown <= -cfg.trailing_stop_pct:
                return ExitDecision(True, f"trailing stop ({drawdown:+.1%} off peak)", 1.0)

        # 4. Partial take-profit, once.
        if not position.partial_tp_done and pnl >= cfg.take_profit_pct:
            fraction = cfg.partial_take_profit_fraction
            if fraction >= 1.0:
                return ExitDecision(True, f"take profit ({pnl:+.1%})", 1.0)
            if fraction > 0:
                return ExitDecision(True, f"partial take profit ({pnl:+.1%})", fraction)

        # 5. Time stop.
        if position.hold_minutes(now) >= cfg.max_hold_minutes:
            return ExitDecision(True, f"max hold {cfg.max_hold_minutes:.0f}m reached ({pnl:+.1%})", 1.0)

        return ExitDecision(False)


def liquidity_trend_pct(history: Sequence[TokenSnapshot]) -> float | None:
    """Percent change in liquidity between the first and last observation we hold."""
    if len(history) < 2:
        return None
    first, last = history[0].liquidity_usd, history[-1].liquidity_usd
    if first <= 0:
        return None
    return (last - first) / first * 100.0
