"""Analyze leader's LOW-PRICE trades: momentum vs resolution.

Key question: When leader buys at <$0.45, do they:
a) Sell mid-hour at a profit? (momentum trade)
b) Hold to resolution? (bet on outcome)

If (a) is common and profitable, we should follow these trades.
If (b) dominates and usually loses, skip them.
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
import src.strategies.profit_taker.strategy as pt_module


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
            except:
                continue
    return -1


def analyze_hour(hour_file):
    """Track leader's buy/sell behavior for low-price tokens."""
    # Parse all leader trades
    leader_trades = []

    with open(hour_file, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
            except:
                continue

            if obj.get("type") == "leader_trade":
                trade = obj.get("leader_trade", {})
                ts = trade.get("timestamp", "")
                try:
                    timestamp = datetime.fromisoformat(ts)
                except:
                    continue

                leader_trades.append({
                    "timestamp": timestamp,
                    "token_id": trade.get("token_id", ""),
                    "action": trade.get("action", ""),
                    "price": float(trade.get("leader_price", 0)),
                    "dollars": float(trade.get("leader_dollars", 0)),
                    "shares": float(trade.get("leader_shares", 0)),
                    "minute": timestamp.minute,
                })

    # Group by token
    by_token = defaultdict(list)
    for t in leader_trades:
        by_token[t["token_id"]].append(t)

    # Load end-of-hour prices for resolution
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return []
    if count == 0:
        return []

    last_hour = None
    for event in replayer.loader.events:
        last_hour = event.trade.timestamp.hour

    end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}

    results = []
    for token_id, trades in by_token.items():
        buys = [t for t in trades if t["action"] == "BUY"]
        sells = [t for t in trades if t["action"] == "SELL"]

        if not buys:
            continue

        # Check if this is a low-entry token (any buy below threshold)
        low_buys = [b for b in buys if b["price"] < 0.45]
        if not low_buys:
            continue

        # Calculate leader's entry
        total_cost = sum(b["dollars"] for b in buys)
        total_shares_bought = sum(b["shares"] for b in buys)
        avg_entry = total_cost / total_shares_bought if total_shares_bought > 0 else 0

        # Calculate leader's mid-hour sell revenue
        total_sell_revenue = sum(s["dollars"] for s in sells)
        total_shares_sold = sum(s["shares"] for s in sells)
        avg_sell_price = total_sell_revenue / total_shares_sold if total_shares_sold > 0 else 0

        # Remaining shares at resolution
        remaining_shares = total_shares_bought - total_shares_sold

        # Resolution outcome
        ps = end_prices.get(token_id)
        end_bid = float(ps.bid) if ps and ps.bid else None
        if end_bid is not None:
            won = end_bid >= 0.50
            res_price = 0.99 if won else 0.01
        else:
            won = None
            res_price = None

        # Calculate PnL components
        sell_pnl = total_sell_revenue - (total_shares_sold * avg_entry) if total_shares_sold > 0 else 0
        res_pnl = remaining_shares * (res_price - avg_entry) if res_price is not None and remaining_shares > 0 else 0

        pct_sold = total_shares_sold / total_shares_bought * 100 if total_shares_bought > 0 else 0

        results.append({
            "token": token_id[-8:],
            "avg_entry": avg_entry,
            "total_bought": total_cost,
            "shares_bought": total_shares_bought,
            "num_sells": len(sells),
            "pct_sold": pct_sold,
            "avg_sell_price": avg_sell_price,
            "sell_pnl": sell_pnl,
            "remaining_shares": remaining_shares,
            "end_bid": end_bid,
            "won": won,
            "res_pnl": res_pnl,
            "total_pnl": sell_pnl + res_pnl,
            "first_buy_min": min(b["minute"] for b in buys),
            "last_sell_min": max(s["minute"] for s in sells) if sells else None,
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
            results = analyze_hour(hf)
            for r in results:
                r["session"] = f"{date_dir.name}/{hf.stem}"
                r["utc_h"] = utc_h
            all_results.extend(results)

    if not all_results:
        print("No low-entry results found!")
        return

    print(f"{'='*120}")
    print(f"  LEADER LOW-PRICE (<45c) ENTRY ANALYSIS: {len(all_results)} tokens across sessions")
    print(f"{'='*120}")

    # Category: sold before resolution vs held
    sold_fully = [r for r in all_results if r["pct_sold"] >= 90]
    sold_partially = [r for r in all_results if 10 < r["pct_sold"] < 90]
    held_to_res = [r for r in all_results if r["pct_sold"] <= 10]

    print(f"\n  LEADER BEHAVIOR:")
    print(f"    Sold fully (>90%):     {len(sold_fully)} tokens")
    print(f"    Sold partially:        {len(sold_partially)} tokens")
    print(f"    Held to resolution:    {len(held_to_res)} tokens")

    # PnL by behavior
    for label, group in [("SOLD FULLY", sold_fully), ("SOLD PARTIALLY", sold_partially), ("HELD TO RES", held_to_res)]:
        if not group:
            continue
        total_sell_pnl = sum(r["sell_pnl"] for r in group)
        total_res_pnl = sum(r["res_pnl"] for r in group)
        total_pnl = sum(r["total_pnl"] for r in group)
        avg_entry = sum(r["avg_entry"] for r in group) / len(group)

        wins = sum(1 for r in group if r["total_pnl"] > 0)
        losses = sum(1 for r in group if r["total_pnl"] < 0)
        wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

        print(f"\n    {label} ({len(group)} tokens, avg entry ${avg_entry:.3f}):")
        print(f"      Mid-hour sell PnL: ${total_sell_pnl:+.2f}")
        print(f"      Resolution PnL:    ${total_res_pnl:+.2f}")
        print(f"      Total PnL:         ${total_pnl:+.2f}")
        print(f"      Win rate:          {wins}W/{losses}L = {wr:.0f}%")

        if label == "SOLD FULLY" and group:
            avg_sell_p = sum(r["avg_sell_price"] for r in group) / len(group)
            print(f"      Avg sell price:    ${avg_sell_p:.3f} (bought at ~${avg_entry:.3f})")

    # Detail: most profitable low entries
    print(f"\n  TOP 10 MOST PROFITABLE LOW ENTRIES (leader):")
    print(f"    {'Token':>10} {'Entry':>6} {'Bought':>8} {'%Sold':>5} {'SellPnL':>8} {'ResPnL':>8} {'Total':>8} {'EndBid':>7} {'W/L':>4}")
    sorted_results = sorted(all_results, key=lambda r: r["total_pnl"], reverse=True)
    for r in sorted_results[:10]:
        eb = f"${r['end_bid']:.3f}" if r['end_bid'] is not None else "  N/A"
        wl = "W" if r["total_pnl"] > 0 else "L"
        print(f"    {r['token']:>10} ${r['avg_entry']:.3f} ${r['total_bought']:>7.1f} {r['pct_sold']:>4.0f}% ${r['sell_pnl']:>+7.1f} ${r['res_pnl']:>+7.1f} ${r['total_pnl']:>+7.1f} {eb} {wl}")

    # Detail: most UNprofitable low entries
    print(f"\n  TOP 10 MOST UNPROFITABLE LOW ENTRIES (leader):")
    for r in sorted_results[-10:][::-1]:
        eb = f"${r['end_bid']:.3f}" if r['end_bid'] is not None else "  N/A"
        wl = "W" if r["total_pnl"] > 0 else "L"
        print(f"    {r['token']:>10} ${r['avg_entry']:.3f} ${r['total_bought']:>7.1f} {r['pct_sold']:>4.0f}% ${r['sell_pnl']:>+7.1f} ${r['res_pnl']:>+7.1f} ${r['total_pnl']:>+7.1f} {eb} {wl}")

    # What's the NET value of following low entries?
    total_low_pnl = sum(r["total_pnl"] for r in all_results)
    total_sell_component = sum(r["sell_pnl"] for r in all_results)
    total_res_component = sum(r["res_pnl"] for r in all_results)
    print(f"\n  NET VALUE OF ALL LOW (<45c) ENTRIES:")
    print(f"    Mid-hour selling:  ${total_sell_component:+.2f}")
    print(f"    Resolution:        ${total_res_component:+.2f}")
    print(f"    TOTAL:             ${total_low_pnl:+.2f}")

    # Now the key question: scale this to OUR position sizes
    # Our capital is ~5.5% of leader's, so our PnL from these trades would be ~5.5%
    our_scale = 50 / 900 * 8  # our_capital/leader_capital * boost
    print(f"\n  ESTIMATED OUR PnL FROM THESE TRADES (scale = {our_scale:.2f}x):")
    our_est = total_low_pnl * our_scale
    print(f"    Our estimated PnL: ${our_est:+.2f}")
    print(f"    vs skip_low=0.45 gains: Sharpe 0.450 vs 0.309 = +46% risk-adjusted improvement")


if __name__ == "__main__":
    main()
