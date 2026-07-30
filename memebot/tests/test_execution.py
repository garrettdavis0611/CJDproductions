"""If these tests are wrong, every profit number the bot reports is a lie."""

import random

import pytest

from memebot.config import CostConfig
from memebot.execution.base import CostModel, OrderFailed, OrderRejected
from memebot.execution.paper import PaperBroker
from memebot.models import Side

MINT = "Mint1111111111111111111111111111111111111111"


def never_fails() -> CostConfig:
    return CostConfig(failed_tx_probability=0.0)


@pytest.fixture
def broker() -> PaperBroker:
    return PaperBroker(never_fails(), rng=random.Random(0), clock=lambda: 1_700_000_000.0)


def test_slippage_always_moves_against_us():
    model = CostModel(never_fails())
    buy = model.apply(Side.BUY, 1.0, 100.0, quoted_impact_bps=50.0)
    sell = model.apply(Side.SELL, 1.0, 100.0, quoted_impact_bps=50.0)
    # 50 bps quoted impact + 100 bps allowance = 150 bps each way.
    assert buy.effective_price_usd == pytest.approx(1.015)
    assert sell.effective_price_usd == pytest.approx(0.985)
    assert buy.slippage_bps == sell.slippage_bps == pytest.approx(150.0)


def test_buy_fill_is_reduced_by_fees_and_slippage(broker):
    fill = broker.buy(MINT, notional_usd=100.0, quoted_price_usd=1.0)
    assert fill.side is Side.BUY
    # $100 - 25bps DEX fee = $99.75 spendable, at an effective price of 1.01.
    assert fill.price_usd == pytest.approx(1.01)
    assert fill.qty == pytest.approx(99.75 / 1.01)
    assert fill.qty < 100.0
    assert fill.fee_usd > 0


def test_a_flat_round_trip_loses_money(broker):
    """The whole point of the cost model: price unchanged means you lost."""
    buy = broker.buy(MINT, notional_usd=100.0, quoted_price_usd=1.0)
    sell = broker.sell(MINT, qty=buy.qty, quoted_price_usd=1.0)

    spent = buy.qty * buy.price_usd + buy.fee_usd
    assert sell.notional_usd < spent
    loss_pct = (sell.notional_usd - spent) / spent
    # 100 bps slippage + 25 bps fee per leg -> roughly a 2.5% round-trip haircut.
    assert -0.035 < loss_pct < -0.02


def test_round_trip_at_the_breakeven_price_is_roughly_flat(broker):
    cfg = broker.config
    round_trip_bps = 2 * (cfg.dex_fee_bps + cfg.jupiter_platform_fee_bps + cfg.extra_slippage_bps)
    buy = broker.buy(MINT, notional_usd=100.0, quoted_price_usd=1.0)
    sell = broker.sell(MINT, qty=buy.qty, quoted_price_usd=1.0 * (1 + round_trip_bps / 10_000.0))

    spent = buy.qty * buy.price_usd + buy.fee_usd
    assert sell.notional_usd == pytest.approx(spent, rel=0.01)


def test_a_winning_trade_still_pays_the_costs(broker):
    buy = broker.buy(MINT, notional_usd=100.0, quoted_price_usd=1.0)
    sell = broker.sell(MINT, qty=buy.qty, quoted_price_usd=1.5)
    spent = buy.qty * buy.price_usd + buy.fee_usd
    gross = buy.qty * 1.5
    assert sell.notional_usd < gross  # costs were charged
    assert sell.notional_usd > spent  # but we still made money


def test_failed_transactions_charge_fees_and_fill_nothing():
    always_fails = CostConfig(failed_tx_probability=0.999999)
    broker = PaperBroker(always_fails, rng=random.Random(7))
    with pytest.raises(OrderFailed):
        broker.buy(MINT, notional_usd=100.0, quoted_price_usd=1.0)
    assert broker.fees_paid_usd > 0


def test_network_fee_is_priced_from_lamports():
    cfg = CostConfig(priority_fee_lamports=200_000, base_tx_fee_lamports=5_000, sol_price_usd=200.0)
    # 205,000 lamports = 0.000205 SOL; at $200/SOL that is $0.041.
    assert CostModel(cfg).network_fee_usd() == pytest.approx(0.041)
    assert CostModel(cfg).network_fee_usd(sol_price_usd=400.0) == pytest.approx(0.082)


def test_zero_and_negative_orders_are_rejected(broker):
    with pytest.raises(OrderRejected):
        broker.buy(MINT, notional_usd=0.0, quoted_price_usd=1.0)
    with pytest.raises(OrderRejected):
        broker.buy(MINT, notional_usd=100.0, quoted_price_usd=0.0)
    with pytest.raises(OrderRejected):
        broker.sell(MINT, qty=0.0, quoted_price_usd=1.0)


def test_order_smaller_than_its_own_fees_is_rejected():
    absurd = CostConfig(dex_fee_bps=10_000, failed_tx_probability=0.0)
    broker = PaperBroker(absurd)
    with pytest.raises(OrderRejected):
        broker.buy(MINT, notional_usd=10.0, quoted_price_usd=1.0)


def test_live_broker_refuses_to_arm_without_explicit_acknowledgement():
    from memebot.config import ExecutionConfig
    from memebot.execution.jupiter_broker import JupiterBroker, LiveTradingDisabled

    execution = ExecutionConfig(mode="live")

    with pytest.raises(LiveTradingDisabled, match="UNDERSTAND_THE_RISK"):
        JupiterBroker(execution, CostConfig(), jupiter=None, rpc=None, env={})

    with pytest.raises(LiveTradingDisabled, match="no keypair"):
        JupiterBroker(
            execution, CostConfig(), jupiter=None, rpc=None,
            env={"MEMEBOT_I_UNDERSTAND_THE_RISK": "1"},
        )


def test_live_broker_refuses_when_mode_is_paper():
    from memebot.config import ExecutionConfig
    from memebot.execution.jupiter_broker import JupiterBroker, LiveTradingDisabled

    with pytest.raises(LiveTradingDisabled, match="not 'live'"):
        JupiterBroker(
            ExecutionConfig(mode="paper"), CostConfig(), jupiter=None, rpc=None,
            env={"MEMEBOT_I_UNDERSTAND_THE_RISK": "1", "SOLANA_PRIVATE_KEY": "x"},
        )
