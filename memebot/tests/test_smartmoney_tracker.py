"""Tests for the copy-trade signal gates, wallet-exit detection, and demotion."""

import pytest

from memebot.config import SmartMoneyConfig, StrategyConfig
from memebot.models import Position
from memebot.smartmoney.models import WalletSide, WalletStats
from memebot.smartmoney.tracker import SmartMoneyTracker
from memebot.strategy.copytrade import CopyTradeStrategy

T0 = 1_700_000_000.0
MINT = "Mint1111111111111111111111111111111111111111"


def qualified(wallet: str, score: float = 0.8) -> WalletStats:
    return WalletStats(wallet=wallet, qualified=True, score=score, closed_episodes=30)


def tracker(**overrides) -> SmartMoneyTracker:
    config = SmartMoneyConfig(enabled=True, **overrides)
    return SmartMoneyTracker(config, clock=lambda: T0)


# ------------------------------------------------------------------- following


def test_only_qualified_wallets_are_followed():
    t = tracker()
    assert t.follow(qualified("A"))
    assert not t.follow(WalletStats(wallet="B", qualified=False, disqualifiers=["too few trades"]))
    assert t.active_wallets() == ["A"]


def test_the_tracked_set_is_capped_and_keeps_the_best():
    t = tracker(max_wallets_tracked=2)
    assert t.follow(qualified("A", 0.9))
    assert t.follow(qualified("B", 0.5))
    assert not t.follow(qualified("C", 0.3))  # worse than the weakest
    assert t.follow(qualified("D", 0.8))      # better, so it displaces B
    assert set(t.active_wallets()) == {"A", "D"}


# --------------------------------------------------------------------- signals


def test_consensus_requires_enough_distinct_wallets():
    t = tracker(min_wallets_consensus=2)
    t.follow(qualified("A"))
    t.follow(qualified("B"))

    t.observe_trade("A", MINT, WalletSide.BUY, T0 - 30, 1.0)
    signal = t.consensus(MINT, 1.0, now=T0)
    assert signal is not None and not signal.accepted
    assert "only 1 wallet" in signal.reason

    t.observe_trade("B", MINT, WalletSide.BUY, T0 - 20, 1.0)
    signal = t.consensus(MINT, 1.0, now=T0)
    assert signal.accepted
    assert signal.wallet_count == 2


def test_one_wallet_cannot_manufacture_consensus_by_scaling_in():
    t = tracker(min_wallets_consensus=2)
    t.follow(qualified("A"))
    for offset in (30, 25, 20, 15):
        t.observe_trade("A", MINT, WalletSide.BUY, T0 - offset, 1.0)
    signal = t.consensus(MINT, 1.0, now=T0)
    assert not signal.accepted
    assert signal.wallet_count == 1


def test_a_stale_signal_is_refused():
    t = tracker(min_wallets_consensus=1, max_signal_age_seconds=60.0)
    t.follow(qualified("A"))
    t.observe_trade("A", MINT, WalletSide.BUY, T0 - 600, 1.0)
    signal = t.consensus(MINT, 1.0, now=T0)
    assert not signal.accepted
    assert "already happened" in signal.reason


def test_price_already_run_up_is_refused():
    """The gate that stops us buying someone else's top."""
    t = tracker(min_wallets_consensus=1, max_price_drift_pct=10.0)
    t.follow(qualified("A"))
    t.observe_trade("A", MINT, WalletSide.BUY, T0 - 30, 1.0)

    assert t.consensus(MINT, 1.05, now=T0).accepted           # +5%, fine
    refused = t.consensus(MINT, 1.40, now=T0)                  # +40%, too late
    assert not refused.accepted
    assert "buying their top" in refused.reason


def test_price_far_below_their_entry_is_refused():
    t = tracker(min_wallets_consensus=1, max_adverse_drift_pct=15.0)
    t.follow(qualified("A"))
    t.observe_trade("A", MINT, WalletSide.BUY, T0 - 30, 1.0)
    refused = t.consensus(MINT, 0.7, now=T0)
    assert not refused.accepted
    assert "already underwater" in refused.reason


def test_buys_outside_the_consensus_window_do_not_count():
    t = tracker(min_wallets_consensus=2, consensus_window_seconds=300.0)
    t.follow(qualified("A"))
    t.follow(qualified("B"))
    t.observe_trade("A", MINT, WalletSide.BUY, T0 - 1_000, 1.0)  # too old
    t.observe_trade("B", MINT, WalletSide.BUY, T0 - 30, 1.0)
    assert not t.consensus(MINT, 1.0, now=T0).accepted


def test_no_signal_for_an_untracked_token():
    t = tracker()
    t.follow(qualified("A"))
    assert t.consensus(MINT, 1.0, now=T0) is None


def test_trades_from_unfollowed_wallets_are_ignored():
    t = tracker(min_wallets_consensus=1)
    t.observe_trade("Stranger", MINT, WalletSide.BUY, T0 - 10, 1.0)
    assert t.consensus(MINT, 1.0, now=T0) is None


# ----------------------------------------------------------------- exit signal


def test_exit_pressure_counts_recent_sellers():
    t = tracker(exit_window_seconds=600.0)
    t.follow(qualified("A"))
    t.follow(qualified("B"))
    t.observe_trade("A", MINT, WalletSide.SELL, T0 - 60, 1.0)
    t.observe_trade("B", MINT, WalletSide.SELL, T0 - 5_000, 1.0)  # outside the window

    count, wallets = t.exit_pressure(MINT, now=T0)
    assert count == 1 and wallets == ["A"]


# ---------------------------------------------------------- attribution/demote


def test_losses_are_attributed_and_the_wallet_is_demoted():
    t = tracker(min_attributed_trades=3, demote_below_pnl_usd=0.0)
    t.follow(qualified("Farmer"))

    for i in range(3):
        t.credit_entry(MINT, ["Farmer"])
        demoted = t.record_outcome(MINT, -10.0)
    assert demoted == ["Farmer"]
    assert not t.is_following("Farmer")
    record = t.attribution["Farmer"]
    assert record.copied_trades == 3
    assert record.realized_pnl_usd == pytest.approx(-30.0)
    assert "costs us money" in record.demoted_reason


def test_a_profitable_wallet_is_not_demoted():
    t = tracker(min_attributed_trades=3)
    t.follow(qualified("Good"))
    for _ in range(5):
        t.credit_entry(MINT, ["Good"])
        t.record_outcome(MINT, +8.0)
    assert t.is_following("Good")


def test_demotion_waits_for_a_minimum_sample():
    """One bad copied trade is not evidence."""
    t = tracker(min_attributed_trades=5)
    t.follow(qualified("A"))
    for _ in range(4):
        t.credit_entry(MINT, ["A"])
        assert t.record_outcome(MINT, -20.0) == []
    assert t.is_following("A")


def test_pnl_is_split_across_the_wallets_that_triggered_the_entry():
    t = tracker(min_attributed_trades=1, demote_below_pnl_usd=-1000.0)
    t.follow(qualified("A"))
    t.follow(qualified("B"))
    t.credit_entry(MINT, ["A", "B"])
    t.record_outcome(MINT, -10.0)
    assert t.attribution["A"].realized_pnl_usd == pytest.approx(-5.0)
    assert t.attribution["B"].realized_pnl_usd == pytest.approx(-5.0)


def test_a_low_win_rate_on_copied_trades_also_demotes():
    t = tracker(min_attributed_trades=5, demote_below_pnl_usd=-10_000.0, demote_below_win_rate=0.5)
    t.follow(qualified("A"))
    # Net positive overall, but we lose most of the time — a shape worth avoiding.
    outcomes = [-1.0, -1.0, -1.0, -1.0, +100.0]
    demoted: list[str] = []
    for pnl in outcomes:
        t.credit_entry(MINT, ["A"])
        demoted += t.record_outcome(MINT, pnl)
    assert demoted == ["A"]
    assert "we won only" in t.attribution["A"].demoted_reason


def test_outcome_without_a_credited_entry_is_a_no_op():
    t = tracker()
    assert t.record_outcome(MINT, -50.0) == []


def test_a_demoted_wallet_is_not_re_followed():
    t = tracker(min_attributed_trades=1, demote_below_pnl_usd=0.0)
    t.follow(qualified("A"))
    t.credit_entry(MINT, ["A"])
    t.record_outcome(MINT, -5.0)
    assert not t.follow(qualified("A", 0.99))


def test_demotions_survive_a_restart(tmp_path):
    """Forgetting which wallets cost us money on every restart would void the defence."""
    path = tmp_path / "state.json"
    first = SmartMoneyTracker(
        SmartMoneyConfig(enabled=True, min_attributed_trades=1, demote_below_pnl_usd=0.0),
        state_path=path, clock=lambda: T0,
    )
    first.follow(qualified("A"))
    first.credit_entry(MINT, ["A"])
    first.record_outcome(MINT, -5.0)
    first.save()

    second = SmartMoneyTracker(
        SmartMoneyConfig(enabled=True), state_path=path, clock=lambda: T0
    )
    second.load()
    assert second.attribution["A"].demoted
    assert not second.follow(qualified("A"))


def test_load_tolerates_a_missing_or_corrupt_state_file(tmp_path):
    path = tmp_path / "missing.json"
    t = SmartMoneyTracker(SmartMoneyConfig(enabled=True), state_path=path)
    t.load()  # no file
    path.write_text("{not json")
    t.load()  # corrupt
    assert t.attribution == {}


# ------------------------------------------------------------ strategy wiring


def strategy_with(t: SmartMoneyTracker) -> CopyTradeStrategy:
    return CopyTradeStrategy(StrategyConfig(), t.config, t)


def make_snapshot(price=1.0, ts=T0, liquidity=120_000.0):
    from conftest import make_snapshot as base

    return base(mint=MINT, price_usd=price, ts=ts, liquidity_usd=liquidity)


def test_copy_strategy_signals_on_accepted_consensus():
    t = tracker(min_wallets_consensus=2)
    t.follow(qualified("A"))
    t.follow(qualified("B"))
    t.observe_trade("A", MINT, WalletSide.BUY, T0 - 30, 1.0)
    t.observe_trade("B", MINT, WalletSide.BUY, T0 - 20, 1.0)

    signal = strategy_with(t).entry_signal([make_snapshot(price=1.02)])
    assert signal is not None
    assert signal.score > 0
    assert any("tracked wallets bought" in r for r in signal.reasons)


def test_copy_strategy_is_silent_without_consensus():
    t = tracker(min_wallets_consensus=2)
    t.follow(qualified("A"))
    t.observe_trade("A", MINT, WalletSide.BUY, T0 - 30, 1.0)
    assert strategy_with(t).entry_signal([make_snapshot()]) is None


def test_copy_strategy_records_wallets_for_attribution():
    t = tracker(min_wallets_consensus=1)
    t.follow(qualified("A"))
    t.observe_trade("A", MINT, WalletSide.BUY, T0 - 10, 1.0)
    strategy = strategy_with(t)
    strategy.entry_signal([make_snapshot()])
    assert strategy.wallets_for(MINT) == ["A"]


def test_tracked_wallets_selling_forces_an_exit():
    t = tracker(min_wallets_selling=1)
    t.follow(qualified("A"))
    t.observe_trade("A", MINT, WalletSide.SELL, T0 - 30, 1.0)

    position = Position(
        mint=MINT, symbol="X", qty=100, entry_price_usd=1.0,
        entry_ts=T0 - 600, cost_usd=100.0, entry_liquidity_usd=120_000.0,
    )
    position.mark(1.05)  # in profit, nothing else would exit
    decision = strategy_with(t).exit_decision(position, [make_snapshot(price=1.05)], now=T0)
    assert decision.should_exit
    assert "sold" in decision.reason
    assert decision.fraction == 1.0


def test_liquidity_drain_still_outranks_the_wallet_exit_signal():
    t = tracker(min_wallets_selling=1)
    t.follow(qualified("A"))
    t.observe_trade("A", MINT, WalletSide.SELL, T0 - 30, 1.0)

    position = Position(
        mint=MINT, symbol="X", qty=100, entry_price_usd=1.0,
        entry_ts=T0 - 600, cost_usd=100.0, entry_liquidity_usd=200_000.0,
    )
    position.mark(1.0)
    decision = strategy_with(t).exit_decision(
        position, [make_snapshot(price=1.0, liquidity=50_000.0)], now=T0
    )
    assert "liquidity drained" in decision.reason


def test_wallet_exit_can_be_disabled():
    t = tracker(min_wallets_selling=1, exit_on_wallet_exit=False)
    t.follow(qualified("A"))
    t.observe_trade("A", MINT, WalletSide.SELL, T0 - 30, 1.0)
    position = Position(
        mint=MINT, symbol="X", qty=100, entry_price_usd=1.0,
        entry_ts=T0 - 600, cost_usd=100.0, entry_liquidity_usd=120_000.0,
    )
    position.mark(1.05)
    assert not strategy_with(t).exit_decision(position, [make_snapshot(price=1.05)], now=T0).should_exit
