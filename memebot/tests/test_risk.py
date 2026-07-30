from datetime import datetime, timezone

import pytest

from memebot.config import RiskConfig
from memebot.risk import RiskManager

MINT = "Mint1111111111111111111111111111111111111111"


def ts(year=2026, month=7, day=30, hour=12, minute=0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def manager() -> RiskManager:
    return RiskManager(RiskConfig())


def test_position_size_uses_stop_distance_and_respects_the_cap():
    cfg = RiskConfig(starting_equity_usd=10_000, risk_fraction_per_trade=0.02, max_position_usd=100)
    manager = RiskManager(cfg)
    # 2% of $10k = $200 at risk; an 18% stop implies a $1,111 position...
    assert manager.position_size(10_000, stop_loss_pct=0.18) == pytest.approx(100.0)
    # ...but max_position_usd clamps it. The cap, not the formula, is the protection.

    cfg.max_position_usd = 5_000
    assert manager.position_size(10_000, stop_loss_pct=0.18) == pytest.approx(200 / 0.18)


def test_position_size_never_exceeds_equity():
    manager = RiskManager(RiskConfig(max_position_usd=1_000_000))
    assert manager.position_size(50.0, stop_loss_pct=0.01) == pytest.approx(50.0)


def test_first_entry_is_allowed(manager):
    decision = manager.can_open(MINT, equity_usd=1_000, open_positions=0, open_exposure_usd=0, now=ts())
    assert decision.allowed
    assert decision.notional_usd > 0


def test_position_cap_blocks_new_entries(manager):
    decision = manager.can_open(MINT, 1_000, open_positions=3, open_exposure_usd=200, now=ts())
    assert not decision.allowed
    assert "position cap" in decision.reason


def test_exposure_cap_blocks_and_can_partially_size(manager):
    # 35% of $1,000 = $350 cap. With $340 deployed there is $10 of room, which is
    # exactly min_position_usd, so it should be allowed at the reduced size.
    decision = manager.can_open(MINT, 1_000, open_positions=1, open_exposure_usd=340, now=ts())
    assert decision.allowed
    assert decision.notional_usd == pytest.approx(10.0)

    # With $345 deployed only $5 of room remains — below the minimum.
    blocked = manager.can_open(MINT, 1_000, open_positions=1, open_exposure_usd=345, now=ts())
    assert not blocked.allowed
    assert "below minimum" in blocked.reason


def test_exposure_cap_fully_consumed_blocks(manager):
    decision = manager.can_open(MINT, 1_000, open_positions=1, open_exposure_usd=350, now=ts())
    assert not decision.allowed
    assert "exposure cap" in decision.reason


def test_daily_loss_limit_halts_trading(manager):
    now = ts()
    manager.roll_day_if_needed(now)
    manager.record_exit(MINT, realized_pnl_usd=-85.0, now=now, full_exit=True)  # -8.5% of $1,000

    decision = manager.can_open("OtherMint", 1_000, open_positions=0, open_exposure_usd=0, now=now)
    assert not decision.allowed
    assert manager.is_halted()
    assert "daily loss limit" in manager.state.halted_reason


def test_daily_loss_halt_clears_at_utc_midnight(manager):
    day_one = ts(day=30, hour=23)
    manager.roll_day_if_needed(day_one)
    manager.record_exit(MINT, -85.0, day_one, full_exit=True)
    manager.can_open("OtherMint", 1_000, 0, 0, day_one)
    assert manager.is_halted()

    manager.roll_day_if_needed(ts(month=7, day=31, hour=1))
    assert not manager.is_halted()
    assert manager.state.realized_pnl_today_usd == 0.0


def test_consecutive_loss_breaker_survives_the_day_roll(manager):
    now = ts()
    for _ in range(4):
        manager.record_exit(MINT, -1.0, now, full_exit=True)
    manager.can_open("OtherMint", 1_000, 0, 0, now)
    assert manager.is_halted()
    assert "consecutive losses" in manager.state.halted_reason

    # A new calendar day does not make a losing strategy correct.
    manager.roll_day_if_needed(ts(day=31))
    assert manager.is_halted()

    manager.resume()
    assert not manager.is_halted()
    assert manager.state.consecutive_losses == 0


def test_a_win_resets_the_consecutive_loss_counter(manager):
    now = ts()
    manager.record_exit(MINT, -5.0, now, full_exit=True)
    manager.record_exit(MINT, -5.0, now, full_exit=True)
    assert manager.state.consecutive_losses == 2
    manager.record_exit(MINT, 12.0, now, full_exit=True)
    assert manager.state.consecutive_losses == 0


def test_partial_exits_do_not_trigger_cooldown_or_loss_streak(manager):
    now = ts()
    manager.record_exit(MINT, -5.0, now, full_exit=False)
    assert manager.state.consecutive_losses == 0
    assert MINT not in manager.state.cooldowns
    assert manager.state.realized_pnl_today_usd == pytest.approx(-5.0)


def test_cooldown_blocks_reentry_into_the_same_mint(manager):
    now = ts()
    manager.record_exit(MINT, -5.0, now, full_exit=True)

    blocked = manager.can_open(MINT, 1_000, 0, 0, now + 60)
    assert not blocked.allowed
    assert "cooldown" in blocked.reason

    # A different mint is unaffected.
    other = manager.can_open("OtherMint", 1_000, 0, 0, now + 60)
    assert other.allowed

    later = manager.can_open(MINT, 1_000, 0, 0, now + 181 * 60)
    assert later.allowed


def test_entry_throttle_spaces_out_trades(manager):
    now = ts()
    manager.record_entry(now)
    blocked = manager.can_open(MINT, 1_000, 0, 0, now + 10)
    assert not blocked.allowed
    assert "throttle" in blocked.reason
    assert manager.can_open(MINT, 1_000, 0, 0, now + 61).allowed


def test_zero_equity_blocks_everything(manager):
    assert not manager.can_open(MINT, 0.0, 0, 0, ts()).allowed
