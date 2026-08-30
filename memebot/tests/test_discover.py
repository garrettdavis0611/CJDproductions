"""Tests for chain-derived wallet discovery.

The point of discovery is to avoid taking addresses from listicles and Telegram
channels — an address someone is publicising is often a wallet that *wants* to be
followed. These tests check that candidates come from actual buy activity and that
discovery only ever proposes: qualification still decides.
"""

import time

import pytest
from conftest import make_snapshot

from memebot.config import SmartMoneyConfig
from memebot.smartmoney.discover import (
    Candidate,
    audit_candidates,
    buyers_of_pool,
    find_winning_tokens,
    gather_candidates,
)
from memebot.smartmoney.models import WalletSide, WalletTrade

DAY = 86_400.0
T0 = 1_700_000_000.0
LAMPORTS = 1_000_000_000


class FakeDexScreener:
    def __init__(self, snapshots):
        self.snapshots = {s.mint: s for s in snapshots}

    def latest_boosted_tokens(self):
        return list(self.snapshots)

    def latest_token_profiles(self):
        return []

    def snapshots_for_mints(self, mints):
        return {m: s for m, s in self.snapshots.items() if m in set(mints)}

    def search(self, _query):
        return []


class FakeRpc:
    """Serves a scripted signature list and transactions per pool address."""

    def __init__(self, pools):
        self.pools = pools  # pair_address -> list[tx dict]

    def signatures_for_address(self, address, limit=100, before=None):
        txs = self.pools.get(address, [])
        return [{"signature": f"{address}-{i}"} for i in range(len(txs))][:limit]

    def get_transaction(self, signature):
        address, _, index = signature.rpartition("-")
        txs = self.pools.get(address, [])
        try:
            return txs[int(index)]
        except (ValueError, IndexError):
            return None


def swap_tx(wallet, mint, sol_delta, token_delta, block_time=T0):
    """A transaction where `wallet` swaps SOL for `mint` (or back)."""
    def balances(amount):
        return [{
            "accountIndex": 1, "mint": mint, "owner": wallet,
            "uiTokenAmount": {"uiAmount": amount, "decimals": 6, "amount": str(int(amount * 1e6))},
        }]

    return {
        "blockTime": block_time,
        "transaction": {"message": {"accountKeys": [{"pubkey": wallet}, {"pubkey": "pool"}]}},
        "meta": {
            "err": None,
            "preBalances": [10 * LAMPORTS, 0],
            "postBalances": [int((10 + sol_delta) * LAMPORTS), 0],
            "preTokenBalances": balances(0.0 if token_delta > 0 else abs(token_delta)),
            "postTokenBalances": balances(token_delta if token_delta > 0 else 0.0),
        },
    }


# ------------------------------------------------------------- winner selection


def test_only_liquid_recent_winners_are_probed():
    winners = [
        make_snapshot(mint="WIN1", pair_address="p1", liquidity_usd=200_000, price_change_h24=120.0),
        make_snapshot(mint="FLAT", pair_address="p2", liquidity_usd=200_000, price_change_h24=3.0),
        make_snapshot(mint="THIN", pair_address="p3", liquidity_usd=2_000, price_change_h24=300.0),
        make_snapshot(mint="NOPAIR", pair_address="", liquidity_usd=200_000, price_change_h24=90.0),
    ]
    found = find_winning_tokens(FakeDexScreener(winners), min_liquidity_usd=50_000, min_gain_h24_pct=40.0)
    assert [s.mint for s in found] == ["WIN1"]


def test_winners_are_ranked_by_gain():
    winners = [
        make_snapshot(mint="A", pair_address="pa", liquidity_usd=100_000, price_change_h24=60.0),
        make_snapshot(mint="B", pair_address="pb", liquidity_usd=100_000, price_change_h24=250.0),
    ]
    found = find_winning_tokens(FakeDexScreener(winners))
    assert [s.mint for s in found] == ["B", "A"]


def test_discovery_survives_a_failing_data_source():
    class Broken(FakeDexScreener):
        def latest_boosted_tokens(self):
            raise RuntimeError("down")

        def search(self, _q):
            raise RuntimeError("down")

    assert find_winning_tokens(Broken([])) == []


# ------------------------------------------------------------------ pool buyers


def test_buyers_of_a_pool_are_extracted():
    txs = [
        swap_tx("BuyerA", "WIN1", -2.0, 1_000.0),
        swap_tx("BuyerB", "WIN1", -5.0, 2_500.0),
        swap_tx("SellerC", "WIN1", +3.0, -1_500.0),   # a sell, not a candidate
    ]
    buyers = buyers_of_pool(FakeRpc({"p1": txs}), "p1", "WIN1", min_sol_size=0.5)
    assert set(buyers) == {"BuyerA", "BuyerB"}
    assert buyers["BuyerB"] == pytest.approx(5.0)


def test_dust_buys_are_ignored():
    """A wallet risking 0.01 SOL is not expressing a view worth copying."""
    txs = [swap_tx("Dust", "WIN1", -0.02, 5.0), swap_tx("Real", "WIN1", -3.0, 1_000.0)]
    buyers = buyers_of_pool(FakeRpc({"p1": txs}), "p1", "WIN1", min_sol_size=0.5)
    assert set(buyers) == {"Real"}


def test_buys_of_a_different_mint_are_ignored():
    txs = [swap_tx("Other", "SOMETHINGELSE", -3.0, 1_000.0)]
    assert buyers_of_pool(FakeRpc({"p1": txs}), "p1", "WIN1") == {}


def test_repeat_buys_accumulate():
    txs = [swap_tx("Whale", "WIN1", -2.0, 500.0), swap_tx("Whale", "WIN1", -4.0, 900.0)]
    buyers = buyers_of_pool(FakeRpc({"p1": txs}), "p1", "WIN1")
    assert buyers["Whale"] == pytest.approx(6.0)


# --------------------------------------------------------------- shortlisting


def two_winner_market():
    snapshots = [
        make_snapshot(mint="WIN1", pair_address="p1", liquidity_usd=200_000, price_change_h24=120.0),
        make_snapshot(mint="WIN2", pair_address="p2", liquidity_usd=200_000, price_change_h24=90.0),
    ]
    pools = {
        "p1": [swap_tx("Repeat", "WIN1", -3.0, 900.0), swap_tx("OneOff", "WIN1", -2.0, 600.0)],
        "p2": [swap_tx("Repeat", "WIN2", -4.0, 800.0), swap_tx("Other", "WIN2", -1.0, 300.0)],
    }
    return FakeDexScreener(snapshots), FakeRpc(pools)


def test_only_wallets_on_several_independent_winners_are_shortlisted():
    """One correct call is noise. Repetition across independent events is a prior."""
    dex, rpc = two_winner_market()
    shortlist = gather_candidates(dex, rpc, min_appearances=2)
    assert [c.wallet for c in shortlist] == ["Repeat"]
    assert shortlist[0].appearances == 2
    assert shortlist[0].total_sol_bought == pytest.approx(7.0)


def test_lowering_min_appearances_admits_one_off_buyers():
    dex, rpc = two_winner_market()
    shortlist = gather_candidates(dex, rpc, min_appearances=1)
    assert {c.wallet for c in shortlist} == {"Repeat", "OneOff", "Other"}
    assert shortlist[0].wallet == "Repeat"  # still ranked first


def test_no_winners_means_no_candidates():
    assert gather_candidates(FakeDexScreener([]), FakeRpc({}), min_appearances=1) == []


def test_a_broken_pool_read_does_not_abort_discovery():
    dex, rpc = two_winner_market()

    def explode(address, limit=100, before=None):
        if address == "p1":
            raise RuntimeError("rpc down")
        return FakeRpc.signatures_for_address(rpc, address, limit, before)

    rpc.signatures_for_address = explode
    shortlist = gather_candidates(dex, rpc, min_appearances=1)
    assert {c.wallet for c in shortlist} == {"Repeat", "Other"}  # p2 still processed


# -------------------------------------------------------------------- auditing


class FakeFeed:
    def __init__(self, histories):
        self.histories = histories
        self.calls: list[tuple[str, float | None]] = []

    def recent_trades(self, wallet, limit=200, since_ts=None, max_pages=None):
        self.calls.append((wallet, since_ts))
        return self.histories.get(wallet, [])


def steady_history(wallet, span_days=200.0, episodes=36, ratio_win=1.5, now=T0):
    start = now - span_days * DAY
    trades = []
    for i in range(episodes):
        mint = f"T{i}"
        ts = start + (i / max(1, episodes - 1)) * span_days * DAY
        ratio = ratio_win if (i % 10) < 6 else 0.85
        trades += [
            WalletTrade(wallet, mint, WalletSide.BUY, 100.0, 1.0, ts),
            WalletTrade(wallet, mint, WalletSide.SELL, 100.0, ratio, ts + 3600),
        ]
    return trades


def test_audit_separates_qualified_from_rejected():
    now = time.time()
    feed = FakeFeed({
        "Good": steady_history("Good", now=now),
        "Bad": steady_history("Bad", span_days=10.0, episodes=8, now=now),
    })
    candidates = [Candidate(wallet="Good"), Candidate(wallet="Bad")]
    qualified, rejected = audit_candidates(candidates, feed, SmartMoneyConfig(enabled=True), now=now)

    assert [c.wallet for c, _ in qualified] == ["Good"]
    assert [c.wallet for c, _ in rejected] == ["Bad"]
    assert rejected[0][1].disqualifiers


def test_audit_requests_the_full_lookback_window():
    """Judging six-month consistency needs six months of data fetched."""
    now = time.time()
    config = SmartMoneyConfig(enabled=True, lookback_days=210.0)
    feed = FakeFeed({"W": steady_history("W", now=now)})
    audit_candidates([Candidate(wallet="W")], feed, config, now=now)

    _wallet, since = feed.calls[0]
    assert since == pytest.approx(now - 210.0 * DAY)


def test_audit_respects_the_limit():
    now = time.time()
    feed = FakeFeed({})
    candidates = [Candidate(wallet=f"W{i}") for i in range(10)]
    audit_candidates(candidates, feed, SmartMoneyConfig(enabled=True), now=now, max_audits=3)
    assert len(feed.calls) == 3


def test_audit_ranks_qualified_wallets_by_score():
    now = time.time()
    feed = FakeFeed({
        "Ok": steady_history("Ok", ratio_win=1.3, now=now),
        "Great": steady_history("Great", ratio_win=3.0, now=now),
    })
    qualified, _ = audit_candidates(
        [Candidate(wallet="Ok"), Candidate(wallet="Great")],
        feed, SmartMoneyConfig(enabled=True), now=now,
    )
    assert [c.wallet for c, _ in qualified] == ["Great", "Ok"]


def test_audit_tolerates_a_feed_without_the_since_parameter():
    now = time.time()

    class OldFeed:
        def recent_trades(self, wallet, limit=200):
            return steady_history(wallet, now=now)

    qualified, _ = audit_candidates(
        [Candidate(wallet="W")], OldFeed(), SmartMoneyConfig(enabled=True), now=now
    )
    assert len(qualified) == 1


def test_discovery_never_bypasses_qualification():
    """A wallet on ten winners with a terrible history must still be rejected."""
    now = time.time()
    candidate = Candidate(wallet="Hot", winners_bought={f"W{i}" for i in range(10)})
    feed = FakeFeed({"Hot": steady_history("Hot", span_days=5.0, episodes=6, now=now)})
    qualified, rejected = audit_candidates([candidate], feed, SmartMoneyConfig(enabled=True), now=now)
    assert not qualified
    assert rejected[0][0].appearances == 10
