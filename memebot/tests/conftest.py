import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from memebot.config import Config
from memebot.models import SafetyReport, TokenSnapshot


@pytest.fixture
def config() -> Config:
    cfg = Config()
    cfg.validate()
    return cfg


def make_snapshot(**overrides) -> TokenSnapshot:
    """A snapshot that passes every default screening filter and clears the default
    momentum score — i.e. a token the bot would actually buy."""
    base = dict(
        mint="Mint1111111111111111111111111111111111111111",
        symbol="GOOD",
        name="Good Token",
        price_usd=0.001,
        liquidity_usd=120_000.0,
        fdv_usd=1_500_000.0,
        volume_m5=20_000.0,
        volume_h1=250_000.0,
        volume_h24=800_000.0,
        buys_m5=70,
        sells_m5=20,
        buys_h1=520,
        sells_h1=300,
        price_change_m5=12.0,
        price_change_h1=20.0,
        price_change_h24=60.0,
        ts=1_700_000_000.0,
    )
    base["pair_created_at_ms"] = int((base["ts"] - 6 * 3600) * 1000)
    base.update(overrides)
    return TokenSnapshot(**base)


def make_safety(**overrides) -> SafetyReport:
    """A safety report that passes every default hard veto."""
    base = dict(
        mint="Mint1111111111111111111111111111111111111111",
        mint_authority_revoked=True,
        freeze_authority_revoked=True,
        lp_locked_pct=100.0,
        top10_holder_pct=18.0,
        rugcheck_score=8.0,
        rugcheck_risks=[],
        sell_route_ok=True,
        sell_price_impact_bps=60.0,
    )
    base.update(overrides)
    return SafetyReport(**base)


@pytest.fixture
def snapshot() -> TokenSnapshot:
    return make_snapshot()


@pytest.fixture
def safety() -> SafetyReport:
    return make_safety()
