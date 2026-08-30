"""DexScreener client — candidate discovery and price/liquidity snapshots.

Rate limits (documented by DexScreener): 60 rpm for token-profile/boost endpoints,
300 rpm for the dex/pairs endpoints. We keep separate limiters for the two classes.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from ..models import TokenSnapshot
from .http import HttpClient

log = logging.getLogger(__name__)

MAX_MINTS_PER_REQUEST = 30


class DexScreenerClient:
    def __init__(
        self,
        base_url: str = "https://api.dexscreener.com",
        timeout: float = 15.0,
        chain: str = "solana",
        profile_client: HttpClient | None = None,
        pairs_client: HttpClient | None = None,
    ) -> None:
        self.chain = chain
        self._profiles = profile_client or HttpClient(base_url, requests_per_minute=55, timeout=timeout)
        self._pairs = pairs_client or HttpClient(base_url, requests_per_minute=280, timeout=timeout)

    def close(self) -> None:
        self._profiles.close()
        self._pairs.close()

    # ---------------------------------------------------------------- discovery

    def latest_token_profiles(self) -> list[str]:
        """Mints of newly listed tokens on our chain, newest first."""
        data = self._profiles.get_json("/token-profiles/latest/v1")
        return self._mints_from_listing(data)

    def latest_boosted_tokens(self) -> list[str]:
        """Mints whose listing was paid-boosted.

        A boost is a marketing spend, not a quality signal — treat it purely as a
        discovery channel, and let screening decide.
        """
        data = self._profiles.get_json("/token-boosts/latest/v1")
        return self._mints_from_listing(data)

    def _mints_from_listing(self, data: Any) -> list[str]:
        if not isinstance(data, list):
            return []
        seen: dict[str, None] = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            if (entry.get("chainId") or "").lower() != self.chain:
                continue
            mint = entry.get("tokenAddress")
            if isinstance(mint, str) and mint:
                seen.setdefault(mint, None)
        return list(seen)

    def search(self, query: str) -> list[TokenSnapshot]:
        data = self._pairs.get_json("/latest/dex/search", params={"q": query})
        return self._snapshots_from_pairs(data)

    # ---------------------------------------------------------------- snapshots

    def snapshots_for_mints(self, mints: Iterable[str]) -> dict[str, TokenSnapshot]:
        """Best (deepest-liquidity) pair per mint, batched 30 at a time."""
        unique = list(dict.fromkeys(m for m in mints if m))
        best: dict[str, TokenSnapshot] = {}
        for start in range(0, len(unique), MAX_MINTS_PER_REQUEST):
            batch = unique[start : start + MAX_MINTS_PER_REQUEST]
            data = self._pairs.get_json(f"/latest/dex/tokens/{','.join(batch)}")
            for snapshot in self._snapshots_from_pairs(data):
                incumbent = best.get(snapshot.mint)
                if incumbent is None or snapshot.liquidity_usd > incumbent.liquidity_usd:
                    best[snapshot.mint] = snapshot
        return best

    def snapshot_for_mint(self, mint: str) -> TokenSnapshot | None:
        return self.snapshots_for_mints([mint]).get(mint)

    def _snapshots_from_pairs(self, data: Any) -> list[TokenSnapshot]:
        pairs: list[Any]
        if isinstance(data, dict):
            pairs = data.get("pairs") or []
        elif isinstance(data, list):
            pairs = data
        else:
            return []

        out: list[TokenSnapshot] = []
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            if (pair.get("chainId") or "").lower() != self.chain:
                continue
            snapshot = TokenSnapshot.from_dexscreener_pair(pair)
            if snapshot.mint and snapshot.price_usd > 0:
                out.append(snapshot)
        return out
