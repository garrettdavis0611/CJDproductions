"""Parsing and rate-limiting tests. No network: httpx clients are stubbed."""

import httpx
import pytest

from memebot.datasources.dexscreener import DexScreenerClient
from memebot.datasources.http import HttpClient, RateLimiter
from memebot.datasources.jupiter import JupiterClient
from memebot.datasources.rugcheck import RugCheckClient
from memebot.datasources.solana_rpc import SolanaRpc


def stub(handler, **kwargs) -> HttpClient:
    transport = httpx.MockTransport(handler)
    return HttpClient(
        "https://example.test",
        requests_per_minute=100_000,
        client=httpx.Client(transport=transport),
        sleeper=lambda _s: None,
        **kwargs,
    )


def json_handler(payload, status=200):
    def handler(_request):
        return httpx.Response(status, json=payload)

    return handler


# ------------------------------------------------------------- rate limiting


def test_rate_limiter_allows_a_burst_then_throttles():
    now = {"t": 0.0}
    waits = []

    def sleeper(seconds):
        waits.append(seconds)
        now["t"] += seconds  # a fake sleep must advance the fake clock

    limiter = RateLimiter(60, clock=lambda: now["t"], sleeper=sleeper)  # 1/sec, capacity 60
    for _ in range(60):
        limiter.acquire()
    assert not waits  # the initial bucket absorbs the burst

    assert limiter.acquire() == pytest.approx(1.0)
    assert waits == [pytest.approx(1.0)]
    assert now["t"] == pytest.approx(1.0)


def test_rate_limiter_does_not_spin_when_the_clock_goes_backwards():
    """Guards a hang: a clock that appears to move backwards must not produce an
    unsatisfiable token deficit."""
    times = iter([0.0, -100.0, -100.0, 0.0])
    limiter = RateLimiter(60, clock=lambda: next(times, 0.0), sleeper=lambda _s: None)
    limiter._tokens = 0.0
    assert limiter.acquire() >= 0.0


def test_rate_limiter_rejects_nonsense_rates():
    with pytest.raises(ValueError):
        RateLimiter(0)


def test_http_client_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    assert stub(handler).get_json("/x") == {"ok": True}
    assert calls["n"] == 3


def test_http_client_gives_up_and_returns_none():
    assert stub(json_handler(None, status=503), max_retries=2).get_json("/x") is None


def test_http_client_does_not_retry_a_404():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404)

    assert stub(handler).get_json("/x") is None
    assert calls["n"] == 1


def test_http_client_survives_a_non_json_body():
    def handler(_request):
        return httpx.Response(200, text="<html>rate limited</html>")

    assert stub(handler).get_json("/x") is None


# --------------------------------------------------------------- dexscreener


PAIR = {
    "chainId": "solana",
    "dexId": "raydium",
    "pairAddress": "Pair111",
    "baseToken": {"address": "MintA", "symbol": "AAA", "name": "Alpha"},
    "priceUsd": "0.00042",
    "liquidity": {"usd": 150000.0},
    "fdv": 2_000_000,
    "volume": {"m5": 1000, "h1": 40000, "h24": 500000},
    "txns": {"m5": {"buys": 30, "sells": 12}, "h1": {"buys": 200, "sells": 150}},
    "priceChange": {"m5": 4.2, "h1": 18.0, "h24": 60.0},
    "pairCreatedAt": 1_700_000_000_000,
}


def test_dexscreener_parses_a_pair():
    client = DexScreenerClient(pairs_client=stub(json_handler({"pairs": [PAIR]})))
    snapshots = client.snapshots_for_mints(["MintA"])
    assert "MintA" in snapshots
    snapshot = snapshots["MintA"]
    assert snapshot.symbol == "AAA"
    assert snapshot.price_usd == pytest.approx(0.00042)
    assert snapshot.liquidity_usd == pytest.approx(150_000.0)
    assert snapshot.buys_m5 == 30
    assert snapshot.buy_pressure_m5 == pytest.approx(30 / 42)
    assert snapshot.vol_liq_ratio_h1 == pytest.approx(40_000 / 150_000)


def test_dexscreener_keeps_the_deepest_pair_per_mint():
    shallow = {**PAIR, "pairAddress": "Shallow", "liquidity": {"usd": 1000.0}}
    deep = {**PAIR, "pairAddress": "Deep", "liquidity": {"usd": 900_000.0}}
    client = DexScreenerClient(pairs_client=stub(json_handler({"pairs": [shallow, deep]})))
    assert client.snapshots_for_mints(["MintA"])["MintA"].pair_address == "Deep"


def test_dexscreener_filters_other_chains():
    ethereum = {**PAIR, "chainId": "ethereum"}
    client = DexScreenerClient(pairs_client=stub(json_handler({"pairs": [ethereum]})))
    assert client.snapshots_for_mints(["MintA"]) == {}


def test_dexscreener_handles_a_failed_request():
    client = DexScreenerClient(pairs_client=stub(json_handler(None, status=500), max_retries=1))
    assert client.snapshots_for_mints(["MintA"]) == {}


def test_dexscreener_discovery_extracts_solana_mints():
    payload = [
        {"chainId": "solana", "tokenAddress": "MintA"},
        {"chainId": "ethereum", "tokenAddress": "NotThis"},
        {"chainId": "solana", "tokenAddress": "MintA"},  # duplicate
        {"chainId": "solana"},  # malformed
    ]
    client = DexScreenerClient(profile_client=stub(json_handler(payload)))
    assert client.latest_token_profiles() == ["MintA"]


# -------------------------------------------------------------------- jupiter


QUOTE = {
    "inputMint": "So11111111111111111111111111111111111111112",
    "outputMint": "MintA",
    "inAmount": "1000000000",
    "outAmount": "2500000",
    "otherAmountThreshold": "2450000",
    "priceImpactPct": "0.0123",
    "routePlan": [{"swapInfo": {"label": "Raydium"}}, {"swapInfo": {"label": "Orca"}}],
}


def test_jupiter_parses_a_quote_and_converts_impact_to_bps():
    client = JupiterClient(client=stub(json_handler(QUOTE)))
    quote = client.quote("So11111111111111111111111111111111111111112", "MintA", 1_000_000_000)
    assert quote is not None
    assert quote.out_amount == 2_500_000
    assert quote.price_impact_bps == pytest.approx(123.0)
    assert quote.hops == 2
    assert quote.route_labels == ["Raydium", "Orca"]


def test_jupiter_returns_none_when_there_is_no_route():
    client = JupiterClient(client=stub(json_handler({"error": "no route found"})))
    assert client.quote("A", "B", 1000) is None


def test_jupiter_rejects_a_nonpositive_amount():
    client = JupiterClient(client=stub(json_handler(QUOTE)))
    assert client.quote("A", "B", 0) is None


def test_jupiter_sell_probe_targets_wrapped_sol():
    seen = {}

    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=QUOTE)

    client = JupiterClient(client=stub(handler))
    assert client.probe_sell_route("MintA", 1_000_000) is not None
    assert seen["inputMint"] == "MintA"
    assert seen["outputMint"] == "So11111111111111111111111111111111111111112"


# ------------------------------------------------------------------- rugcheck


def test_rugcheck_parses_score_and_risks():
    payload = {
        "score_normalised": 22,
        "risks": [{"name": "Low LP providers", "level": "warn"}, "Mutable metadata"],
    }
    client = RugCheckClient(client=stub(json_handler(payload)))
    summary = client.summary("MintA")
    assert summary is not None
    assert summary.score == pytest.approx(22.0)
    assert summary.risks == ["Low LP providers [warn]", "Mutable metadata"]


def test_rugcheck_derives_top_holders_from_the_holder_list():
    payload = {"score": 10, "topHolders": [{"pct": 5.0}] * 12}
    summary = RugCheckClient(client=stub(json_handler(payload))).summary("MintA")
    assert summary.top_holders_pct == pytest.approx(50.0)  # top 10 only


def test_rugcheck_reads_lp_lock_from_the_markets_array():
    payload = {"score": 10, "markets": [{"lp": {"lpLockedPct": 99.5}}]}
    summary = RugCheckClient(client=stub(json_handler(payload))).summary("MintA")
    assert summary.lp_locked_pct == pytest.approx(99.5)


def test_rugcheck_returns_none_on_failure():
    client = RugCheckClient(client=stub(json_handler(None, status=500), max_retries=1))
    assert client.summary("MintA") is None


# ----------------------------------------------------------------- solana rpc


def test_rpc_reads_revoked_authorities():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "value": {
                "data": {
                    "parsed": {
                        "type": "mint",
                        "info": {
                            "decimals": 6,
                            "supply": "1000000000000",
                            "mintAuthority": None,
                            "freezeAuthority": None,
                        },
                    }
                }
            }
        },
    }
    info = SolanaRpc(client=stub(json_handler(payload))).mint_info("MintA")
    assert info is not None
    assert info.mint_authority_revoked and info.freeze_authority_revoked
    assert info.decimals == 6
    assert info.supply == pytest.approx(1_000_000.0)


def test_rpc_reports_a_live_mint_authority():
    payload = {
        "result": {
            "value": {
                "data": {
                    "parsed": {
                        "type": "mint",
                        "info": {
                            "decimals": 9,
                            "supply": "1000",
                            "mintAuthority": "Attacker111",
                            "freezeAuthority": None,
                        },
                    }
                }
            }
        }
    }
    info = SolanaRpc(client=stub(json_handler(payload))).mint_info("MintA")
    assert info.mint_authority_revoked is False
    assert info.freeze_authority_revoked is True


def test_rpc_returns_none_for_a_non_mint_account():
    payload = {"result": {"value": {"data": {"parsed": {"type": "account", "info": {}}}}}}
    assert SolanaRpc(client=stub(json_handler(payload))).mint_info("MintA") is None


def test_rpc_returns_none_on_an_error_response():
    payload = {"error": {"code": -32602, "message": "Invalid param"}}
    assert SolanaRpc(client=stub(json_handler(payload))).mint_info("MintA") is None


def test_rpc_computes_top_holder_share():
    responses = [
        {"result": {"value": [{"amount": "400"}, {"amount": "100"}]}},
        {"result": {"value": {"amount": "1000"}}},
    ]
    calls = {"n": 0}

    def handler(_request):
        payload = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return httpx.Response(200, json=payload)

    assert SolanaRpc(client=stub(handler)).top_holder_share("MintA") == pytest.approx(50.0)


def test_rpc_top_holder_share_handles_zero_supply():
    responses = [
        {"result": {"value": [{"amount": "400"}]}},
        {"result": {"value": {"amount": "0"}}},
    ]
    calls = {"n": 0}

    def handler(_request):
        payload = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return httpx.Response(200, json=payload)

    assert SolanaRpc(client=stub(handler)).top_holder_share("MintA") is None
