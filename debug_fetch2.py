"""Debug price data availability across different market ages."""
import json, sys, requests, time
from datetime import datetime, timezone
sys.stdout.reconfigure(line_buffering=True)

markets = json.load(open('data/all_crypto_updown_markets.json'))
print(f"Total markets: {len(markets)}")

# Use eventStartTime from API (correct timestamps)
for m in markets:
    est = m.get('eventStartTime', '')
    edt = m.get('endDate', '')
    if est:
        try:
            dt = datetime.fromisoformat(est.replace('Z', '+00:00'))
            m['_start_utc'] = int(dt.timestamp())
        except:
            m['_start_utc'] = 0
    else:
        m['_start_utc'] = 0
    if edt:
        try:
            dt = datetime.fromisoformat(edt.replace('Z', '+00:00'))
            m['_end_utc'] = int(dt.timestamp())
        except:
            m['_end_utc'] = 0
    else:
        m['_end_utc'] = 0

# Sort by actual start time (newest first)
markets.sort(key=lambda m: m['_start_utc'], reverse=True)

# Show date range
newest = datetime.fromtimestamp(markets[0]['_start_utc'], tz=timezone.utc)
oldest = datetime.fromtimestamp(markets[-1]['_start_utc'], tz=timezone.utc)
print(f"Date range: {oldest.date()} to {newest.date()}")

# Sample from different time periods
test_indices = [0, 10, 50, 100, 500, 1000, 2000, 5000, 10000, 20000, 30000]
for idx in test_indices:
    if idx >= len(markets):
        continue
    m = markets[idx]
    q = m.get('question', '')[:60]
    start_dt = datetime.fromtimestamp(m['_start_utc'], tz=timezone.utc)

    tokens_str = m.get('clobTokenIds', '[]')
    try:
        tokens = json.loads(tokens_str)
    except:
        tokens = []

    if not tokens:
        print(f"\n[{idx}] {start_dt.date()} {q} — NO TOKENS")
        continue

    tid = tokens[0]
    result = "no data"
    for interval in ['max', '1d']:
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
                    result = f"interval={interval} {len(h)}pts span={span_h:.1f}h active={len(active)}"
                    break
        except:
            pass
        time.sleep(0.05)

    print(f"[{idx}] {start_dt.date()} {q} — {result}")
    time.sleep(0.1)
