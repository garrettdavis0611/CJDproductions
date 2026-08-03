"""Polls followed wallets for new trades and feeds them to the tracker.

Note the honest limitation baked into the name: this **polls**. On a public RPC that
means tens of seconds of latency, and the copy signal is stale by exactly that much.
The tracker's freshness and drift gates are what keep stale signals from becoming
trades — they are not optional decoration, they are what makes a polling copy-trader
safe rather than merely slow.

If you want lower latency, the upgrade path is a websocket subscription (Helius,
Birdeye `SUBSCRIBE_WALLET_TXS`) feeding `observe_trade` directly. The gates stay
exactly as they are; they just reject less often.
"""

from __future__ import annotations

import logging
import time

from ..config import SmartMoneyConfig
from .analysis import analyse
from .models import WalletTrade
from .tracker import SmartMoneyTracker

log = logging.getLogger(__name__)


class WalletWatcher:
    def __init__(
        self,
        config: SmartMoneyConfig,
        feed,
        tracker: SmartMoneyTracker,
        sol_price_provider=None,
        clock=time.time,
    ) -> None:
        self.config = config
        self.feed = feed
        self.tracker = tracker
        self._sol_price_provider = sol_price_provider
        self._clock = clock
        self._seen_signatures: set[str] = set()
        self._last_seen_ts: dict[str, float] = {}
        self._last_refresh = 0.0
        self.polls = 0

    # ------------------------------------------------------------------- pricing

    def sol_price_usd(self) -> float:
        if self._sol_price_provider is not None:
            try:
                price = float(self._sol_price_provider())
                if price > 0:
                    return price
            except Exception as exc:
                log.warning("SOL price lookup failed: %s", exc)
        return 0.0

    # -------------------------------------------------------------- bootstrapping

    def bootstrap(self, wallets: list[str]) -> list[str]:
        """Analyse candidate wallets and follow the ones that qualify."""
        followed: list[str] = []
        for wallet in wallets:
            try:
                trades = self.feed.recent_trades(wallet, self.config.analysis_transactions)
            except Exception as exc:
                log.warning("could not analyse %s: %s", wallet[:8], exc)
                continue
            stats = analyse(wallet, trades, self.config)
            if self.tracker.follow(stats):
                followed.append(wallet)
                log.info(
                    "following %s (score %.2f, %d round trips, %.1f SOL, %.0f%% win rate)",
                    wallet[:8], stats.score, stats.closed_episodes,
                    stats.realized_pnl_sol, stats.win_rate * 100.0,
                )
            # Seed the high-water mark so historical trades are not replayed as signals.
            if trades:
                self._last_seen_ts[wallet] = max(t.ts for t in trades)
                self._seen_signatures.update(t.signature for t in trades if t.signature)
        return followed

    # ------------------------------------------------------------------- polling

    def poll(self, now: float | None = None, per_wallet_limit: int = 25) -> int:
        """Fetch recent trades for every followed wallet. Returns new trades observed."""
        now = self._clock() if now is None else now
        self.polls += 1
        sol_price = self.sol_price_usd()
        observed = 0

        for wallet in self.tracker.active_wallets():
            try:
                trades = self.feed.recent_trades(wallet, per_wallet_limit)
            except Exception as exc:
                log.warning("wallet poll failed for %s: %s", wallet[:8], exc)
                continue
            observed += self._ingest(wallet, trades, sol_price)

        self._maybe_refresh(now)
        return observed

    def _ingest(self, wallet: str, trades: list[WalletTrade], sol_price: float) -> int:
        high_water = self._last_seen_ts.get(wallet, 0.0)
        new_high = high_water
        count = 0

        for trade in trades:
            if trade.signature and trade.signature in self._seen_signatures:
                continue
            if trade.ts <= high_water:
                continue
            if trade.signature:
                self._seen_signatures.add(trade.signature)
            new_high = max(new_high, trade.ts)

            # Their actual fill price, which is what the drift gate must compare against.
            price_usd = trade.price_sol * sol_price if sol_price > 0 else 0.0
            self.tracker.observe_trade(wallet, trade.mint, trade.side, trade.ts, price_usd)
            count += 1

        self._last_seen_ts[wallet] = new_high
        # Bound memory on a long-running process.
        if len(self._seen_signatures) > 50_000:
            self._seen_signatures = set(list(self._seen_signatures)[-25_000:])
        return count

    def _maybe_refresh(self, now: float) -> None:
        """Re-analyse followed wallets periodically: a record can decay."""
        interval = self.config.refresh_minutes * 60.0
        if interval <= 0 or now - self._last_refresh < interval:
            return
        self._last_refresh = now

        for wallet in list(self.tracker.active_wallets()):
            try:
                trades = self.feed.recent_trades(wallet, self.config.analysis_transactions)
            except Exception as exc:
                log.warning("refresh failed for %s: %s", wallet[:8], exc)
                continue
            stats = analyse(wallet, trades, self.config)
            if not stats.qualified:
                log.info(
                    "unfollowing %s — no longer qualifies: %s",
                    wallet[:8], "; ".join(stats.disqualifiers),
                )
                self.tracker.unfollow(wallet)
            else:
                self.tracker.followed[wallet] = stats
