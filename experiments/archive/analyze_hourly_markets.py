"""
Analyze 1-hour crypto up/down markets from Polymarket API.

These are the "X Up or Down - DATE, TIME ET" markets.
The active window is the specific hour mentioned in the question.
"""
import sys
import requests
import json
import time
import re
import statistics
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

CLOB_URL = "https://clob.polymarket.com"
GAMMA_URL = "https://gamma-api.polymarket.com"
CACHE_DIR = Path("data")
HOURLY_CACHE = CACHE_DIR / "hourly_markets_cache.json"
HOURLY_HD = CACHE_DIR / "hourly_hd_histories.json"

ET_OFFSET = timedelta(hours=-5)  # ET = UTC-5


def parse_hour_from_question(question):
    """Parse the resolution hour from question text.

    Examples:
    - "Bitcoin Up or Down - February 8, 10AM ET" -> Feb 8 10:00-11:00 ET
    - "Ethereum Up or Down - February 9, 1PM ET" -> Feb 9 13:00-14:00 ET
    - "Bitcoin Up or Down on February 9?" -> DAILY, skip
    """
    # Match patterns like "February 8, 10AM ET" or "February 9, 1PM ET"
    m = re.search(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+'
        r'(\d{1,2}),?\s+'
        r'(\d{1,2})\s*(AM|PM)\s*ET',
        question, re.IGNORECASE
    )
    if not m:
        return None

    month_str = m.group(1)
    day = int(m.group(2))
    hour = int(m.group(3))
    ampm = m.group(4).upper()

    months = {
        'January': 1, 'February': 2, 'March': 3, 'April': 4,
        'May': 5, 'June': 6, 'July': 7, 'August': 8,
        'September': 9, 'October': 10, 'November': 11, 'December': 12
    }
    month = months.get(month_str)
    if not month:
        return None

    # Convert 12h to 24h
    if ampm == 'AM' and hour == 12:
        hour = 0
    elif ampm == 'PM' and hour != 12:
        hour += 12

    # Build UTC datetime directly (ET = UTC-5 in winter)
    try:
        # Convert ET hour to UTC: add 5 hours
        utc_hour = hour + 5
        utc_day = day
        utc_month = month
        if utc_hour >= 24:
            utc_hour -= 24
            utc_day += 1
            # Handle month overflow (simplified, February only needs 28/29)
            import calendar
            max_day = calendar.monthrange(2026, utc_month)[1]
            if utc_day > max_day:
                utc_day = 1
                utc_month += 1

        utc_time = datetime(2026, utc_month, utc_day, utc_hour, 0, 0,
                            tzinfo=timezone.utc)
        start_ts = int(utc_time.timestamp())
        end_ts = start_ts + 3600
        return {
            'start_utc': start_ts,
            'end_utc': end_ts,
            'label': f"{month_str} {day}, {m.group(3)}{ampm} ET",
        }
    except Exception:
        return None


def fetch_hourly_markets():
    """Find all hourly crypto up/down markets."""
    if HOURLY_CACHE.exists():
        with open(HOURLY_CACHE, encoding="utf-8") as f:
            markets = json.load(f)
        print(f"Loaded {len(markets)} hourly markets from cache")
        return markets

    full_cache = CACHE_DIR / "api_market_cache.json"
    if not full_cache.exists():
        print("No api_market_cache.json!")
        return []

    with open(full_cache, encoding="utf-8") as f:
        all_markets = json.load(f)

    hourly = []
    skipped_daily = 0
    skipped_noparse = 0

    for m in all_markets:
        slug = m.get('slug', '')
        q = m.get('question', '')

        # Only legacy "up-or-down" markets
        if 'up-or-down' not in slug:
            continue

        hour_info = parse_hour_from_question(q)
        if hour_info is None:
            # Might be daily like "Bitcoin Up or Down on February 9?"
            if 'on ' in q.lower() and ('AM' not in q and 'PM' not in q):
                skipped_daily += 1
            else:
                skipped_noparse += 1
            continue

        m['hour_info'] = hour_info
        # Determine coin
        ql = q.lower()
        if 'bitcoin' in ql or 'btc' in ql:
            m['coin'] = 'BTC'
        elif 'ethereum' in ql or 'eth' in ql:
            m['coin'] = 'ETH'
        elif 'solana' in ql or 'sol' in ql:
            m['coin'] = 'SOL'
        else:
            m['coin'] = 'OTHER'

        hourly.append(m)

    print(f"Found {len(hourly)} hourly markets (skipped {skipped_daily} daily, {skipped_noparse} unparseable)")

    # Breakdown
    from collections import Counter
    coins = Counter(m['coin'] for m in hourly)
    for c, n in coins.most_common():
        print(f"  {c}: {n}")

    HOURLY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(HOURLY_CACHE, 'w', encoding='utf-8') as f:
        json.dump(hourly, f, default=str)
    return hourly


def fetch_hd_histories(markets):
    """Fetch high-def price histories for hourly markets."""
    if HOURLY_HD.exists():
        with open(HOURLY_HD, encoding="utf-8") as f:
            cached = json.load(f)
        print(f"Loaded {len(cached)} HD hourly histories from cache")
        return cached

    print(f"Fetching HD histories for {len(markets)} hourly markets...")
    histories = []
    errors = 0
    empty = 0

    for i, m in enumerate(markets):
        tokens_str = m.get('clobTokenIds', '[]')
        try:
            tokens = json.loads(tokens_str)
        except Exception:
            continue
        if len(tokens) < 2:
            continue

        best_history = None
        best_side = None

        for side_idx, token_id in enumerate(tokens[:2]):
            # Try 1d first (better resolution), fall back to max
            for interval in ['1d', 'max']:
                try:
                    r = requests.get(f"{CLOB_URL}/prices-history", params={
                        'market': token_id,
                        'interval': interval,
                        'fidelity': 1,
                    }, timeout=10)
                    if r.status_code == 200:
                        h = r.json().get('history', [])
                        if h and len(h) >= 5:
                            if best_history is None or len(h) > len(best_history):
                                best_history = h
                                best_side = side_idx
                            break  # Got good data, no need for fallback
                except Exception:
                    errors += 1
                time.sleep(0.03)

        if best_history and len(best_history) >= 5:
            histories.append({
                'slug': m.get('slug', ''),
                'question': m.get('question', ''),
                'coin': m.get('coin', ''),
                'hour_info': m.get('hour_info', {}),
                'side': best_side,
                'history': best_history,
                'n_points': len(best_history),
            })
        else:
            empty += 1

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(markets)} | got {len(histories)} | {empty} empty | {errors} err")

    print(f"Done: {len(histories)} histories, {empty} empty, {errors} errors")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(HOURLY_HD, 'w', encoding='utf-8') as f:
        json.dump(histories, f)
    return histories


def extract_active_hour(h):
    """Extract the active 1-hour window from a history entry."""
    hour_info = h.get('hour_info', {})
    if not hour_info:
        return None

    start_utc = hour_info['start_utc']
    end_utc = hour_info['end_utc']
    pts = h['history']

    if len(pts) < 5:
        return None

    # Filter to points within the active hour
    active_p = []
    active_t = []
    for pt in pts:
        if start_utc - 120 <= pt['t'] <= end_utc + 120:  # 2min grace
            active_p.append(pt['p'])
            active_t.append(pt['t'])

    if len(active_p) < 3:
        return None

    final = active_p[-1]
    resolved_up = final >= 0.80
    resolved_down = final <= 0.20
    if not (resolved_up or resolved_down):
        # Check if it's close enough
        if final >= 0.70:
            resolved_up = True
        elif final <= 0.30:
            resolved_down = True
        else:
            return None  # Ambiguous

    return {
        'prices': active_p,
        'times': active_t,
        'final': final,
        'resolved_up': resolved_up,
        'slug': h['slug'],
        'coin': h.get('coin', ''),
        'question': h.get('question', ''),
        'first_price': active_p[0],
        'min_price': min(active_p),
        'max_price': max(active_p),
        'n_data_points': len(active_p),
        'duration_min': (active_t[-1] - active_t[0]) / 60 if len(active_t) > 1 else 0,
        'hour_label': hour_info.get('label', ''),
    }


def analyze(histories):
    """Full analysis on hourly markets."""
    windows = []
    no_data = 0
    for h in histories:
        w = extract_active_hour(h)
        if w:
            windows.append(w)
        else:
            no_data += 1

    print(f"\nValid 1-hour windows: {len(windows)} / {len(histories)} (no data: {no_data})")
    if not windows:
        print("No valid windows!")
        return

    up = sum(1 for w in windows if w['resolved_up'])
    print(f"  Resolved UP: {up}/{len(windows)} ({up/len(windows)*100:.0f}%)")
    print(f"  Resolved DOWN: {len(windows)-up}/{len(windows)}")
    print(f"  Data points per window: min={min(w['n_data_points'] for w in windows)}, "
          f"median={statistics.median([w['n_data_points'] for w in windows]):.0f}, "
          f"max={max(w['n_data_points'] for w in windows)}")
    print(f"  Duration: min={min(w['duration_min'] for w in windows):.0f}min, "
          f"median={statistics.median([w['duration_min'] for w in windows]):.0f}min, "
          f"max={max(w['duration_min'] for w in windows):.0f}min")

    # Coin breakdown
    coins = defaultdict(int)
    for w in windows:
        coins[w['coin']] += 1
    for c, n in sorted(coins.items()):
        print(f"  {c}: {n}")

    # Show a few examples
    print("\n  Sample windows:")
    for w in windows[:5]:
        print(f"    {w['question'][:55]:55s} | pts={w['n_data_points']:>3} | dur={w['duration_min']:>4.0f}min | final=${w['final']:.2f}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"1. BASIC STRUCTURE")
    print(f"{'='*80}")

    min_prices = [w['min_price'] for w in windows]
    max_prices = [w['max_price'] for w in windows]

    print(f"\n  Minimum price during the hour:")
    for thresh in [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.45, 0.50]:
        n = sum(1 for p in min_prices if p <= thresh)
        print(f"    Hits ${thresh:.2f}: {n}/{len(windows)} ({n/len(windows)*100:.0f}%)")

    print(f"\n  Maximum price during the hour:")
    for thresh in [0.50, 0.55, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]:
        n = sum(1 for p in max_prices if p >= thresh)
        print(f"    Hits ${thresh:.2f}: {n}/{len(windows)} ({n/len(windows)*100:.0f}%)")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"2. BUY AT EACH PRICE POINT (skip first 10 min, hold to resolution)")
    print(f"{'='*80}")

    print(f"\n  {'Price':>7} | {'N':>5} | {'Win%':>6} | {'Rev%':>6} | {'PnL/trade':>10} | {'EV/$10':>8} | {'Ratio':>8}")
    print(f"  {'-'*7}-+-{'-'*5}-+-{'-'*6}-+-{'-'*6}-+-{'-'*10}-+-{'-'*8}-+-{'-'*8}")

    for buy_price in [x/100 for x in range(50, 96, 5)]:
        events = []
        for w in windows:
            prices = w['prices']
            times = w['times']
            duration = times[-1] - times[0]
            if duration <= 0:
                continue
            skip_t = times[0] + 600  # skip first 10 min

            for p, t in zip(prices, times):
                if t < skip_t:
                    continue
                if p >= buy_price:
                    won = w['resolved_up']
                    pnl = (0.99 - p) if won else (0.01 - p)
                    events.append({'entry': p, 'won': won, 'pnl': pnl})
                    break

        if not events:
            continue

        n = len(events)
        wins = sum(1 for e in events if e['won'])
        wr = wins / n * 100
        avg_pnl = statistics.mean([e['pnl'] for e in events])
        avg_entry = statistics.mean([e['entry'] for e in events])
        ev_10 = avg_pnl * 10 / avg_entry if avg_entry > 0 else 0
        ratio = f"{(avg_entry-0.01)/(0.99-avg_entry):.1f}:1" if avg_entry < 0.99 else "inf"

        marker = " <--" if avg_pnl > 0.01 else ""
        print(f"  ${buy_price:.2f}  | {n:>5} | {wr:>5.1f}% | {(n-wins)/n*100:>5.1f}% | ${avg_pnl:>+8.4f} | ${ev_10:>+6.2f} | {ratio:>8}{marker}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"3. BUY BY TIME WINDOW (minutes into the hour)")
    print(f"{'='*80}")

    for buy_price in [0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
        print(f"\n  --- Buy at ${buy_price:.2f} ---")
        time_bins = [(10, 20), (20, 30), (30, 40), (40, 50), (50, 60)]
        for t_lo, t_hi in time_bins:
            events = []
            for w in windows:
                prices = w['prices']
                times = w['times']
                start = times[0]
                for p, t in zip(prices, times):
                    mins = (t - start) / 60
                    if t_lo <= mins < t_hi and p >= buy_price:
                        won = w['resolved_up']
                        pnl = (0.99 - p) if won else (0.01 - p)
                        events.append({'entry': p, 'won': won, 'pnl': pnl})
                        break

            if not events or len(events) < 3:
                continue

            n = len(events)
            wins = sum(1 for e in events if e['won'])
            avg_pnl = statistics.mean([e['pnl'] for e in events])
            marker = " <--" if avg_pnl > 0.01 else ""
            print(f"    min {t_lo:>2}-{t_hi:>2}: {n:>4} | WR={wins/n*100:>4.0f}% | Rev={n-wins} | PnL=${avg_pnl:>+.4f}{marker}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"4. MOMENTUM + PRICE STRATEGY")
    print(f"   After 10 min, if price moved X cents from open, buy when hits Y")
    print(f"{'='*80}")

    for mom_thresh in [0.03, 0.05, 0.08, 0.10, 0.15]:
        print(f"\n  --- Momentum >= {mom_thresh*100:.0f}c in first 10 min ---")
        for buy_price in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
            events = []
            for w in windows:
                prices = w['prices']
                times = w['times']
                if len(prices) < 5:
                    continue
                start = times[0]
                first_p = prices[0]

                # Price at ~10 min
                t_10 = start + 600
                p_at_10 = None
                for p, t in zip(prices, times):
                    if t >= t_10:
                        p_at_10 = p
                        break
                if p_at_10 is None:
                    continue

                move = p_at_10 - first_p
                if move < mom_thresh:
                    continue

                for p, t in zip(prices, times):
                    if t >= t_10 and p >= buy_price:
                        won = w['resolved_up']
                        pnl = (0.99 - p) if won else (0.01 - p)
                        events.append({'entry': p, 'won': won, 'pnl': pnl})
                        break

            if not events or len(events) < 3:
                continue

            n = len(events)
            wins = sum(1 for e in events if e['won'])
            avg_pnl = statistics.mean([e['pnl'] for e in events])
            total = sum(e['pnl'] for e in events)
            marker = " <-- EDGE" if avg_pnl > 0.01 and wins/n > 0.75 else ""
            print(f"    buy ${buy_price:.2f}: {n:>4} | WR={wins/n*100:>4.0f}% | Rev={n-wins} | PnL=${avg_pnl:>+.4f} | Tot=${total:>+.2f}{marker}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"5. REVERSAL ANATOMY: When $0.80+ fails")
    print(f"{'='*80}")

    reversals = []
    hit_80_count = 0
    for w in windows:
        prices = w['prices']
        times = w['times']
        start = times[0]
        skip_t = start + 600

        for idx, (p, t) in enumerate(zip(prices, times)):
            if t >= skip_t and p >= 0.80:
                hit_80_count += 1
                if not w['resolved_up']:
                    peak = max(prices[idx:])
                    # Price at min 50
                    t_50 = start + 3000
                    p_at_50 = None
                    for p2, t2 in zip(prices, times):
                        if t2 >= t_50:
                            p_at_50 = p2
                            break
                    reversals.append({
                        'slug': w['slug'],
                        'coin': w['coin'],
                        'entry': p,
                        'peak': peak,
                        'final': w['final'],
                        'entry_min': (t - start) / 60,
                        'p_at_50min': p_at_50,
                    })
                break

    print(f"\n  Markets that hit $0.80+ (after min 10): {hit_80_count}")
    print(f"  Of those, resolved DOWN: {len(reversals)} ({len(reversals)/hit_80_count*100:.1f}%)" if hit_80_count else "")

    for r in reversals[:20]:
        p50 = f"${r['p_at_50min']:.2f}" if r['p_at_50min'] is not None else "N/A"
        print(f"    {r['slug'][:50]:50s} | {r['coin']} | entry=${r['entry']:.2f} min {r['entry_min']:>4.0f} | peak=${r['peak']:.2f} | @min50={p50} | final=${r['final']:.2f}")

    if len(reversals) >= 2:
        peaks = [r['peak'] for r in reversals]
        p50s = [r['p_at_50min'] for r in reversals if r['p_at_50min'] is not None]
        print(f"\n  Reversal patterns:")
        print(f"    Avg peak before collapse: ${statistics.mean(peaks):.3f}")
        if p50s:
            print(f"    Avg price at min 50: ${statistics.mean(p50s):.3f}")
            below_50 = sum(1 for p in p50s if p < 0.50)
            print(f"    Below $0.50 at min 50: {below_50}/{len(p50s)}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"6. BOTH-SIDES: Does price ever dip low enough to buy cheap?")
    print(f"{'='*80}")

    for thresh in [0.35, 0.40, 0.42, 0.44, 0.45, 0.46, 0.48, 0.50]:
        hit = sum(1 for w in windows if w['min_price'] <= thresh)
        net = 1.00 - 2 * thresh
        print(f"  ${thresh:.2f}: {hit}/{len(windows)} ({hit/len(windows)*100:.0f}%) dip to it | If both sides bought: net ${net:+.2f}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"7. VOLATILITY")
    print(f"{'='*80}")

    ranges = [w['max_price'] - w['min_price'] for w in windows]
    print(f"\n  Price range during hour: mean=${statistics.mean(ranges):.3f}, median=${statistics.median(ranges):.3f}")
    for thresh in [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]:
        n = sum(1 for r in ranges if r >= thresh)
        print(f"    Range >= ${thresh:.2f}: {n}/{len(ranges)} ({n/len(ranges)*100:.0f}%)")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"8. BACKTEST: $10/trade")
    print(f"{'='*80}")

    for buy_price in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
        trades = []
        for w in windows:
            prices = w['prices']
            times = w['times']
            skip_t = times[0] + 600

            for p, t in zip(prices, times):
                if t >= skip_t and p >= buy_price:
                    won = w['resolved_up']
                    pnl_share = (0.99 - p) if won else (0.01 - p)
                    shares = 10.0 / p
                    trades.append({'won': won, 'dollar_pnl': pnl_share * shares})
                    break

        if not trades or len(trades) < 3:
            continue

        n = len(trades)
        wins = sum(1 for t in trades if t['won'])
        total = sum(t['dollar_pnl'] for t in trades)
        print(f"  ${buy_price:.2f}: {n} trades | WR={wins/n*100:.1f}% | Total=${total:>+.2f} | Avg=${total/n:>+.2f} | ROI={total/(n*10)*100:>+.1f}%")


if __name__ == "__main__":
    markets = fetch_hourly_markets()
    if not markets:
        print("No hourly markets found!")
        sys.exit(1)

    histories = fetch_hd_histories(markets)
    analyze(histories)
