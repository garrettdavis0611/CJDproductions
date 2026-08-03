"""Command line interface.

    memebot scan                  screen live candidates, place no orders
    memebot paper                 run the loop with simulated fills (default mode)
    memebot live                  run the loop with real money (heavily gated)
    memebot backtest <file>       replay recorded snapshots
    memebot check <mint>          run the safety gauntlet against one token
    memebot costs                 show what a round trip costs you
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .config import Config, ConfigError, load_config
from .datasources.dexscreener import DexScreenerClient
from .datasources.jupiter import JupiterClient
from .datasources.rugcheck import RugCheckClient
from .datasources.solana_rpc import SolanaRpc
from .engine import TradingEngine
from .logging_setup import setup_logging
from .portfolio import Portfolio
from .risk import RiskManager
from .screening.safety import SafetyInspector

log = logging.getLogger("memebot")

DISCLAIMER = (
    "memebot is trading software, not a profit guarantee. Meme coins are a "
    "negative-sum game after fees; most tokens go to zero. Paper trade until you "
    "have evidence of an edge, then risk only money you can lose entirely."
)


def build_clients(config: Config) -> dict[str, object]:
    eng = config.engine
    jupiter_key = os.environ.get(config.execution.jupiter_api_key_env) or None
    base_url = config.execution.jupiter_base_url
    if jupiter_key and "lite-api" in base_url:
        base_url = "https://api.jup.ag"

    return {
        "dexscreener": DexScreenerClient(
            eng.dexscreener_base_url, timeout=eng.http_timeout_seconds, chain=eng.chain
        ),
        "rugcheck": RugCheckClient(
            eng.rugcheck_base_url,
            timeout=eng.http_timeout_seconds,
            api_key=os.environ.get("RUGCHECK_API_KEY") or None,
        ),
        "jupiter": JupiterClient(
            base_url, timeout=eng.http_timeout_seconds, api_key=jupiter_key
        ),
        "rpc": SolanaRpc(config.execution.solana_rpc_url, timeout=eng.http_timeout_seconds),
    }


def build_engine(config: Config, dry_run: bool) -> tuple[TradingEngine, dict[str, object]]:
    clients = build_clients(config)
    inspector = SafetyInspector(
        config.screening,
        rpc=clients["rpc"],
        rugcheck=clients["rugcheck"],
        jupiter=clients["jupiter"],
        slippage_bps=config.execution.slippage_bps,
    )

    data_dir = Path(config.engine.data_dir)
    portfolio = Portfolio(config.risk.starting_equity_usd, trade_log_path=data_dir / "trades.jsonl")

    if dry_run:
        broker = _NullBroker()
    elif config.execution.mode == "live":
        from .execution.jupiter_broker import JupiterBroker

        broker = JupiterBroker(
            config.execution,
            config.costs,
            jupiter=clients["jupiter"],
            rpc=clients["rpc"],
            sol_price_provider=lambda: _sol_price(clients["dexscreener"]),
        )
    else:
        from .execution.paper import PaperBroker

        broker = PaperBroker(config.costs)

    engine = TradingEngine(
        config=config,
        dexscreener=clients["dexscreener"],
        broker=broker,
        portfolio=portfolio,
        risk=RiskManager(config.risk),
        safety=inspector,
        rpc=clients["rpc"],
    )
    return engine, clients


def _sol_price(dexscreener: DexScreenerClient) -> float:
    from .models import WSOL_MINT

    snapshot = dexscreener.snapshot_for_mint(WSOL_MINT)
    return snapshot.price_usd if snapshot else 0.0


class _NullBroker:
    """Used by `scan`: everything runs except the order."""

    simulated = True

    def buy(self, *_args, **_kwargs):
        from .execution.base import OrderRejected

        raise OrderRejected("scan mode — no orders placed")

    def sell(self, *_args, **_kwargs):
        from .execution.base import OrderRejected

        raise OrderRejected("scan mode — no orders placed")


def _close(clients: dict[str, object]) -> None:
    for client in clients.values():
        closer = getattr(client, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass


# ------------------------------------------------------------------- commands


def cmd_scan(args: argparse.Namespace, config: Config) -> int:
    engine, clients = build_engine(config, dry_run=True)
    try:
        engine.run_forever(max_cycles=args.cycles)
    finally:
        _close(clients)
    return 0


def cmd_paper(args: argparse.Namespace, config: Config) -> int:
    config.execution.mode = "paper"
    engine, clients = build_engine(config, dry_run=False)
    try:
        engine.run_forever(max_cycles=args.cycles)
    finally:
        _report(engine)
        _close(clients)
    return 0


def cmd_live(args: argparse.Namespace, config: Config) -> int:
    config.execution.mode = "live"
    print(f"\n!!! LIVE TRADING — REAL MONEY !!!\n{DISCLAIMER}\n")
    print(
        f"Wallet cap per order: ${config.execution.live_max_trade_usd:.2f} | "
        f"max concurrent: {config.risk.max_concurrent_positions} | "
        f"daily loss halt: {config.risk.max_daily_loss_fraction:.0%}\n"
    )
    if not args.yes:
        if input("Type LIVE to continue: ").strip() != "LIVE":
            print("aborted")
            return 1

    from .execution.jupiter_broker import LiveTradingDisabled

    try:
        engine, clients = build_engine(config, dry_run=False)
    except LiveTradingDisabled as exc:
        log.error("live trading blocked: %s", exc)
        return 1
    try:
        engine.run_forever(max_cycles=args.cycles)
    finally:
        _report(engine)
        _close(clients)
    return 0


def cmd_backtest(args: argparse.Namespace, config: Config) -> int:
    from .backtest import load_snapshots, run_backtest

    path = Path(args.snapshots)
    if not path.exists():
        log.error("no snapshot file at %s — run `memebot scan` first to record data", path)
        return 1
    snapshots = load_snapshots(path)
    if not snapshots:
        log.error("%s contained no usable snapshots", path)
        return 1

    result = run_backtest(config, snapshots, seed=args.seed)
    print(json.dumps(result.summary(), indent=2))
    print(
        "\nReminder: this replays only the tokens you recorded, and assumes fills at "
        "observed prices. It is evidence, not proof."
    )
    return 0


def cmd_check(args: argparse.Namespace, config: Config) -> int:
    clients = build_clients(config)
    try:
        dexscreener: DexScreenerClient = clients["dexscreener"]  # type: ignore[assignment]
        snapshot = dexscreener.snapshot_for_mint(args.mint)
        if snapshot is None:
            log.error("no DexScreener pair found for %s", args.mint)
            return 1

        inspector = SafetyInspector(
            config.screening,
            rpc=clients["rpc"],
            rugcheck=clients["rugcheck"],
            jupiter=clients["jupiter"],
            slippage_bps=config.execution.slippage_bps,
        )
        engine_history = [snapshot]
        from .screening.filters import FilterContext, screen

        safety = inspector.inspect(snapshot)
        result = screen(
            FilterContext(snapshot=snapshot, safety=safety, config=config.screening)
        )

        print(f"\n{snapshot.symbol or '?'} ({snapshot.name or '?'})  {args.mint}")
        print(f"  price      ${snapshot.price_usd:,.10g}")
        print(f"  liquidity  ${snapshot.liquidity_usd:,.0f}")
        print(f"  FDV        ${snapshot.fdv_usd:,.0f}")
        print(f"  age        {snapshot.age_minutes / 60:,.1f}h")
        print(f"  24h volume ${snapshot.volume_h24:,.0f}  (1h turnover {snapshot.vol_liq_ratio_h1:.1f}x)")
        print(f"  5m flow    {snapshot.buys_m5}B / {snapshot.sells_m5}S  ({snapshot.buy_pressure_m5:.0%} buys)")
        print("\n  safety:")
        print(f"    mint authority revoked   {_fmt(safety.mint_authority_revoked)}")
        print(f"    freeze authority revoked {_fmt(safety.freeze_authority_revoked)}")
        print(f"    LP locked/burned         {_pct(safety.lp_locked_pct)}")
        print(f"    top-10 holders           {_pct(safety.top10_holder_pct)}")
        print(f"    risk score               {safety.rugcheck_score if safety.rugcheck_score is not None else 'unknown'}")
        print(f"    sell route               {_fmt(safety.sell_route_ok)}"
              f"{f' ({safety.sell_price_impact_bps:.0f} bps impact)' if safety.sell_price_impact_bps is not None else ''}")
        if safety.errors:
            print(f"    data errors              {'; '.join(safety.errors)}")

        verdict = "PASS" if result.passed else "REJECT"
        print(f"\n  verdict: {verdict}")
        for fail in result.hard_fails:
            print(f"    [hard] {fail}")
        for flag in result.soft_flags:
            print(f"    [soft] {flag}")
        print()
        return 0 if result.passed else 2
    finally:
        _close(clients)


def cmd_costs(_args: argparse.Namespace, config: Config) -> int:
    from .execution.base import CostModel

    c, s = config.costs, config.strategy
    model = CostModel(c)
    network = model.network_fee_usd()
    round_trip_bps = config.round_trip_cost_bps

    print("\nRound-trip cost model")
    print(f"  DEX fee            {c.dex_fee_bps:.0f} bps per leg")
    print(f"  platform fee       {c.jupiter_platform_fee_bps:.0f} bps per leg")
    print(f"  slippage allowance {c.extra_slippage_bps:.0f} bps per leg (plus quoted price impact)")
    print(f"  network fee        ${network:.4f} per leg "
          f"({c.priority_fee_lamports + c.base_tx_fee_lamports:,} lamports @ ${c.sol_price_usd:.0f}/SOL)")
    print(f"  failed-tx rate     {c.failed_tx_probability:.0%} (fee paid, no fill)")
    print(f"\n  total round trip   {round_trip_bps:.0f} bps ({round_trip_bps / 100:.2f}%) + ${2 * network:.4f}")

    for size in (25.0, 50.0, 100.0, 250.0):
        cost = size * round_trip_bps / 10_000.0 + 2 * network
        print(f"    ${size:>6,.0f} position -> ${cost:6.2f} to enter and exit ({cost / size:.2%})")

    breakeven = round_trip_bps / 100.0
    print(f"\n  You need +{breakeven:.2f}% just to break even on a round trip.")
    print(f"  Stop loss is {s.stop_loss_pct:.0%}, take profit {s.take_profit_pct:.0%}.")
    rr = s.take_profit_pct / s.stop_loss_pct
    required_win_rate = 1.0 / (1.0 + rr)
    print(f"  Reward:risk {rr:.2f}:1 -> you need a >{required_win_rate:.0%} win rate before costs.")
    print(f"\n{DISCLAIMER}\n")
    return 0


def _build_wallet_feed(config: Config, clients: dict[str, object]):
    from .datasources.wallet_feed import BirdeyeWalletFeed, SolanaWalletFeed

    if config.smart_money.feed == "birdeye":
        key = os.environ.get("BIRDEYE_API_KEY")
        if not key:
            raise RuntimeError("smart_money.feed is 'birdeye' but BIRDEYE_API_KEY is not set")
        return BirdeyeWalletFeed(key, timeout=config.engine.http_timeout_seconds)
    return SolanaWalletFeed(
        clients["rpc"],
        max_transactions=config.smart_money.analysis_transactions,
        max_pages=config.smart_money.max_analysis_pages,
    )


def cmd_wallets(args: argparse.Namespace, config: Config) -> int:
    """Analyse wallets and report whether they survive the luck filters."""
    from .smartmoney.analysis import analyse

    wallets = list(args.wallets) or list(config.smart_money.watchlist)
    if not wallets:
        log.error("no wallets given and smart_money.watchlist is empty")
        return 1

    import time

    now = time.time()
    since = now - config.smart_money.lookback_days * 86_400.0

    clients = build_clients(config)
    try:
        feed = _build_wallet_feed(config, clients)
        qualified = 0
        for wallet in wallets:
            try:
                trades = feed.recent_trades(
                    wallet, config.smart_money.analysis_transactions,
                    since_ts=since, max_pages=config.smart_money.max_analysis_pages,
                )
            except TypeError:
                trades = feed.recent_trades(wallet, config.smart_money.analysis_transactions)

            stats = analyse(wallet, trades, config.smart_money, now=now)
            verdict = "QUALIFIED" if stats.qualified else "REJECTED"
            print(f"\n{wallet}  ->  {verdict}   (score {stats.score:.2f})")
            print(f"  trades analysed     {stats.trades_analysed}")
            print(f"  closed round trips  {stats.closed_episodes}")
            print(f"  distinct tokens     {stats.distinct_tokens}")
            print(f"  realized PnL        {stats.realized_pnl_sol:+.3f} SOL")
            print(f"  win rate            {stats.win_rate:.0%}")
            print(f"  median hold         {stats.median_hold_minutes:.1f} min")
            print(f"  best-token share    {stats.best_token_profit_share:.0%} of gross profit")
            print(f"  active days         {stats.active_days}")
            print(f"  history span        {stats.history_days:.0f} days")
            print(f"  months profitable   {stats.profitable_months}/{stats.months_covered}")
            print(f"  own max drawdown    {stats.wallet_max_drawdown_pct:.0f}%")
            print(
                f"  recent vs prior     {stats.recent_pnl_sol:+.2f} vs "
                f"{stats.prior_pnl_sol:+.2f} SOL"
                f"{'   DECAYING' if stats.is_decaying else ''}"
            )
            print(f"  last traded         {stats.days_since_last_trade:.1f} days ago")
            if stats.monthly_pnl_sol:
                months = "  ".join(
                    f"{m}:{v:+.1f}" for m, v in sorted(stats.monthly_pnl_sol.items())
                )
                print(f"  by month (SOL)      {months}")
            for reason in stats.disqualifiers:
                print(f"    [fail] {reason}")
            qualified += bool(stats.qualified)

        print(f"\n{qualified}/{len(wallets)} wallet(s) qualified.")
        if not qualified:
            print(
                "Rejections are the normal outcome. Most wallets with impressive PnL fail "
                "the concentration or sample-size gates, which is exactly what they are for."
            )
        return 0
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    finally:
        _close(clients)


def cmd_discover(args: argparse.Namespace, config: Config) -> int:
    """Derive candidate wallets from chain data and audit them over six months."""
    import time

    from .smartmoney.discover import audit_candidates, gather_candidates

    clients = build_clients(config)
    try:
        feed = _build_wallet_feed(config, clients)
    except RuntimeError as exc:
        log.error("%s", exc)
        _close(clients)
        return 1

    try:
        candidates = gather_candidates(
            clients["dexscreener"], clients["rpc"],
            min_appearances=args.min_appearances,
            max_winners=args.winners,
            signatures_per_pool=args.signatures,
            min_sol_size=args.min_sol,
        )
        if not candidates:
            print(
                "\nNo candidates. Either the discovery feeds returned nothing reachable, "
                "or no wallet appeared on enough independent winners.\n"
                "Try --min-appearances 1 (noisier) or --winners 30 (slower)."
            )
            return 1

        print(f"\n{len(candidates)} shortlisted wallet(s). Auditing the top {args.limit}...\n")
        qualified, rejected = audit_candidates(
            candidates, feed, config.smart_money, now=time.time(), max_audits=args.limit
        )

        for candidate, stats in qualified:
            print(f"QUALIFIED  {candidate.wallet}   score {stats.score:.2f}")
            _print_wallet_stats(stats, candidate)
        for candidate, stats in rejected:
            print(f"rejected   {candidate.wallet}   ({len(stats.disqualifiers)} failed gate(s))")
            for reason in stats.disqualifiers[:4]:
                print(f"             - {reason}")

        print(f"\n{len(qualified)} qualified, {len(rejected)} rejected.")
        if qualified:
            flags = " ".join(f"--wallet {c.wallet}" for c, _ in qualified[:5])
            print(f"\nPaper trade them before risking anything:\n  python -m memebot copy {flags}")
        else:
            print(
                "\nZero qualified is a normal and healthy outcome. The six-month gates are "
                "strict on purpose; a wallet that is hot this week almost never passes them."
            )
        return 0
    finally:
        _close(clients)


def _print_wallet_stats(stats, candidate=None) -> None:
    if candidate is not None:
        print(
            f"  seen on {candidate.appearances} winner(s), "
            f"{candidate.total_sol_bought:.1f} SOL deployed"
        )
    print(
        f"  {stats.closed_episodes} round trips over {stats.history_days:.0f} days, "
        f"{stats.distinct_tokens} tokens"
    )
    print(
        f"  {stats.realized_pnl_sol:+.2f} SOL   win rate {stats.win_rate:.0%}   "
        f"median hold {stats.median_hold_minutes:.0f}m"
    )
    print(
        f"  profitable in {stats.profitable_months}/{stats.months_covered} months   "
        f"own drawdown {stats.wallet_max_drawdown_pct:.0f}%   "
        f"best token = {stats.best_token_profit_share:.0%} of profit"
    )
    print(
        f"  recent half {stats.recent_pnl_sol:+.2f} SOL vs prior half "
        f"{stats.prior_pnl_sol:+.2f} SOL"
        f"{'   DECAYING' if stats.is_decaying else ''}"
    )
    if stats.monthly_pnl_sol:
        months = "  ".join(
            f"{m[-2:]}:{v:+.1f}" for m, v in sorted(stats.monthly_pnl_sol.items())
        )
        print(f"  by month: {months}")


def cmd_copy(args: argparse.Namespace, config: Config) -> int:
    """Run the loop using wallet consensus instead of price momentum."""
    config.smart_money.enabled = True
    if args.live:
        config.execution.mode = "live"
    else:
        config.execution.mode = "paper"

    wallets = list(args.wallet) or list(config.smart_money.watchlist)
    if not wallets:
        log.error(
            "no wallets to follow. Pass --wallet <address> (repeatable) or set "
            "smart_money.watchlist in the config."
        )
        return 1

    from .smartmoney.tracker import SmartMoneyTracker
    from .smartmoney.watcher import WalletWatcher
    from .strategy.copytrade import CopyTradeStrategy

    clients = build_clients(config)
    data_dir = Path(config.engine.data_dir)
    try:
        feed = _build_wallet_feed(config, clients)
    except RuntimeError as exc:
        log.error("%s", exc)
        _close(clients)
        return 1

    tracker = SmartMoneyTracker(
        config.smart_money, state_path=data_dir / config.smart_money.state_file
    )
    tracker.load()
    watcher = WalletWatcher(
        config.smart_money, feed, tracker,
        sol_price_provider=lambda: _sol_price(clients["dexscreener"]),
    )

    log.info("analysing %d candidate wallet(s)...", len(wallets))
    followed = watcher.bootstrap(wallets)
    if not followed:
        log.error(
            "none of the %d candidate wallets qualified — refusing to trade. "
            "Run `memebot wallets <address>` to see why.", len(wallets)
        )
        tracker.save()
        _close(clients)
        return 1
    log.info("following %d wallet(s)", len(followed))

    inspector = SafetyInspector(
        config.screening, rpc=clients["rpc"], rugcheck=clients["rugcheck"],
        jupiter=clients["jupiter"], slippage_bps=config.execution.slippage_bps,
    )
    portfolio = Portfolio(config.risk.starting_equity_usd, trade_log_path=data_dir / "trades.jsonl")

    if config.execution.mode == "live":
        from .execution.jupiter_broker import JupiterBroker

        broker = JupiterBroker(
            config.execution, config.costs, jupiter=clients["jupiter"], rpc=clients["rpc"],
            sol_price_provider=lambda: _sol_price(clients["dexscreener"]),
        )
    else:
        from .execution.paper import PaperBroker

        broker = PaperBroker(config.costs)

    engine = TradingEngine(
        config=config,
        dexscreener=clients["dexscreener"],
        broker=broker,
        portfolio=portfolio,
        risk=RiskManager(config.risk),
        strategy=CopyTradeStrategy(config.strategy, config.smart_money, tracker),
        safety=inspector,
        rpc=clients["rpc"],
        wallet_watcher=watcher,
        tracker=tracker,
    )

    try:
        engine.run_forever(max_cycles=args.cycles)
    finally:
        tracker.save()
        _report(engine)
        _print_attribution(tracker)
        _close(clients)
    return 0


def _print_attribution(tracker) -> None:
    records = [a for a in tracker.attribution.values() if a.copied_trades]
    if not records:
        return
    print("\nper-wallet attribution (did following them actually pay?)")
    for record in sorted(records, key=lambda a: a.realized_pnl_usd):
        flag = "  DEMOTED" if record.demoted else ""
        print(
            f"  {record.wallet[:10]}  {record.copied_trades:3d} copied  "
            f"${record.realized_pnl_usd:+8.2f}  win {record.win_rate:.0%}{flag}"
        )
        if record.demoted:
            print(f"      {record.demoted_reason}")


def cmd_simulate(args: argparse.Namespace, config: Config) -> int:
    """Drive the real engine against a synthetic market.

    Used when no live feed is available, and as a controlled experiment: the rug
    regimes test the defences, and `random_walk` is the null hypothesis.
    """
    from .simulator import REGIMES, Regime, aggregate, sweep

    if args.selection:
        return _run_selection_experiment(config, args)
    if args.copy:
        return _run_copy_experiment(args)

    def fresh_config() -> Config:
        cfg = load_config(Path(args.config) if Path(args.config).exists() else None)
        cfg.engine.record_snapshots = False
        cfg.risk.min_seconds_between_entries = 0.0
        if args.no_rug_defence:
            # Effectively disables the liquidity-drain exit for the A/B comparison.
            cfg.strategy.liquidity_drain_exit_pct = 0.999
        return cfg

    names = list(REGIMES) if args.regime == "all" else [args.regime]
    seeds = list(range(args.seeds))
    results: dict[str, object] = {}

    for name in names:
        regime: Regime = REGIMES[name]
        log.info("simulating %s: %s", name, regime.description)
        runs = sweep(fresh_config, regime, seeds, cycles=args.cycles, universe_size=args.universe)
        summary = aggregate(runs)
        summary["description"] = regime.description
        results[name] = summary
        _print_regime(summary)

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(results, indent=2))
        print(f"\nwrote {args.json_out}")

    print(
        "\nThese are simulated markets. A profitable result in the `momentum` regime "
        "shows the strategy can capture autocorrelation that exists — it is NOT "
        "evidence that real meme coins are autocorrelated. Only recorded live data "
        "can answer that."
    )
    return 0


def _run_selection_experiment(config: Config, args: argparse.Namespace) -> int:
    """Confusion matrix for the wallet luck filters, against known ground truth."""
    from .smartmoney.simulate import selection_experiment

    result = selection_experiment(config.smart_money, per_archetype=args.population, seed=0)
    print(f"\nWallet selection, {result['per_archetype']} synthetic wallets per archetype\n")
    print(f"  {'archetype':10s} {'accepted':>9s} {'rejected':>9s}   verdict")
    verdicts = {
        "skilled": "want these",
        "lucky": "must reject — no edge, just variance",
        "sniper": "must reject — edge is latency we cannot copy",
        "farmer": "PASSES by design — only demotion catches these",
    }
    for name, bucket in result["by_archetype"].items():  # type: ignore[union-attr]
        print(
            f"  {name:10s} {bucket['accepted']:9d} {bucket['rejected']:9d}   {verdicts.get(name, '')}"
        )
    print(f"\n  skilled recall           {result['skilled_recall_pct']:.1f}%")
    print(f"  lucky false accepts      {result['lucky_false_accept_pct']:.1f}%")
    print(f"  sniper false accepts     {result['sniper_false_accept_pct']:.1f}%")
    print(f"  farmer false accepts     {result['farmer_false_accept_pct']:.1f}%  (expected: high)")
    print(f"  of accepted, farmers     {result['accepted_that_are_farmers_pct']:.1f}%")
    print(
        "\n  Farmers are supposed to pass: nothing in a trade history reveals that a wallet "
        "\n  intends to dump on its followers. That is what attribution and demotion are for."
    )
    return 0


def _run_copy_experiment(args: argparse.Namespace) -> int:
    """A/B each copy-trade defence against a market containing skilled wallets and farmers."""
    import statistics

    from .smartmoney.simulate import copy_experiment

    variants = [
        ("all defences ON", {}),
        ("drift gate OFF", {"drift_gate": False}),
        ("wallet-exit signal OFF", {"wallet_exit": False}),
        ("demotion OFF", {"demotion": False}),
        ("all defences OFF", {"drift_gate": False, "wallet_exit": False, "demotion": False}),
    ]
    seeds = list(range(args.seeds))
    print(f"\nCopy-trade defences, {len(seeds)} runs x {args.cycles} cycles\n")

    for label, toggles in variants:
        runs = [
            copy_experiment(
                load_config(Path(args.config) if Path(args.config).exists() else None),
                cycles=args.cycles, seed=seed, universe_size=args.universe, **toggles,
            )
            for seed in seeds
        ]
        returns = [float(r["total_return_pct"]) for r in runs]
        trades = [int(r["trades"]) for r in runs]
        print(
            f"  {label:24s} median {statistics.median(returns):+7.2f}%  "
            f"mean {sum(returns) / len(returns):+7.2f}%  "
            f"worst {min(returns):+7.2f}%  "
            f"profitable {sum(1 for r in returns if r > 0)}/{len(runs)}  "
            f"trades {statistics.median(trades):.0f}"
        )
        totals: dict[str, dict[str, float]] = {}
        for run in runs:
            for archetype, bucket in (run.get("by_archetype") or {}).items():  # type: ignore[union-attr]
                acc = totals.setdefault(archetype, {"copied": 0.0, "pnl": 0.0, "demoted": 0.0})
                acc["copied"] += bucket["copied"]
                acc["pnl"] += bucket["pnl_usd"]
                acc["demoted"] += bucket["demoted_wallets"]
        for archetype, acc in sorted(totals.items()):
            print(
                f"      {archetype:8s} copied {int(acc['copied']):4d}  "
                f"attributed ${acc['pnl']:+9.2f}  demoted {int(acc['demoted']):2d}"
            )
    print(
        "\n  The skilled wallets here have real foresight because the harness gives it to "
        "\n  them. This measures whether the defences work, not whether such wallets exist."
    )
    return 0


def _print_regime(summary: dict[str, object]) -> None:
    print(f"\n=== {summary['regime']}  ({summary['runs']} runs x {summary.get('description', '')})")
    print(
        f"  return   median {summary['median_return_pct']:+.2f}%   "
        f"p10 {summary['p10_return_pct']:+.2f}%   p90 {summary['p90_return_pct']:+.2f}%   "
        f"worst {summary['worst_return_pct']:+.2f}%"
    )
    print(
        f"  profitable in {summary['profitable_runs']}/{summary['runs']} runs   "
        f"median trades {summary['median_trades']:.0f}   "
        f"median win rate {summary['median_win_rate_pct']:.0f}%"
    )
    print(
        f"  median fees ${summary['median_fees_usd']:.2f}   "
        f"median max drawdown {summary['median_max_drawdown_pct']:.2f}%   "
        f"halted in {summary['runs_halted']}/{summary['runs']} runs"
    )
    if summary.get("exit_reasons"):
        reasons = "  ".join(f"{k}={v}" for k, v in summary["exit_reasons"].items())  # type: ignore[union-attr]
        print(f"  exits: {reasons}")


def _report(engine: TradingEngine) -> None:
    summary = engine.portfolio.performance_summary()
    print("\n" + json.dumps(summary, indent=2))
    if engine.portfolio.positions:
        print(f"\n{len(engine.portfolio.positions)} position(s) still open:")
        for position in engine.portfolio.positions.values():
            print(
                f"  {position.symbol or position.mint[:8]:<12} "
                f"${position.cost_usd:8.2f} cost  {position.unrealized_pnl_pct:+7.1%}  "
                f"{position.hold_minutes():.0f}m"
            )


def _fmt(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "NO")


def _pct(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.1f}%"


# ------------------------------------------------------------------- argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memebot",
        description="Screened, risk-managed Solana meme-coin trading bot. " + DISCLAIMER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config YAML")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="screen candidates without trading")
    scan.add_argument("--cycles", type=int, default=None, help="stop after N cycles")
    scan.set_defaults(func=cmd_scan)

    paper = sub.add_parser("paper", help="run with simulated fills")
    paper.add_argument("--cycles", type=int, default=None)
    paper.set_defaults(func=cmd_paper)

    live = sub.add_parser("live", help="run with real money (gated)")
    live.add_argument("--cycles", type=int, default=None)
    live.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    live.set_defaults(func=cmd_live)

    backtest = sub.add_parser("backtest", help="replay recorded snapshots")
    backtest.add_argument("snapshots", nargs="?", default="data/snapshots.jsonl")
    backtest.add_argument("--seed", type=int, default=1234)
    backtest.set_defaults(func=cmd_backtest)

    check = sub.add_parser("check", help="run the safety gauntlet on one mint")
    check.add_argument("mint")
    check.set_defaults(func=cmd_check)

    costs = sub.add_parser("costs", help="show the round-trip cost model")
    costs.set_defaults(func=cmd_costs)

    wallets = sub.add_parser("wallets", help="analyse wallets for copy trading")
    wallets.add_argument("wallets", nargs="*", help="addresses (default: config watchlist)")
    wallets.set_defaults(func=cmd_wallets)

    discover = sub.add_parser(
        "discover", help="find candidate wallets from chain data, then audit them"
    )
    discover.add_argument("--winners", type=int, default=15, help="recent winners to probe")
    discover.add_argument(
        "--min-appearances", type=int, default=2,
        help="how many independent winners a wallet must appear on",
    )
    discover.add_argument("--signatures", type=int, default=100, help="swaps to read per pool")
    discover.add_argument("--min-sol", type=float, default=0.5, help="ignore buys below this size")
    discover.add_argument("--limit", type=int, default=25, help="wallets to fully audit")
    discover.set_defaults(func=cmd_discover)

    copy = sub.add_parser("copy", help="trade on tracked-wallet consensus")
    copy.add_argument("--wallet", action="append", default=[], help="repeatable")
    copy.add_argument("--cycles", type=int, default=None)
    copy.add_argument("--live", action="store_true", help="real money (still gated)")
    copy.set_defaults(func=cmd_copy)

    simulate = sub.add_parser("simulate", help="run the engine against a synthetic market")
    simulate.add_argument(
        "--regime", default="all",
        help="random_walk | momentum | mean_reverting | rug_infested | mixed | all",
    )
    simulate.add_argument("--seeds", type=int, default=20, help="Monte Carlo runs per regime")
    simulate.add_argument("--cycles", type=int, default=864, help="5-minute cycles (864 = 3 days)")
    simulate.add_argument("--universe", type=int, default=60, help="tokens in the synthetic market")
    simulate.add_argument(
        "--no-rug-defence", action="store_true",
        help="disable the liquidity-drain exit, to measure what it is worth",
    )
    simulate.add_argument("--json-out", default=None, help="write full results to this path")
    simulate.add_argument(
        "--selection", action="store_true",
        help="instead: confusion matrix for the wallet luck filters",
    )
    simulate.add_argument(
        "--copy", action="store_true", help="instead: A/B the copy-trade defences"
    )
    simulate.add_argument(
        "--population", type=int, default=150, help="wallets per archetype for --selection",
    )
    simulate.set_defaults(func=cmd_simulate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config_path = Path(args.config)
    try:
        config = load_config(config_path if config_path.exists() else None)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    level = args.log_level or config.engine.log_level
    setup_logging(level, log_file=Path(config.engine.data_dir) / "memebot.log")
    if not config_path.exists():
        log.warning("no config at %s — using built-in defaults", config_path)

    return args.func(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
