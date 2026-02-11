import requests, json, sys
sys.stdout.reconfigure(line_buffering=True)

h = json.load(open('data/btc_price_histories.json'))
btc_cache = json.load(open('data/btc_markets_cache.json'))

# Get a 15m market
m15 = [x for x in h if 'updown-15m' in x['slug']][0]
target_slug = m15['slug']
token_id = None
for m in btc_cache:
    if m.get('slug') == target_slug:
        tokens = json.loads(m.get('clobTokenIds', '[]'))
        token_id = tokens[0] if tokens else None
        break

print(f"Market: {target_slug}")
r = requests.get('https://clob.polymarket.com/prices-history', params={
    'market': token_id, 'interval': '1d', 'fidelity': 1,
}, timeout=10)
data = r.json().get('history', [])
print(f"Points: {len(data)}")

# Show the data when price actually changes
prev_p = None
for pt in data:
    mins_from_start = (pt['t'] - data[0]['t']) / 60
    mins_from_end = (data[-1]['t'] - pt['t']) / 60
    if prev_p is None or abs(pt['p'] - prev_p) > 0.001:
        print(f"  min {mins_from_start:>6.0f} (end-{mins_from_end:>5.0f}): ${pt['p']:.4f}")
    prev_p = pt['p']

# Now try a legacy market with interval=1d
legacy = [x for x in h if 'up-or-down' in x['slug']]
target_slug2 = legacy[0]['slug']
token_id2 = None
for m in btc_cache:
    if m.get('slug') == target_slug2:
        tokens = json.loads(m.get('clobTokenIds', '[]'))
        token_id2 = tokens[0] if tokens else None
        break

if token_id2:
    print(f"\n\nLegacy: {target_slug2}")
    r2 = requests.get('https://clob.polymarket.com/prices-history', params={
        'market': token_id2, 'interval': '1d', 'fidelity': 1,
    }, timeout=10)
    data2 = r2.json().get('history', [])
    print(f"Points: {len(data2)}")
    prev_p = None
    for pt in data2:
        mins_from_start = (pt['t'] - data2[0]['t']) / 60
        mins_from_end = (data2[-1]['t'] - pt['t']) / 60
        if prev_p is None or abs(pt['p'] - prev_p) > 0.001:
            print(f"  min {mins_from_start:>6.0f} (end-{mins_from_end:>5.0f}): ${pt['p']:.4f}")
        prev_p = pt['p']
