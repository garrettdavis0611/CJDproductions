"""A synthetic market that drives the real engine.

What this is for
----------------
Paper trading needs a live data feed. When one is unavailable — or before you point
real capital at anything — this harness replaces only the *market data source* and
runs the genuine TradingEngine, MomentumStrategy, RiskManager, cost model and
portfolio accounting against a price process we control.

What it can tell you
--------------------
Whether the machinery behaves correctly under conditions you can specify: that costs
are actually charged, that the risk caps bind, that the exit ladder fires in the
right order, and — most usefully — whether the rug defences save money when a rug
actually happens.

What it CANNOT tell you
-----------------------
Whether the momentum thesis is true. The price process is written here, so any
"edge" the strategy shows against it is an edge I put there. A profitable result in
the `momentum` regime proves the strategy can capture autocorrelation *if it exists
in the real market*; it is not evidence that it does. Only recorded live data can
answer that.

Read the regimes as a controlled experiment, not as a forecast.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field

from .config import Config
from .models import SafetyReport, TokenSnapshot

log = logging.getLogger(__name__)

CYCLE_SECONDS = 300.0
"""One simulated cycle is five minutes, which is DexScreener's finest window."""

CYCLES_PER_HOUR = 12


@dataclass
class Regime:
    """A market's statistical character.

    `momentum_phi` is the autocorrelation of successive 5-minute log returns:
      > 0  trends persist   — momentum works
      = 0  random walk      — no edge exists to find
      < 0  moves reverse    — momentum is actively wrong
    """

    name: str
    drift_per_cycle: float = 0.0
    vol_per_cycle: float = 0.06
    momentum_phi: float = 0.0
    rug_probability_per_cycle: float = 0.0
    """Chance per token per cycle that liquidity is pulled."""
    liquidity_vol_per_cycle: float = 0.01
    liquidity_drift_per_cycle: float = 0.0
    description: str = ""


REGIMES: dict[str, Regime] = {
    "random_walk": Regime(
        name="random_walk",
        momentum_phi=0.0,
        description=(
            "The null: prices are a martingale, so no edge exists to find. Expect a "
            "loss — fees plus the cost of the exit ladder itself."
        ),
    ),
    "momentum": Regime(
        name="momentum",
        momentum_phi=0.35,
        liquidity_drift_per_cycle=0.002,
        description="Positive control: trends persist, so the strategy should profit.",
    ),
    "mean_reverting": Regime(
        name="mean_reverting",
        momentum_phi=-0.35,
        description="Moves reverse. A momentum strategy should lose clearly here.",
    ),
    "rug_infested": Regime(
        name="rug_infested",
        momentum_phi=0.20,
        rug_probability_per_cycle=0.0025,
        description="Trending, but ~1 token in 8 rugs over three days. Tests the defences.",
    ),
    "mixed": Regime(
        name="mixed",
        momentum_phi=0.15,
        rug_probability_per_cycle=0.0008,
        vol_per_cycle=0.08,
        description="A blend: weak trend, high volatility, occasional rugs.",
    ),
}


@dataclass
class _SimToken:
    mint: str
    symbol: str
    price: float
    liquidity: float
    created_ts: float
    prices: list[float] = field(default_factory=list)
    last_log_return: float = 0.0
    rugged: bool = False
    rug_stage: int = 0
    # Hooks used by the copy-trading harness: a skilled wallet's pick trends, and a
    # follower-farming wallet's pick collapses once it has its exit liquidity.
    boost_cycles: int = 0
    boost_drift: float = 0.0
    dump_in: int = -1
    dumping: bool = False

    def price_change_pct(self, cycles_back: int) -> float:
        if len(self.prices) <= cycles_back:
            return 0.0
        past = self.prices[-1 - cycles_back]
        if past <= 0:
            return 0.0
        return (self.prices[-1] / past - 1.0) * 100.0


class SimulatedMarket:
    """Stands in for DexScreenerClient. Advances only when `step()` is called."""

    def __init__(
        self,
        regime: Regime,
        rng: random.Random,
        universe_size: int = 60,
        start_ts: float = 1_700_000_000.0,
    ) -> None:
        self.regime = regime
        self.rng = rng
        self.now = start_ts
        self.tokens: dict[str, _SimToken] = {}
        self.rug_events: list[tuple[float, str]] = []
        self._rotation = 0

        for i in range(universe_size):
            mint = f"Sim{i:04d}" + "z" * 36
            token = _SimToken(
                mint=mint,
                symbol=f"SIM{i}",
                price=rng.uniform(0.0002, 0.004),
                liquidity=rng.uniform(40_000.0, 500_000.0),
                # Ages spread from 1h to 2 days so the age filter is exercised.
                created_ts=start_ts - rng.uniform(3_600.0, 172_800.0),
            )
            token.prices.append(token.price)
            self.tokens[mint] = token

    # ------------------------------------------------------------------ stepping

    def step(self) -> None:
        """Advance one five-minute cycle."""
        self.now += CYCLE_SECONDS
        r = self.regime
        for token in self.tokens.values():
            if token.rugged:
                self._advance_rug(token)
                continue

            if token.dump_in == 0 and not token.dumping:
                token.dumping = True
            if token.dumping:
                self._advance_dump(token)
                continue
            if token.dump_in > 0:
                token.dump_in -= 1

            if self.rng.random() < r.rug_probability_per_cycle:
                token.rugged = True
                self.rug_events.append((self.now, token.mint))
                self._advance_rug(token)
                continue

            shock = self.rng.gauss(0.0, r.vol_per_cycle)
            # -sigma^2/2 makes the price a martingale when momentum_phi is 0, so the
            # `random_walk` regime is a genuine null. Without it, the convexity of
            # exp() gives every token positive expected price growth and the control
            # quietly flatters the strategy.
            convexity = 0.5 * r.vol_per_cycle**2
            log_return = (
                r.drift_per_cycle - convexity + r.momentum_phi * token.last_log_return + shock
            )
            if token.boost_cycles > 0:
                log_return += token.boost_drift
                token.boost_cycles -= 1
            token.last_log_return = log_return
            token.price = max(1e-12, token.price * math.exp(log_return))
            token.prices.append(token.price)

            liquidity_move = r.liquidity_drift_per_cycle + self.rng.gauss(0.0, r.liquidity_vol_per_cycle)
            # Liquidity follows price somewhat: buyers add, sellers remove.
            token.liquidity = max(500.0, token.liquidity * math.exp(liquidity_move + 0.25 * log_return))

    def _advance_dump(self, token: _SimToken) -> None:
        """A wallet dumping into its followers.

        Deliberately different from a rug: the price collapses but the pool stays,
        because the seller is trading *through* the liquidity rather than removing it.
        That means the liquidity-drain exit will NOT save you here — only noticing that
        the wallet sold will. It is the case that separates the two defences.
        """
        token.price = max(1e-12, token.price * 0.55)
        token.liquidity = max(500.0, token.liquidity * 0.92)
        token.last_log_return = -0.6
        token.prices.append(token.price)

    def _advance_rug(self, token: _SimToken) -> None:
        """Liquidity leaves first, then the price collapses — the real sequence."""
        token.rug_stage += 1
        if token.rug_stage == 1:
            token.liquidity *= 0.35
            token.price *= 0.80
        elif token.rug_stage == 2:
            token.liquidity *= 0.15
            token.price *= 0.35
        else:
            token.liquidity = max(200.0, token.liquidity * 0.6)
            token.price = max(1e-12, token.price * 0.75)
        token.last_log_return = -0.5
        token.prices.append(token.price)

    # ------------------------------------------- DexScreenerClient-shaped surface

    def latest_token_profiles(self) -> list[str]:
        """Rotate the discovery feed so every token gets looked at over time."""
        mints = list(self.tokens)
        self._rotation = (self._rotation + 7) % max(1, len(mints))
        return mints[self._rotation :] + mints[: self._rotation]

    def latest_boosted_tokens(self) -> list[str]:
        return []

    def snapshots_for_mints(self, mints) -> dict[str, TokenSnapshot]:
        wanted = set(mints)
        out: dict[str, TokenSnapshot] = {}
        for mint in wanted:
            token = self.tokens.get(mint)
            if token is None:
                continue
            out[mint] = self._snapshot(token)
        return out

    def snapshot_for_mint(self, mint: str) -> TokenSnapshot | None:
        token = self.tokens.get(mint)
        return self._snapshot(token) if token else None

    def close(self) -> None:
        pass

    def _snapshot(self, token: _SimToken) -> TokenSnapshot:
        change_m5 = token.price_change_pct(1)
        change_h1 = token.price_change_pct(CYCLES_PER_HOUR)

        # Trade counts track the recent move: up candles draw buyers. This makes buy
        # pressure informative exactly when returns are autocorrelated, and useless
        # when they are not — which is the honest relationship.
        intensity = min(3.0, abs(change_m5) / 5.0)
        total_trades = max(2, int(self.rng.gauss(45 + 35 * intensity, 12)))
        tilt = 0.5 + max(-0.35, min(0.35, change_m5 / 40.0))
        buys = max(0, min(total_trades, int(total_trades * tilt)))

        turnover = self.rng.uniform(0.4, 2.5) * (1.0 + intensity)
        return TokenSnapshot(
            mint=token.mint,
            symbol=token.symbol,
            name=token.symbol,
            pair_address=f"pair-{token.mint[:8]}",
            dex="raydium",
            price_usd=token.price,
            liquidity_usd=token.liquidity,
            fdv_usd=token.liquidity * 11.0,
            volume_m5=token.liquidity * turnover / CYCLES_PER_HOUR,
            volume_h1=token.liquidity * turnover,
            volume_h24=token.liquidity * turnover * 9.0,
            buys_m5=buys,
            sells_m5=total_trades - buys,
            buys_h1=buys * CYCLES_PER_HOUR,
            sells_h1=(total_trades - buys) * CYCLES_PER_HOUR,
            price_change_m5=change_m5,
            price_change_h1=change_h1,
            price_change_h24=token.price_change_pct(CYCLES_PER_HOUR * 24),
            pair_created_at_ms=int(token.created_ts * 1000),
            ts=self.now,
        )


class PassingInspector:
    """A safety inspector whose tokens all pass the on-chain checks.

    This is the interesting case, not a shortcut: it models the *slow rug*, where
    every on-chain check is clean and the deployer pulls liquidity anyway. If the
    system only survived by rejecting rugs at the screening stage, it would be
    untested against the ones that get through.
    """

    def __init__(self) -> None:
        self.calls = 0

    def inspect(self, snapshot: TokenSnapshot, decimals_hint: int | None = None) -> SafetyReport:
        self.calls += 1
        return SafetyReport(
            mint=snapshot.mint,
            mint_authority_revoked=True,
            freeze_authority_revoked=True,
            lp_locked_pct=100.0,
            top10_holder_pct=20.0,
            rugcheck_score=10.0,
            rugcheck_risks=[],
            sell_route_ok=True,
            sell_price_impact_bps=70.0,
        )


class _SimClock:
    def __init__(self, market: SimulatedMarket) -> None:
        self.market = market

    def __call__(self) -> float:
        return self.market.now


def run_simulation(
    config: Config,
    regime: Regime,
    cycles: int = 864,
    seed: int = 0,
    universe_size: int = 60,
) -> dict[str, object]:
    """Run one paper session against a synthetic market. 864 cycles = 3 days."""
    from .engine import TradingEngine
    from .execution.paper import PaperBroker
    from .portfolio import Portfolio
    from .risk import RiskManager

    rng = random.Random(seed)
    market = SimulatedMarket(regime, rng, universe_size=universe_size)
    clock = _SimClock(market)

    config.engine.record_snapshots = False
    config.engine.poll_seconds = CYCLE_SECONDS

    portfolio = Portfolio(config.risk.starting_equity_usd)
    risk = RiskManager(config.risk)
    engine = TradingEngine(
        config=config,
        dexscreener=market,
        broker=PaperBroker(config.costs, rng=random.Random(seed + 1), clock=clock),
        portfolio=portfolio,
        risk=risk,
        safety=PassingInspector(),
        rpc=None,
        clock=clock,
    )

    for _ in range(cycles):
        market.step()
        try:
            engine.run_cycle()
        except Exception:
            log.exception("simulated cycle failed")

    # Mark whatever is still open at the last observed price.
    for mint in list(portfolio.positions):
        snapshot = market.snapshot_for_mint(mint)
        if snapshot is not None:
            portfolio.mark(mint, snapshot.price_usd)

    summary = portfolio.performance_summary()
    exits: dict[str, int] = {}
    for trade in portfolio.closed_trades:
        key = _exit_category(trade.exit_reason)
        exits[key] = exits.get(key, 0) + 1

    # Only positions still open when the rug started count as "caught in a rug".
    # Counting every trade on a token that eventually rugged dilutes the figure with
    # profitable pre-rug trades and makes the defences look better than they are.
    rug_ts = {mint: ts for ts, mint in market.rug_events}
    caught = [
        t
        for t in portfolio.closed_trades
        if t.mint in rug_ts and t.exit_ts >= rug_ts[t.mint] and t.entry_ts < rug_ts[t.mint]
    ]

    summary.update(
        {
            "regime": regime.name,
            "seed": seed,
            "cycles": cycles,
            "sim_days": round(cycles * CYCLE_SECONDS / 86400.0, 2),
            "halted": risk.state.halted_reason or "",
            "exit_reasons": exits,
            "rugs_in_market": len(rug_ts),
            "positions_caught_in_rug": len(caught),
            "pnl_caught_in_rug_usd": round(sum(t.pnl_usd for t in caught), 2),
            "mean_return_caught_in_rug_pct": (
                round(sum(t.pnl_pct for t in caught) / len(caught) * 100.0, 2) if caught else 0.0
            ),
            "worst_return_caught_in_rug_pct": (
                round(min(t.pnl_pct for t in caught) * 100.0, 2) if caught else 0.0
            ),
        }
    )
    return summary


_EXIT_CATEGORIES = (
    "liquidity drained",
    "stop loss",
    "trailing stop",
    "partial take profit",
    "take profit",
    "max hold",
    "no data",
)


def _exit_category(reason: str) -> str:
    """Collapse "liquidity drained 65% since entry" and friends into one bucket."""
    lowered = reason.lower()
    for category in _EXIT_CATEGORIES:
        if lowered.startswith(category):
            return category
    return reason.split("(")[0].strip() or "other"


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def aggregate(runs: list[dict[str, object]]) -> dict[str, object]:
    """Summarise a Monte Carlo sweep. A single run is an anecdote; report the spread."""
    if not runs:
        return {}
    returns = [float(r["total_return_pct"]) for r in runs]
    trades = [int(r["trades"]) for r in runs]
    fees = [float(r["fees_paid_usd"]) for r in runs]
    drawdowns = [float(r["max_drawdown_pct"]) for r in runs]
    win_rates = [float(r["win_rate_pct"]) for r in runs if int(r["trades"]) > 0]

    exit_totals: dict[str, int] = {}
    for run in runs:
        for reason, count in (run.get("exit_reasons") or {}).items():  # type: ignore[union-attr]
            exit_totals[reason] = exit_totals.get(reason, 0) + count

    caught = sum(int(r.get("positions_caught_in_rug") or 0) for r in runs)
    caught_pnl = sum(float(r.get("pnl_caught_in_rug_usd") or 0.0) for r in runs)
    caught_returns = [
        float(r["mean_return_caught_in_rug_pct"])
        for r in runs
        if int(r.get("positions_caught_in_rug") or 0) > 0
    ]
    caught_worst = [
        float(r["worst_return_caught_in_rug_pct"])
        for r in runs
        if int(r.get("positions_caught_in_rug") or 0) > 0
    ]

    return {
        "regime": runs[0]["regime"],
        "runs": len(runs),
        "positions_caught_in_rug": caught,
        "pnl_caught_in_rug_usd": round(caught_pnl, 2),
        "avg_usd_per_position_caught_in_rug": (
            round(caught_pnl / caught, 2) if caught else 0.0
        ),
        "mean_return_caught_in_rug_pct": (
            round(sum(caught_returns) / len(caught_returns), 2) if caught_returns else 0.0
        ),
        "worst_return_caught_in_rug_pct": round(min(caught_worst), 2) if caught_worst else 0.0,
        "median_return_pct": round(_quantile(returns, 0.5), 2),
        "mean_return_pct": round(sum(returns) / len(returns), 2),
        "p10_return_pct": round(_quantile(returns, 0.10), 2),
        "p90_return_pct": round(_quantile(returns, 0.90), 2),
        "worst_return_pct": round(min(returns), 2),
        "best_return_pct": round(max(returns), 2),
        "profitable_runs": sum(1 for r in returns if r > 0),
        "median_trades": round(_quantile([float(t) for t in trades], 0.5), 1),
        "total_trades": sum(trades),
        "median_win_rate_pct": round(_quantile(win_rates, 0.5), 1) if win_rates else 0.0,
        "median_fees_usd": round(_quantile(fees, 0.5), 2),
        "median_max_drawdown_pct": round(_quantile(drawdowns, 0.5), 2),
        "runs_halted": sum(1 for r in runs if r.get("halted")),
        "exit_reasons": dict(sorted(exit_totals.items(), key=lambda kv: -kv[1])),
    }


def sweep(
    config_factory,
    regime: Regime,
    seeds: list[int],
    cycles: int = 864,
    universe_size: int = 60,
) -> list[dict[str, object]]:
    """Run one regime across many seeds. `config_factory` returns a fresh Config per
    run so state cannot leak between them."""
    return [
        run_simulation(config_factory(), regime, cycles=cycles, seed=seed, universe_size=universe_size)
        for seed in seeds
    ]
