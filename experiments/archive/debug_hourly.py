import json, sys
from datetime import datetime, timezone, timedelta
sys.stdout.reconfigure(line_buffering=True)

histories = json.load(open('data/hourly_hd_histories.json'))

for h in histories[:10]:
    pts = h['history']
    hour_info = h.get('hour_info', {})
    start_utc = hour_info.get('start_utc', 0)
    end_utc = hour_info.get('end_utc', 0)

    data_start = pts[0]['t']
    data_end = pts[-1]['t']

    print(f"\n{h['question']}")
    print(f"  Slug: {h['slug']}")
    print(f"  Parsed window: {datetime.fromtimestamp(start_utc, tz=timezone.utc)} to {datetime.fromtimestamp(end_utc, tz=timezone.utc)}")
    print(f"  Data range:    {datetime.fromtimestamp(data_start, tz=timezone.utc)} to {datetime.fromtimestamp(data_end, tz=timezone.utc)}")
    print(f"  Data span: {(data_end - data_start)/3600:.1f}h, {len(pts)} points")
    print(f"  Window start vs data start: {(start_utc - data_start)/3600:.1f}h gap")
    print(f"  Window start vs data end:   {(start_utc - data_end)/3600:.1f}h gap")

    # Check if any data points fall in the parsed window
    in_window = sum(1 for p in pts if start_utc - 120 <= p['t'] <= end_utc + 120)
    print(f"  Data points in window: {in_window}")

    # Show what the data actually looks like near the parsed window time
    # Find closest point to window start
    diffs = [(abs(p['t'] - start_utc), p) for p in pts]
    diffs.sort()
    closest = diffs[0][1]
    print(f"  Closest point to window start: {datetime.fromtimestamp(closest['t'], tz=timezone.utc)} price=${closest['p']:.4f} (off by {diffs[0][0]/60:.0f}min)")
