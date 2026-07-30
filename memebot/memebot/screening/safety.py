"""Gathers the safety facts a token must supply before it is allowed near our money.

Each source is independent and failure-tolerant: if one is unreachable, the fields
it owns stay None and the screener decides what None means (see
`screening.unknown_is_failure`). Nothing here interprets the facts — that is
`filters.py`'s job.
"""

from __future__ import annotations

import logging

from ..config import ScreeningConfig
from ..datasources.jupiter import JupiterClient
from ..datasources.rugcheck import RugCheckClient
from ..datasources.solana_rpc import SolanaRpc
from ..models import SafetyReport, TokenSnapshot

log = logging.getLogger(__name__)


class SafetyInspector:
    def __init__(
        self,
        config: ScreeningConfig,
        rpc: SolanaRpc | None = None,
        rugcheck: RugCheckClient | None = None,
        jupiter: JupiterClient | None = None,
        slippage_bps: int = 300,
    ) -> None:
        self.config = config
        self.rpc = rpc
        self.rugcheck = rugcheck
        self.jupiter = jupiter
        self.slippage_bps = slippage_bps

    def inspect(self, snapshot: TokenSnapshot, decimals_hint: int | None = None) -> SafetyReport:
        report = SafetyReport(mint=snapshot.mint)
        decimals = decimals_hint

        if self.rpc is not None:
            try:
                info = self.rpc.mint_info(snapshot.mint)
            except Exception as exc:  # a data-source bug must not crash the loop
                report.errors.append(f"rpc.mint_info: {exc}")
                log.warning("mint_info failed for %s: %s", snapshot.mint, exc)
            else:
                if info is None:
                    report.errors.append("rpc.mint_info: no data")
                else:
                    decimals = info.decimals
                    report.mint_authority_revoked = info.mint_authority_revoked
                    report.freeze_authority_revoked = info.freeze_authority_revoked

            try:
                report.top10_holder_pct = self.rpc.top_holder_share(snapshot.mint, top_n=10)
            except Exception as exc:
                report.errors.append(f"rpc.top_holder_share: {exc}")

        if self.rugcheck is not None:
            try:
                summary = self.rugcheck.summary(snapshot.mint)
            except Exception as exc:
                report.errors.append(f"rugcheck: {exc}")
                log.warning("rugcheck failed for %s: %s", snapshot.mint, exc)
            else:
                if summary is None:
                    report.errors.append("rugcheck: no data")
                else:
                    report.rugcheck_score = summary.score
                    report.rugcheck_risks = summary.risks
                    if summary.lp_locked_pct is not None:
                        report.lp_locked_pct = summary.lp_locked_pct
                    # Prefer RugCheck's holder figure: it excludes LP vaults, which the
                    # raw largest-accounts call cannot distinguish from a whale wallet.
                    if summary.top_holders_pct is not None:
                        report.top10_holder_pct = summary.top_holders_pct

        if self.jupiter is not None and self.config.require_sell_route:
            self._probe_sell(snapshot, report, decimals)

        return report

    def _probe_sell(
        self, snapshot: TokenSnapshot, report: SafetyReport, decimals: int | None
    ) -> None:
        """Quote a small sell back to SOL. No route out == honeypot."""
        if snapshot.price_usd <= 0:
            report.errors.append("sell_probe: no price")
            return
        if decimals is None:
            report.errors.append("sell_probe: unknown decimals")
            return

        token_amount = self.config.sell_probe_usd / snapshot.price_usd
        raw_amount = int(token_amount * (10**decimals))
        if raw_amount <= 0:
            report.errors.append("sell_probe: probe size rounds to zero")
            return

        try:
            quote = self.jupiter.probe_sell_route(
                snapshot.mint, raw_amount, slippage_bps=self.slippage_bps
            )
        except Exception as exc:
            report.errors.append(f"sell_probe: {exc}")
            return

        if quote is None:
            report.sell_route_ok = False
            return
        report.sell_route_ok = True
        report.sell_price_impact_bps = quote.price_impact_bps
