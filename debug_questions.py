"""Check what formats exist at different offsets."""
import requests, json, sys, re
from collections import Counter
sys.stdout.reconfigure(line_buffering=True)

GAMMA_URL = "https://gamma-api.polymarket.com"

formats = Counter()
samples = {}

for offset in range(0, 50000, 2000):
    r = requests.get(f"{GAMMA_URL}/markets", params={
        'limit': 100, 'offset': offset,
        'closed': True, 'order': 'volume24hr', 'ascending': True,
    }, timeout=15)
    if r.status_code != 200 or not r.json():
        break

    for m in r.json():
        q = m.get('question', '')
        slug = m.get('slug', '').lower()
        if 'up-or-down' not in slug and 'up or down' not in q.lower():
            continue
        coins = ['bitcoin', 'btc', 'ethereum', 'eth', 'solana', 'sol', 'dogecoin', 'xrp']
        if not any(c in slug or c in q.lower() for c in coins):
            continue

        # Classify format
        if re.search(r'\d{1,2}:\d{2}(AM|PM)-\d{1,2}:\d{2}(AM|PM)\s*ET', q, re.IGNORECASE):
            fmt = '5min (HH:MM-HH:MM)'
        elif re.search(r'\d{1,2}\s*(AM|PM)\s*ET', q, re.IGNORECASE):
            if ':' in q.split('ET')[0].split(',')[-1]:
                fmt = 'other_time'
            else:
                fmt = '1hour (HAM/PM)'
        else:
            fmt = 'other'

        formats[fmt] += 1
        if fmt not in samples:
            samples[fmt] = q

    if offset % 10000 == 0:
        print(f"  offset={offset}: {dict(formats)}")

print(f"\nTotal formats found:")
for fmt, n in formats.most_common():
    print(f"  {fmt}: {n}")
    print(f"    Example: {samples[fmt][:80]}")
