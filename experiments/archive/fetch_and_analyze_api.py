"""
Fetch crypto up/down markets from Polymarket Gamma API and analyze price histories.
Targets: BTC, ETH, SOL hourly and 15-min markets.
"""
import requests
import json
import time
import statistics
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
CACHE_FILE = Path("data/api_market_cache.json")


def fetch_crypto_markets():
    """Fetch crypto up/down markets from Gamma API."""
    if CACHE_FILE.exists():
        print(f"Loading cached markets from {CACHE_FILE}...")
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)

    print("Fetching crypto up/down markets from Gamma API...")
    all_markets = []
    seen = set()

    # Use slug-based search since these markets have predictable slug patterns
    # btc-updown-1h-{timestamp}, btc-updown-15m-{timestamp}, etc.
    # Also: bitcoin-up-or-down-*

    # Strategy: fetch recently closed markets and filter
    for offset in range(0, 20000, 100):
        try:
            r = requests.get(f"{GAMMA_URL}/markets", params={
                'limit': 100,
                'offset': offset,
                'closed': True,
                'order': 'volume24hr',
                'ascending': False,
            }, timeout=15)
        except Exception as e:
            print(f"  Request error at offset {offset}: {e}")
            time.sleep(2)
            continue

        if r.status_code != 200:
            print(f"  Error {r.status_code} at offset {offset}")
            break

        data = r.json()
        if not data:
            print(f"  Empty at offset {offset}")
            break

        batch_found = 0
        for m in data:
            cid = m.get('conditionId', '')
            if cid in seen:
                continue
            seen.add(cid)

            slug = m.get('slug', '')
            q = m.get('question', '').lower()

            is_crypto_updown = (
                ('updown' in slug and any(x in slug for x in ['btc', 'eth', 'sol'])) or
                ('up or down' in q and any(x in q for x in ['bitcoin', 'ethereum', 'solana', 'btc', 'eth', 'sol']))
            )

            if is_crypto_updown:
                all_markets.append(m)
                batch_found += 1

        if offset % 500 == 0:
            print(f"  offset={offset}, found_total={len(all_markets)}, batch={batch_found}")

        # If we've gotten a lot with no new finds, stop
        if offset > 5000 and batch_found == 0:
            break

        time.sleep(0.1)  # Rate limiting

    print(f"\nTotal crypto up/down markets found: {len(all_markets)}")

    # Cache for re-use
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, 'w', encoding="utf-8") as f:
        json.dump(all_markets, f)

    return all_markets


def categorize_markets(markets):
    """Categorize markets by crypto and duration."""
    categories = defaultdict(list)

    for m in markets:
        slug = m.get('slug', '')
        q = m.get('question', '').lower()

        # Determine crypto
        if 'btc' in slug or 'bitcoin' in q:
            crypto = 'BTC'
        elif 'eth' in slug or 'ethereum' in q:
            crypto = 'ETH'
        elif 'sol' in slug or 'solana' in q:
            crypto = 'SOL'
        else:
            crypto = 'OTHER'

        # Determine duration
        if '15m' in slug:
            duration = '15min'
        elif '1h' in slug:
            duration = '1hour'
        else:
            # Check from dates
            start = m.get('startDate', '')
            end = m.get('endDate', '')
            # Check question for time range
            if 'am' in q or 'pm' in q:
                # Has time range in question - parse duration
                duration = 'hourly_old'
            else:
                duration = 'daily'

        categories[f"{crypto}_{duration}"].append(m)

    return categories


def fetch_price_history(token_id, interval="max", fidelity=1):
    """Fetch price history for a single token from CLOB API."""
    try:
        r = requests.get(f"{CLOB_URL}/prices-history", params={
            'market': token_id,
            'interval': interval,
            'fidelity': fidelity,
        }, timeout=10)
        if r.status_code == 200:
            return r.json().get('history', [])
    except Exception as e:
        pass
    return []


def analyze_markets(markets, max_fetch=500):
    """Analyze a set of markets by fetching their price histories."""
    results = []
    errors = 0
    skipped = 0

    print(f"\nAnalyzing {min(len(markets), max_fetch)} markets (fetching price histories)...")

    for i, m in enumerate(markets[:max_fetch]):
        tokens_str = m.get('clobTokenIds', '[]')
        try:
            tokens = json.loads(tokens_str)
        except:
            skipped += 1
            continue

        if len(tokens) < 2:
            skipped += 1
            continue

        # Fetch price history for both tokens (YES and NO)
        # Use fidelity=1 for 1-minute resolution
        history_yes = fetch_price_history(tokens[0], fidelity=1)
        history_no = fetch_price_history(tokens[1], fidelity=1)

        if not history_yes and not history_no:
            # Try with lower fidelity
            history_yes = fetch_price_history(tokens[0], fidelity=5)
            history_no = fetch_price_history(tokens[1], fidelity=5)

        if not history_yes and not history_no:
            errors += 1
            if errors > 20:
                print(f"  Too many errors ({errors}), reducing scope...")
            continue

        result = analyze_single_market(m, history_yes, history_no)
        if result:
            results.append(result)

        if (i + 1) % 50 == 0:
            print(f"  Processed {i+1}/{min(len(markets), max_fetch)}, got {len(results)} valid, {errors} errors")

        time.sleep(0.05)  # Rate limit

    print(f"  Done: {len(results)} valid markets, {errors} errors, {skipped} skipped")
    return results


def analyze_single_market(market, history_yes, history_no):
    """Analyze a single market's price history."""
    # Use whichever history has data
    for history, side in [(history_yes, 'YES'), (history_no, 'NO')]:
        if not history or len(history) < 3:
            continue

        prices = [h['p'] for h in history]
        timestamps = [h['t'] for h in history]

        if not prices:
            continue

        min_price = min(prices)
        max_price = max(prices)
        first_price = prices[0]
        last_price = prices[-1]
        price_range = max_price - min_price

        if price_range < 0.01:
            continue  # No meaningful movement

        # Time analysis
        duration_sec = timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0
        duration_min = duration_sec / 60

        # Find when min/max occur (as % of market duration)
        min_idx = prices.index(min_price)
        max_idx = prices.index(max_price)
        min_pct = min_idx / len(prices) * 100 if len(prices) > 1 else 50
        max_pct = max_idx / len(prices) * 100 if len(prices) > 1 else 50

        # Resolution: did it resolve high or low?
        resolved_high = last_price >= 0.90
        resolved_low = last_price <= 0.10

        # Hit thresholds
        hit_90 = any(p >= 0.90 for p in prices)
        hit_10 = any(p <= 0.10 for p in prices)

        # Reversion from high
        reverted_from_90 = False
        min_after_90 = None
        if hit_90:
            first_90 = next(i for i, p in enumerate(prices) if p >= 0.90)
            after = prices[first_90:]
            if after:
                min_after_90 = min(after)
                reverted_from_90 = min_after_90 < 0.70

        # Price at each quartile of time
        q1 = prices[len(prices)//4] if len(prices) > 4 else first_price
        q2 = prices[len(prices)//2] if len(prices) > 2 else first_price
        q3 = prices[3*len(prices)//4] if len(prices) > 4 else last_price

        return {
            'question': market.get('question', ''),
            'slug': market.get('slug', ''),
            'side': side,
            'n_points': len(prices),
            'duration_min': duration_min,
            'first_price': first_price,
            'last_price': last_price,
            'min_price': min_price,
            'max_price': max_price,
            'price_range': price_range,
            'min_pct_time': min_pct,
            'max_pct_time': max_pct,
            'resolved_high': resolved_high,
            'resolved_low': resolved_low,
            'hit_90': hit_90,
            'hit_10': hit_10,
            'reverted_from_90': reverted_from_90,
            'min_after_90': min_after_90,
            'q1_price': q1,
            'q2_price': q2,
            'q3_price': q3,
            # For deeper analysis
            'prices': prices,
            'timestamps': timestamps,
        }

    return None


def print_analysis(results, label="ALL"):
    """Print comprehensive analysis of results."""
    if not results:
        print(f"No results for {label}")
        return

    n = len(results)
    print(f"\n{'='*80}")
    print(f"ANALYSIS: {label} ({n} markets)")
    print(f"{'='*80}")

    # ── Q1: Can you always buy at $0.45? ──
    print(f"\n--- CAN YOU BUY AT $0.45? ---")
    for thresh in [0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20]:
        count = sum(1 for r in results if r['min_price'] <= thresh)
        print(f"  Min price <= ${thresh:.2f}: {count}/{n} = {count/n*100:.1f}%")

    min_prices = sorted([r['min_price'] for r in results])
    print(f"\n  Min price distribution:")
    for pct in [10, 25, 50, 75, 90]:
        idx = int(len(min_prices) * pct / 100)
        print(f"    P{pct}: {min_prices[min(idx, len(min_prices)-1)]:.3f}")

    # ── Q2: Reversion from $0.90 ──
    print(f"\n--- REVERSION FROM $0.90+ ---")
    hit_90 = [r for r in results if r['hit_90']]
    if hit_90:
        print(f"  Markets that hit $0.90+: {len(hit_90)}/{n} = {len(hit_90)/n*100:.1f}%")
        stayed_high = sum(1 for r in hit_90 if r['resolved_high'])
        reverted = sum(1 for r in hit_90 if r['reverted_from_90'])
        print(f"  Of those, ended >= $0.90 (stayed): {stayed_high}/{len(hit_90)} = {stayed_high/len(hit_90)*100:.1f}%")
        print(f"  Of those, dropped below $0.70:     {reverted}/{len(hit_90)} = {reverted/len(hit_90)*100:.1f}%")

        # By timing of when it hit 0.90
        early_90 = [r for r in hit_90 if r['max_pct_time'] < 33]
        mid_90 = [r for r in hit_90 if 33 <= r['max_pct_time'] < 66]
        late_90 = [r for r in hit_90 if r['max_pct_time'] >= 66]

        if early_90:
            e_stayed = sum(1 for r in early_90 if r['resolved_high'])
            print(f"  Hit 0.90+ in first third:  {len(early_90)} cases, {e_stayed}/{len(early_90)} ended high")
        if mid_90:
            m_stayed = sum(1 for r in mid_90 if r['resolved_high'])
            print(f"  Hit 0.90+ in middle third: {len(mid_90)} cases, {m_stayed}/{len(mid_90)} ended high")
        if late_90:
            l_stayed = sum(1 for r in late_90 if r['resolved_high'])
            print(f"  Hit 0.90+ in last third:   {len(late_90)} cases, {l_stayed}/{len(late_90)} ended high")

    # ── Q3: Volatility ──
    print(f"\n--- PRICE VOLATILITY ---")
    ranges = sorted([r['price_range'] for r in results])
    for pct in [10, 25, 50, 75, 90]:
        idx = int(len(ranges) * pct / 100)
        print(f"  P{pct} range: {ranges[min(idx, len(ranges)-1)]:.3f}")

    # Bucket
    for lo, hi, label in [(0, 0.10, '<10c'), (0.10, 0.20, '10-20c'), (0.20, 0.40, '20-40c'), (0.40, 0.60, '40-60c'), (0.60, 1.0, '60c+')]:
        count = sum(1 for r in ranges if lo <= r < hi)
        print(f"  Range {label}: {count}/{n} = {count/n*100:.1f}%")

    # ── Q4: Resolution outcomes ──
    print(f"\n--- RESOLUTION OUTCOMES ---")
    high = sum(1 for r in results if r['resolved_high'])
    low = sum(1 for r in results if r['resolved_low'])
    mid = n - high - low
    print(f"  Resolved HIGH (>= 0.90): {high}/{n} = {high/n*100:.1f}%")
    print(f"  Resolved LOW  (<= 0.10): {low}/{n} = {low/n*100:.1f}%")
    print(f"  Ended MID:               {mid}/{n} = {mid/n*100:.1f}%")

    # ── Q5: Opening prices ──
    print(f"\n--- OPENING PRICES ---")
    first_prices = sorted([r['first_price'] for r in results])
    for pct in [10, 25, 50, 75, 90]:
        idx = int(len(first_prices) * pct / 100)
        print(f"  P{pct} first price: {first_prices[min(idx, len(first_prices)-1)]:.3f}")

    # ── Q6: Timing of extremes ──
    print(f"\n--- WHEN DO MIN/MAX PRICES OCCUR? (% through market duration) ---")
    active = [r for r in results if r['price_range'] > 0.10]
    if active:
        for lo, hi, label in [(0, 20, 'First 20%'), (20, 40, '20-40%'), (40, 60, '40-60%'), (60, 80, '60-80%'), (80, 100, 'Last 20%')]:
            min_count = sum(1 for r in active if lo <= r['min_pct_time'] < hi)
            max_count = sum(1 for r in active if lo <= r['max_pct_time'] < hi)
            n_act = len(active)
            print(f"  {label:>10}: MIN occurs {min_count}/{n_act} ({min_count/n_act*100:.0f}%) | MAX occurs {max_count}/{n_act} ({max_count/n_act*100:.0f}%)")

    # ── Q7: Momentum ──
    print(f"\n--- MOMENTUM: First quarter vs rest ---")
    momentum_data = [r for r in results if r['price_range'] > 0.05]
    if momentum_data:
        early_up_cont = 0
        early_up_rev = 0
        early_down_cont = 0
        early_down_rev = 0

        for r in momentum_data:
            early_move = r['q1_price'] - r['first_price']
            late_move = r['last_price'] - r['q1_price']

            if early_move > 0.03:
                if late_move > 0:
                    early_up_cont += 1
                else:
                    early_up_rev += 1
            elif early_move < -0.03:
                if late_move < 0:
                    early_down_cont += 1
                else:
                    early_down_rev += 1

        total_up = early_up_cont + early_up_rev
        total_down = early_down_cont + early_down_rev
        if total_up:
            print(f"  Early UP move: continues {early_up_cont}/{total_up} = {early_up_cont/total_up*100:.0f}%, reverts {early_up_rev}/{total_up} = {early_up_rev/total_up*100:.0f}%")
        if total_down:
            print(f"  Early DOWN move: continues {early_down_cont}/{total_down} = {early_down_cont/total_down*100:.0f}%, reverts {early_down_rev}/{total_down} = {early_down_rev/total_down*100:.0f}%")

    # ── Q8: Cheap ticket EV ──
    print(f"\n--- CHEAP TICKET STRATEGY (buy low, hold to resolution) ---")
    for entry_thresh in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
        cases = [r for r in results if r['min_price'] <= entry_thresh]
        if not cases:
            continue
        pnls = []
        for r in cases:
            entry = r['min_price']
            final = r['last_price']
            if entry > 0:
                pnl_per_dollar = (final - entry) / entry
                pnls.append(pnl_per_dollar)

        if pnls:
            avg_ev = statistics.mean(pnls)
            wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            print(f"  Buy at <={entry_thresh:.2f}: {len(cases):>4} cases | WR={wr:>5.1f}% | EV/$ = ${avg_ev:>+.2f}")


def main():
    print("=" * 80)
    print("POLYMARKET CRYPTO UP/DOWN MARKET ANALYSIS (API DATA)")
    print("=" * 80)

    # Step 1: Fetch markets
    markets = fetch_crypto_markets()
    print(f"\nTotal markets: {len(markets)}")

    # Step 2: Categorize
    categories = categorize_markets(markets)
    print("\nMarket categories:")
    for cat, ms in sorted(categories.items()):
        print(f"  {cat}: {len(ms)}")

    # Step 3: Analyze - focus on highest-volume markets first
    # Sort all markets by volume
    markets_by_vol = sorted(markets, key=lambda m: m.get('volume_num', m.get('volume24hr', 0)) or 0, reverse=True)

    # Show volume distribution
    vols = [m.get('volume_num', m.get('volume24hr', 0)) or 0 for m in markets_by_vol]
    print(f"\nVolume distribution:")
    print(f"  Top 10 avg: ${statistics.mean(vols[:10]):,.0f}")
    print(f"  Top 50 avg: ${statistics.mean(vols[:50]):,.0f}")
    if len(vols) > 100:
        print(f"  Top 100 avg: ${statistics.mean(vols[:100]):,.0f}")
    print(f"  Total avg: ${statistics.mean(vols):,.0f}")
    print(f"  Zero volume: {sum(1 for v in vols if v == 0)}")

    # Analyze top markets by volume (these have actual price history)
    active_markets = [m for m in markets_by_vol if (m.get('volume_num', m.get('volume24hr', 0)) or 0) > 0]
    print(f"\nMarkets with volume > 0: {len(active_markets)}")

    # Fetch price histories and analyze
    results = analyze_markets(active_markets, max_fetch=300)

    if not results:
        print("\nNo valid price histories found. Trying broader search...")
        results = analyze_markets(markets_by_vol, max_fetch=100)

    if results:
        # Overall analysis
        print_analysis(results, "ALL CRYPTO UP/DOWN")

        # By crypto
        for crypto in ['BTC', 'ETH', 'SOL']:
            crypto_results = [r for r in results if crypto.lower() in r['slug'].lower() or crypto.lower() in r['question'].lower()]
            if len(crypto_results) >= 10:
                print_analysis(crypto_results, f"{crypto} ONLY")

        # By duration (if we can tell)
        hourly = [r for r in results if r['duration_min'] > 45]
        fifteen = [r for r in results if 10 < r['duration_min'] <= 20]
        if len(hourly) >= 10:
            print_analysis(hourly, "HOURLY (>45 min)")
        if len(fifteen) >= 10:
            print_analysis(fifteen, "15-MIN MARKETS")

    print("\n" + "=" * 80)
    print("DONE")
    print("=" * 80)


if __name__ == "__main__":
    main()
