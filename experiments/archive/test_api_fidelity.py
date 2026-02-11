import requests, json, sys
sys.stdout.reconfigure(line_buffering=True)

h = json.load(open('data/btc_price_histories.json'))
m15 = [x for x in h if 'updown-15m' in x['slug']][0]
btc_cache = json.load(open('data/btc_markets_cache.json'))

target_slug = m15['slug']
token_id = None
for m in btc_cache:
    if m.get('slug') == target_slug:
        tokens = json.loads(m.get('clobTokenIds', '[]'))
        token_id = tokens[0] if tokens else None
        break

if not token_id:
    print('Token not found')
    sys.exit(1)

print(f"Market: {target_slug}")
print(f"Token: {token_id}")

for interval in ['1h', '6h', '1d', 'max']:
    for fidelity in [1, 5, 10]:
        try:
            r = requests.get('https://clob.polymarket.com/prices-history', params={
                'market': token_id,
                'interval': interval,
                'fidelity': fidelity,
            }, timeout=10)
            if r.status_code == 200:
                data = r.json().get('history', [])
                if data:
                    prices = [p['p'] for p in data]
                    span = (data[-1]['t'] - data[0]['t']) / 60 if len(data) > 1 else 0
                    unique = len(set(f"{p:.4f}" for p in prices))
                    print(f"  interval={interval:>4s} fidelity={fidelity}: {len(data):>4} pts, span={span:>6.0f}min, unique={unique:>3}, range={min(prices):.4f}-{max(prices):.4f}")
                else:
                    print(f"  interval={interval:>4s} fidelity={fidelity}: empty")
            else:
                print(f"  interval={interval:>4s} fidelity={fidelity}: HTTP {r.status_code}")
        except Exception as e:
            print(f"  interval={interval:>4s} fidelity={fidelity}: error {e}")
