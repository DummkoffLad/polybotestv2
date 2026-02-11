"""Analyze WHICH leader trades are profitable and which lose money.

Groups trades by:
1. Entry price bucket (where we buy)
2. Leader trade size
3. Whether it's leader's first or subsequent buy on that token
4. Resolution outcome

Goal: find the signal that predicts winners vs losers.
"""
import sys
import json
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


def analyze_hour_trades(hour_file):
    """Run strategy and track every BUY decision with its outcome."""
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return []
    if count == 0:
        return []

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    # Track leader buy counts per token (to detect first vs subsequent)
    leader_buy_counts = defaultdict(int)
    last_all_prices = {}
    trades = []

    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        last_all_prices = all_prices
        event.context['all_prices'] = all_prices

        trade = event.trade

        # Track leader buys per token
        if trade.action.value == "BUY":
            leader_buy_counts[trade.token_id] += 1

        decision = strategy.on_event(event)

        if decision.action == DecisionAction.BUY:
            entry_price = float(decision.price or event.prices.ask or 0)
            leader_dollars = float(trade.dollars)
            leader_price = float(trade.price)
            our_dollars = float(decision.dollars or 0)
            minute = trade.timestamp.minute
            leader_nth_buy = leader_buy_counts[trade.token_id]

            strategy.on_fill(event, decision)

            trades.append({
                "token_id": trade.token_id,
                "entry_price": entry_price,
                "leader_dollars": leader_dollars,
                "leader_price": leader_price,
                "our_dollars": our_dollars,
                "our_shares": float(decision.shares or 0),
                "minute": minute,
                "leader_nth_buy": leader_nth_buy,
                "side": trade.side.value if hasattr(trade.side, 'value') else str(trade.side),
            })
        elif decision.action == DecisionAction.SELL:
            strategy.on_fill(event, decision)

    # Now resolve: check outcome for each bought token
    results = []
    for t in trades:
        pos = strategy.portfolio.get_positions().get(t["token_id"])
        price_snap = last_all_prices.get(t["token_id"])

        if price_snap and price_snap.bid and price_snap.bid > 0:
            last_bid = float(price_snap.bid)
        else:
            last_bid = t["entry_price"]

        won = last_bid >= 0.50
        res_price = 0.99 if won else 0.01

        # Our PnL for this specific buy
        pnl_per_share = res_price - t["entry_price"]
        our_pnl = pnl_per_share * t["our_shares"]

        results.append({
            **t,
            "last_bid": last_bid,
            "won": won,
            "res_price": res_price,
            "pnl": round(our_pnl, 2),
        })

    return results


def price_bucket(price):
    if price < 0.20:
        return "0.03-0.20"
    elif price < 0.35:
        return "0.20-0.35"
    elif price < 0.50:
        return "0.35-0.50"
    elif price < 0.65:
        return "0.50-0.65"
    elif price < 0.80:
        return "0.65-0.80"
    else:
        return "0.80-0.97"


def leader_size_bucket(dollars):
    if dollars < 10:
        return "small(<$10)"
    elif dollars < 30:
        return "med($10-30)"
    elif dollars < 60:
        return "large($30-60)"
    else:
        return "xlarge($60+)"


def main():
    base = Path("data/sessions")
    all_trades = []

    # Define train/test split
    TRAIN_SESSIONS = {"2026-02-03/05-56", "2026-02-04/02-35", "2026-02-06/05-30"}
    TEST_SESSIONS = {"2026-02-05/03-58", "2026-02-05/22-15", "2026-02-07/05-52"}

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h < 0:
                continue
            session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"

            trades = analyze_hour_trades(hf)
            for t in trades:
                t["session"] = session
                t["utc_hour"] = utc_h
                t["split"] = "TRAIN" if session in TRAIN_SESSIONS else "TEST"
            all_trades.extend(trades)

    if not all_trades:
        print("No trades found!")
        return

    train = [t for t in all_trades if t["split"] == "TRAIN"]
    test = [t for t in all_trades if t["split"] == "TEST"]

    print(f"{'='*100}")
    print(f"  TRADE OUTCOME ANALYSIS ({len(all_trades)} executed buys)")
    print(f"  TRAIN: {len(train)} trades | TEST: {len(test)} trades")
    print(f"{'='*100}")

    def print_analysis(trades, label):
        wins = [t for t in trades if t["won"]]
        losses = [t for t in trades if not t["won"]]
        total_pnl = sum(t["pnl"] for t in trades)
        print(f"\n  [{label}] {len(trades)} trades: {len(wins)}W/{len(losses)}L ({len(wins)/len(trades)*100:.1f}% WR)")
        print(f"  Total PnL: ${total_pnl:+.2f}, Avg: ${total_pnl/len(trades):+.2f}/trade")

        # By price bucket
        print(f"\n    BY ENTRY PRICE:")
        print(f"    {'Bucket':<12} | {'Trades':>6} {'Wins':>5} {'WR':>5} | {'TotalPnL':>9} {'AvgPnL':>8} | {'AvgOur$':>7}")
        by_price = defaultdict(list)
        for t in trades:
            by_price[price_bucket(t["entry_price"])].append(t)

        for bucket in ["0.03-0.20", "0.20-0.35", "0.35-0.50", "0.50-0.65", "0.65-0.80", "0.80-0.97"]:
            trs = by_price.get(bucket, [])
            if not trs:
                continue
            w = sum(1 for t in trs if t["won"])
            pnl = sum(t["pnl"] for t in trs)
            avg_our = sum(t["our_dollars"] for t in trs) / len(trs)
            wr = w / len(trs) * 100
            avg_pnl = pnl / len(trs)
            marker = " ***" if wr > 55 else (" !!!" if wr < 40 else "")
            print(f"    {bucket:<12} | {len(trs):>6} {w:>5} {wr:>4.1f}% | ${pnl:>+7.2f} ${avg_pnl:>+6.2f} | ${avg_our:>5.2f}{marker}")

        # By leader trade size
        print(f"\n    BY LEADER TRADE SIZE:")
        print(f"    {'Bucket':<14} | {'Trades':>6} {'Wins':>5} {'WR':>5} | {'TotalPnL':>9} {'AvgPnL':>8}")
        by_size = defaultdict(list)
        for t in trades:
            by_size[leader_size_bucket(t["leader_dollars"])].append(t)

        for bucket in ["small(<$10)", "med($10-30)", "large($30-60)", "xlarge($60+)"]:
            trs = by_size.get(bucket, [])
            if not trs:
                continue
            w = sum(1 for t in trs if t["won"])
            pnl = sum(t["pnl"] for t in trs)
            wr = w / len(trs) * 100
            avg_pnl = pnl / len(trs)
            marker = " ***" if wr > 55 else (" !!!" if wr < 40 else "")
            print(f"    {bucket:<14} | {len(trs):>6} {w:>5} {wr:>4.1f}% | ${pnl:>+7.2f} ${avg_pnl:>+6.2f}{marker}")

        # By leader nth buy (first vs subsequent)
        print(f"\n    BY LEADER CONVICTION (nth buy on same token):")
        print(f"    {'NthBuy':<14} | {'Trades':>6} {'Wins':>5} {'WR':>5} | {'TotalPnL':>9} {'AvgPnL':>8}")
        by_nth = defaultdict(list)
        for t in trades:
            key = "1st buy" if t["leader_nth_buy"] == 1 else "2nd buy" if t["leader_nth_buy"] == 2 else "3rd+" if t["leader_nth_buy"] >= 3 else "?"
            by_nth[key].append(t)

        for key in ["1st buy", "2nd buy", "3rd+"]:
            trs = by_nth.get(key, [])
            if not trs:
                continue
            w = sum(1 for t in trs if t["won"])
            pnl = sum(t["pnl"] for t in trs)
            wr = w / len(trs) * 100
            avg_pnl = pnl / len(trs)
            marker = " ***" if wr > 55 else (" !!!" if wr < 40 else "")
            print(f"    {key:<14} | {len(trs):>6} {w:>5} {wr:>4.1f}% | ${pnl:>+7.2f} ${avg_pnl:>+6.2f}{marker}")

        # By minute of hour
        print(f"\n    BY MINUTE OF HOUR:")
        print(f"    {'Window':<14} | {'Trades':>6} {'Wins':>5} {'WR':>5} | {'TotalPnL':>9} {'AvgPnL':>8}")
        by_min = defaultdict(list)
        for t in trades:
            if t["minute"] < 15:
                key = "0-14min"
            elif t["minute"] < 30:
                key = "15-29min"
            elif t["minute"] < 45:
                key = "30-44min"
            else:
                key = "45-59min"
            by_min[key].append(t)

        for key in ["0-14min", "15-29min", "30-44min", "45-59min"]:
            trs = by_min.get(key, [])
            if not trs:
                continue
            w = sum(1 for t in trs if t["won"])
            pnl = sum(t["pnl"] for t in trs)
            wr = w / len(trs) * 100
            avg_pnl = pnl / len(trs)
            marker = " ***" if wr > 55 else (" !!!" if wr < 40 else "")
            print(f"    {key:<14} | {len(trs):>6} {w:>5} {wr:>4.1f}% | ${pnl:>+7.2f} ${avg_pnl:>+6.2f}{marker}")

        # COMBINED: best and worst combos
        print(f"\n    TOP 5 BEST COMBOS (price x size):")
        combos = defaultdict(list)
        for t in trades:
            key = f"{price_bucket(t['entry_price'])} x {leader_size_bucket(t['leader_dollars'])}"
            combos[key].append(t)

        ranked = []
        for key, trs in combos.items():
            if len(trs) < 3:
                continue
            w = sum(1 for t in trs if t["won"])
            pnl = sum(t["pnl"] for t in trs)
            wr = w / len(trs) * 100
            ranked.append((pnl / len(trs), key, len(trs), w, wr, pnl))

        ranked.sort(reverse=True)
        for avg, key, n, w, wr, pnl in ranked[:5]:
            print(f"    {key:<35} {n:>4} trades, {wr:>4.1f}% WR, ${pnl:>+7.2f} total, ${avg:>+5.2f}/trade")

        print(f"\n    TOP 5 WORST COMBOS (price x size):")
        for avg, key, n, w, wr, pnl in ranked[-5:]:
            print(f"    {key:<35} {n:>4} trades, {wr:>4.1f}% WR, ${pnl:>+7.2f} total, ${avg:>+5.2f}/trade")

    print_analysis(train, "TRAIN")
    print(f"\n{'='*100}")
    print_analysis(test, "TEST")


if __name__ == "__main__":
    main()
