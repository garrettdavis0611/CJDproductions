"""The trading loop.

One cycle:
  1. manage open positions first — exits always take priority over entries
  2. discover candidates (new + boosted listings)
  3. snapshot them, keep a short rolling history per mint
  4. run the safety gauntlet; anything with a hard fail is cached as rejected
  5. ask the strategy for entries, the risk manager for size, the broker for a fill

Order matters. A cycle that spends its rate limit on new candidates while an open
position is rugging is a bug, not a trade-off.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Sequence

from .config import Config
from .datasources.dexscreener import DexScreenerClient
from .datasources.jupiter import JupiterClient
from .datasources.rugcheck import RugCheckClient
from .datasources.solana_rpc import SolanaRpc
from .execution.base import OrderFailed, OrderRejected
from .models import WSOL_MINT, Position, ScreenResult, TokenSnapshot
from .portfolio import Portfolio
from .risk import RiskManager
from .screening.filters import FilterContext, screen
from .screening.safety import SafetyInspector
from .strategy.base import ExitDecision
from .strategy.momentum import MomentumStrategy, liquidity_trend_pct

log = logging.getLogger(__name__)

REJECT_CACHE_SECONDS = 3600.0
DEFAULT_DECIMALS = 9


class TradingEngine:
    def __init__(
        self,
        config: Config,
        dexscreener: DexScreenerClient,
        broker,
        portfolio: Portfolio,
        risk: RiskManager,
        strategy=None,
        safety: SafetyInspector | None = None,
        rpc: SolanaRpc | None = None,
        clock=time.time,
        wallet_watcher=None,
        tracker=None,
    ) -> None:
        self.config = config
        self.dexscreener = dexscreener
        self.broker = broker
        self.portfolio = portfolio
        self.risk = risk
        self.strategy = strategy or MomentumStrategy(config.strategy)
        self.safety = safety
        self.rpc = rpc
        self._clock = clock
        self.wallet_watcher = wallet_watcher
        self.tracker = tracker

        self.history: dict[str, deque[TokenSnapshot]] = {}
        self._rejected: dict[str, tuple[float, str]] = {}
        self._decimals: dict[str, int] = {}
        self._recorder: SnapshotRecorder | None = None
        if config.engine.record_snapshots:
            self._recorder = SnapshotRecorder(
                Path(config.engine.data_dir) / "snapshots.jsonl"
            )
        self.cycles = 0

    # ------------------------------------------------------------------ plumbing

    def observe(self, snapshots: Iterable[TokenSnapshot]) -> None:
        limit = self.config.engine.snapshot_history
        for snapshot in snapshots:
            bucket = self.history.get(snapshot.mint)
            if bucket is None:
                bucket = self.history[snapshot.mint] = deque(maxlen=limit)
            bucket.append(snapshot)
            if self._recorder is not None:
                self._recorder.write(snapshot)

    def _is_rejected(self, mint: str, now: float) -> str | None:
        entry = self._rejected.get(mint)
        if entry is None:
            return None
        when, reason = entry
        if now - when > REJECT_CACHE_SECONDS:
            del self._rejected[mint]
            return None
        return reason

    def _decimals_for(self, mint: str) -> int:
        if mint in self._decimals:
            return self._decimals[mint]
        if self.rpc is not None:
            info = self.rpc.mint_info(mint)
            if info is not None:
                self._decimals[mint] = info.decimals
                return info.decimals
        return DEFAULT_DECIMALS

    # --------------------------------------------------------------- one cycle

    def run_cycle(self) -> None:
        now = self._clock()
        self.cycles += 1
        self.risk.roll_day_if_needed(now)

        # Wallet activity is polled before anything else: a followed wallet selling is
        # an exit signal, and exits outrank entries.
        if self.wallet_watcher is not None:
            try:
                new_trades = self.wallet_watcher.poll(now)
                if new_trades:
                    log.info("observed %d new trade(s) from tracked wallets", new_trades)
            except Exception as exc:
                log.warning("wallet watcher failed: %s", exc)

        self.manage_positions(now)

        if self.risk.is_halted(now):
            log.warning("entries suspended: %s", self.risk.state.halted_reason)
            return

        candidates = self.discover()
        if not candidates:
            log.info("cycle %d: no candidates discovered", self.cycles)
            return

        snapshots = self.dexscreener.snapshots_for_mints(candidates)
        self.observe(snapshots.values())
        log.info("cycle %d: %d candidates, %d with market data", self.cycles, len(candidates), len(snapshots))

        for mint, snapshot in sorted(
            snapshots.items(), key=lambda kv: kv[1].volume_h1, reverse=True
        ):
            if mint in self.portfolio.positions:
                continue
            cached = self._is_rejected(mint, now)
            if cached is not None:
                continue
            self.consider_entry(snapshot, now)

    def discover(self) -> list[str]:
        limit = self.config.engine.max_candidates_per_cycle
        seen: dict[str, None] = {}
        for source in (self.dexscreener.latest_token_profiles, self.dexscreener.latest_boosted_tokens):
            try:
                for mint in source():
                    seen.setdefault(mint, None)
            except Exception as exc:
                log.warning("discovery source %s failed: %s", source.__name__, exc)
            if len(seen) >= limit:
                break
        # Always refresh open positions' data even if discovery is saturated.
        for mint in self.portfolio.positions:
            seen.setdefault(mint, None)
        return list(seen)[: limit + len(self.portfolio.positions)]

    # ----------------------------------------------------------------- entries

    def consider_entry(self, snapshot: TokenSnapshot, now: float) -> bool:
        mint = snapshot.mint
        history = list(self.history.get(mint, [snapshot]))

        signal = self.strategy.entry_signal(history)
        if signal is None:
            return False

        sizing = self.risk.can_open(
            mint=mint,
            equity_usd=self.portfolio.equity_usd,
            open_positions=len(self.portfolio.positions),
            open_exposure_usd=self.portfolio.open_cost_usd,
            now=now,
        )
        if not sizing.allowed:
            log.info("skip %s (%s): %s", snapshot.symbol or mint[:8], f"score {signal.score:.2f}", sizing.reason)
            return False

        result = self.run_screening(snapshot, history)
        if not result.passed:
            self._rejected[mint] = (now, result.reason())
            log.info("REJECT %s: %s", snapshot.symbol or mint[:8], result.reason())
            return False
        if result.soft_flags:
            log.info("%s passed with flags: %s", snapshot.symbol or mint[:8], "; ".join(result.soft_flags))

        decimals = self._decimals_for(mint)
        try:
            fill = self.broker.buy(
                mint=mint,
                notional_usd=sizing.notional_usd,
                quoted_price_usd=snapshot.price_usd,
                decimals=decimals,
            )
        except OrderRejected as exc:
            log.info("order rejected for %s: %s", snapshot.symbol or mint[:8], exc)
            return False
        except OrderFailed as exc:
            log.warning("order failed for %s: %s", snapshot.symbol or mint[:8], exc)
            return False

        self.portfolio.apply_buy(fill, symbol=snapshot.symbol, entry_liquidity_usd=snapshot.liquidity_usd)
        self.risk.record_entry(now)

        # Remember which wallets we bought on behalf of, so the outcome lands on them.
        if self.tracker is not None:
            wallets_for = getattr(self.strategy, "wallets_for", None)
            if callable(wallets_for):
                credited = wallets_for(mint)
                if credited:
                    self.tracker.credit_entry(mint, credited)
        log.info(
            "BUY  %-10s $%.2f @ $%.8g (score %.2f, slip %.0f bps, fee $%.3f) | %s",
            snapshot.symbol or mint[:8], fill.qty * fill.price_usd, fill.price_usd,
            signal.score, fill.slippage_bps, fill.fee_usd, ", ".join(signal.reasons),
        )
        return True

    def run_screening(
        self, snapshot: TokenSnapshot, history: Sequence[TokenSnapshot]
    ) -> ScreenResult:
        safety = (
            self.safety.inspect(snapshot, decimals_hint=self._decimals.get(snapshot.mint))
            if self.safety is not None
            else None
        )
        if safety is None:
            from .models import SafetyReport

            safety = SafetyReport(mint=snapshot.mint, errors=["no safety inspector configured"])

        ctx = FilterContext(
            snapshot=snapshot,
            safety=safety,
            config=self.config.screening,
            liquidity_trend_pct=liquidity_trend_pct(history),
        )
        return screen(ctx)

    # ------------------------------------------------------------------- exits

    def manage_positions(self, now: float) -> None:
        if not self.portfolio.positions:
            return
        mints = list(self.portfolio.positions)
        try:
            fresh = self.dexscreener.snapshots_for_mints(mints)
        except Exception as exc:
            log.error("could not refresh open positions: %s", exc)
            return
        self.observe(fresh.values())

        for mint in mints:
            position = self.portfolio.positions.get(mint)
            if position is None:
                continue
            snapshot = fresh.get(mint)
            if snapshot is None:
                # No market data at all is itself a red flag; the time stop still applies.
                log.warning("no market data for open position %s (%s)", position.symbol, mint[:8])
                if position.hold_minutes(now) >= self.config.strategy.max_hold_minutes:
                    self._exit(position, position.last_price_usd, "no data + max hold", 1.0, now)
                continue

            self.portfolio.mark(mint, snapshot.price_usd)
            decision = self.strategy.exit_decision(position, list(self.history.get(mint, [snapshot])), now)
            if decision.should_exit:
                self._exit(position, snapshot.price_usd, decision.reason, decision.fraction, now)

    def _exit(
        self, position: Position, price_usd: float, reason: str, fraction: float, now: float
    ) -> None:
        fraction = min(1.0, max(0.0, fraction))
        qty = position.qty * fraction
        if qty <= 0 or price_usd <= 0:
            return
        full_exit = fraction >= 1.0 - 1e-9

        try:
            fill = self.broker.sell(
                mint=position.mint,
                qty=qty,
                quoted_price_usd=price_usd,
                decimals=self._decimals_for(position.mint),
            )
        except (OrderRejected, OrderFailed) as exc:
            log.error(
                "COULD NOT EXIT %s (%s): %s — retrying next cycle",
                position.symbol, position.mint[:8], exc,
            )
            return

        symbol = position.symbol or position.mint[:8]
        mint = position.mint
        trade = self.portfolio.apply_sell(fill, exit_reason=reason)
        if trade is not None:
            self.risk.record_exit(mint, trade.pnl_usd, now, full_exit=full_exit)
            # Attribute the result to the wallets that triggered the entry. This is the
            # only defence against a wallet using our buys as its exit liquidity.
            if self.tracker is not None and full_exit:
                for demoted in self.tracker.record_outcome(mint, trade.pnl_usd):
                    log.warning("stopped following %s after attributed losses", demoted[:8])
            log.info(
                "SELL %-10s %s $%.2f -> $%.2f (%+.1f%%, %.0fm) | %s",
                symbol, "ALL " if full_exit else f"{fraction:.0%}",
                trade.cost_usd, trade.proceeds_usd, trade.pnl_pct * 100.0,
                trade.hold_minutes, reason,
            )

    # ------------------------------------------------------------------ runner

    def run_forever(self, max_cycles: int | None = None, sleeper=time.sleep) -> None:
        poll = self.config.engine.poll_seconds
        log.info(
            "engine start | mode=%s equity=$%.2f round-trip cost=%.0f bps",
            self.config.execution.mode, self.portfolio.equity_usd, self.config.round_trip_cost_bps,
        )
        # Counted here rather than off self.cycles: a cycle that raises before
        # incrementing self.cycles must still count against max_cycles, or a
        # persistently failing data source turns this into an infinite loop.
        iterations = 0
        try:
            while max_cycles is None or iterations < max_cycles:
                iterations += 1
                started = self._clock()
                try:
                    self.run_cycle()
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log.exception("cycle %d raised; continuing", iterations)
                if max_cycles is not None and iterations >= max_cycles:
                    break
                elapsed = self._clock() - started
                sleeper(max(0.0, poll - elapsed))
        except KeyboardInterrupt:
            log.info("interrupted — leaving open positions untouched")
        finally:
            if self._recorder is not None:
                self._recorder.close()


class SnapshotRecorder:
    """Appends every observation to JSONL so `memebot backtest` has real data to replay."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = None

    def write(self, snapshot: TokenSnapshot) -> None:
        try:
            if self._handle is None:
                self._handle = self.path.open("a")
            self._handle.write(json.dumps(asdict(snapshot)) + "\n")
            self._handle.flush()
        except OSError as exc:
            log.warning("snapshot recording disabled: %s", exc)
            self._handle = None

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
