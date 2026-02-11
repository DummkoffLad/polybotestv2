"""Check what date fields are available on market objects."""
import json, sys
sys.stdout.reconfigure(line_buffering=True)

markets = json.load(open('data/all_crypto_updown_markets.json'))
print(f"Total: {len(markets)}")

# Check a recent and old market
for idx in [0, 1000, 5000, 10000, 20000, 30000]:
    if idx >= len(markets):
        continue
    m = markets[idx]
    print(f"\n=== [{idx}] {m.get('question', '')[:80]} ===")
    # Print all keys that look date-related
    for key in sorted(m.keys()):
        val = m.get(key)
        if val is None:
            continue
        if any(d in key.lower() for d in ['date', 'time', 'created', 'end', 'resol', 'close', 'start']):
            print(f"  {key}: {str(val)[:100]}")
