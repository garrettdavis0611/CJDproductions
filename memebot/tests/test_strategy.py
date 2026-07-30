import pytest
from conftest import make_snapshot

from memebot.config import StrategyConfig
from memebot.models import Position
from memebot.strategy.momentum import MomentumStrategy, liquidity_trend_pct

MINT = "Mint1111111111111111111111111111111111111111"


@pytest.fixture
def strategy() -> MomentumStrategy:
    return MomentumStrategy(StrategyConfig())


def history(*snapshots):
    return list(snapshots)


def position(entry=1.0, qty=100.0, entry_ts=0.0, liquidity=120_000.0) -> Position:
    return Position(
        mint=MINT, symbol="GOOD", qty=qty, entry_price_usd=entry,
        entry_ts=entry_ts, cost_usd=qty * entry, entry_liquidity_usd=liquidity,
    )


# ------------------------------------------------------------------- entries


def test_strong_momentum_produces_a_signal(strategy):
    signal = strategy.entry_signal(history(make_snapshot()))
    assert signal is not None
    assert signal.score >= StrategyConfig().min_score
    assert signal.reasons


def test_no_history_no_signal(strategy):
    assert strategy.entry_signal([]) is None


def test_flat_price_is_not_a_signal(strategy):
    assert strategy.entry_signal(history(make_snapshot(price_change_m5=0.2, price_change_h1=0.5))) is None


def test_already_parabolic_is_rejected(strategy):
    """Not buying a token already up 5x this hour is a feature, not a missed trade."""
    assert strategy.entry_signal(history(make_snapshot(price_change_h1=520.0))) is None


def test_sell_dominated_flow_is_rejected(strategy):
    assert strategy.entry_signal(history(make_snapshot(buys_m5=10, sells_m5=50))) is None


def test_too_few_trades_is_rejected(strategy):
    assert strategy.entry_signal(history(make_snapshot(buys_m5=4, sells_m5=1))) is None


def test_draining_liquidity_blocks_entry(strategy):
    early = make_snapshot(liquidity_usd=200_000.0)
    late = make_snapshot(liquidity_usd=100_000.0, ts=early.ts + 300)
    assert strategy.entry_signal(history(early, late)) is None


def test_rising_liquidity_is_fine(strategy):
    early = make_snapshot(liquidity_usd=100_000.0)
    late = make_snapshot(liquidity_usd=130_000.0, ts=early.ts + 300)
    assert strategy.entry_signal(history(early, late)) is not None


def test_liquidity_trend_helper():
    assert liquidity_trend_pct([]) is None
    assert liquidity_trend_pct([make_snapshot()]) is None
    a = make_snapshot(liquidity_usd=100.0)
    b = make_snapshot(liquidity_usd=150.0)
    assert liquidity_trend_pct([a, b]) == pytest.approx(50.0)
    zero = make_snapshot(liquidity_usd=0.0)
    assert liquidity_trend_pct([zero, b]) is None


# --------------------------------------------------------------------- exits


def test_liquidity_drain_exit_beats_everything(strategy):
    """Even on a profitable position, a draining pool means leave now."""
    pos = position(liquidity=200_000.0)
    pos.mark(1.40)  # up 40%
    snapshot = make_snapshot(liquidity_usd=100_000.0, price_usd=1.40)  # pool halved
    decision = strategy.exit_decision(pos, history(snapshot), now=60.0)
    assert decision.should_exit
    assert decision.fraction == 1.0
    assert "liquidity drained" in decision.reason


def test_hard_stop_fires(strategy):
    pos = position()
    pos.mark(0.80)  # -20%, past the 18% stop
    decision = strategy.exit_decision(pos, history(make_snapshot(price_usd=0.80)), now=60.0)
    assert decision.should_exit
    assert "stop loss" in decision.reason
    assert decision.fraction == 1.0


def test_position_within_the_stop_is_held(strategy):
    pos = position()
    pos.mark(0.95)
    assert not strategy.exit_decision(pos, history(make_snapshot(price_usd=0.95)), now=60.0).should_exit


def test_trailing_stop_fires_after_a_run_up(strategy):
    pos = position()
    pos.mark(2.00)  # peak
    pos.mark(1.50)  # -25% off peak, past the 22% trail, still +50% overall
    decision = strategy.exit_decision(pos, history(make_snapshot(price_usd=1.50)), now=60.0)
    assert decision.should_exit
    assert "trailing stop" in decision.reason


def test_trailing_stop_does_not_arm_below_the_entry(strategy):
    """A position that never worked must exit on the hard stop, not the trail."""
    pos = position()
    pos.mark(0.90)  # -10%: inside the hard stop, and no peak above entry
    assert not strategy.exit_decision(pos, history(make_snapshot(price_usd=0.90)), now=60.0).should_exit


def test_partial_take_profit_sells_half_once(strategy):
    pos = position()
    pos.mark(1.50)  # +50%, past the 45% target
    decision = strategy.exit_decision(pos, history(make_snapshot(price_usd=1.50)), now=60.0)
    assert decision.should_exit
    assert decision.fraction == pytest.approx(0.5)
    assert "partial take profit" in decision.reason

    pos.partial_tp_done = True
    again = strategy.exit_decision(pos, history(make_snapshot(price_usd=1.50)), now=60.0)
    assert not again.should_exit  # does not keep selling the remainder


def test_full_take_profit_when_configured_as_such():
    strategy = MomentumStrategy(StrategyConfig(partial_take_profit_fraction=1.0))
    pos = position()
    pos.mark(1.50)
    decision = strategy.exit_decision(pos, history(make_snapshot(price_usd=1.50)), now=60.0)
    assert decision.should_exit and decision.fraction == 1.0


def test_time_stop_closes_stale_positions(strategy):
    pos = position(entry_ts=0.0)
    pos.mark(1.02)  # nothing happening
    decision = strategy.exit_decision(pos, history(make_snapshot(price_usd=1.02)), now=241 * 60)
    assert decision.should_exit
    assert "max hold" in decision.reason


def test_exit_priority_order(strategy):
    """A position that is simultaneously stopped out AND draining exits on the drain."""
    pos = position(liquidity=200_000.0)
    pos.mark(0.5)
    snapshot = make_snapshot(liquidity_usd=50_000.0, price_usd=0.5)
    assert "liquidity drained" in strategy.exit_decision(pos, history(snapshot), now=60.0).reason
