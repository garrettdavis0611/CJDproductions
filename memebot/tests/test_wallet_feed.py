"""Tests for reconstructing wallet swaps from chain data.

Getting this wrong invents trades that never happened, which then feed the skill
filters. Every ambiguous case here is expected to be *dropped*, not guessed at.
"""

import httpx
import pytest

from memebot.datasources.http import HttpClient
from memebot.datasources.wallet_feed import (
    BirdeyeWalletFeed,
    SolanaWalletFeed,
    _parse_birdeye,
    parse_swap,
)
from memebot.datasources.solana_rpc import SolanaRpc
from memebot.models import WSOL_MINT
from memebot.smartmoney.models import WalletSide

WALLET = "Wallet11111111111111111111111111111111111111"
MINT = "TokenAAA1111111111111111111111111111111111"
LAMPORTS = 1_000_000_000


def tx(sol_before, sol_after, token_before, token_after, mint=MINT, owner=WALLET, block_time=1_700_000_000, err=None):
    def balances(amount):
        if amount is None:
            return []
        return [{
            "accountIndex": 1, "mint": mint, "owner": owner,
            "uiTokenAmount": {"uiAmount": amount, "decimals": 6, "amount": str(int(amount * 1e6))},
        }]

    return {
        "blockTime": block_time,
        "transaction": {"message": {"accountKeys": [{"pubkey": WALLET}, {"pubkey": "Other"}]}},
        "meta": {
            "err": err,
            "preBalances": [int(sol_before * LAMPORTS), 0],
            "postBalances": [int(sol_after * LAMPORTS), 0],
            "preTokenBalances": balances(token_before),
            "postTokenBalances": balances(token_after),
        },
    }


# ------------------------------------------------------------------ parse_swap


def test_a_buy_is_recognised():
    trade = parse_swap(tx(10.0, 8.5, 0.0, 1_000.0), WALLET, "sig1")
    assert trade is not None
    assert trade.side is WalletSide.BUY
    assert trade.token_amount == pytest.approx(1_000.0)
    assert trade.sol_amount == pytest.approx(1.5)
    assert trade.price_sol == pytest.approx(0.0015)
    assert trade.signature == "sig1"


def test_a_sell_is_recognised():
    trade = parse_swap(tx(8.5, 10.5, 1_000.0, 0.0), WALLET, "sig2")
    assert trade is not None
    assert trade.side is WalletSide.SELL
    assert trade.token_amount == pytest.approx(1_000.0)
    assert trade.sol_amount == pytest.approx(2.0)


def test_a_partial_sell_is_recognised():
    trade = parse_swap(tx(8.5, 9.5, 1_000.0, 400.0), WALLET, "sig3")
    assert trade.side is WalletSide.SELL
    assert trade.token_amount == pytest.approx(600.0)


def test_wrapped_sol_is_folded_into_sol():
    """A wrap/unwrap must not read as buying a token called WSOL."""
    data = tx(10.0, 8.5, 0.0, 1_000.0)
    data["meta"]["preTokenBalances"].append({
        "accountIndex": 2, "mint": WSOL_MINT, "owner": WALLET,
        "uiTokenAmount": {"uiAmount": 1.5, "decimals": 9, "amount": "1500000000"},
    })
    data["meta"]["postTokenBalances"].append({
        "accountIndex": 2, "mint": WSOL_MINT, "owner": WALLET,
        "uiTokenAmount": {"uiAmount": 0.0, "decimals": 9, "amount": "0"},
    })
    trade = parse_swap(data, WALLET, "sig")
    assert trade is not None
    assert trade.side is WalletSide.BUY
    assert trade.mint == MINT
    # 1.5 SOL of native movement plus 1.5 of unwrapped WSOL.
    assert trade.sol_amount == pytest.approx(3.0)


def test_a_multi_token_transaction_is_dropped_not_guessed():
    data = tx(10.0, 8.5, 0.0, 1_000.0)
    for balances, amount in (("preTokenBalances", 0.0), ("postTokenBalances", 500.0)):
        data["meta"][balances].append({
            "accountIndex": 3, "mint": "OtherMint", "owner": WALLET,
            "uiTokenAmount": {"uiAmount": amount, "decimals": 6, "amount": "0"},
        })
    assert parse_swap(data, WALLET, "sig") is None


def test_a_transfer_in_is_not_a_trade():
    """Tokens arrive and SOL does not move: an airdrop, not a purchase."""
    assert parse_swap(tx(10.0, 10.0, 0.0, 1_000.0), WALLET, "sig") is None


def test_both_balances_rising_is_not_a_trade():
    assert parse_swap(tx(10.0, 12.0, 0.0, 1_000.0), WALLET, "sig") is None


def test_a_failed_transaction_is_skipped():
    assert parse_swap(tx(10.0, 8.5, 0.0, 1_000.0, err={"InstructionError": []}), WALLET) is None


def test_a_dust_swap_is_skipped():
    assert parse_swap(tx(10.0, 9.9999, 0.0, 1.0), WALLET, "sig") is None


def test_a_transaction_with_no_block_time_is_skipped():
    assert parse_swap(tx(10.0, 8.5, 0.0, 1_000.0, block_time=None), WALLET) is None


def test_balances_owned_by_someone_else_are_ignored():
    assert parse_swap(tx(10.0, 8.5, 0.0, 1_000.0, owner="SomeoneElse"), WALLET, "sig") is None


def test_a_wallet_not_in_the_account_keys_yields_no_sol_delta():
    assert parse_swap(tx(10.0, 8.5, 0.0, 1_000.0), "DifferentWallet", "sig") is None


# ---------------------------------------------------------------- feed plumbing


def rpc_stub(handler) -> SolanaRpc:
    return SolanaRpc(
        client=HttpClient(
            "https://rpc.test", requests_per_minute=100_000,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleeper=lambda _s: None,
        )
    )


def test_solana_feed_reconstructs_and_sorts_trades():
    responses = {
        "getSignaturesForAddress": {"result": [{"signature": "s2"}, {"signature": "s1"}]},
        "getTransaction": None,
    }
    txs = {
        "s1": tx(10.0, 8.5, 0.0, 1_000.0, block_time=1_700_000_100),
        "s2": tx(8.5, 10.5, 1_000.0, 0.0, block_time=1_700_000_200),
    }

    def handler(request):
        import json

        body = json.loads(request.content)
        if body["method"] == "getSignaturesForAddress":
            return httpx.Response(200, json=responses["getSignaturesForAddress"])
        signature = body["params"][0]
        return httpx.Response(200, json={"result": txs[signature]})

    trades = SolanaWalletFeed(rpc_stub(handler)).recent_trades(WALLET)
    assert [t.side for t in trades] == [WalletSide.BUY, WalletSide.SELL]
    assert [t.ts for t in trades] == [1_700_000_100, 1_700_000_200]


def test_solana_feed_paginates_to_reach_older_history():
    """A six-month judgement needs six months of signatures, not the first page."""
    import json

    pages = {
        None: [{"signature": f"a{i}", "blockTime": 1_700_000_000 - i} for i in range(100)],
        "a99": [{"signature": f"b{i}", "blockTime": 1_699_990_000 - i} for i in range(50)],
    }
    requested_before: list[str | None] = []

    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "getSignaturesForAddress":
            before = (body["params"][1] or {}).get("before")
            requested_before.append(before)
            return httpx.Response(200, json={"result": pages.get(before, [])})
        signature = body["params"][0]
        return httpx.Response(200, json={"result": tx(10.0, 8.5, 0.0, 1_000.0)})

    feed = SolanaWalletFeed(rpc_stub(handler), max_transactions=200, max_pages=3)
    trades = feed.recent_trades(WALLET)
    assert requested_before == [None, "a99"]
    assert len(trades) == 150


def test_solana_feed_stops_at_the_lookback_boundary():
    import json

    signatures = [
        {"signature": "recent", "blockTime": 1_700_000_000},
        {"signature": "ancient", "blockTime": 1_600_000_000},
    ]

    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "getSignaturesForAddress":
            return httpx.Response(200, json={"result": signatures})
        assert body["params"][0] == "recent", "must not fetch beyond the window"
        return httpx.Response(200, json={"result": tx(10.0, 8.5, 0.0, 1_000.0)})

    feed = SolanaWalletFeed(rpc_stub(handler), max_pages=3)
    trades = feed.recent_trades(WALLET, since_ts=1_699_000_000)
    assert len(trades) == 1


def test_solana_feed_respects_the_transaction_budget():
    import json

    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "getSignaturesForAddress":
            limit = body["params"][1]["limit"]
            return httpx.Response(
                200,
                json={"result": [{"signature": f"s{i}", "blockTime": 1_700_000_000} for i in range(limit)]},
            )
        return httpx.Response(200, json={"result": tx(10.0, 8.5, 0.0, 1_000.0)})

    feed = SolanaWalletFeed(rpc_stub(handler), max_transactions=30, max_pages=5)
    assert len(feed.recent_trades(WALLET)) == 30


def test_solana_feed_skips_failed_signatures():
    import json

    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "getSignaturesForAddress":
            return httpx.Response(200, json={"result": [{"signature": "bad", "err": "boom"}]})
        raise AssertionError("should not fetch a failed transaction")

    assert SolanaWalletFeed(rpc_stub(handler)).recent_trades(WALLET) == []


def test_solana_feed_tolerates_an_unfetchable_transaction():
    import json

    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "getSignaturesForAddress":
            return httpx.Response(200, json={"result": [{"signature": "s1"}]})
        return httpx.Response(500)

    assert SolanaWalletFeed(rpc_stub(handler)).recent_trades(WALLET) == []


# -------------------------------------------------------------------- birdeye


def test_birdeye_parsing_extracts_a_buy():
    entries = [{
        "blockUnixTime": 1_700_000_000,
        "txHash": "abc",
        "balanceChange": [
            {"address": WSOL_MINT, "amount": -1_500_000_000, "decimals": 9},
            {"address": MINT, "amount": 1_000_000_000, "decimals": 6},
        ],
    }]
    trades = _parse_birdeye(entries, WALLET)
    assert len(trades) == 1
    assert trades[0].side is WalletSide.BUY
    assert trades[0].sol_amount == pytest.approx(1.5)
    assert trades[0].token_amount == pytest.approx(1_000.0)


def test_birdeye_parsing_drops_multi_token_entries():
    entries = [{
        "blockUnixTime": 1_700_000_000,
        "balanceChange": [
            {"address": WSOL_MINT, "amount": -1_500_000_000, "decimals": 9},
            {"address": MINT, "amount": 1_000_000_000, "decimals": 6},
            {"address": "Other", "amount": 5_000_000, "decimals": 6},
        ],
    }]
    assert _parse_birdeye(entries, WALLET) == []


def test_birdeye_feed_handles_a_failed_call():
    feed = BirdeyeWalletFeed(
        "key",
        client=HttpClient(
            "https://birdeye.test", requests_per_minute=100_000,
            client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(500))),
            sleeper=lambda _s: None, max_retries=1,
        ),
    )
    assert feed.recent_trades(WALLET) == []
