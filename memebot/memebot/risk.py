"""Risk management: position sizing and the circuit breakers that keep a bad day
from becoming a terminal one.

This module is deliberately conservative and deliberately boring. Over a long
enough horizon it matters far more than the strategy does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import RiskConfig

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SizingDecision:
    allowed: bool
    notional_usd: float = 0.0
    reason: str = ""


@dataclass
class RiskState:
    day: str = ""
    realized_pnl_today_usd: float = 0.0
    consecutive_losses: int = 0
    halted_reason: str = ""
    halted_until: float = 0.0
    """Unix timestamp when a timed halt expires. 0 means "until manually resumed"."""
    last_entry_ts: float = 0.0
    cooldowns: dict[str, float] = field(default_factory=dict)
    """mint -> unix timestamp before which we will not re-enter."""


class RiskManager:
    def __init__(self, config: RiskConfig, state: RiskState | None = None) -> None:
        self.config = config
        self.state = state or RiskState()

    # ------------------------------------------------------------------ day roll

    @staticmethod
    def _utc_day(now: float) -> str:
        return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")

    def roll_day_if_needed(self, now: float) -> bool:
        """Reset the daily loss counter at UTC midnight. Returns True if it rolled."""
        today = self._utc_day(now)
        if self.state.day == today:
            return False
        if self.state.day:
            log.info(
                "UTC day rolled %s -> %s; resetting daily loss limit (was $%.2f)",
                self.state.day, today, self.state.realized_pnl_today_usd,
            )
        self.state.day = today
        self.state.realized_pnl_today_usd = 0.0
        # A daily-loss halt clears with the day. A consecutive-loss pause does not:
        # that one is about the strategy being wrong, not about the calendar, and it
        # expires on its own clock.
        if self.state.halted_reason.startswith("daily loss"):
            self.state.halted_reason = ""
            self.state.halted_until = 0.0
        return True

    # ------------------------------------------------------------------- gating

    def is_halted(self, now: float | None = None) -> bool:
        if not self.state.halted_reason:
            return False
        if self.state.halted_until and now is not None and now >= self.state.halted_until:
            log.info("halt expired (%s); resuming entries", self.state.halted_reason)
            self.resume()
            return False
        return True

    def halt(self, reason: str, until: float = 0.0) -> None:
        if not self.state.halted_reason:
            log.error("TRADING HALTED: %s", reason)
        self.state.halted_reason = reason
        self.state.halted_until = until

    def resume(self) -> None:
        self.state.halted_reason = ""
        self.state.halted_until = 0.0
        self.state.consecutive_losses = 0

    def can_open(
        self,
        mint: str,
        equity_usd: float,
        open_positions: int,
        open_exposure_usd: float,
        now: float,
    ) -> SizingDecision:
        cfg = self.config
        self.roll_day_if_needed(now)

        if self.is_halted(now):
            return SizingDecision(False, reason=f"halted: {self.state.halted_reason}")

        if equity_usd <= 0:
            return SizingDecision(False, reason="no equity")

        loss_limit = cfg.max_daily_loss_fraction * equity_usd
        if -self.state.realized_pnl_today_usd >= loss_limit:
            self.halt(
                f"daily loss limit reached (${-self.state.realized_pnl_today_usd:,.2f} "
                f">= ${loss_limit:,.2f})"
            )
            return SizingDecision(False, reason=f"halted: {self.state.halted_reason}")

        if self.state.consecutive_losses >= cfg.max_consecutive_losses:
            pause = cfg.consecutive_loss_pause_minutes
            if pause > 0:
                self.halt(
                    f"{self.state.consecutive_losses} consecutive losses "
                    f"(paused {pause:.0f}m)",
                    until=now + pause * 60.0,
                )
            else:
                self.halt(f"{self.state.consecutive_losses} consecutive losses")
            return SizingDecision(False, reason=f"halted: {self.state.halted_reason}")

        if open_positions >= cfg.max_concurrent_positions:
            return SizingDecision(False, reason=f"at position cap ({cfg.max_concurrent_positions})")

        cooldown_until = self.state.cooldowns.get(mint, 0.0)
        if now < cooldown_until:
            remaining = (cooldown_until - now) / 60.0
            return SizingDecision(False, reason=f"cooldown on {mint[:8]} for {remaining:.0f}m")

        since_last = now - self.state.last_entry_ts
        if self.state.last_entry_ts and since_last < cfg.min_seconds_between_entries:
            return SizingDecision(
                False,
                reason=f"entry throttle ({since_last:.0f}s < {cfg.min_seconds_between_entries:.0f}s)",
            )

        exposure_cap = cfg.max_total_exposure_fraction * equity_usd
        exposure_room = exposure_cap - open_exposure_usd
        if exposure_room <= 0:
            return SizingDecision(
                False, reason=f"exposure cap reached (${open_exposure_usd:,.2f} / ${exposure_cap:,.2f})"
            )

        notional = self.position_size(equity_usd)
        notional = min(notional, exposure_room)

        if notional < cfg.min_position_usd:
            return SizingDecision(
                False,
                reason=f"sized ${notional:,.2f} below minimum ${cfg.min_position_usd:,.2f}",
            )
        return SizingDecision(True, notional_usd=notional)

    def position_size(self, equity_usd: float, stop_loss_pct: float | None = None) -> float:
        """Fixed-fractional sizing: risk `risk_fraction_per_trade` of equity per trade.

        With a stop, position = (equity * risk) / stop_distance, so a tighter stop
        buys a larger position for the same dollar risk. Always clamped by
        max_position_usd — the cap, not the formula, is what saves you.
        """
        cfg = self.config
        dollars_at_risk = equity_usd * cfg.risk_fraction_per_trade
        if stop_loss_pct and stop_loss_pct > 0:
            notional = dollars_at_risk / stop_loss_pct
        else:
            notional = dollars_at_risk
        return max(0.0, min(notional, cfg.max_position_usd, equity_usd))

    # ------------------------------------------------------------- bookkeeping

    def record_entry(self, now: float) -> None:
        self.state.last_entry_ts = now

    def record_exit(self, mint: str, realized_pnl_usd: float, now: float, full_exit: bool) -> None:
        self.roll_day_if_needed(now)
        self.state.realized_pnl_today_usd += realized_pnl_usd
        if not full_exit:
            return
        self.state.cooldowns[mint] = now + self.config.cooldown_minutes_per_mint * 60.0
        if realized_pnl_usd < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
