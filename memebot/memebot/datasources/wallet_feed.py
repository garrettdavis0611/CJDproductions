"""Reconstructs a wallet's swap history from chain data.

Default backend is plain Solana JSON-RPC, so this works with no third-party account
and every number can be verified against the chain. The cost is one `getTransaction`
call per signature, which is slow and rate-limit hungry — a paid RPC endpoint is
strongly recommended, and Birdeye is offered as a faster optional alternative.

Parsing rule worth knowing: a transaction that moves **more than one** non-SOL mint
is skipped rather than guessed at. Multi-hop routes and arbitrage cannot be split
into per-token buys and sells without assuming things, and a wallet analyser that
invents trades produces confident nonsense. Dropping ambiguous data is the cheaper
error.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Protocol

from ..models import WSOL_MINT
from ..smartmoney.models import WalletSide, WalletTrade
from .http import HttpClient
from .solana_rpc import SolanaRpc

log = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000
TOKEN_DUST = 1e-9
MIN_SOL_PER_TRADE = 0.001
"""Ignore dust swaps; they are noise and distort win rates."""


class WalletFeed(Protocol):
    def recent_trades(self, wallet: str, limit: int = 200) -> list[WalletTrade]:
        ...


class SolanaWalletFeed:
    """Reconstruct trades from `getSignaturesForAddress` + `getTransaction`.

    Paginates backwards through signature history so a six-month record can actually
    be seen. That is deliberately expensive: judging whether a wallet has been
    *consistently* good needs the whole window, and one page of recent activity
    cannot answer it. `max_pages` bounds the cost.
    """

    SIGNATURES_PER_PAGE = 100

    def __init__(self, rpc: SolanaRpc, max_transactions: int = 200, max_pages: int = 1) -> None:
        self.rpc = rpc
        self.max_transactions = max_transactions
        self.max_pages = max(1, max_pages)

    def recent_trades(
        self,
        wallet: str,
        limit: int | None = None,
        since_ts: float | None = None,
        max_pages: int | None = None,
    ) -> list[WalletTrade]:
        budget = limit or self.max_transactions
        pages = max_pages if max_pages is not None else self.max_pages
        trades: list[WalletTrade] = []
        skipped = 0
        fetched = 0
        before: str | None = None
        reached_start = False

        for _page in range(pages):
            if fetched >= budget:
                break
            page_size = min(self.SIGNATURES_PER_PAGE, budget - fetched)
            signatures = self.rpc.signatures_for_address(wallet, limit=page_size, before=before)
            if not signatures:
                break

            last_signature = None
            for entry in signatures:
                if not isinstance(entry, dict):
                    continue
                signature = entry.get("signature")
                if not signature:
                    continue
                last_signature = signature
                fetched += 1

                block_time = entry.get("blockTime")
                if since_ts is not None and block_time and float(block_time) < since_ts:
                    reached_start = True
                    continue
                if entry.get("err"):
                    continue  # failed transactions moved nothing

                tx = self.rpc.get_transaction(signature)
                if tx is None:
                    skipped += 1
                    continue
                trade = parse_swap(tx, wallet, signature)
                if trade is not None:
                    trades.append(trade)

            if reached_start or last_signature is None or len(signatures) < page_size:
                break
            before = last_signature

        if skipped:
            log.info("%s: %d transactions could not be fetched", wallet[:8], skipped)
        trades.sort(key=lambda t: t.ts)
        return trades


def parse_swap(tx: dict[str, Any], wallet: str, signature: str = "") -> WalletTrade | None:
    """Turn one transaction into a WalletTrade, or None if it is not a clean swap."""
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return None
    block_time = tx.get("blockTime")
    if not block_time:
        return None

    account_keys = _account_keys(tx)
    sol_delta = _sol_delta(meta, account_keys, wallet)
    token_deltas = _token_deltas(meta, wallet)

    # Wrapped SOL is SOL; fold it in so wrap/unwrap does not read as a token trade.
    wsol_delta = token_deltas.pop(WSOL_MINT, 0.0)
    sol_delta += wsol_delta

    moved = {mint: delta for mint, delta in token_deltas.items() if abs(delta) > TOKEN_DUST}
    if len(moved) != 1:
        return None  # not a swap, or a multi-hop we refuse to guess at

    mint, token_delta = next(iter(moved.items()))
    if abs(sol_delta) < MIN_SOL_PER_TRADE:
        return None

    if token_delta > 0 and sol_delta < 0:
        side, token_amount, sol_amount = WalletSide.BUY, token_delta, -sol_delta
    elif token_delta < 0 and sol_delta > 0:
        side, token_amount, sol_amount = WalletSide.SELL, -token_delta, sol_delta
    else:
        # Both moved the same way: a transfer, an airdrop, or an LP action. Not a trade.
        return None

    return WalletTrade(
        wallet=wallet,
        mint=mint,
        side=side,
        token_amount=token_amount,
        sol_amount=sol_amount,
        ts=float(block_time),
        signature=signature,
    )


def _account_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    keys: list[str] = []
    for key in message.get("accountKeys") or []:
        if isinstance(key, dict):
            pubkey = key.get("pubkey")
            if isinstance(pubkey, str):
                keys.append(pubkey)
        elif isinstance(key, str):
            keys.append(key)
    return keys


def _sol_delta(meta: dict[str, Any], account_keys: list[str], wallet: str) -> float:
    try:
        index = account_keys.index(wallet)
    except ValueError:
        return 0.0
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if index >= len(pre) or index >= len(post):
        return 0.0
    try:
        return (float(post[index]) - float(pre[index])) / LAMPORTS_PER_SOL
    except (TypeError, ValueError):
        return 0.0


def _token_deltas(meta: dict[str, Any], wallet: str) -> dict[str, float]:
    def collect(entries: Iterable[Any]) -> dict[str, float]:
        out: dict[str, float] = {}
        for entry in entries or []:
            if not isinstance(entry, dict) or entry.get("owner") != wallet:
                continue
            mint = entry.get("mint")
            amount = (entry.get("uiTokenAmount") or {}).get("uiAmount")
            if not isinstance(mint, str) or amount is None:
                continue
            try:
                out[mint] = out.get(mint, 0.0) + float(amount)
            except (TypeError, ValueError):
                continue
        return out

    pre = collect(meta.get("preTokenBalances"))
    post = collect(meta.get("postTokenBalances"))
    deltas: dict[str, float] = {}
    for mint in set(pre) | set(post):
        deltas[mint] = post.get(mint, 0.0) - pre.get(mint, 0.0)
    return deltas


class BirdeyeWalletFeed:
    """Optional faster backend. Requires BIRDEYE_API_KEY."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://public-api.birdeye.so",
        timeout: float = 15.0,
        client: HttpClient | None = None,
    ) -> None:
        self._http = client or HttpClient(
            base_url,
            requests_per_minute=55,
            timeout=timeout,
            headers={"X-API-KEY": api_key, "x-chain": "solana"},
        )

    def recent_trades(self, wallet: str, limit: int = 100) -> list[WalletTrade]:
        data = self._http.get_json(
            "/v1/wallet/tx_list", params={"wallet": wallet, "limit": min(100, limit)}
        )
        if not isinstance(data, dict):
            return []
        payload = data.get("data")
        raw = []
        if isinstance(payload, dict):
            raw = payload.get("solana") or payload.get("items") or []
        elif isinstance(payload, list):
            raw = payload
        return _parse_birdeye(raw, wallet)


def _parse_birdeye(entries: Iterable[Any], wallet: str) -> list[WalletTrade]:
    trades: list[WalletTrade] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("balanceChange") or []
        ts = entry.get("blockUnixTime") or entry.get("blockTime")
        if not ts or not isinstance(changes, list):
            continue

        sol_delta = 0.0
        token_moves: dict[str, float] = {}
        for change in changes:
            if not isinstance(change, dict):
                continue
            address = change.get("address") or change.get("tokenAddress")
            decimals = int(change.get("decimals") or 0)
            try:
                amount = float(change.get("amount") or 0) / (10**decimals if decimals else 1)
            except (TypeError, ValueError):
                continue
            if address in (WSOL_MINT, "So11111111111111111111111111111111111111111", None):
                sol_delta += amount
            elif isinstance(address, str):
                token_moves[address] = token_moves.get(address, 0.0) + amount

        moved = {m: d for m, d in token_moves.items() if abs(d) > TOKEN_DUST}
        if len(moved) != 1 or abs(sol_delta) < MIN_SOL_PER_TRADE:
            continue
        mint, token_delta = next(iter(moved.items()))

        if token_delta > 0 and sol_delta < 0:
            side, token_amount, sol_amount = WalletSide.BUY, token_delta, -sol_delta
        elif token_delta < 0 and sol_delta > 0:
            side, token_amount, sol_amount = WalletSide.SELL, -token_delta, sol_delta
        else:
            continue

        trades.append(
            WalletTrade(
                wallet=wallet, mint=mint, side=side, token_amount=token_amount,
                sol_amount=sol_amount, ts=float(ts),
                signature=str(entry.get("txHash") or entry.get("signature") or ""),
            )
        )
    trades.sort(key=lambda t: t.ts)
    return trades
