"""Value objects for wallet analysis.

All profit and loss is measured in **SOL**, not USD. Reconstructing historical USD
values would need a price oracle for every timestamp, and getting that subtly wrong
is how you convince yourself a mediocre wallet is a good one. SOL-denominated PnL is
what the wallet actually earned and needs no external data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class WalletSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(slots=True)
class WalletTrade:
    """One reconstructed swap by one wallet."""

    wallet: str
    mint: str
    side: WalletSide
    token_amount: float
    sol_amount: float
    """SOL spent (buy) or received (sell). Always positive."""
    ts: float
    signature: str = ""

    @property
    def price_sol(self) -> float:
        return self.sol_amount / self.token_amount if self.token_amount else 0.0


@dataclass(slots=True)
class PositionEpisode:
    """One complete round trip: position went from zero, up, and back to zero.

    Episodes are the honest unit of account for a wallet's skill. Counting
    individual fills would let a wallet that scaled into one winner look like it won
    twenty separate times.
    """

    mint: str
    cost_sol: float
    proceeds_sol: float
    entry_ts: float
    exit_ts: float
    closed: bool

    @property
    def pnl_sol(self) -> float:
        return self.proceeds_sol - self.cost_sol

    @property
    def pnl_ratio(self) -> float:
        return self.pnl_sol / self.cost_sol if self.cost_sol > 0 else 0.0

    @property
    def hold_minutes(self) -> float:
        return max(0.0, (self.exit_ts - self.entry_ts) / 60.0)


@dataclass
class WalletStats:
    wallet: str
    episodes: list[PositionEpisode] = field(default_factory=list)
    closed_episodes: int = 0
    distinct_tokens: int = 0
    realized_pnl_sol: float = 0.0
    win_rate: float = 0.0
    median_hold_minutes: float = 0.0
    best_token_profit_share: float = 0.0
    """Share of gross profit from the single best token. High means one lucky hit."""
    active_days: int = 0
    trades_analysed: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    score: float = 0.0
    qualified: bool = False
    disqualifiers: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "wallet": self.wallet,
            "qualified": self.qualified,
            "score": round(self.score, 3),
            "closed_episodes": self.closed_episodes,
            "distinct_tokens": self.distinct_tokens,
            "realized_pnl_sol": round(self.realized_pnl_sol, 3),
            "win_rate_pct": round(self.win_rate * 100.0, 1),
            "median_hold_minutes": round(self.median_hold_minutes, 1),
            "best_token_profit_share_pct": round(self.best_token_profit_share * 100.0, 1),
            "active_days": self.active_days,
            "trades_analysed": self.trades_analysed,
            "disqualifiers": list(self.disqualifiers),
        }


@dataclass
class WalletAttribution:
    """How our own copied trades of this wallet actually turned out.

    This is the only defence against a wallet that is farming its followers: you
    cannot detect the intent up front, but you can measure the result and stop.
    """

    wallet: str
    copied_trades: int = 0
    realized_pnl_usd: float = 0.0
    wins: int = 0
    demoted: bool = False
    demoted_reason: str = ""

    @property
    def win_rate(self) -> float:
        return self.wins / self.copied_trades if self.copied_trades else 0.0

    @property
    def avg_pnl_usd(self) -> float:
        return self.realized_pnl_usd / self.copied_trades if self.copied_trades else 0.0
