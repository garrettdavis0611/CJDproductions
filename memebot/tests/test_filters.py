"""The screening tests are the most important tests in this project: each one
corresponds to a specific way people lose their money."""

from conftest import make_safety, make_snapshot

from memebot.config import Config
from memebot.screening.filters import FilterContext, screen


def run(cfg: Config, snapshot=None, safety=None, **ctx_kwargs):
    return screen(
        FilterContext(
            snapshot=snapshot or make_snapshot(),
            safety=safety or make_safety(),
            config=cfg.screening,
            **ctx_kwargs,
        )
    )


def test_clean_token_passes(config):
    result = run(config)
    assert result.passed, result.hard_fails
    assert not result.hard_fails


# ------------------------------------------------------- on-chain rug vectors


def test_live_mint_authority_is_a_hard_fail(config):
    result = run(config, safety=make_safety(mint_authority_revoked=False))
    assert not result.passed
    assert any("mint authority" in f for f in result.hard_fails)


def test_live_freeze_authority_is_a_hard_fail(config):
    result = run(config, safety=make_safety(freeze_authority_revoked=False))
    assert not result.passed
    assert any("freeze authority" in f for f in result.hard_fails)


def test_unlocked_liquidity_is_a_hard_fail(config):
    result = run(config, safety=make_safety(lp_locked_pct=12.0))
    assert not result.passed
    assert any("LP locked" in f for f in result.hard_fails)


def test_whale_concentration_is_a_hard_fail(config):
    result = run(config, safety=make_safety(top10_holder_pct=71.0))
    assert not result.passed
    assert any("top-10 holders" in f for f in result.hard_fails)


def test_honeypot_with_no_sell_route_is_a_hard_fail(config):
    result = run(config, safety=make_safety(sell_route_ok=False, sell_price_impact_bps=None))
    assert not result.passed
    assert any("NO SELL ROUTE" in f for f in result.hard_fails)


def test_unexitable_sell_impact_is_a_hard_fail(config):
    result = run(config, safety=make_safety(sell_price_impact_bps=1500.0))
    assert not result.passed
    assert any("exit price impact" in f for f in result.hard_fails)


def test_high_risk_score_is_a_hard_fail(config):
    result = run(config, safety=make_safety(rugcheck_score=88.0))
    assert not result.passed
    assert any("risk score" in f for f in result.hard_fails)


def test_danger_level_risk_flag_is_a_hard_fail(config):
    result = run(config, safety=make_safety(rugcheck_risks=["Mutable metadata [danger]"]))
    assert not result.passed
    assert any("danger-level" in f for f in result.hard_fails)


def test_non_danger_risk_flag_is_only_a_soft_flag(config):
    result = run(config, safety=make_safety(rugcheck_risks=["Low amount of LP providers [warn]"]))
    assert result.passed
    assert any("risk flag" in f for f in result.soft_flags)


# ------------------------------------------------------------- unknown = fail


def test_unknown_facts_fail_closed_by_default(config):
    result = run(
        config,
        safety=make_safety(
            mint_authority_revoked=None,
            freeze_authority_revoked=None,
            lp_locked_pct=None,
            top10_holder_pct=None,
            rugcheck_score=None,
            sell_route_ok=None,
        ),
    )
    assert not result.passed
    assert len(result.hard_fails) >= 5
    assert all("unknown treated as failure" in f for f in result.hard_fails)


def test_unknown_can_be_downgraded_to_soft_when_explicitly_configured(config):
    config.screening.unknown_is_failure = False
    result = run(config, safety=make_safety(mint_authority_revoked=None, lp_locked_pct=None))
    assert result.passed
    assert len(result.soft_flags) >= 2


# ---------------------------------------------------------- market structure


def test_thin_liquidity_is_a_hard_fail(config):
    result = run(config, snapshot=make_snapshot(liquidity_usd=4_000.0, volume_h1=1_000.0))
    assert not result.passed
    assert any("liquidity" in f for f in result.hard_fails)


def test_zero_liquidity_short_circuits(config):
    result = run(config, snapshot=make_snapshot(liquidity_usd=0.0))
    assert not result.passed
    assert "no liquidity reported" in result.hard_fails


def test_wash_trading_turnover_is_a_hard_fail(config):
    # $6M of hourly volume against $120k of liquidity is 50x turnover.
    result = run(config, snapshot=make_snapshot(volume_h1=6_000_000.0))
    assert not result.passed
    assert any("wash trading" in f for f in result.hard_fails)


def test_brand_new_pair_is_a_hard_fail(config):
    fresh = make_snapshot()
    fresh.pair_created_at_ms = int((fresh.ts - 120) * 1000)  # two minutes old
    result = run(config, snapshot=fresh)
    assert not result.passed
    assert any("snipers and bundlers" in f for f in result.hard_fails)


def test_stale_pair_is_a_hard_fail(config):
    old = make_snapshot()
    old.pair_created_at_ms = int((old.ts - 30 * 86400) * 1000)
    result = run(config, snapshot=old)
    assert not result.passed
    assert any("old" in f for f in result.hard_fails)


def test_low_volume_is_a_hard_fail(config):
    result = run(config, snapshot=make_snapshot(volume_h24=1_000.0, volume_h1=100.0))
    assert not result.passed
    assert any("24h volume" in f for f in result.hard_fails)


def test_draining_liquidity_is_a_hard_fail(config):
    result = run(config, liquidity_trend_pct=-35.0)
    assert not result.passed
    assert any("draining" in f for f in result.hard_fails)


def test_mild_liquidity_decline_is_only_a_soft_flag(config):
    result = run(config, liquidity_trend_pct=-8.0)
    assert result.passed
    assert any("liquidity down" in f for f in result.soft_flags)


def test_excessive_entry_impact_is_a_hard_fail(config):
    result = run(config, intended_buy_impact_bps=950.0)
    assert not result.passed
    assert any("entry price impact" in f for f in result.hard_fails)


# ---------------------------------------------------------------- denylisting


def test_blocked_mint_is_rejected(config):
    snap = make_snapshot()
    config.screening.blocked_mints = [snap.mint]
    result = run(config, snapshot=snap)
    assert not result.passed
    assert "mint is on the blocklist" in result.hard_fails


def test_blocked_symbol_substring_is_rejected(config):
    result = run(config, snapshot=make_snapshot(symbol="SCAMCOIN"))
    assert not result.passed
    assert any("blocked substring" in f for f in result.hard_fails)


def test_all_hard_fails_are_reported_not_just_the_first(config):
    result = run(
        config,
        snapshot=make_snapshot(liquidity_usd=1_000.0, volume_h24=10.0, volume_h1=5.0),
        safety=make_safety(mint_authority_revoked=False, lp_locked_pct=0.0),
    )
    assert not result.passed
    assert len(result.hard_fails) >= 4
    assert "; " in result.reason()
