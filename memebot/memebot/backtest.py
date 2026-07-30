"""Replay recorded snapshots through the strategy, risk and cost model.

This is the only place in the project that can tell you whether the strategy has an
edge, and it can only answer for the data you recorded. Two honest caveats, stated
up front because they bound how much the number is worth:

  * Survivorship: we replay tokens the screener already accepted. Tokens that never
    appeared in your recording are absent, so results say nothing about coverage.
  * Fill realism: we replay observed prices, not the order book. Real slippage on a
    thin pool during a dump is worse than the model, sometimes much worse.

Treat a backtest that shows a small edge as noise. Only a large, stable edge that
survives the cost model is worth funding, and even then it is a hypothesis.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable, Sequence

from .config import Config
from .execution.base import OrderFailed, OrderRejected
from .execution.paper import PaperBroker
from .models import TokenSnapshot
from .portfolio import Portfolio
from .risk import RiskManager
from .strategy.momentum import MomentumStrategy

log = logging.getLogger(__name__)


def load_snapshots(path: str | Path) -> list[TokenSnapshot]:
    records: list[TokenSnapshot] = []
    fields = {f for f in TokenSnapshot.__slots__}
    with Path(path).open() as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                log.warning("%s:%d is not valid JSON, skipping", path, line_no)
                continue
            if not isinstance(raw, dict):
                continue
            records.append(TokenSnapshot(**{k: v for k, v in raw.items() if k in fields}))
    records.sort(key=lambda s: s.ts)
    return records


class BacktestResult:
    def __init__(self, portfolio: Portfolio, snapshots: int, mints: int, entries_blocked: int) -> None:
        self.portfolio = portfolio
        self.snapshots = snapshots
        self.mints = mints
        self.entries_blocked = entries_blocked

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {
            "snapshots_replayed": self.snapshots,
            "unique_mints": self.mints,
            "entries_blocked_by_risk": self.entries_blocked,
        }
        out.update(self.portfolio.performance_summary())
        return out


def run_backtest(
    config: Config,
    snapshots: Sequence[TokenSnapshot] | Iterable[TokenSnapshot],
    seed: int = 1234,
) -> BacktestResult:
    """Replay in timestamp order. Screening is assumed to have already passed —
    the recorded stream is what the live screener let through."""
    import random

    ordered = sorted(snapshots, key=lambda s: s.ts)
    if not ordered:
        raise ValueError("no snapshots to replay")

    portfolio = Portfolio(config.risk.starting_equity_usd)
    risk = RiskManager(config.risk)
    strategy = MomentumStrategy(config.strategy)
    broker = PaperBroker(config.costs, rng=random.Random(seed), clock=lambda: 0.0)

    history: dict[str, deque[TokenSnapshot]] = defaultdict(
        lambda: deque(maxlen=config.engine.snapshot_history)
    )
    entries_blocked = 0
    mints: set[str] = set()

    for snapshot in ordered:
        now = snapshot.ts
        mints.add(snapshot.mint)
        history[snapshot.mint].append(snapshot)
        risk.roll_day_if_needed(now)

        position = portfolio.positions.get(snapshot.mint)
        if position is not None:
            portfolio.mark(snapshot.mint, snapshot.price_usd)
            decision = strategy.exit_decision(position, list(history[snapshot.mint]), now)
            if decision.should_exit:
                qty = position.qty * min(1.0, max(0.0, decision.fraction))
                full = decision.fraction >= 1.0 - 1e-9
                try:
                    fill = broker.sell(snapshot.mint, qty, snapshot.price_usd)
                except (OrderRejected, OrderFailed):
                    continue
                fill.ts = now
                trade = portfolio.apply_sell(fill, exit_reason=decision.reason)
                if trade is not None:
                    risk.record_exit(snapshot.mint, trade.pnl_usd, now, full_exit=full)
            continue

        signal = strategy.entry_signal(list(history[snapshot.mint]))
        if signal is None:
            continue
        sizing = risk.can_open(
            mint=snapshot.mint,
            equity_usd=portfolio.equity_usd,
            open_positions=len(portfolio.positions),
            open_exposure_usd=portfolio.open_cost_usd,
            now=now,
        )
        if not sizing.allowed:
            entries_blocked += 1
            continue
        try:
            fill = broker.buy(snapshot.mint, sizing.notional_usd, snapshot.price_usd)
        except (OrderRejected, OrderFailed):
            continue
        fill.ts = now
        portfolio.apply_buy(fill, symbol=snapshot.symbol, entry_liquidity_usd=snapshot.liquidity_usd)
        risk.record_entry(now)

    # Mark remaining positions at their last seen price so equity is not overstated.
    for mint, position in portfolio.positions.items():
        bucket = history.get(mint)
        if bucket:
            portfolio.mark(mint, bucket[-1].price_usd)

    return BacktestResult(portfolio, len(ordered), len(mints), entries_blocked)
