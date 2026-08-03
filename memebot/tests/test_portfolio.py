import json

import pytest

from memebot.models import Fill, Side
from memebot.portfolio import Portfolio

MINT = "Mint1111111111111111111111111111111111111111"


def buy_fill(qty=100.0, price=1.0, fee=0.5, ts=1_000.0) -> Fill:
    return Fill(
        mint=MINT, side=Side.BUY, qty=qty, price_usd=price,
        notional_usd=qty * price, fee_usd=fee, slippage_bps=100.0, ts=ts,
    )


def sell_fill(qty=100.0, price=1.5, fee=0.5, ts=2_000.0) -> Fill:
    return Fill(
        mint=MINT, side=Side.SELL, qty=qty, price_usd=price,
        notional_usd=qty * price - fee, fee_usd=fee, slippage_bps=100.0, ts=ts,
    )


def test_buy_reduces_cash_by_notional_plus_fees():
    portfolio = Portfolio(1_000.0)
    portfolio.apply_buy(buy_fill(), symbol="GOOD")
    assert portfolio.cash_usd == pytest.approx(1_000.0 - 100.0 - 0.5)
    assert portfolio.positions[MINT].cost_usd == pytest.approx(100.5)
    assert portfolio.fees_paid_usd == pytest.approx(0.5)


def test_full_exit_realises_pnl_and_closes_the_position():
    portfolio = Portfolio(1_000.0)
    portfolio.apply_buy(buy_fill(), symbol="GOOD")
    trade = portfolio.apply_sell(sell_fill(), exit_reason="take profit")

    assert MINT not in portfolio.positions
    assert trade.pnl_usd == pytest.approx(149.5 - 100.5)
    assert portfolio.realized_pnl_usd == pytest.approx(49.0)
    assert portfolio.equity_usd == pytest.approx(1_049.0)
    assert portfolio.fees_paid_usd == pytest.approx(1.0)


def test_losing_exit_is_recorded_as_a_loss():
    portfolio = Portfolio(1_000.0)
    portfolio.apply_buy(buy_fill(), symbol="GOOD")
    trade = portfolio.apply_sell(sell_fill(price=0.8), exit_reason="stop loss")
    assert trade.pnl_usd < 0
    stats = portfolio.stats()
    assert stats.losses == 1 and stats.wins == 0


def test_partial_exit_splits_the_cost_basis_proportionally():
    portfolio = Portfolio(1_000.0)
    portfolio.apply_buy(buy_fill(qty=100.0, price=1.0, fee=0.5), symbol="GOOD")
    trade = portfolio.apply_sell(sell_fill(qty=50.0, price=1.5, fee=0.25), exit_reason="partial tp")

    position = portfolio.positions[MINT]
    assert position.qty == pytest.approx(50.0)
    assert position.cost_usd == pytest.approx(50.25)
    assert position.partial_tp_done is True
    assert trade.cost_usd == pytest.approx(50.25)
    assert trade.pnl_usd == pytest.approx(74.75 - 50.25)


def test_averaging_into_a_position_recomputes_the_entry_price():
    portfolio = Portfolio(1_000.0)
    portfolio.apply_buy(buy_fill(qty=100.0, price=1.0, fee=0.0), symbol="GOOD")
    portfolio.apply_buy(buy_fill(qty=100.0, price=2.0, fee=0.0), symbol="GOOD")
    position = portfolio.positions[MINT]
    assert position.qty == pytest.approx(200.0)
    assert position.entry_price_usd == pytest.approx(1.5)


def test_overselling_is_refused():
    portfolio = Portfolio(1_000.0)
    portfolio.apply_buy(buy_fill(qty=10.0), symbol="GOOD")
    with pytest.raises(ValueError):
        portfolio.apply_sell(sell_fill(qty=50.0), exit_reason="oops")


def test_selling_an_unheld_token_is_refused():
    portfolio = Portfolio(1_000.0)
    with pytest.raises(KeyError):
        portfolio.apply_sell(sell_fill(), exit_reason="oops")


def test_wrong_side_fills_are_refused():
    portfolio = Portfolio(1_000.0)
    with pytest.raises(ValueError):
        portfolio.apply_buy(sell_fill(), symbol="GOOD")
    with pytest.raises(ValueError):
        portfolio.apply_sell(buy_fill(), exit_reason="x")


def test_unrealised_pnl_tracks_the_mark():
    portfolio = Portfolio(1_000.0)
    portfolio.apply_buy(buy_fill(qty=100.0, price=1.0, fee=0.0), symbol="GOOD")
    portfolio.mark(MINT, 1.25)
    assert portfolio.unrealized_pnl_usd == pytest.approx(25.0)
    assert portfolio.equity_usd == pytest.approx(1_025.0)
    assert portfolio.positions[MINT].peak_price_usd == pytest.approx(1.25)

    portfolio.mark(MINT, 1.10)
    assert portfolio.positions[MINT].peak_price_usd == pytest.approx(1.25)  # peak is sticky
    assert portfolio.positions[MINT].drawdown_from_peak_pct == pytest.approx(-0.12)


def test_max_drawdown_uses_the_realised_equity_curve():
    portfolio = Portfolio(1_000.0)
    for i, (price, ts) in enumerate([(1.5, 10.0), (0.5, 20.0), (0.6, 30.0)]):
        portfolio.apply_buy(buy_fill(qty=100.0, price=1.0, fee=0.0, ts=ts - 1), symbol="GOOD")
        portfolio.apply_sell(sell_fill(qty=100.0, price=price, fee=0.0, ts=ts), exit_reason="x")
    # +50, -50, -40 -> peak 1050, trough 960 -> ~8.57% drawdown.
    assert portfolio.max_drawdown_pct() == pytest.approx(90 / 1050, rel=1e-3)


def test_performance_summary_reports_fees_and_win_rate():
    portfolio = Portfolio(1_000.0)
    portfolio.apply_buy(buy_fill(fee=1.0), symbol="GOOD")
    portfolio.apply_sell(sell_fill(price=1.5, fee=1.0), exit_reason="tp")
    summary = portfolio.performance_summary()
    assert summary["trades"] == 1
    assert summary["win_rate_pct"] == 100.0
    assert summary["fees_paid_usd"] == pytest.approx(2.0)
    assert summary["profit_factor"] == "n/a"  # no losses yet


def test_trade_log_is_written_as_jsonl(tmp_path):
    log_path = tmp_path / "nested" / "trades.jsonl"
    portfolio = Portfolio(1_000.0, trade_log_path=log_path)
    portfolio.apply_buy(buy_fill(), symbol="GOOD")
    portfolio.apply_sell(sell_fill(), exit_reason="take profit")

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 2
    entry, exit_ = (json.loads(line) for line in lines)
    assert entry["event"] == "buy" and entry["side"] == "buy"
    assert exit_["event"] == "sell" and exit_["reason"] == "take profit"
    assert exit_["realized_usd"] == pytest.approx(49.0)
    assert "equity_usd_after" in exit_
