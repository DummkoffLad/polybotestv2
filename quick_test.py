"""Quick test of final combos."""
import sys
from pathlib import Path
sys.path.insert(0, '.')
from experiment_final_tune import run_variant, analyze, get_split, get_utc_hour_from_file

base = Path('data/sessions')
all_hours = []
for dd in sorted(base.iterdir()):
    if not dd.is_dir(): continue
    for hf in sorted(dd.glob('*_hour_*.jsonl')):
        h = get_utc_hour_from_file(hf)
        if h >= 0:
            s = dd.name + '/' + hf.stem.split('_hour_')[0]
            all_hours.append((s, h, hf, get_split(s)))

combos = [
    ('cum300+dd12/24+late5x', dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=12, dd_stop=24, late_min=40, late_mult=5, cumulative_min_dollars=300)),
    ('cum300+dd12/24+b4+late5x', dict(boost=4, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=12, dd_stop=24, late_min=40, late_mult=5, cumulative_min_dollars=300)),
    ('cum300+dd12/24+late4x', dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=12, dd_stop=24, late_min=40, late_mult=4, cumulative_min_dollars=300)),
    ('cum350+dd12/24', dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=12, dd_stop=24, late_min=40, late_mult=3, cumulative_min_dollars=350)),
    ('cum300+dd15/30', dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=15, dd_stop=30, late_min=40, late_mult=3, cumulative_min_dollars=300)),
    ('cum350+dd12/24+late5x', dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=12, dd_stop=24, late_min=40, late_mult=5, cumulative_min_dollars=350)),
]

for name, params in combos:
    sp = {'TRAIN': [], 'TEST': [], 'HOLDOUT': []}
    for _, _, hf, split in all_hours:
        pnl = run_variant(hf, capital=50, budget=45, **params)
        if pnl is not None:
            sp[split].append(pnl)
    ap = sp['TRAIN'] + sp['TEST'] + sp['HOLDOUT']
    m = analyze(ap)
    tr = round(sum(sp['TRAIN']), 2)
    te = round(sum(sp['TEST']), 2)
    ho = round(sum(sp['HOLDOUT']), 2)
    rob = 'YES' if tr > 0 and te > 0 and ho > 0 else 'no'
    s = m['sharpe']
    t = m['total']
    ml = m['max_loss']
    dd = m['max_dd']
    print(f"  {name:<30} S={s:+.3f} ${t:>+7.2f} | Tr${tr:>+7.2f} Te${te:>+7.2f} Ho${ho:>+7.2f} | ML${ml:>+6.1f} DD${dd:>5.1f} [{rob}]")
