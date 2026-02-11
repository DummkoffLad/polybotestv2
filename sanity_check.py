"""Sanity check: Compare leader PnL vs our PnL, verify we're not creating phantom money.

For each hour:
1. What did the LEADER actually do? (buys/sells, resolution outcomes)
2. What did WE do? (trades, resolution)
3. Is our PnL plausible relative to the leader?
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
from src.data.models import PriceSnapshot

CONFIG_OVERRIDES = {
    "scaling.our_capital": 50,
    "scaling.hourly_budget": 45,
    "scaling.k_factor": 1,
    "scaling.leader_estimated_capital": 900,
}


def liquidate_at_resolution(strategy, all_prices):
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        price_snap = all_prices.get(token_id)
        if price_snap and price_snap.bid and price_snap.bid > 0:
            last_bid = price_snap.bid
        else:
            last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
        res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
        dollars = pos.shares * res_price
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars


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


def analyze_leader_hour(hour_file):
    """Calculate leader's actual PnL for this hour."""
    leader_buys = {}  # token_id -> {total_cost, total_shares}
    leader_sells = {}

    with open(hour_file, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
            except:
                continue

            if obj.get("type") == "leader_trade":
                trade = obj.get("leader_trade", {})
                token_id = trade.get("token_id", "")
                action = trade.get("action", "")
                dollars = float(trade.get("leader_dollars", 0))
                shares = float(trade.get("leader_shares", 0))

                if action == "BUY":
                    if token_id not in leader_buys:
                        leader_buys[token_id] = {"cost": 0, "shares": 0}
                    leader_buys[token_id]["cost"] += dollars
                    leader_buys[token_id]["shares"] += shares
                elif action == "SELL":
                    if token_id not in leader_sells:
                        leader_sells[token_id] = {"revenue": 0, "shares": 0}
                    leader_sells[token_id]["revenue"] += dollars
                    leader_sells[token_id]["shares"] += shares

    return leader_buys, leader_sells


def main():
    base = Path("data/sessions")

    all_our_pnls = []
    all_leader_pnls = []
    our_buy_total = 0
    our_sell_total = 0
    hour_details = []

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h < 0:
                continue

            session = f"{date_dir.name}/{hf.stem}"

            # Our PnL
            strategy = get_strategy("profit_taker")
            replayer = SessionReplayer(hf, strategy, config_overrides=CONFIG_OVERRIDES)
            try:
                count = replayer.load()
            except:
                continue
            if count == 0:
                continue

            config = replayer._merge_config()
            strategy_config = StrategyConfig.from_dict(config)
            strategy.initialize(strategy_config)
            strategy.on_session_start()

            last_hour = None
            our_buys_dollar = Decimal("0")
            our_sells_dollar = Decimal("0")
            our_buy_count = 0
            our_sell_count = 0
            followed_tokens = set()

            for event in replayer.loader.events:
                all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
                event.context['all_prices'] = all_prices
                last_hour = event.trade.timestamp.hour
                decision = strategy.on_event(event)
                if decision.action == DecisionAction.BUY:
                    strategy.on_fill(event, decision)
                    our_buys_dollar += decision.dollars
                    our_buy_count += 1
                    followed_tokens.add(event.trade.token_id)
                elif decision.action == DecisionAction.SELL:
                    strategy.on_fill(event, decision)
                    our_sells_dollar += decision.dollars
                    our_sell_count += 1

            end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}

            # Count our wins/losses at resolution
            our_wins = 0
            our_losses = 0
            positions = strategy.portfolio.get_positions()
            for token_id, pos in positions.items():
                if pos.shares <= 0:
                    continue
                ps = end_prices.get(token_id)
                if ps and ps.bid and ps.bid > 0:
                    if ps.bid >= Decimal("0.50"):
                        our_wins += 1
                    else:
                        our_losses += 1
                else:
                    entry = strategy.our_entries.get(token_id, Decimal("0.50"))
                    if entry >= Decimal("0.50"):
                        our_wins += 1
                    else:
                        our_losses += 1

            liquidate_at_resolution(strategy, end_prices)
            our_pnl = float(strategy.cash) - 100.0

            # Leader info
            leader_buys, leader_sells = analyze_leader_hour(hf)
            leader_total_bought = sum(b["cost"] for b in leader_buys.values())
            leader_total_sold = sum(s["revenue"] for s in leader_sells.values())
            leader_tokens = len(leader_buys)

            # Leader resolution PnL (approximate)
            leader_res_pnl = 0
            for token_id, buy_info in leader_buys.items():
                net_shares = buy_info["shares"]
                # Subtract sold shares
                if token_id in leader_sells:
                    net_shares -= leader_sells[token_id]["shares"]
                if net_shares <= 0:
                    continue
                ps = end_prices.get(token_id)
                if ps and ps.bid and ps.bid > 0:
                    if ps.bid >= Decimal(str("0.50")):
                        leader_res_pnl += net_shares * 0.99 - (net_shares * buy_info["cost"] / buy_info["shares"])
                    else:
                        leader_res_pnl += net_shares * 0.01 - (net_shares * buy_info["cost"] / buy_info["shares"])

            all_our_pnls.append(our_pnl)
            all_leader_pnls.append(leader_res_pnl)
            our_buy_total += float(our_buys_dollar)
            our_sell_total += float(our_sells_dollar)

            hour_details.append({
                "session": session,
                "hour": utc_h,
                "our_pnl": our_pnl,
                "leader_pnl_approx": leader_res_pnl,
                "our_buys": our_buy_count,
                "our_sells": our_sell_count,
                "our_buy_dollars": float(our_buys_dollar),
                "our_wins": our_wins,
                "our_losses": our_losses,
                "leader_tokens": leader_tokens,
                "leader_bought": leader_total_bought,
                "followed": len(followed_tokens),
            })

    # Print summary
    print(f"{'='*100}")
    print(f"  SANITY CHECK: Our PnL vs Leader ({len(all_our_pnls)} hours)")
    print(f"{'='*100}")

    our_total = sum(all_our_pnls)
    our_avg = our_total / len(all_our_pnls)
    our_wins_count = sum(1 for p in all_our_pnls if p > 0)
    our_losses_count = sum(1 for p in all_our_pnls if p < 0)

    print(f"\n  OUR STRATEGY:")
    print(f"    Total PnL:     ${our_total:+.2f}")
    print(f"    Avg/hour:      ${our_avg:+.2f}")
    print(f"    Win rate:      {our_wins_count}/{our_wins_count+our_losses_count} = {our_wins_count/(our_wins_count+our_losses_count)*100:.0f}%")
    print(f"    Total bought:  ${our_buy_total:.2f}")
    print(f"    Total sold:    ${our_sell_total:.2f}")
    print(f"    ROI:           {our_total/our_buy_total*100:.1f}% on capital deployed")

    print(f"\n  PER-HOUR BREAKDOWN (top 10 wins, top 10 losses):")
    sorted_hours = sorted(hour_details, key=lambda x: x["our_pnl"])

    print(f"\n  WORST 10 HOURS:")
    print(f"    {'Session':<45} {'Our$':>7} {'Buys':>5} {'W/L':>5} {'BuyAmt':>7} {'Followed':>8}")
    for h in sorted_hours[:10]:
        print(f"    {h['session']:<45} ${h['our_pnl']:>+6.2f} {h['our_buys']:>5} {h['our_wins']}/{h['our_losses']:<3} ${h['our_buy_dollars']:>6.1f} {h['followed']:>5}/{h['leader_tokens']}")

    print(f"\n  BEST 10 HOURS:")
    for h in sorted_hours[-10:][::-1]:
        print(f"    {h['session']:<45} ${h['our_pnl']:>+6.2f} {h['our_buys']:>5} {h['our_wins']}/{h['our_losses']:<3} ${h['our_buy_dollars']:>6.1f} {h['followed']:>5}/{h['leader_tokens']}")

    # Correlation check
    both = [(h["our_pnl"], h["leader_pnl_approx"]) for h in hour_details if abs(h["leader_pnl_approx"]) > 0]
    if both:
        agreement = sum(1 for o, l in both if (o > 0) == (l > 0))
        print(f"\n  AGREEMENT WITH LEADER:")
        print(f"    Hours where both win or both lose: {agreement}/{len(both)} = {agreement/len(both)*100:.0f}%")

    # PnL distribution
    print(f"\n  PNL DISTRIBUTION:")
    brackets = [(-100, -30), (-30, -20), (-20, -10), (-10, -5), (-5, 0), (0, 5), (5, 10), (10, 20), (20, 30), (30, 100)]
    for lo, hi in brackets:
        count = sum(1 for p in all_our_pnls if lo <= p < hi)
        bar = "#" * count
        print(f"    ${lo:>+4} to ${hi:>+4}: {count:>3} {bar}")


if __name__ == "__main__":
    main()
