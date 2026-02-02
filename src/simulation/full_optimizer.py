"""
Full Strategy Optimizer - Tests all strategies with all execution mode variations.

For each strategy, tests with different execution mode assumptions:
1. MARKET - Immediate fills at ask/bid, higher spread cost (default 2.5%)
2. LIMIT_PASSIVE - Conservative fills, lower cost but reduced fill rate
3. LIMIT_AGGRESSIVE - Moderate fills, crosses spread slightly

The "execution mode" affects the simulation by adjusting:
- spread_cost_pct: How much spread cost to assume
- slippage_cost_pct: Slippage assumption  
- fill_rate_pct: What % of limit orders actually fill (passive only)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..framework.replay import SessionReplayer, ReplayResult
from ..strategies.base import get_strategy, list_strategies
from ..execution.hybrid import ExecutionMode


@dataclass
class ExecutionModeConfig:
    """Config overrides that simulate an execution mode."""
    name: str
    mode: ExecutionMode
    spread_cost_pct: str
    slippage_cost_pct: str
    # For limit orders, we'd apply a fill rate penalty in a real sim
    # Here we just adjust costs to simulate the effect
    description: str


# Execution mode configurations
EXECUTION_MODES = {
    ExecutionMode.MARKET: ExecutionModeConfig(
        name="MARKET",
        mode=ExecutionMode.MARKET,
        spread_cost_pct="2.5",  # Full spread cost
        slippage_cost_pct="1.0",  # Some slippage on market orders
        description="Immediate fills, higher cost"
    ),
    ExecutionMode.LIMIT_PASSIVE: ExecutionModeConfig(
        name="LIMIT_PASSIVE",
        mode=ExecutionMode.LIMIT_PASSIVE,
        spread_cost_pct="0.5",  # Minimal spread (we set the price)
        slippage_cost_pct="0.2",  # Very little slippage
        description="Conservative limit orders, lower cost but some orders don't fill"
    ),
    ExecutionMode.LIMIT_AGGRESSIVE: ExecutionModeConfig(
        name="LIMIT_AGGRESSIVE",
        mode=ExecutionMode.LIMIT_AGGRESSIVE,
        spread_cost_pct="1.2",  # Moderate spread (crossing slightly)
        slippage_cost_pct="0.5",  # Moderate slippage
        description="Aggressive limits, cross spread for better fills"
    ),
}


@dataclass
class FullOptResult:
    """Result for one strategy + execution mode combination."""
    strategy_name: str
    execution_mode: ExecutionMode
    result: ReplayResult
    config_overrides: Dict[str, str]
    
    @property
    def total_pnl(self) -> Decimal:
        return self.result.total_pnl
    
    @property
    def resolved_pnl(self) -> Decimal:
        return self.result.resolved_pnl or self.total_pnl


@dataclass 
class StrategyModeResults:
    """All execution mode results for one strategy."""
    strategy_name: str
    results: List[FullOptResult] = field(default_factory=list)
    
    @property
    def best_result(self) -> Optional[FullOptResult]:
        """Get the best performing execution mode."""
        if not self.results:
            return None
        return max(self.results, key=lambda r: r.resolved_pnl)
    
    @property
    def worst_result(self) -> Optional[FullOptResult]:
        """Get the worst performing execution mode."""
        if not self.results:
            return None
        return min(self.results, key=lambda r: r.resolved_pnl)


class FullOptimizer:
    """
    Tests all strategies with all execution mode variations.
    
    Usage:
        optimizer = FullOptimizer(session_path)
        results = optimizer.run_all()
        optimizer.print_summary(results)
    """
    
    def __init__(self, session_path: Path, leader_capital: Decimal = Decimal("800")):
        self.session_path = Path(session_path)
        self.leader_capital = leader_capital
    
    def run_single(self, strategy_name: str, exec_mode: ExecutionMode,
                   extra_overrides: Dict[str, str] = None) -> FullOptResult:
        """Run a single strategy with a specific execution mode."""
        mode_config = EXECUTION_MODES[exec_mode]
        
        # Build config overrides
        overrides = {
            "scaling.leader_estimated_capital": str(self.leader_capital),
            "simulation.spread_cost_pct": mode_config.spread_cost_pct,
            "simulation.slippage_cost_pct": mode_config.slippage_cost_pct,
        }
        if extra_overrides:
            overrides.update(extra_overrides)
        
        # Run replay
        strategy = get_strategy(strategy_name)
        replayer = SessionReplayer(self.session_path, strategy, overrides)
        replayer.load()
        result = replayer.run(simulate_resolution=True, collect_trades=True)
        
        return FullOptResult(
            strategy_name=strategy_name,
            execution_mode=exec_mode,
            result=result,
            config_overrides=overrides
        )
    
    def run_strategy_all_modes(self, strategy_name: str) -> StrategyModeResults:
        """Run a single strategy with all execution modes."""
        mode_results = StrategyModeResults(strategy_name=strategy_name)
        
        for mode in ExecutionMode:
            result = self.run_single(strategy_name, mode)
            mode_results.results.append(result)
        
        return mode_results
    
    def run_all(self) -> Dict[str, StrategyModeResults]:
        """Run all strategies with all execution modes."""
        all_results: Dict[str, StrategyModeResults] = {}
        strategies = list_strategies()
        
        print(f"\nRunning {len(strategies)} strategies × {len(ExecutionMode)} execution modes = {len(strategies) * len(ExecutionMode)} combinations\n")
        
        for i, strategy_name in enumerate(strategies, 1):
            print(f"[{i}/{len(strategies)}] Testing {strategy_name}...")
            for mode in ExecutionMode:
                result = self.run_single(strategy_name, mode)
                if strategy_name not in all_results:
                    all_results[strategy_name] = StrategyModeResults(strategy_name=strategy_name)
                all_results[strategy_name].results.append(result)
        
        return all_results
    
    def print_summary(self, all_results: Dict[str, StrategyModeResults]) -> None:
        """Print comprehensive summary table."""
        print()
        print("=" * 100)
        print("  FULL STRATEGY × EXECUTION MODE COMPARISON")
        print("=" * 100)
        print()
        
        # First, show all combinations
        header = f"{'Strategy':<25} {'Mode':<18} {'Buys':<6} {'Sells':<6} {'PnL':<12} {'Resolved':<12}"
        print(header)
        print("-" * 100)
        
        all_flat: List[FullOptResult] = []
        for strategy_name in sorted(all_results.keys()):
            mode_results = all_results[strategy_name]
            for opt in sorted(mode_results.results, key=lambda x: x.execution_mode.value):
                all_flat.append(opt)
                r = opt.result
                mode_str = opt.execution_mode.value
                row = f"{strategy_name:<25} {mode_str:<18} "
                row += f"{r.buys_executed:<6} {r.sells_executed:<6} "
                row += f"${r.total_pnl:>+9.2f}   ${r.resolved_pnl:>+9.2f}"
                print(row)
        
        print("-" * 100)
        
        # Now show best mode per strategy
        print()
        print("=" * 100)
        print("  BEST EXECUTION MODE PER STRATEGY")
        print("=" * 100)
        header2 = f"{'Strategy':<25} {'Best Mode':<18} {'PnL':<12} {'vs Worst':<12}"
        print(header2)
        print("-" * 100)
        
        strategy_bests: List[Tuple[str, FullOptResult, Decimal]] = []
        for strategy_name in sorted(all_results.keys()):
            mode_results = all_results[strategy_name]
            best = mode_results.best_result
            worst = mode_results.worst_result
            if best:
                improvement = best.resolved_pnl - (worst.resolved_pnl if worst else Decimal("0"))
                strategy_bests.append((strategy_name, best, improvement))
                row = f"{strategy_name:<25} {best.execution_mode.value:<18} "
                row += f"${best.resolved_pnl:>+9.2f}   ${improvement:>+9.2f}"
                print(row)
        
        print("-" * 100)
        
        # Overall best
        if all_flat:
            all_flat.sort(key=lambda x: x.resolved_pnl, reverse=True)
            best_overall = all_flat[0]
            print()
            print("=" * 100)
            print(f"🏆 OVERALL BEST: {best_overall.strategy_name} + {best_overall.execution_mode.value}")
            print(f"   Resolved PnL: ${best_overall.resolved_pnl:.2f}")
            print(f"   Total PnL: ${best_overall.total_pnl:.2f}")
            print(f"   Trades: {best_overall.result.buys_executed} buys, {best_overall.result.sells_executed} sells")
            
            # Show top 5
            print()
            print("  TOP 5 COMBINATIONS:")
            for i, opt in enumerate(all_flat[:5], 1):
                print(f"    {i}. {opt.strategy_name:<22} {opt.execution_mode.value:<15} ${opt.resolved_pnl:>+9.2f}")
            
            # Show bottom 5
            print()
            print("  BOTTOM 5 COMBINATIONS:")
            for i, opt in enumerate(all_flat[-5:], 1):
                print(f"    {len(all_flat) - 5 + i}. {opt.strategy_name:<22} {opt.execution_mode.value:<15} ${opt.resolved_pnl:>+9.2f}")
            
            print("=" * 100)
        
        return all_flat


def run_full_optimization(session_path: str, leader_capital: float = 800.0) -> Dict[str, StrategyModeResults]:
    """Main entry point for full optimization."""
    optimizer = FullOptimizer(
        Path(session_path),
        leader_capital=Decimal(str(leader_capital))
    )
    
    print(f"\n{'='*60}")
    print(f"  FULL STRATEGY OPTIMIZATION")
    print(f"{'='*60}")
    print(f"Session: {session_path}")
    print(f"Leader capital: ${leader_capital}")
    print(f"Strategies: {list_strategies()}")
    print(f"Execution modes: {[m.value for m in ExecutionMode]}")
    
    all_results = optimizer.run_all()
    optimizer.print_summary(all_results)
    
    return all_results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Full Strategy Optimizer")
    parser.add_argument("session_path", help="Path to session .jsonl file")
    parser.add_argument("--leader-capital", type=float, default=800.0)
    
    args = parser.parse_args()
    run_full_optimization(args.session_path, args.leader_capital)
