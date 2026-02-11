"""Consistency v2: Combine the best discoveries.

Winners from v1:
- boost_5: Sharpe 0.146, $154
- skip_hi85: Sharpe 0.139, $169
- boost_6: Sharpe 0.127, $139
- safe_v2 (b6, mkt40, dd8/15, late1.5x): Sharpe 0.122, smallest DD

Now test combinations.
"""
import sys
import json
import math
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer
import src.strategies.profit_taker.strategy as pt_mod

TRAIN_SESSIONS = ["2026-02-03/05-56", "2026-02-04/02-35", "2026-02-06/05-30"]
TEST_SESSIONS = ["2026-02-05/03-58", "2026-02-05/22-15", "2026-02-07/05-52"]
HOLDOUT_SESSIONS = ["2026-02-08/06-39"]


def get_split(session_name):
    for s in TRAIN_SESSIONS:
        if s in session_name:
            return "TRAIN"
    for s in TEST_SESSIONS:
        if s in session_name:
            return "TEST"
    for s in HOLDOUT_SESSIONS:
        if s in session_name:
            return "HOLDOUT"
    return "UNKNOWN"


def get_utc_hour_from_file(path):
    with open(path, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
                if obj.get("type") in ("leader_trade", "fill"):
                    ts = obj.get("timestamp", "")
                    if "T" in ts:
                        return int(ts.split("T")[1][:2])
            except Exception:
                continue
    return -1


def run_variant(hour_file, capital, budget, boost, skip_low, skip_high, mkt_cap,
                dd_reduce, dd_stop, late_min, late_mult):
    orig = {}
    for attr in ["SCALE_BOOST", "SKIP_PRICE_LOW", "SKIP_PRICE_HIGH", "PER_MARKET_CAP_PCT",
                 "DRAWDOWN_REDUCE_THRESHOLD", "DRAWDOWN_STOP_THRESHOLD",
                 "LATE_ENTRY_BOOST_MIN", "LATE_ENTRY_BOOST_MULT"]:
        orig[attr] = getattr(pt_mod, attr)

    pt_mod.SCALE_BOOST = Decimal(str(boost))
    pt_mod.SKIP_PRICE_LOW = Decimal(str(skip_low))
    pt_mod.SKIP_PRICE_HIGH = Decimal(str(skip_high))
    pt_mod.PER_MARKET_CAP_PCT = Decimal(str(mkt_cap))
    pt_mod.DRAWDOWN_REDUCE_THRESHOLD = Decimal(str(dd_reduce))
    pt_mod.DRAWDOWN_STOP_THRESHOLD = Decimal(str(dd_stop))
    pt_mod.LATE_ENTRY_BOOST_MIN = late_min
    pt_mod.LATE_ENTRY_BOOST_MULT = Decimal(str(late_mult))

    config_overrides = {
        "scaling.our_capital": capital, "scaling.hourly_budget": budget,
        "scaling.k_factor": 1, "scaling.leader_estimated_capital": 900,
    }

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=config_overrides)
    try:
        count = replayer.load()
    except Exception:
        for k, v in orig.items():
            setattr(pt_mod, k, v)
        return None
    if count == 0:
        for k, v in orig.items():
            setattr(pt_mod, k, v)
        return None

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour
        decision = strategy.on_event(event)
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        ps = end_prices.get(token_id)
        if ps and ps.bid is not None:
            last_bid = ps.bid
        else:
            last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
        res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
        dollars = pos.shares * res_price
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars

    pnl = float(strategy.cash) - float(Decimal(str(capital)) * Decimal("2"))
    for k, v in orig.items():
        setattr(pt_mod, k, v)
    return round(pnl, 2)


def analyze(pnls):
    if not pnls:
        return {}
    n = len(pnls)
    mean = sum(pnls) / n
    var = sum((x - mean)**2 for x in pnls) / max(1, n-1)
    std = math.sqrt(var) if var > 0 else 0.001
    sharpe = mean / std
    s = sorted(pnls)
    winning = sum(1 for x in pnls if x > 0)
    # Max drawdown from cumulative
    cum = 0; peak = 0; max_dd = 0
    for p in pnls:
        cum += p; peak = max(peak, cum); max_dd = max(max_dd, peak - cum)
    return {
        "sharpe": round(sharpe, 4), "total": round(sum(pnls), 2),
        "wr": round(winning/n*100, 1), "max_loss": round(s[0], 2),
        "max_dd": round(max_dd, 2), "worst5": s[:5],
    }


def main():
    base = Path("data/sessions")
    all_hours = []
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                all_hours.append((session, utc_h, hf, get_split(session)))

    print(f"Consistency v2 across {len(all_hours)} hours...\n")

    # (name, cap, budget, boost, skip_low, skip_high, mkt_cap, dd_r, dd_s, late_min, late_mult)
    variants = [
        # References
        ("baseline_b8", 50, 45, 8, 0.45, 0.97, 50, 10, 20, 40, 2),
        ("boost_5", 50, 45, 5, 0.45, 0.97, 50, 10, 20, 40, 2),
        ("skip_hi85", 50, 45, 8, 0.45, 0.85, 50, 10, 20, 40, 2),

        # === COMBOS: boost_5 + skip_hi ===
        ("b5+hi90", 50, 45, 5, 0.45, 0.90, 50, 10, 20, 40, 2),
        ("b5+hi85", 50, 45, 5, 0.45, 0.85, 50, 10, 20, 40, 2),
        ("b5+hi80", 50, 45, 5, 0.45, 0.80, 50, 10, 20, 40, 2),

        # === COMBOS: boost_6 + skip_hi ===
        ("b6+hi90", 50, 45, 6, 0.45, 0.90, 50, 10, 20, 40, 2),
        ("b6+hi85", 50, 45, 6, 0.45, 0.85, 50, 10, 20, 40, 2),

        # === COMBOS: boost + skip_hi + DD ===
        ("b5+hi85+dd7/14", 50, 45, 5, 0.45, 0.85, 50, 7, 14, 40, 2),
        ("b5+hi85+dd8/16", 50, 45, 5, 0.45, 0.85, 50, 8, 16, 40, 2),
        ("b5+hi90+dd7/14", 50, 45, 5, 0.45, 0.90, 50, 7, 14, 40, 2),
        ("b6+hi85+dd8/15", 50, 45, 6, 0.45, 0.85, 50, 8, 15, 40, 2),

        # === COMBOS: + late entry variations ===
        ("b5+hi85+noLate", 50, 45, 5, 0.45, 0.85, 50, 10, 20, 60, 1),
        ("b5+hi85+late1.5", 50, 45, 5, 0.45, 0.85, 50, 10, 20, 40, 1.5),
        ("b5+hi85+late3", 50, 45, 5, 0.45, 0.85, 50, 10, 20, 40, 3),

        # === COMBOS: + mkt cap ===
        ("b5+hi85+mkt40", 50, 45, 5, 0.45, 0.85, 40, 10, 20, 40, 2),
        ("b5+hi85+mkt60", 50, 45, 5, 0.45, 0.85, 60, 10, 20, 40, 2),

        # === COMBOS: skip_low variations ===
        ("b5+hi85+sk35", 50, 45, 5, 0.35, 0.85, 50, 10, 20, 40, 2),
        ("b5+hi85+sk50", 50, 45, 5, 0.50, 0.85, 50, 10, 20, 40, 2),

        # === FINAL COMBOS ===
        ("FINAL_v1", 50, 45, 5, 0.45, 0.85, 50, 7, 14, 40, 2),
        ("FINAL_v2", 50, 45, 5, 0.45, 0.85, 50, 10, 20, 40, 1.5),
        ("FINAL_v3", 50, 45, 5, 0.45, 0.90, 50, 8, 16, 40, 2),
        ("FINAL_v4", 50, 45, 6, 0.45, 0.85, 40, 8, 15, 40, 2),
        ("FINAL_v5", 50, 45, 5, 0.45, 0.85, 45, 10, 20, 40, 2),
        ("FINAL_v6", 50, 45, 5, 0.35, 0.90, 50, 10, 20, 40, 2),
    ]

    results = []
    for i, (name, cap, bud, bst, sl, sh, mc, dr, ds, lm, lmult) in enumerate(variants):
        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}
        for _, _, hf, split in all_hours:
            pnl = run_variant(hf, cap, bud, bst, sl, sh, mc, dr, ds, lm, lmult)
            if pnl is not None:
                split_pnl[split].append(pnl)

        all_pnls = split_pnl["TRAIN"] + split_pnl["TEST"] + split_pnl["HOLDOUT"]
        m = analyze(all_pnls)
        train = sum(split_pnl["TRAIN"])
        test = sum(split_pnl["TEST"])
        hold = sum(split_pnl["HOLDOUT"])
        robust = train > 0 and test > 0 and hold > 0
        results.append({
            "name": name, "train": train, "test": test, "holdout": hold,
            "robust": robust, "m": m,
        })
        rob = "YES" if robust else "no"
        w5 = ", ".join(f"${x:+.0f}" for x in m.get("worst5", []))
        print(f"  [{i+1:>2}/{len(variants)}] {name:<20} S={m.get('sharpe',0):+.3f} WR={m.get('wr',0):>4.0f}% ${m.get('total',0):>+7.2f} | Tr${train:>+7.2f} Te${test:>+7.2f} Ho${hold:>+7.2f} | MaxL${m.get('max_loss',0):>+6.1f} DD${m.get('max_dd',0):>5.1f} [{rob}]")

    # Ranking
    print(f"\n{'='*140}")
    print(f"  FINAL RANKING (sorted by Sharpe, robust only)")
    print(f"{'='*140}")
    print(f"  {'#':>2} {'Name':<22} | {'Sharpe':>7} {'WR%':>5} | {'Train$':>8} {'Test$':>8} {'Hold$':>8} {'Total$':>8} | {'MaxLoss':>8} {'MaxDD':>7} | Rob")
    print(f"  {'-'*120}")

    for rank, r in enumerate(sorted([r for r in results if r["robust"]], key=lambda x: -x["m"]["sharpe"]), 1):
        m = r["m"]
        print(f"  {rank:>2} {r['name']:<22} | {m['sharpe']:>+6.3f} {m['wr']:>4.0f}% | ${r['train']:>+7.2f} ${r['test']:>+7.2f} ${r['holdout']:>+7.2f} ${m['total']:>+7.2f} | ${m['max_loss']:>+7.2f} ${m['max_dd']:>6.2f} | YES")

    not_robust = [r for r in results if not r["robust"]]
    if not_robust:
        print(f"\n  NOT ROBUST:")
        for r in sorted(not_robust, key=lambda x: -x["m"]["sharpe"])[:5]:
            m = r["m"]
            print(f"     {r['name']:<22} | {m['sharpe']:>+6.3f} | Tr${r['train']:>+7.2f} Te${r['test']:>+7.2f} Ho${r['holdout']:>+7.2f}")


if __name__ == "__main__":
    main()
