"""Configuration loading and validation.

Loads config from YAML file and environment variables.
Provides typed access to all configuration values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv

from .core import (
    ExecutionMode,
    ScalingConfig,
    CapsConfig,
    StateMachineConfig,
    DecisionConfig,
)


@dataclass
class LeaderConfig:
    address: str
    poll_interval_sec: float = 1.0
    use_websocket: bool = False


@dataclass
class TraderConfig:
    """Your trading wallet configuration for LIVE mode."""
    address: str = ""  # Your Polymarket wallet address (proxy/funder)
    private_key_env: str = "POLYMARKET_PRIVATE_KEY"  # Env var name for private key
    signature_type: int = 2  # 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE


@dataclass
class TimingConfig:
    burst_window_sec: float = 60
    burst_poll_interval_sec: float = 1.5
    burst_stabilization_threshold: Decimal = Decimal("0.50")
    burst_stabilization_count: int = 3
    resync_interval_sec: float = 60
    resync_error_threshold: Decimal = Decimal("0.50")
    follow_batch_window_sec: float = 2.0


@dataclass
class CircuitBreakerConfig:
    max_consecutive_api_errors: int = 5
    max_pending_orders: int = 3
    max_unknown_fill_duration_sec: float = 30
    max_orders_per_minute: int = 10
    max_new_exposure_per_minute: Decimal = Decimal("10")
    breaker_cooldown_sec: float = 60


@dataclass
class LoggingConfig:
    level: str = "INFO"
    output: str = "both"
    file_path: str = "logs/bot.jsonl"
    trace_enabled: bool = True
    trace_path: str = "traces/decisions.jsonl"
    trace_include_snapshots: bool = True
    log_max_mb: int = 50
    log_backup_count: int = 5
    trace_max_mb: int = 100
    trace_backup_count: int = 5


@dataclass
class CollectorConfig:
    enabled: bool = True
    storage_format: str = "jsonl"
    storage_path: str = "collected/"
    position_snapshot_interval_sec: float = 5
    record_prices: bool = True
    price_snapshot_interval_sec: float = 1
    max_file_size_mb: int = 50
    min_exposure_dollars: float = 1.0
    trade_lookback_limit: int = 50


@dataclass
class SimulationConfig:
    fill_model: str = "immediate"
    simulated_spread_pct: float = 0.5
    simulated_slippage_pct: float = 0.1
    fees_pct: float = 0.0
    random_seed: int = 42


@dataclass
class PersistenceConfig:
    state_file: str = "state/bot_state.json"
    auto_save_interval_sec: float = 10
    backup_count: int = 5


@dataclass
class ApiConfig:
    base_url: str = "https://clob.polymarket.com"
    timeout_sec: float = 10
    retry_count: int = 3
    retry_delay_sec: float = 1.0
    rate_limit_rps: float = 5.0
    endpoints: Dict[str, str] = field(default_factory=dict)


@dataclass
class MarketsConfig:
    type_filter: str = "hourly_btc"
    allowlist: List[str] = field(default_factory=list)
    denylist: List[str] = field(default_factory=list)


@dataclass
class PreflightConfig:
    smoke_test_duration_sec: float = 30
    min_collected_data_sec: float = 60


@dataclass
class MirrorStrategyConfig:
    """Configuration for the live trade mirroring strategy."""
    staleness_window_sec: float = 10.0
    burst_window_sec: float = 2.0
    snapshot_interval_sec: float = 20.0
    cash_reserve_pct: Decimal = Decimal("20.0")
    per_market_cap_pct: Decimal = Decimal("15.0")
    ratio_margin_pct: Decimal = Decimal("10.0")
    expiry_close_sec: float = 120.0
    retry_attempts: int = 2
    retry_delay_sec: float = 0.5


@dataclass
class SafetyConfig:
    """Safety features to prevent bad trades.
    
    These are HARD LIMITS that override all other logic.
    All orders use MARKET execution (buy at ask, sell at bid).
    """
    # === PRICE DRIFT PROTECTION (BUY only) ===
    # Don't buy if current price has drifted too far from leader's entry
    # SELLs are NOT blocked - leader may be cutting risk and sells are important
    price_drift_enabled: bool = True
    max_buy_price_drift_pct: Decimal = Decimal("6.0")   # 6% max drift for buys
    
    # === SPREAD PROTECTION ===
    # Skip trade if bid-ask spread is too wide (losing too much on execution)
    max_spread_pct: Decimal = Decimal("6.0")  # 6% max spread
    
    # === STALE DATA PROTECTION ===
    max_price_age_sec: float = 10.0  # Skip if price older than 10s
    
    # === POSITION LIMITS ===
    max_concurrent_positions: int = 10
    min_position_value: Decimal = Decimal("1.0")  # $1 minimum
    
    # === LOSS PROTECTION ===
    max_session_loss_pct: Decimal = Decimal("20.0")  # Stop if down 20%
    per_position_stop_loss_pct: Decimal = Decimal("0")  # 0 = disabled
    
    # === SELL LOSS PROTECTION ===
    # Block sells where WE would lose money, UNLESS leader also lost
    # If leader took profit but we'd take loss -> skip (our entry was worse)
    # If leader also took loss -> follow (they're cutting risk)
    block_loss_sells_if_leader_profit: bool = True
    
    # === EXECUTION TIMING ===
    min_order_interval_sec: float = 2.0  # Min time between orders to same market
    market_open_wait_sec: float = 5.0  # Wait after market open


@dataclass
class CopyTradingConfig:
    """Delta-driven copy trading configuration."""
    # Delta detection (percentages of position)
    epsilon_pct: Decimal = Decimal("1.0")  # 1% change to trigger
    epsilon_min_dollars: Decimal = Decimal("0.50")  # Minimum $0.50 absolute
    
    # Resync drift thresholds (percentage of target)
    resync_drift_start_pct: Decimal = Decimal("5.0")  # 5% drift triggers resync
    resync_drift_stop_pct: Decimal = Decimal("2.0")   # Stop when <2%
    resync_recent_activity_sec: float = 300.0
    
    # Execution controls
    max_spread: Decimal = Decimal("0.05")  # Skip if ask-bid > max_spread
    max_order_pct: Decimal = Decimal("10.0")  # Max 10% of position per order
    max_order_chunks: int = 3
    
    # Accumulator aging
    buy_accumulator_max_age_sec: float = 120.0
    sell_accumulator_max_age_sec: float = 60.0
    
    # Expiry safety (requires market close time)
    expiry_close_minutes: float = 10.0
    expiry_force_seconds: float = 30.0
    
    # Live reconcile
    reconcile_interval_sec: float = 30.0
    smoke_test_duration_sec: float = 30
    min_collected_data_sec: float = 60


@dataclass
class ScalingConfigPct:
    """Percentage-based scaling configuration.
    
    All values are percentages of our_capital.
    In LIVE mode, our_capital is fetched from API.
    In DRY_RUN mode, our_capital is from config (dry_run_capital).
    """
    # K factor (fraction of leader's moves to copy)
    k_factor: Decimal = Decimal("0.7")  # Copy 70% of leader's moves
    
    # Hourly budget as % of capital
    hourly_budget_pct: Decimal = Decimal("90.0")  # 90% of capital per hour
    
    # Caps as % of capital
    per_market_gross_pct: Decimal = Decimal("30.0")  # Max 30% in one market
    per_side_pct: Decimal = Decimal("26.0")  # Max 26% on one side
    global_exposure_pct: Decimal = Decimal("100.0")  # Max 100% total exposure
    
    # DRY_RUN only: simulated starting capital
    dry_run_capital: Decimal = Decimal("50.0")  # $50 simulated capital
    
    # Computed (filled at runtime)
    our_capital: Decimal = Decimal("0")  # Actual capital (fetched or simulated)
    hourly_budget: Decimal = Decimal("0")  # Computed from percentage
    per_market_gross: Decimal = Decimal("0")  # Computed
    per_side: Decimal = Decimal("0")  # Computed
    global_capital: Decimal = Decimal("0")  # Computed
    
    def compute_from_capital(self, capital: Decimal) -> None:
        """Compute dollar values from percentages based on actual capital."""
        self.our_capital = capital
        self.hourly_budget = (capital * self.hourly_budget_pct / Decimal("100")).quantize(Decimal("0.01"))
        self.per_market_gross = (capital * self.per_market_gross_pct / Decimal("100")).quantize(Decimal("0.01"))
        self.per_side = (capital * self.per_side_pct / Decimal("100")).quantize(Decimal("0.01"))
        self.global_capital = (capital * self.global_exposure_pct / Decimal("100")).quantize(Decimal("0.01"))


@dataclass
class BotConfig:
    """Main bot configuration.
    
    IMPORTANT: Config uses PERCENTAGES for scaling/caps.
    In LIVE mode, actual capital is fetched from API.
    In DRY_RUN mode, dry_run_capital from config is used.
    
    Call initialize_capital() before using to compute dollar values.
    """
    mode: ExecutionMode
    leader: LeaderConfig
    trader: TraderConfig  # Your wallet for LIVE mode
    scaling_pct: ScalingConfigPct  # Percentage-based config
    scaling: ScalingConfig  # Legacy (computed from percentages)
    caps: CapsConfig  # Computed from percentages
    timing: TimingConfig
    circuit_breakers: CircuitBreakerConfig
    logging: LoggingConfig
    collector: CollectorConfig
    simulation: SimulationConfig
    persistence: PersistenceConfig
    api: ApiConfig
    markets: MarketsConfig
    preflight: PreflightConfig
    copy_trading: CopyTradingConfig
    safety: SafetyConfig  # Safety features
    mirror_strategy: MirrorStrategyConfig  # Live trade mirroring config
    strategy: str  # "delta" or "mirror"
    data_dir: Path
    
    # Runtime state
    _capital_initialized: bool = False
    
    def initialize_capital(self, capital: Optional[Decimal] = None) -> None:
        """Initialize capital values from percentages.
        
        Args:
            capital: Actual capital (from API in LIVE mode, None for DRY_RUN)
        """
        if capital is None:
            # DRY_RUN mode uses configured dry_run_capital
            capital = self.scaling_pct.dry_run_capital
        
        # Compute percentage-based values
        self.scaling_pct.compute_from_capital(capital)
        
        # Update legacy scaling config
        self.scaling.our_capital = self.scaling_pct.our_capital
        self.scaling.k_factor = self.scaling_pct.k_factor
        self.scaling.hourly_budget = self.scaling_pct.hourly_budget
        
        # Update caps from computed values
        self.caps.per_market_gross = self.scaling_pct.per_market_gross
        self.caps.per_side = self.scaling_pct.per_side
        self.caps.global_capital = self.scaling_pct.global_capital
        
        self._capital_initialized = True
    
    def get_decision_config(self) -> DecisionConfig:
        """Get config for decision engine."""
        if not self._capital_initialized:
            raise RuntimeError("Must call initialize_capital() before get_decision_config()")
        return DecisionConfig(
            scaling=self.scaling,
            caps=self.caps,
        )
    
    def get_state_machine_config(self) -> StateMachineConfig:
        """Get config for state machine."""
        return StateMachineConfig(
            burst_window_sec=self.timing.burst_window_sec,
            burst_poll_interval_sec=self.timing.burst_poll_interval_sec,
            burst_stabilization_threshold=self.timing.burst_stabilization_threshold,
            burst_stabilization_count=self.timing.burst_stabilization_count,
            resync_interval_sec=self.timing.resync_interval_sec,
            resync_error_threshold=self.timing.resync_error_threshold,
            follow_batch_window_sec=self.timing.follow_batch_window_sec,
        )
    
    def get_summary(self) -> Dict[str, Any]:
        """Get config summary for logging/display."""
        return {
            "mode": str(self.mode),
            "leader_address": self.leader.address[:10] + "..." if self.leader.address else None,
            "trader_address": self.trader.address[:10] + "..." if self.trader.address else None,
            "capital_initialized": self._capital_initialized,
            "our_capital": str(self.scaling.our_capital) if self._capital_initialized else "NOT SET",
            "k_factor": str(self.scaling_pct.k_factor),
            "hourly_budget_pct": f"{self.scaling_pct.hourly_budget_pct}%",
            "hourly_budget": str(self.scaling.hourly_budget) if self._capital_initialized else "NOT SET",
            "per_market_gross_pct": f"{self.scaling_pct.per_market_gross_pct}%",
            "per_side_pct": f"{self.scaling_pct.per_side_pct}%",
            "global_exposure_pct": f"{self.scaling_pct.global_exposure_pct}%",
            "market_min_dollars": str(self.caps.market_min_dollars),
            "burst_window_sec": self.timing.burst_window_sec,
            "resync_interval_sec": self.timing.resync_interval_sec,
            "market_type_filter": self.markets.type_filter,
            "epsilon_pct": f"{self.copy_trading.epsilon_pct}%",
            "resync_drift_start_pct": f"{self.copy_trading.resync_drift_start_pct}%",
        }


def load_config(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
) -> BotConfig:
    """Load configuration from YAML file and environment.
    
    Args:
        config_path: Path to config.yaml (default: config/config.yaml)
        env_path: Path to .env file (default: .env)
        
    Returns:
        Loaded BotConfig
    """
    # Load environment variables
    env_path = env_path or Path(".env")
    if env_path.exists():
        load_dotenv(env_path)
    
    # Determine config path
    if config_path is None:
        config_path = Path("config/config.yaml")
        if not config_path.exists():
            config_path = Path("config/config.example.yaml")
    
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    # Load YAML
    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)
    
    # Determine data directory
    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Parse mode
    mode_str = raw.get("mode", "DRY_RUN").upper()
    mode = ExecutionMode(mode_str)
    
    # Parse sections
    leader_raw = raw.get("leader", {})
    leader = LeaderConfig(
        address=leader_raw.get("address", ""),
        poll_interval_sec=float(leader_raw.get("poll_interval_sec", 1.0)),
        use_websocket=bool(leader_raw.get("use_websocket", False)),
    )
    
    trader_raw = raw.get("trader", {})
    trader = TraderConfig(
        address=trader_raw.get("address", os.getenv("POLYMARKET_FUNDER_ADDRESS", "")),
        private_key_env=trader_raw.get("private_key_env", "POLYMARKET_PRIVATE_KEY"),
        signature_type=int(trader_raw.get("signature_type", 2)),
    )
    
    # Parse percentage-based scaling config
    scaling_raw = raw.get("scaling", {})
    scaling_pct = ScalingConfigPct(
        k_factor=Decimal(str(scaling_raw.get("k_factor", 0.7))),
        hourly_budget_pct=Decimal(str(scaling_raw.get("hourly_budget_pct", 90.0))),
        per_market_gross_pct=Decimal(str(scaling_raw.get("per_market_gross_pct", 30.0))),
        per_side_pct=Decimal(str(scaling_raw.get("per_side_pct", 26.0))),
        global_exposure_pct=Decimal(str(scaling_raw.get("global_exposure_pct", 100.0))),
        dry_run_capital=Decimal(str(scaling_raw.get("dry_run_capital", 50.0))),
    )
    
    # Legacy scaling config (will be computed from percentages)
    scaling = ScalingConfig(
        our_capital=Decimal("0"),  # Computed at runtime
        k_factor=scaling_pct.k_factor,
        leader_capital=Decimal(str(scaling_raw.get("leader_estimated_capital", 0))),
        mode="percentage",
        hourly_budget=Decimal("0"),  # Computed at runtime
    )
    
    # Caps (computed from percentages, minimums are fixed)
    minimums_raw = raw.get("minimums", {})
    caps = CapsConfig(
        per_market_gross=Decimal("0"),  # Computed at runtime
        per_side=Decimal("0"),  # Computed at runtime
        global_capital=Decimal("0"),  # Computed at runtime
        market_min_dollars=Decimal(str(minimums_raw.get("market_order_dollars", 1))),
        limit_min_shares=Decimal(str(minimums_raw.get("limit_order_shares", 5))),
        share_precision=int(minimums_raw.get("share_precision", 2)),
    )
    
    timing_raw = raw.get("timing", {})
    timing = TimingConfig(
        burst_window_sec=float(timing_raw.get("burst_window_sec", 60)),
        burst_poll_interval_sec=float(timing_raw.get("burst_poll_interval_sec", 1.5)),
        burst_stabilization_threshold=Decimal(str(timing_raw.get("burst_stabilization_threshold", 0.50))),
        burst_stabilization_count=int(timing_raw.get("burst_stabilization_count", 3)),
        resync_interval_sec=float(timing_raw.get("resync_interval_sec", 60)),
        resync_error_threshold=Decimal(str(timing_raw.get("resync_error_threshold", 0.50))),
        follow_batch_window_sec=float(timing_raw.get("follow_batch_window_sec", 2.0)),
    )
    
    cb_raw = raw.get("circuit_breakers", {})
    circuit_breakers = CircuitBreakerConfig(
        max_consecutive_api_errors=int(cb_raw.get("max_consecutive_api_errors", 5)),
        max_pending_orders=int(cb_raw.get("max_pending_orders", 3)),
        max_unknown_fill_duration_sec=float(cb_raw.get("max_unknown_fill_duration_sec", 30)),
        max_orders_per_minute=int(cb_raw.get("max_orders_per_minute", 10)),
        max_new_exposure_per_minute=Decimal(str(cb_raw.get("max_new_exposure_per_minute", 10))),
        breaker_cooldown_sec=float(cb_raw.get("breaker_cooldown_sec", 60)),
    )
    
    log_raw = raw.get("logging", {})
    logging_config = LoggingConfig(
        level=os.getenv("LOG_LEVEL", log_raw.get("level", "INFO")),
        output=log_raw.get("output", "both"),
        file_path=log_raw.get("file_path", "logs/bot.jsonl"),
        trace_enabled=bool(log_raw.get("trace_enabled", True)),
        trace_path=log_raw.get("trace_path", "traces/decisions.jsonl"),
        trace_include_snapshots=bool(log_raw.get("trace_include_snapshots", True)),
        log_max_mb=int(log_raw.get("log_max_mb", 50)),
        log_backup_count=int(log_raw.get("log_backup_count", 5)),
        trace_max_mb=int(log_raw.get("trace_max_mb", 100)),
        trace_backup_count=int(log_raw.get("trace_backup_count", 5)),
    )
    
    coll_raw = raw.get("collector", {})
    collector = CollectorConfig(
        enabled=bool(coll_raw.get("enabled", True)),
        storage_format=coll_raw.get("storage_format", "jsonl"),
        storage_path=coll_raw.get("storage_path", "collected/"),
        position_snapshot_interval_sec=float(coll_raw.get("position_snapshot_interval_sec", 5)),
        record_prices=bool(coll_raw.get("record_prices", True)),
        price_snapshot_interval_sec=float(coll_raw.get("price_snapshot_interval_sec", 1)),
        max_file_size_mb=int(coll_raw.get("max_file_size_mb", 50)),
        min_exposure_dollars=float(coll_raw.get("min_exposure_dollars", 1.0)),
        trade_lookback_limit=int(coll_raw.get("trade_lookback_limit", 50)),
    )
    
    sim_raw = raw.get("simulation", {})
    simulation = SimulationConfig(
        fill_model=sim_raw.get("fill_model", "immediate"),
        simulated_spread_pct=float(sim_raw.get("simulated_spread_pct", 0.5)),
        simulated_slippage_pct=float(sim_raw.get("simulated_slippage_pct", 0.1)),
        fees_pct=float(sim_raw.get("fees_pct", 0.0)),
        random_seed=int(sim_raw.get("random_seed", 42)),
    )
    
    pers_raw = raw.get("persistence", {})
    persistence = PersistenceConfig(
        state_file=pers_raw.get("state_file", "state/bot_state.json"),
        auto_save_interval_sec=float(pers_raw.get("auto_save_interval_sec", 10)),
        backup_count=int(pers_raw.get("backup_count", 5)),
    )
    
    api_raw = raw.get("api", {})
    api = ApiConfig(
        base_url=api_raw.get("base_url", "https://clob.polymarket.com"),
        timeout_sec=float(api_raw.get("timeout_sec", 10)),
        retry_count=int(api_raw.get("retry_count", 3)),
        retry_delay_sec=float(api_raw.get("retry_delay_sec", 1.0)),
        rate_limit_rps=float(api_raw.get("rate_limit_rps", 5.0)),
        endpoints=api_raw.get("endpoints", {}),
    )
    
    markets_raw = raw.get("markets", {})
    markets = MarketsConfig(
        type_filter=markets_raw.get("type_filter", "hourly_btc"),
        allowlist=markets_raw.get("allowlist", []),
        denylist=markets_raw.get("denylist", []),
    )
    
    pf_raw = raw.get("preflight", {})
    preflight = PreflightConfig(
        smoke_test_duration_sec=float(pf_raw.get("smoke_test_duration_sec", 30)),
        min_collected_data_sec=float(pf_raw.get("min_collected_data_sec", 60)),
    )
    
    copy_raw = raw.get("copy_trading", {})
    copy_trading = CopyTradingConfig(
        epsilon_pct=Decimal(str(copy_raw.get("epsilon_pct", 1.0))),
        epsilon_min_dollars=Decimal(str(copy_raw.get("epsilon_min_dollars", 0.50))),
        resync_drift_start_pct=Decimal(str(copy_raw.get("resync_drift_start_pct", 5.0))),
        resync_drift_stop_pct=Decimal(str(copy_raw.get("resync_drift_stop_pct", 2.0))),
        resync_recent_activity_sec=float(copy_raw.get("resync_recent_activity_sec", 300.0)),
        max_spread=Decimal(str(copy_raw.get("max_spread", 0.05))),
        max_order_pct=Decimal(str(copy_raw.get("max_order_pct", 10.0))),
        max_order_chunks=int(copy_raw.get("max_order_chunks", 3)),
        buy_accumulator_max_age_sec=float(copy_raw.get("buy_accumulator_max_age_sec", 120.0)),
        sell_accumulator_max_age_sec=float(copy_raw.get("sell_accumulator_max_age_sec", 60.0)),
        expiry_close_minutes=float(copy_raw.get("expiry_close_minutes", 10.0)),
        expiry_force_seconds=float(copy_raw.get("expiry_force_seconds", 30.0)),
        reconcile_interval_sec=float(copy_raw.get("reconcile_interval_sec", 30.0)),
    )
    
    # Parse safety config
    safety_raw = raw.get("safety", {})
    safety = SafetyConfig(
        price_drift_enabled=bool(safety_raw.get("price_drift_enabled", True)),
        max_buy_price_drift_pct=Decimal(str(safety_raw.get("max_buy_price_drift_pct", 6.0))),
        max_spread_pct=Decimal(str(safety_raw.get("max_spread_pct", 6.0))),
        max_price_age_sec=float(safety_raw.get("max_price_age_sec", 10.0)),
        max_concurrent_positions=int(safety_raw.get("max_concurrent_positions", 10)),
        min_position_value=Decimal(str(safety_raw.get("min_position_value", 1.0))),
        max_session_loss_pct=Decimal(str(safety_raw.get("max_session_loss_pct", 20.0))),
        per_position_stop_loss_pct=Decimal(str(safety_raw.get("per_position_stop_loss_pct", 0))),
        block_loss_sells_if_leader_profit=bool(safety_raw.get("block_loss_sells_if_leader_profit", True)),
        min_order_interval_sec=float(safety_raw.get("min_order_interval_sec", 2.0)),
        market_open_wait_sec=float(safety_raw.get("market_open_wait_sec", 5.0)),
    )
    
    # Parse strategy selector (default: "delta" for existing behavior)
    strategy = raw.get("strategy", "delta")

    # Parse mirror strategy config
    mirror_raw = raw.get("mirror_strategy", {})
    mirror_strategy = MirrorStrategyConfig(
        staleness_window_sec=float(mirror_raw.get("staleness_window_sec", 10.0)),
        burst_window_sec=float(mirror_raw.get("burst_window_sec", 2.0)),
        snapshot_interval_sec=float(mirror_raw.get("snapshot_interval_sec", 20.0)),
        cash_reserve_pct=Decimal(str(mirror_raw.get("cash_reserve_pct", 20.0))),
        per_market_cap_pct=Decimal(str(mirror_raw.get("per_market_cap_pct", 15.0))),
        ratio_margin_pct=Decimal(str(mirror_raw.get("ratio_margin_pct", 10.0))),
        expiry_close_sec=float(mirror_raw.get("expiry_close_sec", 120.0)),
        retry_attempts=int(mirror_raw.get("retry_attempts", 2)),
        retry_delay_sec=float(mirror_raw.get("retry_delay_sec", 0.5)),
    )

    config = BotConfig(
        mode=mode,
        leader=leader,
        trader=trader,
        scaling_pct=scaling_pct,
        scaling=scaling,
        caps=caps,
        timing=timing,
        circuit_breakers=circuit_breakers,
        logging=logging_config,
        collector=collector,
        simulation=simulation,
        persistence=persistence,
        api=api,
        markets=markets,
        preflight=preflight,
        copy_trading=copy_trading,
        safety=safety,
        mirror_strategy=mirror_strategy,
        strategy=strategy,
        data_dir=data_dir,
    )
    
    # CRITICAL: Validate configuration
    _validate_config(config)
    
    return config


def _validate_config(config: BotConfig) -> None:
    """Validate configuration values.
    
    Note: Capital-dependent validation happens AFTER initialize_capital() is called.
    This validates the percentage-based config and address requirements.
    
    Raises:
        ValueError: If any configuration is invalid
    """
    errors = []
    
    # Leader address validation
    if not config.leader.address or config.leader.address == "0x0000000000000000000000000000000000000000":
        errors.append("leader.address must be set to a valid Polymarket address")
    elif not config.leader.address.startswith("0x") or len(config.leader.address) != 42:
        errors.append(f"leader.address '{config.leader.address}' is not a valid Ethereum address")
    
    # LIVE mode requires trader configuration
    if config.mode == ExecutionMode.LIVE:
        if not config.trader.address:
            errors.append("LIVE mode requires trader.address to be set")
        elif not config.trader.address.startswith("0x") or len(config.trader.address) != 42:
            errors.append(f"trader.address '{config.trader.address}' is not a valid Ethereum address")
    
    # Percentage-based scaling validation
    if config.scaling_pct.k_factor <= 0 or config.scaling_pct.k_factor > 2:
        errors.append(f"scaling.k_factor should be between 0 and 2, got {config.scaling_pct.k_factor}")
    if config.scaling_pct.hourly_budget_pct <= 0 or config.scaling_pct.hourly_budget_pct > 100:
        errors.append(f"scaling.hourly_budget_pct should be between 0 and 100, got {config.scaling_pct.hourly_budget_pct}")
    if config.scaling_pct.per_market_gross_pct <= 0 or config.scaling_pct.per_market_gross_pct > 100:
        errors.append(f"scaling.per_market_gross_pct should be between 0 and 100, got {config.scaling_pct.per_market_gross_pct}")
    if config.scaling_pct.per_side_pct <= 0 or config.scaling_pct.per_side_pct > 100:
        errors.append(f"scaling.per_side_pct should be between 0 and 100, got {config.scaling_pct.per_side_pct}")
    if config.scaling_pct.global_exposure_pct <= 0 or config.scaling_pct.global_exposure_pct > 100:
        errors.append(f"scaling.global_exposure_pct should be between 0 and 100, got {config.scaling_pct.global_exposure_pct}")
    
    # Logical consistency for percentages
    if config.scaling_pct.per_side_pct > config.scaling_pct.per_market_gross_pct:
        errors.append(f"per_side_pct ({config.scaling_pct.per_side_pct}%) cannot exceed per_market_gross_pct ({config.scaling_pct.per_market_gross_pct}%)")
    if config.scaling_pct.per_market_gross_pct > config.scaling_pct.global_exposure_pct:
        errors.append(f"per_market_gross_pct ({config.scaling_pct.per_market_gross_pct}%) cannot exceed global_exposure_pct ({config.scaling_pct.global_exposure_pct}%)")
    
    # DRY_RUN requires dry_run_capital
    if config.mode == ExecutionMode.DRY_RUN:
        if config.scaling_pct.dry_run_capital <= 0:
            errors.append(f"DRY_RUN mode requires positive dry_run_capital, got {config.scaling_pct.dry_run_capital}")
    
    # Minimums validation
    if config.caps.market_min_dollars <= 0:
        errors.append(f"minimums.market_order_dollars must be positive, got {config.caps.market_min_dollars}")
    
    # Copy trading validation
    if config.copy_trading.epsilon_pct < 0:
        errors.append(f"copy_trading.epsilon_pct must be non-negative, got {config.copy_trading.epsilon_pct}")
    if config.copy_trading.max_spread <= 0 or config.copy_trading.max_spread > Decimal("0.5"):
        errors.append(f"copy_trading.max_spread should be between 0 and 0.5, got {config.copy_trading.max_spread}")
    
    if errors:
        error_msg = "Configuration validation failed:\n  - " + "\n  - ".join(errors)
        raise ValueError(error_msg)
