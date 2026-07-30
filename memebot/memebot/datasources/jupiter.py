"""Jupiter Swap API client (quotes + swap transaction build).

Free tier: https://lite-api.jup.ag/swap/v1/...  (no API key)
Paid tier: https://api.jup.ag/swap/v1/...       (send x-api-key)

Quotes are used for two distinct purposes:
  1. Execution — the route we actually trade.
  2. Screening — probing whether a SELL back to SOL routes at all. A token you can
     buy but not sell is a honeypot, and this probe is the cheapest way to find out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..models import WSOL_MINT
from .http import HttpClient

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Quote:
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    other_amount_threshold: int
    price_impact_bps: float
    slippage_bps: int
    route_labels: list[str]
    raw: dict[str, Any]

    @property
    def hops(self) -> int:
        return len(self.route_labels)


class JupiterClient:
    def __init__(
        self,
        base_url: str = "https://lite-api.jup.ag",
        timeout: float = 15.0,
        api_key: str | None = None,
        requests_per_minute: int = 55,
        client: HttpClient | None = None,
    ) -> None:
        headers = {"x-api-key": api_key} if api_key else None
        self._http = client or HttpClient(
            base_url, requests_per_minute=requests_per_minute, timeout=timeout, headers=headers
        )

    def close(self) -> None:
        self._http.close()

    def quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 300,
        only_direct_routes: bool = False,
    ) -> Quote | None:
        """`amount` is in the input mint's smallest units (lamports for SOL)."""
        if amount <= 0:
            return None
        params: dict[str, Any] = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(slippage_bps),
            "restrictIntermediateTokens": "true",
        }
        if only_direct_routes:
            params["onlyDirectRoutes"] = "true"

        data = self._http.get_json("/swap/v1/quote", params=params)
        if not isinstance(data, dict) or not data.get("outAmount"):
            return None
        return self._parse_quote(data, slippage_bps)

    @staticmethod
    def _parse_quote(data: dict[str, Any], slippage_bps: int) -> Quote | None:
        try:
            in_amount = int(data["inAmount"])
            out_amount = int(data["outAmount"])
        except (KeyError, TypeError, ValueError):
            return None

        # Jupiter reports priceImpactPct as a decimal fraction string, e.g. "0.0123".
        try:
            impact_pct = float(data.get("priceImpactPct") or 0.0)
        except (TypeError, ValueError):
            impact_pct = 0.0

        labels: list[str] = []
        for step in data.get("routePlan") or []:
            if isinstance(step, dict):
                info = step.get("swapInfo") or {}
                label = info.get("label") or info.get("ammKey")
                if label:
                    labels.append(str(label))

        try:
            threshold = int(data.get("otherAmountThreshold") or out_amount)
        except (TypeError, ValueError):
            threshold = out_amount

        return Quote(
            input_mint=str(data.get("inputMint") or ""),
            output_mint=str(data.get("outputMint") or ""),
            in_amount=in_amount,
            out_amount=out_amount,
            other_amount_threshold=threshold,
            price_impact_bps=abs(impact_pct) * 10_000.0,
            slippage_bps=slippage_bps,
            route_labels=labels,
            raw=data,
        )

    def probe_sell_route(
        self,
        mint: str,
        token_amount_raw: int,
        slippage_bps: int = 300,
    ) -> Quote | None:
        """Can we get out? Quote `token_amount_raw` of `mint` back into wrapped SOL."""
        return self.quote(mint, WSOL_MINT, token_amount_raw, slippage_bps=slippage_bps)

    def build_swap_transaction(
        self,
        quote: Quote,
        user_public_key: str,
        priority_fee_lamports: int = 200_000,
        wrap_unwrap_sol: bool = True,
    ) -> str | None:
        """Return a base64 serialized VersionedTransaction, ready to sign."""
        body = {
            "quoteResponse": quote.raw,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": wrap_unwrap_sol,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {
                "priorityLevelWithMaxLamports": {
                    "maxLamports": int(priority_fee_lamports),
                    "priorityLevel": "high",
                }
            },
        }
        data = self._http.post_json("/swap/v1/swap", body)
        if not isinstance(data, dict):
            return None
        tx = data.get("swapTransaction")
        return tx if isinstance(tx, str) else None
