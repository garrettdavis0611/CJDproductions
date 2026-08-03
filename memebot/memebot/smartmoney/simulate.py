"""Experiments for the copy-trading machinery.

Two questions, both answerable without a live feed:

**1. Does qualification separate skill from luck?**
`selection_experiment` builds a population of wallets with known ground truth —
genuinely skilled, merely lucky, latency snipers, and follower-farmers — and reports
a confusion matrix for `qualify()`. This directly tests the central claim that the
luck filters are worth having. A filter that admits lucky wallets is worse than no
filter, because it launders randomness as evidence.

**2. Do the runtime gates protect us?**
`copy_experiment` runs the real engine with `CopyTradeStrategy` against a market
containing skilled wallets and farmers, and can toggle the drift gate and the
demotion logic to measure what each is worth.

As with the price regimes: the ground truth here is something I wrote. These
experiments validate the *mechanism*, not the premise that profitable copyable
wallets exist on Solana today.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from enum import Enum

from ..config import Config, SmartMoneyConfig
from .analysis import analyse
from .models import WalletSide, WalletTrade

log = logging.getLogger(__name__)

DAY = 86_400.0


class Archetype(str, Enum):
    SKILLED = "skilled"
    """Real edge: consistently positive across many tokens and many days."""
    LUCKY = "lucky"
    """No edge. Some members of a large population will look spectacular anyway."""
    SNIPER = "sniper"
    """Real edge, but it is latency. Uncopyable — you are always behind it."""
    FARMER = "farmer"
    """Profitable by dumping on followers. Designed to PASS the a-priori filters."""


@dataclass
class SynthWallet:
    address: str
    archetype: Archetype
    trades: list[WalletTrade] = field(default_factory=list)


def _episode(
    wallet: str, mint: str, entry_ts: float, hold_minutes: float, sol_in: float, ratio: float
) -> list[WalletTrade]:
    """One complete round trip expressed as a buy and a matching sell."""
    tokens = sol_in / max(1e-9, 0.001)
    exit_ts = entry_ts + hold_minutes * 60.0
    return [
        WalletTrade(
            wallet=wallet, mint=mint, side=WalletSide.BUY, token_amount=tokens,
            sol_amount=sol_in, ts=entry_ts, signature=f"{wallet[:6]}-{mint}-b",
        ),
        WalletTrade(
            wallet=wallet, mint=mint, side=WalletSide.SELL, token_amount=tokens,
            sol_amount=max(0.0, sol_in * ratio), ts=exit_ts, signature=f"{wallet[:6]}-{mint}-s",
        ),
    ]


def synth_history(
    address: str, archetype: Archetype, rng: random.Random, start_ts: float = 1_700_000_000.0
) -> list[WalletTrade]:
    """Generate a plausible trade history for a wallet of a known type."""
    trades: list[WalletTrade] = []

    # Histories span six-plus months: the temporal-stability gates cannot be
    # evaluated on a three-week sample, and neither can a claim of sustained success.
    if archetype is Archetype.SKILLED:
        count = rng.randint(36, 70)
        days = rng.randint(185, 240)
        for i in range(count):
            win = rng.random() < 0.58
            ratio = rng.uniform(1.25, 2.6) if win else rng.uniform(0.72, 0.95)
            trades += _episode(
                address, f"tok{i:03d}", start_ts + rng.uniform(0, days * DAY),
                rng.uniform(25.0, 400.0), rng.uniform(0.5, 4.0), ratio,
            )

    elif archetype is Archetype.LUCKY:
        # A losing coin-flipper — but with one enormous winner, which is precisely the
        # shape that fools a PnL leaderboard.
        count = rng.randint(20, 45)
        days = rng.randint(160, 220)
        for i in range(count):
            win = rng.random() < 0.33
            ratio = rng.uniform(1.1, 1.8) if win else rng.uniform(0.55, 0.9)
            trades += _episode(
                address, f"tok{i:03d}", start_ts + rng.uniform(0, days * DAY),
                rng.uniform(20.0, 300.0), rng.uniform(1.0, 3.0), ratio,
            )
        trades += _episode(
            address, "moonshot", start_ts + rng.uniform(0, days * DAY),
            rng.uniform(30.0, 200.0), 2.0, rng.uniform(25.0, 90.0),
        )

    elif archetype is Archetype.SNIPER:
        count = rng.randint(50, 95)
        days = rng.randint(185, 240)
        for i in range(count):
            win = rng.random() < 0.62
            ratio = rng.uniform(1.2, 2.2) if win else rng.uniform(0.75, 0.95)
            trades += _episode(
                address, f"tok{i:03d}", start_ts + rng.uniform(0, days * DAY),
                rng.uniform(0.5, 3.0),  # seconds-to-minutes holds
                rng.uniform(1.0, 5.0), ratio,
            )

    elif archetype is Archetype.FARMER:
        # Built to pass every a-priori gate: broad, consistent, long enough history,
        # steady month to month. Its profits come from followers, which no historical
        # metric can reveal.
        count = rng.randint(36, 60)
        days = rng.randint(185, 240)
        for i in range(count):
            win = rng.random() < 0.60
            ratio = rng.uniform(1.2, 2.0) if win else rng.uniform(0.8, 0.96)
            trades += _episode(
                address, f"tok{i:03d}", start_ts + rng.uniform(0, days * DAY),
                rng.uniform(20.0, 180.0), rng.uniform(1.0, 3.0), ratio,
            )

    trades.sort(key=lambda t: t.ts)
    return trades


def selection_experiment(
    config: SmartMoneyConfig | None = None,
    per_archetype: int = 150,
    seed: int = 0,
) -> dict[str, object]:
    """Confusion matrix for `qualify()` over a population with known ground truth."""
    cfg = config or SmartMoneyConfig(enabled=True)
    rng = random.Random(seed)
    results: dict[str, dict[str, int]] = {}

    for archetype in Archetype:
        accepted = 0
        for i in range(per_archetype):
            address = f"{archetype.value[:4]}{i:04d}" + "w" * 30
            stats = analyse(address, synth_history(address, archetype, rng), cfg)
            accepted += bool(stats.qualified)
        results[archetype.value] = {
            "population": per_archetype,
            "accepted": accepted,
            "rejected": per_archetype - accepted,
        }

    skilled = results[Archetype.SKILLED.value]["accepted"]
    lucky = results[Archetype.LUCKY.value]["accepted"]
    sniper = results[Archetype.SNIPER.value]["accepted"]
    farmer = results[Archetype.FARMER.value]["accepted"]
    total_accepted = skilled + lucky + sniper + farmer

    return {
        "per_archetype": per_archetype,
        "by_archetype": results,
        "skilled_recall_pct": round(skilled / per_archetype * 100.0, 1),
        "lucky_false_accept_pct": round(lucky / per_archetype * 100.0, 1),
        "sniper_false_accept_pct": round(sniper / per_archetype * 100.0, 1),
        "farmer_false_accept_pct": round(farmer / per_archetype * 100.0, 1),
        "precision_vs_luck_pct": (
            round(skilled / total_accepted * 100.0, 1) if total_accepted else 0.0
        ),
        "accepted_that_are_farmers_pct": (
            round(farmer / total_accepted * 100.0, 1) if total_accepted else 0.0
        ),
    }


# --------------------------------------------------------------------- live copy


@dataclass
class _MarketWallet:
    address: str
    archetype: Archetype
    holding: str | None = None
    entry_cycle: int = 0
    hold_cycles: int = 0


class CopyMarket:
    """Wraps a SimulatedMarket with wallets whose buys move (or wreck) the tokens."""

    def __init__(
        self,
        market,
        tracker,
        rng: random.Random,
        skilled: int = 6,
        farmers: int = 2,
        lucky: int = 4,
        focus_size: int = 5,
        focus_rotate_cycles: int = 6,
        buy_probability: float = 0.10,
    ) -> None:
        self.market = market
        self.tracker = tracker
        self.rng = rng
        self.cycle = 0
        self.focus_size = focus_size
        self.focus_rotate_cycles = max(1, focus_rotate_cycles)
        self.buy_probability = buy_probability
        self._focus: list[str] = []
        self.wallets: list[_MarketWallet] = []
        self.dump_events: list[tuple[float, str, str]] = []

        for i in range(skilled):
            self.wallets.append(_MarketWallet(f"skilled{i:03d}" + "w" * 30, Archetype.SKILLED))
        for i in range(farmers):
            self.wallets.append(_MarketWallet(f"farmer{i:03d}" + "w" * 31, Archetype.FARMER))
        for i in range(lucky):
            self.wallets.append(_MarketWallet(f"lucky{i:03d}" + "w" * 32, Archetype.LUCKY))

    def step(self) -> None:
        self.cycle += 1
        self.market.step()

        # Smart money clusters: these wallets all read the same screeners, so they
        # converge on a handful of tokens at a time rather than picking uniformly at
        # random. Without this the consensus requirement essentially never fires —
        # which is itself a real property of the design, see `focus_size`.
        if self.cycle % self.focus_rotate_cycles == 1 or not self._focus:
            mints = list(self.market.tokens)
            self.rng.shuffle(mints)
            self._focus = mints[: self.focus_size]

        for wallet in self.wallets:
            if wallet.holding is not None:
                if self.cycle - wallet.entry_cycle >= wallet.hold_cycles:
                    self._sell(wallet)
                continue
            if self.rng.random() > self.buy_probability:
                continue
            self._buy(wallet, self.rng.choice(self._focus))

    def _buy(self, wallet: _MarketWallet, mint: str) -> None:
        token = self.market.tokens[mint]
        if token.rugged or token.dumping:
            return
        wallet.holding = mint
        wallet.entry_cycle = self.cycle

        if wallet.archetype is Archetype.SKILLED:
            # Skill means the pick actually trends afterwards.
            token.boost_cycles = self.rng.randint(10, 30)
            token.boost_drift = self.rng.uniform(0.012, 0.030)
            wallet.hold_cycles = self.rng.randint(12, 36)
        elif wallet.archetype is Archetype.FARMER:
            # It buys, waits for followers to pile in, then dumps on them.
            token.dump_in = self.rng.randint(3, 8)
            token.boost_cycles = token.dump_in
            token.boost_drift = self.rng.uniform(0.010, 0.020)
            wallet.hold_cycles = token.dump_in
        else:
            wallet.hold_cycles = self.rng.randint(6, 30)

        self.tracker.observe_trade(
            wallet.address, mint, WalletSide.BUY, self.market.now, token.price
        )

    def _sell(self, wallet: _MarketWallet) -> None:
        mint = wallet.holding
        if mint is None:
            return
        token = self.market.tokens.get(mint)
        price = token.price if token else 0.0
        if wallet.archetype is Archetype.FARMER:
            self.dump_events.append((self.market.now, wallet.address, mint))
        self.tracker.observe_trade(wallet.address, mint, WalletSide.SELL, self.market.now, price)
        wallet.holding = None

    # DexScreener-shaped passthrough so the engine can use us directly.
    def latest_token_profiles(self):
        return self.market.latest_token_profiles()

    def latest_boosted_tokens(self):
        return []

    def snapshots_for_mints(self, mints):
        return self.market.snapshots_for_mints(mints)

    def snapshot_for_mint(self, mint):
        return self.market.snapshot_for_mint(mint)

    def close(self):
        pass

    @property
    def now(self) -> float:
        return self.market.now


def copy_experiment(
    config: Config,
    cycles: int = 864,
    seed: int = 0,
    universe_size: int = 40,
    drift_gate: bool = True,
    demotion: bool = True,
    wallet_exit: bool = True,
) -> dict[str, object]:
    """Run the real engine on wallet consensus, with each defence toggleable."""
    from ..engine import TradingEngine
    from ..execution.paper import PaperBroker
    from ..portfolio import Portfolio
    from ..risk import RiskManager
    from ..simulator import REGIMES, PassingInspector, SimulatedMarket, _exit_category
    from ..strategy.copytrade import CopyTradeStrategy
    from .tracker import SmartMoneyTracker

    sm = config.smart_money
    sm.enabled = True
    if not drift_gate:
        sm.max_price_drift_pct = 10_000.0
        sm.max_adverse_drift_pct = 10_000.0
        sm.max_signal_age_seconds = 10_000.0
    if not demotion:
        sm.min_attributed_trades = 10**9
    sm.exit_on_wallet_exit = wallet_exit
    config.engine.record_snapshots = False
    config.risk.min_seconds_between_entries = 0.0

    rng = random.Random(seed)
    market = SimulatedMarket(REGIMES["random_walk"], rng, universe_size=universe_size)
    tracker = SmartMoneyTracker(sm, clock=lambda: market.now)
    copy_market = CopyMarket(market, tracker, random.Random(seed + 5))

    # Follow every wallet in the market. Whether the machinery can tell them apart
    # afterwards, from the outcomes, is the thing being measured.
    from .models import WalletStats

    for wallet in copy_market.wallets:
        stats = WalletStats(wallet=wallet.address, qualified=True, score=0.8, closed_episodes=30)
        tracker.follow(stats)

    portfolio = Portfolio(config.risk.starting_equity_usd)
    risk = RiskManager(config.risk)
    strategy = CopyTradeStrategy(config.strategy, sm, tracker)
    engine = TradingEngine(
        config=config,
        dexscreener=copy_market,
        broker=PaperBroker(config.costs, rng=random.Random(seed + 1), clock=lambda: market.now),
        portfolio=portfolio,
        risk=risk,
        strategy=strategy,
        safety=PassingInspector(),
        rpc=None,
        clock=lambda: market.now,
        tracker=tracker,
    )

    for _ in range(cycles):
        copy_market.step()
        try:
            engine.run_cycle()
        except Exception:
            log.exception("copy cycle failed")

    for mint in list(portfolio.positions):
        snapshot = market.snapshot_for_mint(mint)
        if snapshot is not None:
            portfolio.mark(mint, snapshot.price_usd)

    exits: dict[str, int] = {}
    for trade in portfolio.closed_trades:
        key = _exit_category(trade.exit_reason)
        exits[key] = exits.get(key, 0) + 1

    by_type: dict[str, dict[str, float]] = {}
    for wallet in copy_market.wallets:
        record = tracker.attribution.get(wallet.address)
        if record is None or not record.copied_trades:
            continue
        bucket = by_type.setdefault(
            wallet.archetype.value, {"copied": 0, "pnl_usd": 0.0, "demoted": 0}
        )
        bucket["copied"] += record.copied_trades
        bucket["pnl_usd"] += record.realized_pnl_usd
        bucket["demoted"] += int(record.demoted)

    summary = portfolio.performance_summary()
    summary.update(
        {
            "seed": seed,
            "cycles": cycles,
            "drift_gate": drift_gate,
            "demotion": demotion,
            "wallet_exit": wallet_exit,
            "exit_reasons": exits,
            "farmer_dumps": len(copy_market.dump_events),
            "by_archetype": {
                k: {
                    "copied": int(v["copied"]),
                    "pnl_usd": round(v["pnl_usd"], 2),
                    "demoted_wallets": int(v["demoted"]),
                }
                for k, v in sorted(by_type.items())
            },
        }
    )
    return summary
