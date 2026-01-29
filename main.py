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
    
    args = parser.parse_args()
    
    # Load config
    from src.config import load_config
    
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Run: copy config/config.example.yaml config/config.yaml")
        return 1
    
    # Handle different modes
    if args.preflight:
        from src.tools.preflight import run_preflight
        # Initialize capital so caps are computed for validation
        config.initialize_capital()
        success = run_preflight(config)
        return 0 if success else 1
    
    elif args.collect:
        from src.collector import DataCollector
        from src.data.live_source import LiveDataSource

        data_source = LiveDataSource(
            leader_address=config.leader.address,
        )

        collector = DataCollector(config, data_source)
        try:
            if args.duration:
                import threading
                timer = threading.Timer(args.duration * 60, collector.stop)
                timer.daemon = True
                timer.start()
            collector.run()
        except KeyboardInterrupt:
            print("\nCollector stopped.")
        finally:
            data_source.close()
        return 0
    
    elif args.replay_session:
        from src.strategy.session_replayer import run_session_replay
        run_session_replay(args.replay_session)
        return 0
    
    elif args.mode:
        # Direct mode execution
        from src.core import ExecutionMode, SystemClock
        from decimal import Decimal
        
        if args.mode == "dry-run":
            from src.execution import NullExecutionAdapter
            adapter = NullExecutionAdapter()
            
            # DRY_RUN uses configured dry_run_capital
            print("=" * 60)
            print("  DRY-RUN MODE (Simulation)")
            print("=" * 60)
            print()
            print(f"  Using simulated capital: ${config.scaling_pct.dry_run_capital}")
            config.initialize_capital()  # Uses dry_run_capital
            print(f"  Hourly budget ({config.scaling_pct.hourly_budget_pct}%): ${config.scaling.hourly_budget}")
            print(f"  Per-market cap ({config.scaling_pct.per_market_gross_pct}%): ${config.caps.per_market_gross}")
            print(f"  Per-side cap ({config.scaling_pct.per_side_pct}%): ${config.caps.per_side}")
            print(f"  Global exposure cap ({config.scaling_pct.global_exposure_pct}%): ${config.caps.global_capital}")
            print()
            
            # Select strategy runner
            if config.strategy == "mirror":
                from src.strategy.mirror_runner import MirrorRunner
                runner = MirrorRunner(
                    config,
                    adapter,
                    SystemClock(),
                    duration_minutes=args.duration,
                    record_session=args.record,
                )
            elif not args.verbose:
                from src.strategy.runner_display import DisplayRunner
                runner = DisplayRunner(
                    config,
                    adapter,
                    SystemClock(),
                    duration_minutes=args.duration,
                )
            else:
                from src.strategy import StrategyRunner
                runner = StrategyRunner(config, adapter, SystemClock())
            
        else:  # live
            from src.execution.live_adapter import LiveExecutionAdapter
            from src.tools.preflight import run_preflight
            from src.strategy import StrategyRunner
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
            print(f"  Hourly budget ({config.scaling_pct.hourly_budget_pct}%): ${config.scaling.hourly_budget}")
            print(f"  Per-market cap ({config.scaling_pct.per_market_gross_pct}%): ${config.caps.per_market_gross}")
            print(f"  Per-side cap ({config.scaling_pct.per_side_pct}%): ${config.caps.per_side}")
            print(f"  Global exposure cap ({config.scaling_pct.global_exposure_pct}%): ${config.caps.global_capital}")
            print()
            
            if not run_preflight(config):
                print("Preflight failed. Cannot start live mode.")
                return 1
            
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
            
            if config.strategy == "mirror":
                from src.strategy.mirror_runner import MirrorRunner
                runner = MirrorRunner(
                    config,
                    adapter,
                    SystemClock(),
                )
            else:
                runner = StrategyRunner(config, adapter, SystemClock())

        try:
            runner.run()
        except KeyboardInterrupt:
            print("\nStopped by user.")
        
        # Note: DisplayRunner handles its own summary in _print_final_summary()
        # Only print summary for runners that don't have their own summary handling
        # (DisplayRunner has shadow portfolio, so it has its own comprehensive summary)
        if hasattr(runner, 'display') and runner.display and not hasattr(runner, 'shadow'):
            runner.display.print_summary()
        
        return 0
    
    else:
        # Interactive menu (default)
        from src.cli import MainMenu
        menu = MainMenu(config)
        menu.run()
        return 0


if __name__ == "__main__":
    sys.exit(main())
