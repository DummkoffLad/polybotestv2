"""Deep analysis of leader behavior hour by hour.

For every hour: show leader's FULL trading activity (buys, sells, timing, prices)
alongside our PnL. Focus on our worst hours — what did the leader do differently?

Key questions:
1. In our worst hours, did the leader ALSO lose? Or did they hedge and survive?
2. When the leader sells mid-hour, what happens next? Does selling predict outcomes?
3. Is there a signal in leader's selling pattern that we could use?
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
import src.strategies.profit_taker.strategy as pt_mod

TRAIN_SESSIONS = ["2026-02-03/05-56", "2026-02-04/02-35", "2026-02-06/05-30"]
TEST_SESSIONS = ["2026-02-05/03-58", "2026-02-05/22-15", "2026-02-07/05-52"]
HOLDOUT_SESSIONS = ["2026-02-08/06-39"]

CONFIG = {
    "scaling.our_capital": 50, "scaling.hourly_budget": 45,
    "scaling.k_factor": 1, "scaling.leader_estimated_capital": 900,
}


def get_split(session_name):
    for s in TRAIN_SESSIONS:
        if s in session_name:
            return "TRAIN"
    for s in TEST_SESSIONS:
        if s in session_name:
            return "TEST"
    for s in HOLDOUT_SESSIONS:
        if s in session_name:
            return "HOLDOUT"
    return "UNKNOWN"


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


def parse_full_hour(hour_file):
    """Parse EVERYTHING from one hour: leader trades, prices, our strategy."""
    leader_trades = []
    last_prices = {}

    with open(hour_file, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue

            if obj.get("type") == "price_snapshot":
                for token_id, pdata in obj.get("prices", {}).items():
                    bid = pdata.get("bid")
                    if bid is not None:
                        last_prices[token_id] = Decimal(str(bid))

            if obj.get("type") == "leader_trade":
                lt = obj.get("leader_trade", {})
                pc = obj.get("price_context", {})
                token_id = lt.get("token_id", "")
                ts_str = lt.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                except Exception:
                    continue

                if pc:
                    bid = pc.get("bid")
                    if bid is not None:
                        last_prices[token_id] = Decimal(str(bid))

                leader_trades.append({
                    "ts": ts, "minute": ts.minute, "second": ts.second,
                    "token": token_id, "token_short": token_id[-8:],
                    "action": lt.get("action", ""),
                    "price": Decimal(str(lt.get("leader_price", 0))),
                    "dollars": Decimal(str(lt.get("leader_dollars", 0))),
                    "shares": Decimal(str(lt.get("leader_shares", 0))),
                    "side": lt.get("side", ""),
                    "ask": Decimal(str(pc.get("ask", 0))) if pc.get("ask") else None,
                    "bid": Decimal(str(pc.get("bid", 0))) if pc.get("bid") else None,
                })

    # Compute leader PnL with full detail
    positions = {}
    sell_pnl = Decimal("0")
    buy_cost = Decimal("0")
    sell_proceeds = Decimal("0")
    per_token = defaultdict(lambda: {
        "buys": [], "sells": [], "total_buy_cost": Decimal("0"),
        "total_buy_shares": Decimal("0"), "total_sell_proceeds": Decimal("0"),
        "total_sell_shares": Decimal("0"), "remaining_shares": Decimal("0"),
        "remaining_cost": Decimal("0"), "entry_price": None, "side": "",
    })

    for t in leader_trades:
        tk = per_token[t["token"]]
        if t["action"] == "BUY":
            if t["token"] not in positions:
                positions[t["token"]] = {"shares": Decimal("0"), "cost": Decimal("0"),
                                         "entry": t["price"], "side": t["side"]}
            pos = positions[t["token"]]
            pos["shares"] += t["shares"]
            pos["cost"] += t["dollars"]
            buy_cost += t["dollars"]

            tk["buys"].append(t)
            tk["total_buy_cost"] += t["dollars"]
            tk["total_buy_shares"] += t["shares"]
            if tk["entry_price"] is None:
                tk["entry_price"] = t["price"]
            tk["side"] = t["side"]

        elif t["action"] == "SELL":
            pos = positions.get(t["token"])
            if pos and pos["shares"] > 0:
                ratio = min(t["shares"] / pos["shares"], Decimal("1"))
                cost_sold = pos["cost"] * ratio
                pos["cost"] -= cost_sold
                pos["shares"] = max(Decimal("0"), pos["shares"] - t["shares"])
                sell_pnl += t["dollars"] - cost_sold
            sell_proceeds += t["dollars"]

            tk["sells"].append(t)
            tk["total_sell_proceeds"] += t["dollars"]
            tk["total_sell_shares"] += t["shares"]

    # Resolution
    resolution_pnl = Decimal("0")
    for token_id, pos in positions.items():
        tk = per_token[token_id]
        tk["remaining_shares"] = pos["shares"]
        tk["remaining_cost"] = pos["cost"]
        if pos["shares"] <= 0:
            continue
        last_bid = last_prices.get(token_id, Decimal("0.50"))
        if last_bid >= Decimal("0.50"):
            res = Decimal("0.99")
            tk["resolution"] = "WIN"
        else:
            res = Decimal("0.01")
            tk["resolution"] = "LOSE"
        value = pos["shares"] * res
        tk["resolution_pnl"] = float(value - pos["cost"])
        resolution_pnl += value - pos["cost"]

    leader_total = sell_pnl + resolution_pnl

    # Run our strategy
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG)
    try:
        count = replayer.load()
    except Exception:
        return None
    if count == 0:
        return None

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour
        decision = strategy.on_event(event)
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}
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
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars

    our_pnl = float(strategy.cash) - 100.0

    return {
        "leader_trades": leader_trades,
        "per_token": dict(per_token),
        "leader_pnl": float(leader_total),
        "leader_sell_pnl": float(sell_pnl),
        "leader_res_pnl": float(resolution_pnl),
        "leader_buy_cost": float(buy_cost),
        "our_pnl": round(our_pnl, 2),
        "our_buys": strategy.buys,
        "our_sells": strategy.sells,
        "our_skips": dict(strategy.skip_reasons),
        "last_prices": last_prices,
    }


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
                all_hours.append((session, utc_h, hf, get_split(session)))

    print(f"Analyzing leader behavior across {len(all_hours)} hours...\n")

    hour_results = []
    # Aggregate: leader sells -> does the token win or lose at resolution?
    sell_then_win = 0
    sell_then_lose = 0
    sell_then_win_sell_pnl = 0.0
    sell_then_lose_sell_pnl = 0.0
    no_sell_win = 0
    no_sell_lose = 0
    # Leader sells > X% of position -> outcome
    big_sell_outcomes = defaultdict(lambda: {"win": 0, "lose": 0, "our_pnl_when_big_sell": []})

    for session, utc_h, hf, split in all_hours:
        result = parse_full_hour(hf)
        if not result:
            continue
        hour_results.append({
            "session": session, "hour": utc_h, "split": split,
            **result,
        })

        # Analyze: when leader sells, what happens at resolution?
        for token_id, tk in result["per_token"].items():
            res = tk.get("resolution", "NONE")
            has_sells = len(tk["sells"]) > 0
            pct_sold = (float(tk["total_sell_shares"]) / float(tk["total_buy_shares"]) * 100
                        if float(tk["total_buy_shares"]) > 0 else 0)

            if res == "NONE":
                continue  # Fully sold mid-hour

            if has_sells:
                if res == "WIN":
                    sell_then_win += 1
                    sell_then_win_sell_pnl += sum(float(s["dollars"]) for s in tk["sells"])
                else:
                    sell_then_lose += 1
                    sell_then_lose_sell_pnl += sum(float(s["dollars"]) for s in tk["sells"])
            else:
                if res == "WIN":
                    no_sell_win += 1
                else:
                    no_sell_lose += 1

            # Big sell analysis
            if pct_sold >= 30:
                big_sell_outcomes["sold_30%+"][res.lower()] += 1
            if pct_sold >= 50:
                big_sell_outcomes["sold_50%+"][res.lower()] += 1
            if pct_sold >= 70:
                big_sell_outcomes["sold_70%+"][res.lower()] += 1
            if pct_sold == 0:
                big_sell_outcomes["no_sells"][res.lower()] += 1

    # Sort by our PnL (worst first)
    hour_results.sort(key=lambda x: x["our_pnl"])

    # =====================================================================
    # SECTION 1: OUR WORST HOURS — detailed leader behavior
    # =====================================================================
    print(f"{'='*120}")
    print(f"  OUR 10 WORST HOURS — What did the leader do?")
    print(f"{'='*120}")

    for h in hour_results[:10]:
        print(f"\n  {h['session']} H{h['hour']:02d} ({h['split']}) | Us=${h['our_pnl']:>+.2f} | Leader=${h['leader_pnl']:>+.2f} (sell=${h['leader_sell_pnl']:>+.2f} res=${h['leader_res_pnl']:>+.2f})")
        print(f"  {'='*100}")

        # Show leader's token-by-token activity
        for token_id, tk in sorted(h["per_token"].items(),
                                    key=lambda x: float(x[1].get("resolution_pnl", 0))):
            res = tk.get("resolution", "SOLD")
            res_pnl = tk.get("resolution_pnl", 0)
            entry = float(tk["entry_price"]) if tk["entry_price"] else 0
            buys_total = float(tk["total_buy_cost"])
            sells_total = float(tk["total_sell_proceeds"])
            pct_sold = (float(tk["total_sell_shares"]) / float(tk["total_buy_shares"]) * 100
                        if float(tk["total_buy_shares"]) > 0 else 0)

            print(f"\n    Token ...{token_id[-8:]} ({tk['side']}) entry=${entry:.3f}")
            print(f"      Bought: ${buys_total:.2f} ({len(tk['buys'])} trades)")
            print(f"      Sold:   ${sells_total:.2f} ({len(tk['sells'])} trades, {pct_sold:.0f}% of shares)")
            print(f"      Remaining: {float(tk['remaining_shares']):.0f} shares, cost=${float(tk['remaining_cost']):.2f}")
            print(f"      Resolution: {res} (PnL ${res_pnl:+.2f})" if res != "SOLD" else "      Fully sold mid-hour")

            # Show individual trades timeline
            all_trades = [(t["minute"], t["second"], i, t) for i, t in enumerate(tk["buys"])] + \
                         [(t["minute"], t["second"], 1000+i, t) for i, t in enumerate(tk["sells"])]
            all_trades.sort(key=lambda x: (x[0], x[1], x[2]))

            for minute, second, _, t in all_trades:
                action = t["action"]
                price = float(t["price"])
                dollars = float(t["dollars"])
                shares = float(t["shares"])
                bid_str = f"bid={float(t['bid']):.3f}" if t.get("bid") else ""
                ask_str = f"ask={float(t['ask']):.3f}" if t.get("ask") else ""
                print(f"        min {minute:>2}:{second:02d} {action:>4} ${dollars:>7.2f} @ ${price:.3f} ({shares:.0f}sh) {bid_str} {ask_str}")

    # =====================================================================
    # SECTION 2: OUR BEST HOURS — What did leader do there?
    # =====================================================================
    print(f"\n{'='*120}")
    print(f"  OUR 5 BEST HOURS — What did the leader do?")
    print(f"{'='*120}")

    for h in hour_results[-5:]:
        print(f"\n  {h['session']} H{h['hour']:02d} ({h['split']}) | Us=${h['our_pnl']:>+.2f} | Leader=${h['leader_pnl']:>+.2f} (sell=${h['leader_sell_pnl']:>+.2f} res=${h['leader_res_pnl']:>+.2f})")

        for token_id, tk in sorted(h["per_token"].items(),
                                    key=lambda x: -float(x[1].get("resolution_pnl", 0))):
            res = tk.get("resolution", "SOLD")
            res_pnl = tk.get("resolution_pnl", 0)
            entry = float(tk["entry_price"]) if tk["entry_price"] else 0
            pct_sold = (float(tk["total_sell_shares"]) / float(tk["total_buy_shares"]) * 100
                        if float(tk["total_buy_shares"]) > 0 else 0)
            print(f"    ...{token_id[-8:]} entry=${entry:.3f} {res} (res PnL ${res_pnl:+.2f}) sold {pct_sold:.0f}% mid-hour")

    # =====================================================================
    # SECTION 3: LEADER SELL -> RESOLUTION OUTCOME
    # =====================================================================
    print(f"\n{'='*120}")
    print(f"  LEADER SELL -> RESOLUTION OUTCOME (does selling predict losing?)")
    print(f"{'='*120}")

    total_with_sells = sell_then_win + sell_then_lose
    total_no_sells = no_sell_win + no_sell_lose
    print(f"\n  Tokens where leader SOLD mid-hour:")
    print(f"    Then WON at resolution:  {sell_then_win:>4} ({sell_then_win/max(1,total_with_sells)*100:.0f}%)")
    print(f"    Then LOST at resolution: {sell_then_lose:>4} ({sell_then_lose/max(1,total_with_sells)*100:.0f}%)")
    print(f"\n  Tokens where leader DID NOT sell:")
    print(f"    WON at resolution:       {no_sell_win:>4} ({no_sell_win/max(1,total_no_sells)*100:.0f}%)")
    print(f"    LOST at resolution:      {no_sell_lose:>4} ({no_sell_lose/max(1,total_no_sells)*100:.0f}%)")

    print(f"\n  BIG SELL ANALYSIS (does selling more = worse outcome?):")
    for label in ["no_sells", "sold_30%+", "sold_50%+", "sold_70%+"]:
        b = big_sell_outcomes[label]
        total = b["win"] + b["lose"]
        if total == 0:
            continue
        wr = b["win"] / total * 100
        print(f"    {label:>12}: {total:>4} tokens | WR={wr:.0f}% ({b['win']}W/{b['lose']}L)")

    # =====================================================================
    # SECTION 4: HOURLY COMPARISON TABLE
    # =====================================================================
    print(f"\n{'='*120}")
    print(f"  ALL HOURS: Leader vs Us (sorted by our PnL)")
    print(f"{'='*120}")
    print(f"  {'Session':<26} {'H':>2} | {'Us$':>8} {'Ldr$':>8} {'LdrSell$':>8} {'LdrRes$':>8} | {'Ldr sold?':>10} {'Tokens':>6}")
    print(f"  {'-'*100}")

    # Aggregates for quadrant analysis
    both_win = both_lose = us_lose_ldr_win = us_win_ldr_lose = 0

    for h in hour_results:
        our = h["our_pnl"]
        ldr = h["leader_pnl"]
        sells = h["leader_sell_pnl"]
        res = h["leader_res_pnl"]
        n_tokens = len(h["per_token"])
        had_sells = "YES" if sells != 0 else "no"

        if our > 0 and ldr > 0:
            both_win += 1
        elif our < 0 and ldr < 0:
            both_lose += 1
        elif our < 0 and ldr > 0:
            us_lose_ldr_win += 1
        elif our > 0 and ldr < 0:
            us_win_ldr_lose += 1

        marker = ""
        if our < -10 and ldr > 10:
            marker = " *** WE LOST, LEADER WON"
        elif our < -20:
            marker = " ** BIG LOSS"

        print(f"  {h['session']:<26} {h['hour']:>2} | ${our:>+7.2f} ${ldr:>+7.2f} ${sells:>+7.2f} ${res:>+7.2f} | {had_sells:>10} {n_tokens:>6}{marker}")

    print(f"\n  QUADRANT ANALYSIS:")
    print(f"    Both profit:        {both_win:>3} hours")
    print(f"    Both loss:          {both_lose:>3} hours")
    print(f"    Us loss, Ldr profit:{us_lose_ldr_win:>3} hours  <-- opportunity")
    print(f"    Us profit, Ldr loss:{us_win_ldr_lose:>3} hours  <-- we beat leader!")

    # =====================================================================
    # SECTION 5: PATTERN — Leader sells HEAVILY = what signal?
    # =====================================================================
    print(f"\n{'='*120}")
    print(f"  SIGNAL: When leader sells heavily in an hour, what happens?")
    print(f"{'='*120}")

    for h in hour_results:
        h["leader_sell_ratio"] = (abs(h["leader_sell_pnl"]) / max(0.01, h["leader_buy_cost"])
                                  if h["leader_buy_cost"] > 0 else 0)

    # Bucket by sell ratio
    sell_buckets = defaultdict(lambda: {"us_pnl": [], "ldr_pnl": [], "count": 0})
    for h in hour_results:
        ratio = h["leader_sell_ratio"]
        if ratio < 0.1:
            key = "<10%"
        elif ratio < 0.3:
            key = "10-30%"
        elif ratio < 0.5:
            key = "30-50%"
        else:
            key = "50%+"
        sell_buckets[key]["us_pnl"].append(h["our_pnl"])
        sell_buckets[key]["ldr_pnl"].append(h["leader_pnl"])
        sell_buckets[key]["count"] += 1

    print(f"\n  {'Sell/Buy ratio':>15} | {'Count':>5} | {'Avg Us$':>8} {'Avg Ldr$':>9} | {'Our WR':>7}")
    for key in ["<10%", "10-30%", "30-50%", "50%+"]:
        b = sell_buckets.get(key)
        if not b or b["count"] == 0:
            continue
        avg_us = sum(b["us_pnl"]) / b["count"]
        avg_ldr = sum(b["ldr_pnl"]) / b["count"]
        our_wr = sum(1 for x in b["us_pnl"] if x > 0) / b["count"] * 100
        print(f"  {key:>15} | {b['count']:>5} | ${avg_us:>+7.2f} ${avg_ldr:>+8.2f} | {our_wr:>5.0f}%")


if __name__ == "__main__":
    main()
