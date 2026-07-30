import json

import pytest
from conftest import make_snapshot

from memebot.backtest import load_snapshots, run_backtest
from memebot.config import Config, ScreeningConfig
from memebot.datasources.jupiter import Quote
from memebot.datasources.solana_rpc import MintInfo
from memebot.screening.safety import SafetyInspector

MINT = "Mint1111111111111111111111111111111111111111"


class FakeRpc:
    def __init__(self, info=None, holders=None, raises=False):
        self.info = info
        self.holders = holders
        self.raises = raises

    def mint_info(self, _mint):
        if self.raises:
            raise RuntimeError("rpc down")
        return self.info

    def top_holder_share(self, _mint, top_n=10):
        return self.holders


class FakeRugCheck:
    def __init__(self, summary=None, raises=False):
        self.summary_value = summary
        self.raises = raises

    def summary(self, _mint):
        if self.raises:
            raise RuntimeError("rugcheck down")
        return self.summary_value


class FakeJupiter:
    def __init__(self, quote=None, raises=False):
        self.quote_value = quote
        self.raises = raises
        self.last_amount = None

    def probe_sell_route(self, _mint, amount, slippage_bps=300):
        if self.raises:
            raise RuntimeError("jupiter down")
        self.last_amount = amount
        return self.quote_value


def a_quote(impact_bps=80.0) -> Quote:
    return Quote(
        input_mint=MINT, output_mint="So1", in_amount=1000, out_amount=900,
        other_amount_threshold=890, price_impact_bps=impact_bps,
        slippage_bps=300, route_labels=["Raydium"], raw={},
    )


# ------------------------------------------------------------------- inspector


def test_inspector_collects_every_fact():
    inspector = SafetyInspector(
        ScreeningConfig(),
        rpc=FakeRpc(MintInfo(MINT, None, None, 6, 1e9), holders=22.0),
        rugcheck=FakeRugCheck(type("S", (), {
            "score": 12.0, "risks": ["x"], "lp_locked_pct": 100.0, "top_holders_pct": 19.0,
        })()),
        jupiter=FakeJupiter(a_quote()),
    )
    report = inspector.inspect(make_snapshot(mint=MINT, price_usd=0.001))

    assert report.mint_authority_revoked is True
    assert report.freeze_authority_revoked is True
    assert report.lp_locked_pct == pytest.approx(100.0)
    assert report.rugcheck_score == pytest.approx(12.0)
    assert report.sell_route_ok is True
    assert report.sell_price_impact_bps == pytest.approx(80.0)
    assert not report.errors
    # RugCheck's holder figure wins: it excludes LP vaults.
    assert report.top10_holder_pct == pytest.approx(19.0)


def test_inspector_records_a_missing_sell_route_as_a_honeypot():
    inspector = SafetyInspector(
        ScreeningConfig(),
        rpc=FakeRpc(MintInfo(MINT, None, None, 6, 1e9)),
        jupiter=FakeJupiter(quote=None),
    )
    report = inspector.inspect(make_snapshot(mint=MINT, price_usd=0.001))
    assert report.sell_route_ok is False


def test_inspector_sizes_the_sell_probe_from_price_and_decimals():
    jupiter = FakeJupiter(a_quote())
    config = ScreeningConfig(sell_probe_usd=25.0)
    inspector = SafetyInspector(
        config, rpc=FakeRpc(MintInfo(MINT, None, None, 6, 1e9)), jupiter=jupiter
    )
    inspector.inspect(make_snapshot(mint=MINT, price_usd=0.0005))
    # $25 / $0.0005 = 50,000 tokens; at 6 decimals that is 50e9 raw units.
    assert jupiter.last_amount == 50_000 * 10**6


def test_inspector_survives_every_source_failing():
    inspector = SafetyInspector(
        ScreeningConfig(),
        rpc=FakeRpc(raises=True),
        rugcheck=FakeRugCheck(raises=True),
        jupiter=FakeJupiter(raises=True),
    )
    report = inspector.inspect(make_snapshot(mint=MINT, price_usd=0.001))
    assert report.mint_authority_revoked is None
    assert report.rugcheck_score is None
    assert len(report.errors) >= 3
    # And with unknown_is_failure (the default) this token will be rejected downstream.


def test_inspector_skips_the_probe_without_decimals():
    inspector = SafetyInspector(ScreeningConfig(), rpc=None, jupiter=FakeJupiter(a_quote()))
    report = inspector.inspect(make_snapshot(mint=MINT, price_usd=0.001))
    assert report.sell_route_ok is None
    assert any("unknown decimals" in e for e in report.errors)


# -------------------------------------------------------------------- backtest


def test_load_snapshots_reads_jsonl_and_skips_junk(tmp_path):
    path = tmp_path / "snapshots.jsonl"
    good = {"mint": MINT, "symbol": "AAA", "price_usd": 0.001, "ts": 5.0}
    older = {"mint": MINT, "symbol": "AAA", "price_usd": 0.002, "ts": 1.0}
    path.write_text("\n".join([json.dumps(good), "not json", "", json.dumps(older)]))

    snapshots = load_snapshots(path)
    assert len(snapshots) == 2
    assert [s.ts for s in snapshots] == [1.0, 5.0]  # sorted by time


def test_load_snapshots_ignores_unexpected_fields(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps({"mint": MINT, "price_usd": 1.0, "ts": 1.0, "future_field": 9}))
    assert load_snapshots(path)[0].mint == MINT


def test_backtest_needs_data():
    with pytest.raises(ValueError):
        run_backtest(Config(), [])


def test_backtest_runs_a_full_trade_and_reports_costs():
    config = Config()
    config.costs.failed_tx_probability = 0.0
    config.risk.min_seconds_between_entries = 0.0
    config.validate()

    base = 1_700_000_000.0
    stream = []
    for i, price in enumerate([0.001, 0.001, 0.0016, 0.0016]):
        snapshot = make_snapshot(mint=MINT, price_usd=price, ts=base + i * 60)
        snapshot.pair_created_at_ms = int((base - 6 * 3600) * 1000)
        stream.append(snapshot)

    result = run_backtest(config, stream)
    summary = result.summary()
    assert summary["snapshots_replayed"] == 4
    assert summary["unique_mints"] == 1
    assert summary["trades"] >= 1
    assert summary["fees_paid_usd"] > 0  # costs were charged, not assumed away


def test_backtest_is_deterministic_for_a_given_seed():
    config = Config()
    config.risk.min_seconds_between_entries = 0.0
    config.validate()
    base = 1_700_000_000.0
    stream = []
    for i, price in enumerate([0.001, 0.0012, 0.0007, 0.0009] * 3):
        snapshot = make_snapshot(mint=MINT, price_usd=price, ts=base + i * 60)
        snapshot.pair_created_at_ms = int((base - 6 * 3600) * 1000)
        stream.append(snapshot)

    first = run_backtest(config, stream, seed=99).summary()
    second = run_backtest(config, stream, seed=99).summary()
    assert first == second


def test_backtest_marks_open_positions_at_the_last_seen_price():
    config = Config()
    config.costs.failed_tx_probability = 0.0
    config.risk.min_seconds_between_entries = 0.0
    config.validate()

    base = 1_700_000_000.0
    stream = []
    for i, price in enumerate([0.001, 0.0011]):
        snapshot = make_snapshot(mint=MINT, price_usd=price, ts=base + i * 60)
        snapshot.pair_created_at_ms = int((base - 6 * 3600) * 1000)
        stream.append(snapshot)

    result = run_backtest(config, stream)
    if result.portfolio.positions:
        assert result.portfolio.positions[MINT].last_price_usd == pytest.approx(0.0011)
