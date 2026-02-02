"""
Simple Strategy Optimizer - uses existing SessionReplayer infrastructure.

Runs the same replayer multiple times with different config overrides to find
optimal parameters. No duplicated logic - just reuses what already works.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..framework.replay import SessionReplayer, ReplayResult
from ..strategies.base import get_strategy, list_strategies


@dataclass
class OptimizationResult:
    """Extended replay result with optimization metadata."""
    config_name: str
    strategy_name: str
    params: Dict[str, Any]
    result: ReplayResult
    
    # Computed scores
    score: Decimal = Decimal("0")
    
    def __post_init__(self):
        # Score = total PnL weighted by how much is realized vs unrealized
        r = self.result
        realized_weight = Decimal("1.2")  # Prefer realized gains
        unrealized_weight = Decimal("0.8")  # Discount unrealized
        self.score = r.realized_pnl * realized_weight + r.unrealized_pnl * unrealized_weight


# All tunable parameters from StrategyConfig that can be overridden via replay
CONFIG_PARAMS = {
    # Scaling parameters
    "scaling.k_factor": [0.5, 0.7, 0.85, 1.0, 1.2],
    "scaling.our_capital": [100],  # Fixed - from session
    "scaling.hourly_budget": [50, 75, 100, 150],
    "scaling.leader_estimated_capital": [800],  # ~$800 as user mentioned
    
    # Mirror strategy parameters
    "mirror_strategy.cash_reserve_pct": [5, 10, 15, 20],
    "mirror_strategy.per_market_cap_pct": [20, 30, 40, 50],
    "mirror_strategy.per_side_pct": [18, 26, 35],
    "mirror_strategy.global_exposure_pct": [80, 100],
    
    # Cost thresholds - these are in StrategyConfig
    "simulation.spread_cost_pct": [1.5, 2.0, 2.5],
    "simulation.slippage_cost_pct": [0.5, 1.0, 1.5],
    "simulation.max_total_cost_pct": [6, 8, 10],
}

# Default single values for baseline testing
DEFAULT_PARAMS = {
    "scaling.k_factor": "0.85",
    "scaling.our_capital": "100",
    "scaling.hourly_budget": "100",
    "scaling.leader_estimated_capital": "800",
    "mirror_strategy.cash_reserve_pct": "10",
    "mirror_strategy.per_market_cap_pct": "30", 
    "mirror_strategy.per_side_pct": "26",
    "mirror_strategy.global_exposure_pct": "100",
}


class SimpleOptimizer:
    """
    Simple optimizer that reuses SessionReplayer.
    
    For each strategy + parameter combination:
    1. Create a fresh strategy instance
    2. Create a SessionReplayer with config overrides
    3. Run the replay
    4. Collect results and trade records
    """
    
    def __init__(self, session_path: Path, simulate_resolution: bool = True,
                 leader_capital: Decimal = Decimal("800")):
        self.session_path = Path(session_path)
        self.simulate_resolution = simulate_resolution
        self.leader_capital = leader_capital
        self.results: List[OptimizationResult] = []
    
    def run_strategy(self, strategy_name: str, config_overrides: Dict[str, Any] = None,
                     collect_trades: bool = False) -> ReplayResult:
        """Run a single strategy with optional config overrides.
        
        Returns ReplayResult with trades if collect_trades=True
        """
        # Apply leader capital override
        overrides = dict(config_overrides or {})
        overrides.setdefault("scaling.leader_estimated_capital", str(self.leader_capital))
        
        strategy = get_strategy(strategy_name)
        replayer = SessionReplayer(self.session_path, strategy, overrides)
        replayer.load()
        result = replayer.run(
            simulate_resolution=self.simulate_resolution,
            collect_trades=collect_trades
        )
        
        # Add open positions info from strategy
        if collect_trades and hasattr(strategy, 'portfolio'):
            positions = strategy.portfolio.get_positions()
            for token_id, pos in positions.items():
                if pos.shares > 0:
                    final_price = replayer.final_prices.get(token_id)
                    current_value = pos.shares * (final_price.bid if final_price else Decimal("0"))
                    # Store position info in result (we'll use trades list for open positions too)
                    pass  # Already captured in result
        
        return result
    
    def run_grid(self, strategy_name: str, param_grid: Dict[str, List[Any]] = None,
                 max_configs: int = 50) -> List[OptimizationResult]:
        """Run grid search for a strategy."""
        if not param_grid:
            # No params to vary - just run once with defaults
            result = self.run_strategy(strategy_name, collect_trades=True)
            opt = OptimizationResult(
                config_name=strategy_name,
                strategy_name=strategy_name,
                params={},
                result=result,
            )
            return [opt]
        
        # Generate all combinations
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        
        results = []
        for i, combo in enumerate(itertools.product(*param_values)):
            if i >= max_configs:
                break
            
            overrides = {k: str(v) for k, v in zip(param_names, combo)}
            config_name = f"{strategy_name}_{i+1}"
            
            result = self.run_strategy(strategy_name, overrides, collect_trades=(i == 0))
            opt = OptimizationResult(
                config_name=config_name,
                strategy_name=strategy_name,
                params=overrides,
                result=result,
            )
            results.append(opt)
        
        return sorted(results, key=lambda x: x.score, reverse=True)
    
    def run_all_strategies(self, collect_trades: bool = True) -> Dict[str, List[OptimizationResult]]:
        """Run all available strategies with default params."""
        all_results = {}
        
        for strategy_name in list_strategies():
            print(f"  Running {strategy_name}...")
            result = self.run_strategy(strategy_name, collect_trades=collect_trades)
            opt = OptimizationResult(
                config_name=strategy_name,
                strategy_name=strategy_name,
                params={},
                result=result,
            )
            all_results[strategy_name] = [opt]
        
        return all_results
    
    def print_summary(self, all_results: Dict[str, List[OptimizationResult]]) -> None:
        """Print comparison table."""
        print()
        
        # Session info from first result
        first_result = None
        for results in all_results.values():
            if results:
                first_result = results[0].result
                break
        
        if first_result:
            print("=" * 85)
            print("  SESSION INFO")
            print("=" * 85)
            print(f"  Session ID: {first_result.session_id}")
            if first_result.session_start:
                print(f"  Started: {first_result.session_start.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            if first_result.session_end:
                print(f"  Ended: {first_result.session_end.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            if first_result.session_duration_minutes > 0:
                hours = int(first_result.session_duration_minutes // 60)
                mins = int(first_result.session_duration_minutes % 60)
                print(f"  Duration: {hours}h {mins}m ({first_result.session_duration_minutes:.1f} minutes)")
            print(f"  Events: {first_result.events_processed} trades, {first_result.events_dropped_duplicates} duplicates filtered")
            print()
        
        print("=" * 85)
        print("  STRATEGY COMPARISON")
        print("=" * 85)
        header = f"{'Strategy':<22} {'Buys':<6} {'Sells':<6} {'Skips':<7} {'PnL':<10} {'Realized':<10}"
        print(header)
        print("-" * 85)
        
        all_flat = []
        for name, results in all_results.items():
            if results:
                best = results[0]
                all_flat.append(best)
                r = best.result
                row = f"{name:<22} {r.buys_executed:<6} {r.sells_executed:<6} {r.skips:<7} "
                row += f"${r.total_pnl:>+7.2f}  ${r.realized_pnl:>+7.2f}"
                print(row)
        
        print("=" * 85)
        
        # Show market resolution results if available
        if any(r.result.win_count + r.result.loss_count > 0 for r in all_flat):
            print("\n  MARKET RESOLUTION SIMULATION (final prices → win/lose)")
            print("-" * 85)
            header2 = f"{'Strategy':<22} {'Resolved PnL':<12} {'Wins':<6} {'Losses':<6} {'Win%':<8}"
            print(header2)
            print("-" * 85)
            for opt in all_flat:
                r = opt.result
                total = r.win_count + r.loss_count
                win_pct = (r.win_count / total * 100) if total > 0 else 0
                row = f"{opt.strategy_name:<22} ${r.resolved_pnl:>+9.2f}  {r.win_count:<6} {r.loss_count:<6} {win_pct:>5.1f}%"
                print(row)
            print("=" * 85)
        
        # Best overall
        all_flat.sort(key=lambda x: x.result.resolved_pnl if x.result.resolved_pnl else x.score, reverse=True)
        if all_flat:
            best = all_flat[0]
            print(f"\n🏆 Best overall: {best.strategy_name}")
            print(f"   Total PnL: ${best.result.total_pnl:.2f}")
            if best.result.resolved_pnl:
                print(f"   Resolved PnL: ${best.result.resolved_pnl:.2f}")
                print(f"   Wins: {best.result.win_count}, Losses: {best.result.loss_count}")
            
            # Show follow metrics for best strategy
            if best.result.follow_metrics:
                self._print_follow_metrics(best.result.follow_metrics)
    
    def _print_follow_metrics(self, metrics: Dict[str, Any]) -> None:
        """Print follow quality metrics."""
        summary = metrics.get("summary", {})
        
        print()
        print("=" * 70)
        print("  FOLLOW QUALITY METRICS")
        print("=" * 70)
        print()
        print(f"  Leader trades: {summary.get('leader_trades', 0)}")
        print(f"  Our trades:    {summary.get('our_trades', 0)}")
        print()
        print(f"  Follow rate:   {summary.get('follow_rate_pct', 0):.1f}%")
        print(f"  Same-sign %:   {summary.get('same_sign_pct', 0):.1f}%")
        if summary.get('avg_reaction_time_ms'):
            print(f"  Avg reaction:  {summary['avg_reaction_time_ms']:.0f}ms")
        print()
        print(f"  Best correlation: {summary.get('best_correlation', 0):.3f} at {summary.get('best_lag_seconds', 0)}s lag")
        
        lag_correlations = metrics.get("lag_correlations", {})
        if lag_correlations:
            print()
            print("  Correlation by lag:")
            for lag, corr in lag_correlations.items():
                bar = "█" * int(abs(corr) * 20)
                sign = "+" if corr >= 0 else "-"
                print(f"    {lag}: {sign}{abs(corr):.3f} {bar}")
        
        fb = metrics.get("follow_breakdown", {})
        if fb:
            print()
            print("  Follow breakdown:")
            print(f"    Followed: {fb.get('followed', 0)}")
            print(f"    Skipped:  {fb.get('skipped', 0)}")
            print(f"    Opposite: {fb.get('opposite', 0)}")
        print("=" * 70)
    
    def print_trades(self, result: ReplayResult) -> None:
        """Print detailed trade list for a strategy result."""
        print()
        print("=" * 120)
        print(f"  TRADE ANALYSIS: {result.strategy_name}")
        print("=" * 120)
        
        # First show summary stats
        print(f"  Buys: {result.buys_executed}  |  Sells: {result.sells_executed}  |  Skips: {result.skips}")
        print(f"  Buy $: ${result.buy_dollars:.2f}  |  Sell $: ${result.sell_dollars:.2f}")
        print(f"  PnL: ${result.total_pnl:.2f} (Realized: ${result.realized_pnl:.2f}, Unrealized: ${result.unrealized_pnl:.2f})")
        print()
        
        # Show skip reasons
        if result.skip_reasons:
            print("  Skip Reasons:")
            for reason, count in sorted(result.skip_reasons.items(), key=lambda x: -x[1]):
                print(f"    {reason}: {count}")
            print()
        
        # Show all executed trades
        if result.trades:
            print("  EXECUTED TRADES:")
            print("-" * 120)
            header = f"  {'#':<4} {'Action':<6} {'Side':<5} {'Token (short)':<18} {'Leader$':<10} {'Our$':<10} {'Shares':<10} {'Price':<8}"
            print(header)
            print("-" * 120)
            
            for t in result.trades:
                short_token = t.token_id[:15] + "..." if len(t.token_id) > 18 else t.token_id
                row = f"  {t.sequence:<4} {t.action:<6} {t.side:<5} {short_token:<18} "
                row += f"${float(t.leader_dollars):<9.2f} ${float(t.our_dollars):<9.2f} "
                row += f"{float(t.our_shares):<10.2f} ${float(t.our_price):<7.4f}"
                print(row)
            
            print("-" * 120)
            print(f"  Total: {len(result.trades)} trades executed")
            print("=" * 120)


def run_optimization(session_path: str, simulate_resolution: bool = True,
                     leader_capital: float = 800.0, print_trades: bool = True) -> Dict[str, Any]:
    """Main entry point."""
    optimizer = SimpleOptimizer(
        Path(session_path), 
        simulate_resolution=simulate_resolution,
        leader_capital=Decimal(str(leader_capital))
    )
    
    print(f"\nOptimizing session: {session_path}")
    print(f"Leader capital: ${leader_capital}")
    print(f"Available strategies: {list_strategies()}")
    print(f"Market resolution simulation: {'ON' if simulate_resolution else 'OFF'}")
    print()
    
    all_results = optimizer.run_all_strategies(collect_trades=True)
    optimizer.print_summary(all_results)
    
    # Print trades for best strategy
    if print_trades:
        all_flat = [r[0] for r in all_results.values() if r]
        all_flat.sort(key=lambda x: x.result.resolved_pnl if x.result.resolved_pnl else x.score, reverse=True)
        if all_flat:
            optimizer.print_trades(all_flat[0].result)
    
    return all_results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Strategy Optimizer for Polymarket session replay")
    parser.add_argument("session_path", help="Path to session .jsonl file")
    parser.add_argument("--leader-capital", type=float, default=800.0, help="Estimated leader capital")
    parser.add_argument("--no-resolution", action="store_true", help="Disable market resolution simulation")
    parser.add_argument("--no-trades", action="store_true", help="Don't print individual trades")
    parser.add_argument("--detailed", action="store_true", help="Show detailed output")
    
    args = parser.parse_args()
    run_optimization(
        args.session_path,
        simulate_resolution=not args.no_resolution,
        leader_capital=args.leader_capital,
        print_trades=not args.no_trades
    )
