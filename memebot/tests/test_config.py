import pytest
import yaml

from memebot.config import Config, ConfigError, load_config


def write(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_defaults_are_valid():
    Config().validate()


def test_yaml_overrides_nested_values(tmp_path):
    path = write(tmp_path, {"risk": {"max_position_usd": 42}, "engine": {"poll_seconds": 5}})
    cfg = load_config(path, env={})
    assert cfg.risk.max_position_usd == 42
    assert cfg.engine.poll_seconds == 5
    assert cfg.risk.max_concurrent_positions == 3  # untouched default


def test_unknown_config_keys_are_rejected(tmp_path):
    path = write(tmp_path, {"risk": {"yolo_mode": True}})
    with pytest.raises(ConfigError, match="unknown config key: risk.yolo_mode"):
        load_config(path, env={})


def test_env_overrides_yaml(tmp_path):
    path = write(tmp_path, {"risk": {"max_position_usd": 42}})
    cfg = load_config(path, env={"MEMEBOT_MAX_POSITION_USD": "77.5"})
    assert cfg.risk.max_position_usd == pytest.approx(77.5)


def test_invalid_mode_is_rejected(tmp_path):
    path = write(tmp_path, {"execution": {"mode": "yolo"}})
    with pytest.raises(ConfigError, match="execution.mode"):
        load_config(path, env={})


def test_excessive_per_trade_risk_is_rejected(tmp_path):
    path = write(tmp_path, {"risk": {"risk_fraction_per_trade": 0.9}})
    with pytest.raises(ConfigError, match="risk_fraction_per_trade"):
        load_config(path, env={})


def test_costs_that_swallow_the_target_are_rejected(tmp_path):
    """Guards against the most seductive configuration mistake: a take-profit target
    smaller than the round trip needed to reach it."""
    path = write(
        tmp_path,
        {"costs": {"extra_slippage_bps": 400}, "strategy": {"take_profit_pct": 0.05}},
    )
    with pytest.raises(ConfigError, match="swallows the take-profit target"):
        load_config(path, env={})


def test_inverted_liquidity_band_is_rejected(tmp_path):
    path = write(tmp_path, {"screening": {"min_liquidity_usd": 10_000, "max_liquidity_usd": 5_000}})
    with pytest.raises(ConfigError, match="min_liquidity_usd"):
        load_config(path, env={})


def test_round_trip_cost_bps_is_reported():
    cfg = Config()
    # 2 legs x (25 dex + 0 platform + 100 slippage) = 250 bps
    assert cfg.round_trip_cost_bps == pytest.approx(250.0)


def test_shipped_config_file_loads_and_validates():
    from pathlib import Path

    shipped = Path(__file__).resolve().parents[1] / "config.yaml"
    assert shipped.exists(), "config.yaml should ship with the project"
    load_config(shipped, env={})
