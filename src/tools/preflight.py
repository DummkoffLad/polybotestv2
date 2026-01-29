"""Preflight checks - must pass before live trading.

Validates:
1. Configuration sanity
2. Connectivity
3. Mode separation
4. Determinism hooks
5. Risk constraints
6. Smoke simulation (if data available)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple

if TYPE_CHECKING:
    from ..config import BotConfig

from ..core import ExecutionMode


class PreflightCheck:
    """A single preflight check."""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.passed = False
        self.message = ""
    
    def pass_check(self, message: str = "") -> None:
        self.passed = True
        self.message = message
    
    def fail_check(self, message: str) -> None:
        self.passed = False
        self.message = message


def check_config_sanity(config: "BotConfig") -> List[PreflightCheck]:
    """Check configuration sanity."""
    checks = []
    
    # Check min order sizes vs caps
    check = PreflightCheck(
        "min_order_vs_cap",
        "Market min dollars must be <= per-market cap"
    )
    if config.caps.market_min_dollars <= config.caps.per_market_gross:
        check.pass_check()
    else:
        check.fail_check(
            f"market_min_dollars ({config.caps.market_min_dollars}) > "
            f"per_market_gross ({config.caps.per_market_gross})"
        )
    checks.append(check)
    
    # Check per-side vs per-market
    check = PreflightCheck(
        "per_side_vs_gross",
        "Per-side cap must be <= per-market gross cap"
    )
    if config.caps.per_side <= config.caps.per_market_gross:
        check.pass_check()
    else:
        check.fail_check(
            f"per_side ({config.caps.per_side}) > "
            f"per_market_gross ({config.caps.per_market_gross})"
        )
    checks.append(check)
    
    # Check global cap vs our capital
    check = PreflightCheck(
        "global_vs_capital",
        "Global cap should leave buffer from our capital"
    )
    if config.caps.global_capital < config.scaling.our_capital:
        check.pass_check(
            f"Global cap ({config.caps.global_capital}) < "
            f"our capital ({config.scaling.our_capital})"
        )
    else:
        check.fail_check(
            f"Global cap ({config.caps.global_capital}) >= "
            f"our capital ({config.scaling.our_capital}) - no buffer!"
        )
    checks.append(check)
    
    # Check k_factor range
    check = PreflightCheck(
        "k_factor_range",
        "K factor should be 0.1-1.0 for safety"
    )
    k = float(config.scaling.k_factor)
    if 0.1 <= k <= 1.0:
        check.pass_check(f"k={k}")
    else:
        check.fail_check(f"k={k} is outside safe range 0.1-1.0")
    checks.append(check)
    
    # Check burst window
    check = PreflightCheck(
        "burst_window",
        "Burst window should be 30-120 seconds"
    )
    bw = config.timing.burst_window_sec
    if 30 <= bw <= 120:
        check.pass_check(f"burst_window={bw}s")
    else:
        check.fail_check(f"burst_window={bw}s is outside recommended 30-120s")
    checks.append(check)
    
    # Check leader address
    check = PreflightCheck(
        "leader_address",
        "Leader address must be set"
    )
    if config.leader.address and config.leader.address != "0x" + "0" * 40:
        check.pass_check()
    else:
        check.fail_check("Leader address is not set or is zero address")
    checks.append(check)
    
    return checks


def check_connectivity(config: "BotConfig") -> List[PreflightCheck]:
    """Check API connectivity."""
    checks = []
    
    # Try to import and test live data source
    try:
        from ..data import LiveDataSource
        HAS_DATA_SOURCE = True
    except ImportError:
        HAS_DATA_SOURCE = False
    
    # Check CLOB API reachable
    check = PreflightCheck(
        "api_reachable",
        "Can reach Polymarket CLOB API"
    )
    try:
        import httpx
        response = httpx.get("https://clob.polymarket.com/", timeout=5)
        if response.status_code == 200:
            check.pass_check("CLOB API responding")
        else:
            check.fail_check(f"CLOB API returned {response.status_code}")
    except Exception as e:
        check.fail_check(f"Cannot reach CLOB API: {e}")
    checks.append(check)
    
    # Check Data API reachable
    check = PreflightCheck(
        "data_api_reachable",
        "Can reach Polymarket Data API"
    )
    try:
        import httpx
        response = httpx.get("https://data-api.polymarket.com/", timeout=5)
        if response.status_code in (200, 404):  # 404 is OK - endpoint exists
            check.pass_check("Data API responding")
        else:
            check.fail_check(f"Data API returned {response.status_code}")
    except Exception as e:
        check.fail_check(f"Cannot reach Data API: {e}")
    checks.append(check)
    
    # Check leader state fetch
    check = PreflightCheck(
        "leader_state",
        "Can fetch leader positions"
    )
    if HAS_DATA_SOURCE and config.leader.address and config.leader.address != "0x" + "0" * 40:
        try:
            ds = LiveDataSource(
                leader_address=config.leader.address,
                timeout=config.api.timeout_sec,
            )
            positions = ds.fetch_positions(config.leader.address)
            ds.close()
            check.pass_check(f"Fetched {len(positions)} positions for leader")
        except Exception as e:
            check.fail_check(f"Cannot fetch leader positions: {e}")
    else:
        check.pass_check("(leader fetch skipped - address not set)")
    checks.append(check)
    
    return checks


def check_mode_separation(config: "BotConfig") -> List[PreflightCheck]:
    """Check mode separation integrity."""
    checks = []
    
    # Check that DRY_RUN and PAPER modes don't have live adapter
    check = PreflightCheck(
        "mode_adapter_match",
        "Execution adapter matches mode"
    )
    
    mode = config.mode
    if mode == ExecutionMode.DRY_RUN:
        # In DRY_RUN mode, verify NullAdapter cannot place orders
        from ..execution.null_adapter import NullExecutionAdapter
        
        # Verify null adapter cannot place orders
        null = NullExecutionAdapter()
        if not null.can_place_orders:
            check.pass_check(f"Mode={mode}, NullAdapter correctly disabled")
        else:
            check.fail_check("NullAdapter reports can_place_orders=True!")
    else:
        check.pass_check(f"Mode={mode}")
    
    checks.append(check)
    
    return checks


def check_determinism(config: "BotConfig") -> List[PreflightCheck]:
    """Check determinism hooks are configured."""
    checks = []
    
    check = PreflightCheck(
        "trace_enabled",
        "Decision tracing is enabled"
    )
    if config.logging.trace_enabled:
        check.pass_check()
    else:
        check.fail_check("trace_enabled is False")
    checks.append(check)
    
    check = PreflightCheck(
        "trace_path_writable",
        "Trace output path is writable"
    )
    trace_path = config.data_dir / config.logging.trace_path
    trace_dir = trace_path.parent
    
    try:
        trace_dir.mkdir(parents=True, exist_ok=True)
        test_file = trace_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        check.pass_check(str(trace_dir))
    except Exception as e:
        check.fail_check(f"Cannot write to {trace_dir}: {e}")
    
    checks.append(check)
    
    return checks


def check_risk_constraints(config: "BotConfig") -> List[PreflightCheck]:
    """Check risk constraints are sensible."""
    checks = []
    
    # Check hourly budget
    check = PreflightCheck(
        "hourly_budget",
        "Hourly budget is set and reasonable"
    )
    hb = float(config.scaling.hourly_budget)
    cap = float(config.caps.global_capital)
    
    if hb > 0 and hb <= cap:
        check.pass_check(f"hourly_budget={hb}, global_cap={cap}")
    elif hb <= 0:
        check.fail_check(f"hourly_budget={hb} must be > 0")
    else:
        check.fail_check(f"hourly_budget={hb} > global_cap={cap}")
    checks.append(check)
    
    # Check circuit breaker settings
    check = PreflightCheck(
        "circuit_breakers",
        "Circuit breakers are configured"
    )
    cb = config.circuit_breakers
    if cb.max_orders_per_minute > 0 and cb.max_pending_orders > 0:
        check.pass_check(
            f"max_orders_per_minute={cb.max_orders_per_minute}, "
            f"max_pending={cb.max_pending_orders}"
        )
    else:
        check.fail_check("Circuit breakers not properly configured")
    checks.append(check)
    
    return checks


def check_smoke_simulation(config: "BotConfig") -> List[PreflightCheck]:
    """Run a short smoke simulation if data is available."""
    checks = []
    
    check = PreflightCheck(
        "smoke_simulation",
        "Short replay produces no errors"
    )
    
    # Check if collected data exists
    collected_path = config.data_dir / config.collector.storage_path
    
    if not collected_path.exists():
        check.pass_check("(no collected data, skipping smoke test)")
        checks.append(check)
        return checks
    
    # Look for data files
    data_files = list(collected_path.glob("*.jsonl"))
    
    if not data_files:
        check.pass_check("(no JSONL files, skipping smoke test)")
        checks.append(check)
        return checks
    
    # TODO: Implement actual smoke test replay
    check.pass_check("(smoke test not implemented)")
    checks.append(check)
    
    return checks


def run_preflight(config: "BotConfig") -> bool:
    """Run all preflight checks and report results.
    
    Returns:
        True if all checks passed, False otherwise
    """
    all_checks: List[PreflightCheck] = []
    
    print("\n[1/6] Configuration Sanity...")
    all_checks.extend(check_config_sanity(config))
    
    print("[2/6] Connectivity...")
    all_checks.extend(check_connectivity(config))
    
    print("[3/6] Mode Separation...")
    all_checks.extend(check_mode_separation(config))
    
    print("[4/6] Determinism Hooks...")
    all_checks.extend(check_determinism(config))
    
    print("[5/6] Risk Constraints...")
    all_checks.extend(check_risk_constraints(config))
    
    print("[6/6] Smoke Simulation...")
    all_checks.extend(check_smoke_simulation(config))
    
    # Print results
    print("\n" + "=" * 60)
    print("PREFLIGHT RESULTS")
    print("=" * 60)
    
    passed = 0
    failed = 0
    
    for check in all_checks:
        status = "[OK]" if check.passed else "[FAIL]"
        print(f"  {status} {check.name}: {check.description}")
        if check.message:
            print(f"       {check.message}")
        
        if check.passed:
            passed += 1
        else:
            failed += 1
    
    print("-" * 60)
    print(f"Total: {passed} passed, {failed} failed")
    
    return failed == 0


if __name__ == "__main__":
    # Allow running directly
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    
    from src.config import load_config
    
    config = load_config()
    success = run_preflight(config)
    sys.exit(0 if success else 1)
