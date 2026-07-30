"""Tests for wallet trade reconstruction and the skill-vs-luck filters."""

import pytest

from memebot.config import SmartMoneyConfig
from memebot.smartmoney.analysis import analyse, build_episodes, compute_stats, qualify
from memebot.smartmoney.models import WalletSide, WalletTrade

WALLET = "Wallet11111111111111111111111111111111111111"
DAY = 86_400.0
T0 = 1_700_000_000.0


def buy(mint, tokens, sol, ts):
    return WalletTrade(WALLET, mint, WalletSide.BUY, tokens, sol, ts)


def sell(mint, tokens, sol, ts):
    return WalletTrade(WALLET, mint, WalletSide.SELL, tokens, sol, ts)


# ------------------------------------------------------------------- episodes


def test_a_simple_round_trip_is_one_episode():
    episodes = build_episodes([buy("A", 100, 1.0, T0), sell("A", 100, 1.5, T0 + 600)])
    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.closed
    assert episode.pnl_sol == pytest.approx(0.5)
    assert episode.pnl_ratio == pytest.approx(0.5)
    assert episode.hold_minutes == pytest.approx(10.0)


def test_scaling_in_stays_one_episode():
    """Otherwise a wallet that averaged into one winner looks like several wins."""
    episodes = build_episodes(
        [buy("A", 100, 1.0, T0), buy("A", 100, 1.4, T0 + 60), sell("A", 200, 3.0, T0 + 600)]
    )
    assert len(episodes) == 1
    assert episodes[0].cost_sol == pytest.approx(2.4)
    assert episodes[0].pnl_sol == pytest.approx(0.6)


def test_partial_sells_close_only_when_flat():
    episodes = build_episodes(
        [buy("A", 100, 1.0, T0), sell("A", 50, 0.9, T0 + 60), sell("A", 50, 1.0, T0 + 120)]
    )
    assert len(episodes) == 1
    assert episodes[0].closed
    assert episodes[0].proceeds_sol == pytest.approx(1.9)


def test_reentering_the_same_token_makes_a_second_episode():
    episodes = build_episodes(
        [
            buy("A", 100, 1.0, T0), sell("A", 100, 1.2, T0 + 60),
            buy("A", 100, 1.0, T0 + 600), sell("A", 100, 0.7, T0 + 900),
        ]
    )
    assert len(episodes) == 2
    assert [round(e.pnl_sol, 3) for e in episodes] == [0.2, -0.3]


def test_selling_tokens_we_never_saw_bought_is_ignored():
    """Airdrops and transfers in would otherwise register as free profit."""
    assert build_episodes([sell("A", 100, 5.0, T0)]) == []


def test_selling_more_than_held_does_not_inflate_proceeds():
    episodes = build_episodes([buy("A", 100, 1.0, T0), sell("A", 500, 5.0, T0 + 60)])
    assert len(episodes) == 1
    # Only the 100 tokens we bought count: 1/5 of the sale proceeds.
    assert episodes[0].proceeds_sol == pytest.approx(1.0)


def test_an_open_position_is_recorded_but_not_closed():
    episodes = build_episodes([buy("A", 100, 1.0, T0)])
    assert len(episodes) == 1
    assert not episodes[0].closed

    stats = compute_stats(WALLET, [buy("A", 100, 1.0, T0)])
    assert stats.closed_episodes == 0
    assert stats.realized_pnl_sol == 0.0  # unrealised opinions are not results


def test_episodes_are_tracked_per_mint():
    episodes = build_episodes(
        [
            buy("A", 100, 1.0, T0), buy("B", 100, 2.0, T0 + 10),
            sell("A", 100, 1.5, T0 + 60), sell("B", 100, 1.0, T0 + 70),
        ]
    )
    assert len(episodes) == 2
    assert {e.mint for e in episodes} == {"A", "B"}


# ----------------------------------------------------------------------- stats


def test_stats_compute_win_rate_and_hold_time():
    trades = []
    for i, ratio in enumerate([1.5, 1.5, 0.5, 1.2]):
        mint = f"T{i}"
        trades += [buy(mint, 100, 1.0, T0 + i * DAY), sell(mint, 100, ratio, T0 + i * DAY + 600)]
    stats = compute_stats(WALLET, trades)
    assert stats.closed_episodes == 4
    assert stats.win_rate == pytest.approx(0.75)
    assert stats.distinct_tokens == 4
    assert stats.median_hold_minutes == pytest.approx(10.0)
    assert stats.active_days == 4


def test_concentration_detects_a_single_lucky_hit():
    trades = []
    for i in range(9):
        mint = f"T{i}"
        trades += [buy(mint, 100, 1.0, T0 + i * DAY), sell(mint, 100, 0.9, T0 + i * DAY + 600)]
    trades += [buy("MOON", 100, 1.0, T0 + 10 * DAY), sell("MOON", 100, 50.0, T0 + 10 * DAY + 600)]

    stats = compute_stats(WALLET, trades)
    assert stats.realized_pnl_sol > 40  # looks fantastic on a leaderboard
    assert stats.best_token_profit_share == pytest.approx(1.0)  # all of it from one token


def test_empty_history_is_handled():
    stats = compute_stats(WALLET, [])
    assert stats.closed_episodes == 0
    assert stats.win_rate == 0.0


# --------------------------------------------------------------- qualification


def solid_history(
    episodes=36, tokens=None, ratio_win=1.5, win_rate=0.6, span_days=200.0, hold=60.0
):
    """A wallet that would pass every gate: broad, consistent, and spread over ~7 months."""
    trades = []
    for i in range(episodes):
        mint = f"T{i % (tokens or episodes)}"
        win = (i % 10) < int(round(win_rate * 10))
        ratio = ratio_win if win else 0.85
        ts = T0 + (i / max(1, episodes - 1)) * span_days * DAY
        trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, ratio, ts + hold * 60.0)]
    return trades


def test_a_solid_wallet_qualifies():
    stats = analyse(WALLET, solid_history(), SmartMoneyConfig(enabled=True))
    assert stats.qualified, stats.disqualifiers
    assert stats.score > 0


def test_too_few_round_trips_is_disqualifying():
    stats = analyse(WALLET, solid_history(episodes=12), SmartMoneyConfig(enabled=True))
    assert not stats.qualified
    assert any("closed round trips" in d for d in stats.disqualifiers)


def test_too_few_distinct_tokens_is_disqualifying():
    stats = analyse(WALLET, solid_history(episodes=30, tokens=4), SmartMoneyConfig(enabled=True))
    assert not stats.qualified
    assert any("traded only" in d for d in stats.disqualifiers)


def test_low_win_rate_is_disqualifying():
    stats = analyse(WALLET, solid_history(win_rate=0.2), SmartMoneyConfig(enabled=True))
    assert not stats.qualified
    assert any("win rate" in d for d in stats.disqualifiers)


def test_a_single_session_record_is_disqualifying():
    stats = analyse(WALLET, solid_history(span_days=0.5), SmartMoneyConfig(enabled=True))
    assert not stats.qualified
    assert any("one session's luck" in d for d in stats.disqualifiers)


def test_profit_concentrated_in_one_token_is_disqualifying():
    trades = solid_history(episodes=30, win_rate=0.0)  # all small losers
    trades += [buy("MOON", 100, 1.0, T0 + 5 * DAY), sell("MOON", 100, 400.0, T0 + 5 * DAY + 3600)]
    stats = analyse(WALLET, trades, SmartMoneyConfig(enabled=True))
    assert not stats.qualified
    assert any("one lucky hit" in d for d in stats.disqualifiers)


def test_a_sniper_is_disqualified_because_we_cannot_copy_latency():
    stats = analyse(WALLET, solid_history(hold=0.5), SmartMoneyConfig(enabled=True))
    assert not stats.qualified
    assert any("latency you do not have" in d for d in stats.disqualifiers)


def test_a_slow_holder_is_disqualified_as_unactionable():
    stats = analyse(WALLET, solid_history(hold=5_000.0), SmartMoneyConfig(enabled=True))
    assert not stats.qualified
    assert any("too slow to copy" in d for d in stats.disqualifiers)


def test_unprofitable_wallet_is_disqualified():
    stats = analyse(WALLET, solid_history(ratio_win=1.01, win_rate=0.6), SmartMoneyConfig(enabled=True))
    assert not stats.qualified
    assert any("realized" in d for d in stats.disqualifiers)


def test_all_failures_are_reported_together():
    stats = analyse(
        WALLET, solid_history(episodes=5, span_days=1.0, hold=0.5), SmartMoneyConfig(enabled=True)
    )
    assert not stats.qualified
    assert len(stats.disqualifiers) >= 3


def test_qualification_thresholds_are_hard_gates_not_a_weighted_average():
    """A spectacular PnL must not buy its way past the sample-size gate."""
    trades = solid_history(episodes=12)
    trades += [buy("BIG", 100, 1.0, T0 + 3 * DAY), sell("BIG", 100, 5_000.0, T0 + 3 * DAY + 3600)]
    stats = analyse(WALLET, trades, SmartMoneyConfig(enabled=True))
    assert stats.realized_pnl_sol > 1_000
    assert not stats.qualified


# ------------------------------------------------------ temporal stability
# The gates that answer "has this worked consistently for six months", as opposed
# to "is the all-time total impressive".


def test_monthly_buckets_and_span_are_measured():
    stats = compute_stats(WALLET, solid_history(episodes=36, span_days=200.0))
    assert stats.history_days == pytest.approx(200.0, rel=0.02)
    assert stats.months_covered >= 6
    assert stats.profitable_months >= 5
    assert stats.profitable_month_fraction > 0.6


def test_a_short_history_cannot_claim_a_sustained_record():
    stats = analyse(WALLET, solid_history(span_days=25.0), SmartMoneyConfig(enabled=True))
    assert not stats.qualified
    assert any("cannot claim a sustained record" in d for d in stats.disqualifiers)


def test_a_wallet_whose_profit_came_from_one_month_is_rejected():
    """The exact failure the user's question is about: big total, one good month."""
    trades = []
    # Five months of small losses...
    for i in range(30):
        mint = f"T{i}"
        ts = T0 + i * 5 * DAY
        trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 0.9, ts + 3600)]
    # ...then one spectacular month spread over several tokens, so the concentration
    # gate does not catch it and only the monthly view can.
    for i in range(6):
        mint = f"HOT{i}"
        ts = T0 + 160 * DAY + i * DAY
        trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 9.0, ts + 3600)]

    stats = analyse(WALLET, trades, SmartMoneyConfig(enabled=True))
    assert stats.realized_pnl_sol > 20  # looks great in total
    assert not stats.qualified
    assert any("carried by a few good months" in d for d in stats.disqualifiers)


def test_a_decaying_wallet_is_rejected():
    """Great for four months, flat since. Not what "stable success" means."""
    trades = []
    for i in range(20):  # strong first half
        mint = f"E{i}"
        ts = T0 + i * 4 * DAY
        trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 2.0, ts + 3600)]
    for i in range(20):  # limp second half
        mint = f"L{i}"
        ts = T0 + 100 * DAY + i * 4 * DAY
        trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 1.0, ts + 3600)]

    stats = analyse(WALLET, trades, SmartMoneyConfig(enabled=True))
    assert stats.is_decaying
    assert not stats.qualified
    assert any("decaying" in d for d in stats.disqualifiers)


def test_decay_rejection_can_be_disabled():
    config = SmartMoneyConfig(enabled=True, reject_decaying_wallets=False, min_recent_pnl_sol=-99.0)
    trades = []
    for i in range(20):
        mint = f"E{i}"
        ts = T0 + i * 4 * DAY
        trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 2.0, ts + 3600)]
    for i in range(20):
        mint = f"L{i}"
        ts = T0 + 100 * DAY + i * 4 * DAY
        trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 1.0, ts + 3600)]
    stats = analyse(WALLET, trades, config)
    assert not any("decaying" in d for d in stats.disqualifiers)


def test_a_losing_recent_half_is_rejected():
    trades = []
    for i in range(20):
        mint = f"E{i}"
        ts = T0 + i * 4 * DAY
        trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 3.0, ts + 3600)]
    for i in range(20):
        mint = f"L{i}"
        ts = T0 + 100 * DAY + i * 4 * DAY
        trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 0.5, ts + 3600)]
    stats = analyse(WALLET, trades, SmartMoneyConfig(enabled=True))
    assert stats.recent_pnl_sol < 0
    assert not stats.qualified


def test_a_dormant_wallet_is_rejected():
    """A record from three months ago is history, not a signal."""
    trades = solid_history()
    last = max(t.ts for t in trades)
    stats = analyse(WALLET, trades, SmartMoneyConfig(enabled=True), now=last + 90 * DAY)
    assert stats.days_since_last_trade == pytest.approx(90.0, rel=0.01)
    assert not stats.qualified
    assert any("record may be historical" in d for d in stats.disqualifiers)


def test_a_long_losing_streak_is_rejected():
    trades = []
    # Four consecutive losing months, then recovery.
    for month in range(4):
        for i in range(4):
            mint = f"M{month}_{i}"
            ts = T0 + month * 31 * DAY + i * DAY
            trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 0.7, ts + 3600)]
    for month in range(4, 8):
        for i in range(6):
            mint = f"W{month}_{i}"
            ts = T0 + month * 31 * DAY + i * DAY
            trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 2.5, ts + 3600)]

    stats = analyse(WALLET, trades, SmartMoneyConfig(enabled=True))
    assert stats.longest_losing_month_streak >= 4
    assert not stats.qualified
    assert any("consecutive losing months" in d for d in stats.disqualifiers)


def test_the_wallets_own_drawdown_is_measured():
    trades = []
    for i in range(20):  # run up
        mint = f"U{i}"
        ts = T0 + i * 5 * DAY
        trades += [buy(mint, 100, 1.0, ts), sell(mint, 100, 2.0, ts + 3600)]
    for i in range(12):  # then give a lot of it back
        mint = f"D{i}"
        ts = T0 + 105 * DAY + i * 5 * DAY
        trades += [buy(mint, 100, 5.0, ts), sell(mint, 100, 3.5, ts + 3600)]

    stats = compute_stats(WALLET, trades)
    assert stats.wallet_max_drawdown_pct > 50.0


def test_steadiness_raises_the_score():
    steady = compute_stats(WALLET, solid_history(episodes=36, span_days=200.0))
    from memebot.smartmoney.analysis import score_wallet

    lumpy = compute_stats(WALLET, solid_history(episodes=36, span_days=200.0))
    lumpy.profitable_months = 1
    lumpy.months_covered = 7
    assert score_wallet(steady) > score_wallet(lumpy)


def test_summary_is_serialisable():
    stats = analyse(WALLET, solid_history(), SmartMoneyConfig(enabled=True))
    summary = stats.summary()
    assert summary["wallet"] == WALLET
    assert isinstance(summary["qualified"], bool)
    assert "disqualifiers" in summary
