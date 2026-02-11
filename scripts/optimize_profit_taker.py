"""Optimize profit_taker dynamic pricing parameters.

Tests different profit target thresholds at different price levels.
The hypothesis: low-price entries have more room to run (higher targets),
high-price entries are closer to ceiling (lower targets).
"""
import itertools
from pathlib import Path
from decimal import Decimal
from dataclasses import dataclass
from typing import List, Tuple
import src.strategies.profit_taker.strategy as pt_mod
from src.strategies import get_strategy
from src.framework.replay import SessionReplayer

# Only use main sessions (not filtered subsets)
SESSIONS = [
    Path('data/sessions/2026-02-03/05-56.jsonl'),
    Path('data/sessions/2026-02-04/02-35.jsonl'),
]


@dataclass
class ParamResult:
    low_target: int    # Target % for prices < 0.30
    mid_target: int    # Target % for prices 0.30-0.60
    high_target: int   # Target % for prices > 0.60
    total_pnl: float
    session_pnls: List[float]


def patch_profit_taker_dynamic():
    """Patch profit_taker to use dynamic targets."""
    # Store original function
    original_check = pt_mod.ProfitTakerStrategy._check_profit_target

    def dynamic_check(self, token_id: str, current_bid: Decimal) -> bool:
        if token_id not in self.our_entries:
            return False
        entry_price = self.our_entries[token_id]
        if entry_price <= 0:
            return False

        # Dynamic target based on entry price
        if entry_price < Decimal("0.30"):
            target = pt_mod.PROFIT_TARGET_LOW
        elif entry_price < Decimal("0.60"):
            target = pt_mod.PROFIT_TARGET_MID
        else:
            target = pt_mod.PROFIT_TARGET_HIGH

        profit_pct = ((current_bid - entry_price) / entry_price) * 100
        return profit_pct >= target

    pt_mod.ProfitTakerStrategy._check_profit_target = dynamic_check


def test_params(low: int, mid: int, high: int) -> ParamResult:
    """Test a specific parameter combination."""
    pt_mod.PROFIT_TARGET_LOW = Decimal(str(low))
    pt_mod.PROFIT_TARGET_MID = Decimal(str(mid))
    pt_mod.PROFIT_TARGET_HIGH = Decimal(str(high))

    session_pnls = []
    for session in SESSIONS:
        strat = get_strategy('profit_taker')
        replayer = SessionReplayer(session, strat)
        replayer.load()
        result = replayer.run(track_analysis=False)
        session_pnls.append(float(result.total_pnl))

    return ParamResult(
        low_target=low,
        mid_target=mid,
        high_target=high,
        total_pnl=sum(session_pnls),
        session_pnls=session_pnls
    )


def run_grid_search():
    """Run grid search over parameter space."""
    patch_profit_taker_dynamic()

    # Parameter space
    # Low price targets: 15-40% (lots of room to run)
    # Mid price targets: 10-25%
    # High price targets: 5-15% (less room, take profits earlier)
    low_targets = [15, 20, 25, 30, 35, 40]
    mid_targets = [10, 15, 20, 25]
    high_targets = [5, 8, 10, 12, 15]

    print(f"Testing {len(low_targets) * len(mid_targets) * len(high_targets)} parameter combinations...")
    print(f"Sessions: {[s.name for s in SESSIONS]}\n")

    results = []
    total = len(low_targets) * len(mid_targets) * len(high_targets)
    i = 0

    for low in low_targets:
        for mid in mid_targets:
            for high in high_targets:
                i += 1
                if i % 20 == 0:
                    print(f"  Progress: {i}/{total}")
                result = test_params(low, mid, high)
                results.append(result)

    # Sort by total PnL
    results.sort(key=lambda x: x.total_pnl, reverse=True)

    # Print top 20
    print(f"\n{'='*70}")
    print(f"  TOP 20 PARAMETER COMBINATIONS")
    print(f"{'='*70}")
    print(f"{'Low%':>6} {'Mid%':>6} {'High%':>6} | {'Total':>10} | Session PnLs")
    print("-" * 70)

    for r in results[:20]:
        session_str = " | ".join(f"${p:+.2f}" for p in r.session_pnls)
        print(f"{r.low_target:>6} {r.mid_target:>6} {r.high_target:>6} | ${r.total_pnl:>+8.2f} | {session_str}")

    # Also show worst 5 for comparison
    print(f"\n{'='*70}")
    print(f"  WORST 5 (for comparison)")
    print(f"{'='*70}")
    for r in results[-5:]:
        session_str = " | ".join(f"${p:+.2f}" for p in r.session_pnls)
        print(f"{r.low_target:>6} {r.mid_target:>6} {r.high_target:>6} | ${r.total_pnl:>+8.2f} | {session_str}")

    # Conservative baseline
    print(f"\n{'='*70}")
    print(f"  BASELINE (conservative_mirror)")
    print(f"{'='*70}")
    cons_total = 0
    for session in SESSIONS:
        cons = get_strategy('conservative_mirror')
        r = SessionReplayer(session, cons)
        r.load()
        result = r.run(track_analysis=False)
        cons_total += float(result.total_pnl)
        print(f"  {session.name}: ${float(result.total_pnl):+.2f}")
    print(f"  Total: ${cons_total:+.2f}")

    return results


def run_flat_comparison():
    """Compare flat targets for reference."""
    # Test flat targets (same across all price levels)
    print(f"\n{'='*70}")
    print(f"  FLAT TARGET COMPARISON")
    print(f"{'='*70}")

    for target in [10, 15, 20, 25, 30]:
        pt_mod.PROFIT_TARGET_PCT = Decimal(str(target))
        total = 0
        for session in SESSIONS:
            strat = get_strategy('profit_taker')
            replayer = SessionReplayer(session, strat)
            replayer.load()
            result = replayer.run(track_analysis=False)
            total += float(result.total_pnl)
        print(f"  Flat {target}%: ${total:+.2f}")


if __name__ == "__main__":
    run_flat_comparison()
    print()
    run_grid_search()
