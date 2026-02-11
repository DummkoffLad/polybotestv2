"""Test all strategies on your session data."""
from src.simulation.optimizer import SimpleOptimizer
from pathlib import Path

SESSION = Path("data/sessions/session_20260203_055651.jsonl")

STRATEGIES = [
    "mirror",
    "momentum",
    "conservative",
    "aggressive",
    "spread_aware",
    "velocity",
    "price_level",
    "hybrid_conservative",
    "simple_follow",
]

print("=" * 60)
print("TESTING ALL STRATEGIES ON YOUR 12-HOUR SESSION")
print("=" * 60)

optimizer = SimpleOptimizer(session_path=SESSION, simulate_resolution=True)

results = []
for strategy in STRATEGIES:
    try:
        result = optimizer.run_strategy(strategy_name=strategy)
        pnl = float(result.total_pnl)
        results.append((strategy, pnl, result.buy_count, result.sell_count, result.skip_count))
        print(f"{strategy:25} | PnL: ${pnl:>8.2f} | Buys: {result.buy_count:>4} | Sells: {result.sell_count:>4} | Skips: {result.skip_count:>4}")
    except Exception as e:
        print(f"{strategy:25} | ERROR: {e}")

print("=" * 60)
print("\nRANKED BY PROFIT:")
print("-" * 40)
for strategy, pnl, buys, sells, skips in sorted(results, key=lambda x: x[1], reverse=True):
    print(f"${pnl:>8.2f}  {strategy}")
