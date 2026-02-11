"""Check data resolution across different intervals for recent markets."""
import json, sys, requests, time
from datetime import datetime, timezone
sys.stdout.reconfigure(line_buffering=True)

markets = json.load(open('data/all_crypto_updown_markets.json'))

# Parse real timestamps
for m in markets:
    est = m.get('eventStartTime', '')
    edt = m.get('endDate', '')
    try:
        m['_start_utc'] = int(datetime.fromisoformat(est.replace('Z', '+00:00')).timestamp()) if est else 0
        m['_end_utc'] = int(datetime.fromisoformat(edt.replace('Z', '+00:00')).timestamp()) if edt else 0
    except:
        m['_start_utc'] = 0
        m['_end_utc'] = 0

markets.sort(key=lambda m: m['_start_utc'], reverse=True)

# Pick a few markets of different types
test_markets = []
# Recent 5-min (today)
for m in markets:
    if m.get('format') == '5min' and m['_start_utc'] > 0:
        test_markets.append(('5min-today', m))
        break
# 1-hour (today)
for m in markets:
    if m.get('format') == '1hour' and m['_start_utc'] > 0:
        test_markets.append(('1hour-today', m))
        break
# 5-min from a week ago
for m in markets:
    dt = datetime.fromtimestamp(m['_start_utc'], tz=timezone.utc)
    if m.get('format') == '5min' and dt.day <= 3 and dt.month == 2:
        test_markets.append(('5min-week-ago', m))
        break

for label, m in test_markets:
    q = m.get('question', '')[:70]
    fmt = m.get('format', '?')
    duration = m.get('duration_sec', 0)
    tokens_str = m.get('clobTokenIds', '[]')
    tokens = json.loads(tokens_str)
    if not tokens:
        continue

    dt = datetime.fromtimestamp(m['_start_utc'], tz=timezone.utc)
    print(f"\n=== {label}: {q} ===")
    print(f"  format={fmt} duration={duration}s start={dt}")

    tid = tokens[0]
    for interval in ['1h', '6h', '1d', 'max']:
        try:
            r = requests.get('https://clob.polymarket.com/prices-history', params={
                'market': tid, 'interval': interval, 'fidelity': 1,
            }, timeout=10)
            if r.status_code == 200:
                h = r.json().get('history', [])
                if h:
                    span_h = (h[-1]['t'] - h[0]['t']) / 3600
                    active = [p for p in h
                              if m['_start_utc'] - 60 <= p['t'] <= m['_end_utc'] + 60]
                    prices = [p['p'] for p in active] if active else []
                    unique = len(set(f"{p:.4f}" for p in prices))
                    print(f"  {interval:>4s}: {len(h):>5} pts, span={span_h:>6.1f}h, "
                          f"active={len(active):>3} pts, unique_prices={unique}")
                    if active and interval in ['1h', '6h']:
                        # Show first and last few prices in active window
                        show = active[:3] + active[-3:]
                        for p in show:
                            pdt = datetime.fromtimestamp(p['t'], tz=timezone.utc)
                            print(f"    {pdt.strftime('%H:%M:%S')} ${p['p']:.4f}")
                else:
                    print(f"  {interval:>4s}: empty")
            else:
                print(f"  {interval:>4s}: HTTP {r.status_code}")
        except Exception as e:
            print(f"  {interval:>4s}: error {e}")
        time.sleep(0.1)

# Check how far back interval=max goes
print("\n\n=== DATA AVAILABILITY CUTOFF ===")
for idx in range(0, min(3000, len(markets)), 100):
    m = markets[idx]
    if m['_start_utc'] == 0:
        continue
    tokens_str = m.get('clobTokenIds', '[]')
    tokens = json.loads(tokens_str)
    if not tokens:
        continue
    dt = datetime.fromtimestamp(m['_start_utc'], tz=timezone.utc)
    try:
        r = requests.get('https://clob.polymarket.com/prices-history', params={
            'market': tokens[0], 'interval': 'max', 'fidelity': 1,
        }, timeout=10)
        if r.status_code == 200:
            h = r.json().get('history', [])
            status = f"{len(h)} pts" if h else "empty"
        else:
            status = f"HTTP {r.status_code}"
    except:
        status = "error"
    print(f"  [{idx}] {dt.date()} {dt.strftime('%H:%M')} — {status}")
    time.sleep(0.05)
