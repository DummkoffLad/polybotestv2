"""
Comprehensive pattern analysis across 1600+ crypto up/down markets.
Uses price history data to find exploitable edges.
"""
import json, sys
from datetime import datetime, timezone
from collections import Counter, defaultdict

sys.stdout.reconfigure(line_buffering=True)

histories = json.load(open('data/all_crypto_updown_histories.json'))
print(f"Total histories: {len(histories)}")

# =====================================================
# SECTION 1: DATA OVERVIEW
# =====================================================
print("\n" + "="*70)
print("SECTION 1: DATA OVERVIEW")
print("="*70)

fmt_counts = Counter(h['format'] for h in histories)
coin_counts = Counter(h['coin'] for h in histories)
outcome_counts = Counter(h.get('outcome') for h in histories)

print(f"\nBy format: {dict(fmt_counts)}")
print(f"By coin: {dict(coin_counts)}")
print(f"By outcome: {dict(outcome_counts)}")

# Date range
dates = sorted(set(
    datetime.fromtimestamp(h['resolution_start_utc'], tz=timezone.utc).strftime('%Y-%m-%d')
    for h in histories if h.get('resolution_start_utc')
))
print(f"Date range: {dates[0]} to {dates[-1]} ({len(dates)} unique days)")

# =====================================================
# SECTION 2: ACTIVE WINDOW PRICE ANALYSIS
# =====================================================
print("\n" + "="*70)
print("SECTION 2: PRICE TRAJECTORY DURING ACTIVE WINDOW")
print("="*70)

for fmt in ['15min', '1hour', '4hour']:
    subset = [h for h in histories if h['format'] == fmt
              and h.get('outcome') in ('UP', 'DOWN')
              and h['n_active_pts'] >= 2]
    if not subset:
        continue

    print(f"\n--- {fmt} markets ({len(subset)} with outcome) ---")

    # For each market, compute key metrics from active window
    min_prices = []
    max_prices = []
    start_prices = []
    end_prices = []
    ranges = []
    # Track prices at different time percentages
    pct_prices = defaultdict(list)  # {pct: [(price, outcome), ...]}

    for h in subset:
        active = h['active_window']
        if not active:
            continue
        prices = [p['p'] for p in active]
        times = [p['t'] for p in active]
        start_t = h['resolution_start_utc']
        end_t = h['resolution_end_utc']
        duration = end_t - start_t

        min_p = min(prices)
        max_p = max(prices)
        min_prices.append(min_p)
        max_prices.append(max_p)
        start_prices.append(prices[0])
        end_prices.append(prices[-1])
        ranges.append(max_p - min_p)

        # Price at different time percentages through the window
        for pct in [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            target_t = start_t + duration * pct / 100
            # Find closest data point
            closest = min(active, key=lambda p: abs(p['t'] - target_t))
            if abs(closest['t'] - target_t) < duration * 0.15:  # within 15% of window
                pct_prices[pct].append((closest['p'], h['outcome']))

    print(f"  Start price: avg=${sum(start_prices)/len(start_prices):.3f}, "
          f"min=${min(start_prices):.3f}, max=${max(start_prices):.3f}")
    print(f"  End price:   avg=${sum(end_prices)/len(end_prices):.3f}")
    print(f"  Min in window: avg=${sum(min_prices)/len(min_prices):.3f}")
    print(f"  Max in window: avg=${sum(max_prices)/len(max_prices):.3f}")
    print(f"  Price range:   avg=${sum(ranges)/len(ranges):.3f}")

    # Win rate at different time points
    if pct_prices:
        print(f"\n  Time-based price and win rate:")
        for pct in sorted(pct_prices.keys()):
            data = pct_prices[pct]
            if len(data) < 10:
                continue
            avg_p = sum(p for p, _ in data) / len(data)
            up = sum(1 for _, o in data if o == 'UP')
            wr = up / len(data) * 100
            print(f"    t={pct:>3}%: avg_price=${avg_p:.3f}, UP_rate={wr:.1f}% (n={len(data)})")

# =====================================================
# SECTION 3: BUY AT PRICE POINT STRATEGY
# =====================================================
print("\n" + "="*70)
print("SECTION 3: BUY AT PRICE POINT (during active window)")
print("="*70)

for fmt in ['15min', '1hour', '4hour']:
    subset = [h for h in histories if h['format'] == fmt
              and h.get('outcome') in ('UP', 'DOWN')
              and h['n_active_pts'] >= 2]
    if not subset:
        continue

    print(f"\n--- {fmt} ({len(subset)} markets) ---")
    print(f"  {'Price':>8} {'Hit':>5} {'Win':>5} {'WR%':>6} {'AvgEntry':>9} {'EV/trade':>10}")

    for price_threshold in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70,
                            0.75, 0.80, 0.85, 0.90, 0.95]:
        hits = 0
        wins = 0
        entry_prices = []

        for h in subset:
            active = h['active_window']
            prices = [p['p'] for p in active]

            # Did price hit this level?
            hit_prices = [p for p in prices if p >= price_threshold]
            if hit_prices:
                hits += 1
                entry = min(hit_prices)  # best entry at or above threshold
                entry_prices.append(entry)
                if h['outcome'] == 'UP':
                    wins += 1

        if hits >= 5:
            wr = wins / hits * 100
            avg_entry = sum(entry_prices) / len(entry_prices)
            ev = wr/100 * (0.99 - avg_entry) - (100-wr)/100 * avg_entry
            marker = " ***" if ev > 0.02 else " **" if ev > 0 else ""
            print(f"  ${price_threshold:.2f}  {hits:>5} {wins:>5} {wr:>5.1f}% ${avg_entry:>8.3f} ${ev:>+9.4f}{marker}")

# =====================================================
# SECTION 4: TIME-BASED ENTRY (for 1hour markets with enough data)
# =====================================================
print("\n" + "="*70)
print("SECTION 4: TIME-BASED ENTRY (buy after minute X at price >= Y)")
print("="*70)

for fmt in ['1hour', '4hour']:
    subset = [h for h in histories if h['format'] == fmt
              and h.get('outcome') in ('UP', 'DOWN')
              and h['n_active_pts'] >= 3]
    if not subset:
        continue

    duration_min = 60 if fmt == '1hour' else 240
    print(f"\n--- {fmt} ({len(subset)} markets, {duration_min}min window) ---")

    for min_time_pct in [0, 20, 30, 40, 50, 60, 70, 80]:
        min_time = duration_min * min_time_pct / 100
        print(f"\n  After minute {min_time:.0f} ({min_time_pct}% through):")
        print(f"  {'Price':>8} {'Hit':>5} {'Win':>5} {'WR%':>6} {'EV/trade':>10}")

        for price in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
            hits = 0
            wins = 0
            entries = []

            for h in subset:
                start_t = h['resolution_start_utc']
                cutoff_t = start_t + min_time * 60

                active = h['active_window']
                late_pts = [p for p in active
                           if p['t'] >= cutoff_t and p['p'] >= price]
                if late_pts:
                    hits += 1
                    entry = min(p['p'] for p in late_pts)
                    entries.append(entry)
                    if h['outcome'] == 'UP':
                        wins += 1

            if hits >= 5:
                wr = wins / hits * 100
                avg_e = sum(entries) / len(entries)
                ev = wr/100 * (0.99 - avg_e) - (100-wr)/100 * avg_e
                marker = " ***" if ev > 0.02 else " **" if ev > 0 else ""
                print(f"  ${price:.2f}  {hits:>5} {wins:>5} {wr:>5.1f}% ${ev:>+9.4f}{marker}")

# =====================================================
# SECTION 5: MOMENTUM ANALYSIS
# =====================================================
print("\n" + "="*70)
print("SECTION 5: MOMENTUM (price change in first X% predicts outcome)")
print("="*70)

for fmt in ['1hour', '4hour']:
    subset = [h for h in histories if h['format'] == fmt
              and h.get('outcome') in ('UP', 'DOWN')
              and h['n_active_pts'] >= 4]
    if not subset:
        continue

    print(f"\n--- {fmt} ({len(subset)} markets) ---")

    for split_pct in [20, 30, 40, 50]:
        print(f"\n  Momentum from first {split_pct}% of window:")
        print(f"  {'Momentum':>10} {'Count':>6} {'UP':>4} {'WR%':>6} {'+ Price':>8} {'EV':>10}")

        for mom_threshold in [-0.10, -0.05, 0.00, 0.05, 0.10, 0.15, 0.20, 0.30]:
            count = 0
            ups = 0
            current_prices = []

            for h in subset:
                start_t = h['resolution_start_utc']
                end_t = h['resolution_end_utc']
                duration = end_t - start_t
                split_t = start_t + duration * split_pct / 100

                active = h['active_window']
                early = [p for p in active if p['t'] <= split_t]
                late = [p for p in active if p['t'] > split_t]

                if not early or not late:
                    continue

                early_last = early[-1]['p']
                first_price = early[0]['p']
                momentum = early_last - first_price

                if momentum >= mom_threshold:
                    count += 1
                    current_prices.append(early_last)
                    if h['outcome'] == 'UP':
                        ups += 1

            if count >= 5:
                wr = ups / count * 100
                avg_p = sum(current_prices) / len(current_prices)
                ev = wr/100 * (0.99 - avg_p) - (100-wr)/100 * avg_p
                marker = " ***" if ev > 0.02 else " **" if ev > 0 else ""
                print(f"  >=${mom_threshold:>+.2f}   {count:>5} {ups:>4} {wr:>5.1f}% ${avg_p:>.3f}   ${ev:>+.4f}{marker}")

# =====================================================
# SECTION 6: REVERSALS (price hits X then resolves opposite)
# =====================================================
print("\n" + "="*70)
print("SECTION 6: REVERSAL RISK (price hits X but resolves DOWN)")
print("="*70)

for fmt in ['15min', '1hour', '4hour']:
    subset = [h for h in histories if h['format'] == fmt
              and h.get('outcome') in ('UP', 'DOWN')
              and h['n_active_pts'] >= 2]
    if not subset:
        continue

    print(f"\n--- {fmt} ({len(subset)} markets) ---")
    print(f"  {'Max Price':>10} {'Hit':>5} {'Reversed':>9} {'Rev%':>6}")

    for threshold in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]:
        hit = 0
        reversed_count = 0
        for h in subset:
            prices = [p['p'] for p in h['active_window']]
            if max(prices) >= threshold:
                hit += 1
                if h['outcome'] == 'DOWN':
                    reversed_count += 1

        if hit >= 5:
            rev_pct = reversed_count / hit * 100
            print(f"  ${threshold:.2f}     {hit:>5} {reversed_count:>8}  {rev_pct:>5.1f}%")

# =====================================================
# SECTION 7: PRE-RESOLUTION PRICE AS PREDICTOR
# =====================================================
print("\n" + "="*70)
print("SECTION 7: PRE-RESOLUTION PRICE (last price before window opens)")
print("="*70)

for fmt in ['15min', '1hour', '4hour']:
    subset = [h for h in histories if h['format'] == fmt
              and h.get('outcome') in ('UP', 'DOWN')
              and h.get('last_pre_price') is not None]
    if not subset:
        continue

    print(f"\n--- {fmt} ({len(subset)} markets with pre-price) ---")
    print(f"  {'Pre-Price':>10} {'Count':>6} {'UP':>4} {'WR%':>6} {'AvgPre':>8} {'EV':>10}")

    for threshold in [0.40, 0.45, 0.50, 0.52, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        above = [h for h in subset if h['last_pre_price'] >= threshold]
        if len(above) >= 5:
            ups = sum(1 for h in above if h['outcome'] == 'UP')
            wr = ups / len(above) * 100
            avg_pre = sum(h['last_pre_price'] for h in above) / len(above)
            ev = wr/100 * (0.99 - avg_pre) - (100-wr)/100 * avg_pre
            marker = " ***" if ev > 0.02 else " **" if ev > 0 else ""
            print(f"  >=${threshold:.2f}   {len(above):>5} {ups:>4} {wr:>5.1f}% ${avg_pre:.3f}   ${ev:>+.4f}{marker}")

    # Also check: pre-price < 0.50 (bearish setup)
    below = [h for h in subset if h['last_pre_price'] < 0.50]
    if below:
        downs = sum(1 for h in below if h['outcome'] == 'DOWN')
        wr_down = downs / len(below) * 100
        avg_pre = sum(h['last_pre_price'] for h in below) / len(below)
        print(f"\n  Pre-price < $0.50: {len(below)} markets, {wr_down:.1f}% resolve DOWN")
        print(f"  (Shorting at avg ${avg_pre:.3f} -> EV ${wr_down/100 * avg_pre - (100-wr_down)/100 * (0.99-avg_pre):+.4f})")

# =====================================================
# SECTION 8: BOTH-SIDES STRATEGY
# =====================================================
print("\n" + "="*70)
print("SECTION 8: BOTH-SIDES (buy UP + DOWN cheaply, one resolves $0.99)")
print("="*70)

for fmt in ['15min', '1hour', '4hour']:
    subset = [h for h in histories if h['format'] == fmt
              and h.get('outcome') in ('UP', 'DOWN')
              and h['n_active_pts'] >= 2]
    if not subset:
        continue

    print(f"\n--- {fmt} ({len(subset)} markets) ---")

    # The idea: both UP and DOWN tokens are available
    # UP token = our price data (side 0), DOWN token ≈ 1 - UP price
    # Buy UP at min_price, buy DOWN at (1 - max_price)
    # One pays $0.99, cost = min + (1 - max) = min - max + 1

    trades = 0
    total_pnl = 0
    costs = []

    for h in subset:
        prices = [p['p'] for p in h['active_window']]
        min_p = min(prices)
        max_p = max(prices)

        # Cost to buy both sides at best prices
        up_cost = min_p  # buy UP at minimum
        down_cost = 1 - max_p  # buy DOWN when UP is at max (DOWN = 1 - UP)

        if up_cost + down_cost < 0.99:  # profitable if total cost < $0.99
            profit = 0.99 - up_cost - down_cost
            total_pnl += profit
            trades += 1
            costs.append(up_cost + down_cost)

    if trades:
        avg_cost = sum(costs) / len(costs)
        print(f"  Profitable both-sides trades: {trades}/{len(subset)} "
              f"({trades/len(subset)*100:.1f}%)")
        print(f"  Avg cost: ${avg_cost:.3f}, Avg profit: ${total_pnl/trades:.3f}")
        print(f"  Total PnL: ${total_pnl:.2f} across {trades} trades")
        print(f"  NOTE: This assumes perfect timing (buy at min, sell at max) - not realistic")

# =====================================================
# SECTION 9: COIN COMPARISON
# =====================================================
print("\n" + "="*70)
print("SECTION 9: COIN-SPECIFIC PATTERNS")
print("="*70)

for fmt in ['15min', '1hour']:
    subset = [h for h in histories if h['format'] == fmt
              and h.get('outcome') in ('UP', 'DOWN')]
    if not subset:
        continue

    print(f"\n--- {fmt} ---")
    for coin in ['BTC', 'ETH', 'SOL', 'XRP']:
        coin_data = [h for h in subset if h['coin'] == coin]
        if not coin_data:
            continue
        ups = sum(1 for h in coin_data if h['outcome'] == 'UP')
        wr = ups / len(coin_data) * 100
        print(f"  {coin}: {len(coin_data)} markets, {wr:.1f}% UP")

# =====================================================
# SECTION 10: TIME OF DAY ANALYSIS
# =====================================================
print("\n" + "="*70)
print("SECTION 10: TIME OF DAY (UTC hour)")
print("="*70)

for fmt in ['15min', '1hour']:
    subset = [h for h in histories if h['format'] == fmt
              and h.get('outcome') in ('UP', 'DOWN')]
    if not subset:
        continue

    print(f"\n--- {fmt} ---")
    hour_data = defaultdict(lambda: {'up': 0, 'down': 0})
    for h in subset:
        hour = datetime.fromtimestamp(
            h['resolution_start_utc'], tz=timezone.utc).hour
        if h['outcome'] == 'UP':
            hour_data[hour]['up'] += 1
        else:
            hour_data[hour]['down'] += 1

    print(f"  {'Hour':>6} {'Total':>6} {'UP':>4} {'WR%':>6}")
    for hour in sorted(hour_data.keys()):
        d = hour_data[hour]
        total = d['up'] + d['down']
        if total >= 5:
            wr = d['up'] / total * 100
            marker = " *" if wr > 55 or wr < 40 else ""
            print(f"  {hour:>4}h  {total:>5} {d['up']:>4} {wr:>5.1f}%{marker}")

# =====================================================
# SECTION 11: COMBINED STRATEGY (momentum + price + time)
# =====================================================
print("\n" + "="*70)
print("SECTION 11: COMBINED STRATEGIES")
print("="*70)

for fmt in ['1hour']:
    subset = [h for h in histories if h['format'] == fmt
              and h.get('outcome') in ('UP', 'DOWN')
              and h['n_active_pts'] >= 4]
    if not subset:
        continue

    print(f"\n--- {fmt} ({len(subset)} markets) ---")

    # Strategy: momentum in first 30% + current price
    print("\n  Strategy: Momentum >= X in first 30% AND current price >= Y")
    print(f"  {'Mom':>6} {'Price':>6} {'Hit':>5} {'Win':>5} {'WR%':>6} {'EV':>10}")

    for mom in [0.00, 0.05, 0.10, 0.15]:
        for price_min in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
            hits = 0
            wins = 0
            entries = []

            for h in subset:
                start_t = h['resolution_start_utc']
                end_t = h['resolution_end_utc']
                duration = end_t - start_t
                split_t = start_t + duration * 0.3

                active = h['active_window']
                early = [p for p in active if p['t'] <= split_t]
                if not early or len(early) < 2:
                    continue

                momentum = early[-1]['p'] - early[0]['p']
                current_price = early[-1]['p']

                if momentum >= mom and current_price >= price_min:
                    hits += 1
                    entries.append(current_price)
                    if h['outcome'] == 'UP':
                        wins += 1

            if hits >= 5:
                wr = wins / hits * 100
                avg_e = sum(entries) / len(entries)
                ev = wr/100 * (0.99 - avg_e) - (100-wr)/100 * avg_e
                if ev > 0:
                    print(f"  {mom:>+.2f}  ${price_min:.2f} {hits:>5} {wins:>5} {wr:>5.1f}% ${ev:>+.4f} ***")

    # Strategy: High pre-price + in-window confirmation
    subset_pre = [h for h in subset if h.get('last_pre_price') is not None]
    if subset_pre:
        print(f"\n  Strategy: Pre-price >= X AND early window price >= Y")
        print(f"  {'Pre':>6} {'InWin':>6} {'Hit':>5} {'Win':>5} {'WR%':>6} {'EV':>10}")

        for pre_min in [0.50, 0.52, 0.55]:
            for in_win_min in [0.55, 0.60, 0.65, 0.70]:
                hits = 0
                wins = 0
                entries = []

                for h in subset_pre:
                    if h['last_pre_price'] < pre_min:
                        continue
                    active = h['active_window']
                    early_prices = [p['p'] for p in active[:len(active)//3+1]]
                    if not early_prices:
                        continue
                    max_early = max(early_prices)
                    if max_early >= in_win_min:
                        hits += 1
                        entries.append(max_early)
                        if h['outcome'] == 'UP':
                            wins += 1

                if hits >= 5:
                    wr = wins / hits * 100
                    avg_e = sum(entries) / len(entries)
                    ev = wr/100 * (0.99 - avg_e) - (100-wr)/100 * avg_e
                    if ev > 0:
                        print(f"  ${pre_min:.2f}  ${in_win_min:.2f} {hits:>5} {wins:>5} {wr:>5.1f}% ${ev:>+.4f} ***")

print("\n" + "="*70)
print("ANALYSIS COMPLETE")
print("="*70)
