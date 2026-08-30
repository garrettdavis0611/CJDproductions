"""Core value objects passed between the data, screening, strategy and execution layers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(slots=True)
class TokenSnapshot:
    """A point-in-time view of one token/pair, normalised from DexScreener."""

    mint: str
    symbol: str = ""
    name: str = ""
    pair_address: str = ""
    dex: str = ""
    price_usd: float = 0.0
    liquidity_usd: float = 0.0
    fdv_usd: float = 0.0
    volume_m5: float = 0.0
    volume_h1: float = 0.0
    volume_h24: float = 0.0
    buys_m5: int = 0
    sells_m5: int = 0
    buys_h1: int = 0
    sells_h1: int = 0
    price_change_m5: float = 0.0
    price_change_h1: float = 0.0
    price_change_h6: float = 0.0
    price_change_h24: float = 0.0
    pair_created_at_ms: int = 0
    ts: float = field(default_factory=time.time)

    @property
    def age_minutes(self) -> float:
        if not self.pair_created_at_ms:
            return float("inf")
        return max(0.0, (self.ts - self.pair_created_at_ms / 1000.0) / 60.0)

    @property
    def vol_liq_ratio_h1(self) -> float:
        """Hourly turnover. Healthy interest is a moderate number; 50x+ smells like wash trading."""
        if self.liquidity_usd <= 0:
            return float("inf")
        return self.volume_h1 / self.liquidity_usd

    @property
    def buy_pressure_m5(self) -> float:
        """Share of 5-minute trades that were buys. 0.5 is balanced."""
        total = self.buys_m5 + self.sells_m5
        if total == 0:
            return 0.5
        return self.buys_m5 / total

    @classmethod
    def from_dexscreener_pair(cls, pair: dict[str, Any], now: float | None = None) -> "TokenSnapshot":
        base = pair.get("baseToken") or {}
        txns = pair.get("txns") or {}
        vol = pair.get("volume") or {}
        chg = pair.get("priceChange") or {}
        liq = pair.get("liquidity") or {}

        def _txn(window: str, kind: str) -> int:
            return int((txns.get(window) or {}).get(kind) or 0)

        def _f(source: dict[str, Any], key: str) -> float:
            try:
                return float(source.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        return cls(
            mint=base.get("address") or "",
            symbol=base.get("symbol") or "",
            name=base.get("name") or "",
            pair_address=pair.get("pairAddress") or "",
            dex=pair.get("dexId") or "",
            price_usd=_f(pair, "priceUsd"),
            liquidity_usd=_f(liq, "usd"),
            fdv_usd=_f(pair, "fdv"),
            volume_m5=_f(vol, "m5"),
            volume_h1=_f(vol, "h1"),
            volume_h24=_f(vol, "h24"),
            buys_m5=_txn("m5", "buys"),
            sells_m5=_txn("m5", "sells"),
            buys_h1=_txn("h1", "buys"),
            sells_h1=_txn("h1", "sells"),
            price_change_m5=_f(chg, "m5"),
            price_change_h1=_f(chg, "h1"),
            price_change_h6=_f(chg, "h6"),
            price_change_h24=_f(chg, "h24"),
            pair_created_at_ms=int(pair.get("pairCreatedAt") or 0),
            ts=now if now is not None else time.time(),
        )


@dataclass(slots=True)
class SafetyReport:
    """On-chain / third-party safety facts about a mint. `None` means "unknown"."""

    mint: str
    mint_authority_revoked: bool | None = None
    freeze_authority_revoked: bool | None = None
    lp_locked_pct: float | None = None
    top10_holder_pct: float | None = None
    rugcheck_score: float | None = None
    rugcheck_risks: list[str] = field(default_factory=list)
    sell_route_ok: bool | None = None
    sell_price_impact_bps: float | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScreenResult:
    mint: str
    passed: bool
    hard_fails: list[str] = field(default_factory=list)
    soft_flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def reason(self) -> str:
        if self.hard_fails:
            return "; ".join(self.hard_fails)
        if self.soft_flags:
            return "soft: " + "; ".join(self.soft_flags)
        return "clean"


@dataclass(slots=True)
class Signal:
    mint: str
    side: Side
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Fill:
    mint: str
    side: Side
    qty: float
    price_usd: float
    notional_usd: float
    fee_usd: float
    slippage_bps: float
    ts: float = field(default_factory=time.time)
    tx_signature: str | None = None
    simulated: bool = True


@dataclass(slots=True)
class Position:
    mint: str
    symbol: str
    qty: float
    entry_price_usd: float
    entry_ts: float
    cost_usd: float
    """Cash actually spent, fees included."""
    fees_paid_usd: float = 0.0
    peak_price_usd: float = 0.0
    entry_liquidity_usd: float = 0.0
    partial_tp_done: bool = False
    last_price_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.peak_price_usd <= 0:
            self.peak_price_usd = self.entry_price_usd
        if self.last_price_usd <= 0:
            self.last_price_usd = self.entry_price_usd

    def mark(self, price_usd: float) -> None:
        if price_usd > 0:
            self.last_price_usd = price_usd
            self.peak_price_usd = max(self.peak_price_usd, price_usd)

    @property
    def avg_cost_per_unit(self) -> float:
        return self.cost_usd / self.qty if self.qty else 0.0

    @property
    def unrealized_pnl_usd(self) -> float:
        """Gross of exit fees — exit costs are applied on the actual sell."""
        return self.qty * self.last_price_usd - self.cost_usd

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.cost_usd <= 0:
            return 0.0
        return self.unrealized_pnl_usd / self.cost_usd

    @property
    def drawdown_from_peak_pct(self) -> float:
        if self.peak_price_usd <= 0:
            return 0.0
        return (self.last_price_usd - self.peak_price_usd) / self.peak_price_usd

    def hold_minutes(self, now: float | None = None) -> float:
        return ((now if now is not None else time.time()) - self.entry_ts) / 60.0


@dataclass(slots=True)
class ClosedTrade:
    mint: str
    symbol: str
    qty: float
    entry_price_usd: float
    exit_price_usd: float
    entry_ts: float
    exit_ts: float
    cost_usd: float
    proceeds_usd: float
    fees_usd: float
    exit_reason: str

    @property
    def pnl_usd(self) -> float:
        return self.proceeds_usd - self.cost_usd

    @property
    def pnl_pct(self) -> float:
        if self.cost_usd <= 0:
            return 0.0
        return self.pnl_usd / self.cost_usd

    @property
    def hold_minutes(self) -> float:
        return (self.exit_ts - self.entry_ts) / 60.0
