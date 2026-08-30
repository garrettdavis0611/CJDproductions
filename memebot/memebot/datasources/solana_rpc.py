"""Direct Solana JSON-RPC reads for facts we refuse to take on trust.

Mint authority and freeze authority are checked on-chain rather than via a third
party, because a stale or wrong answer here is the difference between a trade and
an unlimited-supply rug.
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

from .http import HttpClient

log = logging.getLogger(__name__)


class MintInfo:
    __slots__ = ("mint", "mint_authority", "freeze_authority", "decimals", "supply")

    def __init__(
        self,
        mint: str,
        mint_authority: str | None,
        freeze_authority: str | None,
        decimals: int,
        supply: float,
    ) -> None:
        self.mint = mint
        self.mint_authority = mint_authority
        self.freeze_authority = freeze_authority
        self.decimals = decimals
        self.supply = supply

    @property
    def mint_authority_revoked(self) -> bool:
        return self.mint_authority is None

    @property
    def freeze_authority_revoked(self) -> bool:
        return self.freeze_authority is None


class SolanaRpc:
    def __init__(
        self,
        url: str = "https://api.mainnet-beta.solana.com",
        timeout: float = 15.0,
        requests_per_minute: int = 300,
        client: HttpClient | None = None,
    ) -> None:
        self._http = client or HttpClient(url, requests_per_minute=requests_per_minute, timeout=timeout)
        self._ids = itertools.count(1)

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, params: list[Any]) -> Any | None:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        data = self._http.post_json("", payload)
        if not isinstance(data, dict):
            return None
        if "error" in data:
            log.warning("RPC %s error: %s", method, data["error"])
            return None
        return data.get("result")

    def mint_info(self, mint: str) -> MintInfo | None:
        result = self._call("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        if not isinstance(result, dict):
            return None
        value = result.get("value")
        if not isinstance(value, dict):
            return None
        parsed = ((value.get("data") or {}).get("parsed") or {})
        if parsed.get("type") != "mint":
            log.warning("%s is not an SPL mint account", mint)
            return None
        info = parsed.get("info") or {}
        try:
            decimals = int(info.get("decimals") or 0)
            raw_supply = float(info.get("supply") or 0)
        except (TypeError, ValueError):
            return None
        return MintInfo(
            mint=mint,
            mint_authority=info.get("mintAuthority") or None,
            freeze_authority=info.get("freezeAuthority") or None,
            decimals=decimals,
            supply=raw_supply / (10**decimals) if decimals else raw_supply,
        )

    def top_holder_share(self, mint: str, top_n: int = 10) -> float | None:
        """Percentage (0-100) of circulating supply held by the largest `top_n` accounts.

        Note this counts token accounts, and liquidity-pool vaults are token accounts
        too. `exclude_addresses` lets callers drop known pool vaults so a healthy pool
        is not mistaken for a whale.
        """
        result = self._call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        if not isinstance(result, dict):
            return None
        accounts = (result.get("value") or [])
        if not accounts:
            return None

        supply_result = self._call("getTokenSupply", [mint, {"commitment": "confirmed"}])
        if not isinstance(supply_result, dict):
            return None
        supply_raw = ((supply_result.get("value") or {}).get("amount"))
        try:
            supply = float(supply_raw)
        except (TypeError, ValueError):
            return None
        if supply <= 0:
            return None

        held = 0.0
        for account in accounts[:top_n]:
            if not isinstance(account, dict):
                continue
            try:
                held += float(account.get("amount") or 0)
            except (TypeError, ValueError):
                continue
        return min(100.0, held / supply * 100.0)

    def signatures_for_address(
        self, address: str, limit: int = 100, before: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, min(1000, limit)), "commitment": "confirmed"}
        if before:
            params["before"] = before
        result = self._call("getSignaturesForAddress", [address, params])
        return result if isinstance(result, list) else []

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        result = self._call(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        return result if isinstance(result, dict) else None

    def sol_balance(self, pubkey: str) -> float | None:
        result = self._call("getBalance", [pubkey, {"commitment": "confirmed"}])
        if not isinstance(result, dict):
            return None
        try:
            return float(result.get("value") or 0) / 1e9
        except (TypeError, ValueError):
            return None
