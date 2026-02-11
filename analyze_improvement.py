"""Analyze where we can improve: time patterns, entry quality, leader behavior.

Questions:
1. Are certain hours of day better/worse?
2. What's our entry price distribution vs resolution? (buy at 0.60, resolve at 0.99)
3. How much do we lose to spread/slippage vs to wrong direction?
4. Does leader streak matter? (consecutive wins/losses)
5. What if we sized positions based on price distance from 0.50?
"""
import sys
import json
import statistics
from pathlib import Path
from decimal import Decimal
from collections import defaultdict
from datetime import datetime

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


def main():
    base = Path("data/sessions")

    # Per UTC hour stats
    hour_of_day_pnls = defaultdict(list)

    # Entry price analysis
    all_entries = []  # (entry_price, resolution_outcome, pnl_per_share, token_id, hour)

    # Leader trade count vs our performance
    leader_activity_vs_pnl = []

    # Session-level streaks
    session_hours = defaultdict(list)  # session -> [(hour, pnl)]

    # Spread cost analysis
    spread_costs = []

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h < 0:
                continue

            session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"

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
            our_trades = []  # (action, token_id, price, dollars, shares, bid, ask)
            leader_trade_count = 0

            for event in replayer.loader.events:
                all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
                event.context['all_prices'] = all_prices
                last_hour = event.trade.timestamp.hour
                leader_trade_count += 1

                decision = strategy.on_event(event)
                if decision.action == DecisionAction.BUY:
                    strategy.on_fill(event, decision)
                    our_trades.append({
                        "action": "BUY",
                        "token_id": event.trade.token_id,
                        "our_price": float(decision.price),
                        "our_dollars": float(decision.dollars),
                        "our_shares": float(decision.shares),
                        "bid": float(event.prices.bid) if event.prices.bid else None,
                        "ask": float(event.prices.ask) if event.prices.ask else None,
                        "leader_price": float(event.trade.price),
                        "leader_dollars": float(event.trade.dollars),
                        "minute": event.trade.timestamp.minute,
                    })
                elif decision.action == DecisionAction.SELL:
                    strategy.on_fill(event, decision)
                    our_trades.append({
                        "action": "SELL",
                        "token_id": event.trade.token_id,
                        "our_price": float(decision.price),
                        "our_dollars": float(decision.dollars),
                        "our_shares": float(decision.shares),
                        "bid": float(event.prices.bid) if event.prices.bid else None,
                        "ask": float(event.prices.ask) if event.prices.ask else None,
                        "leader_price": float(event.trade.price),
                        "leader_dollars": float(event.trade.dollars),
                        "minute": event.trade.timestamp.minute,
                    })

            end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}

            # Analyze each entry at resolution
            positions = strategy.portfolio.get_positions()
            for token_id, pos in positions.items():
                if pos.shares <= 0:
                    continue
                entry = strategy.our_entries.get(token_id)
                if not entry:
                    continue
                ps = end_prices.get(token_id)
                if ps and ps.bid and ps.bid > 0:
                    end_bid = float(ps.bid)
                    won = end_bid >= 0.50
                    res_price = 0.99 if won else 0.01
                    pnl_per_share = res_price - float(entry)
                    all_entries.append({
                        "entry": float(entry),
                        "end_bid": end_bid,
                        "won": won,
                        "pnl_per_share": pnl_per_share,
                        "shares": float(pos.shares),
                        "total_pnl": pnl_per_share * float(pos.shares),
                        "hour": utc_h,
                    })

            # Liquidate for PnL calc
            for token_id, pos in list(positions.items()):
                if pos.shares <= 0:
                    continue
                price_snap = end_prices.get(token_id)
                if price_snap and price_snap.bid and price_snap.bid > 0:
                    last_bid = price_snap.bid
                else:
                    last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
                res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
                dollars = pos.shares * res_price
                strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
                strategy.cash += dollars

            pnl = float(strategy.cash) - 100.0
            hour_of_day_pnls[utc_h].append(pnl)
            session_hours[session].append((utc_h, pnl))
            leader_activity_vs_pnl.append((leader_trade_count, pnl))

            # Spread cost: sum of (ask - bid) * shares for each buy
            hour_spread_cost = sum(
                (t["ask"] - t["bid"]) * t["our_shares"]
                for t in our_trades
                if t["action"] == "BUY" and t["bid"] and t["ask"]
            )
            spread_costs.append(hour_spread_cost)

    # ===== REPORT =====
    print(f"{'='*100}")
    print(f"  IMPROVEMENT ANALYSIS ({sum(len(v) for v in hour_of_day_pnls.values())} hours)")
    print(f"{'='*100}")

    # 1. TIME OF DAY
    print(f"\n  1. PERFORMANCE BY UTC HOUR:")
    print(f"     {'Hour':>4} {'Hours':>5} {'Total$':>8} {'Avg$':>7} {'WR':>5} {'Sharpe':>7}")
    for h in sorted(hour_of_day_pnls.keys()):
        pnls = hour_of_day_pnls[h]
        total = sum(pnls)
        avg = total / len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100
        std = statistics.stdev(pnls) if len(pnls) > 1 else 0
        sharpe = avg / std if std > 0 else 0
        bar = "+" * int(max(0, total / 5)) + "-" * int(max(0, -total / 5))
        print(f"     H{h:02d}  {len(pnls):>5} ${total:>+7.1f} ${avg:>+6.2f} {wr:>4.0f}% {sharpe:>+6.3f}  {bar}")

    # 2. ENTRY PRICE ANALYSIS
    print(f"\n  2. ENTRY PRICE vs RESOLUTION ({len(all_entries)} positions):")
    price_brackets = [(0.25, 0.35), (0.35, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 0.97)]
    print(f"     {'Entry Range':>15} {'Count':>5} {'WR':>5} {'Avg PnL/sh':>10} {'Total PnL':>10} {'Avg Entry':>10}")
    for lo, hi in price_brackets:
        entries = [e for e in all_entries if lo <= e["entry"] < hi]
        if not entries:
            continue
        wr = sum(1 for e in entries if e["won"]) / len(entries) * 100
        avg_pnl = sum(e["pnl_per_share"] for e in entries) / len(entries)
        total_pnl = sum(e["total_pnl"] for e in entries)
        avg_entry = sum(e["entry"] for e in entries) / len(entries)
        print(f"     {lo:.2f} - {hi:.2f}   {len(entries):>5} {wr:>4.0f}% ${avg_pnl:>+9.4f} ${total_pnl:>+9.2f} ${avg_entry:>9.4f}")

    # 3. WIN vs LOSS: how much do we make on wins vs lose on losses?
    wins = [e for e in all_entries if e["won"]]
    losses = [e for e in all_entries if not e["won"]]
    if wins and losses:
        avg_win_pnl = sum(e["total_pnl"] for e in wins) / len(wins)
        avg_loss_pnl = sum(e["total_pnl"] for e in losses) / len(losses)
        total_win_pnl = sum(e["total_pnl"] for e in wins)
        total_loss_pnl = sum(e["total_pnl"] for e in losses)
        print(f"\n  3. WIN/LOSS ASYMMETRY:")
        print(f"     Wins:   {len(wins)} positions, avg ${avg_win_pnl:+.2f}/pos, total ${total_win_pnl:+.2f}")
        print(f"     Losses: {len(losses)} positions, avg ${avg_loss_pnl:+.2f}/pos, total ${total_loss_pnl:+.2f}")
        print(f"     Ratio:  We make ${avg_win_pnl:.2f} per win vs lose ${abs(avg_loss_pnl):.2f} per loss")
        print(f"     Edge:   WR={len(wins)/(len(wins)+len(losses))*100:.0f}% needed={abs(avg_loss_pnl)/(avg_win_pnl+abs(avg_loss_pnl))*100:.0f}% (breakeven)")

    # 4. SPREAD COST
    if spread_costs:
        total_spread = sum(spread_costs)
        avg_spread = total_spread / len(spread_costs)
        print(f"\n  4. SPREAD COSTS:")
        print(f"     Total spread paid: ${total_spread:.2f}")
        print(f"     Avg/hour: ${avg_spread:.2f}")
        print(f"     As % of total PnL: {total_spread/sum(sum(v) for v in hour_of_day_pnls.values())*100:.1f}%")

    # 5. LEADER ACTIVITY vs OUR PnL
    # Bin by leader trade count
    print(f"\n  5. LEADER ACTIVITY vs OUR PnL:")
    activity_bins = [(0, 50), (50, 100), (100, 200), (200, 400), (400, 1000)]
    print(f"     {'Leader Trades':>15} {'Hours':>5} {'Avg PnL':>8} {'WR':>5}")
    for lo, hi in activity_bins:
        matching = [(c, p) for c, p in leader_activity_vs_pnl if lo <= c < hi]
        if not matching:
            continue
        avg_p = sum(p for _, p in matching) / len(matching)
        wr = sum(1 for _, p in matching if p > 0) / len(matching) * 100
        print(f"     {lo:>3}-{hi:<4}        {len(matching):>5} ${avg_p:>+7.2f} {wr:>4.0f}%")

    # 6. ENTRY MINUTE ANALYSIS (do late entries perform worse?)
    print(f"\n  6. WHAT ENTRY PRICES WIN THE MOST?")
    # For each entry, calculate: if price is closer to 0.99 (high confidence), does it win more?
    confidence_brackets = [(0.50, 0.60, "50-60%"), (0.60, 0.70, "60-70%"), (0.70, 0.80, "70-80%"), (0.80, 0.90, "80-90%"), (0.90, 0.97, "90-97%")]
    print(f"     {'Confidence':>12} {'Count':>5} {'WR':>5} {'Avg$/pos':>8} {'Total$':>8}")
    for lo, hi, label in confidence_brackets:
        entries = [e for e in all_entries if lo <= e["entry"] < hi]
        if not entries:
            continue
        wr = sum(1 for e in entries if e["won"]) / len(entries) * 100
        avg_p = sum(e["total_pnl"] for e in entries) / len(entries)
        total_p = sum(e["total_pnl"] for e in entries)
        print(f"     {label:>12} {len(entries):>5} {wr:>4.0f}% ${avg_p:>+7.2f} ${total_p:>+7.2f}")

    # 7. IDEAS SCORING
    print(f"\n  7. IMPROVEMENT IDEAS (estimated impact):")

    # Idea A: Skip hours where leader has <50 trades (low activity = noise)
    low_act = [p for c, p in leader_activity_vs_pnl if c < 50]
    high_act = [p for c, p in leader_activity_vs_pnl if c >= 50]
    if low_act and high_act:
        low_total = sum(low_act)
        high_total = sum(high_act)
        print(f"     A. Skip low-activity hours (<50 trades): Lost ${low_total:+.1f} from {len(low_act)} hours")
        print(f"        Keep high-activity hours: ${high_total:+.1f} from {len(high_act)} hours")

    # Idea B: Best hours of day only
    best_hours = sorted(hour_of_day_pnls.keys(), key=lambda h: sum(hour_of_day_pnls[h]), reverse=True)
    top_half_pnl = sum(sum(hour_of_day_pnls[h]) for h in best_hours[:len(best_hours)//2])
    bot_half_pnl = sum(sum(hour_of_day_pnls[h]) for h in best_hours[len(best_hours)//2:])
    top_half_count = sum(len(hour_of_day_pnls[h]) for h in best_hours[:len(best_hours)//2])
    bot_half_count = sum(len(hour_of_day_pnls[h]) for h in best_hours[len(best_hours)//2:])
    print(f"     B. Top-half UTC hours only: ${top_half_pnl:+.1f} from {top_half_count}h, skip ${bot_half_pnl:+.1f} from {bot_half_count}h")

    # Idea C: Increase position size on high-confidence entries (>0.70)
    high_conf = [e for e in all_entries if e["entry"] >= 0.70]
    mid_conf = [e for e in all_entries if 0.50 <= e["entry"] < 0.70]
    if high_conf and mid_conf:
        hc_wr = sum(1 for e in high_conf if e["won"]) / len(high_conf) * 100
        mc_wr = sum(1 for e in mid_conf if e["won"]) / len(mid_conf) * 100
        hc_pnl = sum(e["total_pnl"] for e in high_conf)
        mc_pnl = sum(e["total_pnl"] for e in mid_conf)
        print(f"     C. High-confidence (>70c): WR={hc_wr:.0f}%, PnL=${hc_pnl:+.1f} | Mid (50-70c): WR={mc_wr:.0f}%, PnL=${mc_pnl:+.1f}")
        print(f"        If we 2x sized high-conf entries: +${hc_pnl:.0f} extra (rough estimate)")

    # Idea D: Multiple leaders
    print(f"     D. Multiple leaders: Diversification would reduce Sharpe variance")
    print(f"        Current: 1 leader, Sharpe 0.309")
    print(f"        With 2 uncorrelated leaders: Sharpe ~{0.309 * 1.41:.3f} (rough √2 scaling)")

    # Idea E: Tighter stops on losers
    loss_at_resolution = [e for e in all_entries if not e["won"]]
    if loss_at_resolution:
        avg_loss_entry = sum(e["entry"] for e in loss_at_resolution) / len(loss_at_resolution)
        print(f"     E. Losers avg entry: ${avg_loss_entry:.3f}, resolve at $0.01")
        print(f"        If we sold losers at $0.10 instead of $0.01: save ${sum(0.09*e['shares'] for e in loss_at_resolution):.0f}")


if __name__ == "__main__":
    main()
