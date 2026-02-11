"""Conviction v2: Fixed boost mechanism + more ideas.

v1 showed:
- cum_min_$200: Sharpe 0.244, max loss -$31.66 (best consistency)
- Boost variants BROKEN (strategy caches SCALE_BOOST at init, module patch ignored)

Fix: Directly modify strategy.scale_boost during event processing.

New ideas:
- Combine cum_min with late3 boost (only boost AFTER conviction established)
- Leader dollar-volume signal: bigger leader trades = follow bigger
- Leader price-direction: follow harder when leader buys at rising prices
- Skip early minutes entirely (let market settle first)
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
                dd_reduce, dd_stop, late_min, late_mult,
                # Conviction params
                cumulative_min_dollars=0,
                conviction_boost_after=0, conviction_boost_mult=1,
                rebuy_boost_mult=1,
                skip_first_n=0,
                # Time-based
                skip_before_minute=0,
                # Leader trade size signal
                leader_trade_size_boost=False,
                ):
    """Run one hour with conviction tracking.

    All signals use ONLY past/current information (no future leaking).

    cumulative_min_dollars: Only follow after leader spent $X cumulative on token
    conviction_boost_after: After $X cumulative leader spend, apply conviction_boost_mult
    conviction_boost_mult: Multiplier for positions after conviction threshold
    rebuy_boost_mult: Boost when leader re-buys (sold then bought again)
    skip_first_n: Skip first N leader buys per token
    skip_before_minute: Skip all trades before this minute (let market settle)
    leader_trade_size_boost: Scale our position proportional to leader's trade size
    """
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

    # Track conviction per token (only uses past data)
    token_buys = defaultdict(lambda: {"count": 0, "dollars": Decimal("0")})
    token_has_sold = set()
    base_boost = strategy.scale_boost  # Cache the initialized boost

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour

        trade = event.trade
        minute = trade.timestamp.minute

        if trade.action.value == "BUY":
            # Record this buy (cumulative, real-time info)
            tb = token_buys[trade.token_id]
            tb["count"] += 1
            tb["dollars"] += trade.dollars

            # Skip before minute threshold
            if minute < skip_before_minute:
                continue

            # Skip first N buys per token
            if tb["count"] <= skip_first_n:
                continue

            # Cumulative minimum filter
            if cumulative_min_dollars > 0 and float(tb["dollars"]) < cumulative_min_dollars:
                continue

            # Apply conviction boost DIRECTLY to strategy instance
            effective_boost = base_boost
            if conviction_boost_after > 0 and float(tb["dollars"]) >= conviction_boost_after:
                effective_boost = base_boost * Decimal(str(conviction_boost_mult))

            # Re-buy boost
            if trade.token_id in token_has_sold and rebuy_boost_mult > 1:
                effective_boost = effective_boost * Decimal(str(rebuy_boost_mult))

            # Leader trade size boost: bigger leader trades = bigger our position
            if leader_trade_size_boost and float(trade.dollars) > 30:
                # Leader trades $30+ are higher conviction
                size_mult = min(Decimal("2"), trade.dollars / Decimal("20"))
                effective_boost = effective_boost * size_mult

            # Apply boost directly to strategy (bypasses cached value)
            strategy.scale_boost = effective_boost
            decision = strategy.on_event(event)
            strategy.scale_boost = base_boost  # Reset

            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)
        else:
            # SELL
            token_has_sold.add(trade.token_id)
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
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                all_hours.append((session, utc_h, hf, get_split(session)))

    print(f"Conviction v2 across {len(all_hours)} hours (fixed boost mechanism)...\n")

    # Base: current best b5+hi85+late3
    BASE = dict(capital=50, budget=45, boost=5, skip_low=0.45, skip_high=0.85,
                mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3)

    variants = [
        # Reference
        ("baseline", {}),

        # === CUMULATIVE MIN (from v1, confirmed best) ===
        ("cum$150", {"cumulative_min_dollars": 150}),
        ("cum$200", {"cumulative_min_dollars": 200}),
        ("cum$250", {"cumulative_min_dollars": 250}),
        ("cum$300", {"cumulative_min_dollars": 300}),

        # === FIXED CONVICTION BOOST ===
        ("boost2x@$100", {"conviction_boost_after": 100, "conviction_boost_mult": 2}),
        ("boost2x@$200", {"conviction_boost_after": 200, "conviction_boost_mult": 2}),
        ("boost3x@$200", {"conviction_boost_after": 200, "conviction_boost_mult": 3}),
        ("boost2x@$300", {"conviction_boost_after": 300, "conviction_boost_mult": 2}),

        # === FIXED RE-BUY BOOST ===
        ("rebuy_1.5x", {"rebuy_boost_mult": 1.5}),
        ("rebuy_2x", {"rebuy_boost_mult": 2}),
        ("rebuy_3x", {"rebuy_boost_mult": 3}),

        # === SKIP FIRST N + BOOST ===
        ("skip1+boost2x@$100", {"skip_first_n": 1, "conviction_boost_after": 100, "conviction_boost_mult": 2}),
        ("skip1+boost2x@$200", {"skip_first_n": 1, "conviction_boost_after": 200, "conviction_boost_mult": 2}),

        # === CUM MIN + CONVICTION BOOST ===
        ("cum$100+boost2x@$200", {"cumulative_min_dollars": 100, "conviction_boost_after": 200, "conviction_boost_mult": 2}),
        ("cum$150+boost2x@$300", {"cumulative_min_dollars": 150, "conviction_boost_after": 300, "conviction_boost_mult": 2}),
        ("cum$200+boost2x@$300", {"cumulative_min_dollars": 200, "conviction_boost_after": 300, "conviction_boost_mult": 2}),

        # === SKIP BEFORE MINUTE (let market settle) ===
        ("skip_min3", {"skip_before_minute": 3}),
        ("skip_min5", {"skip_before_minute": 5}),
        ("skip_min10", {"skip_before_minute": 10}),

        # === LEADER TRADE SIZE BOOST ===
        ("leader_size_boost", {"leader_trade_size_boost": True}),

        # === CUM MIN + RE-BUY ===
        ("cum$200+rebuy2x", {"cumulative_min_dollars": 200, "rebuy_boost_mult": 2}),
        ("cum$150+rebuy1.5x", {"cumulative_min_dollars": 150, "rebuy_boost_mult": 1.5}),

        # === BEST COMBOS ===
        ("cum$200+boost2x@$200", {"cumulative_min_dollars": 200, "conviction_boost_after": 200, "conviction_boost_mult": 2}),
        ("cum$200+skip_min5", {"cumulative_min_dollars": 200, "skip_before_minute": 5}),
        ("cum$200+leader_sz", {"cumulative_min_dollars": 200, "leader_trade_size_boost": True}),
        ("skip1+rebuy2x", {"skip_first_n": 1, "rebuy_boost_mult": 2}),
        ("skip_min5+rebuy2x", {"skip_before_minute": 5, "rebuy_boost_mult": 2}),
    ]

    results = []
    for i, (name, conv_params) in enumerate(variants):
        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}
        for _, _, hf, split in all_hours:
            pnl = run_variant(hf, **BASE, **conv_params)
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
        print(f"  [{i+1:>2}/{len(variants)}] {name:<26} S={m.get('sharpe',0):+.3f} WR={m.get('wr',0):>4.0f}% ${m.get('total',0):>+7.2f} | Tr${train:>+7.2f} Te${test:>+7.2f} Ho${hold:>+7.2f} | MaxL${m.get('max_loss',0):>+6.1f} DD${m.get('max_dd',0):>5.1f} [{rob}]")

    # Ranking
    print(f"\n{'='*140}")
    print(f"  FINAL RANKING (sorted by Sharpe, robust only)")
    print(f"{'='*140}")
    baseline = next((r for r in results if r["name"] == "baseline"), None)
    base_total = baseline["m"]["total"] if baseline else 0
    base_sharpe = baseline["m"]["sharpe"] if baseline else 0

    print(f"  {'#':>2} {'Name':<28} | {'Sharpe':>7} {'WR%':>5} | {'Train$':>8} {'Test$':>8} {'Hold$':>8} {'Total$':>8} | {'MaxLoss':>8} {'MaxDD':>7} | vs Base")
    print(f"  {'-'*125}")

    for rank, r in enumerate(sorted([r for r in results if r["robust"]], key=lambda x: -x["m"]["sharpe"]), 1):
        m = r["m"]
        delta = m["total"] - base_total
        delta_s = m["sharpe"] - base_sharpe
        marker = " ***" if delta_s > 0.01 else ""
        print(f"  {rank:>2} {r['name']:<28} | {m['sharpe']:>+6.3f} {m['wr']:>4.0f}% | ${r['train']:>+7.2f} ${r['test']:>+7.2f} ${r['holdout']:>+7.2f} ${m['total']:>+7.2f} | ${m['max_loss']:>+7.2f} ${m['max_dd']:>6.2f} | ${delta:>+6.1f} dS{delta_s:>+.3f}{marker}")

    not_robust = [r for r in results if not r["robust"]]
    if not_robust:
        print(f"\n  NOT ROBUST:")
        for r in sorted(not_robust, key=lambda x: -x["m"].get("sharpe", 0))[:5]:
            m = r["m"]
            print(f"     {r['name']:<28} | {m.get('sharpe',0):>+6.3f} | Tr${r['train']:>+7.2f} Te${r['test']:>+7.2f} Ho${r['holdout']:>+7.2f}")


if __name__ == "__main__":
    main()
