"""Push for $300+: Test ideas to squeeze more profit while keeping consistency.

Current best: cum$300+dd12/24 -> Sharpe 0.382, PnL $266, MaxLoss -$26
Target: $300+ with Sharpe >= 0.35

Ideas:
1. Conviction-tiered sizing: bigger positions when leader spends $500+/$800+
2. First-entry boost: 2x on the very first buy after conviction threshold
3. Lower threshold + price filter: $200 conviction is enough if ask >= $0.55
4. Time-adaptive threshold: lower to $200 after minute 30
5. Budget/capital optimization: raise budget since we barely use it
6. Remove price filters for high-conviction: if leader spent $500+, buy even at 0.40 or 0.90
7. Conviction velocity: leader spending $300 in 10 min vs 40 min — different signal?
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
                # New params
                tiered_boost_500=0,     # Extra boost multiplier when leader spent $500+
                tiered_boost_800=0,     # Extra boost multiplier when leader spent $800+
                first_entry_mult=1,     # Multiplier for first buy after conviction
                low_threshold_high_ask=0,  # Lower conviction threshold when ask >= this price
                low_threshold_value=0,  # The lower threshold to use
                time_adaptive_min=0,    # After this minute, use lower threshold
                time_adaptive_value=0,  # The lower threshold after time_adaptive_min
                remove_price_high_at=0, # Remove SKIP_PRICE_HIGH when conviction >= this
                remove_price_low_at=0,  # Remove SKIP_PRICE_LOW when conviction >= this
                conviction_velocity_min=0,  # Min conviction $ per minute for fast-conviction boost
                conviction_velocity_mult=1, # Boost when conviction velocity is high
                ):
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
    pt_mod.CUMULATIVE_MIN_LEADER_DOLLARS = Decimal("0")  # Disabled — we handle conviction externally

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

    # Conviction tracking
    token_buys = defaultdict(lambda: {"dollars": Decimal("0"), "first_min": 99, "entered": False})
    base_boost = strategy.scale_boost

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour

        trade = event.trade
        minute = trade.timestamp.minute

        if trade.action.value == "BUY":
            tb = token_buys[trade.token_id]
            tb["dollars"] += trade.dollars
            if tb["first_min"] == 99:
                tb["first_min"] = minute

            cum = float(tb["dollars"])

            # Determine effective conviction threshold
            threshold = cumulative_min_dollars

            # Time-adaptive: lower threshold after certain minute
            if time_adaptive_min > 0 and minute >= time_adaptive_min:
                threshold = min(threshold, time_adaptive_value)

            # Price-adaptive: lower threshold when ask is high
            ask = event.prices.ask if event.prices and event.prices.ask else None
            if low_threshold_high_ask > 0 and ask and float(ask) >= low_threshold_high_ask:
                threshold = min(threshold, low_threshold_value)

            # Check conviction — skip entirely (don't call on_event)
            if cum < threshold:
                continue

            # Conviction met! Determine boost
            effective_boost = base_boost

            # Tiered boost based on conviction level
            if tiered_boost_800 > 0 and cum >= 800:
                effective_boost = base_boost * Decimal(str(tiered_boost_800))
            elif tiered_boost_500 > 0 and cum >= 500:
                effective_boost = base_boost * Decimal(str(tiered_boost_500))

            # First entry boost
            if first_entry_mult > 1 and not tb["entered"]:
                effective_boost = effective_boost * Decimal(str(first_entry_mult))
                tb["entered"] = True
            elif first_entry_mult > 1:
                tb["entered"] = True

            # Conviction velocity boost
            if conviction_velocity_min > 0 and tb["first_min"] < minute:
                velocity = cum / max(1, minute - tb["first_min"])
                if velocity >= conviction_velocity_min:
                    effective_boost = effective_boost * Decimal(str(conviction_velocity_mult))

            # Override price filters for very high conviction
            orig_skip_high = pt_mod.SKIP_PRICE_HIGH
            orig_skip_low = pt_mod.SKIP_PRICE_LOW
            if remove_price_high_at > 0 and cum >= remove_price_high_at:
                pt_mod.SKIP_PRICE_HIGH = Decimal("0.97")
            if remove_price_low_at > 0 and cum >= remove_price_low_at:
                pt_mod.SKIP_PRICE_LOW = Decimal("0.10")

            strategy.scale_boost = effective_boost
            decision = strategy.on_event(event)
            strategy.scale_boost = base_boost
            pt_mod.SKIP_PRICE_HIGH = orig_skip_high
            pt_mod.SKIP_PRICE_LOW = orig_skip_low

            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)
        else:
            # SELL
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

    print(f"Push for $300 across {len(all_hours)} hours...\n")

    BASE = dict(capital=50, budget=45, boost=5, skip_low=0.45, skip_high=0.85,
                mkt_cap=50, dd_reduce=12, dd_stop=24, late_min=40, late_mult=3,
                cumulative_min_dollars=300)

    variants = [
        # Reference
        ("baseline", {}),

        # === IDEA 1: TIERED SIZING (bigger positions on higher conviction) ===
        ("tier_500=1.5x", {"tiered_boost_500": 1.5}),
        ("tier_500=2x", {"tiered_boost_500": 2}),
        ("tier_800=2x", {"tiered_boost_800": 2}),
        ("tier_500=1.5+800=2x", {"tiered_boost_500": 1.5, "tiered_boost_800": 2}),
        ("tier_500=1.3+800=1.8", {"tiered_boost_500": 1.3, "tiered_boost_800": 1.8}),

        # === IDEA 2: FIRST ENTRY BOOST ===
        ("first_1.5x", {"first_entry_mult": 1.5}),
        ("first_2x", {"first_entry_mult": 2}),
        ("first_1.5+t500=1.5", {"first_entry_mult": 1.5, "tiered_boost_500": 1.5}),

        # === IDEA 3: LOWER THRESHOLD WHEN ASK IS HIGH ===
        ("ask55+_thr200", {"low_threshold_high_ask": 0.55, "low_threshold_value": 200}),
        ("ask60+_thr200", {"low_threshold_high_ask": 0.60, "low_threshold_value": 200}),
        ("ask65+_thr150", {"low_threshold_high_ask": 0.65, "low_threshold_value": 150}),
        ("ask55+_thr250", {"low_threshold_high_ask": 0.55, "low_threshold_value": 250}),

        # === IDEA 4: TIME-ADAPTIVE THRESHOLD ===
        ("min30_thr200", {"time_adaptive_min": 30, "time_adaptive_value": 200}),
        ("min35_thr200", {"time_adaptive_min": 35, "time_adaptive_value": 200}),
        ("min40_thr150", {"time_adaptive_min": 40, "time_adaptive_value": 150}),
        ("min25_thr250", {"time_adaptive_min": 25, "time_adaptive_value": 250}),

        # === IDEA 5: REMOVE PRICE FILTERS AT HIGH CONVICTION ===
        ("noPriceHi@500", {"remove_price_high_at": 500}),
        ("noPriceLo@500", {"remove_price_low_at": 500}),
        ("noBoth@500", {"remove_price_high_at": 500, "remove_price_low_at": 500}),
        ("noPriceHi@300", {"remove_price_high_at": 300}),

        # === IDEA 6: CONVICTION VELOCITY ===
        ("vel>15/min_1.5x", {"conviction_velocity_min": 15, "conviction_velocity_mult": 1.5}),
        ("vel>20/min_1.5x", {"conviction_velocity_min": 20, "conviction_velocity_mult": 1.5}),
        ("vel>10/min_2x", {"conviction_velocity_min": 10, "conviction_velocity_mult": 2}),

        # === IDEA 7: BUDGET/CAPITAL TWEAKS ===
        ("budget50", {"budget": 50}),
        ("mktcap60", {"mkt_cap": 60}),
        ("mktcap70", {"mkt_cap": 70}),

        # === COMBOS ===
        ("t500=1.5+ask55_200", {"tiered_boost_500": 1.5, "low_threshold_high_ask": 0.55, "low_threshold_value": 200}),
        ("t500=1.5+min35_200", {"tiered_boost_500": 1.5, "time_adaptive_min": 35, "time_adaptive_value": 200}),
        ("first1.5+ask55_200", {"first_entry_mult": 1.5, "low_threshold_high_ask": 0.55, "low_threshold_value": 200}),
        ("first1.5+noPrHi500", {"first_entry_mult": 1.5, "remove_price_high_at": 500}),
        ("t500=1.5+mkt60", {"tiered_boost_500": 1.5, "mkt_cap": 60}),
        ("ask55_200+min35_200", {"low_threshold_high_ask": 0.55, "low_threshold_value": 200,
                                  "time_adaptive_min": 35, "time_adaptive_value": 200}),
    ]

    results = []
    for i, (name, params) in enumerate(variants):
        # Merge params with base
        run_params = {**BASE}
        for k, v in params.items():
            if k in BASE:
                run_params[k] = v

        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}
        for _, _, hf, split in all_hours:
            extra = {k: v for k, v in params.items() if k not in BASE}
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
        print(f"  [{i+1:>2}/{len(variants)}] {name:<25} S={m.get('sharpe',0):+.3f} ${m.get('total',0):>+7.2f} WR={m.get('wr',0):>4.0f}% | Tr={train:>+7.2f} Te={test:>+7.2f} Ho={hold:>+7.2f} | ML={m.get('max_loss',0):>+6.1f} DD={m.get('max_dd',0):>5.1f} [{rob}]")

    # Ranking
    print(f"\n{'='*140}")
    print(f"  RANKING (robust only, sorted by Sharpe)")
    print(f"{'='*140}")
    baseline = next((r for r in results if r["name"] == "baseline"), None)
    bs = baseline["m"]["sharpe"] if baseline else 0
    bt = baseline["m"]["total"] if baseline else 0

    robust_results = [r for r in results if r["robust"]]
    for rank, r in enumerate(sorted(robust_results, key=lambda x: -x["m"]["sharpe"]), 1):
        m = r["m"]
        ds = m["sharpe"] - bs
        dt = m["total"] - bt
        marker = " ***" if m["total"] >= 300 and m["sharpe"] >= 0.35 else ""
        marker2 = " $$$" if m["total"] >= 300 else ""
        print(f"  {rank:>2} {r['name']:<25} S={m['sharpe']:>+.3f} ${m['total']:>+7.2f} WR={m['wr']:>4.0f}% | Tr={r['train']:>+7.2f} Te={r['test']:>+7.2f} Ho={r['holdout']:>+7.2f} | dS={ds:>+.3f} d$={dt:>+.1f}{marker}{marker2}")

    print(f"\n  NOT ROBUST:")
    for r in sorted([r for r in results if not r["robust"]], key=lambda x: -x["m"].get("sharpe", 0)):
        m = r["m"]
        print(f"     {r['name']:<25} S={m.get('sharpe',0):>+.3f} ${m.get('total',0):>+7.2f} | Tr={r['train']:>+7.2f} Te={r['test']:>+7.2f} Ho={r['holdout']:>+7.2f}")


if __name__ == "__main__":
    main()
