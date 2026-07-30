"""Find candidate wallets from chain data, rather than from someone's blog post.

The bad way to do this — and the way most guides suggest — is to take wallet
addresses from a Telegram channel, a listicle, or a leaderboard screenshot. Those are
unverifiable, and an unverified address that someone is publicising is frequently a
wallet that *wants* to be followed. That is the exit-liquidity setup, not a tip.

The approach here derives candidates from the chain itself:

  1. Find tokens that recently ran up hard (the winners).
  2. Read the swap history of those tokens' pools to see who actually bought them,
     early, with real size.
  3. Keep wallets that appear across **several independent winners** — one correct
     call is noise, showing up on four separate winners is a prior worth testing.
  4. Hand every survivor to the full six-month audit in `analysis.py`.

Step 3 is what makes this different from a leaderboard: it selects for repetition
across independent events, and then step 4 still has to pass. Discovery only proposes;
qualification decides.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from ..config import SmartMoneyConfig
from ..models import TokenSnapshot
from .models import WalletSide

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    wallet: str
    winners_bought: set[str] = field(default_factory=set)
    total_sol_bought: float = 0.0
    buys_seen: int = 0

    @property
    def appearances(self) -> int:
        return len(self.winners_bought)


def find_winning_tokens(
    dexscreener,
    min_liquidity_usd: float = 50_000.0,
    min_gain_h24_pct: float = 40.0,
    limit: int = 15,
    search_terms: tuple[str, ...] = ("sol", "pump", "bonk", "wif", "meme"),
) -> list[TokenSnapshot]:
    """Tokens that have run recently. These are the probes, not the trades.

    We are not buying these — a token already up 40% is exactly what the momentum
    screen refuses. We are using them to find out who was holding *before* the run.
    """
    seen: dict[str, TokenSnapshot] = {}
    sources = []
    try:
        sources.append(dexscreener.latest_boosted_tokens())
    except Exception as exc:
        log.warning("boosted-token discovery failed: %s", exc)
    try:
        sources.append(dexscreener.latest_token_profiles())
    except Exception as exc:
        log.warning("profile discovery failed: %s", exc)

    mints: list[str] = []
    for source in sources:
        mints.extend(source or [])
    if mints:
        try:
            for mint, snapshot in dexscreener.snapshots_for_mints(mints[:120]).items():
                seen[mint] = snapshot
        except Exception as exc:
            log.warning("snapshot lookup failed: %s", exc)

    # Broaden the net with searches, since the "new listings" feeds skew very fresh.
    for term in search_terms:
        try:
            for snapshot in dexscreener.search(term):
                seen.setdefault(snapshot.mint, snapshot)
        except Exception as exc:
            log.warning("search %r failed: %s", term, exc)

    winners = [
        s for s in seen.values()
        if s.liquidity_usd >= min_liquidity_usd
        and s.price_change_h24 >= min_gain_h24_pct
        and s.pair_address
    ]
    winners.sort(key=lambda s: s.price_change_h24, reverse=True)
    return winners[:limit]


def buyers_of_pool(
    rpc,
    pair_address: str,
    mint: str,
    max_signatures: int = 100,
    min_sol_size: float = 0.5,
) -> dict[str, float]:
    """Wallets that bought `mint` through this pool, and how much SOL they spent.

    Every swap touches the pool account, so its signature history is the trade tape.
    `min_sol_size` filters out dust — a wallet risking 0.01 SOL is not expressing a
    view worth copying.
    """
    from ..datasources.wallet_feed import parse_swap

    signatures = rpc.signatures_for_address(pair_address, limit=max_signatures)
    buyers: dict[str, float] = defaultdict(float)

    for entry in signatures:
        if not isinstance(entry, dict) or entry.get("err"):
            continue
        signature = entry.get("signature")
        if not signature:
            continue
        tx = rpc.get_transaction(signature)
        if tx is None:
            continue

        for wallet in _candidate_owners(tx):
            trade = parse_swap(tx, wallet, signature)
            if (
                trade is not None
                and trade.side is WalletSide.BUY
                and trade.mint == mint
                and trade.sol_amount >= min_sol_size
            ):
                buyers[wallet] += trade.sol_amount

    return dict(buyers)


def _candidate_owners(tx: dict) -> list[str]:
    """Token-account owners in this transaction — the humans, not the pool programs."""
    meta = tx.get("meta") or {}
    owners: dict[str, None] = {}
    for key in ("preTokenBalances", "postTokenBalances"):
        for entry in meta.get(key) or []:
            if isinstance(entry, dict):
                owner = entry.get("owner")
                if isinstance(owner, str) and owner:
                    owners.setdefault(owner, None)
    return list(owners)


def gather_candidates(
    dexscreener,
    rpc,
    min_appearances: int = 2,
    max_winners: int = 15,
    signatures_per_pool: int = 100,
    min_sol_size: float = 0.5,
    min_liquidity_usd: float = 50_000.0,
    min_gain_h24_pct: float = 40.0,
) -> list[Candidate]:
    """Candidates ranked by how many independent winners they were early on."""
    winners = find_winning_tokens(
        dexscreener,
        min_liquidity_usd=min_liquidity_usd,
        min_gain_h24_pct=min_gain_h24_pct,
        limit=max_winners,
    )
    if not winners:
        log.warning("no winning tokens found — nothing to derive candidates from")
        return []

    log.info("probing %d recent winners for early buyers", len(winners))
    candidates: dict[str, Candidate] = {}

    for snapshot in winners:
        try:
            buyers = buyers_of_pool(
                rpc, snapshot.pair_address, snapshot.mint,
                max_signatures=signatures_per_pool, min_sol_size=min_sol_size,
            )
        except Exception as exc:
            log.warning("could not read pool %s: %s", snapshot.pair_address[:8], exc)
            continue

        log.info(
            "%s (%+.0f%% 24h): %d qualifying buyers",
            snapshot.symbol or snapshot.mint[:8], snapshot.price_change_h24, len(buyers),
        )
        for wallet, sol in buyers.items():
            candidate = candidates.setdefault(wallet, Candidate(wallet=wallet))
            candidate.winners_bought.add(snapshot.mint)
            candidate.total_sol_bought += sol
            candidate.buys_seen += 1

    shortlist = [c for c in candidates.values() if c.appearances >= min_appearances]
    shortlist.sort(key=lambda c: (c.appearances, c.total_sol_bought), reverse=True)
    log.info(
        "%d wallets seen, %d appeared on >= %d winners",
        len(candidates), len(shortlist), min_appearances,
    )
    return shortlist


def audit_candidates(
    candidates: list[Candidate],
    feed,
    config: SmartMoneyConfig,
    now: float | None = None,
    max_audits: int = 25,
):
    """Run the full six-month audit over a shortlist. Returns (qualified, rejected)."""
    from .analysis import analyse

    since = None
    if now is not None:
        since = now - config.lookback_days * 86_400.0

    qualified = []
    rejected = []
    for candidate in candidates[:max_audits]:
        try:
            trades = feed.recent_trades(
                candidate.wallet,
                config.analysis_transactions,
                **({"since_ts": since, "max_pages": config.max_analysis_pages} if since else {}),
            )
        except TypeError:
            trades = feed.recent_trades(candidate.wallet, config.analysis_transactions)
        except Exception as exc:
            log.warning("audit failed for %s: %s", candidate.wallet[:8], exc)
            continue

        stats = analyse(candidate.wallet, trades, config, now=now)
        (qualified if stats.qualified else rejected).append((candidate, stats))

    qualified.sort(key=lambda pair: pair[1].score, reverse=True)
    return qualified, rejected
