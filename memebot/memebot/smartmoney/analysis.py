"""Reconstruct a wallet's trades from chain data and score its actual skill.

The whole point of this module is to distinguish **skill from luck**, because the
naive version of copy trading — sort wallets by PnL, follow the top ones — is a
survivorship-bias machine. In any large population of gamblers, some will have
spectacular records by chance, and those are exactly the wallets a PnL leaderboard
surfaces.

The guards that matter:
  * `best_token_profit_share` — one 100x on one token is luck, not a process.
  * `active_days` — a record built in a single session is one session's luck.
  * `closed_episodes` — twenty round trips is a weak sample; five is nothing.
  * `median_hold_minutes` — a wallet whose edge is being 400ms faster than everyone
    has an edge you cannot copy, because you will always be later than it.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Sequence

from ..config import SmartMoneyConfig
from .models import PositionEpisode, WalletSide, WalletStats, WalletTrade

log = logging.getLogger(__name__)

DUST = 1e-9


def build_episodes(trades: Sequence[WalletTrade]) -> list[PositionEpisode]:
    """Group a single wallet's trades into round trips, per mint.

    An episode opens when a position goes from zero to positive and closes when it
    returns to (approximately) zero. Sells beyond the tracked position are ignored
    rather than counted as profit: airdrops and transfers in would otherwise show up
    as free money.
    """
    by_mint: dict[str, list[WalletTrade]] = defaultdict(list)
    for trade in trades:
        by_mint[trade.mint].append(trade)

    episodes: list[PositionEpisode] = []
    for mint, mint_trades in by_mint.items():
        mint_trades.sort(key=lambda t: t.ts)
        position = 0.0
        cost = 0.0
        proceeds = 0.0
        entry_ts = 0.0
        last_ts = 0.0
        open_episode = False

        for trade in mint_trades:
            last_ts = trade.ts
            if trade.side is WalletSide.BUY:
                if not open_episode:
                    open_episode = True
                    entry_ts = trade.ts
                    cost = 0.0
                    proceeds = 0.0
                position += trade.token_amount
                cost += trade.sol_amount
            else:
                if not open_episode or position <= DUST:
                    # Selling something we never saw bought — not our PnL to claim.
                    continue
                sold = min(trade.token_amount, position)
                fraction = sold / trade.token_amount if trade.token_amount else 1.0
                proceeds += trade.sol_amount * fraction
                position -= sold
                if position <= max(DUST, 1e-6 * (position + sold)):
                    episodes.append(
                        PositionEpisode(
                            mint=mint, cost_sol=cost, proceeds_sol=proceeds,
                            entry_ts=entry_ts, exit_ts=trade.ts, closed=True,
                        )
                    )
                    open_episode = False
                    position = 0.0

        if open_episode and cost > 0:
            # Still holding. Recorded but not counted toward win rate: an open
            # position is an unrealised opinion, not a result.
            episodes.append(
                PositionEpisode(
                    mint=mint, cost_sol=cost, proceeds_sol=proceeds,
                    entry_ts=entry_ts, exit_ts=last_ts, closed=False,
                )
            )
    return episodes


def compute_stats(wallet: str, trades: Sequence[WalletTrade]) -> WalletStats:
    stats = WalletStats(wallet=wallet, trades_analysed=len(trades))
    if not trades:
        return stats

    timestamps = [t.ts for t in trades]
    stats.first_ts = min(timestamps)
    stats.last_ts = max(timestamps)
    stats.active_days = len(
        {datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") for ts in timestamps}
    )

    episodes = build_episodes(trades)
    stats.episodes = episodes
    closed = [e for e in episodes if e.closed]
    stats.closed_episodes = len(closed)
    stats.distinct_tokens = len({e.mint for e in episodes})

    if not closed:
        return stats

    stats.realized_pnl_sol = sum(e.pnl_sol for e in closed)
    stats.win_rate = sum(1 for e in closed if e.pnl_sol > 0) / len(closed)
    stats.median_hold_minutes = statistics.median([e.hold_minutes for e in closed])

    profit_by_mint: dict[str, float] = defaultdict(float)
    for episode in closed:
        if episode.pnl_sol > 0:
            profit_by_mint[episode.mint] += episode.pnl_sol
    gross_profit = sum(profit_by_mint.values())
    if gross_profit > 0:
        stats.best_token_profit_share = max(profit_by_mint.values()) / gross_profit

    return stats


def qualify(stats: WalletStats, config: SmartMoneyConfig) -> WalletStats:
    """Apply the hard requirements. Any single failure disqualifies the wallet."""
    fails: list[str] = []

    if stats.closed_episodes < config.min_closed_trades:
        fails.append(
            f"only {stats.closed_episodes} closed round trips "
            f"(< {config.min_closed_trades}) — sample too small to distinguish skill"
        )
    if stats.distinct_tokens < config.min_distinct_tokens:
        fails.append(
            f"traded only {stats.distinct_tokens} tokens (< {config.min_distinct_tokens})"
        )
    if stats.realized_pnl_sol < config.min_realized_pnl_sol:
        fails.append(
            f"realized {stats.realized_pnl_sol:.2f} SOL (< {config.min_realized_pnl_sol:.2f})"
        )
    if stats.win_rate < config.min_win_rate:
        fails.append(f"win rate {stats.win_rate:.0%} (< {config.min_win_rate:.0%})")
    if stats.active_days < config.min_active_days:
        fails.append(
            f"active on only {stats.active_days} days (< {config.min_active_days}) — "
            "one session's luck"
        )
    if stats.best_token_profit_share > config.max_single_token_profit_share:
        fails.append(
            f"{stats.best_token_profit_share:.0%} of profit came from one token "
            f"(> {config.max_single_token_profit_share:.0%}) — one lucky hit, not a process"
        )
    if stats.median_hold_minutes < config.min_median_hold_minutes:
        fails.append(
            f"median hold {stats.median_hold_minutes:.1f}m "
            f"(< {config.min_median_hold_minutes:.1f}m) — this is a sniper whose edge is "
            "latency you do not have"
        )
    if stats.median_hold_minutes > config.max_median_hold_minutes:
        fails.append(
            f"median hold {stats.median_hold_minutes:.0f}m "
            f"(> {config.max_median_hold_minutes:.0f}m) — signal is too slow to copy"
        )

    stats.disqualifiers = fails
    stats.qualified = not fails
    stats.score = score_wallet(stats)
    return stats


def score_wallet(stats: WalletStats) -> float:
    """A 0-1 ranking score. Used to order qualified wallets, not to qualify them —
    qualification is a set of hard gates, deliberately not a weighted average that a
    single spectacular number could carry."""
    if stats.closed_episodes <= 0:
        return 0.0

    def band(value: float, low: float, high: float) -> float:
        if high <= low:
            return 0.0
        return min(1.0, max(0.0, (value - low) / (high - low)))

    consistency = 1.0 - min(1.0, stats.best_token_profit_share)
    return (
        0.30 * band(stats.win_rate, 0.40, 0.70)
        + 0.25 * band(stats.realized_pnl_sol, 1.0, 100.0)
        + 0.20 * consistency
        + 0.15 * band(float(stats.closed_episodes), 10.0, 100.0)
        + 0.10 * band(float(stats.active_days), 3.0, 30.0)
    )


def analyse(
    wallet: str, trades: Iterable[WalletTrade], config: SmartMoneyConfig
) -> WalletStats:
    return qualify(compute_stats(wallet, list(trades)), config)
