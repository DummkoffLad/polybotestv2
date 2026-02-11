"""Analyze leader conviction signals and their predictive value.

IDEA: Instead of fixed boost, use leader's behavior AS a signal:
1. Leader trade size (% of capital) -> bigger = more conviction
2. Leader speed (multiple buys of same token) -> faster re-entry = more conviction
3. Leader's recent accuracy (last 2-3 hours) -> hot/cold streaks
4. Number of concurrent positions -> spread thin = less conviction
5. Bid-ask spread at entry -> tight spread = more liquid = better odds
"""
import sys
import json
from pathlib import Path
from decimal import Decimal
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from src.framework.replay.replayer import SessionReplayer
from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.data.models import PriceSnapshot

CONFIG_OVERRIDES = {
    "scaling.our_capital": 50,
    "scaling.hourly_budget": 45,
    "scaling.k_factor": 1,
    "scaling.leader_estimated_capital": 900,
}


def get_utc_hour_from_file(path):
    with open(path, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
                if obj.get("type") in ("leader_trade", "fill"):
                    ts = obj.get("timestamp", "")
                    if "T" in ts:
                        return int(ts.split("T")[1][:2])
            except Exception:
                continue
    return -1


def analyze_hour(hour_file):
    """Analyze one hour for conviction signals."""
    # Parse leader trades
    leader_trades = []
    price_snapshots = []

    with open(hour_file, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue

            if obj.get("type") == "leader_trade":
                trade = obj.get("leader_trade", {})
                ts = trade.get("timestamp", "")
                try:
                    timestamp = datetime.fromisoformat(ts)
                except Exception:
                    continue
                leader_trades.append({
                    "timestamp": timestamp,
                    "token_id": trade.get("token_id", ""),
                    "action": trade.get("action", ""),
                    "price": float(trade.get("leader_price", 0)),
                    "dollars": float(trade.get("leader_dollars", 0)),
                    "shares": float(trade.get("leader_shares", 0)),
                    "minute": timestamp.minute,
                    "ask": float(trade.get("ask", 0)) if trade.get("ask") else None,
                    "bid": float(trade.get("bid", 0)) if trade.get("bid") else None,
                })

            elif obj.get("type") == "price_snapshot":
                snap = obj.get("prices", {})
                ts = obj.get("timestamp", "")
                try:
                    timestamp = datetime.fromisoformat(ts)
                except Exception:
                    continue
                price_snapshots.append({"timestamp": timestamp, "prices": snap})

    if not leader_trades:
        return None

    # Load end-of-hour prices for resolution
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except Exception:
        return None
    if count == 0:
        return None

    last_hour = None
    for event in replayer.loader.events:
        last_hour = event.trade.timestamp.hour

    end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}

    # Group leader trades by token
    by_token = defaultdict(list)
    for t in leader_trades:
        by_token[t["token_id"]].append(t)

    # Analyze each token
    results = []
    for token_id, trades in by_token.items():
        buys = [t for t in trades if t["action"] == "BUY"]
        sells = [t for t in trades if t["action"] == "SELL"]
        if not buys:
            continue

        total_cost = sum(b["dollars"] for b in buys)
        total_shares = sum(b["shares"] for b in buys)
        avg_entry = total_cost / total_shares if total_shares > 0 else 0

        # === CONVICTION SIGNALS ===
        # 1. Trade size (% of leader capital)
        first_buy_pct = buys[0]["dollars"] / 900 * 100 if buys else 0
        total_buy_pct = total_cost / 900 * 100

        # 2. Re-entry count (how many times leader bought this token)
        buy_count = len(buys)

        # 3. Spread at entry (tight = liquid = better)
        first_buy_spread = None
        if buys[0].get("ask") and buys[0].get("bid"):
            if buys[0]["ask"] > 0 and buys[0]["bid"] > 0:
                first_buy_spread = (buys[0]["ask"] - buys[0]["bid"]) / buys[0]["ask"] * 100

        # 4. Entry minute
        first_buy_min = buys[0]["minute"]

        # 5. Did leader sell mid-hour?
        total_sold_dollars = sum(s["dollars"] for s in sells)
        pct_sold = total_sold_dollars / total_cost * 100 if total_cost > 0 else 0
        avg_sell_price = sum(s["price"] * s["shares"] for s in sells) / sum(s["shares"] for s in sells) if sells else 0

        # 6. Resolution outcome
        ps = end_prices.get(token_id)
        if ps and ps.bid is not None:
            end_bid = float(ps.bid)
            won = end_bid >= 0.50
        else:
            end_bid = None
            won = avg_entry >= 0.50

        # Calculate PnL components
        sell_pnl = 0
        for s in sells:
            sell_pnl += s["dollars"] - (s["shares"] * avg_entry)

        remaining_shares = total_shares - sum(s["shares"] for s in sells)
        if won:
            res_price = 0.99
        else:
            res_price = 0.01
        res_pnl = remaining_shares * (res_price - avg_entry) if remaining_shares > 0 else 0

        results.append({
            "token_id": token_id[-8:],
            "avg_entry": avg_entry,
            "total_cost": total_cost,
            "first_buy_pct": first_buy_pct,
            "total_buy_pct": total_buy_pct,
            "buy_count": buy_count,
            "first_buy_spread": first_buy_spread,
            "first_buy_min": first_buy_min,
            "pct_sold": pct_sold,
            "won": won,
            "end_bid": end_bid,
            "sell_pnl": sell_pnl,
            "res_pnl": res_pnl,
            "total_pnl": sell_pnl + res_pnl,
        })

    return results


def main():
    base = Path("data/sessions")
    all_results = []

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h < 0:
                continue
            r = analyze_hour(hf)
            if r:
                all_results.extend(r)

    if not all_results:
        print("No data!")
        return

    print(f"{'='*120}")
    print(f"  LEADER CONVICTION ANALYSIS ({len(all_results)} token-positions)")
    print(f"{'='*120}")

    # === 1. FIRST BUY SIZE (% of capital) vs outcome ===
    print(f"\n  1. LEADER FIRST BUY SIZE vs OUTCOME:")
    pct_brackets = [(0, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 50)]
    for lo, hi in pct_brackets:
        group = [r for r in all_results if lo <= r["first_buy_pct"] < hi]
        if not group:
            continue
        wins = sum(1 for r in group if r["total_pnl"] > 0)
        losses = sum(1 for r in group if r["total_pnl"] < 0)
        wr = wins / max(1, wins + losses) * 100
        total_pnl = sum(r["total_pnl"] for r in group)
        avg_pnl = total_pnl / len(group)
        print(f"    {lo:>2}-{hi:>2}% capital: {len(group):>4} pos  WR={wr:>4.0f}%  PnL=${total_pnl:>+8.0f}  avg=${avg_pnl:>+6.1f}")

    # === 2. RE-ENTRY COUNT vs outcome ===
    print(f"\n  2. LEADER RE-ENTRY COUNT vs OUTCOME:")
    for count in [1, 2, 3, 4, 5]:
        if count < 5:
            group = [r for r in all_results if r["buy_count"] == count]
            label = f"  {count} buys"
        else:
            group = [r for r in all_results if r["buy_count"] >= count]
            label = f" {count}+ buys"
        if not group:
            continue
        wins = sum(1 for r in group if r["total_pnl"] > 0)
        losses = sum(1 for r in group if r["total_pnl"] < 0)
        wr = wins / max(1, wins + losses) * 100
        total_pnl = sum(r["total_pnl"] for r in group)
        avg_entry = sum(r["avg_entry"] for r in group) / len(group)
        print(f"    {label}: {len(group):>4} pos  WR={wr:>4.0f}%  PnL=${total_pnl:>+8.0f}  avg_entry=${avg_entry:.3f}")

    # === 3. ENTRY PRICE BRACKET (leader perspective) ===
    print(f"\n  3. ENTRY PRICE vs OUTCOME (leader perspective):")
    price_brackets = [(0, 0.25), (0.25, 0.35), (0.35, 0.45), (0.45, 0.55),
                      (0.55, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 0.97)]
    for lo, hi in price_brackets:
        group = [r for r in all_results if lo <= r["avg_entry"] < hi]
        if not group:
            continue
        wins = sum(1 for r in group if r["total_pnl"] > 0)
        losses = sum(1 for r in group if r["total_pnl"] < 0)
        wr = wins / max(1, wins + losses) * 100
        total_pnl = sum(r["total_pnl"] for r in group)
        avg_pnl = total_pnl / len(group)
        sell_pnl = sum(r["sell_pnl"] for r in group)
        res_pnl = sum(r["res_pnl"] for r in group)
        print(f"    ${lo:.2f}-${hi:.2f}: {len(group):>4} pos  WR={wr:>4.0f}%  PnL=${total_pnl:>+8.0f}  sell=${sell_pnl:>+7.0f} res=${res_pnl:>+7.0f}")

    # === 4. FIRST BUY MINUTE vs outcome ===
    print(f"\n  4. FIRST BUY MINUTE vs OUTCOME:")
    min_brackets = [(0, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 60)]
    for lo, hi in min_brackets:
        group = [r for r in all_results if lo <= r["first_buy_min"] < hi]
        if not group:
            continue
        wins = sum(1 for r in group if r["total_pnl"] > 0)
        losses = sum(1 for r in group if r["total_pnl"] < 0)
        wr = wins / max(1, wins + losses) * 100
        total_pnl = sum(r["total_pnl"] for r in group)
        avg_pnl = total_pnl / len(group)
        print(f"    min {lo:>2}-{hi:>2}: {len(group):>4} pos  WR={wr:>4.0f}%  PnL=${total_pnl:>+8.0f}  avg=${avg_pnl:>+6.1f}")

    # === 5. COMBINED: Price + conviction (buy count) ===
    print(f"\n  5. COMBINED PRICE + CONVICTION FILTER:")
    combos = [
        ("entry>=0.65 + >=2 buys", lambda r: r["avg_entry"] >= 0.65 and r["buy_count"] >= 2),
        ("entry>=0.65 + >=3 buys", lambda r: r["avg_entry"] >= 0.65 and r["buy_count"] >= 3),
        ("entry>=0.55 + >=2 buys", lambda r: r["avg_entry"] >= 0.55 and r["buy_count"] >= 2),
        ("entry>=0.65 + first>=3%", lambda r: r["avg_entry"] >= 0.65 and r["first_buy_pct"] >= 3),
        ("entry>=0.65 + first>=2%", lambda r: r["avg_entry"] >= 0.65 and r["first_buy_pct"] >= 2),
        ("entry>=0.55 + first>=3%", lambda r: r["avg_entry"] >= 0.55 and r["first_buy_pct"] >= 3),
        ("ALL (no filter)", lambda r: True),
        ("entry>=0.45 (current)", lambda r: r["avg_entry"] >= 0.45),
        ("entry>=0.65", lambda r: r["avg_entry"] >= 0.65),
    ]
    for label, filt in combos:
        group = [r for r in all_results if filt(r)]
        if not group:
            continue
        wins = sum(1 for r in group if r["total_pnl"] > 0)
        losses = sum(1 for r in group if r["total_pnl"] < 0)
        wr = wins / max(1, wins + losses) * 100
        total_pnl = sum(r["total_pnl"] for r in group)
        avg_pnl = total_pnl / len(group)
        print(f"    {label:<30}: {len(group):>4} pos  WR={wr:>4.0f}%  PnL=${total_pnl:>+8.0f}  avg=${avg_pnl:>+6.1f}")

    # === 6. SELL BEHAVIOR: Tokens where leader sold vs held ===
    print(f"\n  6. LEADER SELL BEHAVIOR vs OUTCOME:")
    sold_high = [r for r in all_results if r["pct_sold"] >= 80]
    sold_some = [r for r in all_results if 20 < r["pct_sold"] < 80]
    held_most = [r for r in all_results if r["pct_sold"] <= 20]
    for label, group in [("Sold >80%", sold_high), ("Sold 20-80%", sold_some), ("Held >80%", held_most)]:
        if not group:
            continue
        wins = sum(1 for r in group if r["total_pnl"] > 0)
        losses = sum(1 for r in group if r["total_pnl"] < 0)
        wr = wins / max(1, wins + losses) * 100
        total_pnl = sum(r["total_pnl"] for r in group)
        sell_pnl = sum(r["sell_pnl"] for r in group)
        res_pnl = sum(r["res_pnl"] for r in group)
        avg_entry = sum(r["avg_entry"] for r in group) / len(group)
        print(f"    {label:<16}: {len(group):>4} pos  WR={wr:>4.0f}%  PnL=${total_pnl:>+8.0f}  sell=${sell_pnl:>+7.0f} res=${res_pnl:>+7.0f}  avg_entry=${avg_entry:.3f}")

    # === 7. NUMBER OF CONCURRENT POSITIONS (when this trade happened) ===
    # This needs to be tracked differently - skip for now
    print(f"\n  7. RESOLUTION WIN RATE BY PRICE BAND (leader buys only):")
    for lo, hi in price_brackets:
        group = [r for r in all_results if lo <= r["avg_entry"] < hi]
        if not group:
            continue
        # Only look at tokens that were held to resolution
        held = [r for r in group if r["pct_sold"] < 50]
        if not held:
            continue
        res_wins = sum(1 for r in held if r["won"])
        res_wr = res_wins / len(held) * 100
        avg_res_pnl = sum(r["res_pnl"] for r in held) / len(held)
        print(f"    ${lo:.2f}-${hi:.2f}: {len(held):>3} held  res_WR={res_wr:>4.0f}%  avg_res=${avg_res_pnl:>+6.1f}")


if __name__ == "__main__":
    main()
