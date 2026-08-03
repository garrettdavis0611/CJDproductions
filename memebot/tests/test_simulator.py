import math
import random

import pytest

from memebot.config import Config
from memebot.simulator import (
    REGIMES,
    Regime,
    SimulatedMarket,
    _exit_category,
    _quantile,
    aggregate,
    run_simulation,
)


def fresh_config() -> Config:
    cfg = Config()
    cfg.engine.record_snapshots = False
    cfg.risk.min_seconds_between_entries = 0.0
    cfg.validate()
    return cfg


# ------------------------------------------------------------ the price process


def test_random_walk_regime_is_a_martingale():
    """The null must be a true null: zero expected price change, so any profit the
    strategy shows there is the machinery, not a drift I accidentally baked in."""
    regime = REGIMES["random_walk"]
    market = SimulatedMarket(regime, random.Random(1), universe_size=400)
    start = {m: t.price for m, t in market.tokens.items()}
    for _ in range(120):
        market.step()

    ratios = [market.tokens[m].price / start[m] for m in start]
    mean_ratio = sum(ratios) / len(ratios)
    # 400 tokens x 120 steps is a finite sample, so allow a tolerance band.
    assert 0.85 < mean_ratio < 1.15, mean_ratio


def test_momentum_regime_has_positive_return_autocorrelation():
    market = SimulatedMarket(REGIMES["momentum"], random.Random(2), universe_size=200)
    for _ in range(60):
        market.step()

    pairs = []
    for token in market.tokens.values():
        returns = [
            math.log(token.prices[i + 1] / token.prices[i]) for i in range(len(token.prices) - 1)
        ]
        pairs.extend(zip(returns, returns[1:]))

    n = len(pairs)
    mean_x = sum(p[0] for p in pairs) / n
    mean_y = sum(p[1] for p in pairs) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs) / n
    var_x = sum((x - mean_x) ** 2 for x, _ in pairs) / n
    assert cov / var_x > 0.15  # configured phi is 0.35


def test_mean_reverting_regime_has_negative_autocorrelation():
    market = SimulatedMarket(REGIMES["mean_reverting"], random.Random(3), universe_size=100)
    for _ in range(60):
        market.step()
    token = next(iter(market.tokens.values()))
    returns = [math.log(token.prices[i + 1] / token.prices[i]) for i in range(len(token.prices) - 1)]
    # Sign flips should dominate.
    flips = sum(1 for a, b in zip(returns, returns[1:]) if a * b < 0)
    assert flips > len(returns) * 0.5


def test_rug_drains_liquidity_before_price_fully_collapses():
    """The ordering is the whole reason a liquidity-based exit can beat a price stop."""
    always_rug = Regime(name="t", rug_probability_per_cycle=1.0)
    market = SimulatedMarket(always_rug, random.Random(4), universe_size=1)
    token = next(iter(market.tokens.values()))
    liq0, price0 = token.liquidity, token.price

    market.step()
    liquidity_drop = 1.0 - token.liquidity / liq0
    price_drop = 1.0 - token.price / price0
    assert liquidity_drop > price_drop  # liquidity leaves first
    assert market.rug_events

    market.step()
    assert token.price < price0 * 0.4  # then the price goes


def test_snapshot_price_change_matches_the_price_path():
    market = SimulatedMarket(REGIMES["momentum"], random.Random(5), universe_size=1)
    for _ in range(20):
        market.step()
    token = next(iter(market.tokens.values()))
    snapshot = market.snapshot_for_mint(token.mint)

    expected_m5 = (token.prices[-1] / token.prices[-2] - 1.0) * 100.0
    assert snapshot.price_change_m5 == pytest.approx(expected_m5)
    assert snapshot.price_usd == pytest.approx(token.price)
    assert snapshot.liquidity_usd == pytest.approx(token.liquidity)


def test_market_only_advances_when_stepped():
    """Two reads inside one engine cycle must see identical state."""
    market = SimulatedMarket(REGIMES["mixed"], random.Random(6), universe_size=3)
    market.step()
    first = market.snapshots_for_mints(list(market.tokens))
    second = market.snapshots_for_mints(list(market.tokens))
    assert {m: s.price_usd for m, s in first.items()} == {m: s.price_usd for m, s in second.items()}


def test_discovery_rotates_so_every_token_is_eventually_seen():
    market = SimulatedMarket(REGIMES["mixed"], random.Random(7), universe_size=20)
    seen: set[str] = set()
    for _ in range(20):
        seen.update(market.latest_token_profiles()[:5])
    assert len(seen) > 5


# ------------------------------------------------------------------ simulation


def test_run_simulation_reports_the_expected_shape():
    result = run_simulation(fresh_config(), REGIMES["mixed"], cycles=40, seed=11, universe_size=12)
    for key in (
        "regime", "seed", "cycles", "total_return_pct", "trades",
        "fees_paid_usd", "exit_reasons", "positions_caught_in_rug",
    ):
        assert key in result
    assert result["regime"] == "mixed"
    assert result["cycles"] == 40


def test_run_simulation_is_deterministic_for_a_seed():
    a = run_simulation(fresh_config(), REGIMES["mixed"], cycles=60, seed=21, universe_size=10)
    b = run_simulation(fresh_config(), REGIMES["mixed"], cycles=60, seed=21, universe_size=10)
    assert a == b


def test_different_seeds_give_different_outcomes():
    a = run_simulation(fresh_config(), REGIMES["mixed"], cycles=60, seed=31, universe_size=10)
    b = run_simulation(fresh_config(), REGIMES["mixed"], cycles=60, seed=32, universe_size=10)
    assert a != b


def test_costs_are_always_charged_when_trades_happen():
    result = run_simulation(fresh_config(), REGIMES["momentum"], cycles=200, seed=41, universe_size=20)
    if result["trades"]:
        assert result["fees_paid_usd"] > 0


def test_momentum_regime_outperforms_the_null():
    """The positive control: the strategy must capture autocorrelation when it is
    present, and must not profit when it is absent."""
    momentum = [
        run_simulation(fresh_config(), REGIMES["momentum"], cycles=400, seed=s, universe_size=25)
        for s in range(4)
    ]
    null = [
        run_simulation(fresh_config(), REGIMES["random_walk"], cycles=400, seed=s, universe_size=25)
        for s in range(4)
    ]
    mean_momentum = sum(float(r["total_return_pct"]) for r in momentum) / len(momentum)
    mean_null = sum(float(r["total_return_pct"]) for r in null) / len(null)
    assert mean_momentum > mean_null
    assert mean_null < 2.0  # no free money in a martingale market


def test_risk_caps_bind_during_a_simulation():
    config = fresh_config()
    config.risk.max_concurrent_positions = 2
    result = run_simulation(config, REGIMES["momentum"], cycles=200, seed=51, universe_size=25)
    assert result["open_positions"] <= 2


# ------------------------------------------------------------------- reporting


def test_exit_categories_collapse_parameterised_reasons():
    assert _exit_category("liquidity drained 65% since entry") == "liquidity drained"
    assert _exit_category("stop loss hit (-20.1%)") == "stop loss"
    assert _exit_category("trailing stop (-25.0% off peak)") == "trailing stop"
    assert _exit_category("partial take profit (+50.0%)") == "partial take profit"
    assert _exit_category("max hold 240m reached (+2.0%)") == "max hold"


def test_quantiles():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _quantile(values, 0.0) == 1.0
    assert _quantile(values, 0.5) == 3.0
    assert _quantile(values, 1.0) == 5.0
    assert _quantile([7.0], 0.5) == 7.0
    assert _quantile([], 0.5) == 0.0


def test_aggregate_summarises_a_sweep():
    runs = [
        run_simulation(fresh_config(), REGIMES["mixed"], cycles=60, seed=s, universe_size=10)
        for s in range(3)
    ]
    summary = aggregate(runs)
    assert summary["runs"] == 3
    assert summary["regime"] == "mixed"
    assert "median_return_pct" in summary
    assert summary["p10_return_pct"] <= summary["median_return_pct"] <= summary["p90_return_pct"]
    assert summary["worst_return_pct"] <= summary["p10_return_pct"]


def test_aggregate_handles_an_empty_sweep():
    assert aggregate([]) == {}
