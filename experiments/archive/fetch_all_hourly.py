"""
Fetch ALL historical crypto up/down markets and their price histories.
Uses API-provided eventStartTime/endDate for correct timestamps.
Handles both 5-min and 1-hour formats.

Data availability: ~2 weeks of price history via interval=max.
For recent markets (<1 day): interval=6h gives 60+ pts/hour.
For older markets (1-14 days): interval=max gives ~6 pts/hour.
"""
import sys
import requests
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

sys.stdout.reconfigure(line_buffering=True)

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
CACHE_DIR = Path("data")
MARKETS_CACHE = CACHE_DIR / "all_crypto_updown_markets.json"
HISTORIES_CACHE = CACHE_DIR / "all_crypto_updown_histories.json"

COINS = ['bitcoin', 'btc', 'ethereum', 'eth', 'solana', 'sol',
         'dogecoin', 'doge', 'xrp']


def is_crypto_updown(market):
    """Check if a market is a crypto up/down market."""
    slug = market.get('slug', '').lower()
    question = market.get('question', '').lower()
    if 'up-or-down' not in slug and 'up or down' not in question:
        return False
    if not any(c in slug or c in question for c in COINS):
        return False
    return True


def get_market_timestamps(market):
    """Extract resolution window from API fields. Returns (start_utc, end_utc) or None."""
    est = market.get('eventStartTime', '')
    edt = market.get('endDate', '')
    if not est or not edt:
        return None
    try:
        start = int(datetime.fromisoformat(est.replace('Z', '+00:00')).timestamp())
        end = int(datetime.fromisoformat(edt.replace('Z', '+00:00')).timestamp())
        if end > start > 0:
            return (start, end)
    except Exception:
        pass
    return None


def classify_format(market):
    """Classify market as 5min, 15min, 1hour, or other based on duration."""
    ts = get_market_timestamps(market)
    if not ts:
        return None, None, None
    start, end = ts
    duration = end - start
    if 240 <= duration <= 360:  # 4-6 min
        return start, end, '5min'
    elif 840 <= duration <= 960:  # 14-16 min
        return start, end, '15min'
    elif 3400 <= duration <= 3700:  # ~1 hour
        return start, end, '1hour'
    elif 14000 <= duration <= 14800:  # ~4 hours
        return start, end, '4hour'
    else:
        return start, end, f'other_{duration}s'


def get_coin(question):
    """Extract coin name from question."""
    q = question.lower()
    if 'bitcoin' in q or 'btc' in q: return 'BTC'
    if 'ethereum' in q or 'eth' in q: return 'ETH'
    if 'solana' in q or 'sol' in q: return 'SOL'
    if 'dogecoin' in q or 'doge' in q: return 'DOGE'
    if 'xrp' in q: return 'XRP'
    return 'OTHER'


def scan_markets():
    """Scan Gamma API for all crypto up/down markets."""
    if MARKETS_CACHE.exists():
        with open(MARKETS_CACHE, encoding='utf-8') as f:
            markets = json.load(f)
        if len(markets) > 0:
            fmt_counts = Counter(m.get('format') for m in markets)
            print(f"Loaded {len(markets)} markets from cache: {dict(fmt_counts)}")
            return markets

    found = []
    seen = set()
    fmt_counts = Counter()
    print("Scanning Gamma API for crypto up/down markets...")

    for offset in range(0, 100000, 100):
        try:
            r = requests.get(f"{GAMMA_URL}/markets", params={
                'limit': 100, 'offset': offset,
                'closed': True, 'order': 'volume24hr', 'ascending': True,
            }, timeout=15)
        except Exception as e:
            print(f"  Request error at offset {offset}: {e}")
            time.sleep(2)
            continue

        if r.status_code != 200:
            print(f"  HTTP {r.status_code} at offset {offset}, stopping")
            break
        batch = r.json()
        if not batch:
            print(f"  Empty batch at offset {offset}, stopping")
            break

        for m in batch:
            mid = m.get('id', m.get('slug', ''))
            if mid in seen:
                continue
            seen.add(mid)

            if not is_crypto_updown(m):
                continue

            start, end, fmt = classify_format(m)
            if start and fmt:
                m['resolution_start_utc'] = start
                m['resolution_end_utc'] = end
                m['duration_sec'] = end - start
                m['format'] = fmt
                m['coin'] = get_coin(m.get('question', ''))
                found.append(m)
                fmt_counts[fmt] += 1

        if (offset // 100 + 1) % 100 == 0:
            print(f"  offset={offset} scanned={offset+len(batch)} found={len(found)} ({dict(fmt_counts)})")

        time.sleep(0.02)

    print(f"\nTotal found: {len(found)} markets")
    for fmt, n in fmt_counts.most_common():
        print(f"  {fmt}: {n}")

    # Coin breakdown
    coins = Counter(m.get('coin', '?') for m in found)
    print("By coin:")
    for c, n in coins.most_common():
        print(f"  {c}: {n}")

    # Date range
    if found:
        dates = [m['resolution_start_utc'] for m in found]
        oldest = datetime.fromtimestamp(min(dates), tz=timezone.utc)
        newest = datetime.fromtimestamp(max(dates), tz=timezone.utc)
        print(f"Date range: {oldest.date()} to {newest.date()}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(MARKETS_CACHE, 'w', encoding='utf-8') as f:
        json.dump(found, f)
    return found


def fetch_histories(markets, max_fetch=3000):
    """Fetch price histories for markets. Tries multiple intervals for best fidelity."""
    if HISTORIES_CACHE.exists():
        with open(HISTORIES_CACHE, encoding='utf-8') as f:
            cached = json.load(f)
        if len(cached) > 0:
            print(f"Loaded {len(cached)} histories from cache")
            return cached

    # Sort by resolution time (newest first — these have best data)
    markets_sorted = sorted(markets,
                            key=lambda m: m.get('resolution_start_utc', 0),
                            reverse=True)
    targets = markets_sorted[:max_fetch]
    print(f"Fetching histories for {len(targets)} most recent markets...")

    histories = []
    empty = 0
    errors = 0
    now_ts = int(datetime.now(timezone.utc).timestamp())

    for i, m in enumerate(targets):
        tokens_str = m.get('clobTokenIds', '[]')
        try:
            tokens = json.loads(tokens_str)
        except Exception:
            continue
        if len(tokens) < 2:
            continue

        start_utc = m.get('resolution_start_utc', 0)
        end_utc = m.get('resolution_end_utc', 0)
        age_hours = (now_ts - end_utc) / 3600

        # Choose intervals based on market age
        # Recent (<6h): 6h gives best resolution
        # Medium (<24h): 1d gives good resolution
        # Older: max is the only option
        if age_hours < 6:
            intervals = ['6h', '1d', 'max']
        elif age_hours < 24:
            intervals = ['1d', 'max']
        else:
            intervals = ['max']

        best = None
        best_side = None

        for side_idx, tid in enumerate(tokens[:2]):
            for interval in intervals:
                try:
                    r = requests.get(f"{CLOB_URL}/prices-history", params={
                        'market': tid, 'interval': interval, 'fidelity': 1,
                    }, timeout=10)
                    if r.status_code == 200:
                        h = r.json().get('history', [])
                        if h and (best is None or len(h) > len(best)):
                            best = h
                            best_side = side_idx
                            if len(h) > 50:  # good enough data
                                break
                except Exception:
                    errors += 1
                time.sleep(0.02)
            if best and len(best) > 50:
                break  # don't need to try other side

        if best and len(best) >= 3:
            # Extract points in different windows
            active = [p for p in best
                      if start_utc - 60 <= p['t'] <= end_utc + 60]
            # Also get "pre-resolution" data (1h before active window)
            pre_hour = [p for p in best
                        if start_utc - 3660 <= p['t'] < start_utc - 60]
            # And the last price before resolution (entry opportunity)
            pre_prices = [p for p in best if p['t'] < start_utc]
            last_pre = pre_prices[-1] if pre_prices else None

            # Determine outcome: last active price near 0.99 = UP won, near 0.01 = DOWN won
            outcome = None
            if active:
                final_price = active[-1]['p']
                if final_price > 0.90:
                    outcome = 'UP'
                elif final_price < 0.10:
                    outcome = 'DOWN'

            histories.append({
                'slug': m.get('slug', ''),
                'question': m.get('question', ''),
                'coin': m.get('coin', 'BTC'),
                'format': m.get('format', '5min'),
                'resolution_start_utc': start_utc,
                'resolution_end_utc': end_utc,
                'duration_sec': m.get('duration_sec', 300),
                'side': best_side,
                'outcome': outcome,
                'full_history': best,
                'active_window': active,
                'pre_hour': pre_hour,
                'last_pre_price': last_pre['p'] if last_pre else None,
                'last_pre_time': last_pre['t'] if last_pre else None,
                'n_active_pts': len(active),
                'n_total_pts': len(best),
                'interval_used': intervals[0],
            })
        else:
            empty += 1

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(targets)} | got {len(histories)} | {empty} empty | {errors} err")

        # Save periodic checkpoint
        if (i + 1) % 500 == 0 and histories:
            with open(HISTORIES_CACHE, 'w', encoding='utf-8') as f:
                json.dump(histories, f)
            print(f"  [checkpoint saved: {len(histories)} histories]")

    print(f"\nDone: {len(histories)} histories, {empty} empty, {errors} errors")

    with open(HISTORIES_CACHE, 'w', encoding='utf-8') as f:
        json.dump(histories, f)
    return histories


if __name__ == "__main__":
    markets = scan_markets()
    if not markets:
        print("No markets found!")
        sys.exit(1)

    histories = fetch_histories(markets, max_fetch=3000)

    # Quick stats
    if histories:
        with_active = [h for h in histories if h['n_active_pts'] >= 2]
        print(f"\nTotal: {len(histories)} histories, {len(with_active)} with 2+ active pts")

        # Outcome distribution
        outcomes = Counter(h.get('outcome') for h in histories)
        print(f"Outcomes: {dict(outcomes)}")

        # By format
        for fmt in ['5min', '15min', '1hour', '4hour']:
            subset = [h for h in histories if h['format'] == fmt]
            if not subset:
                continue
            with_a = [h for h in subset if h['n_active_pts'] >= 2]
            up = sum(1 for h in subset if h.get('outcome') == 'UP')
            down = sum(1 for h in subset if h.get('outcome') == 'DOWN')
            print(f"\n{fmt}: {len(subset)} total, {len(with_a)} with 2+ active pts")
            print(f"  UP: {up}, DOWN: {down} ({up/(up+down)*100:.1f}% up)" if up + down > 0 else "")
            if subset:
                active = [h['n_active_pts'] for h in subset]
                print(f"  Active pts: min={min(active)}, max={max(active)}, "
                      f"median={sorted(active)[len(active)//2]}")

        # Pre-resolution price analysis (can we predict outcome?)
        with_pre = [h for h in histories if h.get('last_pre_price') is not None
                    and h.get('outcome') is not None]
        if with_pre:
            print(f"\n=== PRE-RESOLUTION PRICE ANALYSIS ({len(with_pre)} markets) ===")
            for threshold in [0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
                above = [h for h in with_pre if h['last_pre_price'] >= threshold]
                if above:
                    wins = sum(1 for h in above if h['outcome'] == 'UP')
                    wr = wins / len(above) * 100
                    avg_price = sum(h['last_pre_price'] for h in above) / len(above)
                    ev = wr/100 * (0.99 - avg_price) - (100-wr)/100 * avg_price
                    print(f"  Pre-price >= ${threshold:.2f}: {len(above):>4} markets, "
                          f"WR={wr:>5.1f}%, avg_entry=${avg_price:.3f}, EV=${ev:+.3f}")

        # By coin
        print(f"\n=== BY COIN ===")
        for coin in ['BTC', 'ETH', 'SOL', 'DOGE', 'XRP']:
            subset = [h for h in histories if h['coin'] == coin and h.get('outcome')]
            if subset:
                up = sum(1 for h in subset if h['outcome'] == 'UP')
                print(f"  {coin}: {len(subset)} markets, {up/(len(subset))*100:.1f}% UP")
    else:
        print("No histories fetched!")
