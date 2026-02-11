"""
Comprehensive analysis of Polymarket 1-hour crypto UP/DOWN market structure.
Answers: min/max prices, reversion patterns, volatility, exploitable edges.
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import statistics

DATA_DIR = Path("data/sessions")

# ─── Data Loading ────────────────────────────────────────────────────────

def load_hourly_price_paths():
    """
    For each hourly file, extract per-token price time series.
    Returns: list of MarketHour dicts with price paths.
    """
    market_hours = []

    for date_dir in sorted(DATA_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.glob("*_hour_*.jsonl")):
            # Parse hour number from filename
            parts = f.stem.split("_hour_")
            if len(parts) != 2:
                continue
            session_name = parts[0]
            hour_num = int(parts[1])

            # token_id -> list of (timestamp, bid, ask)
            token_prices = defaultdict(list)
            line_count = 0

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
                            bid = Decimal(p["bid"])
                            ask = Decimal(p["ask"])
                        except (KeyError, InvalidOperation):
                            continue

                        # Skip degenerate prices (resolved/no-liquidity)
                        if bid <= Decimal("0") and ask <= Decimal("0.01"):
                            continue
                        if bid >= Decimal("0.99") and ask >= Decimal("1"):
                            continue

                        token_prices[token_id].append({
                            "ts": ts,
                            "bid": float(bid),
                            "ask": float(ask),
                            "mid": float((bid + ask) / 2),
                            "minutes_in": (ts.minute + ts.second / 60),
                        })
                    line_count += 1

            if not token_prices:
                continue

            for token_id, snapshots in token_prices.items():
                if len(snapshots) < 5:  # Need meaningful data
                    continue
                market_hours.append({
                    "session": f"{date_dir.name}/{session_name}",
                    "hour": hour_num,
                    "token_id": token_id[:16] + "...",  # Truncate for display
                    "token_id_full": token_id,
                    "snapshots": sorted(snapshots, key=lambda x: x["ts"]),
                    "file": str(f),
                })

    return market_hours


def analyze_market_hour(mh):
    """Compute stats for one token in one hour."""
    snaps = mh["snapshots"]
    bids = [s["bid"] for s in snaps]
    asks = [s["ask"] for s in snaps]
    mids = [s["mid"] for s in snaps]

    # Basic stats
    min_bid = min(bids)
    max_bid = max(bids)
    min_ask = min(asks)
    max_ask = max(asks)
    min_mid = min(mids)
    max_mid = max(mids)

    # First and last prices
    first_mid = mids[0]
    last_mid = mids[-1]

    # Time of min/max
    min_idx = mids.index(min_mid)
    max_idx = mids.index(max_mid)
    min_time = snaps[min_idx]["minutes_in"]
    max_time = snaps[max_idx]["minutes_in"]

    # Price range (volatility)
    price_range = max_mid - min_mid

    # Did it ever hit certain thresholds?
    hit_90_plus = any(b >= 0.90 for b in bids)
    hit_85_plus = any(b >= 0.85 for b in bids)
    hit_80_plus = any(b >= 0.80 for b in bids)
    hit_10_minus = any(a <= 0.10 for a in asks)
    hit_15_minus = any(a <= 0.15 for a in asks)
    hit_20_minus = any(a <= 0.20 for a in asks)

    # Thresholds for "can you buy at X?"
    hit_below_45 = any(a <= 0.45 for a in asks)
    hit_below_50 = any(a <= 0.50 for a in asks)
    hit_below_40 = any(a <= 0.40 for a in asks)
    hit_below_35 = any(a <= 0.35 for a in asks)
    hit_below_30 = any(a <= 0.30 for a in asks)

    # Reversion analysis: if price hits 0.90+, does it come back?
    reverted_from_90 = False
    min_after_90 = None
    if hit_90_plus:
        # Find first time bid >= 0.90
        first_90_idx = next(i for i, b in enumerate(bids) if b >= 0.90)
        # Track min bid after that point
        bids_after = bids[first_90_idx:]
        if bids_after:
            min_after_90 = min(bids_after)
            reverted_from_90 = min_after_90 < 0.80

    # Reversion from low: if price hits <0.10, does it come back?
    reverted_from_10 = False
    max_after_10 = None
    if hit_10_minus:
        first_10_idx = next(i for i, a in enumerate(asks) if a <= 0.10)
        asks_after = asks[first_10_idx:]
        if asks_after:
            max_after_10 = max(asks_after)
            reverted_from_10 = max_after_10 > 0.20

    # Resolution: what was the last observed price?
    last_bid = bids[-1]
    last_ask = asks[-1]
    resolved_high = last_bid >= 0.90  # Likely resolved YES
    resolved_low = last_ask <= 0.10   # Likely resolved NO

    # Spread analysis
    spreads = [s["ask"] - s["bid"] for s in snaps]
    avg_spread = statistics.mean(spreads)
    max_spread = max(spreads)

    return {
        **mh,
        "n_snapshots": len(snaps),
        "min_bid": min_bid, "max_bid": max_bid,
        "min_ask": min_ask, "max_ask": max_ask,
        "min_mid": min_mid, "max_mid": max_mid,
        "first_mid": first_mid, "last_mid": last_mid,
        "min_time_min": min_time, "max_time_min": max_time,
        "price_range": price_range,
        "hit_90_plus": hit_90_plus, "hit_85_plus": hit_85_plus, "hit_80_plus": hit_80_plus,
        "hit_10_minus": hit_10_minus, "hit_15_minus": hit_15_minus, "hit_20_minus": hit_20_minus,
        "hit_below_45": hit_below_45, "hit_below_50": hit_below_50,
        "hit_below_40": hit_below_40, "hit_below_35": hit_below_35, "hit_below_30": hit_below_30,
        "reverted_from_90": reverted_from_90, "min_after_90": min_after_90,
        "reverted_from_10": reverted_from_10, "max_after_10": max_after_10,
        "resolved_high": resolved_high, "resolved_low": resolved_low,
        "last_bid": last_bid, "last_ask": last_ask,
        "avg_spread": avg_spread, "max_spread": max_spread,
    }


# ─── Pairing UP/DOWN tokens ─────────────────────────────────────────────

def pair_market_sides(analyzed):
    """
    Try to pair tokens that are opposing sides of the same market.
    Two tokens in the same hour whose mid prices sum to ~1.0 are likely pairs.
    """
    # Group by (session, hour)
    groups = defaultdict(list)
    for a in analyzed:
        groups[(a["session"], a["hour"])].append(a)

    pairs = []
    for key, tokens in groups.items():
        used = set()
        for i, t1 in enumerate(tokens):
            if i in used:
                continue
            for j, t2 in enumerate(tokens):
                if j in used or j <= i:
                    continue
                # Check if complementary (mid prices sum to ~1.0)
                sum_mid = t1["first_mid"] + t2["first_mid"]
                if 0.90 < sum_mid < 1.10:
                    pairs.append((t1, t2))
                    used.add(i)
                    used.add(j)
                    break
    return pairs


# ─── Reversion Deep Dive ────────────────────────────────────────────────

def analyze_reversion_detailed(analyzed):
    """For tokens that hit 0.90+, track exactly what happens after."""
    results = []
    for a in analyzed:
        snaps = a["snapshots"]
        bids = [s["bid"] for s in snaps]

        # Find all times bid >= 0.90
        for i, b in enumerate(bids):
            if b >= 0.90:
                # Track: min price after this point, final price, time to revert
                bids_after = bids[i:]
                min_after = min(bids_after)
                final = bids[-1]

                # How far does it drop?
                max_drop = b - min_after

                # Does it end high or low?
                ended_high = final >= 0.85
                ended_low = final < 0.50

                results.append({
                    "session": a["session"],
                    "hour": a["hour"],
                    "price_at_trigger": b,
                    "minute_of_trigger": snaps[i]["minutes_in"],
                    "min_after": min_after,
                    "max_drop": max_drop,
                    "final_price": final,
                    "ended_high": ended_high,
                    "ended_low": ended_low,
                    "snaps_remaining": len(bids_after),
                })
                break  # Only first hit per token-hour
    return results


def analyze_both_sides_cheap(pairs):
    """
    For paired markets: can you ever buy BOTH sides cheaply?
    If UP ask <= X and DOWN ask <= X simultaneously, you have a guaranteed profit.
    """
    results = []
    for t1, t2 in pairs:
        snaps1 = {s["ts"]: s for s in t1["snapshots"]}
        snaps2 = {s["ts"]: s for s in t2["snapshots"]}

        # Find overlapping timestamps (within 5 seconds)
        min_combined_ask = 999.0
        best_ts = None
        both_below_50 = False
        both_below_45 = False
        both_below_40 = False

        for ts1, s1 in snaps1.items():
            # Find closest snap in t2
            best_match = None
            best_diff = 999
            for ts2, s2 in snaps2.items():
                diff = abs((ts1 - ts2).total_seconds())
                if diff < best_diff:
                    best_diff = diff
                    best_match = s2

            if best_match and best_diff < 10:  # Within 10 seconds
                combined = s1["ask"] + best_match["ask"]
                if combined < min_combined_ask:
                    min_combined_ask = combined
                    best_ts = ts1

                if s1["ask"] <= 0.50 and best_match["ask"] <= 0.50:
                    both_below_50 = True
                if s1["ask"] <= 0.45 and best_match["ask"] <= 0.45:
                    both_below_45 = True
                if s1["ask"] <= 0.40 and best_match["ask"] <= 0.40:
                    both_below_40 = True

        results.append({
            "session": t1["session"],
            "hour": t1["hour"],
            "min_combined_ask": min_combined_ask,
            "best_ts": best_ts,
            "both_below_50": both_below_50,
            "both_below_45": both_below_45,
            "both_below_40": both_below_40,
            "t1_min_ask": t1["min_ask"],
            "t2_min_ask": t2["min_ask"],
            "profit_if_bought": max(0, 1.0 - min_combined_ask) if min_combined_ask < 1.0 else 0,
        })
    return results


def analyze_time_patterns(analyzed):
    """When do min/max prices typically occur within the hour?"""
    min_times = []
    max_times = []

    for a in analyzed:
        if a["price_range"] > 0.05:  # Only meaningful volatility
            min_times.append(a["min_time_min"])
            max_times.append(a["max_time_min"])

    # Bucket into 10-minute windows
    buckets_min = defaultdict(int)
    buckets_max = defaultdict(int)
    for t in min_times:
        bucket = int(t // 10) * 10
        buckets_min[bucket] += 1
    for t in max_times:
        bucket = int(t // 10) * 10
        buckets_max[bucket] += 1

    return buckets_min, buckets_max, min_times, max_times


def analyze_price_at_minute(analyzed):
    """What's the typical price at each minute mark? When is volatility highest?"""
    # For each minute bucket, collect all mid prices
    minute_prices = defaultdict(list)
    minute_spreads = defaultdict(list)

    for a in analyzed:
        for s in a["snapshots"]:
            minute = int(s["minutes_in"])
            if 0 <= minute <= 59:
                minute_prices[minute].append(s["mid"])
                minute_spreads[minute].append(s["ask"] - s["bid"])

    return minute_prices, minute_spreads


def analyze_momentum(analyzed):
    """
    If price moves X% in first 15 min, what happens next?
    Momentum vs mean-reversion analysis.
    """
    results = []
    for a in analyzed:
        snaps = a["snapshots"]

        # Get price at ~15 minutes
        early_snaps = [s for s in snaps if s["minutes_in"] <= 15]
        late_snaps = [s for s in snaps if s["minutes_in"] >= 15]

        if not early_snaps or not late_snaps:
            continue

        first_mid = snaps[0]["mid"]
        mid_at_15 = early_snaps[-1]["mid"]
        last_mid = snaps[-1]["mid"]

        if first_mid < 0.10 or first_mid > 0.90:
            continue

        early_move = mid_at_15 - first_mid
        late_move = last_mid - mid_at_15

        results.append({
            "first_mid": first_mid,
            "mid_at_15": mid_at_15,
            "last_mid": last_mid,
            "early_move": early_move,
            "late_move": late_move,
            "momentum": (early_move > 0 and late_move > 0) or (early_move < 0 and late_move < 0),
            "reversion": (early_move > 0 and late_move < 0) or (early_move < 0 and late_move > 0),
        })

    return results


def analyze_fade_the_extreme(analyzed):
    """
    If you buy when price drops to X, or sell when price rises to Y,
    what's your expected value at resolution?
    """
    thresholds = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
    buy_results = defaultdict(list)  # threshold -> list of (entry_price, final_price)

    for a in analyzed:
        snaps = a["snapshots"]
        asks = [s["ask"] for s in snaps]
        final_bid = snaps[-1]["bid"]

        for thresh in thresholds:
            # Did ask ever drop to this level?
            if any(ask <= thresh for ask in asks):
                # What price could you have bought at?
                entry = min(ask for ask in asks if ask <= thresh)
                buy_results[thresh].append({
                    "entry": entry,
                    "final": final_bid,
                    "pnl": final_bid - entry,
                    "session": a["session"],
                    "hour": a["hour"],
                })

    # Sell side: what if you sell when bid >= X?
    sell_thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    sell_results = defaultdict(list)

    for a in analyzed:
        snaps = a["snapshots"]
        bids = [s["bid"] for s in snaps]
        final_ask = snaps[-1]["ask"]

        for thresh in sell_thresholds:
            if any(bid >= thresh for bid in bids):
                exit_price = max(bid for bid in bids if bid >= thresh)
                sell_results[thresh].append({
                    "exit": exit_price,
                    "final": final_ask,
                    "pnl": exit_price - final_ask,  # Positive if sold high and it dropped
                    "session": a["session"],
                    "hour": a["hour"],
                })

    return buy_results, sell_results


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("POLYMARKET 1-HOUR CRYPTO MARKET STRUCTURE ANALYSIS")
    print("=" * 80)
    print()

    # Load data
    print("Loading hourly price data...")
    market_hours = load_hourly_price_paths()
    print(f"Found {len(market_hours)} token-hours with price data")
    print()

    # Analyze each
    print("Analyzing each market-hour...")
    analyzed = [analyze_market_hour(mh) for mh in market_hours]
    print(f"Analyzed {len(analyzed)} token-hour combinations")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Q1: Can you ALWAYS buy at 0.45?
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q1: CAN YOU ALWAYS BUY A TOKEN AT $0.45 OR LESS?")
    print("=" * 80)

    total = len(analyzed)
    below_45 = sum(1 for a in analyzed if a["hit_below_45"])
    below_50 = sum(1 for a in analyzed if a["hit_below_50"])
    below_40 = sum(1 for a in analyzed if a["hit_below_40"])
    below_35 = sum(1 for a in analyzed if a["hit_below_35"])
    below_30 = sum(1 for a in analyzed if a["hit_below_30"])

    print(f"  Total token-hours analyzed: {total}")
    print(f"  Ask drops to <=$0.50:  {below_50:>4}/{total} = {below_50/total*100:.1f}%")
    print(f"  Ask drops to <=$0.45:  {below_45:>4}/{total} = {below_45/total*100:.1f}%")
    print(f"  Ask drops to <=$0.40:  {below_40:>4}/{total} = {below_40/total*100:.1f}%")
    print(f"  Ask drops to <=$0.35:  {below_35:>4}/{total} = {below_35/total*100:.1f}%")
    print(f"  Ask drops to <=$0.30:  {below_30:>4}/{total} = {below_30/total*100:.1f}%")
    print()

    # Min ask distribution
    min_asks = sorted([a["min_ask"] for a in analyzed])
    print(f"  Min ask price distribution:")
    print(f"    Lowest:  {min(min_asks):.3f}")
    print(f"    P10:     {min_asks[int(len(min_asks)*0.10)]:.3f}")
    print(f"    P25:     {min_asks[int(len(min_asks)*0.25)]:.3f}")
    print(f"    Median:  {min_asks[int(len(min_asks)*0.50)]:.3f}")
    print(f"    P75:     {min_asks[int(len(min_asks)*0.75)]:.3f}")
    print(f"    P90:     {min_asks[int(len(min_asks)*0.90)]:.3f}")
    print(f"    Highest: {max(min_asks):.3f}")
    print()

    # Tokens that NEVER went below 0.50 — what were they?
    never_below_50 = [a for a in analyzed if not a["hit_below_50"]]
    if never_below_50:
        print(f"  Tokens that NEVER had ask <= $0.50 ({len(never_below_50)} cases):")
        for a in never_below_50[:10]:
            print(f"    {a['session']} h{a['hour']:02d} | min_ask={a['min_ask']:.3f} max_bid={a['max_bid']:.3f} range={a['price_range']:.3f}")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Q2: When price hits 0.90+, does it come back?
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q2: WHEN PRICE HITS $0.90+, HOW OFTEN DOES IT REVERT?")
    print("=" * 80)

    hit_90 = [a for a in analyzed if a["hit_90_plus"]]
    reverted = [a for a in hit_90 if a["reverted_from_90"]]

    print(f"  Token-hours that hit bid >= $0.90: {len(hit_90)}/{total} = {len(hit_90)/total*100:.1f}%")
    print(f"  Of those, reverted below $0.80:   {len(reverted)}/{len(hit_90)} = {len(reverted)/len(hit_90)*100:.1f}%" if hit_90 else "  N/A")
    print()

    # Detailed reversion
    reversion_data = analyze_reversion_detailed(analyzed)
    if reversion_data:
        # Group by minute of trigger
        early_triggers = [r for r in reversion_data if r["minute_of_trigger"] < 30]
        late_triggers = [r for r in reversion_data if r["minute_of_trigger"] >= 30]

        print(f"  Detailed reversion from $0.90+ ({len(reversion_data)} events):")

        drops = [r["max_drop"] for r in reversion_data]
        print(f"    Max drop after hitting 0.90:")
        print(f"      Mean:   {statistics.mean(drops):.3f}")
        print(f"      Median: {statistics.median(drops):.3f}")
        print(f"      Max:    {max(drops):.3f}")
        print()

        ended_high = sum(1 for r in reversion_data if r["ended_high"])
        ended_low = sum(1 for r in reversion_data if r["ended_low"])
        print(f"    After hitting 0.90+:")
        print(f"      Ended >= 0.85 (stayed high): {ended_high}/{len(reversion_data)} = {ended_high/len(reversion_data)*100:.1f}%")
        print(f"      Ended < 0.50 (full revert):  {ended_low}/{len(reversion_data)} = {ended_low/len(reversion_data)*100:.1f}%")
        print()

        if early_triggers:
            early_ended_high = sum(1 for r in early_triggers if r["ended_high"])
            print(f"    Early triggers (min 0-29): {len(early_triggers)} events, {early_ended_high}/{len(early_triggers)} ended high")
        if late_triggers:
            late_ended_high = sum(1 for r in late_triggers if r["ended_high"])
            print(f"    Late triggers (min 30-59):  {len(late_triggers)} events, {late_ended_high}/{len(late_triggers)} ended high")
    print()

    # Same for low side
    print("  REVERSE: When price drops to $0.10 or less:")
    hit_10 = [a for a in analyzed if a["hit_10_minus"]]
    reverted_10 = [a for a in hit_10 if a["reverted_from_10"]]
    print(f"    Token-hours that hit ask <= $0.10: {len(hit_10)}/{total}")
    print(f"    Of those, bounced back above $0.20: {len(reverted_10)}/{len(hit_10)}" if hit_10 else "    N/A")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Q3: Can you buy BOTH sides at 0.45?
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q3: CAN YOU BUY BOTH SIDES CHEAP? (Guaranteed profit if combined < $1.00)")
    print("=" * 80)

    pairs = pair_market_sides(analyzed)
    both_sides = analyze_both_sides_cheap(pairs)

    print(f"  Paired markets found: {len(pairs)}")

    if both_sides:
        profitable = [b for b in both_sides if b["min_combined_ask"] < 1.0]
        below_95 = [b for b in both_sides if b["min_combined_ask"] < 0.95]
        below_90 = [b for b in both_sides if b["min_combined_ask"] < 0.90]
        both_50 = sum(1 for b in both_sides if b["both_below_50"])
        both_45 = sum(1 for b in both_sides if b["both_below_45"])
        both_40 = sum(1 for b in both_sides if b["both_below_40"])

        print(f"  Combined ask (both sides) < $1.00: {len(profitable)}/{len(both_sides)} = {len(profitable)/len(both_sides)*100:.1f}%")
        print(f"  Combined ask < $0.95:              {len(below_95)}/{len(both_sides)} = {len(below_95)/len(both_sides)*100:.1f}%")
        print(f"  Combined ask < $0.90:              {len(below_90)}/{len(both_sides)} = {len(below_90)/len(both_sides)*100:.1f}%")
        print(f"  Both sides ask <= $0.50 at same time: {both_50}/{len(both_sides)}")
        print(f"  Both sides ask <= $0.45 at same time: {both_45}/{len(both_sides)}")
        print(f"  Both sides ask <= $0.40 at same time: {both_40}/{len(both_sides)}")
        print()

        combined_asks = sorted([b["min_combined_ask"] for b in both_sides])
        print(f"  Min combined ask distribution:")
        print(f"    Lowest:  {min(combined_asks):.3f}")
        print(f"    P10:     {combined_asks[int(len(combined_asks)*0.10)]:.3f}")
        print(f"    P25:     {combined_asks[int(len(combined_asks)*0.25)]:.3f}")
        print(f"    Median:  {combined_asks[int(len(combined_asks)*0.50)]:.3f}")
        print(f"    P75:     {combined_asks[int(len(combined_asks)*0.75)]:.3f}")
        print(f"    P90:     {combined_asks[int(len(combined_asks)*0.90)]:.3f}")
        print(f"    Highest: {max(combined_asks):.3f}")
        print()

        if profitable:
            print(f"  Top 10 cheapest combined asks (arbitrage opportunities):")
            for b in sorted(profitable, key=lambda x: x["min_combined_ask"])[:10]:
                print(f"    {b['session']} h{b['hour']:02d} | combined={b['min_combined_ask']:.3f} | side1_min={b['t1_min_ask']:.3f} side2_min={b['t2_min_ask']:.3f} | profit={b['profit_if_bought']:.3f}")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Q4: Volatility and Price Range
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q4: PRICE VOLATILITY — HOW MUCH DO PRICES MOVE WITHIN AN HOUR?")
    print("=" * 80)

    ranges = sorted([a["price_range"] for a in analyzed])
    print(f"  Price range (max_mid - min_mid) distribution:")
    print(f"    Lowest:  {min(ranges):.3f}")
    print(f"    P10:     {ranges[int(len(ranges)*0.10)]:.3f}")
    print(f"    P25:     {ranges[int(len(ranges)*0.25)]:.3f}")
    print(f"    Median:  {ranges[int(len(ranges)*0.50)]:.3f}")
    print(f"    P75:     {ranges[int(len(ranges)*0.75)]:.3f}")
    print(f"    P90:     {ranges[int(len(ranges)*0.90)]:.3f}")
    print(f"    Highest: {max(ranges):.3f}")
    print()

    # Bucket ranges
    range_buckets = defaultdict(int)
    for r in ranges:
        if r < 0.05:
            range_buckets["< 5c"] += 1
        elif r < 0.10:
            range_buckets["5-10c"] += 1
        elif r < 0.20:
            range_buckets["10-20c"] += 1
        elif r < 0.30:
            range_buckets["20-30c"] += 1
        elif r < 0.50:
            range_buckets["30-50c"] += 1
        else:
            range_buckets["50c+"] += 1

    print(f"  Range buckets:")
    for bucket in ["< 5c", "5-10c", "10-20c", "20-30c", "30-50c", "50c+"]:
        count = range_buckets[bucket]
        print(f"    {bucket:>8}: {count:>4} ({count/total*100:.1f}%)")
    print()

    # Spread analysis
    spreads = [a["avg_spread"] for a in analyzed]
    print(f"  Average bid-ask spread:")
    print(f"    Mean:   {statistics.mean(spreads):.4f}")
    print(f"    Median: {statistics.median(spreads):.4f}")
    print(f"    Max:    {max(spreads):.4f}")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Q5: Time-of-hour patterns
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q5: WHEN DO EXTREMES HAPPEN? (Time within the hour)")
    print("=" * 80)

    buckets_min, buckets_max, min_times, max_times = analyze_time_patterns(analyzed)

    print(f"  When does the MINIMUM price occur? (10-min buckets, volatile markets only)")
    for bucket in sorted(buckets_min.keys()):
        count = buckets_min[bucket]
        total_vol = sum(buckets_min.values())
        bar = "#" * int(count / total_vol * 50)
        print(f"    min {bucket:>2}-{bucket+9}: {count:>4} ({count/total_vol*100:.1f}%) {bar}")
    print()

    print(f"  When does the MAXIMUM price occur?")
    for bucket in sorted(buckets_max.keys()):
        count = buckets_max[bucket]
        total_vol = sum(buckets_max.values())
        bar = "#" * int(count / total_vol * 50)
        print(f"    min {bucket:>2}-{bucket+9}: {count:>4} ({count/total_vol*100:.1f}%) {bar}")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Q6: Momentum vs Mean Reversion
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q6: MOMENTUM VS MEAN REVERSION")
    print("=" * 80)

    momentum = analyze_momentum(analyzed)
    if momentum:
        mom_count = sum(1 for m in momentum if m["momentum"])
        rev_count = sum(1 for m in momentum if m["reversion"])
        total_m = len(momentum)

        print(f"  Based on first 15 min move vs rest of hour:")
        print(f"    Momentum (continues): {mom_count}/{total_m} = {mom_count/total_m*100:.1f}%")
        print(f"    Reversion (reverses): {rev_count}/{total_m} = {rev_count/total_m*100:.1f}%")
        print()

        # Break down by size of early move
        big_up = [m for m in momentum if m["early_move"] > 0.10]
        big_down = [m for m in momentum if m["early_move"] < -0.10]
        small = [m for m in momentum if abs(m["early_move"]) <= 0.10]

        if big_up:
            up_mom = sum(1 for m in big_up if m["momentum"])
            print(f"    Big early UP (>10c): {len(big_up)} cases, momentum {up_mom}/{len(big_up)} = {up_mom/len(big_up)*100:.1f}%")
        if big_down:
            down_mom = sum(1 for m in big_down if m["momentum"])
            print(f"    Big early DOWN (>10c): {len(big_down)} cases, momentum {down_mom}/{len(big_down)} = {down_mom/len(big_down)*100:.1f}%")
        if small:
            small_mom = sum(1 for m in small if m["momentum"])
            print(f"    Small early move (<=10c): {len(small)} cases, momentum {small_mom}/{len(small)} = {small_mom/len(small)*100:.1f}%")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Q7: "Fade the Extreme" — buy dips / sell rips EV
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q7: FADE THE EXTREME — BUY THE DIP / SELL THE RIP EXPECTED VALUE")
    print("=" * 80)

    buy_results, sell_results = analyze_fade_the_extreme(analyzed)

    print(f"  BUY when ask drops to threshold (then hold to resolution):")
    print(f"  {'Threshold':>10} | {'Count':>6} | {'Avg PnL':>8} | {'Win%':>6} | {'Avg Entry':>10} | {'Avg Final':>10}")
    print(f"  {'-'*10}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*10}-+-{'-'*10}")
    for thresh in sorted(buy_results.keys()):
        results = buy_results[thresh]
        avg_pnl = statistics.mean([r["pnl"] for r in results])
        win_rate = sum(1 for r in results if r["pnl"] > 0) / len(results) * 100
        avg_entry = statistics.mean([r["entry"] for r in results])
        avg_final = statistics.mean([r["final"] for r in results])
        print(f"  <=${thresh:.2f}    | {len(results):>6} | ${avg_pnl:>+7.3f} | {win_rate:>5.1f}% | ${avg_entry:>9.3f} | ${avg_final:>9.3f}")
    print()

    print(f"  SELL when bid rises to threshold (profit = exit - cost_to_close):")
    print(f"  {'Threshold':>10} | {'Count':>6} | {'Avg PnL':>8} | {'Win%':>6} | {'Avg Exit':>10} | {'Avg Final':>10}")
    print(f"  {'-'*10}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*10}-+-{'-'*10}")
    for thresh in sorted(sell_results.keys()):
        results = sell_results[thresh]
        avg_pnl = statistics.mean([r["pnl"] for r in results])
        win_rate = sum(1 for r in results if r["pnl"] > 0) / len(results) * 100
        avg_exit = statistics.mean([r["exit"] for r in results])
        avg_final = statistics.mean([r["final"] for r in results])
        print(f"  >=${thresh:.2f}    | {len(results):>6} | ${avg_pnl:>+7.3f} | {win_rate:>5.1f}% | ${avg_exit:>9.3f} | ${avg_final:>9.3f}")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Q8: Resolution outcomes
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q8: HOW DO MARKETS RESOLVE? (End-of-hour price distribution)")
    print("=" * 80)

    last_bids = [a["last_bid"] for a in analyzed]
    last_asks = [a["last_ask"] for a in analyzed]

    resolved_high = sum(1 for b in last_bids if b >= 0.90)
    resolved_low = sum(1 for a in last_asks if a <= 0.10)
    resolved_mid = total - resolved_high - resolved_low

    print(f"  End-of-hour outcomes:")
    print(f"    Resolved HIGH (bid >= 0.90): {resolved_high}/{total} = {resolved_high/total*100:.1f}%")
    print(f"    Resolved LOW  (ask <= 0.10): {resolved_low}/{total} = {resolved_low/total*100:.1f}%")
    print(f"    Still MID  (neither):       {resolved_mid}/{total} = {resolved_mid/total*100:.1f}%")
    print()

    # Last bid distribution
    last_bids_sorted = sorted(last_bids)
    print(f"  Final bid price distribution:")
    print(f"    P10: {last_bids_sorted[int(len(last_bids_sorted)*0.10)]:.3f}")
    print(f"    P25: {last_bids_sorted[int(len(last_bids_sorted)*0.25)]:.3f}")
    print(f"    P50: {last_bids_sorted[int(len(last_bids_sorted)*0.50)]:.3f}")
    print(f"    P75: {last_bids_sorted[int(len(last_bids_sorted)*0.75)]:.3f}")
    print(f"    P90: {last_bids_sorted[int(len(last_bids_sorted)*0.90)]:.3f}")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Q9: Spread patterns and execution edge
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q9: SPREAD PATTERNS — WHEN ARE SPREADS WIDEST? (Execution cost)")
    print("=" * 80)

    minute_prices, minute_spreads = analyze_price_at_minute(analyzed)

    print(f"  Average spread by minute of hour:")
    for minute in range(0, 60, 5):
        if minute in minute_spreads:
            avg_sp = statistics.mean(minute_spreads[minute])
            n = len(minute_spreads[minute])
            bar = "#" * int(avg_sp * 200)
            print(f"    min {minute:>2}: spread={avg_sp:.4f} (n={n:>4}) {bar}")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # Q10: Opening price analysis
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q10: OPENING PRICES — WHERE DO MARKETS TYPICALLY START?")
    print("=" * 80)

    first_mids = sorted([a["first_mid"] for a in analyzed])
    print(f"  First observed mid price distribution:")
    print(f"    P10: {first_mids[int(len(first_mids)*0.10)]:.3f}")
    print(f"    P25: {first_mids[int(len(first_mids)*0.25)]:.3f}")
    print(f"    Median: {first_mids[int(len(first_mids)*0.50)]:.3f}")
    print(f"    P75: {first_mids[int(len(first_mids)*0.75)]:.3f}")
    print(f"    P90: {first_mids[int(len(first_mids)*0.90)]:.3f}")
    print()

    # Bucket by starting price
    start_buckets = defaultdict(int)
    for m in first_mids:
        if m < 0.30:
            start_buckets["< 0.30"] += 1
        elif m < 0.40:
            start_buckets["0.30-0.40"] += 1
        elif m < 0.50:
            start_buckets["0.40-0.50"] += 1
        elif m < 0.60:
            start_buckets["0.50-0.60"] += 1
        elif m < 0.70:
            start_buckets["0.60-0.70"] += 1
        else:
            start_buckets["0.70+"] += 1

    print(f"  Starting price buckets:")
    for bucket in ["< 0.30", "0.30-0.40", "0.40-0.50", "0.50-0.60", "0.60-0.70", "0.70+"]:
        count = start_buckets[bucket]
        print(f"    {bucket:>10}: {count:>4} ({count/total*100:.1f}%)")
    print()

    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY: Exploitable Edges
    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("SUMMARY: POTENTIAL EXPLOITABLE EDGES")
    print("=" * 80)
    print()
    print("  1. BOTH-SIDES CHEAP BUY:")
    if both_sides:
        profitable_count = sum(1 for b in both_sides if b["min_combined_ask"] < 1.0)
        if profitable_count:
            avg_profit = statistics.mean([b["profit_if_bought"] for b in both_sides if b["min_combined_ask"] < 1.0])
            print(f"     {profitable_count}/{len(both_sides)} hours had combined ask < $1.00 (guaranteed profit)")
            print(f"     Average profit per $1 deployed: ${avg_profit:.3f}")
        else:
            print(f"     No guaranteed arbitrage found (combined ask always >= $1.00)")
    print()

    print("  2. HIGH-PRICE REVERSION:")
    if reversion_data:
        stayed_pct = sum(1 for r in reversion_data if r["ended_high"]) / len(reversion_data) * 100
        print(f"     Price hits $0.90+: {stayed_pct:.0f}% stay high -> fading $0.90 is {'risky' if stayed_pct > 60 else 'profitable'}")
    print()

    print("  3. VOLATILITY EXPLOITATION:")
    median_range = ranges[len(ranges)//2]
    print(f"     Median intra-hour range: ${median_range:.3f}")
    print(f"     {sum(1 for r in ranges if r > 0.20)/total*100:.0f}% of hours have >20c range")
    print()

    print("  4. MOMENTUM/REVERSION SIGNAL:")
    if momentum:
        mom_pct = sum(1 for m in momentum if m["momentum"]) / len(momentum) * 100
        print(f"     First 15 min direction continues: {mom_pct:.0f}% of time")
        print(f"     {'MOMENTUM market' if mom_pct > 55 else 'MEAN-REVERTING market' if mom_pct < 45 else 'NO CLEAR SIGNAL'}")
    print()

    print("Done!")


if __name__ == "__main__":
    main()
