"""Quick test: try different intervals for hourly markets."""
import json, sys, requests
sys.stdout.reconfigure(line_buffering=True)

cache = json.load(open('data/api_market_cache.json'))
# Find a bitcoin-up-or-down market
hourly = [m for m in cache if 'bitcoin-up-or-down' in m.get('slug', '') and 'AM' in m.get('question', '')]

if not hourly:
    print("No hourly markets found!")
    sys.exit(1)

m = hourly[0]
print(f"Market: {m['question']}")
print(f"Slug: {m['slug']}")

tokens = json.loads(m.get('clobTokenIds', '[]'))
if len(tokens) < 2:
    print("No tokens!")
    sys.exit(1)

token = tokens[0]
print(f"Token: {token[:30]}...")

for interval in ['1h', '6h', '1d', '1w', '1m', 'max', 'all']:
    try:
        r = requests.get('https://clob.polymarket.com/prices-history', params={
            'market': token, 'interval': interval, 'fidelity': 1
        }, timeout=10)
        if r.status_code == 200:
            data = r.json().get('history', [])
            if data:
                span = (data[-1]['t'] - data[0]['t']) / 3600
                prices = [p['p'] for p in data]
                unique = len(set(f"{p:.4f}" for p in prices))
                print(f"  interval={interval:>4s}: {len(data):>5} pts, span={span:>6.1f}h, unique={unique:>3}")
            else:
                print(f"  interval={interval:>4s}: empty")
        else:
            print(f"  interval={interval:>4s}: HTTP {r.status_code}")
    except Exception as e:
        print(f"  interval={interval:>4s}: error {e}")

# Also try the second token
print(f"\nToken 2: {tokens[1][:30]}...")
for interval in ['1d', 'max']:
    try:
        r = requests.get('https://clob.polymarket.com/prices-history', params={
            'market': tokens[1], 'interval': interval, 'fidelity': 1
        }, timeout=10)
        if r.status_code == 200:
            data = r.json().get('history', [])
            if data:
                span = (data[-1]['t'] - data[0]['t']) / 3600
                prices = [p['p'] for p in data]
                unique = len(set(f"{p:.4f}" for p in prices))
                print(f"  interval={interval:>4s}: {len(data):>5} pts, span={span:>6.1f}h, unique={unique:>3}")
            else:
                print(f"  interval={interval:>4s}: empty")
        else:
            print(f"  interval={interval:>4s}: HTTP {r.status_code}")
    except Exception as e:
        print(f"  interval={interval:>4s}: error {e}")
