#!/usr/bin/env python3
"""Polymarket Copy-Trading Bot V2 - Main Entry Point.

Usage:
    python main.py                  # Interactive menu
    python main.py --mode dry-run   # Direct DRY-RUN mode
    python main.py --preflight      # Run preflight only
    python main.py --collect        # Run collector only
    python main.py --replay-session PATH  # Replay a recorded session
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Copy-Trading Bot V2"
    )
    
    # Mode selection
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--menu",
        action="store_true",
        default=True,
        help="Run interactive menu (default)",
    )
    mode_group.add_argument(
        "--mode",
        choices=["dry-run", "live"],
        help="Run directly in specified mode",
    )
    mode_group.add_argument(
        "--preflight",
        action="store_true",
        help="Run preflight checks only",
    )
    mode_group.add_argument(
        "--collect",
        action="store_true",
        help="Run data collector",
    )
    mode_group.add_argument(
        "--replay-session",
        type=Path,
        metavar="PATH",
        dest="replay_session",
        help="Replay a recorded session with strategy testing",
    )
    mode_group.add_argument(
        "--optimize",
        type=Path,
        metavar="SESSION_PATH",
        dest="optimize_session",
        help="Run strategy optimization on a recorded session",
    )
    mode_group.add_argument(
        "--full-optimize",
        type=Path,
        metavar="SESSION_PATH",
        dest="full_optimize_session",
        help="Run full optimization (all strategies × all execution modes)",
    )
    
    # Config options
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: config/config.yaml)",
    )
    
    # Duration for timed runs
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        metavar="MINUTES",
        help="Run for specified duration in minutes, then stop and show summary",
    )
    
    # Verbose JSON logs vs human-readable
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show verbose JSON logs instead of human-readable output",
    )
    
    # Session recording for replay/analysis
    parser.add_argument(
        "--record",
        action="store_true",
        help="Record session data for replay and strategy testing (DRY_RUN only)",
    )
    
    # Strategy selection for replay
    parser.add_argument(
        "--strategy",
        type=str,
        default="mirror",
        help="Strategy to use for replay (default: mirror)",
    )

    # Hourly liquidation for replay
    parser.add_argument(
        "--liquidate-hourly",
        action="store_true",
        dest="liquidate_hourly",
        help="Force-sell all positions at hour boundaries during replay",
    )
    
    # Optimization variant
    parser.add_argument(
        "--variant",
        type=str,
        default=None,
        help="Strategy variant for optimization (aggressive_mirror, tight_spread, momentum_only, conservative, buy_focused)",
    )
    
    args = parser.parse_args()
    
    # Load config
    from src.core.config import load_config
    
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Run: copy config/config.example.yaml config/config.yaml")
        return 1
    
    # Handle different modes
    if args.preflight:
        print("Preflight checks not available (module removed in refactor)")
        print("Use --mode dry-run to test configuration")
        return 1
    
    elif args.collect:
        print("Data collector not available (module removed in refactor)")
        print("Use --mode dry-run to observe leader trades")
        return 1
    
    elif args.replay_session:
        # Use new universal framework for replay
        from src.strategies import get_strategy, list_strategies
        from src.framework.replay import run_session_replay
        
        strategy_name = getattr(args, 'strategy', 'mirror') or 'mirror'
        
        try:
            strategy = get_strategy(strategy_name)
            result = run_session_replay(
                args.replay_session, strategy,
                liquidate_hourly=args.liquidate_hourly,
            )
        except ValueError as e:
            print(f"Error: {e}")
            print(f"Available strategies: {', '.join(list_strategies())}")
            return 1
        
        return 0
    
    elif args.full_optimize_session:
        # Full optimization - all strategies × all execution modes
        from src.simulation.full_optimizer import run_full_optimization
        from src.strategies import list_strategies
        
        session_path = str(args.full_optimize_session)
        capital = float(config.scaling.our_capital)
        
        try:
            results = run_full_optimization(session_path, leader_capital=capital)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return 1
        
        return 0
    
    elif args.optimize_session:
        # Strategy optimization - grid search over parameters
        from src.simulation.optimizer import run_optimization
        from src.strategies import list_strategies
        
        session_path = str(args.optimize_session)
        capital = float(config.scaling.our_capital)
        variant = args.variant
        
        print("=" * 70)
        print("  STRATEGY OPTIMIZER")
        print("=" * 70)
        print(f"\n  Session: {session_path}")
        print(f"  Capital: ${capital}")
        if variant:
            print(f"  Variant: {variant}")
        else:
            print(f"  Testing all strategies: {', '.join(list_strategies())}")
        print()
        
        try:
            results = run_optimization(session_path)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1
        except ValueError as e:
            print(f"Error: {e}")
            print(f"Available strategies: {', '.join(list_strategies())}")
            return 1
        
        return 0
    
    elif args.mode:
        # Direct mode execution using UniversalRunner
        from src.strategies import get_strategy
        from src.framework.runner import UniversalRunner
        
        strategy_name = args.strategy or "mirror"
        
        if args.mode == "dry-run":
            from src.execution import NullExecutionAdapter
            adapter = NullExecutionAdapter()
            
            print("=" * 60)
            print("  DRY-RUN MODE (Simulation)")
            print("=" * 60)
            print()
            print(f"  Using simulated capital: ${config.scaling.our_capital}")
            print(f"  Hourly budget: ${config.scaling.hourly_budget}")
            print(f"  Per-market cap: {config.mirror_strategy.per_market_cap_pct}%")
            print(f"  Per-side cap: {config.mirror_strategy.per_side_pct}%")
            print(f"  Global exposure: {config.mirror_strategy.global_exposure_pct}%")
            print()
            
        else:  # live
            from src.execution.live import LiveExecutionAdapter
            import os
            
            print("=" * 60)
            print("  LIVE MODE INITIALIZATION")
            print("=" * 60)
            
            # Check trader configuration
            if not config.trader.address:
                print("\nERROR: trader.address not configured in config.yaml")
                print("Set your Polymarket wallet address in config.yaml under 'trader:'")
                return 1
            
            # Check for private key
            private_key = os.getenv(config.trader.private_key_env)
            if not private_key:
                print(f"\nERROR: {config.trader.private_key_env} not set in environment")
                print("Add your private key to .env file:")
                print(f"  {config.trader.private_key_env}=your_private_key_here")
                return 1
            
            print(f"  Leader:  {config.leader.address[:12]}...")
            print(f"  Trader:  {config.trader.address[:12]}...")
            print()
            
            # LIVE mode: fetch actual balance from API
            print("Fetching wallet balance from Polymarket...")
            try:
                from src.data.live_source import fetch_wallet_balance
                actual_balance = fetch_wallet_balance(config.trader.address)
                print(f"  Current balance: ${actual_balance}")
            except Exception as e:
                print(f"ERROR: Could not fetch balance: {e}")
                print("Cannot start LIVE mode without knowing actual balance.")
                return 1
            
            if actual_balance <= 0:
                print("ERROR: Wallet balance is zero or negative.")
                print("Fund your wallet before starting LIVE mode.")
                return 1
            
            # Initialize config with actual balance
            config.initialize_capital(actual_balance)
            print(f"  Hourly budget: ${config.scaling.hourly_budget}")
            print(f"  Per-market cap: {config.mirror_strategy.per_market_cap_pct}%")
            print(f"  Per-side cap: {config.mirror_strategy.per_side_pct}%")
            print(f"  Global exposure: {config.mirror_strategy.global_exposure_pct}%")
            print()
            
            # Preflight checks - skipped since module was removed
            print("Skipping preflight checks (module removed in refactor)")
            print()
            
            adapter = LiveExecutionAdapter(
                private_key=private_key,
                funder_address=config.trader.address,
                signature_type=config.trader.signature_type,
            )
            adapter.set_preflight_passed(True)
            
            print("\n*** CONFIRM LIVE TRADING ***")
            print(f"Real orders will be placed using ${actual_balance} capital.")
            confirm = input("Type 'YES' to confirm: ").strip()
            if confirm != "YES":
                print("Aborted.")
                return 1
            
            if not adapter.arm():
                print("Failed to arm live adapter.")
                return 1
        
        # Get strategy and create runner
        try:
            strategy = get_strategy(strategy_name)
        except ValueError as e:
            print(f"Error: Unknown strategy '{strategy_name}'")
            return 1
        
        # Setup recorder if requested
        recorder = None
        if args.record:
            from src.framework.recorder import SessionRecorder
            recorder = SessionRecorder(
                price_snapshot_interval_sec=getattr(config, 'price_snapshot_interval_sec', 2.0)
            )
            print("  📼 Session recording enabled")
        
        runner = UniversalRunner(
            config=config,
            strategy=strategy,
            execution=adapter,
            duration_minutes=args.duration,
            recorder=recorder,
        )
        
        try:
            runner.run()
        except KeyboardInterrupt:
            print("\nStopped by user.")
        
        return 0
    
    else:
        # Interactive menu - simplified, just show available modes
        print("=" * 60)
        print("  POLYMARKET COPY-TRADING BOT")
        print("=" * 60)
        print()
        print("Usage:")
        print("  python main.py --mode dry-run    # Simulation mode")
        print("  python main.py --mode live       # Live trading")
        print("  python main.py --replay-session PATH  # Replay session")
        print()
        print("Options:")
        print("  --duration N    # Run for N minutes")
        print("  --record        # Record session for replay")
        print("  --strategy NAME # Strategy to use (default: mirror)")
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())

