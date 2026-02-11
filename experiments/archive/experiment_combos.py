"""Combo experiment: test combinations of the top performers from push300.

Top individual performers:
  - budget50: Sharpe 0.389 (+0.007), PnL $275
  - noPriceLo@500: Sharpe 0.341, PnL $307 (only one over $300!)
  - mktcap60: Sharpe 0.356, PnL $274
  - noPriceHi@500: small gains
  - boost tweaks: minor

Goal: Find a combo hitting $300+ with Sharpe >= 0.35
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

TRAIN = ["2026-02-03/05-56", "2026-02-04/02-35", "2026-02-06/05-30"]
TEST = ["2026-02-05/03-58", "2026-02-05/22-15", "2026-02-07/05-52"]
HOLDOUT = ["2026-02-08/06-39"]


def get_split(s):
    for t in TRAIN:
        if t in s: return "TRAIN"
    for t in TEST:
        if t in s: return "TEST"
    for t in HOLDOUT:
        if t in s: return "HOLDOUT"
    return "UNKNOWN"


def get_utc_hour(path):
    with open(path, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
                if obj.get("type") in ("leader_trade", "fill"):
                    ts = obj.get("timestamp", "")
                    if "T" in ts: return int(ts.split("T")[1][:2])
            except: continue
    return -1


def run_variant(hour_file, capital, budget, boost, skip_low, skip_high, mkt_cap,
                dd_reduce, dd_stop, late_min, late_mult, cumulative_min_dollars,
                remove_price_low_at=0, remove_price_high_at=0,
                boost_at_500=0, boost_at_800=0):
    """Run one hour. Manages conviction externally, optional price filter removal & tiered boost."""
    orig = {}
    for attr in ["SCALE_BOOST", "SKIP_PRICE_LOW", "SKIP_PRICE_HIGH", "PER_MARKET_CAP_PCT",
                 "DRAWDOWN_REDUCE_THRESHOLD", "DRAWDOWN_STOP_THRESHOLD",
                 "LATE_ENTRY_BOOST_MIN", "LATE_ENTRY_BOOST_MULT",
                 "CUMULATIVE_MIN_LEADER_DOLLARS"]:
        orig[attr] = getattr(pt_mod, attr)

    pt_mod.SCALE_BOOST = Decimal(str(boost))
    pt_mod.SKIP_PRICE_LOW = Decimal(str(skip_low))
    pt_mod.SKIP_PRICE_HIGH = Decimal(str(skip_high))
    pt_mod.PER_MARKET_CAP_PCT = Decimal(str(mkt_cap))
    pt_mod.DRAWDOWN_REDUCE_THRESHOLD = Decimal(str(dd_reduce))
    pt_mod.DRAWDOWN_STOP_THRESHOLD = Decimal(str(dd_stop))
    pt_mod.LATE_ENTRY_BOOST_MIN = late_min
    pt_mod.LATE_ENTRY_BOOST_MULT = Decimal(str(late_mult))
    pt_mod.CUMULATIVE_MIN_LEADER_DOLLARS = Decimal("0")  # Disabled — handled externally

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

    token_spend = defaultdict(Decimal)
    base_boost = strategy.scale_boost

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour

        trade = event.trade

        if trade.action.value == "BUY":
            token_spend[trade.token_id] += trade.dollars
            cum = float(token_spend[trade.token_id])

            # Check conviction
            if cum < cumulative_min_dollars:
                continue

            # Override price filters for very high conviction
            orig_skip_high = pt_mod.SKIP_PRICE_HIGH
            orig_skip_low = pt_mod.SKIP_PRICE_LOW
            if remove_price_high_at > 0 and cum >= remove_price_high_at:
                pt_mod.SKIP_PRICE_HIGH = Decimal("0.97")
            if remove_price_low_at > 0 and cum >= remove_price_low_at:
                pt_mod.SKIP_PRICE_LOW = Decimal("0.10")

            # Tiered boost
            effective_boost = base_boost
            if boost_at_800 > 0 and cum >= 800:
                effective_boost = base_boost * Decimal(str(boost_at_800))
            elif boost_at_500 > 0 and cum >= 500:
                effective_boost = base_boost * Decimal(str(boost_at_500))

            strategy.scale_boost = effective_boost
            decision = strategy.on_event(event)
            strategy.scale_boost = base_boost
            pt_mod.SKIP_PRICE_HIGH = orig_skip_high
            pt_mod.SKIP_PRICE_LOW = orig_skip_low

            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)
        else:
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
    cum = 0; peak = 0; max_dd = 0
    for p in pnls:
        cum += p; peak = max(peak, cum); max_dd = max(max_dd, peak - cum)
    return {
        "sharpe": round(sharpe, 4), "total": round(sum(pnls), 2),
        "wr": round(winning/n*100, 1), "max_loss": round(s[0], 2),
        "max_dd": round(max_dd, 2),
    }


def main():
    base = Path("data/sessions")
    all_hours = []
    for dd in sorted(base.iterdir()):
        if not dd.is_dir(): continue
        for hf in sorted(dd.glob("*_hour_*.jsonl")):
            h = get_utc_hour(hf)
            if h >= 0:
                s = dd.name + "/" + hf.stem.split("_hour_")[0]
                all_hours.append((s, h, hf, get_split(s)))

    print(f"Combo experiment across {len(all_hours)} hours...\n")

    BASE = dict(capital=50, budget=45, boost=5, skip_low=0.45, skip_high=0.85,
                mkt_cap=50, dd_reduce=12, dd_stop=24, late_min=40, late_mult=3,
                cumulative_min_dollars=300)

    variants = [
        # Reference
        ("baseline", {}),

        # === TOP INDIVIDUAL WINNERS (for comparison) ===
        ("budget50", {"budget": 50}),
        ("noPriceLo@500", {"remove_price_low_at": 500}),
        ("mktcap60", {"mkt_cap": 60}),

        # === COMBOS OF TOP 3 ===
        ("bgt50+noLo500", {"budget": 50, "remove_price_low_at": 500}),
        ("bgt50+mkt60", {"budget": 50, "mkt_cap": 60}),
        ("noLo500+mkt60", {"remove_price_low_at": 500, "mkt_cap": 60}),
        ("bgt50+noLo500+mkt60", {"budget": 50, "remove_price_low_at": 500, "mkt_cap": 60}),

        # === ALSO REMOVE PRICE HIGH ===
        ("noLo500+noHi500", {"remove_price_low_at": 500, "remove_price_high_at": 500}),
        ("bgt50+noBoth500", {"budget": 50, "remove_price_low_at": 500, "remove_price_high_at": 500}),
        ("bgt50+noBoth+mkt60", {"budget": 50, "remove_price_low_at": 500, "remove_price_high_at": 500, "mkt_cap": 60}),

        # === LOWER CONVICTION THRESHOLD FOR PRICE REMOVAL ===
        ("noLo400", {"remove_price_low_at": 400}),
        ("noLo450", {"remove_price_low_at": 450}),
        ("noLo350", {"remove_price_low_at": 350}),
        ("bgt50+noLo400", {"budget": 50, "remove_price_low_at": 400}),
        ("bgt50+noLo450", {"budget": 50, "remove_price_low_at": 450}),

        # === BOOST TWEAKS ===
        ("boost6", {"boost": 6}),
        ("boost7", {"boost": 7}),
        ("boost6+noLo500", {"boost": 6, "remove_price_low_at": 500}),
        ("boost7+noLo500", {"boost": 7, "remove_price_low_at": 500}),
        ("boost6+bgt50+noLo500", {"boost": 6, "budget": 50, "remove_price_low_at": 500}),

        # === LATE MULT TWEAKS ===
        ("late4", {"late_mult": 4}),
        ("late5", {"late_mult": 5}),
        ("late4+noLo500", {"late_mult": 4, "remove_price_low_at": 500}),
        ("late4+bgt50", {"late_mult": 4, "budget": 50}),
        ("late4+bgt50+noLo500", {"late_mult": 4, "budget": 50, "remove_price_low_at": 500}),

        # === DD TWEAKS + COMBOS ===
        ("dd10/20+noLo500", {"dd_reduce": 10, "dd_stop": 20, "remove_price_low_at": 500}),
        ("dd15/30+noLo500", {"dd_reduce": 15, "dd_stop": 30, "remove_price_low_at": 500}),
        ("dd15/30+bgt50", {"dd_reduce": 15, "dd_stop": 30, "budget": 50}),

        # === TIERED BOOST + COMBOS ===
        ("t500=1.3+noLo500", {"boost_at_500": 1.3, "remove_price_low_at": 500}),
        ("t500=1.3+bgt50+noLo500", {"boost_at_500": 1.3, "budget": 50, "remove_price_low_at": 500}),

        # === CONVICTION THRESHOLD TWEAKS ===
        ("cum250", {"cumulative_min_dollars": 250}),
        ("cum350", {"cumulative_min_dollars": 350}),
        ("cum250+noLo400", {"cumulative_min_dollars": 250, "remove_price_low_at": 400}),

        # === KITCHEN SINK: best combo candidates ===
        ("bgt50+noLo500+boost6", {"budget": 50, "remove_price_low_at": 500, "boost": 6}),
        ("bgt50+noLo500+late4", {"budget": 50, "remove_price_low_at": 500, "late_mult": 4}),
        ("bgt50+noLo450+mkt60", {"budget": 50, "remove_price_low_at": 450, "mkt_cap": 60}),
        ("bgt50+noLo400+mkt60+late4", {"budget": 50, "remove_price_low_at": 400, "mkt_cap": 60, "late_mult": 4}),
    ]

    results = []
    for i, (name, params) in enumerate(variants):
        run_params = {**BASE}
        extra = {}
        for k, v in params.items():
            if k in BASE:
                run_params[k] = v
            else:
                extra[k] = v

        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}
        for _, _, hf, split in all_hours:
            pnl = run_variant(hf, **run_params, **extra)
            if pnl is not None:
                split_pnl[split].append(pnl)

        all_pnls = split_pnl["TRAIN"] + split_pnl["TEST"] + split_pnl["HOLDOUT"]
        m = analyze(all_pnls)
        train = round(sum(split_pnl["TRAIN"]), 2)
        test = round(sum(split_pnl["TEST"]), 2)
        hold = round(sum(split_pnl["HOLDOUT"]), 2)
        robust = train > 0 and test > 0 and hold > 0
        results.append({"name": name, "train": train, "test": test, "holdout": hold,
                        "robust": robust, "m": m})
        rob = "YES" if robust else "no"
        print(f"  [{i+1:>2}/{len(variants)}] {name:<30} S={m.get('sharpe',0):+.3f} ${m.get('total',0):>+7.2f} WR={m.get('wr',0):>4.0f}% | Tr={train:>+7.2f} Te={test:>+7.2f} Ho={hold:>+7.2f} | ML={m.get('max_loss',0):>+6.1f} DD={m.get('max_dd',0):>5.1f} [{rob}]")

    # Ranking
    print(f"\n{'='*150}")
    print(f"  RANKING (robust only, sorted by Sharpe)")
    print(f"{'='*150}")
    baseline = next((r for r in results if r["name"] == "baseline"), None)
    bs = baseline["m"]["sharpe"] if baseline else 0
    bt = baseline["m"]["total"] if baseline else 0

    robust_results = [r for r in results if r["robust"]]
    for rank, r in enumerate(sorted(robust_results, key=lambda x: -x["m"]["sharpe"]), 1):
        m = r["m"]
        ds = m["sharpe"] - bs
        dt = m["total"] - bt
        marker = " *** TARGET ***" if m["total"] >= 300 and m["sharpe"] >= 0.35 else ""
        marker2 = " $$$" if m["total"] >= 300 else ""
        print(f"  {rank:>2} {r['name']:<30} S={m['sharpe']:>+.3f} ${m['total']:>+7.2f} WR={m['wr']:>4.0f}% | Tr={r['train']:>+7.2f} Te={r['test']:>+7.2f} Ho={r['holdout']:>+7.2f} | dS={ds:>+.3f} d$={dt:>+.1f} ML={m['max_loss']:>+6.1f}{marker}{marker2}")

    not_robust = [r for r in results if not r["robust"]]
    if not_robust:
        print(f"\n  NOT ROBUST:")
        for r in sorted(not_robust, key=lambda x: -x["m"].get("sharpe", 0)):
            m = r["m"]
            print(f"     {r['name']:<30} S={m.get('sharpe',0):>+.3f} ${m.get('total',0):>+7.2f} | Tr={r['train']:>+7.2f} Te={r['test']:>+7.2f} Ho={r['holdout']:>+7.2f}")


if __name__ == "__main__":
    main()
