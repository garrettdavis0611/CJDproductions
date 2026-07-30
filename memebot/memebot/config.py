"""Typed configuration, loaded from YAML with environment-variable overrides.

Every number that can lose you money lives here rather than being buried in code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


@dataclass
class ScreeningConfig:
    # Liquidity / size floors. Thin pools cannot be exited without eating the spread.
    min_liquidity_usd: float = 25_000.0
    max_liquidity_usd: float = 5_000_000.0
    min_volume_h24_usd: float = 50_000.0
    min_fdv_usd: float = 100_000.0
    max_fdv_usd: float = 50_000_000.0

    # Age band. The first minutes belong to snipers and bundlers; you are not faster.
    min_pair_age_minutes: float = 30.0
    max_pair_age_minutes: float = 4_320.0  # 3 days

    # Turnover sanity. Very high hourly turnover on a small pool is usually wash trading.
    max_vol_liq_ratio_h1: float = 40.0
    min_vol_liq_ratio_h1: float = 0.15

    # Hard on-chain vetoes.
    require_mint_authority_revoked: bool = True
    require_freeze_authority_revoked: bool = True
    min_lp_locked_pct: float = 90.0
    max_top10_holder_pct: float = 35.0
    max_rugcheck_score: float = 40.0

    # Treat a check we could not complete as a failure. Keep this True: an unknown
    # is not a pass, and this is the difference between cautious and reckless.
    unknown_is_failure: bool = True

    # Honeypot detection: we must be able to quote a SELL back to SOL before we buy.
    require_sell_route: bool = True
    max_sell_price_impact_bps: float = 400.0
    sell_probe_usd: float = 25.0

    # Buy-side execution quality.
    max_buy_price_impact_bps: float = 300.0

    blocked_mints: list[str] = field(default_factory=list)
    blocked_symbol_substrings: list[str] = field(default_factory=lambda: ["test", "scam"])


@dataclass
class StrategyConfig:
    # Momentum entry gates.
    min_price_change_m5: float = 1.0
    max_price_change_m5: float = 40.0
    min_price_change_h1: float = 3.0
    max_price_change_h1: float = 300.0  # do not buy something already up 3x this hour
    min_buy_pressure_m5: float = 0.55
    min_trades_m5: int = 15
    min_liquidity_trend_pct: float = -5.0
    """Reject if liquidity fell more than this (%) across our observation window."""

    min_score: float = 0.60

    # Exits.
    stop_loss_pct: float = 0.18
    trailing_stop_pct: float = 0.22
    take_profit_pct: float = 0.45
    partial_take_profit_fraction: float = 0.5
    """Fraction of the position sold when take_profit_pct is first hit."""
    max_hold_minutes: float = 240.0
    liquidity_drain_exit_pct: float = 0.30
    """Emergency exit if pool liquidity drops this fraction below entry. This is the
    single most useful rug defence: liquidity leaves before price fully collapses."""


@dataclass
class RiskConfig:
    starting_equity_usd: float = 1_000.0
    risk_fraction_per_trade: float = 0.02
    """Fraction of equity risked per trade. With an 18% stop, 2% risk ~= 11% position."""
    max_position_usd: float = 100.0
    min_position_usd: float = 10.0
    max_concurrent_positions: int = 3
    max_total_exposure_fraction: float = 0.35
    max_daily_loss_fraction: float = 0.08
    """Trading halts for the rest of the UTC day once realised losses hit this."""
    max_consecutive_losses: int = 4
    cooldown_minutes_per_mint: float = 180.0
    min_seconds_between_entries: float = 60.0


@dataclass
class CostConfig:
    """Realistic round-trip cost model. Understating these is how paper results lie."""

    dex_fee_bps: float = 25.0
    jupiter_platform_fee_bps: float = 0.0
    extra_slippage_bps: float = 100.0
    """On top of the quoted price impact — accounts for latency and MEV/sandwiching."""
    priority_fee_lamports: int = 200_000
    base_tx_fee_lamports: int = 5_000
    sol_price_usd: float = 150.0
    """Used only to price the lamport fees in USD when no live SOL price is available."""
    failed_tx_probability: float = 0.05
    """Paper mode charges the fee and takes no fill, like a real failed swap."""


@dataclass
class ExecutionConfig:
    mode: str = "paper"  # paper | live
    jupiter_base_url: str = "https://lite-api.jup.ag"
    jupiter_api_key_env: str = "JUPITER_API_KEY"
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    keypair_env: str = "SOLANA_PRIVATE_KEY"
    slippage_bps: int = 300
    live_max_trade_usd: float = 50.0
    """Absolute clamp on any single live order, independent of risk sizing."""
    confirm_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ConfigError(f"execution.mode must be 'paper' or 'live', got {self.mode!r}")


@dataclass
class EngineConfig:
    poll_seconds: float = 30.0
    max_candidates_per_cycle: int = 25
    snapshot_history: int = 20
    data_dir: str = "data"
    log_level: str = "INFO"
    record_snapshots: bool = True
    dexscreener_base_url: str = "https://api.dexscreener.com"
    rugcheck_base_url: str = "https://api.rugcheck.xyz"
    http_timeout_seconds: float = 15.0
    chain: str = "solana"


@dataclass
class Config:
    screening: ScreeningConfig = field(default_factory=ScreeningConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)

    def validate(self) -> None:
        r, s, c = self.risk, self.strategy, self.costs
        if not 0 < r.risk_fraction_per_trade <= 0.25:
            raise ConfigError("risk.risk_fraction_per_trade must be in (0, 0.25]")
        if r.min_position_usd > r.max_position_usd:
            raise ConfigError("risk.min_position_usd exceeds risk.max_position_usd")
        if not 0 < r.max_total_exposure_fraction <= 1.0:
            raise ConfigError("risk.max_total_exposure_fraction must be in (0, 1]")
        if r.max_concurrent_positions < 1:
            raise ConfigError("risk.max_concurrent_positions must be >= 1")
        if not 0 < s.stop_loss_pct < 1:
            raise ConfigError("strategy.stop_loss_pct must be in (0, 1)")
        if not 0 < s.trailing_stop_pct < 1:
            raise ConfigError("strategy.trailing_stop_pct must be in (0, 1)")
        if not 0 <= s.partial_take_profit_fraction <= 1:
            raise ConfigError("strategy.partial_take_profit_fraction must be in [0, 1]")
        if self.screening.min_liquidity_usd <= 0:
            raise ConfigError("screening.min_liquidity_usd must be positive")
        if self.screening.min_liquidity_usd >= self.screening.max_liquidity_usd:
            raise ConfigError("screening.min_liquidity_usd must be below max_liquidity_usd")
        if c.sol_price_usd <= 0:
            raise ConfigError("costs.sol_price_usd must be positive")
        if not 0 <= c.failed_tx_probability < 1:
            raise ConfigError("costs.failed_tx_probability must be in [0, 1)")

        # A round trip that costs more than the take-profit target can never win.
        round_trip_bps = 2 * (c.dex_fee_bps + c.jupiter_platform_fee_bps + c.extra_slippage_bps)
        if round_trip_bps >= s.take_profit_pct * 10_000:
            raise ConfigError(
                f"round-trip cost ({round_trip_bps:.0f} bps) swallows the take-profit target "
                f"({s.take_profit_pct * 10_000:.0f} bps) — this strategy cannot be profitable"
            )

    @property
    def round_trip_cost_bps(self) -> float:
        c = self.costs
        return 2 * (c.dex_fee_bps + c.jupiter_platform_fee_bps + c.extra_slippage_bps)


def _merge(instance: Any, data: dict[str, Any], path: str = "") -> Any:
    """Recursively apply a plain dict onto a nested dataclass, rejecting unknown keys."""
    known = {f.name: f for f in fields(instance)}
    for key, value in data.items():
        where = f"{path}{key}"
        if key not in known:
            raise ConfigError(f"unknown config key: {where}")
        current = getattr(instance, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value, path=f"{where}.")
            continue
        target_type = known[key].type
        if value is not None and target_type in ("float", float) and isinstance(value, int):
            value = float(value)
        setattr(instance, key, value)
    return instance


_ENV_OVERRIDES: dict[str, tuple[str, str, type]] = {
    "MEMEBOT_MODE": ("execution", "mode", str),
    "MEMEBOT_POLL_SECONDS": ("engine", "poll_seconds", float),
    "MEMEBOT_LOG_LEVEL": ("engine", "log_level", str),
    "MEMEBOT_DATA_DIR": ("engine", "data_dir", str),
    "MEMEBOT_STARTING_EQUITY_USD": ("risk", "starting_equity_usd", float),
    "MEMEBOT_MAX_POSITION_USD": ("risk", "max_position_usd", float),
    "MEMEBOT_LIVE_MAX_TRADE_USD": ("execution", "live_max_trade_usd", float),
    "MEMEBOT_SOLANA_RPC_URL": ("execution", "solana_rpc_url", str),
}


def load_config(path: str | Path | None = None, env: dict[str, str] | None = None) -> Config:
    cfg = Config()
    if path is not None:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a YAML mapping")
        _merge(cfg, raw)

    environ = os.environ if env is None else env
    for var, (section, attr, caster) in _ENV_OVERRIDES.items():
        if var in environ and environ[var] != "":
            setattr(getattr(cfg, section), attr, caster(environ[var]))

    # Re-run per-section validation that __post_init__ would have done.
    ExecutionConfig.__post_init__(cfg.execution)
    cfg.validate()
    return cfg
