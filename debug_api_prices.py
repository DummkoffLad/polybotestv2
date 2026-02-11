import json, sys
sys.stdout.reconfigure(line_buffering=True)

h = json.load(open('data/btc_price_histories.json'))

# Look at legacy markets
legacy = [x for x in h if 'up-or-down' in x['slug']]
print(f"Legacy markets: {len(legacy)}")

for m in legacy[:2]:
    pts = m['history']
    slug = m['slug']
    print(f"\n--- {slug} ---")
    print(f"Points: {len(pts)}, span: {(pts[-1]['t']-pts[0]['t'])/60:.0f} min")

    # Show every 20th point
    for i in range(0, len(pts), 20):
        p = pts[i]
        mins = (p['t'] - pts[0]['t']) / 60
        print(f"  min {mins:>6.0f}: ${p['p']:.4f}")

    # Find when price leaves 0.50 area
    for p in pts:
        if abs(p['p'] - 0.50) > 0.05:
            mins = (p['t'] - pts[0]['t']) / 60
            from_end = (pts[-1]['t'] - p['t']) / 60
            print(f"\n  First move from $0.50 at min {mins:.0f} ({from_end:.0f} min before end): ${p['p']:.4f}")
            break

# Now look at 15min markets
m15 = [x for x in h if 'updown-15m' in x['slug']]
print(f"\n\n15-min markets: {len(m15)}")

for m in m15[:3]:
    pts = m['history']
    slug = m['slug']
    print(f"\n--- {slug} ---")
    print(f"Points: {len(pts)}, span: {(pts[-1]['t']-pts[0]['t'])/60:.0f} min")

    # Show every 10th point
    for i in range(0, len(pts), 10):
        p = pts[i]
        mins = (p['t'] - pts[0]['t']) / 60
        print(f"  min {mins:>6.0f}: ${p['p']:.4f}")

    # Find when price leaves 0.50 area
    for p in pts:
        if abs(p['p'] - 0.50) > 0.05:
            mins = (p['t'] - pts[0]['t']) / 60
            from_end = (pts[-1]['t'] - p['t']) / 60
            print(f"\n  First move from $0.50 at min {mins:.0f} ({from_end:.0f} min before end): ${p['p']:.4f}")
            break

# Check: do 15min markets have actual price movement or just flat?
print("\n\n--- 15min market price ranges ---")
for m in m15[:10]:
    pts = m['history']
    prices = [p['p'] for p in pts]
    unique = len(set(f"{p:.4f}" for p in prices))
    print(f"  {m['slug'][:50]:50s} | range: ${min(prices):.4f}-${max(prices):.4f} | unique: {unique} | pts: {len(pts)}")
