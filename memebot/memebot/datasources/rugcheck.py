"""RugCheck client — third-party risk score, LP lock status and holder concentration.

Read endpoints are free by mint address. If the service is unreachable we return
None and let the screener apply `unknown_is_failure`.
"""

from __future__ import annotations

import logging
from typing import Any

from .http import HttpClient

log = logging.getLogger(__name__)


class RugCheckSummary:
    __slots__ = ("score", "risks", "lp_locked_pct", "top_holders_pct", "raw")

    def __init__(
        self,
        score: float | None,
        risks: list[str],
        lp_locked_pct: float | None,
        top_holders_pct: float | None,
        raw: dict[str, Any],
    ) -> None:
        self.score = score
        self.risks = risks
        self.lp_locked_pct = lp_locked_pct
        self.top_holders_pct = top_holders_pct
        self.raw = raw


class RugCheckClient:
    def __init__(
        self,
        base_url: str = "https://api.rugcheck.xyz",
        timeout: float = 15.0,
        api_key: str | None = None,
        client: HttpClient | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._http = client or HttpClient(
            base_url, requests_per_minute=55, timeout=timeout, headers=headers
        )

    def close(self) -> None:
        self._http.close()

    def summary(self, mint: str) -> RugCheckSummary | None:
        data = self._http.get_json(f"/v1/tokens/{mint}/report/summary")
        if not isinstance(data, dict):
            return None
        return self._parse(data)

    def full_report(self, mint: str) -> dict[str, Any] | None:
        data = self._http.get_json(f"/v1/tokens/{mint}/report")
        return data if isinstance(data, dict) else None

    @staticmethod
    def _parse(data: dict[str, Any]) -> RugCheckSummary:
        score = _first_float(data, "score_normalised", "scoreNormalised", "score")

        risks: list[str] = []
        for risk in data.get("risks") or []:
            if isinstance(risk, dict):
                name = risk.get("name") or risk.get("description")
                level = risk.get("level")
                if name:
                    risks.append(f"{name} [{level}]" if level else str(name))
            elif isinstance(risk, str):
                risks.append(risk)

        # RugCheck reports LP lock either directly or via the markets array.
        lp_locked = _first_float(data, "lpLockedPct", "lp_locked_pct")
        if lp_locked is None:
            for market in data.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                lp = market.get("lp")
                if isinstance(lp, dict):
                    candidate = _first_float(lp, "lpLockedPct", "lpLockedPercentage")
                    if candidate is not None:
                        lp_locked = candidate if lp_locked is None else max(lp_locked, candidate)

        top_holders = _first_float(data, "topHoldersPct", "top_holders_pct")
        if top_holders is None:
            holders = data.get("topHolders")
            if isinstance(holders, list) and holders:
                total = 0.0
                found = False
                for holder in holders[:10]:
                    if isinstance(holder, dict):
                        pct = _first_float(holder, "pct", "percentage")
                        if pct is not None:
                            total += pct
                            found = True
                if found:
                    top_holders = total

        return RugCheckSummary(score, risks, lp_locked, top_holders, data)


def _first_float(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in source and source[key] is not None:
            try:
                return float(source[key])
            except (TypeError, ValueError):
                continue
    return None
