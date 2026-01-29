"""Interactive CLI menu for operator control.

This CLI is for control only - it does NOT contain trading logic.
It starts the appropriate mode runner and displays status.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import BotConfig


class MainMenu:
    """Main interactive menu for bot control."""
    
    def __init__(self, config: "BotConfig"):
        self.config = config
        self._running = False
        self._armed = False
    
    def display_banner(self) -> None:
        """Display startup banner."""
        mode = self.config.mode
        print("\n" + "=" * 70)
        print("         POLYMARKET COPY-TRADING BOT V2 - OPERATOR CONSOLE")
        print("=" * 70)
        print(f"  MODE: {mode}")
        print(f"  Leader: {self.config.leader.address[:20]}...")
        # Show dry_run_capital before initialization
        if self.config._capital_initialized:
            print(f"  Our Capital: ${self.config.scaling.our_capital}")
        else:
            print(f"  Simulated Capital: ${self.config.scaling_pct.dry_run_capital} (will initialize on run)")
        print(f"  K Factor: {self.config.scaling_pct.k_factor}")
        print("-" * 70)
    
    def display_menu(self) -> None:
        """Display main menu options."""
        print("\n--- MAIN MENU ---")
        print("1. Run Preflight Checks")
        print("2. Start DRY-RUN (real data, no orders)")
        print("3. Start Data Collector")
        print("4. View Current State")
        print("5. View Config Summary")
        
        if self.config.mode.value == "LIVE":
            print("-" * 30)
            print("6. [LIVE] ARM Trading")
            print("7. [LIVE] DISARM Trading (KILL)")
            print("8. [LIVE] Start Live Trading")
        
        print("-" * 30)
        print("0. Exit")
        print()
    
    def run(self) -> None:
        """Run the interactive menu loop."""
        self.display_banner()
        
        while True:
            self.display_menu()
            choice = input("Select option: ").strip()
            
            if choice == "0":
                print("\nExiting...")
                break
            elif choice == "1":
                self._run_preflight()
            elif choice == "2":
                self._run_dry_run()
            elif choice == "3":
                self._run_collector()
            elif choice == "4":
                self._view_state()
            elif choice == "5":
                self._view_config()
            elif choice == "6" and self.config.mode.value == "LIVE":
                self._arm_live()
            elif choice == "7" and self.config.mode.value == "LIVE":
                self._disarm_live()
            elif choice == "8" and self.config.mode.value == "LIVE":
                self._run_live()
            else:
                print("Invalid option. Please try again.")
    
    def _run_preflight(self) -> None:
        """Run preflight checks."""
        print("\n" + "=" * 50)
        print("Running Preflight Checks...")
        print("=" * 50)
        
        # Initialize capital if not done yet (needed for preflight checks)
        if not self.config._capital_initialized:
            self.config.initialize_capital()
            print(f"Capital initialized: ${self.config.scaling.our_capital}")
        
        # Import here to avoid circular imports
        from ..tools.preflight import run_preflight
        
        passed = run_preflight(self.config)
        
        if passed:
            print("\n[OK] All preflight checks passed.")
        else:
            print("\n[FAIL] Some preflight checks failed.")
    
    def _run_dry_run(self) -> None:
        """Start DRY-RUN mode."""
        print("\n" + "=" * 50)
        print("Starting DRY-RUN Mode...")
        print("=" * 50)
        print("Press Ctrl+C to stop.\n")

        from ..execution import NullExecutionAdapter
        from ..core import SystemClock

        # CRITICAL: Initialize capital before creating runner
        # DRY_RUN uses dry_run_capital from config
        if not self.config._capital_initialized:
            self.config.initialize_capital()
            print(f"Capital initialized: ${self.config.scaling.our_capital}")
            print(f"Hourly budget: ${self.config.scaling.hourly_budget}")
            print()

        # Select strategy runner based on config
        if self.config.strategy == "mirror":
            print(f"Strategy: MIRROR (live trade mirroring)\n")
            from ..strategy.mirror_runner import MirrorRunner
            runner = MirrorRunner(
                config=self.config,
                adapter=NullExecutionAdapter(),
                clock=SystemClock(),
            )
        else:
            print(f"Strategy: DELTA (snapshot-based)\n")
            from ..strategy.runner_display import DisplayRunner
            runner = DisplayRunner(
                config=self.config,
                execution_adapter=NullExecutionAdapter(),
                clock=SystemClock(),
            )

        try:
            runner.run()
        except KeyboardInterrupt:
            print("\nDRY-RUN stopped by user.")
    
    def _run_collector(self) -> None:
        """Start data collector."""
        print("\n" + "=" * 50)
        print("Starting Data Collector...")
        print("=" * 50)
        print("Press Ctrl+C to stop.\n")
        
        from ..collector.collector import DataCollector
        
        collector = DataCollector(self.config)
        
        try:
            collector.run()
        except KeyboardInterrupt:
            print("\nCollector stopped by user.")
    
    def _view_state(self) -> None:
        """View current bot state."""
        print("\n" + "=" * 50)
        print("Current State")
        print("=" * 50)
        
        state_file = self.config.data_dir / self.config.persistence.state_file
        
        if state_file.exists():
            import json
            with open(state_file) as f:
                state = json.load(f)
            print(json.dumps(state, indent=2))
        else:
            print("No state file found. Bot has not run yet.")
    
    def _view_config(self) -> None:
        """View config summary."""
        print("\n" + "=" * 50)
        print("Configuration Summary")
        print("=" * 50)
        
        import json
        summary = self.config.get_summary()
        print(json.dumps(summary, indent=2))
    
    def _arm_live(self) -> None:
        """ARM live trading."""
        print("\n" + "=" * 50)
        print("ARM LIVE TRADING")
        print("=" * 50)
        
        print("\nWARNING: This will enable REAL order placement!")
        print("\nCurrent Settings:")
        print(f"  Mode: {self.config.mode}")
        print(f"  K factor: {self.config.scaling_pct.k_factor}")
        print(f"  Per-market gross cap: {self.config.scaling_pct.per_market_gross_pct}%")
        print(f"  Per-side cap: {self.config.scaling_pct.per_side_pct}%")
        print(f"  Global exposure cap: {self.config.scaling_pct.global_exposure_pct}%")
        print(f"  Hourly budget: {self.config.scaling_pct.hourly_budget_pct}%")
        
        confirm = input("\nType 'ARM' to confirm: ").strip()
        
        if confirm == "ARM":
            self._armed = True
            print("\n[ARMED] Live trading is now enabled.")
        else:
            print("\nArming cancelled.")
    
    def _disarm_live(self) -> None:
        """DISARM live trading (KILL switch)."""
        self._armed = False
        print("\n" + "=" * 50)
        print("[DISARMED] Live trading is now DISABLED.")
        print("=" * 50)
    
    def _run_live(self) -> None:
        """Start LIVE trading."""
        if not self._armed:
            print("\n[ERROR] Must ARM before starting live trading.")
            print("Use option 7 to ARM first.")
            return
        
        print("\n" + "=" * 50)
        print("Starting LIVE Trading...")
        print("=" * 50)
        print("\nWARNING: REAL orders will be placed!")
        
        import os
        from ..strategy.runner import StrategyRunner
        from ..execution.live_adapter import LiveExecutionAdapter
        from ..core import SystemClock
        from ..tools.preflight import run_preflight
        
        # Check for private key
        private_key = os.getenv(self.config.trader.private_key_env)
        if not private_key:
            print(f"\n[ERROR] {self.config.trader.private_key_env} not set in environment")
            return
        
        # Fetch actual balance for LIVE mode
        if not self.config._capital_initialized:
            print("\nFetching wallet balance...")
            try:
                from ..data.live_source import fetch_wallet_balance
                actual_balance = fetch_wallet_balance(self.config.trader.address)
                print(f"  Balance: ${actual_balance}")
                self.config.initialize_capital(actual_balance)
            except Exception as e:
                print(f"\n[ERROR] Could not fetch balance: {e}")
                return
        
        print(f"\nCapital: ${self.config.scaling.our_capital}")
        print(f"Hourly budget: ${self.config.scaling.hourly_budget}")
        print("Press Ctrl+C to stop.\n")
        
        # Run preflight
        if not run_preflight(self.config):
            print("\n[ERROR] Preflight checks failed. Cannot start live trading.")
            return
        
        adapter = LiveExecutionAdapter(
            private_key=private_key,
            funder_address=self.config.trader.address,
            signature_type=self.config.trader.signature_type,
        )
        adapter.set_preflight_passed(True)
        
        if not adapter.arm():
            print("\n[ERROR] Failed to arm live adapter.")
            return
        
        runner = StrategyRunner(
            config=self.config,
            execution_adapter=adapter,
            clock=SystemClock(),
        )
        
        try:
            runner.run()
        except KeyboardInterrupt:
            print("\nLive trading stopped by user.")
            adapter.disarm()
