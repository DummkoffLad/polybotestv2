"""Deep analysis: Where does money come from? Where do we lose it?

With corrected bid=0 resolution, our PnL dropped from +$477 to +$97.
This analysis breaks down exactly where gains and losses happen.
"""
import sys
import json
import statistics
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer

CONFIG_OVERRIDES = {
    "scaling.our_capital": 50,
    "scaling.hourly_budget": 45,
    "scaling.k_factor": 1,
    "scaling.leader_estimated_capital": 900,
}


def run_hour_detailed(hour_file):
    """Run one hour and track every position's lifecycle."""
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return None
    if count == 0:
        return None

    config = replayer._merge_config()
    strategy.initialize(StrategyConfig.from_dict(config))
    strategy.on_session_start()

    # Track per-token lifecycle
    token_buys = defaultdict(lambda: {"shares": Decimal("0"), "cost": Decimal("0"), "count": 0,
                                       "first_min": 99, "last_buy_min": 0, "prices": []})
    token_sells = defaultdict(lambda: {"shares": Decimal("0"), "revenue": Decimal("0"), "count": 0,
                                        "last_sell_min": 0})

    last_hour = None
    # Also build snapshot timeline for time-exit analysis
    snapshot_prices_at = {}  # minute -> {token_id -> bid}

    for snapshot in replayer.loader._price_snapshots:
        minute = snapshot.timestamp.minute
        if minute not in snapshot_prices_at:
            snapshot_prices_at[minute] = {}
        for tid, ps in snapshot.prices.items():
            if ps.bid is not None:
                snapshot_prices_at[minute][tid] = float(ps.bid)

    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour
        minute = event.trade.timestamp.minute

        decision = strategy.on_event(event)
        if decision.action == DecisionAction.BUY:
            strategy.on_fill(event, decision)
            tid = event.trade.token_id
            token_buys[tid]["shares"] += decision.shares
            token_buys[tid]["cost"] += decision.dollars
            token_buys[tid]["count"] += 1
            token_buys[tid]["first_min"] = min(token_buys[tid]["first_min"], minute)
            token_buys[tid]["last_buy_min"] = max(token_buys[tid]["last_buy_min"], minute)
            token_buys[tid]["prices"].append(float(decision.price))
        elif decision.action == DecisionAction.SELL:
            strategy.on_fill(event, decision)
            tid = event.trade.token_id
            token_sells[tid]["shares"] += decision.shares
            token_sells[tid]["revenue"] += decision.dollars
            token_sells[tid]["count"] += 1
            token_sells[tid]["last_sell_min"] = max(token_sells[tid]["last_sell_min"], minute)

    # Resolution
    end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour else {}

    positions = []
    for tid in set(list(token_buys.keys()) + list(token_sells.keys())):
        buy = token_buys.get(tid, {"shares": Decimal("0"), "cost": Decimal("0"), "count": 0,
                                    "first_min": 0, "last_buy_min": 0, "prices": []})
        sell = token_sells.get(tid, {"shares": Decimal("0"), "revenue": Decimal("0"), "count": 0,
                                      "last_sell_min": 0})
        if buy["shares"] <= 0:
            continue

        avg_entry = float(buy["cost"] / buy["shares"])
        net_shares = float(buy["shares"] - sell["shares"])
        mid_pnl = float(sell["revenue"]) - float(sell["shares"]) * avg_entry if sell["shares"] > 0 else 0

        # Resolution
        res_pnl = 0
        res_price = None
        end_bid = None
        if net_shares > 0.01:
            ps = end_prices.get(tid)
            if ps and ps.bid is not None:
                end_bid = float(ps.bid)
                won = end_bid >= 0.50
                res_price = 0.99 if won else 0.01
            else:
                won = avg_entry >= 0.50
                res_price = 0.99 if won else 0.01
                end_bid = avg_entry
            res_pnl = net_shares * (res_price - avg_entry)

        # What would we get at different exit times?
        exit_values = {}
        for exit_min in [40, 45, 50, 55, 57]:
            best_bid = None
            # Get closest snapshot at or before exit_min
            for m in range(exit_min, -1, -1):
                if m in snapshot_prices_at and tid in snapshot_prices_at[m]:
                    best_bid = snapshot_prices_at[m][tid]
                    break
            if best_bid is not None and net_shares > 0.01:
                exit_values[exit_min] = net_shares * (best_bid - avg_entry)
            else:
                exit_values[exit_min] = None

        positions.append({
            "token": tid[:12],
            "avg_entry": avg_entry,
            "total_cost": float(buy["cost"]),
            "buy_count": buy["count"],
            "sell_count": sell["count"],
            "mid_pnl": mid_pnl,
            "net_shares": net_shares,
            "end_bid": end_bid,
            "res_price": res_price,
            "res_pnl": res_pnl,
            "total_pnl": mid_pnl + res_pnl,
            "first_buy_min": buy["first_min"],
            "last_buy_min": buy["last_buy_min"],
            "held_to_res": net_shares > 0.01,
            "exit_values": exit_values,
        })

    total_pnl = sum(p["total_pnl"] for p in positions)
    return {
        "positions": positions,
        "total_pnl": total_pnl,
        "mid_pnl": sum(p["mid_pnl"] for p in positions),
        "res_pnl": sum(p["res_pnl"] for p in positions),
    }


def main():
    base = Path("data/sessions")
    all_positions = []
    hour_results = []

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            result = run_hour_detailed(hf)
            if result and result["positions"]:
                hour_results.append(result)
                all_positions.extend(result["positions"])

    if not all_positions:
        print("No data!")
        return

    # ===== SECTION 1: Overall PnL breakdown =====
    total_mid = sum(p["mid_pnl"] for p in all_positions)
    total_res = sum(p["res_pnl"] for p in all_positions)
    total_all = total_mid + total_res

    held = [p for p in all_positions if p["held_to_res"]]
    closed = [p for p in all_positions if not p["held_to_res"]]

    print("=" * 90)
    print(f"  DEEP PnL ANALYSIS ({len(all_positions)} positions across {len(hour_results)} hours)")
    print("=" * 90)

    print(f"\n  1. PnL BREAKDOWN:")
    print(f"     Mid-hour selling PnL:  ${total_mid:+.2f}")
    print(f"     Resolution PnL:        ${total_res:+.2f}")
    print(f"     TOTAL:                 ${total_all:+.2f}")
    print(f"     Positions closed mid-hour: {len(closed)} (${sum(p['total_pnl'] for p in closed):+.2f})")
    print(f"     Positions held to res:     {len(held)} (${sum(p['total_pnl'] for p in held):+.2f})")

    # ===== SECTION 2: Resolution outcomes =====
    res_wins = [p for p in held if p["res_price"] == 0.99]
    res_losses = [p for p in held if p["res_price"] == 0.01]
    print(f"\n  2. RESOLUTION OUTCOMES (positions held to resolution):")
    print(f"     Wins:  {len(res_wins)} positions, PnL ${sum(p['res_pnl'] for p in res_wins):+.2f}")
    if res_wins:
        print(f"       Avg gain/position: ${sum(p['res_pnl'] for p in res_wins)/len(res_wins):+.2f}")
    print(f"     Losses: {len(res_losses)} positions, PnL ${sum(p['res_pnl'] for p in res_losses):+.2f}")
    if res_losses:
        print(f"       Avg loss/position: ${sum(p['res_pnl'] for p in res_losses)/len(res_losses):+.2f}")

    # ===== SECTION 3: Entry price analysis =====
    print(f"\n  3. ENTRY PRICE vs OUTCOME:")
    price_brackets = [(0.45, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 0.97)]
    for lo, hi in price_brackets:
        bracket = [p for p in all_positions if lo <= p["avg_entry"] < hi]
        if not bracket:
            continue
        b_pnl = sum(p["total_pnl"] for p in bracket)
        b_mid = sum(p["mid_pnl"] for p in bracket)
        b_res = sum(p["res_pnl"] for p in bracket)
        wins = sum(1 for p in bracket if p["total_pnl"] > 0)
        losses = sum(1 for p in bracket if p["total_pnl"] < 0)
        wr = wins / max(1, wins + losses) * 100
        print(f"     ${lo:.2f}-${hi:.2f}: {len(bracket):>3} pos  PnL=${b_pnl:>+7.2f}  mid=${b_mid:>+7.2f} res=${b_res:>+7.2f}  WR={wr:.0f}%")

    # ===== SECTION 4: Entry minute analysis =====
    print(f"\n  4. ENTRY MINUTE vs OUTCOME:")
    min_brackets = [(0, 15), (15, 30), (30, 45), (45, 60)]
    for lo, hi in min_brackets:
        bracket = [p for p in all_positions if lo <= p["first_buy_min"] < hi]
        if not bracket:
            continue
        b_pnl = sum(p["total_pnl"] for p in bracket)
        wins = sum(1 for p in bracket if p["total_pnl"] > 0)
        losses = sum(1 for p in bracket if p["total_pnl"] < 0)
        wr = wins / max(1, wins + losses) * 100
        held_count = sum(1 for p in bracket if p["held_to_res"])
        print(f"     min {lo:>2}-{hi:>2}: {len(bracket):>3} pos  PnL=${b_pnl:>+7.2f}  WR={wr:.0f}%  held_to_res={held_count}")

    # ===== SECTION 5: Time-based exit potential =====
    print(f"\n  5. TIME-BASED EXIT POTENTIAL (for positions held to resolution):")
    print(f"     If we sold at minute X instead of resolution:")
    for exit_min in [40, 45, 50, 55, 57]:
        exit_pnl = 0
        count = 0
        for p in held:
            ev = p["exit_values"].get(exit_min)
            if ev is not None:
                exit_pnl += ev
                count += 1
        actual_res = sum(p["res_pnl"] for p in held)
        improvement = exit_pnl - actual_res
        print(f"     Exit@min{exit_min}: res_PnL=${exit_pnl:>+7.2f} ({count} pos) vs actual=${actual_res:>+7.2f}  improvement=${improvement:>+7.2f}")

    # ===== SECTION 6: How much is mid-hour selling making per position? =====
    print(f"\n  6. MID-HOUR SELLING QUALITY:")
    sellers = [p for p in all_positions if p["sell_count"] > 0]
    non_sellers = [p for p in all_positions if p["sell_count"] == 0]
    if sellers:
        print(f"     With mid-hour sells: {len(sellers)} pos  mid_pnl=${sum(p['mid_pnl'] for p in sellers):+.2f}  res_pnl=${sum(p['res_pnl'] for p in sellers):+.2f}")
        print(f"       Avg mid_pnl/pos: ${sum(p['mid_pnl'] for p in sellers)/len(sellers):+.2f}")
    if non_sellers:
        print(f"     No mid-hour sells: {len(non_sellers)} pos  total_pnl=${sum(p['total_pnl'] for p in non_sellers):+.2f}")

    # ===== SECTION 7: Hours where we hold 0 positions to resolution =====
    print(f"\n  7. HOUR TYPES:")
    zero_held = [h for h in hour_results if all(not p["held_to_res"] for p in h["positions"])]
    some_held = [h for h in hour_results if any(p["held_to_res"] for p in h["positions"])]
    print(f"     All positions closed mid-hour: {len(zero_held)} hours  PnL=${sum(h['total_pnl'] for h in zero_held):+.2f}")
    print(f"     Some positions held to res:    {len(some_held)} hours  PnL=${sum(h['total_pnl'] for h in some_held):+.2f}")
    if some_held:
        sh_wins = sum(1 for h in some_held if h['total_pnl'] > 0)
        sh_losses = sum(1 for h in some_held if h['total_pnl'] < 0)
        print(f"       Win rate: {sh_wins}W/{sh_losses}L = {sh_wins/max(1,sh_wins+sh_losses)*100:.0f}%")

    # ===== SECTION 8: Per-hour PnL distribution =====
    print(f"\n  8. PER-HOUR PnL DISTRIBUTION:")
    hour_pnls = [h["total_pnl"] for h in hour_results]
    brackets = [(-50, -30), (-30, -20), (-20, -10), (-10, -5), (-5, 0), (0, 5), (5, 10), (10, 20), (20, 30), (30, 50)]
    for lo, hi in brackets:
        count = sum(1 for p in hour_pnls if lo <= p < hi)
        bar = "#" * count
        print(f"     ${lo:>+4} to ${hi:>+4}: {count:>3} {bar}")


if __name__ == "__main__":
    main()
