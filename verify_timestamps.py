import json, sys
from datetime import datetime, timezone
sys.stdout.reconfigure(line_buffering=True)

h = json.load(open('data/btc_price_histories.json'))

# Check 15m markets - slug has timestamp
for m in [x for x in h if 'updown-15m' in x['slug']][:5]:
    slug = m['slug']
    ts = int(slug.split('-')[-1])
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    pts = m['history']
    last_t = pts[-1]['t']
    last_dt = datetime.fromtimestamp(last_t, tz=timezone.utc)
    diff_min = (ts - last_t) / 60
    print(f"{slug}")
    print(f"  Slug timestamp: {dt} (unix: {ts})")
    print(f"  Last data point: {last_dt} (unix: {last_t})")
    print(f"  Diff: {diff_min:.1f} min (slug is {diff_min:.0f}min after last datapoint)")
    print()

# Check legacy markets
for m in [x for x in h if 'up-or-down' in x['slug']][:3]:
    pts = m['history']
    first_dt = datetime.fromtimestamp(pts[0]['t'], tz=timezone.utc)
    last_dt = datetime.fromtimestamp(pts[-1]['t'], tz=timezone.utc)
    print(f"{m['slug']}")
    print(f"  {m['question']}")
    print(f"  First: {first_dt}, Last: {last_dt}")
    print(f"  Span: {(pts[-1]['t'] - pts[0]['t'])/60:.0f} min")
    print()
