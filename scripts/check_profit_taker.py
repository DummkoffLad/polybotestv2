"""Test profit_taker vs conservative on unique sessions only."""
from pathlib import Path
from src.strategies import get_strategy
from src.framework.replay import SessionReplayer

# Only main sessions (exclude filtered which are subsets)
sessions = [
    Path('data/sessions/2026-02-03/05-56.jsonl'),
    Path('data/sessions/2026-02-04/02-35.jsonl'),
]

print(f"Testing on {len(sessions)} unique sessions\n")

print("Session | Conservative | Profit Taker | Winner")
print("-" * 60)

cons_total = 0
prof_total = 0

for session in sessions:
    # Conservative
    cons = get_strategy('conservative_mirror')
    cons_replay = SessionReplayer(session, cons)
    cons_replay.load()
    cons_result = cons_replay.run(track_analysis=False)

    # Profit taker
    prof = get_strategy('profit_taker')
    prof_replay = SessionReplayer(session, prof)
    prof_replay.load()
    prof_result = prof_replay.run(track_analysis=False)

    cons_pnl = float(cons_result.total_pnl)
    prof_pnl = float(prof_result.total_pnl)
    cons_total += cons_pnl
    prof_total += prof_pnl

    winner = "CONS" if cons_pnl > prof_pnl else "PROF" if prof_pnl > cons_pnl else "TIE"
    print(f"{session.name:20} | ${cons_pnl:>+8.2f} | ${prof_pnl:>+8.2f} | {winner}")

print("-" * 60)
print(f"{'TOTAL':20} | ${cons_total:>+8.2f} | ${prof_total:>+8.2f} | {'CONS' if cons_total > prof_total else 'PROF'}")

# Also test flat 20% for comparison
import src.strategies.profit_taker.strategy as pt_mod
from decimal import Decimal

# Override to flat 20%
pt_mod.PROFIT_TARGET_LOW_PRICE = Decimal("20")
pt_mod.PROFIT_TARGET_MID_PRICE = Decimal("20")
pt_mod.PROFIT_TARGET_HIGH_PRICE = Decimal("20")

print("\n--- With flat 20% target ---")
flat_total = 0
for session in sessions:
    prof = get_strategy('profit_taker')
    prof_replay = SessionReplayer(session, prof)
    prof_replay.load()
    prof_result = prof_replay.run(track_analysis=False)
    flat_total += float(prof_result.total_pnl)
    print(f"{session.name:20} | ${float(prof_result.total_pnl):>+8.2f}")

print(f"{'TOTAL (20% flat)':20} | ${flat_total:>+8.2f}")
