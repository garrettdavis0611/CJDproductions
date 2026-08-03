"""Strategy interface.

A strategy sees a token's snapshot history and decides whether to enter, and — for
open positions — whether to exit. It never sizes trades (that is RiskManager) and
never talks to a broker (that is the engine). Swap strategies freely; the safety
gauntlet and risk limits apply regardless.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ..models import Position, Signal, TokenSnapshot


@dataclass(slots=True)
class ExitDecision:
    should_exit: bool
    reason: str = ""
    fraction: float = 1.0
    """Fraction of the position to sell, in (0, 1]."""


class Strategy(Protocol):
    name: str

    def entry_signal(self, history: Sequence[TokenSnapshot]) -> Signal | None:
        """Return a BUY signal, or None to pass."""

    def exit_decision(
        self, position: Position, history: Sequence[TokenSnapshot], now: float
    ) -> ExitDecision:
        """Decide whether to close (or partially close) an open position."""
