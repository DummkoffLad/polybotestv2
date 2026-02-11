"""
Deep crypto up/down analysis using Polymarket API.

Key findings from investigation:
- Polymarket has 5m, 15m, and 4h crypto up/down markets (NO 1h)
- Slug timestamp = START of resolution window
- Data with interval=1d, fidelity=1 gives minute-by-minute prices
- Price action happens in the resolution window; before that it's ~$0.50
"""
import sys
import requests
import json
import time
import statistics
import re
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

CLOB_URL = "https://clob.polymarket.com"
CACHE_DIR = Path("data")
HD_HISTORIES = CACHE_DIR / "crypto_hd_histories.json"  # High-definition


def load_all_crypto_markets():
    """Load crypto markets from full cache."""
    full_cache = CACHE_DIR / "api_market_cache.json"
    if not full_cache.exists():
        print("No api_market_cache.json! Run fetch_and_analyze_api.py first.")
        return []

    with open(full_cache, encoding="utf-8") as f:
        all_markets = json.load(f)

    # Filter to crypto up/down markets
    crypto = []
    for m in all_markets:
        slug = m.get('slug', '')
        if any(k in slug for k in ['btc-updown', 'eth-updown', 'sol-updown',
                                     'bitcoin-up-or-down', 'ethereum-up-or-down',
                                     'solana-up-or-down']):
            # Determine timeframe
            if 'updown-5m' in slug:
                m['timeframe'] = '5m'
                m['window_sec'] = 300
            elif 'updown-15m' in slug:
                m['timeframe'] = '15m'
                m['window_sec'] = 900
            elif 'updown-4h' in slug:
                m['timeframe'] = '4h'
                m['window_sec'] = 14400
            elif 'up-or-down' in slug:
                m['timeframe'] = 'legacy'
                m['window_sec'] = 3600  # assume 1h
            else:
                continue

            # Extract slug timestamp for updown markets
            ts_match = re.search(r'updown-\d+[mh]-(\d+)', slug)
            if ts_match:
                m['window_start'] = int(ts_match.group(1))
            else:
                m['window_start'] = None

            crypto.append(m)

    return crypto


def fetch_hd_histories(markets):
    """Fetch high-definition price histories (interval=1d, fidelity=1)."""
    if HD_HISTORIES.exists():
        with open(HD_HISTORIES, encoding="utf-8") as f:
            cached = json.load(f)
        print(f"Loaded {len(cached)} HD histories from cache")
        return cached

    # Focus on 15m markets first (best balance of volume and frequency)
    targets = [m for m in markets if m['timeframe'] == '15m']
    print(f"Fetching HD histories for {len(targets)} 15m markets...")

    histories = []
    errors = 0
    empty = 0

    for i, m in enumerate(targets):
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
            try:
                r = requests.get(f"{CLOB_URL}/prices-history", params={
                    'market': token_id,
                    'interval': '1d',  # Last 24h -- better granularity
                    'fidelity': 1,     # 1-minute intervals
                }, timeout=10)
                if r.status_code == 200:
                    h = r.json().get('history', [])
                    if h and (best_history is None or len(h) > len(best_history)):
                        best_history = h
                        best_side = side_idx
            except Exception:
                errors += 1
            time.sleep(0.05)

        if best_history and len(best_history) >= 5:
            histories.append({
                'slug': m.get('slug', ''),
                'question': m.get('question', ''),
                'timeframe': m['timeframe'],
                'window_sec': m['window_sec'],
                'window_start': m.get('window_start'),
                'side': best_side,
                'history': best_history,
                'n_points': len(best_history),
            })
        else:
            empty += 1

        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(targets)} | got {len(histories)} | {empty} empty | {errors} err")

    print(f"Done: {len(histories)} histories, {empty} empty, {errors} errors")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(HD_HISTORIES, 'w', encoding='utf-8') as f:
        json.dump(histories, f)
    return histories


def extract_active_window(h):
    """Extract the active resolution window from a history entry.

    Returns dict with prices, times, etc. or None.
    """
    pts = h['history']
    window_start = h.get('window_start')
    window_sec = h.get('window_sec', 900)

    if not window_start or len(pts) < 5:
        return None

    window_end = window_start + window_sec

    # Filter to points within the active window
    active_p = []
    active_t = []
    for pt in pts:
        if window_start <= pt['t'] <= window_end + 60:  # +60s grace
            active_p.append(pt['p'])
            active_t.append(pt['t'])

    if len(active_p) < 3:
        return None

    final = active_p[-1]
    resolved_up = final >= 0.80
    resolved_down = final <= 0.20
    if not (resolved_up or resolved_down):
        return None  # Didn't resolve cleanly

    return {
        'prices': active_p,
        'times': active_t,
        'window_min': window_sec // 60,
        'final': final,
        'resolved_up': resolved_up,
        'slug': h['slug'],
        'first_price': active_p[0],
        'min_price': min(active_p),
        'max_price': max(active_p),
        'n_data_points': len(active_p),
    }


def analyze(histories):
    """Run analysis on active windows."""
    windows = []
    for h in histories:
        w = extract_active_window(h)
        if w:
            windows.append(w)

    print(f"\nValid active windows: {len(windows)} / {len(histories)}")
    if not windows:
        print("No valid windows! Check data.")
        return

    up = sum(1 for w in windows if w['resolved_up'])
    print(f"  Resolved UP: {up}/{len(windows)} ({up/len(windows)*100:.0f}%)")
    print(f"  Resolved DOWN: {len(windows)-up}/{len(windows)} ({(len(windows)-up)/len(windows)*100:.0f}%)")
    print(f"  Data points per window: median={statistics.median([w['n_data_points'] for w in windows]):.0f}")

    # Coin breakdown
    coins = defaultdict(int)
    for w in windows:
        if 'btc' in w['slug']:
            coins['BTC'] += 1
        elif 'eth' in w['slug']:
            coins['ETH'] += 1
        elif 'sol' in w['slug']:
            coins['SOL'] += 1
    for c, n in sorted(coins.items()):
        print(f"  {c}: {n}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"1. BASIC STRUCTURE: What does a typical market look like?")
    print(f"{'='*80}")

    min_prices = [w['min_price'] for w in windows]
    max_prices = [w['max_price'] for w in windows]
    first_prices = [w['first_price'] for w in windows]

    print(f"\n  First price (start of window):")
    print(f"    Mean: ${statistics.mean(first_prices):.3f}")
    print(f"    Range: ${min(first_prices):.3f} - ${max(first_prices):.3f}")
    below_50 = sum(1 for p in first_prices if p < 0.48)
    above_50 = sum(1 for p in first_prices if p > 0.52)
    near_50 = len(first_prices) - below_50 - above_50
    print(f"    Near $0.50: {near_50}/{len(first_prices)} ({near_50/len(first_prices)*100:.0f}%)")

    print(f"\n  Minimum price during window:")
    for thresh in [0.10, 0.20, 0.30, 0.40, 0.45, 0.50]:
        n = sum(1 for p in min_prices if p <= thresh)
        print(f"    Ever hits ${thresh:.2f}: {n}/{len(windows)} ({n/len(windows)*100:.0f}%)")

    print(f"\n  Maximum price during window:")
    for thresh in [0.50, 0.55, 0.60, 0.70, 0.80, 0.90, 0.95]:
        n = sum(1 for p in max_prices if p >= thresh)
        print(f"    Ever hits ${thresh:.2f}: {n}/{len(windows)} ({n/len(windows)*100:.0f}%)")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"2. BUY AT EACH PRICE POINT (hold to resolution)")
    print(f"   Skip first 15% of window to avoid stale prices")
    print(f"{'='*80}")

    print(f"\n  {'Price':>7} | {'Events':>7} | {'Win%':>6} | {'Rev%':>6} | {'PnL/trade':>10} | {'EV/$10':>8} | {'Ratio':>8}")
    print(f"  {'-'*7}-+-{'-'*7}-+-{'-'*6}-+-{'-'*6}-+-{'-'*10}-+-{'-'*8}-+-{'-'*8}")

    for buy_price in [x/100 for x in range(50, 96, 5)]:
        events = []
        for w in windows:
            prices = w['prices']
            times = w['times']
            duration = times[-1] - times[0]
            if duration <= 0:
                continue

            skip_t = times[0] + duration * 0.15

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
        rev = n - wins
        avg_pnl = statistics.mean([e['pnl'] for e in events])
        avg_entry = statistics.mean([e['entry'] for e in events])
        ev_10 = avg_pnl * 10 / avg_entry if avg_entry > 0 else 0
        ratio = f"{(avg_entry-0.01)/(0.99-avg_entry):.1f}:1" if avg_entry < 0.99 else "inf"

        marker = " <-- best" if avg_pnl > 0.02 else ""
        print(f"  ${buy_price:.2f}  | {n:>7} | {wr:>5.1f}% | {rev/n*100:>5.1f}% | ${avg_pnl:>+8.4f} | ${ev_10:>+6.2f} | {ratio:>8}{marker}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"3. BUY BY TIME (% through resolution window)")
    print(f"{'='*80}")

    for buy_price in [0.60, 0.70, 0.80, 0.85, 0.90]:
        print(f"\n  --- Buy at ${buy_price:.2f} ---")
        time_bins = [(15, 30), (30, 50), (50, 70), (70, 85), (85, 100)]
        for t_lo, t_hi in time_bins:
            events = []
            for w in windows:
                prices = w['prices']
                times = w['times']
                duration = times[-1] - times[0]
                if duration <= 0:
                    continue

                for p, t in zip(prices, times):
                    pct = (t - times[0]) / duration * 100
                    if t_lo <= pct < t_hi and p >= buy_price:
                        won = w['resolved_up']
                        pnl = (0.99 - p) if won else (0.01 - p)
                        events.append({'entry': p, 'won': won, 'pnl': pnl})
                        break

            if not events or len(events) < 3:
                continue

            n = len(events)
            wins = sum(1 for e in events if e['won'])
            avg_pnl = statistics.mean([e['pnl'] for e in events])
            print(f"    {t_lo:>3}-{t_hi:>3}%: {n:>4} | WR={wins/n*100:>4.0f}% | Rev={n-wins} | PnL=${avg_pnl:>+.4f}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"4. MOMENTUM + PRICE STRATEGY")
    print(f"   Check if price moved X from $0.50 at 20% mark, then buy at Y")
    print(f"{'='*80}")

    for mom_thresh in [0.03, 0.05, 0.08, 0.10, 0.15]:
        print(f"\n  --- Momentum >= {mom_thresh*100:.0f}c ---")
        for buy_price in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
            events = []
            for w in windows:
                prices = w['prices']
                times = w['times']
                if len(prices) < 5:
                    continue
                duration = times[-1] - times[0]
                if duration <= 0:
                    continue

                check_t = times[0] + duration * 0.20
                first_p = prices[0]

                # Find price at 20% mark
                p_at_check = None
                for p, t in zip(prices, times):
                    if t >= check_t:
                        p_at_check = p
                        break
                if p_at_check is None:
                    continue

                move = p_at_check - first_p
                if move < mom_thresh:
                    continue

                # Buy at buy_price after check time
                for p, t in zip(prices, times):
                    if t >= check_t and p >= buy_price:
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
            marker = " <-- EDGE" if avg_pnl > 0.02 and wins/n > 0.75 else ""
            print(f"    buy ${buy_price:.2f}: {n:>4} | WR={wins/n*100:>4.0f}% | Rev={n-wins} | PnL=${avg_pnl:>+.4f} | Tot=${total:>+.2f}{marker}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"5. REVERSAL ANATOMY: When $0.80+ fails")
    print(f"{'='*80}")

    reversals = []
    for w in windows:
        prices = w['prices']
        times = w['times']
        duration = times[-1] - times[0]
        if duration <= 0:
            continue
        skip_t = times[0] + duration * 0.15

        for idx, (p, t) in enumerate(zip(prices, times)):
            if t >= skip_t and p >= 0.80:
                if not w['resolved_up']:
                    peak = max(prices[idx:])
                    # Price at 90% through
                    t_90 = times[0] + duration * 0.90
                    p_near_end = None
                    for p2, t2 in zip(prices, times):
                        if t2 >= t_90:
                            p_near_end = p2
                            break
                    reversals.append({
                        'slug': w['slug'],
                        'entry': p,
                        'peak': peak,
                        'final': w['final'],
                        'entry_pct': (t - times[0]) / duration * 100,
                        'p_at_90': p_near_end,
                    })
                break

    print(f"\n  Total: {len(reversals)} markets hit $0.80+ then resolved DOWN")
    total_80_events = sum(1 for w in windows
                          for idx, (p, t) in enumerate(zip(w['prices'], w['times']))
                          if t >= w['times'][0] + (w['times'][-1]-w['times'][0])*0.15 and p >= 0.80)
    # Actually count properly
    hit_80 = 0
    for w in windows:
        prices = w['prices']
        times = w['times']
        duration = times[-1] - times[0]
        if duration <= 0:
            continue
        skip_t = times[0] + duration * 0.15
        for p, t in zip(prices, times):
            if t >= skip_t and p >= 0.80:
                hit_80 += 1
                break
    print(f"  Out of {hit_80} markets that hit $0.80+ = {len(reversals)/hit_80*100:.1f}% reversal rate" if hit_80 > 0 else "")

    for r in reversals[:15]:
        p90 = f"${r['p_at_90']:.2f}" if r['p_at_90'] is not None else "N/A"
        print(f"    {r['slug'][:50]:50s} | entry=${r['entry']:.2f} at {r['entry_pct']:>3.0f}% | peak=${r['peak']:.2f} | at 90%={p90} | final=${r['final']:.2f}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"6. BOTH-SIDES STRATEGY")
    print(f"   If price dips below X, buy. The OTHER token is above (1-X).")
    print(f"   One wins, one loses. Net = $1.00 - 2*entry")
    print(f"{'='*80}")

    for buy_price in [0.35, 0.38, 0.40, 0.42, 0.44, 0.45, 0.46, 0.48, 0.50]:
        hit = 0
        for w in windows:
            if w['min_price'] <= buy_price:
                hit += 1
        net = 1.00 - 2 * buy_price
        pct = hit / len(windows) * 100
        print(f"  ${buy_price:.2f}: {hit:>4}/{len(windows)} ({pct:>4.0f}%) hit low | Net=${net:+.2f} | EV if hit=${net:+.2f}")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"7. BACKTEST: $10/trade")
    print(f"   Buy first time price >= $X after 15% of window")
    print(f"{'='*80}")

    for buy_price in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
        trades = []
        for w in windows:
            prices = w['prices']
            times = w['times']
            duration = times[-1] - times[0]
            if duration <= 0:
                continue
            skip_t = times[0] + duration * 0.15

            for p, t in zip(prices, times):
                if t >= skip_t and p >= buy_price:
                    won = w['resolved_up']
                    pnl_share = (0.99 - p) if won else (0.01 - p)
                    shares = 10.0 / p
                    trades.append({'won': won, 'dollar_pnl': pnl_share * shares, 'entry': p})
                    break

        if not trades or len(trades) < 5:
            continue

        n = len(trades)
        wins = sum(1 for t in trades if t['won'])
        total = sum(t['dollar_pnl'] for t in trades)
        avg = total / n
        capital = n * 10

        print(f"\n  ${buy_price:.2f}: {n} trades | WR={wins/n*100:.1f}% | Total=${total:>+.2f} | Avg=${avg:>+.2f} | ROI={total/capital*100:>+.1f}%")

    # =========================================================================
    print(f"\n{'='*80}")
    print(f"8. VOLATILITY: Price swing within window")
    print(f"{'='*80}")

    ranges = [w['max_price'] - w['min_price'] for w in windows]
    print(f"\n  Price range (max - min) during window:")
    print(f"    Mean: ${statistics.mean(ranges):.3f}")
    print(f"    Median: ${statistics.median(ranges):.3f}")
    print(f"    Min: ${min(ranges):.3f}")
    print(f"    Max: ${max(ranges):.3f}")

    for thresh in [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]:
        n = sum(1 for r in ranges if r >= thresh)
        print(f"    Range >= ${thresh:.2f}: {n}/{len(ranges)} ({n/len(ranges)*100:.0f}%)")


if __name__ == "__main__":
    markets = load_all_crypto_markets()
    print(f"Total crypto markets: {len(markets)}")

    # Breakdown
    from collections import Counter
    tf_counts = Counter(m['timeframe'] for m in markets)
    for tf, c in tf_counts.most_common():
        print(f"  {tf}: {c}")

    histories = fetch_hd_histories(markets)
    analyze(histories)
