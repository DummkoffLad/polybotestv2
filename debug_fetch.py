"""Debug why histories come back empty for recent markets."""
import json, sys, requests, time
sys.stdout.reconfigure(line_buffering=True)

markets = json.load(open('data/all_crypto_updown_markets.json'))
print(f"Total markets: {len(markets)}")

# Sort by resolution time, most recent first
markets.sort(key=lambda m: m.get('resolution_start_utc', 0), reverse=True)

# Check first 10 markets
for i, m in enumerate(markets[:10]):
    q = m.get('question', '')
    fmt = m.get('format', '?')
    start_utc = m.get('resolution_start_utc', 0)
    tokens_str = m.get('clobTokenIds', '[]')
    try:
        tokens = json.loads(tokens_str)
    except:
        tokens = []

    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(start_utc, tz=timezone.utc)
    print(f"\n[{i}] {q[:80]}")
    print(f"    fmt={fmt} start={dt} tokens={len(tokens)}")

    if not tokens:
        print(f"    NO TOKENS")
        continue

    # Try fetching history
    tid = tokens[0]
    for interval in ['1d', 'max']:
        try:
            r = requests.get('https://clob.polymarket.com/prices-history', params={
                'market': tid, 'interval': interval, 'fidelity': 1,
            }, timeout=10)
            if r.status_code == 200:
                h = r.json().get('history', [])
                if h:
                    span_h = (h[-1]['t'] - h[0]['t']) / 3600
                    # How many pts in active window?
                    active = [p for p in h if start_utc - 60 <= p['t'] <= m.get('resolution_end_utc', start_utc + 3600) + 60]
                    print(f"    interval={interval}: {len(h)} pts, span={span_h:.1f}h, active_window={len(active)} pts")
                    if h:
                        data_start = datetime.fromtimestamp(h[0]['t'], tz=timezone.utc)
                        data_end = datetime.fromtimestamp(h[-1]['t'], tz=timezone.utc)
                        print(f"    data: {data_start} to {data_end}")
                    if interval == '1d' and len(h) > 0:
                        break  # got good data
                else:
                    print(f"    interval={interval}: empty")
            else:
                print(f"    interval={interval}: HTTP {r.status_code}")
        except Exception as e:
            print(f"    interval={interval}: error {e}")
        time.sleep(0.1)

# Also check some OLDER markets (the ones that worked before)
print("\n\n=== OLDER MARKETS (offset 1000-1010) ===")
for i, m in enumerate(markets[1000:1010]):
    q = m.get('question', '')
    fmt = m.get('format', '?')
    start_utc = m.get('resolution_start_utc', 0)
    tokens_str = m.get('clobTokenIds', '[]')
    try:
        tokens = json.loads(tokens_str)
    except:
        tokens = []

    dt = datetime.fromtimestamp(start_utc, tz=timezone.utc)
    print(f"\n[{1000+i}] {q[:80]}")
    print(f"    fmt={fmt} start={dt} tokens={len(tokens)}")

    if not tokens:
        continue

    tid = tokens[0]
    for interval in ['1d', 'max']:
        try:
            r = requests.get('https://clob.polymarket.com/prices-history', params={
                'market': tid, 'interval': interval, 'fidelity': 1,
            }, timeout=10)
            if r.status_code == 200:
                h = r.json().get('history', [])
                if h:
                    span_h = (h[-1]['t'] - h[0]['t']) / 3600
                    active = [p for p in h if start_utc - 60 <= p['t'] <= m.get('resolution_end_utc', start_utc + 3600) + 60]
                    print(f"    interval={interval}: {len(h)} pts, span={span_h:.1f}h, active_window={len(active)} pts")
                    data_start = datetime.fromtimestamp(h[0]['t'], tz=timezone.utc)
                    data_end = datetime.fromtimestamp(h[-1]['t'], tz=timezone.utc)
                    print(f"    data: {data_start} to {data_end}")
                    if interval == '1d' and len(h) > 0:
                        break
                else:
                    print(f"    interval={interval}: empty")
            else:
                print(f"    interval={interval}: HTTP {r.status_code}")
        except Exception as e:
            print(f"    interval={interval}: error {e}")
        time.sleep(0.1)
