"""
Focused analysis: buying at $0.85, momentum strategy, reversal risk.
Uses API data (230 markets) + session data (640 token-hours).
"""
import json
import statistics
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATA_DIR = Path("data/sessions")
API_CACHE = Path("data/api_market_cache.json")
CLOB_URL = "https://clob.polymarket.com"


# ── Load session data (2-second resolution) ──────────────────────────────

def load_session_data():
    """Load per-token price paths from hourly session files."""
    market_hours = []
    for date_dir in sorted(DATA_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.glob("*_hour_*.jsonl")):
            parts = f.stem.split("_hour_")
            if len(parts) != 2:
                continue
            hour_num = int(parts[1])
            session_name = parts[0]
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
                            "ts": ts, "bid": bid, "ask": ask,
                            "mid": (bid + ask) / 2,
                            "minutes_in": ts.minute + ts.second / 60,
                        })

            for token_id, snapshots in token_prices.items():
                if len(snapshots) < 10:
                    continue
                snaps = sorted(snapshots, key=lambda x: x["ts"])
                mids = [s["mid"] for s in snaps]
                price_range = max(mids) - min(mids)
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


# ── Load API data ────────────────────────────────────────────────────────

def load_api_data():
    """Load API markets and fetch price histories."""
    import requests, time

    if not API_CACHE.exists():
        print("No API cache found. Run fetch_and_analyze_api.py first.")
        return []

    with open(API_CACHE, encoding="utf-8") as f:
        markets = json.load(f)

    # Sort by volume, take active ones
    markets_by_vol = sorted(markets, key=lambda m: m.get('volume_num', m.get('volume24hr', 0)) or 0, reverse=True)
    active = [m for m in markets_by_vol if (m.get('volume_num', m.get('volume24hr', 0)) or 0) > 0]

    results = []
    errors = 0
    for i, m in enumerate(active[:300]):
        tokens_str = m.get('clobTokenIds', '[]')
        try:
            tokens = json.loads(tokens_str)
        except:
            continue
        if len(tokens) < 2:
            continue

        for token_idx, token_id in enumerate(tokens[:2]):
            try:
                r = requests.get(f"{CLOB_URL}/prices-history", params={
                    'market': token_id, 'interval': 'max', 'fidelity': 1,
                }, timeout=10)
                if r.status_code == 200:
                    history = r.json().get('history', [])
                    if history and len(history) >= 5:
                        prices = [h['p'] for h in history]
                        timestamps = [h['t'] for h in history]
                        price_range = max(prices) - min(prices)
                        if price_range > 0.05:
                            results.append({
                                'question': m.get('question', ''),
                                'slug': m.get('slug', ''),
                                'token_idx': token_idx,
                                'prices': prices,
                                'timestamps': timestamps,
                                'n_points': len(prices),
                                'duration_min': (timestamps[-1] - timestamps[0]) / 60 if len(timestamps) > 1 else 0,
                            })
                            break  # Only need one side per market
            except:
                errors += 1

            time.sleep(0.03)

        if (i + 1) % 50 == 0:
            print(f"  Fetched {i+1}/{len(active[:300])}, got {len(results)} valid")

    print(f"API data: {len(results)} markets with price histories")
    return results


def main():
    print("=" * 80)
    print("FOCUSED EDGE ANALYSIS")
    print("=" * 80)

    # ── Load data ──
    print("\nLoading session data (2-sec resolution)...")
    session_data = load_session_data()
    print(f"  Session: {len(session_data)} active token-hours")

    print("\nLoading API data...")
    api_data = load_api_data()
    print(f"  API: {len(api_data)} markets with price histories")

    # ═══════════════════════════════════════════════════════════════════════
    # SAMPLE SIZE
    # ═══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("SAMPLE SIZE")
    print("=" * 80)
    print(f"  Session data: {len(session_data)} token-hours (6 days, 2-sec snapshots)")
    print(f"  API data: {len(api_data)} unique markets from Gamma API")
    print(f"  API cache: 2,652 total crypto up/down markets found")
    print(f"  API with volume: 230 actively traded markets")

    # Count unique hours in session data
    unique_hours = set()
    for mh in session_data:
        unique_hours.add((mh["session"], mh["hour"]))
    print(f"  Session unique market-hours: {len(unique_hours)}")

    total_snapshots = sum(len(mh["snapshots"]) for mh in session_data)
    print(f"  Total price snapshots: {total_snapshots:,}")

    # ═══════════════════════════════════════════════════════════════════════
    # RESOLUTION FIX: Markets resolve to $0.99 or $0.01, not ambiguously
    # ═══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("RESOLUTION REALITY CHECK")
    print("  Markets resolve to $0.99 or $0.01 (binary)")
    print("  The '18% mid' from API was an artifact of last-trade prices")
    print("=" * 80)

    # From API data, check last prices
    if api_data:
        last_prices = [d['prices'][-1] for d in api_data]
        high = sum(1 for p in last_prices if p >= 0.85)
        low = sum(1 for p in last_prices if p <= 0.15)
        mid = len(last_prices) - high - low
        print(f"  API last-trade prices:")
        print(f"    >= $0.85: {high}/{len(last_prices)} = {high/len(last_prices)*100:.1f}%")
        print(f"    <= $0.15: {low}/{len(last_prices)} = {low/len(last_prices)*100:.1f}%")
        print(f"    In between: {mid}/{len(last_prices)} = {mid/len(last_prices)*100:.1f}% (API artifact, not real)")
        print(f"  TRUE resolution: ~50% UP wins ($0.99), ~50% DOWN wins ($0.01)")
        print(f"  For analysis below, we assume binary resolution.")

    # ═══════════════════════════════════════════════════════════════════════
    # BUYING AT $0.85 — Is it profitable?
    # ═══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("STRATEGY: BUY WHEN PRICE HITS $0.85")
    print("  Entry: buy at $0.85 ask")
    print("  Win: market resolves your way -> $0.99 -> profit $0.14/share")
    print("  Lose: market reverses -> $0.01 -> loss $0.84/share")
    print("=" * 80)

    # Session data analysis (finer granularity, we know resolution)
    print("\n  --- SESSION DATA (2-sec resolution) ---")

    for buy_threshold in [0.80, 0.85, 0.90, 0.95]:
        events = []
        for mh in session_data:
            snaps = mh["snapshots"]
            bids = [s["bid"] for s in snaps]
            asks = [s["ask"] for s in snaps]

            # Find first time bid >= threshold (meaning you could SELL at this price,
            # or equivalently this token's ask hit this level for someone else)
            # Actually: if BID >= 0.85, the market thinks this side wins.
            # To BUY at $0.85, we need ASK <= $0.85... no wait.
            # If bid=0.85, you could sell at 0.85. If ask=0.86, you could buy at 0.86.
            # "Buy when it hits 0.85" means: buy when ask drops to 0.85 or when
            # the price (mid) reaches 0.85.
            # For this side of the market, reaching bid=0.85 means it's ALREADY at 0.85.
            # You'd buy at the ask, which is ~0.86.
            # Let's use mid >= threshold as the trigger.

            for i, s in enumerate(snaps):
                if s["mid"] >= buy_threshold:
                    minute = s["minutes_in"]
                    # What happens? Track to end of hour
                    final_bid = bids[-1]

                    # Determine resolution: if final bid >= 0.90, this side WON
                    # If final bid <= 0.10, this side LOST
                    won = final_bid >= 0.85
                    lost = final_bid <= 0.15

                    # Entry cost is approximately the ask at this point
                    entry_ask = s["ask"]

                    if won:
                        pnl = 0.99 - entry_ask  # resolve at $0.99
                    elif lost:
                        pnl = 0.01 - entry_ask  # resolve at $0.01
                    else:
                        pnl = final_bid - entry_ask  # mid-resolution (rare)

                    events.append({
                        "minute": minute,
                        "entry_ask": entry_ask,
                        "final_bid": final_bid,
                        "won": won,
                        "lost": lost,
                        "pnl": pnl,
                        "session": mh["session"],
                        "hour": mh["hour"],
                    })
                    break  # Only first trigger per token-hour

        if not events:
            continue

        n = len(events)
        wins = sum(1 for e in events if e["won"])
        losses = sum(1 for e in events if e["lost"])
        avg_pnl = statistics.mean([e["pnl"] for e in events])
        avg_entry = statistics.mean([e["entry_ask"] for e in events])

        print(f"\n  Buy when mid >= ${buy_threshold:.2f} ({n} events):")
        print(f"    Win rate: {wins}/{n} = {wins/n*100:.1f}%")
        print(f"    Loss rate: {losses}/{n} = {losses/n*100:.1f}%")
        print(f"    Avg entry ask: ${avg_entry:.3f}")
        print(f"    Avg PnL/trade: ${avg_pnl:+.4f}")
        print(f"    On $10 per trade: ${avg_pnl * 10 / avg_entry:+.2f}")

        # By timing
        for min_lo, min_hi, label in [(0, 15, "min 0-14"), (15, 30, "min 15-29"), (30, 45, "min 30-44"), (45, 60, "min 45-59")]:
            bucket = [e for e in events if min_lo <= e["minute"] < min_hi]
            if bucket:
                b_wins = sum(1 for e in bucket if e["won"])
                b_avg = statistics.mean([e["pnl"] for e in bucket])
                print(f"      {label}: {len(bucket)} events, WR={b_wins}/{len(bucket)} ({b_wins/len(bucket)*100:.0f}%), avg PnL=${b_avg:+.4f}")

        # Show worst reversals
        reversals = [e for e in events if e["lost"]]
        if reversals:
            print(f"    REVERSALS (bought at ${buy_threshold:.2f}+, resolved at $0.01):")
            print(f"      Count: {len(reversals)}/{n} = {len(reversals)/n*100:.1f}%")
            for r in sorted(reversals, key=lambda x: x["minute"])[:10]:
                print(f"        {r['session']} h{r['hour']:02d} min{r['minute']:.0f} | bought at ${r['entry_ask']:.3f} -> final ${r['final_bid']:.3f} | PnL ${r['pnl']:+.3f}")

    # ═══════════════════════════════════════════════════════════════════════
    # MOMENTUM STRATEGY: 10c move in first 10 min -> follow it
    # ═══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("STRATEGY: FOLLOW 10c+ MOMENTUM (first 10 min)")
    print("  If price moves 10c+ in one direction in first 10 min, BUY that direction")
    print("  Entry: buy at current ask after the 10c move")
    print("  Hold to resolution: $0.99 if right, $0.01 if wrong")
    print("=" * 80)

    for move_threshold in [0.05, 0.08, 0.10, 0.15, 0.20]:
        events = []
        for mh in session_data:
            snaps = mh["snapshots"]
            # Get price at minute 0 and minute 10
            early = [s for s in snaps if s["minutes_in"] <= 2]
            at_10 = [s for s in snaps if 9 <= s["minutes_in"] <= 11]

            if not early or not at_10:
                continue

            first_mid = early[0]["mid"]
            mid_at_10 = at_10[-1]["mid"]
            move = mid_at_10 - first_mid

            if abs(move) < move_threshold:
                continue

            # Direction: UP or DOWN
            direction = "UP" if move > 0 else "DOWN"

            # Entry: if UP, buy at ask at minute 10. If DOWN, we'd buy the OTHER side
            # But we only have this token's data. If this token went UP by 10c+,
            # we buy this token. The entry is the ask at minute 10.
            entry_ask = at_10[-1]["ask"]

            # Final resolution
            final_bid = snaps[-1]["bid"]

            if direction == "UP":
                # We bought this token (the one that went up)
                won = final_bid >= 0.85
                lost = final_bid <= 0.15
                if won:
                    pnl = 0.99 - entry_ask
                elif lost:
                    pnl = 0.01 - entry_ask
                else:
                    pnl = final_bid - entry_ask
            else:
                # This token went DOWN. We'd want to BUY the other side.
                # For this token going down, the other side went up.
                # We'd sell this token or buy the complement.
                # Let's model it as: we SHORT this token at the bid at minute 10
                entry_bid = at_10[-1]["bid"]
                won = final_bid <= 0.15  # This token crashes = our short wins
                lost = final_bid >= 0.85
                if won:
                    pnl = entry_bid - 0.01  # Shorted at bid, resolve at 0.01
                elif lost:
                    pnl = entry_bid - 0.99
                else:
                    pnl = entry_bid - final_bid
                entry_ask = entry_bid  # For display purposes

            events.append({
                "direction": direction,
                "move": move,
                "entry": entry_ask,
                "final": final_bid,
                "won": won,
                "lost": lost,
                "pnl": pnl,
                "minute_10_mid": mid_at_10,
                "session": mh["session"],
                "hour": mh["hour"],
            })

        if not events:
            continue

        n = len(events)
        wins = sum(1 for e in events if e["won"])
        losses = sum(1 for e in events if e["lost"])
        avg_pnl = statistics.mean([e["pnl"] for e in events])
        avg_entry = statistics.mean([abs(e["entry"]) for e in events])

        up_events = [e for e in events if e["direction"] == "UP"]
        down_events = [e for e in events if e["direction"] == "DOWN"]

        print(f"\n  Follow {move_threshold*100:.0f}c+ move ({n} events):")
        print(f"    Win rate: {wins}/{n} = {wins/n*100:.1f}%")
        print(f"    Loss rate: {losses}/{n} = {losses/n*100:.1f}%")
        print(f"    Avg PnL/trade: ${avg_pnl:+.4f}")

        if up_events:
            up_wins = sum(1 for e in up_events if e["won"])
            up_pnl = statistics.mean([e["pnl"] for e in up_events])
            print(f"    UP moves: {len(up_events)} events, WR={up_wins}/{len(up_events)} ({up_wins/len(up_events)*100:.0f}%), avg PnL=${up_pnl:+.4f}")

        if down_events:
            down_wins = sum(1 for e in down_events if e["won"])
            down_pnl = statistics.mean([e["pnl"] for e in down_events])
            print(f"    DOWN moves: {len(down_events)} events, WR={down_wins}/{len(down_events)} ({down_wins/len(down_events)*100:.0f}%), avg PnL=${down_pnl:+.4f}")

        # Reversals detail
        reversals = [e for e in events if e["lost"]]
        if reversals:
            print(f"    REVERSALS ({len(reversals)}/{n} = {len(reversals)/n*100:.1f}%):")
            avg_rev_loss = statistics.mean([e["pnl"] for e in reversals])
            print(f"      Avg loss per reversal: ${avg_rev_loss:+.4f}")
            # Show examples
            for r in reversals[:5]:
                print(f"        {r['session']} h{r['hour']:02d} | {r['direction']} {r['move']:+.3f} -> entry=${r['entry']:.3f} final=${r['final']:.3f} | PnL ${r['pnl']:+.3f}")

        # Expected value per $1 risked
        if avg_entry > 0:
            win_payoff = statistics.mean([e["pnl"] for e in events if e["won"]]) if wins > 0 else 0
            loss_payoff = statistics.mean([e["pnl"] for e in events if e["lost"]]) if losses > 0 else 0
            wr = wins / n
            ev = wr * win_payoff + (1 - wr) * loss_payoff
            print(f"    EV per trade: ${ev:+.4f} (win ${win_payoff:+.3f} x {wr*100:.0f}% + lose ${loss_payoff:+.3f} x {(1-wr)*100:.0f}%)")

    # ═══════════════════════════════════════════════════════════════════════
    # COMBINED: Momentum + Buy at $0.85 threshold
    # ═══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("COMBINED: MOMENTUM CONFIRMATION + HIGH-PRICE ENTRY")
    print("  Wait for 10c+ move, THEN wait for price to hit $0.85+, THEN buy")
    print("=" * 80)

    for move_thresh in [0.05, 0.10, 0.15]:
        for price_thresh in [0.75, 0.80, 0.85, 0.90]:
            events = []
            for mh in session_data:
                snaps = mh["snapshots"]
                early = [s for s in snaps if s["minutes_in"] <= 2]
                at_10 = [s for s in snaps if 9 <= s["minutes_in"] <= 11]

                if not early or not at_10:
                    continue

                first_mid = early[0]["mid"]
                mid_at_10 = at_10[-1]["mid"]
                move = mid_at_10 - first_mid

                if move < move_thresh:  # Only follow UP moves on this token
                    continue

                # Now wait for price to hit threshold
                remaining = [s for s in snaps if s["minutes_in"] >= 10]
                hit = False
                for s in remaining:
                    if s["mid"] >= price_thresh:
                        entry_ask = s["ask"]
                        minute = s["minutes_in"]
                        hit = True
                        break

                if not hit:
                    continue

                final_bid = snaps[-1]["bid"]
                won = final_bid >= 0.85
                lost = final_bid <= 0.15

                if won:
                    pnl = 0.99 - entry_ask
                elif lost:
                    pnl = 0.01 - entry_ask
                else:
                    pnl = final_bid - entry_ask

                events.append({
                    "entry": entry_ask, "final": final_bid,
                    "won": won, "lost": lost, "pnl": pnl,
                    "minute": minute,
                })

            if len(events) < 5:
                continue

            n = len(events)
            wins = sum(1 for e in events if e["won"])
            losses = sum(1 for e in events if e["lost"])
            avg_pnl = statistics.mean([e["pnl"] for e in events])

            print(f"  Move>{move_thresh*100:.0f}c + price>=${price_thresh:.2f}: {n} events | WR={wins}/{n} ({wins/n*100:.0f}%) | Reversals={losses}/{n} ({losses/n*100:.0f}%) | PnL=${avg_pnl:+.4f}")

    # ═══════════════════════════════════════════════════════════════════════
    # DETAILED $0.85 REVERSAL ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("DETAILED: EVERY TIME PRICE HIT $0.85+ AND THEN REVERSED")
    print("=" * 80)

    all_85_events = []
    for mh in session_data:
        snaps = mh["snapshots"]
        for i, s in enumerate(snaps):
            if s["mid"] >= 0.85:
                final_bid = snaps[-1]["bid"]
                won = final_bid >= 0.85
                lost = final_bid <= 0.15
                entry_ask = s["ask"]
                if won:
                    pnl = 0.99 - entry_ask
                elif lost:
                    pnl = 0.01 - entry_ask
                else:
                    pnl = final_bid - entry_ask

                all_85_events.append({
                    "session": mh["session"], "hour": mh["hour"],
                    "minute": s["minutes_in"],
                    "entry_ask": entry_ask,
                    "final_bid": final_bid,
                    "won": won, "lost": lost, "pnl": pnl,
                })
                break

    if all_85_events:
        n = len(all_85_events)
        wins = sum(1 for e in all_85_events if e["won"])
        losses = sum(1 for e in all_85_events if e["lost"])

        print(f"\n  Total times a token hit mid >= $0.85: {n}")
        print(f"  Won (resolved >= $0.85): {wins}/{n} = {wins/n*100:.1f}%")
        print(f"  REVERSED (resolved <= $0.15): {losses}/{n} = {losses/n*100:.1f}%")

        # Profit math
        avg_win_pnl = statistics.mean([e["pnl"] for e in all_85_events if e["won"]]) if wins else 0
        avg_loss_pnl = statistics.mean([e["pnl"] for e in all_85_events if e["lost"]]) if losses else 0
        total_pnl = sum(e["pnl"] for e in all_85_events)
        print(f"\n  Avg win: ${avg_win_pnl:+.4f} per share")
        print(f"  Avg loss: ${avg_loss_pnl:+.4f} per share")
        print(f"  Total PnL across all {n} trades: ${total_pnl:+.2f} per share")
        print(f"  Avg PnL per trade: ${total_pnl/n:+.4f}")
        print(f"  On $100 capital per trade: ${total_pnl/n * 100 / 0.86:+.2f}")

        # Break down by minute
        print(f"\n  By minute of entry:")
        for min_lo, min_hi, label in [(0, 10, "min 0-9"), (10, 20, "min 10-19"), (20, 30, "min 20-29"), (30, 40, "min 30-39"), (40, 50, "min 40-49"), (50, 60, "min 50-59")]:
            bucket = [e for e in all_85_events if min_lo <= e["minute"] < min_hi]
            if bucket:
                b_w = sum(1 for e in bucket if e["won"])
                b_l = sum(1 for e in bucket if e["lost"])
                b_pnl = statistics.mean([e["pnl"] for e in bucket])
                print(f"    {label}: {len(bucket):>4} events | WR={b_w}/{len(bucket)} ({b_w/len(bucket)*100:.0f}%) | Rev={b_l}/{len(bucket)} ({b_l/len(bucket)*100:.0f}%) | PnL=${b_pnl:+.4f}")

        # List ALL reversals
        reversals = [e for e in all_85_events if e["lost"]]
        print(f"\n  ALL REVERSALS from $0.85+ ({len(reversals)} total):")
        for r in sorted(reversals, key=lambda x: x["minute"]):
            print(f"    {r['session']} h{r['hour']:02d} min{r['minute']:>5.1f} | entry=${r['entry_ask']:.3f} -> ${r['final_bid']:.3f} | PnL ${r['pnl']:+.3f}")

    # ═══════════════════════════════════════════════════════════════════════
    # SAME FOR API DATA (larger sample)
    # ═══════════════════════════════════════════════════════════════════════
    if api_data:
        print("\n" + "=" * 80)
        print("API DATA: BUY AT $0.85 (larger sample)")
        print("=" * 80)

        for buy_thresh in [0.80, 0.85, 0.90]:
            events = []
            for d in api_data:
                prices = d['prices']
                for i, p in enumerate(prices):
                    if p >= buy_thresh:
                        entry = p
                        final = prices[-1]
                        # Assume binary resolution
                        won = final >= 0.80
                        lost = final <= 0.20
                        if won:
                            pnl = 0.99 - entry
                        elif lost:
                            pnl = 0.01 - entry
                        else:
                            # If API shows mid price, assume it resolved to nearest extreme
                            pnl = (0.99 if final > 0.50 else 0.01) - entry

                        pct_through = i / len(prices) * 100

                        events.append({
                            "entry": entry, "final": final,
                            "won": won, "lost": lost, "pnl": pnl,
                            "pct_through": pct_through,
                            "slug": d["slug"],
                        })
                        break

            if events:
                n = len(events)
                wins = sum(1 for e in events if e["won"])
                losses = sum(1 for e in events if e["lost"])
                avg_pnl = statistics.mean([e["pnl"] for e in events])

                print(f"\n  Buy at ${buy_thresh:.2f} ({n} events):")
                print(f"    Win rate: {wins}/{n} = {wins/n*100:.1f}%")
                print(f"    Reversals: {losses}/{n} = {losses/n*100:.1f}%")
                print(f"    Avg PnL: ${avg_pnl:+.4f}")

                # By timing
                for pct_lo, pct_hi, label in [(0, 33, "First third"), (33, 66, "Middle third"), (66, 100, "Last third")]:
                    bucket = [e for e in events if pct_lo <= e["pct_through"] < pct_hi]
                    if bucket:
                        b_w = sum(1 for e in bucket if e["won"])
                        b_l = sum(1 for e in bucket if e["lost"])
                        b_pnl = statistics.mean([e["pnl"] for e in bucket])
                        print(f"      {label}: {len(bucket)} events | WR={b_w}/{len(bucket)} ({b_w/len(bucket)*100:.0f}%) | Rev={b_l} ({b_l/len(bucket)*100:.0f}%) | PnL=${b_pnl:+.4f}")

    print("\n" + "=" * 80)
    print("DONE")
    print("=" * 80)


if __name__ == "__main__":
    main()
