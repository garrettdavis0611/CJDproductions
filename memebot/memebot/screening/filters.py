"""The screening gauntlet.

Two classes of verdict:
  * hard fail — an absolute veto. Any single hard fail rejects the token.
  * soft flag — noted, logged, but not disqualifying on its own.

Design rule: a check that could not be completed is a hard fail when
`unknown_is_failure` is set (the default). "We don't know whether the developer can
mint unlimited supply" is not a reason to buy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import ScreeningConfig
from ..models import SafetyReport, ScreenResult, TokenSnapshot


@dataclass(slots=True)
class FilterContext:
    snapshot: TokenSnapshot
    safety: SafetyReport
    config: ScreeningConfig
    liquidity_trend_pct: float | None = None
    """Percent change in pool liquidity across our observation window, if known."""
    intended_buy_impact_bps: float | None = None


@dataclass(slots=True)
class Verdict:
    hard_fails: list[str]
    soft_flags: list[str]
    notes: list[str]

    @classmethod
    def empty(cls) -> "Verdict":
        return cls([], [], [])


FilterFn = Callable[[FilterContext, Verdict], None]


# --------------------------------------------------------------------- liquidity


def check_liquidity(ctx: FilterContext, v: Verdict) -> None:
    liq = ctx.snapshot.liquidity_usd
    cfg = ctx.config
    if liq <= 0:
        v.hard_fails.append("no liquidity reported")
        return
    if liq < cfg.min_liquidity_usd:
        v.hard_fails.append(f"liquidity ${liq:,.0f} < floor ${cfg.min_liquidity_usd:,.0f}")
    if liq > cfg.max_liquidity_usd:
        # Not dangerous, just outside the strategy's intended regime.
        v.soft_flags.append(f"liquidity ${liq:,.0f} above target band")


def check_volume(ctx: FilterContext, v: Verdict) -> None:
    cfg = ctx.config
    if ctx.snapshot.volume_h24 < cfg.min_volume_h24_usd:
        v.hard_fails.append(
            f"24h volume ${ctx.snapshot.volume_h24:,.0f} < floor ${cfg.min_volume_h24_usd:,.0f}"
        )


def check_turnover_sanity(ctx: FilterContext, v: Verdict) -> None:
    """Absurd turnover on a small pool is the signature of wash trading."""
    ratio = ctx.snapshot.vol_liq_ratio_h1
    cfg = ctx.config
    if ratio > cfg.max_vol_liq_ratio_h1:
        v.hard_fails.append(
            f"1h volume/liquidity {ratio:.1f}x > {cfg.max_vol_liq_ratio_h1:.1f}x "
            "(likely wash trading)"
        )
    elif ratio < cfg.min_vol_liq_ratio_h1:
        v.soft_flags.append(f"1h volume/liquidity only {ratio:.2f}x (illiquid interest)")


def check_market_cap(ctx: FilterContext, v: Verdict) -> None:
    fdv = ctx.snapshot.fdv_usd
    cfg = ctx.config
    if fdv <= 0:
        v.soft_flags.append("FDV unknown")
        return
    if fdv < cfg.min_fdv_usd:
        v.hard_fails.append(f"FDV ${fdv:,.0f} < floor ${cfg.min_fdv_usd:,.0f}")
    if fdv > cfg.max_fdv_usd:
        v.soft_flags.append(f"FDV ${fdv:,.0f} above target band")


def check_age(ctx: FilterContext, v: Verdict) -> None:
    age = ctx.snapshot.age_minutes
    cfg = ctx.config
    if age == float("inf"):
        if cfg.unknown_is_failure:
            v.hard_fails.append("pair age unknown")
        else:
            v.soft_flags.append("pair age unknown")
        return
    if age < cfg.min_pair_age_minutes:
        v.hard_fails.append(
            f"pair only {age:.0f}m old (< {cfg.min_pair_age_minutes:.0f}m); "
            "early minutes are dominated by snipers and bundlers"
        )
    if age > cfg.max_pair_age_minutes:
        v.hard_fails.append(f"pair {age / 60:.0f}h old (> {cfg.max_pair_age_minutes / 60:.0f}h)")


# ------------------------------------------------------------------ on-chain rug


def check_mint_authority(ctx: FilterContext, v: Verdict) -> None:
    if not ctx.config.require_mint_authority_revoked:
        return
    state = ctx.safety.mint_authority_revoked
    if state is None:
        _unknown(ctx, v, "mint authority status unknown")
    elif not state:
        v.hard_fails.append("mint authority NOT revoked — supply can be inflated at will")


def check_freeze_authority(ctx: FilterContext, v: Verdict) -> None:
    if not ctx.config.require_freeze_authority_revoked:
        return
    state = ctx.safety.freeze_authority_revoked
    if state is None:
        _unknown(ctx, v, "freeze authority status unknown")
    elif not state:
        v.hard_fails.append("freeze authority NOT revoked — your account can be frozen")


def check_lp_locked(ctx: FilterContext, v: Verdict) -> None:
    cfg = ctx.config
    if cfg.min_lp_locked_pct <= 0:
        return
    pct = ctx.safety.lp_locked_pct
    if pct is None:
        _unknown(ctx, v, "LP lock/burn status unknown")
    elif pct < cfg.min_lp_locked_pct:
        v.hard_fails.append(
            f"only {pct:.1f}% of LP locked/burned (< {cfg.min_lp_locked_pct:.0f}%) — "
            "the deployer can pull liquidity"
        )


def check_holder_concentration(ctx: FilterContext, v: Verdict) -> None:
    cfg = ctx.config
    pct = ctx.safety.top10_holder_pct
    if pct is None:
        _unknown(ctx, v, "holder concentration unknown")
    elif pct > cfg.max_top10_holder_pct:
        v.hard_fails.append(
            f"top-10 holders own {pct:.1f}% (> {cfg.max_top10_holder_pct:.0f}%) — "
            "a single exit dumps the pool"
        )


def check_rugcheck_score(ctx: FilterContext, v: Verdict) -> None:
    cfg = ctx.config
    score = ctx.safety.rugcheck_score
    if score is None:
        _unknown(ctx, v, "third-party risk score unavailable")
        return
    if score > cfg.max_rugcheck_score:
        v.hard_fails.append(f"risk score {score:.0f} > max {cfg.max_rugcheck_score:.0f}")
    for risk in ctx.safety.rugcheck_risks:
        lowered = risk.lower()
        if "danger" in lowered:
            v.hard_fails.append(f"danger-level risk flag: {risk}")
        else:
            v.soft_flags.append(f"risk flag: {risk}")


def check_sell_route(ctx: FilterContext, v: Verdict) -> None:
    """The honeypot test: if we cannot quote a way out, we do not go in."""
    cfg = ctx.config
    if not cfg.require_sell_route:
        return
    ok = ctx.safety.sell_route_ok
    if ok is None:
        _unknown(ctx, v, "sell route could not be probed")
        return
    if not ok:
        v.hard_fails.append("NO SELL ROUTE — honeypot; buyable but not sellable")
        return
    impact = ctx.safety.sell_price_impact_bps
    if impact is None:
        _unknown(ctx, v, "sell price impact unknown")
    elif impact > cfg.max_sell_price_impact_bps:
        v.hard_fails.append(
            f"exit price impact {impact:.0f} bps on a ${cfg.sell_probe_usd:.0f} probe "
            f"(> {cfg.max_sell_price_impact_bps:.0f} bps) — you cannot get out cleanly"
        )


def check_buy_impact(ctx: FilterContext, v: Verdict) -> None:
    impact = ctx.intended_buy_impact_bps
    if impact is None:
        return
    if impact > ctx.config.max_buy_price_impact_bps:
        v.hard_fails.append(
            f"entry price impact {impact:.0f} bps > max {ctx.config.max_buy_price_impact_bps:.0f} bps"
        )


def check_liquidity_trend(ctx: FilterContext, v: Verdict) -> None:
    """Liquidity walking out of the pool precedes the price collapse."""
    trend = ctx.liquidity_trend_pct
    if trend is None:
        return
    if trend <= -20.0:
        v.hard_fails.append(f"liquidity down {abs(trend):.1f}% while we watched — pool is draining")
    elif trend <= -5.0:
        v.soft_flags.append(f"liquidity down {abs(trend):.1f}% while we watched")
    else:
        v.notes.append(f"liquidity trend {trend:+.1f}%")


def check_denylist(ctx: FilterContext, v: Verdict) -> None:
    cfg = ctx.config
    if ctx.snapshot.mint in set(cfg.blocked_mints):
        v.hard_fails.append("mint is on the blocklist")
    haystack = f"{ctx.snapshot.symbol} {ctx.snapshot.name}".lower()
    for needle in cfg.blocked_symbol_substrings:
        if needle and needle.lower() in haystack:
            v.hard_fails.append(f"name/symbol contains blocked substring {needle!r}")


def _unknown(ctx: FilterContext, v: Verdict, message: str) -> None:
    if ctx.config.unknown_is_failure:
        v.hard_fails.append(f"{message} (unknown treated as failure)")
    else:
        v.soft_flags.append(message)


DEFAULT_FILTERS: tuple[FilterFn, ...] = (
    check_denylist,
    check_liquidity,
    check_volume,
    check_turnover_sanity,
    check_market_cap,
    check_age,
    check_mint_authority,
    check_freeze_authority,
    check_lp_locked,
    check_holder_concentration,
    check_rugcheck_score,
    check_sell_route,
    check_buy_impact,
    check_liquidity_trend,
)


def screen(ctx: FilterContext, filters: tuple[FilterFn, ...] = DEFAULT_FILTERS) -> ScreenResult:
    verdict = Verdict.empty()
    for check in filters:
        check(ctx, verdict)
    return ScreenResult(
        mint=ctx.snapshot.mint,
        passed=not verdict.hard_fails,
        hard_fails=verdict.hard_fails,
        soft_flags=verdict.soft_flags,
        notes=verdict.notes,
    )
