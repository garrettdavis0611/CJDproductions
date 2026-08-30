"""Portfolio accounting: cash, positions, realised PnL, and an audit trail.

Every fill is appended to a JSONL trade log. If you ever want to know whether the
bot actually made money, that file — not a dashboard — is the answer.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import ClosedTrade, Fill, Position, Side

log = logging.getLogger(__name__)


@dataclass
class PortfolioStats:
    equity_usd: float
    cash_usd: float
    open_value_usd: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    fees_paid_usd: float
    trades: int
    wins: int
    losses: int
    open_positions: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def total_pnl_usd(self) -> float:
        return self.realized_pnl_usd + self.unrealized_pnl_usd


class Portfolio:
    def __init__(self, starting_cash_usd: float, trade_log_path: str | Path | None = None) -> None:
        self.starting_equity_usd = starting_cash_usd
        self.cash_usd = starting_cash_usd
        self.positions: dict[str, Position] = {}
        self.closed_trades: list[ClosedTrade] = []
        self.realized_pnl_usd = 0.0
        self.fees_paid_usd = 0.0
        self._log_path = Path(trade_log_path) if trade_log_path else None
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------- mutation

    def apply_buy(self, fill: Fill, symbol: str, entry_liquidity_usd: float = 0.0) -> Position:
        if fill.side is not Side.BUY:
            raise ValueError("apply_buy requires a BUY fill")
        cost = fill.qty * fill.price_usd + fill.fee_usd
        self.cash_usd -= cost
        self.fees_paid_usd += fill.fee_usd

        existing = self.positions.get(fill.mint)
        if existing is None:
            position = Position(
                mint=fill.mint,
                symbol=symbol,
                qty=fill.qty,
                entry_price_usd=fill.price_usd,
                entry_ts=fill.ts,
                cost_usd=cost,
                fees_paid_usd=fill.fee_usd,
                entry_liquidity_usd=entry_liquidity_usd,
            )
            self.positions[fill.mint] = position
        else:
            existing.qty += fill.qty
            existing.cost_usd += cost
            existing.fees_paid_usd += fill.fee_usd
            existing.entry_price_usd = existing.avg_cost_per_unit
            position = existing

        self._append_log("buy", fill, extra={"symbol": symbol})
        return position

    def apply_sell(self, fill: Fill, exit_reason: str) -> ClosedTrade | None:
        if fill.side is not Side.SELL:
            raise ValueError("apply_sell requires a SELL fill")
        position = self.positions.get(fill.mint)
        if position is None:
            raise KeyError(f"no open position in {fill.mint}")
        if fill.qty > position.qty + 1e-12:
            raise ValueError(f"cannot sell {fill.qty} of {position.qty} held")

        proceeds = fill.notional_usd  # already net of fees
        self.cash_usd += proceeds
        self.fees_paid_usd += fill.fee_usd

        fraction = fill.qty / position.qty if position.qty else 1.0
        cost_basis = position.cost_usd * fraction
        realized = proceeds - cost_basis
        self.realized_pnl_usd += realized

        trade = ClosedTrade(
            mint=position.mint,
            symbol=position.symbol,
            qty=fill.qty,
            entry_price_usd=position.entry_price_usd,
            exit_price_usd=fill.price_usd,
            entry_ts=position.entry_ts,
            exit_ts=fill.ts,
            cost_usd=cost_basis,
            proceeds_usd=proceeds,
            fees_usd=fill.fee_usd,
            exit_reason=exit_reason,
        )
        self.closed_trades.append(trade)

        position.qty -= fill.qty
        position.cost_usd -= cost_basis
        if position.qty <= 1e-12:
            del self.positions[fill.mint]
        else:
            position.partial_tp_done = True

        self._append_log("sell", fill, extra={"reason": exit_reason, "realized_usd": realized})
        return trade

    def mark(self, mint: str, price_usd: float) -> None:
        position = self.positions.get(mint)
        if position is not None:
            position.mark(price_usd)

    # ------------------------------------------------------------------ readouts

    @property
    def open_value_usd(self) -> float:
        return sum(p.qty * p.last_price_usd for p in self.positions.values())

    @property
    def open_cost_usd(self) -> float:
        return sum(p.cost_usd for p in self.positions.values())

    @property
    def equity_usd(self) -> float:
        return self.cash_usd + self.open_value_usd

    @property
    def unrealized_pnl_usd(self) -> float:
        return sum(p.unrealized_pnl_usd for p in self.positions.values())

    def stats(self) -> PortfolioStats:
        wins = sum(1 for t in self.closed_trades if t.pnl_usd > 0)
        return PortfolioStats(
            equity_usd=self.equity_usd,
            cash_usd=self.cash_usd,
            open_value_usd=self.open_value_usd,
            realized_pnl_usd=self.realized_pnl_usd,
            unrealized_pnl_usd=self.unrealized_pnl_usd,
            fees_paid_usd=self.fees_paid_usd,
            trades=len(self.closed_trades),
            wins=wins,
            losses=len(self.closed_trades) - wins,
            open_positions=len(self.positions),
        )

    def performance_summary(self) -> dict[str, float | int | str]:
        stats = self.stats()
        pnls = [t.pnl_usd for t in self.closed_trades]
        gross_win = sum(p for p in pnls if p > 0)
        gross_loss = -sum(p for p in pnls if p < 0)
        returns = [t.pnl_pct for t in self.closed_trades]
        mean = sum(returns) / len(returns) if returns else 0.0
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1) if len(returns) > 1 else 0.0

        return {
            "starting_equity_usd": round(self.starting_equity_usd, 2),
            "equity_usd": round(stats.equity_usd, 2),
            "total_return_pct": round(
                (stats.equity_usd / self.starting_equity_usd - 1.0) * 100.0
                if self.starting_equity_usd
                else 0.0,
                2,
            ),
            "realized_pnl_usd": round(stats.realized_pnl_usd, 2),
            "unrealized_pnl_usd": round(stats.unrealized_pnl_usd, 2),
            "fees_paid_usd": round(stats.fees_paid_usd, 2),
            "fees_as_pct_of_starting_equity": round(
                stats.fees_paid_usd / self.starting_equity_usd * 100.0 if self.starting_equity_usd else 0.0, 2
            ),
            "trades": stats.trades,
            "win_rate_pct": round(stats.win_rate * 100.0, 1),
            "avg_trade_return_pct": round(mean * 100.0, 2),
            "return_stdev_pct": round(math.sqrt(variance) * 100.0, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else "n/a",
            "max_drawdown_pct": round(self.max_drawdown_pct() * 100.0, 2),
            "open_positions": stats.open_positions,
        }

    def max_drawdown_pct(self) -> float:
        """Worst peak-to-trough decline of the realised equity curve."""
        equity = self.starting_equity_usd
        peak = equity
        worst = 0.0
        for trade in sorted(self.closed_trades, key=lambda t: t.exit_ts):
            equity += trade.pnl_usd
            peak = max(peak, equity)
            if peak > 0:
                worst = min(worst, (equity - peak) / peak)
        return abs(worst)

    # ---------------------------------------------------------------- audit log

    def _append_log(self, event: str, fill: Fill, extra: dict | None = None) -> None:
        if self._log_path is None:
            return
        record = {"event": event, **{k: v for k, v in asdict(fill).items()}, **(extra or {})}
        record["side"] = fill.side.value
        record["cash_usd_after"] = round(self.cash_usd, 6)
        record["equity_usd_after"] = round(self.equity_usd, 6)
        try:
            with self._log_path.open("a") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except OSError as exc:
            log.warning("could not write trade log: %s", exc)
