"""End-to-end loop tests with fake data sources — no network."""

import random

import pytest
from conftest import make_safety, make_snapshot

from memebot.config import Config
from memebot.engine import TradingEngine
from memebot.execution.paper import PaperBroker
from memebot.models import SafetyReport
from memebot.portfolio import Portfolio
from memebot.risk import RiskManager

MINT = "Mint1111111111111111111111111111111111111111"


class FakeDexScreener:
    def __init__(self, snapshots_by_cycle):
        self.snapshots_by_cycle = snapshots_by_cycle
        self.cycle = -1

    def latest_token_profiles(self):
        return [MINT]

    def latest_boosted_tokens(self):
        return []

    def snapshots_for_mints(self, mints):
        index = min(self.cycle, len(self.snapshots_by_cycle) - 1)
        current = self.snapshots_by_cycle[max(0, index)]
        return {s.mint: s for s in current if s.mint in set(mints)}

    def close(self):
        pass


class FakeInspector:
    def __init__(self, report: SafetyReport):
        self.report = report
        self.calls = 0

    def inspect(self, snapshot, decimals_hint=None):
        self.calls += 1
        return self.report


class Clock:
    def __init__(self, start=1_700_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def build(config: Config, snapshots_by_cycle, safety=None, clock=None):
    clock = clock or Clock()
    config.engine.record_snapshots = False
    dex = FakeDexScreener(snapshots_by_cycle)
    portfolio = Portfolio(config.risk.starting_equity_usd)
    engine = TradingEngine(
        config=config,
        dexscreener=dex,
        broker=PaperBroker(config.costs, rng=random.Random(0), clock=clock),
        portfolio=portfolio,
        risk=RiskManager(config.risk),
        safety=FakeInspector(safety or make_safety()),
        rpc=None,
        clock=clock,
    )

    original = engine.run_cycle

    def run_cycle():
        dex.cycle += 1
        original()

    engine.run_cycle = run_cycle
    return engine, portfolio, clock, dex


@pytest.fixture
def config():
    cfg = Config()
    cfg.costs.failed_tx_probability = 0.0
    cfg.risk.min_seconds_between_entries = 0.0
    cfg.validate()
    return cfg


def snap(**kw):
    kw.setdefault("ts", 1_700_000_000.0)
    s = make_snapshot(**kw)
    s.pair_created_at_ms = int((1_700_000_000.0 - 6 * 3600) * 1000)
    return s


def test_a_clean_candidate_is_bought(config):
    engine, portfolio, _, _ = build(config, [[snap()]])
    engine.run_cycle()
    assert MINT in portfolio.positions
    assert portfolio.cash_usd < config.risk.starting_equity_usd


def test_a_rejected_candidate_is_not_bought(config):
    engine, portfolio, _, _ = build(
        config, [[snap()]], safety=make_safety(mint_authority_revoked=False)
    )
    engine.run_cycle()
    assert not portfolio.positions


def test_rejections_are_cached_so_we_stop_re_screening(config):
    engine, portfolio, _, _ = build(
        config, [[snap()], [snap()], [snap()]], safety=make_safety(sell_route_ok=False)
    )
    for _ in range(3):
        engine.run_cycle()
    assert not portfolio.positions
    assert engine.safety.calls == 1  # screened once, then cached


def test_stop_loss_closes_the_position_and_realises_the_loss(config):
    engine, portfolio, clock, _ = build(
        config,
        [[snap(price_usd=0.001)], [snap(price_usd=0.0007)]],  # -30%
    )
    engine.run_cycle()
    assert MINT in portfolio.positions

    clock.advance(60)
    engine.run_cycle()
    assert MINT not in portfolio.positions
    assert portfolio.realized_pnl_usd < 0
    assert portfolio.closed_trades[0].exit_reason.startswith("stop loss")


def test_liquidity_drain_triggers_an_emergency_exit(config):
    engine, portfolio, clock, _ = build(
        config,
        [
            [snap(liquidity_usd=200_000.0)],
            [snap(liquidity_usd=60_000.0, price_usd=0.00099)],  # pool gone, price barely moved
        ],
    )
    engine.run_cycle()
    assert MINT in portfolio.positions

    clock.advance(60)
    engine.run_cycle()
    assert MINT not in portfolio.positions
    assert "liquidity drained" in portfolio.closed_trades[0].exit_reason


def test_partial_take_profit_leaves_a_runner(config):
    engine, portfolio, clock, _ = build(
        config, [[snap(price_usd=0.001)], [snap(price_usd=0.0016)]]  # +60%
    )
    engine.run_cycle()
    entry_qty = portfolio.positions[MINT].qty

    clock.advance(60)
    engine.run_cycle()
    assert MINT in portfolio.positions
    assert portfolio.positions[MINT].qty == pytest.approx(entry_qty / 2)
    assert portfolio.realized_pnl_usd > 0
    assert portfolio.positions[MINT].partial_tp_done


def test_position_cap_is_respected(config):
    config.risk.max_concurrent_positions = 1
    mints = [f"Mint{i}" + "1" * 40 for i in range(3)]
    snapshots = [snap(mint=m, symbol=f"T{i}") for i, m in enumerate(mints)]

    engine, portfolio, _, dex = build(config, [snapshots])
    dex.latest_token_profiles = lambda: mints
    engine.run_cycle()
    assert len(portfolio.positions) == 1


def test_halted_risk_manager_blocks_entries_but_still_manages_exits(config):
    engine, portfolio, clock, _ = build(
        config, [[snap(price_usd=0.001)], [snap(price_usd=0.0007)]]
    )
    engine.run_cycle()
    assert MINT in portfolio.positions

    engine.risk.halt("manual test halt")
    clock.advance(60)
    engine.run_cycle()
    # The exit still happened despite the halt.
    assert MINT not in portfolio.positions


def test_missing_market_data_does_not_crash_the_loop(config):
    engine, portfolio, clock, _ = build(config, [[snap()], []])
    engine.run_cycle()
    assert MINT in portfolio.positions
    clock.advance(60)
    engine.run_cycle()  # no data this cycle
    assert MINT in portfolio.positions  # held, not force-sold


def test_a_raising_cycle_does_not_kill_the_runner(config):
    engine, _, _, _ = build(config, [[snap()]])

    def boom():
        raise RuntimeError("data source exploded")

    engine.run_cycle = boom
    engine.run_forever(max_cycles=1, sleeper=lambda _s: None)  # must not propagate


def test_observation_history_is_bounded(config):
    config.engine.snapshot_history = 3
    engine, _, _, _ = build(config, [[snap()]])
    for i in range(10):
        engine.observe([snap(ts=1_700_000_000.0 + i)])
    assert len(engine.history[MINT]) == 3
