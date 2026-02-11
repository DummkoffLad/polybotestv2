"""Deep analysis of leader scalp trades + conviction filter price verification.

Questions:
1. When we buy after conviction threshold, what price do we ACTUALLY get vs leader?
2. What does the leader's scalp pattern look like? Entry price, exit price, timing?
3. Can we detect a scalp in progress and follow the sell?
4. What's the profit margin on scalps? Are they worth chasing?
"""
import sys
import json
import math
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from src.data.models import TradeAction

TRAIN = ["2026-02-03/05-56", "2026-02-04/02-35", "2026-02-06/05-30"]
TEST = ["2026-02-05/03-58", "2026-02-05/22-15", "2026-02-07/05-52"]
HOLDOUT = ["2026-02-08/06-39"]


def get_split(s):
    for t in TRAIN:
        if t in s: return "TRAIN"
    for t in TEST:
        if t in s: return "TEST"
    for t in HOLDOUT:
        if t in s: return "HOLDOUT"
    return "UNKNOWN"


def get_utc_hour(path):
    with open(path, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
                if obj.get("type") in ("leader_trade", "fill"):
                    ts = obj.get("timestamp", "")
                    if "T" in ts: return int(ts.split("T")[1][:2])
            except: continue
    return -1


def parse_hour_file(path):
    """Parse all events from a session hour file."""
    events = []
    with open(path, 'r') as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except:
                continue
    return events


def analyze_hour(events):
    """Analyze all leader trades + price snapshots in one hour."""
    tokens = defaultdict(lambda: {
        "buys": [], "sells": [],
        "total_buy_dollars": Decimal("0"), "total_buy_shares": Decimal("0"),
        "total_sell_dollars": Decimal("0"), "total_sell_shares": Decimal("0"),
        "side": None,
    })
    snapshots = {}  # token_id -> list of (minute, bid, ask)
    end_prices = {}

    for evt in events:
        if evt.get("type") == "leader_trade":
            lt = evt.get("leader_trade", {})
            ts_str = lt.get("timestamp", evt.get("timestamp", ""))
            minute = 0
            second = 0
            if "T" in ts_str:
                time_part = ts_str.split("T")[1]
                minute = int(time_part[:2].split(":")[0]) if ":" in time_part else int(time_part[:2])
                # Parse minute:second properly
                parts = time_part.split(":")
                if len(parts) >= 2:
                    minute = int(parts[1])
                    if len(parts) > 2:
                        sec_str = parts[2].split(".")[0].split("+")[0].split("-")[0]
                        second = int(sec_str)
                    else:
                        second = 0

            token_id = lt.get("token_id", "")
            action = lt.get("action", "")
            price = Decimal(str(lt.get("leader_price", 0)))
            shares = Decimal(str(lt.get("leader_shares", 0)))
            dollars = Decimal(str(lt.get("leader_dollars", 0)))
            side = lt.get("side", "")

            tk = tokens[token_id]
            tk["side"] = side

            trade_info = {
                "minute": minute, "second": second,
                "price": price, "shares": shares, "dollars": dollars,
            }

            if action == "BUY":
                tk["buys"].append(trade_info)
                tk["total_buy_dollars"] += dollars
                tk["total_buy_shares"] += shares
            elif action == "SELL":
                tk["sells"].append(trade_info)
                tk["total_sell_dollars"] += dollars
                tk["total_sell_shares"] += shares

        elif evt.get("type") == "price_snapshot":
            prices = evt.get("prices", {})
            ts_str = evt.get("timestamp", "")
            snap_minute = 0
            if "T" in ts_str:
                parts = ts_str.split("T")[1].split(":")
                if len(parts) >= 2:
                    snap_minute = int(parts[1])

            for token_id, pdata in prices.items():
                if isinstance(pdata, dict):
                    bid = pdata.get("bid")
                    ask = pdata.get("ask")
                    if bid is not None:
                        if token_id not in snapshots:
                            snapshots[token_id] = []
                        snapshots[token_id].append({
                            "minute": snap_minute,
                            "bid": Decimal(str(bid)),
                            "ask": Decimal(str(ask)) if ask else None,
                        })
                        end_prices[token_id] = Decimal(str(bid))

    return tokens, snapshots, end_prices


def main():
    base = Path("data/sessions")
    all_hours = []
    for dd in sorted(base.iterdir()):
        if not dd.is_dir(): continue
        for hf in sorted(dd.glob("*_hour_*.jsonl")):
            h = get_utc_hour(hf)
            if h >= 0:
                s = dd.name + "/" + hf.stem.split("_hour_")[0]
                all_hours.append((s, h, hf, get_split(s)))

    print(f"Analyzing leader scalps + conviction prices across {len(all_hours)} hours...\n")

    # =========================================================================
    # PART 1: CONVICTION FILTER PRICE VERIFICATION
    # =========================================================================
    print(f"{'='*120}")
    print(f"  PART 1: CONVICTION FILTER — PRICE VERIFICATION")
    print(f"{'='*120}")
    print(f"  Checking: when conviction threshold ($300) is hit, what's the")
    print(f"  CURRENT market price vs leader's original entry price?\n")

    conviction_entries = []  # (token, leader_first_price, leader_avg_price, price_at_threshold, ask_at_threshold, cumulative_spend)

    for session, utc_h, hf, split in all_hours:
        events = parse_hour_file(hf)
        tokens, snapshots, end_prices = analyze_hour(events)

        for token_id, tk in tokens.items():
            if not tk["buys"]:
                continue

            # Simulate cumulative spend
            cum_spend = Decimal("0")
            threshold_hit = False
            threshold_minute = -1
            leader_first_price = tk["buys"][0]["price"]

            for buy in tk["buys"]:
                cum_spend += buy["dollars"]
                if not threshold_hit and cum_spend >= Decimal("300"):
                    threshold_hit = True
                    threshold_minute = buy["minute"]
                    threshold_price = buy["price"]  # Leader's price when threshold hit
                    break

            if not threshold_hit:
                continue

            # Find market snapshot closest to threshold minute
            snaps = snapshots.get(token_id, [])
            closest_snap = None
            for snap in snaps:
                if snap["minute"] <= threshold_minute:
                    closest_snap = snap
                elif snap["minute"] > threshold_minute:
                    if closest_snap is None:
                        closest_snap = snap
                    break

            # Leader's average entry up to threshold
            buy_dollars_to_threshold = Decimal("0")
            buy_shares_to_threshold = Decimal("0")
            for buy in tk["buys"]:
                buy_dollars_to_threshold += buy["dollars"]
                buy_shares_to_threshold += buy["shares"]
                if buy_dollars_to_threshold >= Decimal("300"):
                    break
            leader_avg_at_threshold = (buy_dollars_to_threshold / buy_shares_to_threshold
                                        if buy_shares_to_threshold > 0 else Decimal("0"))

            # Resolution
            ep = end_prices.get(token_id)
            resolution = "WIN" if ep and ep >= Decimal("0.50") else "LOSE"

            conviction_entries.append({
                "token_id": token_id[-8:],
                "hour": f"{session}_h{utc_h}",
                "split": split,
                "leader_first_price": float(leader_first_price),
                "leader_avg_at_threshold": float(leader_avg_at_threshold),
                "threshold_price": float(threshold_price),
                "threshold_minute": threshold_minute,
                "market_ask": float(closest_snap["ask"]) if closest_snap and closest_snap.get("ask") else None,
                "market_bid": float(closest_snap["bid"]) if closest_snap else None,
                "snap_minute": closest_snap["minute"] if closest_snap else None,
                "cum_spend": float(cum_spend),
                "resolution": resolution,
                "total_buy_cost": float(tk["total_buy_dollars"]),
            })

    print(f"  Tokens that hit $300 conviction threshold: {len(conviction_entries)}")
    # Show price comparisons
    with_ask = [e for e in conviction_entries if e["market_ask"] is not None]
    print(f"  With market ask data at threshold time: {len(with_ask)}")

    if with_ask:
        gaps = [e["market_ask"] - e["leader_first_price"] for e in with_ask]
        gaps_vs_avg = [e["market_ask"] - e["leader_avg_at_threshold"] for e in with_ask]
        threshold_gaps = [e["market_ask"] - e["threshold_price"] for e in with_ask]

        print(f"\n  Price gap: Market ASK at conviction threshold vs Leader's prices:")
        print(f"    vs Leader FIRST buy:    avg ${sum(gaps)/len(gaps):+.4f}, median ${sorted(gaps)[len(gaps)//2]:+.4f}")
        print(f"    vs Leader AVG at thresh: avg ${sum(gaps_vs_avg)/len(gaps_vs_avg):+.4f}, median ${sorted(gaps_vs_avg)[len(gaps_vs_avg)//2]:+.4f}")
        print(f"    vs Leader's TRADE at threshold: avg ${sum(threshold_gaps)/len(threshold_gaps):+.4f}")

        print(f"\n  Sample entries (first 15):")
        print(f"  {'Token':<10} {'Hour':<35} {'LdrFirst':>8} {'LdrAvg':>8} {'MktAsk':>8} {'Gap':>7} {'Min':>4} {'Res':<5} {'TotalSpend':>10}")
        for e in with_ask[:15]:
            gap = e["market_ask"] - e["leader_first_price"]
            print(f"  {e['token_id']:<10} {e['hour']:<35} ${e['leader_first_price']:.3f}  ${e['leader_avg_at_threshold']:.3f}  ${e['market_ask']:.3f}  ${gap:+.3f}  {e['threshold_minute']:>3}  {e['resolution']:<5} ${e['total_buy_cost']:>8.2f}")

        # Minute the threshold is hit
        minutes = [e["threshold_minute"] for e in conviction_entries]
        print(f"\n  When conviction threshold is reached:")
        print(f"    Avg minute: {sum(minutes)/len(minutes):.1f}")
        print(f"    Median:     {sorted(minutes)[len(minutes)//2]}")
        print(f"    Min:        {min(minutes)}")
        print(f"    Max:        {max(minutes)}")

        # Distribution
        for lo, hi in [(0, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 60)]:
            count = sum(1 for m in minutes if lo <= m < hi)
            print(f"    min {lo}-{hi}: {count} tokens ({count/len(minutes)*100:.0f}%)")

    # =========================================================================
    # PART 2: SCALP TRADE ANALYSIS
    # =========================================================================
    print(f"\n\n{'='*120}")
    print(f"  PART 2: LEADER SCALP TRADE ANALYSIS")
    print(f"{'='*120}")
    print(f"  Definition: scalp = leader buys AND sells a token within the hour,")
    print(f"  sells at profit, total spend < $300 (below our conviction threshold)\n")

    all_scalps = []
    all_holds = []  # tokens where leader spends $300+ (what we trade)

    for session, utc_h, hf, split in all_hours:
        events = parse_hour_file(hf)
        tokens, snapshots, end_prices = analyze_hour(events)

        for token_id, tk in tokens.items():
            if not tk["buys"]:
                continue

            total_buy = float(tk["total_buy_dollars"])
            total_sell = float(tk["total_sell_dollars"])
            buy_shares = float(tk["total_buy_shares"])
            sell_shares = float(tk["total_sell_shares"])

            if buy_shares == 0:
                continue

            avg_buy = total_buy / buy_shares
            avg_sell = total_sell / sell_shares if sell_shares > 0 else 0

            remaining = buy_shares - sell_shares
            ep = end_prices.get(token_id)
            last_bid = float(ep) if ep else 0.50
            resolution = "WIN" if last_bid >= 0.50 else "LOSE"
            res_price = 0.99 if resolution == "WIN" else 0.01
            remaining_value = remaining * res_price if remaining > 0 else 0
            net_pnl = total_sell + remaining_value - total_buy

            # Sell profit (from sells only, before resolution)
            sell_pnl = total_sell - (sell_shares * avg_buy) if sell_shares > 0 else 0

            # First buy/sell timing
            first_buy_min = tk["buys"][0]["minute"]
            last_buy_min = tk["buys"][-1]["minute"]
            first_sell_min = tk["sells"][0]["minute"] if tk["sells"] else 99
            last_sell_min = tk["sells"][-1]["minute"] if tk["sells"] else 99

            # Pct of shares sold
            pct_sold = (sell_shares / buy_shares * 100) if buy_shares > 0 else 0

            entry = {
                "hour": f"{session}_h{utc_h}",
                "split": split,
                "token_id": token_id[-8:],
                "total_buy": total_buy,
                "total_sell": total_sell,
                "avg_buy": avg_buy,
                "avg_sell": avg_sell,
                "buy_count": len(tk["buys"]),
                "sell_count": len(tk["sells"]),
                "first_buy_min": first_buy_min,
                "last_buy_min": last_buy_min,
                "first_sell_min": first_sell_min,
                "last_sell_min": last_sell_min,
                "pct_sold": pct_sold,
                "sell_pnl": sell_pnl,
                "resolution": resolution,
                "net_pnl": net_pnl,
                "remaining_shares": remaining,
                "buy_shares": buy_shares,
                "sell_shares": sell_shares,
                "side": tk["side"],
            }

            if total_buy >= 300:
                all_holds.append(entry)
            else:
                if tk["sells"]:  # has sells = potential scalp
                    all_scalps.append(entry)

    # Scalp analysis
    profitable_scalps = [s for s in all_scalps if s["sell_pnl"] > 0]
    losing_scalps = [s for s in all_scalps if s["sell_pnl"] <= 0]

    print(f"  Total tokens below $300 spend WITH sells: {len(all_scalps)}")
    print(f"  Profitable scalps (sell_pnl > 0): {len(profitable_scalps)} ({len(profitable_scalps)/max(1,len(all_scalps))*100:.0f}%)")
    print(f"  Losing scalps:                     {len(losing_scalps)} ({len(losing_scalps)/max(1,len(all_scalps))*100:.0f}%)")

    # Profit stats
    scalp_sell_pnl = sum(s["sell_pnl"] for s in all_scalps)
    scalp_net_pnl = sum(s["net_pnl"] for s in all_scalps)
    print(f"\n  Total scalp sell PnL (before resolution): ${scalp_sell_pnl:+.2f}")
    print(f"  Total scalp net PnL (after resolution):   ${scalp_net_pnl:+.2f}")
    print(f"  Avg scalp sell PnL per trade: ${scalp_sell_pnl/max(1,len(all_scalps)):+.2f}")

    # Scalp by spend bucket
    print(f"\n  Scalps by leader spend:")
    for lo, hi in [(0, 50), (50, 100), (100, 200), (200, 300)]:
        bucket = [s for s in all_scalps if lo <= s["total_buy"] < hi]
        if not bucket: continue
        prof = [s for s in bucket if s["sell_pnl"] > 0]
        sell_pnl = sum(s["sell_pnl"] for s in bucket)
        net_pnl = sum(s["net_pnl"] for s in bucket)
        avg_margin = sum(s["avg_sell"] - s["avg_buy"] for s in bucket if s["avg_sell"] > 0) / max(1, len([s for s in bucket if s["avg_sell"] > 0]))
        wins = sum(1 for s in bucket if s["resolution"] == "WIN")
        print(f"    ${lo}-${hi}: {len(bucket)} scalps, {len(prof)} profitable ({len(prof)/max(1,len(bucket))*100:.0f}%), sellPnL=${sell_pnl:+.2f}, netPnL=${net_pnl:+.2f}, avgMargin=${avg_margin:+.4f}, resWR={wins}/{len(bucket)}")

    # Entry/exit price analysis for profitable scalps
    print(f"\n  PROFITABLE SCALP PATTERNS:")
    if profitable_scalps:
        avg_buys = [s["avg_buy"] for s in profitable_scalps]
        avg_sells = [s["avg_sell"] for s in profitable_scalps]
        margins = [s["avg_sell"] - s["avg_buy"] for s in profitable_scalps]
        pct_margins = [(s["avg_sell"] - s["avg_buy"]) / s["avg_buy"] * 100 for s in profitable_scalps if s["avg_buy"] > 0]
        durations = [s["first_sell_min"] - s["first_buy_min"] for s in profitable_scalps]
        pct_solds = [s["pct_sold"] for s in profitable_scalps]

        print(f"    Avg entry price:     ${sum(avg_buys)/len(avg_buys):.4f}")
        print(f"    Avg exit price:      ${sum(avg_sells)/len(avg_sells):.4f}")
        print(f"    Avg margin:          ${sum(margins)/len(margins):+.4f} ({sum(pct_margins)/len(pct_margins):+.1f}%)")
        print(f"    Median margin:       ${sorted(margins)[len(margins)//2]:+.4f}")
        print(f"    Avg buy-to-sell gap: {sum(durations)/len(durations):.1f} minutes")
        print(f"    Median sell gap:     {sorted(durations)[len(durations)//2]} minutes")
        print(f"    Avg pct shares sold: {sum(pct_solds)/len(pct_solds):.0f}%")

        # By entry price range
        print(f"\n    By entry price range:")
        for lo, hi in [(0.0, 0.30), (0.30, 0.45), (0.45, 0.55), (0.55, 0.70), (0.70, 1.0)]:
            bucket = [s for s in profitable_scalps if lo <= s["avg_buy"] < hi]
            if not bucket: continue
            sell_pnl = sum(s["sell_pnl"] for s in bucket)
            net_pnl = sum(s["net_pnl"] for s in bucket)
            avg_m = sum(s["avg_sell"] - s["avg_buy"] for s in bucket) / len(bucket)
            res_wins = sum(1 for s in bucket if s["resolution"] == "WIN")
            print(f"      ${lo:.2f}-${hi:.2f}: {len(bucket)} trades, sellPnL=${sell_pnl:+.2f}, netPnL=${net_pnl:+.2f}, margin=${avg_m:+.4f}, resWR={res_wins}/{len(bucket)}")

        # Time to first sell
        print(f"\n    Time from first buy to first sell:")
        for lo, hi in [(0, 5), (5, 15), (15, 30), (30, 45), (45, 60)]:
            bucket = [s for s in profitable_scalps if lo <= (s["first_sell_min"] - s["first_buy_min"]) < hi]
            if not bucket: continue
            sell_pnl = sum(s["sell_pnl"] for s in bucket)
            print(f"      {lo}-{hi} min: {len(bucket)} trades, sellPnL=${sell_pnl:+.2f}")

    # =========================================================================
    # PART 3: CAN WE DETECT SCALPS IN REAL-TIME?
    # =========================================================================
    print(f"\n\n{'='*120}")
    print(f"  PART 3: REAL-TIME SCALP DETECTION SIGNALS")
    print(f"{'='*120}")

    # Signal 1: Leader sells within N minutes of first buy
    # If we see leader sell a token they bought recently, follow the sell
    print(f"\n  Signal: Leader sells within X minutes of first buy on same token")
    print(f"  (We could follow the sell if we already have a position)")

    for max_gap in [5, 10, 15, 20, 30]:
        quick_sellers = [s for s in all_scalps if (s["first_sell_min"] - s["first_buy_min"]) <= max_gap]
        qs_pnl = sum(s["sell_pnl"] for s in quick_sellers)
        qs_prof = sum(1 for s in quick_sellers if s["sell_pnl"] > 0)
        print(f"    Sell within {max_gap:>2} min: {len(quick_sellers):>3} tokens, {qs_prof:>3} profitable ({qs_prof/max(1,len(quick_sellers))*100:.0f}%), sellPnL=${qs_pnl:+.2f}")

    # Signal 2: Leader entry price range as scalp indicator
    print(f"\n  Signal: Leader entry price as scalp vs hold indicator")
    all_tokens_with_sells = all_scalps + [h for h in all_holds if h["sell_count"] > 0]
    for lo, hi in [(0.0, 0.30), (0.30, 0.45), (0.45, 0.55), (0.55, 0.70), (0.70, 1.0)]:
        scalps_in = [s for s in all_scalps if lo <= s["avg_buy"] < hi]
        holds_in = [s for s in all_holds if lo <= s["avg_buy"] < hi]
        if not scalps_in and not holds_in: continue
        scalp_wr = sum(1 for s in scalps_in if s["sell_pnl"] > 0) / max(1, len(scalps_in)) * 100
        hold_wr = sum(1 for s in holds_in if s["resolution"] == "WIN") / max(1, len(holds_in)) * 100
        print(f"    ${lo:.2f}-${hi:.2f}: {len(scalps_in)} scalps ({scalp_wr:.0f}% sell-profitable), {len(holds_in)} holds ({hold_wr:.0f}% res-win)")

    # Signal 3: What if we bought ALL tokens at leader's first buy and sold when leader sells?
    print(f"\n  Signal: 'Mirror everything' including sells (scalp-style)")
    print(f"  What if we followed ALL leader sells on tokens below $300 conviction?")

    # For each scalp, calculate what we'd make if we entered on first buy and exited on first sell
    mirror_pnls = []
    for s in all_scalps:
        if s["sell_count"] == 0:
            continue
        # We'd buy at ask (slightly worse than leader)
        our_entry = s["avg_buy"] + 0.02  # Assume 2c slippage
        our_exit = s["avg_sell"] - 0.02   # Assume 2c slippage on sell
        if our_entry >= 1 or our_exit <= 0:
            continue
        # Scale: $50 capital, $900 leader, k=1, boost=5
        our_dollars = min(s["total_buy"] * (50/900) * 5, 5)  # Cap at $5 per scalp
        our_shares = our_dollars / our_entry if our_entry > 0 else 0
        # Sell same pct as leader
        sell_pct = min(s["pct_sold"] / 100, 1.0)
        our_sell_shares = our_shares * sell_pct
        our_sell_revenue = our_sell_shares * our_exit
        remaining = our_shares - our_sell_shares

        # Remaining resolves
        res_price = 0.99 if s["resolution"] == "WIN" else 0.01
        remaining_val = remaining * res_price

        mirror_pnl = our_sell_revenue + remaining_val - our_dollars
        mirror_pnls.append({
            "pnl": mirror_pnl,
            "resolution": s["resolution"],
            "entry": our_entry,
            "exit": our_exit,
            "dollars": our_dollars,
            "sell_pnl": s["sell_pnl"],
            "leader_pnl": s["net_pnl"],
            "pct_sold": s["pct_sold"],
        })

    if mirror_pnls:
        total_mirror = sum(m["pnl"] for m in mirror_pnls)
        winners = sum(1 for m in mirror_pnls if m["pnl"] > 0)
        print(f"    Mirror scalp PnL:  ${total_mirror:+.2f} across {len(mirror_pnls)} trades")
        print(f"    Win rate:          {winners}/{len(mirror_pnls)} ({winners/len(mirror_pnls)*100:.0f}%)")

        # What if we ONLY mirror scalps where leader's first sell is profitable?
        # (We can detect this: leader sell price > leader buy price on same token)
        prof_mirror = [m for m in mirror_pnls if m["exit"] > m["entry"]]
        loss_mirror = [m for m in mirror_pnls if m["exit"] <= m["entry"]]
        if prof_mirror:
            print(f"\n    If we ONLY mirror where leader sells higher than they bought:")
            print(f"      Trades:     {len(prof_mirror)}")
            print(f"      Total PnL:  ${sum(m['pnl'] for m in prof_mirror):+.2f}")
            print(f"      Win rate:   {sum(1 for m in prof_mirror if m['pnl'] > 0)}/{len(prof_mirror)}")
        if loss_mirror:
            print(f"    If we ONLY mirror where leader sells lower:")
            print(f"      Trades:     {len(loss_mirror)}")
            print(f"      Total PnL:  ${sum(m['pnl'] for m in loss_mirror):+.2f}")

    # =========================================================================
    # PART 4: LEADER SELL SIGNAL ON LOW-CONVICTION TOKENS
    # =========================================================================
    print(f"\n\n{'='*120}")
    print(f"  PART 4: LEADER SELL AS ENTRY/EXIT SIGNAL ON LOW-CONVICTION TOKENS")
    print(f"{'='*120}")
    print(f"  Idea: When leader SELLS a low-conviction token at profit, DON'T follow")
    print(f"  the sell. Instead, use it as a signal that the token is moving in the")
    print(f"  right direction and BUY ourselves, then hold to resolution.\n")

    # For tokens where leader sells at profit AND token resolves as WIN
    sell_then_win = [s for s in all_scalps if s["sell_pnl"] > 0 and s["resolution"] == "WIN"]
    sell_then_lose = [s for s in all_scalps if s["sell_pnl"] > 0 and s["resolution"] == "LOSE"]

    print(f"  Leader scalps at profit:")
    print(f"    Token resolves WIN:  {len(sell_then_win)} ({len(sell_then_win)/max(1,len(profitable_scalps))*100:.0f}%)")
    print(f"    Token resolves LOSE: {len(sell_then_lose)} ({len(sell_then_lose)/max(1,len(profitable_scalps))*100:.0f}%)")

    # What if we bought at leader's sell price and held to resolution?
    counter_pnls = []
    for s in all_scalps:
        if s["sell_pnl"] <= 0:
            continue
        # Buy at leader's sell price + 2c slippage
        our_entry = s["avg_sell"] + 0.02
        if our_entry >= 1.0:
            continue
        our_dollars = 3.0  # Small $3 position
        our_shares = our_dollars / our_entry
        res_price = 0.99 if s["resolution"] == "WIN" else 0.01
        our_pnl = our_shares * res_price - our_dollars

        counter_pnls.append({
            "pnl": our_pnl,
            "entry": our_entry,
            "resolution": s["resolution"],
            "leader_sell_pnl": s["sell_pnl"],
            "leader_total_buy": s["total_buy"],
        })

    if counter_pnls:
        total = sum(c["pnl"] for c in counter_pnls)
        wins = sum(1 for c in counter_pnls if c["pnl"] > 0)
        print(f"\n  Contrarian: Buy when leader sells at profit, hold to resolution:")
        print(f"    Trades:     {len(counter_pnls)}")
        print(f"    Total PnL:  ${total:+.2f}")
        print(f"    Win rate:   {wins}/{len(counter_pnls)} ({wins/max(1,len(counter_pnls))*100:.0f}%)")
        print(f"    Avg PnL:    ${total/len(counter_pnls):+.2f}")

        # By entry price
        for lo, hi in [(0.30, 0.45), (0.45, 0.55), (0.55, 0.70), (0.70, 1.0)]:
            bucket = [c for c in counter_pnls if lo <= c["entry"] < hi]
            if not bucket: continue
            bp = sum(c["pnl"] for c in bucket)
            bw = sum(1 for c in bucket if c["pnl"] > 0)
            print(f"      ${lo:.2f}-${hi:.2f}: {len(bucket)} trades, PnL=${bp:+.2f}, WR={bw}/{len(bucket)}")


    # =========================================================================
    # PART 5: TOKENS WE ENTER AT $300 — DETAILED PRICE TIMELINE
    # =========================================================================
    print(f"\n\n{'='*120}")
    print(f"  PART 5: CONVICTION ENTRIES — WHAT PRICE DO WE ACTUALLY GET?")
    print(f"{'='*120}")
    print(f"  For each token where we'd enter after $300 conviction, show timeline:\n")

    shown = 0
    for session, utc_h, hf, split in all_hours:
        if shown >= 8:
            break
        events = parse_hour_file(hf)
        tokens, snapshots, end_prices = analyze_hour(events)

        for token_id, tk in tokens.items():
            if shown >= 8:
                break
            if float(tk["total_buy_dollars"]) < 300:
                continue

            # Show timeline
            cum_spend = Decimal("0")
            threshold_min = None

            print(f"  Token ...{token_id[-8:]} ({tk['side']}) in {session}_h{utc_h} [{split}]")
            print(f"    Total buy: ${float(tk['total_buy_dollars']):.2f} in {len(tk['buys'])} trades")

            ep = end_prices.get(token_id)
            res = "WIN" if ep and ep >= Decimal("0.50") else "LOSE"
            print(f"    Resolution: {res} (last_bid=${float(ep) if ep else 0:.3f})")

            # First few buys showing cumulative
            for i, buy in enumerate(tk["buys"][:15]):
                cum_spend += buy["dollars"]
                marker = ""
                if threshold_min is None and cum_spend >= Decimal("300"):
                    threshold_min = buy["minute"]
                    marker = " <-- CONVICTION THRESHOLD HIT, WE BUY HERE"
                    # Find ask at this moment
                    snaps = snapshots.get(token_id, [])
                    close_snap = None
                    for snap in snaps:
                        if snap["minute"] <= buy["minute"]:
                            close_snap = snap
                    if close_snap and close_snap.get("ask"):
                        marker += f" (market ask=${float(close_snap['ask']):.3f})"

                print(f"      buy #{i+1:>2} min={buy['minute']:>2} ${float(buy['dollars']):>6.2f} @${float(buy['price']):.3f} cum=${float(cum_spend):>7.2f}{marker}")

            if len(tk["buys"]) > 15:
                print(f"      ... +{len(tk['buys'])-15} more buys")

            # Show first few sells
            if tk["sells"]:
                for i, sell in enumerate(tk["sells"][:5]):
                    print(f"      sell #{i+1} min={sell['minute']:>2} ${float(sell['dollars']):>6.2f} @${float(sell['price']):.3f}")

            print()
            shown += 1


if __name__ == "__main__":
    main()
