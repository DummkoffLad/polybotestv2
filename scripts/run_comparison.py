"""Run full strategy comparison with Phase 7 tools.

Generates:
- Equity curve comparison (HTML)
- Metrics comparison table
- QuantStats tear sheets per strategy
- Decision matrix (HTML)

Usage:
    python run_comparison.py data/sessions/2026-02-03/05-56.jsonl
    python run_comparison.py data/sessions/2026-02-03/
    python run_comparison.py data/sessions/
"""
import argparse
from pathlib import Path
from decimal import Decimal

from src.comparison import (
    StrategyComparator,
    create_equity_comparison,
    save_equity_html,
    create_metrics_table,
    create_decision_matrix,
    generate_tear_sheets,
)
from src.strategies.base import get_strategy, list_strategies
from src.simulation.full_optimizer import find_sessions


def run_comparison(session_path: str, output_dir: str = "data/reports"):
    """Run full comparison on session(s)."""
    path = Path(session_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    # Find sessions
    sessions = find_sessions(path)
    if not sessions:
        print(f"No sessions found at: {path}")
        return

    print(f"\n{'='*60}")
    print(f"  PHASE 7 STRATEGY COMPARISON")
    print(f"{'='*60}")
    print(f"Sessions: {len(sessions)}")
    for s in sessions:
        print(f"  - {s.name}")
    print(f"Output: {output}")
    print()

    # Get all strategies
    strategy_names = list_strategies()
    print(f"Strategies: {strategy_names}")
    print()

    # Run comparison on each session
    for session_file in sessions:
        print(f"\n--- Processing: {session_file.name} ---")

        # Create strategy instances
        strategies = [get_strategy(name) for name in strategy_names]

        # Run comparator
        comparator = StrategyComparator(session_file, strategies)
        result = comparator.run_comparison()

        session_id = session_file.stem  # e.g., "05-56"
        day_id = session_file.parent.name  # e.g., "2026-02-03"
        prefix = f"{day_id}_{session_id}"

        # 1. Equity curves
        print("  Generating equity curves...")
        # Convert ComparisonResult to dict format expected by visualizer
        equity_data = {
            sr.strategy_name: sr.equity_df
            for sr in result.strategy_results
            if not sr.equity_df.empty and len(sr.equity_df) >= 2
        }
        if equity_data:
            fig = create_equity_comparison(equity_data)
            equity_path = output / f"{prefix}_equity.html"
            save_equity_html(fig, equity_path)
            print(f"    Saved: {equity_path}")
        else:
            print("    Skipped (insufficient equity data)")

        # 2. Metrics table
        print("  Calculating metrics...")
        # create_metrics_table expects ComparisonResult directly
        styled_table = create_metrics_table(result)
        # Get the underlying DataFrame for display and CSV
        table_df = styled_table.data
        print("\n  METRICS TABLE:")
        print(table_df.to_string())
        metrics_path = output / f"{prefix}_metrics.csv"
        table_df.to_csv(metrics_path)
        print(f"\n    Saved: {metrics_path}")

        # 3. Decision matrix
        print("  Creating decision matrix...")
        styled_matrix = create_decision_matrix(result)
        matrix_html = styled_matrix.to_html()
        matrix_path = output / f"{prefix}_decisions.html"
        with open(matrix_path, "w", encoding="utf-8") as f:
            f.write(matrix_html)
        print(f"    Saved: {matrix_path}")

        # 4. Tear sheets
        print("  Generating tear sheets...")
        tear_dir = output / f"{prefix}_tearsheets"
        tear_paths = generate_tear_sheets(result, tear_dir)
        for tp in tear_paths:
            print(f"    Saved: {tp}")

        # Summary
        print(f"\n  Results for {prefix}:")
        for sr in result.strategy_results:
            pnl = sr.replay_result.total_pnl
            buys = sr.replay_result.buys_executed
            sells = sr.replay_result.sells_executed
            print(f"    {sr.strategy_name:<25} PnL: ${pnl:>+8.2f}  ({buys} buys, {sells} sells)")

    print(f"\n{'='*60}")
    print(f"  COMPARISON COMPLETE")
    print(f"{'='*60}")
    print(f"Reports saved to: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Phase 7 strategy comparison")
    parser.add_argument("session_path",
                        help="Path to session file or folder")
    parser.add_argument("--output", "-o", default="data/reports",
                        help="Output directory for reports")

    args = parser.parse_args()
    run_comparison(args.session_path, args.output)
