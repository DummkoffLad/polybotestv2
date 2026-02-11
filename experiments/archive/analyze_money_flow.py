"""Comprehensive money flow analysis: WHERE is the money going?

Questions to answer:
1. How much of the leader's trades do we MISS vs TAKE?
2. For trades we take: where do we make/lose money?
3. Leader's mid-hour selling on low entries: does he buy low and sell on upswings?
4. What's our average position size vs what it COULD be?
5. Per-hour breakdown: winning vs losing hours, WHY do losing hours lose?
6. Are we under-deployed? How much cash sits idle?
"""
import sys
import json
from pathlib import Path
from decimal import Decimal
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer
from src.data.models import PriceSnapshot
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
            except Exception:
                continue
    return -1


def analyze_hour(hour_file):
    """Deep analysis of one hour: what the leader does, what we do, where money goes."""
    # Parse raw JSONL for leader behavior
    leader_trades_raw = []
    with open(hour_file, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") == "leader_trade":
                t = obj.get("leader_trade", {})
                ts_str = t.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                except Exception:
                    continue
                leader_trades_raw.append({
                    "timestamp": ts,
                    "token_id": t.get("token_id", ""),
                    "action": t.get("action", ""),
                    "price": Decimal(str(t.get("leader_price", 0))),
                    "dollars": Decimal(str(t.get("leader_dollars", 0))),
                    "shares": Decimal(str(t.get("leader_shares", 0))),
                    "ask": Decimal(str(t.get("ask", 0))) if t.get("ask") else None,
                    "bid": Decimal(str(t.get("bid", 0))) if t.get("bid") else None,
                    "minute": ts.minute,
                })

    if not leader_trades_raw:
        return None

    # Run strategy simulation
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except Exception:
        return None
    if count == 0:
        return None

    config = replayer._merge_config()
    strategy.initialize(StrategyConfig.from_dict(config))
    strategy.on_session_start()

    # Track our decisions and fills
    our_actions = []  # (event, decision, skip_reason)
    last_hour = None

    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour

        decision = strategy.on_event(event)
        skip_reason = decision.skip_reason if hasattr(decision, 'skip_reason') else None

        our_actions.append({
            "token_id": event.trade.token_id,
            "action": event.trade.action.value,
            "leader_dollars": float(event.trade.dollars),
            "leader_price": float(event.trade.price),
            "ask": float(event.prices.ask) if event.prices.ask else None,
            "bid": float(event.prices.bid) if event.prices.bid else None,
            "minute": event.trade.timestamp.minute,
            "our_action": decision.action.value,
            "our_dollars": float(decision.dollars) if decision.dollars else 0,
            "our_price": float(decision.price) if decision.price else 0,
            "our_shares": float(decision.shares) if decision.shares else 0,
            "skip_reason": skip_reason,
        })

        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    # Resolution
    end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}
    resolution_details = []
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        ps = end_prices.get(token_id)
        if ps and ps.bid is not None:
            last_bid = ps.bid
        else:
            last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
        res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
        dollars = pos.shares * res_price
        entry = strategy.our_entries.get(token_id, Decimal("0"))
        pnl = float(dollars - pos.cost_basis)
        resolution_details.append({
            "token_id": token_id,
            "entry": float(entry),
            "last_bid": float(last_bid),
            "res_price": float(res_price),
            "shares": float(pos.shares),
            "cost_basis": float(pos.cost_basis),
            "pnl": pnl,
            "won": res_price == Decimal("0.99"),
        })
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars

    hour_pnl = float(strategy.cash) - 100.0

    return {
        "leader_trades": leader_trades_raw,
        "our_actions": our_actions,
        "resolution": resolution_details,
        "hour_pnl": round(hour_pnl, 2),
        "buys": strategy.buys,
        "sells": strategy.sells,
        "skip_reasons": dict(strategy.skip_reasons),
    }


def analyze_leader_swing_trades(leader_trades_raw):
    """Analyze leader's mid-hour swing trading: buy low, sell on upswing."""
    by_token = defaultdict(list)
    for t in leader_trades_raw:
        by_token[t["token_id"]].append(t)

    swing_trades = []
    for token_id, trades in by_token.items():
        buys = [t for t in trades if t["action"] == "BUY"]
        sells = [t for t in trades if t["action"] == "SELL"]
        if not buys:
            continue

        total_buy_dollars = sum(float(t["dollars"]) for t in buys)
        total_buy_shares = sum(float(t["shares"]) for t in buys)
        avg_buy_price = total_buy_dollars / total_buy_shares if total_buy_shares > 0 else 0

        sell_details = []
        total_sell_dollars = 0
        total_sell_shares = 0
        for s in sells:
            sell_price = float(s["price"])
            sell_dollars = float(s["dollars"])
            sell_shares = float(s["shares"])
            profit_per_share = sell_price - avg_buy_price
            sell_pnl = sell_shares * profit_per_share
            sell_details.append({
                "minute": s["minute"],
                "price": sell_price,
                "shares": sell_shares,
                "dollars": sell_dollars,
                "pnl": sell_pnl,
                "bid_at_sell": float(s["bid"]) if s["bid"] else None,
            })
            total_sell_dollars += sell_dollars
            total_sell_shares += sell_shares

        pct_sold = total_sell_shares / total_buy_shares * 100 if total_buy_shares > 0 else 0
        total_sell_pnl = sum(d["pnl"] for d in sell_details)
        avg_sell_price = total_sell_dollars / total_sell_shares if total_sell_shares > 0 else 0

        # Was this a profitable swing trade?
        swing_profitable = avg_sell_price > avg_buy_price

        swing_trades.append({
            "token_id": token_id[-8:],
            "avg_buy_price": avg_buy_price,
            "avg_sell_price": avg_sell_price,
            "total_buy_dollars": total_buy_dollars,
            "total_sell_dollars": total_sell_dollars,
            "pct_sold": pct_sold,
            "sell_pnl": total_sell_pnl,
            "swing_profitable": swing_profitable,
            "num_buys": len(buys),
            "num_sells": len(sells),
            "sell_details": sell_details,
            "first_buy_min": buys[0]["minute"],
            "buy_price_range": (float(min(t["price"] for t in buys)),
                               float(max(t["price"] for t in buys))),
        })

    return swing_trades


def main():
    base = Path("data/sessions")
    all_hours = []

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                all_hours.append((session, utc_h, hf))

    print(f"Analyzing {len(all_hours)} hours...\n")

    # Aggregate stats
    total_leader_buys = 0
    total_leader_sells = 0
    our_buys_taken = 0
    our_buys_skipped = 0
    our_sells_taken = 0
    our_sells_skipped = 0
    skip_reason_counts = defaultdict(int)

    # Money tracking
    total_our_buy_dollars = 0
    total_our_sell_dollars = 0
    total_resolution_pnl = 0
    total_pnl = 0

    # Per-entry-price analysis
    entry_price_buckets = defaultdict(lambda: {"count": 0, "buy_dollars": 0, "sell_pnl": 0,
                                                "res_pnl": 0, "total_pnl": 0, "wins": 0, "losses": 0})
    # Leader swing trade analysis
    all_leader_swings = []
    leader_swing_by_price = defaultdict(lambda: {"count": 0, "profitable": 0, "sell_pnl": 0,
                                                  "avg_sell_price": 0, "avg_buy_price": 0})

    # Missed trade analysis
    missed_buys = []  # leader buys we didn't follow
    taken_buys = []   # leader buys we followed

    # Hour-level stats
    hour_results = []
    max_deployed_per_hour = []
    idle_cash_per_hour = []

    for session, utc_h, hf in all_hours:
        result = analyze_hour(hf)
        if not result:
            continue

        hour_pnl = result["hour_pnl"]
        total_pnl += hour_pnl
        hour_results.append(hour_pnl)

        # Track our trades and skips
        token_our_buys = {}  # token_id -> list of our buys
        token_our_sells = {}  # token_id -> list of our sells

        for action in result["our_actions"]:
            if action["action"] == "BUY":
                total_leader_buys += 1
                if action["our_action"] == "BUY":
                    our_buys_taken += 1
                    total_our_buy_dollars += action["our_dollars"]
                    taken_buys.append(action)
                    if action["token_id"] not in token_our_buys:
                        token_our_buys[action["token_id"]] = []
                    token_our_buys[action["token_id"]].append(action)
                else:
                    our_buys_skipped += 1
                    if action["skip_reason"]:
                        skip_reason_counts[action["skip_reason"]] += 1
                    missed_buys.append(action)
            elif action["action"] == "SELL":
                total_leader_sells += 1
                if action["our_action"] == "SELL":
                    our_sells_taken += 1
                    total_our_sell_dollars += action["our_dollars"]
                    if action["token_id"] not in token_our_sells:
                        token_our_sells[action["token_id"]] = []
                    token_our_sells[action["token_id"]].append(action)
                else:
                    our_sells_skipped += 1
                    if action["skip_reason"]:
                        skip_reason_counts[action["skip_reason"]] += 1

        # Resolution analysis
        for res in result["resolution"]:
            total_resolution_pnl += res["pnl"]
            bucket = f"{res['entry']:.1f}"
            b = entry_price_buckets[bucket]
            b["count"] += 1
            b["res_pnl"] += res["pnl"]
            b["total_pnl"] += res["pnl"]
            if res["won"]:
                b["wins"] += 1
            else:
                b["losses"] += 1

        # Leader swing trade analysis
        swings = analyze_leader_swing_trades(result["leader_trades"])
        all_leader_swings.extend(swings)
        for swing in swings:
            price = swing["avg_buy_price"]
            if price < 0.25:
                key = "<0.25"
            elif price < 0.35:
                key = "0.25-0.35"
            elif price < 0.45:
                key = "0.35-0.45"
            elif price < 0.55:
                key = "0.45-0.55"
            elif price < 0.65:
                key = "0.55-0.65"
            elif price < 0.75:
                key = "0.65-0.75"
            elif price < 0.85:
                key = "0.75-0.85"
            else:
                key = "0.85+"
            b = leader_swing_by_price[key]
            b["count"] += 1
            if swing["swing_profitable"]:
                b["profitable"] += 1
            b["sell_pnl"] += swing["sell_pnl"]

    # =====================================================================
    # DISPLAY RESULTS
    # =====================================================================
    print(f"{'='*130}")
    print(f"  MONEY FLOW ANALYSIS ({len(hour_results)} hours, PnL = ${total_pnl:+.2f})")
    print(f"{'='*130}")

    # 1. Trade coverage
    print(f"\n  1. TRADE COVERAGE (what we follow vs skip):")
    print(f"     Leader buys:  {total_leader_buys}")
    print(f"     We bought:    {our_buys_taken} ({our_buys_taken/max(1,total_leader_buys)*100:.1f}%)")
    print(f"     We skipped:   {our_buys_skipped} ({our_buys_skipped/max(1,total_leader_buys)*100:.1f}%)")
    print(f"     Leader sells: {total_leader_sells}")
    print(f"     We sold:      {our_sells_taken} ({our_sells_taken/max(1,total_leader_sells)*100:.1f}%)")
    print(f"     We skipped:   {our_sells_skipped} ({our_sells_skipped/max(1,total_leader_sells)*100:.1f}%)")
    print(f"\n     Skip reasons (top 10):")
    sorted_reasons = sorted(skip_reason_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    for reason, count in sorted_reasons:
        print(f"       {reason:<30}: {count:>5}")

    # 2. Money deployed
    total_mid_sell_pnl = total_pnl - total_resolution_pnl
    print(f"\n  2. MONEY FLOW:")
    print(f"     Total bought:       ${total_our_buy_dollars:>8.2f}")
    print(f"     Total sold mid-hr:  ${total_our_sell_dollars:>8.2f}")
    print(f"     Mid-hour sell PnL:  ${total_mid_sell_pnl:>+8.2f}")
    print(f"     Resolution PnL:     ${total_resolution_pnl:>+8.2f}")
    print(f"     TOTAL PnL:          ${total_pnl:>+8.2f}")
    if total_our_buy_dollars > 0:
        print(f"     Return on deployed: {total_pnl/total_our_buy_dollars*100:>+6.2f}%")
    print(f"     Avg buy per hour:   ${total_our_buy_dollars/max(1,len(hour_results)):>8.2f}")

    # 3. What we're missing
    print(f"\n  3. MISSED BUYS ANALYSIS:")
    missed_by_price = defaultdict(lambda: {"count": 0, "dollars": 0})
    for mb in missed_buys:
        if mb["ask"] is None:
            continue
        if mb["ask"] < 0.25:
            key = "<0.25"
        elif mb["ask"] < 0.35:
            key = "0.25-0.35"
        elif mb["ask"] < 0.45:
            key = "0.35-0.45"
        elif mb["ask"] < 0.55:
            key = "0.45-0.55"
        elif mb["ask"] < 0.65:
            key = "0.55-0.65"
        elif mb["ask"] < 0.75:
            key = "0.65-0.75"
        elif mb["ask"] < 0.85:
            key = "0.75-0.85"
        else:
            key = "0.85+"
        missed_by_price[key]["count"] += 1
        missed_by_price[key]["dollars"] += mb["leader_dollars"]

    taken_by_price = defaultdict(lambda: {"count": 0, "dollars": 0})
    for tb in taken_buys:
        if tb["ask"] is None:
            continue
        if tb["ask"] < 0.25:
            key = "<0.25"
        elif tb["ask"] < 0.35:
            key = "0.25-0.35"
        elif tb["ask"] < 0.45:
            key = "0.35-0.45"
        elif tb["ask"] < 0.55:
            key = "0.45-0.55"
        elif tb["ask"] < 0.65:
            key = "0.55-0.65"
        elif tb["ask"] < 0.75:
            key = "0.65-0.75"
        elif tb["ask"] < 0.85:
            key = "0.75-0.85"
        else:
            key = "0.85+"
        taken_by_price[key]["count"] += 1
        taken_by_price[key]["dollars"] += tb["our_dollars"]

    all_keys = sorted(set(list(missed_by_price.keys()) + list(taken_by_price.keys())))
    print(f"     {'Price':>10} | {'Taken':>6} {'OurAvg$':>8} | {'Missed':>6} {'LdrAvg$':>8} | {'Take%':>6}")
    for key in all_keys:
        t = taken_by_price[key]
        m = missed_by_price[key]
        total = t["count"] + m["count"]
        take_pct = t["count"] / total * 100 if total > 0 else 0
        t_avg = t["dollars"] / t["count"] if t["count"] > 0 else 0
        m_avg = m["dollars"] / m["count"] if m["count"] > 0 else 0
        print(f"     {key:>10} | {t['count']:>6} ${t_avg:>6.1f} | {m['count']:>6} ${m_avg:>6.1f} | {take_pct:>5.1f}%")

    # 4. Missed buys by skip reason and price
    print(f"\n  4. WHY WE MISS BUYS (by price range):")
    missed_reason_by_price = defaultdict(lambda: defaultdict(int))
    for mb in missed_buys:
        if mb["ask"] is None or mb["skip_reason"] is None:
            continue
        if mb["ask"] < 0.45:
            key = "<0.45"
        elif mb["ask"] < 0.65:
            key = "0.45-0.65"
        elif mb["ask"] < 0.85:
            key = "0.65-0.85"
        else:
            key = "0.85+"
        missed_reason_by_price[key][mb["skip_reason"]] += 1

    for key in sorted(missed_reason_by_price.keys()):
        reasons = missed_reason_by_price[key]
        top_reasons = sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:5]
        print(f"     {key}:")
        for reason, count in top_reasons:
            print(f"       {reason:<28}: {count:>4}")

    # 5. LEADER SWING TRADE ANALYSIS
    print(f"\n  5. LEADER SWING TRADES (buy low, sell on upswing):")
    print(f"     {'Price Range':>12} | {'Count':>5} | {'Profitable':>10} | {'WR':>5} | {'Sell PnL':>10} | {'Avg PnL/trade':>13}")
    for key in ["<0.25", "0.25-0.35", "0.35-0.45", "0.45-0.55", "0.55-0.65", "0.65-0.75", "0.75-0.85", "0.85+"]:
        b = leader_swing_by_price.get(key)
        if not b or b["count"] == 0:
            continue
        wr = b["profitable"] / b["count"] * 100
        avg_pnl = b["sell_pnl"] / b["count"]
        print(f"     {key:>12} | {b['count']:>5} | {b['profitable']:>10} | {wr:>4.0f}% | ${b['sell_pnl']:>+8.0f} | ${avg_pnl:>+10.1f}")

    # Show specific examples of profitable low swings
    low_swings = [s for s in all_leader_swings if s["avg_buy_price"] < 0.45]
    profitable_low = [s for s in low_swings if s["swing_profitable"] and s["sell_pnl"] > 50]
    if profitable_low:
        profitable_low.sort(key=lambda x: x["sell_pnl"], reverse=True)
        print(f"\n     TOP PROFITABLE LOW-PRICE SWINGS (buy < $0.45, sell PnL > $50):")
        print(f"     {'Token':>10} | {'AvgBuy':>6} | {'AvgSell':>7} | {'%Sold':>5} | {'SellPnL':>8} | {'Buys':>4} | {'Sells':>5} | {'FirstMin':>8}")
        for s in profitable_low[:15]:
            print(f"     {s['token_id']:>10} | ${s['avg_buy_price']:.3f} | ${s['avg_sell_price']:.3f} | {s['pct_sold']:>4.0f}% | ${s['sell_pnl']:>+6.0f} | {s['num_buys']:>4} | {s['num_sells']:>5} | min {s['first_buy_min']:>2}")

    # Show examples of LOSING low swings
    losing_low = [s for s in low_swings if not s["swing_profitable"] and s["sell_pnl"] < -50]
    if losing_low:
        losing_low.sort(key=lambda x: x["sell_pnl"])
        print(f"\n     TOP LOSING LOW-PRICE SWINGS (buy < $0.45, sell PnL < -$50):")
        print(f"     {'Token':>10} | {'AvgBuy':>6} | {'AvgSell':>7} | {'%Sold':>5} | {'SellPnL':>8} | {'Buys':>4} | {'Sells':>5} | {'FirstMin':>8}")
        for s in losing_low[:15]:
            print(f"     {s['token_id']:>10} | ${s['avg_buy_price']:.3f} | ${s['avg_sell_price']:.3f} | {s['pct_sold']:>4.0f}% | ${s['sell_pnl']:>+6.0f} | {s['num_buys']:>4} | {s['num_sells']:>5} | min {s['first_buy_min']:>2}")

    # 6. Sell details for ALL price ranges
    print(f"\n  6. LEADER MID-HOUR SELL BEHAVIOR (does he sell higher than he buys?):")
    for key in ["<0.25", "0.25-0.35", "0.35-0.45", "0.45-0.55", "0.55-0.65", "0.65-0.75", "0.75-0.85", "0.85+"]:
        group = [s for s in all_leader_swings if
                 (key == "<0.25" and s["avg_buy_price"] < 0.25) or
                 (key == "0.25-0.35" and 0.25 <= s["avg_buy_price"] < 0.35) or
                 (key == "0.35-0.45" and 0.35 <= s["avg_buy_price"] < 0.45) or
                 (key == "0.45-0.55" and 0.45 <= s["avg_buy_price"] < 0.55) or
                 (key == "0.55-0.65" and 0.55 <= s["avg_buy_price"] < 0.65) or
                 (key == "0.65-0.75" and 0.65 <= s["avg_buy_price"] < 0.75) or
                 (key == "0.75-0.85" and 0.75 <= s["avg_buy_price"] < 0.85) or
                 (key == "0.85+" and s["avg_buy_price"] >= 0.85)]
        if not group:
            continue
        with_sells = [s for s in group if s["num_sells"] > 0]
        if not with_sells:
            continue
        avg_buy = sum(s["avg_buy_price"] for s in with_sells) / len(with_sells)
        avg_sell = sum(s["avg_sell_price"] for s in with_sells) / len(with_sells)
        avg_spread = avg_sell - avg_buy
        total_sell_pnl = sum(s["sell_pnl"] for s in with_sells)
        profitable = sum(1 for s in with_sells if s["swing_profitable"])
        wr = profitable / len(with_sells) * 100

        print(f"     {key:>10}: {len(with_sells):>3} trades | buy=${avg_buy:.3f} sell=${avg_sell:.3f} spread=${avg_spread:+.3f} | WR={wr:.0f}% | sell_PnL=${total_sell_pnl:+.0f}")

    # 7. Our position sizing
    print(f"\n  7. OUR POSITION SIZING:")
    if taken_buys:
        sizes = [tb["our_dollars"] for tb in taken_buys]
        prices = [tb["ask"] for tb in taken_buys if tb["ask"]]
        print(f"     Average buy:    ${sum(sizes)/len(sizes):.2f}")
        print(f"     Median buy:     ${sorted(sizes)[len(sizes)//2]:.2f}")
        print(f"     Max buy:        ${max(sizes):.2f}")
        print(f"     Min buy:        ${min(sizes):.2f}")
        print(f"     Total buys:     {len(sizes)}")
        print(f"     Total deployed: ${sum(sizes):.2f}")
        print(f"     Avg per hour:   ${sum(sizes)/max(1,len(hour_results)):.2f}")
        print(f"     Max per hour cap: $45 budget, $50 deploy")

    # 8. Hour distribution
    print(f"\n  8. HOUR PnL DISTRIBUTION:")
    if hour_results:
        winning_hours = [h for h in hour_results if h > 0]
        losing_hours = [h for h in hour_results if h < 0]
        zero_hours = [h for h in hour_results if h == 0]
        print(f"     Winning hours: {len(winning_hours)} (avg ${sum(winning_hours)/max(1,len(winning_hours)):+.2f})")
        print(f"     Losing hours:  {len(losing_hours)} (avg ${sum(losing_hours)/max(1,len(losing_hours)):+.2f})")
        print(f"     Zero hours:    {len(zero_hours)}")
        print(f"     Worst 5 hours: {', '.join(f'${h:+.1f}' for h in sorted(hour_results)[:5])}")
        print(f"     Best 5 hours:  {', '.join(f'${h:+.1f}' for h in sorted(hour_results, reverse=True)[:5])}")

    # 9. Summary: where is the money?
    print(f"\n  9. MONEY SUMMARY:")
    print(f"     Total PnL: ${total_pnl:+.2f}")
    print(f"     From mid-hour sells: ${total_mid_sell_pnl:+.2f}")
    print(f"     From resolution: ${total_resolution_pnl:+.2f}")
    if total_pnl != 0:
        print(f"     % from sells: {total_mid_sell_pnl/abs(total_pnl)*100:.1f}%")
        print(f"     % from resolution: {total_resolution_pnl/abs(total_pnl)*100:.1f}%")


if __name__ == "__main__":
    main()
