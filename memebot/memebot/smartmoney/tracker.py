"""Follows qualified wallets and turns their activity into signals.

Three defences are built in, each answering a specific documented failure mode of
copy trading:

1. **Freshness.** You are always later than the wallet you copy. A signal older than
   `max_signal_age_seconds` is discarded, because the first seconds of a meme-coin
   move are frequently larger than the entire remainder of it.

2. **Price drift.** Even a fresh signal is refused if the price has already run more
   than `max_price_drift_pct` above the wallet's entry. Copying a wallet after the
   move is buying its top. This is the most important gate in the file.

3. **Attribution and demotion.** A wallet that knows it is followed can use follower
   buys as exit liquidity. That intent is undetectable up front, so instead we track
   the realised PnL of every trade we took *because of* each wallet, and stop
   following the ones that lose us money. Farming shows up in the results even when
   it is invisible in the signal.

Consensus across several independent wallets is required by default: one wallet
buying is an opinion, several buying within a short window is a signal.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import SmartMoneyConfig
from .models import WalletAttribution, WalletSide, WalletStats

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ObservedBuy:
    wallet: str
    mint: str
    ts: float
    price_usd: float


@dataclass(slots=True)
class ConsensusSignal:
    mint: str
    wallets: list[str]
    first_ts: float
    latest_ts: float
    avg_entry_price_usd: float
    age_seconds: float
    drift_pct: float
    accepted: bool
    reason: str = ""

    @property
    def wallet_count(self) -> int:
        return len(self.wallets)


@dataclass
class TrackerState:
    followed: dict[str, dict] = field(default_factory=dict)
    """wallet -> serialised WalletStats summary."""
    attribution: dict[str, dict] = field(default_factory=dict)
    """wallet -> serialised WalletAttribution."""


class SmartMoneyTracker:
    def __init__(
        self,
        config: SmartMoneyConfig,
        state_path: str | Path | None = None,
        clock=time.time,
    ) -> None:
        self.config = config
        self._clock = clock
        self._state_path = Path(state_path) if state_path else None
        self.followed: dict[str, WalletStats] = {}
        self.attribution: dict[str, WalletAttribution] = {}
        self._buys: list[ObservedBuy] = []
        self._sells: list[ObservedBuy] = []
        # mint -> wallets whose buys caused our position, for later attribution.
        self._credit: dict[str, list[str]] = {}

    # ------------------------------------------------------------------ registry

    def follow(self, stats: WalletStats) -> bool:
        if not stats.qualified:
            log.info("refusing to follow %s: %s", stats.wallet[:8], "; ".join(stats.disqualifiers))
            return False
        record = self.attribution.get(stats.wallet)
        if record is not None and record.demoted:
            log.info("refusing to re-follow demoted wallet %s: %s", stats.wallet[:8], record.demoted_reason)
            return False
        if len(self.followed) >= self.config.max_wallets_tracked and stats.wallet not in self.followed:
            weakest = min(self.followed.values(), key=lambda s: s.score)
            if weakest.score >= stats.score:
                return False
            log.info("dropping %s (score %.2f) for %s (%.2f)",
                     weakest.wallet[:8], weakest.score, stats.wallet[:8], stats.score)
            del self.followed[weakest.wallet]
        self.followed[stats.wallet] = stats
        self.attribution.setdefault(stats.wallet, WalletAttribution(wallet=stats.wallet))
        return True

    def unfollow(self, wallet: str) -> None:
        self.followed.pop(wallet, None)

    def is_following(self, wallet: str) -> bool:
        record = self.attribution.get(wallet)
        if record is not None and record.demoted:
            return False
        return wallet in self.followed

    def active_wallets(self) -> list[str]:
        return [w for w in self.followed if self.is_following(w)]

    # ----------------------------------------------------------------- observing

    def observe_trade(self, wallet: str, mint: str, side: WalletSide, ts: float, price_usd: float) -> None:
        if not self.is_following(wallet):
            return
        record = ObservedBuy(wallet=wallet, mint=mint, ts=ts, price_usd=price_usd)
        if side is WalletSide.BUY:
            self._buys.append(record)
        else:
            self._sells.append(record)
        self._prune(ts)

    def _prune(self, now: float) -> None:
        horizon = now - max(
            self.config.consensus_window_seconds, self.config.max_signal_age_seconds
        ) * 4.0
        self._buys = [b for b in self._buys if b.ts >= horizon]
        self._sells = [s for s in self._sells if s.ts >= horizon]

    # ------------------------------------------------------------------- signals

    def consensus(self, mint: str, current_price_usd: float, now: float | None = None) -> ConsensusSignal | None:
        """Do enough qualified wallets agree, recently enough, at a price we can still pay?"""
        now = self._clock() if now is None else now
        cfg = self.config
        window_start = now - cfg.consensus_window_seconds

        relevant = [
            b for b in self._buys
            if b.mint == mint and b.ts >= window_start and self.is_following(b.wallet)
        ]
        if not relevant:
            return None

        # One vote per wallet: a wallet scaling in must not manufacture consensus.
        by_wallet: dict[str, ObservedBuy] = {}
        for buy in relevant:
            existing = by_wallet.get(buy.wallet)
            if existing is None or buy.ts < existing.ts:
                by_wallet[buy.wallet] = buy

        buys = list(by_wallet.values())
        prices = [b.price_usd for b in buys if b.price_usd > 0]
        avg_entry = sum(prices) / len(prices) if prices else 0.0
        first_ts = min(b.ts for b in buys)
        latest_ts = max(b.ts for b in buys)
        age = now - latest_ts
        drift = ((current_price_usd / avg_entry - 1.0) * 100.0) if avg_entry > 0 else 0.0

        signal = ConsensusSignal(
            mint=mint,
            wallets=sorted(by_wallet),
            first_ts=first_ts,
            latest_ts=latest_ts,
            avg_entry_price_usd=avg_entry,
            age_seconds=age,
            drift_pct=drift,
            accepted=False,
        )

        if len(buys) < cfg.min_wallets_consensus:
            signal.reason = f"only {len(buys)} wallet(s), need {cfg.min_wallets_consensus}"
            return signal
        if age > cfg.max_signal_age_seconds:
            signal.reason = (
                f"signal is {age:.0f}s old (> {cfg.max_signal_age_seconds:.0f}s) — "
                "the move we would be copying has already happened"
            )
            return signal
        if avg_entry <= 0:
            signal.reason = "no reference entry price"
            return signal
        if drift > cfg.max_price_drift_pct:
            signal.reason = (
                f"price already +{drift:.1f}% above their entry "
                f"(> {cfg.max_price_drift_pct:.1f}%) — this would be buying their top"
            )
            return signal
        if drift < -cfg.max_adverse_drift_pct:
            signal.reason = (
                f"price {drift:.1f}% below their entry — they are already underwater, "
                "the thesis is not working"
            )
            return signal

        signal.accepted = True
        signal.reason = f"{len(buys)} wallets, {age:.0f}s old, {drift:+.1f}% drift"
        return signal

    def exit_pressure(self, mint: str, now: float | None = None) -> tuple[int, list[str]]:
        """How many followed wallets have sold this token recently.

        Smart money leaving is the strongest exit signal available: they have the same
        information we do plus whatever got them qualified in the first place.
        """
        now = self._clock() if now is None else now
        window_start = now - self.config.exit_window_seconds
        sellers = {
            s.wallet for s in self._sells
            if s.mint == mint and s.ts >= window_start and self.is_following(s.wallet)
        }
        return len(sellers), sorted(sellers)

    # -------------------------------------------------------------- attribution

    def credit_entry(self, mint: str, wallets: list[str]) -> None:
        """Remember which wallets we bought on behalf of, so the outcome lands on them."""
        self._credit[mint] = list(wallets)

    def record_outcome(self, mint: str, realized_pnl_usd: float) -> list[str]:
        """Attribute a closed trade back to the wallets that triggered it.

        Returns the wallets demoted as a result.
        """
        wallets = self._credit.pop(mint, [])
        if not wallets:
            return []
        cfg = self.config
        share = realized_pnl_usd / len(wallets)
        demoted: list[str] = []

        for wallet in wallets:
            record = self.attribution.setdefault(wallet, WalletAttribution(wallet=wallet))
            record.copied_trades += 1
            record.realized_pnl_usd += share
            if share > 0:
                record.wins += 1

            if record.demoted or record.copied_trades < cfg.min_attributed_trades:
                continue
            if record.realized_pnl_usd <= cfg.demote_below_pnl_usd:
                record.demoted = True
                record.demoted_reason = (
                    f"lost ${-record.realized_pnl_usd:.2f} over {record.copied_trades} "
                    "copied trades — following this wallet costs us money, whatever its "
                    "own record says"
                )
                demoted.append(wallet)
                log.warning("DEMOTED wallet %s: %s", wallet[:8], record.demoted_reason)
            elif record.win_rate < cfg.demote_below_win_rate:
                record.demoted = True
                record.demoted_reason = (
                    f"we won only {record.win_rate:.0%} of {record.copied_trades} copied "
                    f"trades (< {cfg.demote_below_win_rate:.0%})"
                )
                demoted.append(wallet)
                log.warning("DEMOTED wallet %s: %s", wallet[:8], record.demoted_reason)

        return demoted

    # ------------------------------------------------------------- persistence

    def save(self) -> None:
        if self._state_path is None:
            return
        state = TrackerState(
            followed={w: s.summary() for w, s in self.followed.items()},
            attribution={w: asdict(a) for w, a in self.attribution.items()},
        )
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(asdict(state), indent=2))
        except OSError as exc:
            log.warning("could not persist tracker state: %s", exc)

    def load(self) -> None:
        """Restore attribution and demotions across restarts.

        Demotions in particular must survive: forgetting which wallets cost us money
        every time the process restarts would make the defence useless.
        """
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("could not read tracker state: %s", exc)
            return

        for wallet, record in (raw.get("attribution") or {}).items():
            self.attribution[wallet] = WalletAttribution(
                wallet=wallet,
                copied_trades=int(record.get("copied_trades") or 0),
                realized_pnl_usd=float(record.get("realized_pnl_usd") or 0.0),
                wins=int(record.get("wins") or 0),
                demoted=bool(record.get("demoted")),
                demoted_reason=str(record.get("demoted_reason") or ""),
            )
        demoted = sum(1 for a in self.attribution.values() if a.demoted)
        log.info("restored %d wallet records (%d demoted)", len(self.attribution), demoted)
