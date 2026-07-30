# memebot

A screened, risk-managed Solana meme-coin trading bot.

---

## Read this part first

**This bot cannot guarantee profit, and it is not "profitable from day 1."** No
software can promise that, and anything that does is lying to you. Here is the
arithmetic, which you can reproduce with `python -m memebot costs`:

- A round trip costs about **2.5%** at the default settings — 25 bps DEX fee plus a
  100 bps slippage allowance, on each leg, plus priority fees.
- So a trade that ends where it started **loses money**. You need +2.5% just to
  break even.
- Most new meme coins go to zero. The large majority of wallets trading them lose
  money. That is not pessimism; it is the base rate.

What this bot actually does is give you a fighting chance:

1. **Refuses to buy rug pulls** — a hard screening gauntlet, where any single
   failure is an absolute veto (details below).
2. **Prices the costs honestly** — the paper broker charges the same fees,
   slippage, and failed-transaction rate you will really pay, so a paper run that
   loses money tells you the truth instead of flattering you.
3. **Limits the damage** — position caps, exposure caps, a daily loss halt, and a
   consecutive-loss circuit breaker.
4. **Defaults to paper trading** — live mode requires several deliberate steps.

The honest path to using this: run `paper` for a few weeks, then read
`data/trades.jsonl`. If it did not make money on paper, it will not make money
live — live is strictly worse, because real fills are worse than modelled ones.

**Which strategy has the better chance?** On the evidence in this repo, copy trading
(`memebot copy`) rather than price momentum (`memebot paper`). The momentum strategy
lost a median 4% in a market constructed to have no edge at all, and price-derived
signals are the most crowded input in this space. Following wallets with a
luck-filtered record is a different class of bet, and it held up under every
simulated test — see [Copy trading](#copy-trading-following-wallets-that-make-solid-trades).
Neither is a guarantee.

---

## Install

```bash
cd memebot
pip install -r requirements.txt          # scan / paper / backtest
pip install -r requirements-live.txt     # additionally, only if going live
pip install -r requirements-dev.txt      # to run the tests
```

No API keys are required. Optional: `JUPITER_API_KEY` (higher rate limits),
`RUGCHECK_API_KEY`.

## Use

```bash
python -m memebot costs                   # what a round trip costs you
python -m memebot check <mint>            # run the safety gauntlet on one token
python -m memebot scan                    # screen live candidates, place no orders
python -m memebot paper                   # simulated fills, real costs  (start here)
python -m memebot backtest                # replay what `scan`/`paper` recorded
python -m memebot simulate                # drive the engine against synthetic markets
python -m memebot wallets <address>...    # does this wallet survive the luck filters?
python -m memebot copy --wallet <addr>    # trade on tracked-wallet consensus (paper)
python -m memebot live                    # real money (heavily gated)
```

Every run appends observations to `data/snapshots.jsonl` and fills to
`data/trades.jsonl`. Those two files are your evidence.

---

## The screening gauntlet

Each check below maps to a specific way people lose their money. A **hard fail** is
an absolute veto — one is enough to reject the token, and the bot reports all of
them rather than stopping at the first.

| Check | Why it exists |
|---|---|
| Mint authority revoked | Otherwise the deployer can print unlimited new supply and dump it on you. Read from chain, not from a third party. |
| Freeze authority revoked | Otherwise the deployer can freeze your token account so you cannot sell. |
| LP locked/burned ≥ 90% | Otherwise the deployer can withdraw the liquidity — the classic rug. |
| Top-10 holders ≤ 35% | Otherwise one wallet's exit is the entire pool. |
| **Sell route exists** | The honeypot test. Before buying, we quote a $25 **sell** back to SOL through Jupiter. A token you can buy but not sell is a trap, and this is the cheapest way to detect one. |
| Exit price impact ≤ 400 bps | Being technically able to sell is not the same as being able to sell without giving the position away. |
| Liquidity ≥ $25k | Below this you cannot exit a $100 position without paying for the privilege. |
| 1h volume/liquidity ≤ 40× | Absurd turnover on a small pool is the signature of wash trading, not demand. |
| Pair age ≥ 30 min | The first minutes belong to snipers, bundlers, and the deployer. You are polling a public REST API — you are not faster than them, and pretending otherwise is how you become their exit liquidity. |
| Liquidity not draining | Liquidity leaves before price fully collapses. A pool down >20% while we watched is a rug in progress. |
| Third-party risk score ≤ 40 | A second opinion from RugCheck, plus its danger-level flags. |

### Unknown is treated as failure

`screening.unknown_is_failure: true` is the default and you should leave it on. If
an API call fails and we cannot confirm the mint authority is revoked, the token is
**rejected**, not accepted. "We don't know whether the developer can print
unlimited supply" is not a reason to buy.

This means the bot trades less when data sources are flaky. That is the correct
behaviour.

---

## Risk controls

| Control | Default | Effect |
|---|---|---|
| `risk_fraction_per_trade` | 2% | Dollars of equity risked per trade, sized against the stop. |
| `max_position_usd` | $100 | Hard cap per position. The cap, not the formula, is what protects you. |
| `max_concurrent_positions` | 3 | |
| `max_total_exposure_fraction` | 35% | Ceiling on total deployed capital. |
| `max_daily_loss_fraction` | 8% | Halts entries for the rest of the UTC day. Clears at midnight. |
| `max_consecutive_losses` | 5 | Pauses entries for `consecutive_loss_pause_minutes` (default 6h). Runs on its own clock, so a new calendar day does not clear it — but it does not need a human either. Set the pause to 0 to require a manual resume. |
| `cooldown_minutes_per_mint` | 180 | No revenge-trading the token that just stopped you out. |
| `live_max_trade_usd` | $50 | Absolute clamp on any live order, independent of sizing. |

Exits, in priority order — the first one to fire wins:

1. **Liquidity drain** (pool down 30% from entry) — emergency exit, ahead of
   everything else. This is the most useful rug defence available to us.
2. **Hard stop** at −18%.
3. **Trailing stop** 22% off the peak, armed only once the position is above entry
   so it cannot pre-empt the hard stop on a trade that never worked.
4. **Partial take-profit** — sells half at +45%, once. Removes the specific misery
   of round-tripping a 2x back to break-even.
5. **Time stop** at 4 hours. Meme momentum decays; dead money is still risk.

---

## The strategy, stated so it can be falsified

Among tokens that have already passed the rug screen and are 30 minutes to 3 days
old, those showing accelerating buy-side flow on stable-or-rising liquidity
continue long enough to clear round-trip costs more often than chance.

**That is a hypothesis, not a fact.** It is the part of this system I have the least
confidence in, and the part you should test hardest. The screening and the risk
controls are defensible on first principles; a momentum edge is an empirical claim
about a market that adapts.

With a 2.5:1 reward:risk ratio you need better than a **29% win rate before costs**
to break even. Whether this strategy clears that is exactly what `backtest` is for.

### What the backtest can and cannot tell you

`memebot backtest` replays recorded snapshots through the real strategy, risk, and
cost model. Two limits bound how much the number is worth:

- **Survivorship** — it replays only tokens your recording captured. It says
  nothing about what you never saw.
- **Fill realism** — it replays observed prices, not the order book. Real slippage
  when a thin pool is dumping is worse than the model, sometimes much worse.

A backtest showing a small edge is noise. Treat only a large, stable edge that
survives the cost model as worth funding — and even then it is a hypothesis, not a
forecast. Five trades is not a sample.

---

## Simulated results (`memebot simulate`)

When no live feed is available, `simulate` replaces **only** the market data source
and runs the genuine engine, strategy, risk manager, cost model and accounting
against a price process we control. It is a controlled experiment, not a forecast.

**Read this before reading the numbers:** the price process is written in
`simulator.py`, so any edge the strategy shows against it is an edge that was put
there. These runs test *the machinery* — do costs get charged, do the caps bind, does
the exit ladder fire in the right order, do the rug defences work. They say **nothing
about whether real meme coins trend.** Only recorded live data can answer that.

20 Monte Carlo runs per regime, 3 simulated days each, $1,000 starting equity,
60-token universe, default config:

| Regime | Median | Mean | p10 | p90 | Worst | Profitable | Median trades | Win rate |
|---|---|---|---|---|---|---|---|---|
| `random_walk` (null) | −4.05% | −2.78% | −7.91% | +3.32% | −14.10% | 6/20 | 84 | 40% |
| `momentum` (positive control) | +16.76% | +16.05% | +9.80% | +22.69% | +9.22% | 20/20 | 120 | 53% |
| `mean_reverting` | −8.67% | −8.52% | −11.08% | −6.30% | −12.32% | 0/20 | 66 | 29% |
| `rug_infested` | +0.04% | +1.68% | −4.82% | +11.47% | −8.30% | 10/20 | 67 | 43% |
| `mixed` | +0.56% | +1.70% | −5.76% | +12.81% | −7.61% | 11/20 | 98 | 43% |

What each row is actually telling you:

- **`random_walk` is the important one.** Prices are a martingale — zero expected
  return, no edge to find. The bot loses a median 4%. Roughly 1.2 points of that is
  fees; the rest is the exit ladder itself, because an 18% stop gets hit often at
  this volatility while the trailing stop caps the upside. **In a market with no
  edge, this bot bleeds.** That is the correct and expected result, and it is the
  single most useful number here.
- **`momentum` shows the strategy works when the edge exists** — 20/20 profitable.
  That validates the machinery end to end. It is not evidence the edge is real.
- **`mean_reverting` loses in 20/20 runs**, which confirms the strategy is genuinely
  directional rather than accidentally regime-neutral. A strategy that made money in
  both `momentum` and `mean_reverting` would be measuring nothing.
- **`rug_infested` and `mixed` land near zero** — the trend edge roughly cancels
  against costs, rugs and volatility.

### Do the rug defences actually work?

The A/B that matters. Same markets, same seeds, `liquidity_drain_exit_pct` enabled
vs disabled, measuring only positions **held into a rug** (entered before the rug
started, exited after):

| | Positions caught | Mean return | Worst | Avg $/position |
|---|---|---|---|---|
| Drain exit **on** | 50 | **−12.08%** | −72.89% | −$2.04 |
| Drain exit **off** | 48 | **−34.23%** | −72.89% | −$6.11 |

**A position caught in a rug loses 12% instead of 34%** — the drain exit fired 50
times and cut about two-thirds of the loss, because liquidity leaves before the price
fully collapses, so it triggers ahead of the price stop.

At the portfolio level the effect is smaller and mostly in the tail: mean return
improved +1.76pp in `rug_infested`, and in `mixed` the mean barely moved (+0.30pp)
while the **worst case improved by 4 percentage points**. That is what insurance
looks like — it does not raise your average, it truncates your left tail. Do not
expect it to make a losing strategy profitable.

---

## Copy trading: following wallets that make solid trades

A different bet from momentum, and on the evidence here a better one. It does not try
to predict price from price — the most crowded, most-arbitraged input there is. The
signal is that several people with a **luck-filtered** record just bought the same
thing, recently, at a price you can still get.

```bash
python -m memebot wallets <address>          # audit a wallet before following it
python -m memebot copy --wallet <address>    # paper-trade their consensus
python -m memebot simulate --selection       # do the luck filters actually work?
python -m memebot simulate --copy            # what is each defence worth?
```

### The problem with "follow profitable wallets"

Sort wallets by PnL and follow the top ones, and you have built a survivorship-bias
machine. In any large population of gamblers, some have spectacular records by
chance — and a leaderboard surfaces exactly those. So qualification is a set of
**hard gates**, deliberately not a weighted score that one huge number could carry:

| Gate | Default | What it rejects |
|---|---|---|
| `min_closed_trades` | 20 round trips | A handful of wins is a coin that came up heads. |
| `min_distinct_tokens` | 10 | One token traded twenty times is one opinion. |
| `max_single_token_profit_share` | 50% | **One 100x is luck, not a process.** |
| `min_active_days` | 5 | A record built in one session is one session's luck. |
| `min_win_rate` | 45% | |
| `min_realized_pnl_sol` | 5 SOL | PnL measured in SOL, so no price oracle can skew it. |
| `min_median_hold_minutes` | 10 min | **Snipers.** Their edge is being 400ms faster than you. You cannot copy latency. |
| `max_median_hold_minutes` | 48 h | Signal too slow to act on. |

Round trips are counted as *episodes* — position goes from zero, up, and back to zero
— so a wallet that averaged into one winner does not register as twenty separate
wins. Sells of tokens never seen bought are ignored, or airdrops would read as free
profit.

### Do the filters work? Measured, not asserted

`simulate --selection` builds 150 wallets each of four known types and reports what
qualification does with them:

| Archetype | Accepted | Rejected | Verdict |
|---|---|---|---|
| **skilled** (real edge) | 145 | 5 | 96.7% recall — we keep the ones we want |
| **lucky** (no edge, one moonshot) | 0 | 150 | **0% false accepts** |
| **sniper** (edge is latency) | 0 | 150 | **0% false accepts** |
| **farmer** (dumps on followers) | 147 | 3 | **98% pass — by design** |

The concentration gate is load-bearing: relax `max_single_token_profit_share` to 1.0
and lucky wallets start getting through (there is a test asserting exactly that).

**Farmers passing is not a bug.** Nothing in a trade history reveals that a wallet
intends to use your buys as its exit liquidity — it looks like a good trader, because
it is one, right up until you are the counterparty. About half of everything that
qualifies is a farmer. That is what the runtime defences are for.

### The three runtime defences

1. **Freshness** (`max_signal_age_seconds`, 180s) — you are *always* later than the
   wallet you copy. Past this the move you would be copying has already happened.
2. **Drift gate** (`max_price_drift_pct`, 12%) — refuses to buy once price has run
   past their entry. This is the gate that stops you buying someone's top.
3. **Attribution and demotion** — every trade is credited to the wallets that
   triggered it, and a wallet whose copied trades lose money gets dropped. Farming is
   invisible in the signal but obvious in the results. Demotions persist across
   restarts, or the defence would reset every time the process bounced.

Plus the strongest exit available in this design: **the wallets we followed are
selling.** They have our information plus whatever qualified them.

### What each defence is worth

`simulate --copy`, 10 runs × 3 simulated days, market containing skilled wallets,
lucky wallets and farmers:

| Configuration | Median | Mean | Worst | Profitable | Farmer-attributed PnL |
|---|---|---|---|---|---|
| **All defences on** | +3.49% | +3.93% | +0.90% | **10/10** | **+$23** |
| Drift gate off | +4.96% | +6.48% | +3.16% | 10/10 | +$31 |
| Demotion off | +3.42% | +3.91% | +0.90% | 10/10 | +$20 |
| **Wallet-exit signal off** | −0.97% | −0.24% | −5.89% | 4/10 | **−$259** |
| All defences off | −2.44% | −1.60% | −7.65% | 2/10 | **−$375** |

Reading this honestly:

- **The wallet-exit signal is the one that matters.** Turn it off and farmer PnL goes
  from +$23 to −$259, and the whole strategy flips negative. A farmer's dump is a
  price collapse *with the pool intact*, so the liquidity-drain exit cannot see it —
  only noticing that they sold can.
- **Demotion is a backstop, not a primary.** With the exit signal working it changes
  almost nothing, because there is nothing left to catch. It earns its keep when the
  exit signal misses (note the 4 farmer wallets demoted in the row where it is the
  only defence left).
- **The drift gate did not pay for itself in this simulation, and I am keeping it
  anyway.** The harness advances in 5-minute cycles, so it cannot represent the real
  failure it exists to prevent — being two seconds behind a sniper-style entry, which
  is documented as the dominant way copy traders get hurt. Here it just filters out
  profitable trades (18 vs 26). Treat the +5% row as a simulation artifact rather than
  a recommendation, and if you disagree, `max_price_drift_pct` is one line of config.
- Skilled wallets contributed the most attributed profit (+$182) in every
  configuration, which is the whole thesis: the money comes from following genuine
  skill.

One caveat on the lucky wallets showing positive PnL: in this harness they buy from
the same clustered focus list as skilled wallets, so they sometimes ride a skilled
wallet's move. That is a simulation artifact, not evidence that copying random
wallets works.

### Trade frequency is the real cost of safety

Median 18 trades per 3 days. With `min_wallets_consensus: 2`, two tracked wallets
buying the same token within 15 minutes is genuinely uncommon — the first version of
this experiment produced **2 trades per run** until the harness modelled wallets
clustering on the same tokens. Dropping to 1-wallet consensus will fire far more
often and be far noisier. That trade-off is yours to make, and it is the main dial.

### Getting the data

Default feed is plain Solana JSON-RPC — no API key, and every number verifiable
against the chain. It costs one `getTransaction` per signature, so **use a paid RPC
endpoint** or it will be painfully slow. Set `smart_money.feed: birdeye` with
`BIRDEYE_API_KEY` for a faster path.

Ambiguity is dropped rather than guessed: a transaction moving more than one non-SOL
mint is skipped, because multi-hop routes cannot be split into per-token trades
without assuming things, and a wallet analyser that invents trades produces confident
nonsense.

This polls, which means tens of seconds of latency on a public RPC. The upgrade path
is a websocket subscription (Helius, or Birdeye's `SUBSCRIBE_WALLET_TXS`) feeding
`tracker.observe_trade` directly. The gates do not change; they just reject less
often.

### What copy trading still cannot fix

- **You are always behind.** Every copied entry is worse than theirs. The gates limit
  how much worse; they cannot make you first.
- **Past performance decays.** Wallets are re-analysed every `refresh_minutes` and
  unfollowed when they stop qualifying, but that is reactive by definition.
- **A wallet can be skilled at something you cannot replicate** — private deal flow,
  insider information, or size that moves the market it is trading.
- **Selection still happens on data you chose.** If you feed it a watchlist scraped
  from Telegram callers, the filters will reject most of them, but a watchlist is
  still an input you picked.

### A bug this study found

The first run of the study halted **every single simulation** permanently. The
consecutive-loss breaker was set to halt after 4 losses and required a manual resume
— and at a ~43% win rate, four consecutive losses is close to certain within the
first day. The default config would have bricked the bot on day one, and the
predictable human response is to switch the breaker off entirely, which is worse than
having a weaker one. It is now a timed 6-hour pause (`consecutive_loss_pause_minutes`),
still settable to 0 if you genuinely want manual-only.

Running the thing is how you find that. Reading the code is not.

---

## Before you go live

Live mode is gated on purpose. All of these must be true:

1. `execution.mode: live` in your config.
2. `MEMEBOT_I_UNDERSTAND_THE_RISK=1` in your environment.
3. A keypair in `SOLANA_PRIVATE_KEY` (base58 or a JSON byte array).
4. Typing `LIVE` at the interactive prompt.

And these are on you:

- **Use a burner wallet.** Fund it with only what you are fully prepared to lose.
  Never put a seed phrase or a main-wallet key in an environment variable.
- **Use a paid RPC endpoint.** The public `api.mainnet-beta.solana.com` is rate
  limited and will fail you at the worst possible moment.
- **Start at `live_max_trade_usd: 10`** and leave it there until you have live
  fills to compare against your paper fills.
- **Expect live results to be worse than paper.** Latency, MEV/sandwich bots, and
  failed transactions are all real and all against you.

### What this bot still cannot protect you against

Being honest about the gaps, because the screening table above might otherwise read
as a guarantee:

- **Slow rugs.** Every on-chain check can pass and the team can simply stop working
  while insiders distribute over days.
- **Sniped exits.** Nothing prevents another bot from front-running your sell.
- **Sudden social collapse.** Price can gap through your stop; you exit lower.
- **Bad or stale API data.** DexScreener liquidity figures can lag reality. The
  fail-closed default helps, but it is not a proof of correctness.
- **Honeypots that arm later.** Our sell probe tests the token *now*. A malicious
  upgrade path or transfer hook can change that after you buy.

---

## Layout

```
memebot/
  config.py            every number that can lose you money
  models.py            snapshots, positions, fills, safety reports
  datasources/
    http.py            token-bucket rate limiting, bounded retries
    dexscreener.py     discovery + price/liquidity snapshots
    rugcheck.py        third-party risk score, LP lock, holder concentration
    solana_rpc.py      mint/freeze authority read straight from chain
    jupiter.py         quotes, sell-route probe, swap transaction build
    wallet_feed.py     reconstruct wallet swaps (RPC, or Birdeye)
  screening/
    safety.py          gathers the facts (failure-tolerant)
    filters.py         interprets them (hard fails vs soft flags)
  smartmoney/
    analysis.py        reconstruct round trips; separate skill from luck
    tracker.py         consensus, freshness/drift gates, attribution, demotion
    watcher.py         polls followed wallets for new trades
    simulate.py        selection confusion matrix + defence A/B
  strategy/
    momentum.py        entries, and the exit ladder that does the real work
    copytrade.py       entries from wallet consensus; exit when they sell
  risk.py              sizing, caps, daily halt, circuit breaker
  execution/
    base.py            the shared cost model — both brokers use the same math
    paper.py           simulated fills, real costs
    jupiter_broker.py  live swaps, heavily gated
  portfolio.py         cash, positions, realised PnL, JSONL audit trail
  engine.py            the loop: exits before entries, always
  backtest.py          replay recorded snapshots
  simulator.py         synthetic markets for controlled experiments
  cli.py
```

## Tests

```bash
python -m pytest -q      # 235 tests
```

The screening and cost-model tests are the important ones: each screening test
corresponds to a specific way people lose their money, and if the cost-model tests
are wrong then every profit number the bot reports is a lie.

Note that no test hits the network — HTTP clients are stubbed with recorded
response shapes. That means parsing is covered, but **the live API integrations have
not been verified against the live services from this repository's CI**. Run
`python -m memebot check <mint>` against a known-good token as your first smoke
test, and confirm the output looks sane before trusting `scan`.

---

## License / disclaimer

Provided as-is, for educational purposes. Trading cryptocurrencies risks total loss
of capital. You are solely responsible for your own trades and for compliance with
the law where you live. Nothing here is financial advice.
