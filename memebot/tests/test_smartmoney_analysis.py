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


def solid_history(episodes=30, tokens=None, ratio_win=1.5, win_rate=0.6, days=20, hold=60.0):
    trades = []
    for i in range(episodes):
        mint = f"T{i % (tokens or episodes)}"
        win = (i % 10) < int(win_rate * 10)
        ratio = ratio_win if win else 0.85
        ts = T0 + (i % days) * DAY + i * 60
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
    stats = analyse(WALLET, solid_history(days=1), SmartMoneyConfig(enabled=True))
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
    stats = analyse(WALLET, solid_history(episodes=5, days=1, hold=0.5), SmartMoneyConfig(enabled=True))
    assert not stats.qualified
    assert len(stats.disqualifiers) >= 3


def test_qualification_thresholds_are_hard_gates_not_a_weighted_average():
    """A spectacular PnL must not buy its way past the sample-size gate."""
    trades = solid_history(episodes=12)
    trades += [buy("BIG", 100, 1.0, T0 + 3 * DAY), sell("BIG", 100, 5_000.0, T0 + 3 * DAY + 3600)]
    stats = analyse(WALLET, trades, SmartMoneyConfig(enabled=True))
    assert stats.realized_pnl_sol > 1_000
    assert not stats.qualified


def test_summary_is_serialisable():
    stats = analyse(WALLET, solid_history(), SmartMoneyConfig(enabled=True))
    summary = stats.summary()
    assert summary["wallet"] == WALLET
    assert isinstance(summary["qualified"], bool)
    assert "disqualifiers" in summary
