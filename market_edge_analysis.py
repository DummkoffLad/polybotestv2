"""
Focused edge analysis: filter to ACTIVE markets only (meaningful price movement).
Answers the real exploitable questions with clean data.
"""
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import statistics

DATA_DIR = Path("data/sessions")


def load_hourly_price_paths():
    """Load per-token price time series from hourly files."""
    market_hours = []
    for date_dir in sorted(DATA_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.glob("*_hour_*.jsonl")):
            parts = f.stem.split("_hour_")
            if len(parts) != 2:
                continue
            session_name = parts[0]
            hour_num = int(parts[1])
            token_prices = defaultdict(list)

            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except:
                        continue
                    if data.get("type") != "price_snapshot":
                        continue
                    ts_str = data.get("timestamp", "")
                    try:
                        ts = datetime.fromisoformat(ts_str)
                    except:
                        continue
                    prices = data.get("prices", {})
                    for token_id, p in prices.items():
                        try:
                            bid = float(Decimal(p["bid"]))
                            ask = float(Decimal(p["ask"]))
                        except (KeyError, InvalidOperation):
                            continue
                        token_prices[token_id].append({
                            "ts": ts,
                            "bid": bid,
                            "ask": ask,
                            "mid": (bid + ask) / 2,
                            "minutes_in": ts.minute + ts.second / 60,
                        })

            for token_id, snapshots in token_prices.items():
                if len(snapshots) < 10:
                    continue
                snaps = sorted(snapshots, key=lambda x: x["ts"])
                mids = [s["mid"] for s in snaps]
                price_range = max(mids) - min(mids)

                # FILTER: Only keep "active" markets with real price movement
                # Skip tokens stuck at 0/1 or with no meaningful range
                if price_range < 0.05:
                    continue

                market_hours.append({
                    "session": f"{date_dir.name}/{session_name}",
                    "hour": hour_num,
                    "token_id": token_id,
                    "snapshots": snaps,
                    "price_range": price_range,
                })
    return market_hours


def pair_markets(market_hours):
    """Pair UP/DOWN tokens from same hour."""
    groups = defaultdict(list)
    for mh in market_hours:
        groups[(mh["session"], mh["hour"])].append(mh)

    pairs = []
    for key, tokens in groups.items():
        used = set()
        for i, t1 in enumerate(tokens):
            if i in used:
                continue
            for j, t2 in enumerate(tokens):
                if j in used or j <= i:
                    continue
                sum_mid = t1["snapshots"][0]["mid"] + t2["snapshots"][0]["mid"]
                if 0.85 < sum_mid < 1.15:
                    pairs.append((t1, t2))
                    used.add(i)
                    used.add(j)
                    break
    return pairs


def main():
    print("=" * 80)
    print("POLYMARKET EDGE ANALYSIS (Active Markets Only, range > 5c)")
    print("=" * 80)
    print()

    market_hours = load_hourly_price_paths()
    print(f"Active token-hours: {len(market_hours)}")

    pairs = pair_markets(market_hours)
    print(f"Paired markets (UP+DOWN): {len(pairs)}")
    print()

    # =====================================================================
    # EDGE 1: Can you buy BOTH sides cheap enough for guaranteed profit?
    # =====================================================================
    print("=" * 80)
    print("EDGE 1: BOTH-SIDES ARBITRAGE")
    print("  Buy UP + DOWN for < $1.00 total -> guaranteed profit at resolution")
    print("=" * 80)

    for pair in pairs:
        t1, t2 = pair
        # For each timestamp, find if both sides are simultaneously cheap
        snaps1 = {s["ts"]: s for s in t1["snapshots"]}
        snaps2 = {s["ts"]: s for s in t2["snapshots"]}

        # Track simultaneous prices
        pair_data = []
        for ts1, s1 in snaps1.items():
            best_match = None
            best_diff = 999
            for ts2, s2 in snaps2.items():
                diff = abs((ts1 - ts2).total_seconds())
                if diff < best_diff:
                    best_diff = diff
                    best_match = s2
            if best_match and best_diff < 10:
                pair_data.append({
                    "ts": ts1,
                    "ask1": s1["ask"],
                    "ask2": best_match["ask"],
                    "combined_ask": s1["ask"] + best_match["ask"],
                    "bid1": s1["bid"],
                    "bid2": best_match["bid"],
                    "combined_bid": s1["bid"] + best_match["bid"],
                })

        pair[0]["pair_data"] = pair_data
        pair[1]["pair_data"] = pair_data

    # Analyze combined asks
    all_min_combined = []
    arb_opportunities = []
    for t1, t2 in pairs:
        pd = t1.get("pair_data", [])
        if not pd:
            continue
        min_combined = min(p["combined_ask"] for p in pd)
        max_combined_bid = max(p["combined_bid"] for p in pd)
        all_min_combined.append({
            "session": t1["session"],
            "hour": t1["hour"],
            "min_combined_ask": min_combined,
            "max_combined_bid": max_combined_bid,
        })
        if min_combined < 1.0:
            # Find the timestamp
            best = min(pd, key=lambda x: x["combined_ask"])
            arb_opportunities.append({
                "session": t1["session"],
                "hour": t1["hour"],
                "combined_ask": best["combined_ask"],
                "ask1": best["ask1"],
                "ask2": best["ask2"],
                "minute": best["ts"].minute,
                "ts": best["ts"],
            })

    all_min_combined.sort(key=lambda x: x["min_combined_ask"])

    total_pairs = len(all_min_combined)
    under_100 = sum(1 for x in all_min_combined if x["min_combined_ask"] < 1.00)
    under_95 = sum(1 for x in all_min_combined if x["min_combined_ask"] < 0.95)
    under_90 = sum(1 for x in all_min_combined if x["min_combined_ask"] < 0.90)
    under_85 = sum(1 for x in all_min_combined if x["min_combined_ask"] < 0.85)

    print(f"\n  Out of {total_pairs} paired market-hours:")
    print(f"    Combined ask < $1.00 (arb exists):  {under_100} ({under_100/total_pairs*100:.1f}%)")
    print(f"    Combined ask < $0.95 (5c+ profit):  {under_95} ({under_95/total_pairs*100:.1f}%)")
    print(f"    Combined ask < $0.90 (10c+ profit): {under_90} ({under_90/total_pairs*100:.1f}%)")
    print(f"    Combined ask < $0.85 (15c+ profit): {under_85} ({under_85/total_pairs*100:.1f}%)")

    if all_min_combined:
        vals = [x["min_combined_ask"] for x in all_min_combined]
        vals.sort()
        print(f"\n  Min combined ask distribution:")
        print(f"    P10: {vals[int(len(vals)*0.10)]:.3f}")
        print(f"    P25: {vals[int(len(vals)*0.25)]:.3f}")
        print(f"    P50: {vals[int(len(vals)*0.50)]:.3f}")
        print(f"    P75: {vals[int(len(vals)*0.75)]:.3f}")
        print(f"    P90: {vals[int(len(vals)*0.90)]:.3f}")

    if arb_opportunities:
        arb_opportunities.sort(key=lambda x: x["combined_ask"])
        print(f"\n  Top arbitrage moments (both sides simultaneously cheap):")
        for a in arb_opportunities[:20]:
            profit = 1.0 - a["combined_ask"]
            print(f"    {a['session']} h{a['hour']:02d} min{a['minute']:02d} | UP_ask={a['ask1']:.3f} DOWN_ask={a['ask2']:.3f} | combined={a['combined_ask']:.3f} | profit={profit:.3f}")

    # When do arb opportunities happen?
    if arb_opportunities:
        arb_minutes = [a["minute"] for a in arb_opportunities]
        print(f"\n  When do arb opportunities occur (minute of hour)?")
        buckets = defaultdict(int)
        for m in arb_minutes:
            buckets[m // 10 * 10] += 1
        for b in sorted(buckets.keys()):
            print(f"    min {b:>2}-{b+9}: {buckets[b]} opportunities")

    print()

    # =====================================================================
    # EDGE 2: "Always buy at $0.45" analysis
    # =====================================================================
    print("=" * 80)
    print("EDGE 2: CAN YOU ALWAYS BUY AT $0.45?")
    print("  (Active markets with >5c range only)")
    print("=" * 80)

    min_asks = []
    for mh in market_hours:
        snaps = mh["snapshots"]
        ma = min(s["ask"] for s in snaps)
        min_asks.append({
            "session": mh["session"],
            "hour": mh["hour"],
            "min_ask": ma,
            "first_mid": snaps[0]["mid"],
            "last_mid": snaps[-1]["mid"],
            "range": mh["price_range"],
        })

    total = len(min_asks)
    below_55 = sum(1 for x in min_asks if x["min_ask"] <= 0.55)
    below_50 = sum(1 for x in min_asks if x["min_ask"] <= 0.50)
    below_45 = sum(1 for x in min_asks if x["min_ask"] <= 0.45)
    below_40 = sum(1 for x in min_asks if x["min_ask"] <= 0.40)
    below_35 = sum(1 for x in min_asks if x["min_ask"] <= 0.35)

    print(f"\n  Out of {total} active token-hours:")
    print(f"    Min ask <= $0.55: {below_55}/{total} = {below_55/total*100:.1f}%")
    print(f"    Min ask <= $0.50: {below_50}/{total} = {below_50/total*100:.1f}%")
    print(f"    Min ask <= $0.45: {below_45}/{total} = {below_45/total*100:.1f}%")
    print(f"    Min ask <= $0.40: {below_40}/{total} = {below_40/total*100:.1f}%")
    print(f"    Min ask <= $0.35: {below_35}/{total} = {below_35/total*100:.1f}%")

    vals = sorted([x["min_ask"] for x in min_asks])
    print(f"\n  Min ask distribution:")
    print(f"    Lowest:  {vals[0]:.3f}")
    print(f"    P10:     {vals[int(len(vals)*0.10)]:.3f}")
    print(f"    P25:     {vals[int(len(vals)*0.25)]:.3f}")
    print(f"    Median:  {vals[int(len(vals)*0.50)]:.3f}")
    print(f"    P75:     {vals[int(len(vals)*0.75)]:.3f}")
    print(f"    P90:     {vals[int(len(vals)*0.90)]:.3f}")
    print(f"    Highest: {vals[-1]:.3f}")

    # Cases where min ask NEVER hit 0.50
    never_cheap = [x for x in min_asks if x["min_ask"] > 0.50]
    if never_cheap:
        print(f"\n  Tokens that NEVER went below $0.50 ({len(never_cheap)} cases):")
        for x in sorted(never_cheap, key=lambda x: x["min_ask"])[:15]:
            direction = "UP" if x["last_mid"] > x["first_mid"] else "DOWN"
            print(f"    {x['session']} h{x['hour']:02d} | min_ask={x['min_ask']:.3f} | started={x['first_mid']:.3f} ended={x['last_mid']:.3f} | went {direction}")

    # Now check PAIRS: can you buy BOTH sides at $0.45 during the hour?
    print(f"\n  PAIRED ANALYSIS: Do both sides ever hit $0.45 during the same hour?")
    pair_both_cheap = 0
    for t1, t2 in pairs:
        min1 = min(s["ask"] for s in t1["snapshots"])
        min2 = min(s["ask"] for s in t2["snapshots"])
        if min1 <= 0.45 and min2 <= 0.45:
            pair_both_cheap += 1
    print(f"    Both sides hit ask <= $0.45 (not necessarily same time): {pair_both_cheap}/{len(pairs)} = {pair_both_cheap/len(pairs)*100:.1f}%")

    print()

    # =====================================================================
    # EDGE 3: High-price reversion deep dive
    # =====================================================================
    print("=" * 80)
    print("EDGE 3: HIGH-PRICE REVERSION ANALYSIS")
    print("  When price hits $0.85/$0.90/$0.95, does it come back?")
    print("=" * 80)

    for threshold in [0.80, 0.85, 0.90, 0.95]:
        events = []
        for mh in market_hours:
            snaps = mh["snapshots"]
            bids = [s["bid"] for s in snaps]
            for i, b in enumerate(bids):
                if b >= threshold:
                    remaining = bids[i:]
                    min_after = min(remaining)
                    final = bids[-1]
                    drop = b - min_after
                    pct_remaining = (len(snaps) - i) / len(snaps) * 100
                    minute = snaps[i]["minutes_in"]
                    events.append({
                        "drop": drop,
                        "final": final,
                        "minute": minute,
                        "pct_remaining": pct_remaining,
                        "ended_high": final >= 0.85,
                        "ended_mid": 0.30 <= final < 0.85,
                        "ended_low": final < 0.30,
                    })
                    break

        if not events:
            continue

        ended_high = sum(1 for e in events if e["ended_high"])
        ended_mid = sum(1 for e in events if e["ended_mid"])
        ended_low = sum(1 for e in events if e["ended_low"])
        n = len(events)

        print(f"\n  Bid hits ${threshold:.2f}+ ({n} events):")
        print(f"    Ended >= $0.85 (stayed high): {ended_high}/{n} = {ended_high/n*100:.1f}%")
        print(f"    Ended $0.30-$0.85 (partial):  {ended_mid}/{n} = {ended_mid/n*100:.1f}%")
        print(f"    Ended < $0.30 (collapsed):    {ended_low}/{n} = {ended_low/n*100:.1f}%")

        drops = [e["drop"] for e in events]
        print(f"    Max intra-hour drop after trigger: mean={statistics.mean(drops):.3f} median={statistics.median(drops):.3f}")

        # By minute of trigger
        early = [e for e in events if e["minute"] < 20]
        mid = [e for e in events if 20 <= e["minute"] < 40]
        late = [e for e in events if e["minute"] >= 40]

        if early:
            e_high = sum(1 for e in early if e["ended_high"])
            print(f"    Triggered min 0-19:  {len(early)} events, {e_high}/{len(early)} ended high ({e_high/len(early)*100:.0f}%)")
        if mid:
            m_high = sum(1 for e in mid if e["ended_high"])
            print(f"    Triggered min 20-39: {len(mid)} events, {m_high}/{len(mid)} ended high ({m_high/len(mid)*100:.0f}%)")
        if late:
            l_high = sum(1 for e in late if e["ended_high"])
            print(f"    Triggered min 40-59: {len(late)} events, {l_high}/{len(late)} ended high ({l_high/len(late)*100:.0f}%)")

    # Same for low side
    print(f"\n  --- LOW SIDE ---")
    for threshold in [0.20, 0.15, 0.10, 0.05]:
        events = []
        for mh in market_hours:
            snaps = mh["snapshots"]
            asks = [s["ask"] for s in snaps]
            for i, a in enumerate(asks):
                if a <= threshold:
                    remaining = asks[i:]
                    max_after = max(remaining)
                    final = asks[-1]
                    events.append({
                        "bounce": max_after - a,
                        "final": final,
                        "minute": snaps[i]["minutes_in"],
                        "ended_low": final <= 0.15,
                        "ended_mid": 0.15 < final <= 0.70,
                        "ended_high": final > 0.70,
                    })
                    break

        if not events:
            continue

        ended_low = sum(1 for e in events if e["ended_low"])
        ended_mid = sum(1 for e in events if e["ended_mid"])
        ended_high = sum(1 for e in events if e["ended_high"])
        n = len(events)

        print(f"\n  Ask hits ${threshold:.2f} or below ({n} events):")
        print(f"    Ended <= $0.15 (stayed low):   {ended_low}/{n} = {ended_low/n*100:.1f}%")
        print(f"    Ended $0.15-$0.70 (partial):   {ended_mid}/{n} = {ended_mid/n*100:.1f}%")
        print(f"    Ended > $0.70 (full reversal):  {ended_high}/{n} = {ended_high/n*100:.1f}%")

    print()

    # =====================================================================
    # EDGE 4: Optimal entry timing
    # =====================================================================
    print("=" * 80)
    print("EDGE 4: OPTIMAL ENTRY TIMING")
    print("  When is the best time to buy/sell within the hour?")
    print("=" * 80)

    # For each market, what % of the total range has been covered by each minute?
    range_covered = defaultdict(list)  # minute -> list of % of range covered

    for mh in market_hours:
        snaps = mh["snapshots"]
        mids = [s["mid"] for s in snaps]
        full_range = max(mids) - min(mids)
        if full_range < 0.10:
            continue

        first_mid = mids[0]
        for s in snaps:
            minute = int(s["minutes_in"])
            if 0 <= minute <= 59:
                move_so_far = abs(s["mid"] - first_mid)
                pct_of_range = move_so_far / full_range * 100
                range_covered[minute].append(pct_of_range)

    print(f"\n  Average % of total hour range covered by each minute:")
    for minute in range(0, 60, 5):
        if minute in range_covered:
            avg = statistics.mean(range_covered[minute])
            n = len(range_covered[minute])
            bar = "#" * int(avg / 2)
            print(f"    min {minute:>2}: {avg:>5.1f}% of range (n={n:>4}) {bar}")

    print()

    # =====================================================================
    # EDGE 5: "Scalp the bounce" -- buy at minute X, sell at minute Y
    # =====================================================================
    print("=" * 80)
    print("EDGE 5: SCALP STRATEGY -- Buy at minute X, sell at minute Y")
    print("  What's the avg PnL of buying at various minutes and selling later?")
    print("=" * 80)

    # For each pair, track: if you buy the CHEAPER side at minute X, what happens?
    entry_exit_pnl = defaultdict(list)  # (entry_bucket, exit_bucket) -> pnl list

    for mh in market_hours:
        snaps = mh["snapshots"]
        # Group by 5-minute buckets
        by_bucket = defaultdict(list)
        for s in snaps:
            bucket = int(s["minutes_in"]) // 5 * 5
            by_bucket[bucket].append(s)

        for entry_min in range(0, 55, 5):
            if entry_min not in by_bucket:
                continue
            entry_price = statistics.mean([s["ask"] for s in by_bucket[entry_min]])

            for exit_min in range(entry_min + 5, 60, 5):
                if exit_min not in by_bucket:
                    continue
                exit_price = statistics.mean([s["bid"] for s in by_bucket[exit_min]])
                pnl = exit_price - entry_price
                entry_exit_pnl[(entry_min, exit_min)].append(pnl)

    # Show best entry/exit combinations
    print(f"\n  Top 15 entry/exit combinations by avg PnL (buy ask, sell bid):")
    combos = []
    for (entry, exit_), pnls in entry_exit_pnl.items():
        if len(pnls) >= 20:  # Need enough samples
            avg = statistics.mean(pnls)
            wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            combos.append({"entry": entry, "exit": exit_, "avg_pnl": avg, "wr": wr, "n": len(pnls)})

    combos.sort(key=lambda x: x["avg_pnl"], reverse=True)
    print(f"    {'Entry':>6} | {'Exit':>6} | {'Avg PnL':>8} | {'Win%':>6} | {'Count':>6}")
    print(f"    {'-'*6}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*6}")
    for c in combos[:15]:
        print(f"    min {c['entry']:>2} | min {c['exit']:>2} | ${c['avg_pnl']:>+7.4f} | {c['wr']:>5.1f}% | {c['n']:>5}")

    print(f"\n  Worst 10 combinations (avoid these):")
    for c in combos[-10:]:
        print(f"    min {c['entry']:>2} | min {c['exit']:>2} | ${c['avg_pnl']:>+7.4f} | {c['wr']:>5.1f}% | {c['n']:>5}")

    print()

    # =====================================================================
    # EDGE 6: Momentum continuation
    # =====================================================================
    print("=" * 80)
    print("EDGE 6: MOMENTUM -- If it moves X in first 10 min, bet on continuation?")
    print("=" * 80)

    momentum_results = []
    for mh in market_hours:
        snaps = mh["snapshots"]
        early = [s for s in snaps if s["minutes_in"] <= 10]
        late = [s for s in snaps if s["minutes_in"] >= 50]

        if not early or not late:
            continue

        first = snaps[0]["mid"]
        at_10 = early[-1]["mid"]
        final = snaps[-1]["mid"]

        if abs(first) < 0.01 or abs(first) > 0.99:
            continue

        early_move = at_10 - first
        full_move = final - first

        momentum_results.append({
            "early_move": early_move,
            "full_move": full_move,
            "first": first,
            "final": final,
            "same_dir": (early_move > 0 and full_move > 0) or (early_move < 0 and full_move < 0),
        })

    if momentum_results:
        # Bucket by early move size
        for move_thresh in [0.03, 0.05, 0.10, 0.15, 0.20]:
            up_moves = [m for m in momentum_results if m["early_move"] >= move_thresh]
            down_moves = [m for m in momentum_results if m["early_move"] <= -move_thresh]

            if up_moves:
                continued_up = sum(1 for m in up_moves if m["full_move"] > 0)
                avg_final_move = statistics.mean([m["full_move"] for m in up_moves])
                print(f"  Early move >= +${move_thresh:.2f}: {len(up_moves)} cases, continued UP {continued_up}/{len(up_moves)} = {continued_up/len(up_moves)*100:.0f}%, avg full move = ${avg_final_move:+.3f}")

            if down_moves:
                continued_down = sum(1 for m in down_moves if m["full_move"] < 0)
                avg_final_move = statistics.mean([m["full_move"] for m in down_moves])
                print(f"  Early move <= -${move_thresh:.2f}: {len(down_moves)} cases, continued DOWN {continued_down}/{len(down_moves)} = {continued_down/len(down_moves)*100:.0f}%, avg full move = ${avg_final_move:+.3f}")

    print()

    # =====================================================================
    # EDGE 7: "Resolution lottery" -- cheap tickets
    # =====================================================================
    print("=" * 80)
    print("EDGE 7: CHEAP TICKET STRATEGY")
    print("  Buy at very low prices, hope for $0.99 resolution")
    print("  Expected value per $1 spent at each entry price")
    print("=" * 80)

    # For each token-hour, find: if you could buy at price X, what's EV?
    # We use last observed price as proxy for resolution
    for entry_thresh in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
        cases = []
        for mh in market_hours:
            snaps = mh["snapshots"]
            asks = [s["ask"] for s in snaps]

            if any(a <= entry_thresh for a in asks):
                entry_price = min(a for a in asks if a <= entry_thresh)
                final_bid = snaps[-1]["bid"]

                # If you bought at entry_price, resolution gives you final_bid
                # PnL per share = final - entry
                # Shares per $1 = 1 / entry
                # PnL per $1 = (final - entry) / entry = final/entry - 1
                if entry_price > 0:
                    pnl_per_dollar = (final_bid - entry_price) / entry_price
                    resolved_win = final_bid >= 0.90
                    cases.append({
                        "entry": entry_price,
                        "final": final_bid,
                        "pnl_per_dollar": pnl_per_dollar,
                        "resolved_win": resolved_win,
                    })

        if cases:
            avg_ev = statistics.mean([c["pnl_per_dollar"] for c in cases])
            win_rate = sum(1 for c in cases if c["resolved_win"]) / len(cases) * 100
            avg_entry = statistics.mean([c["entry"] for c in cases])
            # Calculate: if you win, you get ~$0.99/entry per $1. If you lose, you lose ~$1.
            avg_win_return = statistics.mean([c["pnl_per_dollar"] for c in cases if c["pnl_per_dollar"] > 0]) if any(c["pnl_per_dollar"] > 0 for c in cases) else 0
            avg_loss_return = statistics.mean([c["pnl_per_dollar"] for c in cases if c["pnl_per_dollar"] <= 0]) if any(c["pnl_per_dollar"] <= 0 for c in cases) else 0

            print(f"  Buy at <=${entry_thresh:.2f}: {len(cases):>4} cases | Win%={win_rate:>5.1f}% | EV/$ = ${avg_ev:>+.2f} | Avg win return: {avg_win_return:>+.1f}x | Avg loss: {avg_loss_return:>+.2f}x")

    print()

    # =====================================================================
    # EDGE 8: Spread patterns over time
    # =====================================================================
    print("=" * 80)
    print("EDGE 8: WHEN TO TRADE (spread + price stability)")
    print("=" * 80)

    minute_volatility = defaultdict(list)
    for mh in market_hours:
        snaps = mh["snapshots"]
        by_5min = defaultdict(list)
        for s in snaps:
            bucket = int(s["minutes_in"]) // 5 * 5
            by_5min[bucket].append(s["mid"])

        for bucket, mids in by_5min.items():
            if len(mids) >= 3:
                vol = max(mids) - min(mids)
                minute_volatility[bucket].append(vol)

    print(f"\n  5-minute volatility (range within 5-min window):")
    for minute in range(0, 60, 5):
        if minute in minute_volatility:
            avg_vol = statistics.mean(minute_volatility[minute])
            n = len(minute_volatility[minute])
            bar = "#" * int(avg_vol * 100)
            print(f"    min {minute:>2}-{minute+4}: avg_range=${avg_vol:.4f} (n={n:>4}) {bar}")

    print()
    print("=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
