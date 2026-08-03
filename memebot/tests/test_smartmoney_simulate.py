"""Tests for the copy-trading experiments themselves.

The selection experiment is the evidence for the central claim that the luck filters
work, so it needs to be verified rather than trusted.
"""

import random

import pytest

from memebot.config import Config, SmartMoneyConfig
from memebot.smartmoney.analysis import analyse
from memebot.smartmoney.simulate import (
    Archetype,
    copy_experiment,
    selection_experiment,
    synth_history,
)


def sm() -> SmartMoneyConfig:
    return SmartMoneyConfig(enabled=True)


# --------------------------------------------------------- synthetic archetypes


def test_each_archetype_generates_a_usable_history():
    rng = random.Random(0)
    for archetype in Archetype:
        trades = synth_history("w" * 40, archetype, rng)
        assert trades, archetype
        assert all(t.sol_amount > 0 for t in trades)
        assert trades == sorted(trades, key=lambda t: t.ts)


def test_a_skilled_wallet_looks_skilled():
    stats = analyse("s" * 40, synth_history("s" * 40, Archetype.SKILLED, random.Random(3)), sm())
    assert stats.closed_episodes >= 20
    assert stats.win_rate > 0.4
    assert stats.realized_pnl_sol > 0


def test_a_lucky_wallets_profit_is_concentrated():
    """The tell that separates luck from process."""
    stats = analyse("l" * 40, synth_history("l" * 40, Archetype.LUCKY, random.Random(4)), sm())
    assert stats.best_token_profit_share > 0.5


def test_a_sniper_holds_for_minutes():
    stats = analyse("n" * 40, synth_history("n" * 40, Archetype.SNIPER, random.Random(5)), sm())
    assert stats.median_hold_minutes < 10.0


# ----------------------------------------------------------- selection quality


def test_selection_accepts_skill_and_rejects_luck_and_snipers():
    """Recall is ~83% at scale, down from ~97% before the six-month consistency gates
    were added. That drop is the measured price of demanding sustained success: some
    genuinely skilled wallets have a losing month and get cut. For deciding where to
    put money, a low false-accept rate is worth more than recall."""
    result = selection_experiment(sm(), per_archetype=40, seed=7)
    assert result["skilled_recall_pct"] >= 70.0
    assert result["lucky_false_accept_pct"] <= 5.0
    assert result["sniper_false_accept_pct"] <= 5.0


def test_farmers_pass_the_a_priori_filters_by_design():
    """If this ever starts failing, the honest README claim needs updating: farmers are
    supposed to be invisible to historical analysis, which is why demotion exists."""
    result = selection_experiment(sm(), per_archetype=40, seed=8)
    assert result["farmer_false_accept_pct"] >= 50.0


def test_a_naive_pnl_leaderboard_config_admits_lucky_wallets():
    """Direct evidence that the gates are load-bearing, not decoration.

    This config is what "sort wallets by PnL and follow the top ones" amounts to:
    keep the profit threshold, drop everything that tests for consistency.
    """
    strict = selection_experiment(sm(), per_archetype=40, seed=9)

    naive = sm()
    naive.max_single_token_profit_share = 1.0   # one moonshot is fine
    naive.min_win_rate = 0.0                    # who cares how often
    naive.min_active_days = 1
    naive.min_closed_trades = 10
    naive.min_history_days = 1.0
    naive.min_months_covered = 1
    naive.min_profitable_month_fraction = 0.01
    naive.max_losing_month_streak = 99
    naive.max_wallet_drawdown_pct = 1e9
    naive.max_days_since_last_trade = 1e9
    naive.reject_decaying_wallets = False
    naive.min_recent_pnl_sol = -1e9

    loose = selection_experiment(naive, per_archetype=40, seed=9)
    assert strict["lucky_false_accept_pct"] == 0.0
    assert loose["lucky_false_accept_pct"] > 50.0


def test_temporal_gates_reject_a_short_lived_record():
    """A six-month claim needs six months of history, whatever the totals say."""
    short = sm()
    short.min_history_days = 400.0  # demand more history than any synthetic wallet has
    short.lookback_days = 500.0
    result = selection_experiment(short, per_archetype=20, seed=11)
    assert result["skilled_recall_pct"] == 0.0


def test_selection_report_shape():
    result = selection_experiment(sm(), per_archetype=10, seed=1)
    assert set(result["by_archetype"]) == {a.value for a in Archetype}
    for bucket in result["by_archetype"].values():
        assert bucket["accepted"] + bucket["rejected"] == bucket["population"]


# ------------------------------------------------------------ copy experiment


def copy_config() -> Config:
    config = Config()
    config.costs.failed_tx_probability = 0.0
    config.risk.min_seconds_between_entries = 0.0
    config.smart_money.enabled = True
    config.validate()
    return config


def test_copy_experiment_runs_and_reports_by_archetype():
    result = copy_experiment(copy_config(), cycles=200, seed=2, universe_size=15)
    assert "total_return_pct" in result
    assert "by_archetype" in result
    assert result["cycles"] == 200


def test_copy_experiment_is_deterministic():
    a = copy_experiment(copy_config(), cycles=150, seed=3, universe_size=12)
    b = copy_experiment(copy_config(), cycles=150, seed=3, universe_size=12)
    assert a == b


def test_disabling_the_wallet_exit_signal_hurts_farmer_attributed_pnl():
    """The defence that actually neutralises follower-farming."""
    with_signal = [
        copy_experiment(copy_config(), cycles=600, seed=s, universe_size=25)
        for s in range(3)
    ]
    without = [
        copy_experiment(copy_config(), cycles=600, seed=s, universe_size=25, wallet_exit=False)
        for s in range(3)
    ]

    def farmer_pnl(runs):
        return sum(
            (r["by_archetype"].get("farmer") or {}).get("pnl_usd", 0.0) for r in runs
        )

    assert farmer_pnl(with_signal) > farmer_pnl(without)


def test_toggles_are_reflected_in_the_report():
    result = copy_experiment(
        copy_config(), cycles=100, seed=4, universe_size=10,
        drift_gate=False, demotion=False, wallet_exit=False,
    )
    assert result["drift_gate"] is False
    assert result["demotion"] is False
    assert result["wallet_exit"] is False
