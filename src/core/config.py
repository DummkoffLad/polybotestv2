"""Configuration loading - simplified."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv

from .types import ExecutionMode


@dataclass
class LeaderConfig:
    address: str
    poll_interval_sec: float = 1.0


@dataclass
class TraderConfig:
    address: str = ""
    private_key_env: str = "POLYMARKET_PRIVATE_KEY"
    signature_type: int = 2


@dataclass
class ScalingConfig:
    """Scaling configuration."""
    our_capital: Decimal = Decimal("50")
    k_factor: Decimal = Decimal("0.7")
    leader_capital: Decimal = Decimal("900")
    hourly_budget: Decimal = Decimal("50")


@dataclass
class MirrorStrategyConfig:
    """Mirror strategy configuration."""
    staleness_window_sec: float = 60.0
    cash_reserve_pct: Decimal = Decimal("10")
    per_market_cap_pct: Decimal = Decimal("30")
    per_side_pct: Decimal = Decimal("26")
    global_exposure_pct: Decimal = Decimal("100")
    spread_cost_pct: Decimal = Decimal("2")
    slippage_cost_pct: Decimal = Decimal("1")
    max_total_cost_pct: Decimal = Decimal("8")


@dataclass
class BotConfig:
    """Main bot configuration."""
    mode: ExecutionMode
    leader: LeaderConfig
    trader: TraderConfig
    scaling: ScalingConfig
    mirror_strategy: MirrorStrategyConfig
    data_dir: Path = field(default_factory=lambda: Path("data"))
    
    def initialize_capital(self, capital: Optional[Decimal] = None) -> None:
        """Initialize capital values."""
        if capital:
            self.scaling.our_capital = capital
            self.scaling.hourly_budget = capital * Decimal("0.9")


def load_config(config_path: Optional[Path] = None) -> BotConfig:
    """Load configuration from YAML file."""
    load_dotenv()
    
    if config_path is None:
        config_path = Path("config/config.yaml")
        if not config_path.exists():
            config_path = Path("config/config.example.yaml")
    
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    
    mode_str = raw.get("mode", "DRY_RUN").upper()
    mode = ExecutionMode(mode_str)
    
    leader_raw = raw.get("leader", {})
    leader = LeaderConfig(
        address=leader_raw.get("address", ""),
        poll_interval_sec=float(leader_raw.get("poll_interval_sec", 1.0)),
    )
    
    trader_raw = raw.get("trader", {})
    trader = TraderConfig(
        address=trader_raw.get("address", os.getenv("POLYMARKET_FUNDER_ADDRESS", "")),
    )
    
    scaling_raw = raw.get("scaling", {})
    scaling = ScalingConfig(
        our_capital=Decimal(str(scaling_raw.get("dry_run_capital", 50))),
        k_factor=Decimal(str(scaling_raw.get("k_factor", 0.7))),
        hourly_budget=Decimal(str(scaling_raw.get("hourly_budget", 45))),
        leader_capital=Decimal(str(scaling_raw.get("leader_estimated_capital", 900))),
    )
    
    mirror_raw = raw.get("mirror_strategy", {})
    mirror_strategy = MirrorStrategyConfig(
        staleness_window_sec=float(mirror_raw.get("staleness_window_sec", 60.0)),
        cash_reserve_pct=Decimal(str(mirror_raw.get("cash_reserve_pct", 10))),
        per_market_cap_pct=Decimal(str(mirror_raw.get("per_market_cap_pct", 30))),
        per_side_pct=Decimal(str(mirror_raw.get("per_side_pct", 26))),
        global_exposure_pct=Decimal(str(mirror_raw.get("global_exposure_pct", 100))),
        spread_cost_pct=Decimal(str(mirror_raw.get("spread_cost_pct", 2))),
        slippage_cost_pct=Decimal(str(mirror_raw.get("slippage_cost_pct", 1))),
        max_total_cost_pct=Decimal(str(mirror_raw.get("max_total_cost_pct", 8))),
    )
    
    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    
    return BotConfig(
        mode=mode,
        leader=leader,
        trader=trader,
        scaling=scaling,
        mirror_strategy=mirror_strategy,
        data_dir=data_dir,
    )