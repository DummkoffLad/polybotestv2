"""Experiment: Leader conviction signals.

Key discovery from analyze_leader_deep.py:
- Leader spends $500+ on token -> 95% WR
- Leader spends <$200 -> 12% WR
- Winners avg entry $0.76, losers $0.32
- We buy $0.06-0.20 higher than leader's avg entry

Testable ideas:
1. MIN_LEADER_BUY_COUNT: Only follow after leader buys Nth time on same token
2. CONVICTION_BOOST: Boost position size when leader shows repeated buys
3. CUMULATIVE_MIN_DOLLARS: Only follow after leader has spent $X on token
4. SKIP_FIRST_BUY: Skip leader's first buy, wait for confirmation
5. Combined with existing b5+hi85+late3 base
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


class ConvictionTracker:
    """Track leader's cumulative buys per token during an hour."""

    def __init__(self):
        self.token_buys = defaultdict(lambda: {"count": 0, "dollars": Decimal("0"), "shares": Decimal("0")})

    def record_buy(self, token_id, dollars, shares):
        b = self.token_buys[token_id]
        b["count"] += 1
        b["dollars"] += dollars
        b["shares"] += shares

    def get_buy_count(self, token_id):
        return self.token_buys[token_id]["count"]

    def get_cumulative_dollars(self, token_id):
        return self.token_buys[token_id]["dollars"]

    def reset(self):
        self.token_buys.clear()


def run_variant(hour_file, capital, budget, boost, skip_low, skip_high, mkt_cap,
                dd_reduce, dd_stop, late_min, late_mult,
                min_buy_count=1, conviction_boost_count=0, conviction_boost_mult=1,
                cumulative_min_dollars=0, skip_first_n=0,
                rebuy_boost_mult=1):
    """Run one hour with conviction tracking.

    min_buy_count: Only follow after leader has bought token N times (1=follow first buy)
    conviction_boost_count: After N buys, apply conviction_boost_mult
    cumulative_min_dollars: Only follow after leader spent $X on token
    skip_first_n: Skip first N leader buys on each token
    rebuy_boost_mult: Multiply position when leader re-buys (2nd+ buy after a sell)
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

    # Conviction tracking
    tracker = ConvictionTracker()
    leader_sold_tokens = set()  # tokens the leader has sold (for re-buy tracking)

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour

        trade = event.trade

        if trade.action.value == "BUY":
            # Record leader buy for conviction tracking
            tracker.record_buy(trade.token_id, trade.dollars, trade.shares)
            buy_count = tracker.get_buy_count(trade.token_id)
            cumulative = tracker.get_cumulative_dollars(trade.token_id)

            # Check conviction filters
            if buy_count <= skip_first_n:
                # Skip first N buys (wait for confirmation)
                continue

            if buy_count < min_buy_count:
                continue

            if cumulative_min_dollars > 0 and float(cumulative) < cumulative_min_dollars:
                continue

            # Temporarily adjust boost for conviction
            orig_boost = pt_mod.SCALE_BOOST
            if conviction_boost_count > 0 and buy_count >= conviction_boost_count:
                pt_mod.SCALE_BOOST = Decimal(str(boost)) * Decimal(str(conviction_boost_mult))

            # Re-buy boost (leader sold then re-bought)
            if trade.token_id in leader_sold_tokens and rebuy_boost_mult > 1:
                pt_mod.SCALE_BOOST = pt_mod.SCALE_BOOST * Decimal(str(rebuy_boost_mult))

            decision = strategy.on_event(event)
            pt_mod.SCALE_BOOST = orig_boost

            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)
        else:
            # SELL — record for re-buy tracking
            leader_sold_tokens.add(trade.token_id)
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

    print(f"Testing leader conviction signals across {len(all_hours)} hours...\n")

    # Base config (current best: b5+hi85+late3)
    BASE = dict(capital=50, budget=45, boost=5, skip_low=0.45, skip_high=0.85,
                mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3)

    # (name, conviction_params)
    variants = [
        # Reference: current best (no conviction filter)
        ("baseline_b5hi85late3", {}),

        # === SKIP FIRST N BUYS (wait for confirmation) ===
        ("skip_first_1", {"skip_first_n": 1}),
        ("skip_first_2", {"skip_first_n": 2}),
        ("skip_first_3", {"skip_first_n": 3}),

        # === MIN BUY COUNT (only follow after N buys on token) ===
        ("min_buys_2", {"min_buy_count": 2}),
        ("min_buys_3", {"min_buy_count": 3}),
        ("min_buys_4", {"min_buy_count": 4}),
        ("min_buys_5", {"min_buy_count": 5}),

        # === CUMULATIVE MINIMUM DOLLARS ===
        ("cum_min_$50", {"cumulative_min_dollars": 50}),
        ("cum_min_$100", {"cumulative_min_dollars": 100}),
        ("cum_min_$150", {"cumulative_min_dollars": 150}),
        ("cum_min_$200", {"cumulative_min_dollars": 200}),

        # === CONVICTION BOOST (increase size after N buys) ===
        ("conv_boost_2x@3", {"conviction_boost_count": 3, "conviction_boost_mult": 2}),
        ("conv_boost_2x@5", {"conviction_boost_count": 5, "conviction_boost_mult": 2}),
        ("conv_boost_3x@3", {"conviction_boost_count": 3, "conviction_boost_mult": 3}),
        ("conv_boost_3x@5", {"conviction_boost_count": 5, "conviction_boost_mult": 3}),

        # === RE-BUY BOOST (when leader sells then buys back) ===
        ("rebuy_2x", {"rebuy_boost_mult": 2}),
        ("rebuy_3x", {"rebuy_boost_mult": 3}),

        # === COMBOS: skip first + boost later ===
        ("skip1+boost2x@3", {"skip_first_n": 1, "conviction_boost_count": 3, "conviction_boost_mult": 2}),
        ("skip1+boost3x@3", {"skip_first_n": 1, "conviction_boost_count": 3, "conviction_boost_mult": 3}),
        ("skip1+rebuy2x", {"skip_first_n": 1, "rebuy_boost_mult": 2}),
        ("skip2+boost2x@3", {"skip_first_n": 2, "conviction_boost_count": 3, "conviction_boost_mult": 2}),

        # === COMBOS: cum min + boost ===
        ("cum$50+boost2x@3", {"cumulative_min_dollars": 50, "conviction_boost_count": 3, "conviction_boost_mult": 2}),
        ("cum$100+boost2x@5", {"cumulative_min_dollars": 100, "conviction_boost_count": 5, "conviction_boost_mult": 2}),

        # === MIN BUYS + BOOST ===
        ("min2+boost2x@4", {"min_buy_count": 2, "conviction_boost_count": 4, "conviction_boost_mult": 2}),
        ("min3+boost3x@5", {"min_buy_count": 3, "conviction_boost_count": 5, "conviction_boost_mult": 3}),
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
        print(f"  [{i+1:>2}/{len(variants)}] {name:<25} S={m.get('sharpe',0):+.3f} WR={m.get('wr',0):>4.0f}% ${m.get('total',0):>+7.2f} | Tr${train:>+7.2f} Te${test:>+7.2f} Ho${hold:>+7.2f} | MaxL${m.get('max_loss',0):>+6.1f} DD${m.get('max_dd',0):>5.1f} [{rob}]")

    # Ranking
    print(f"\n{'='*140}")
    print(f"  FINAL RANKING (sorted by Sharpe, robust only)")
    print(f"{'='*140}")
    print(f"  {'#':>2} {'Name':<27} | {'Sharpe':>7} {'WR%':>5} | {'Train$':>8} {'Test$':>8} {'Hold$':>8} {'Total$':>8} | {'MaxLoss':>8} {'MaxDD':>7} | vs Base")
    print(f"  {'-'*120}")

    baseline = next((r for r in results if r["name"] == "baseline_b5hi85late3"), None)
    base_total = baseline["m"]["total"] if baseline else 0
    base_sharpe = baseline["m"]["sharpe"] if baseline else 0

    for rank, r in enumerate(sorted([r for r in results if r["robust"]], key=lambda x: -x["m"]["sharpe"]), 1):
        m = r["m"]
        delta = m["total"] - base_total
        delta_s = m["sharpe"] - base_sharpe
        print(f"  {rank:>2} {r['name']:<27} | {m['sharpe']:>+6.3f} {m['wr']:>4.0f}% | ${r['train']:>+7.2f} ${r['test']:>+7.2f} ${r['holdout']:>+7.2f} ${m['total']:>+7.2f} | ${m['max_loss']:>+7.2f} ${m['max_dd']:>6.2f} | ${delta:>+6.1f} dS{delta_s:>+.3f}")

    not_robust = [r for r in results if not r["robust"]]
    if not_robust:
        print(f"\n  NOT ROBUST:")
        for r in sorted(not_robust, key=lambda x: -x["m"].get("sharpe", 0))[:5]:
            m = r["m"]
            print(f"     {r['name']:<27} | {m.get('sharpe',0):>+6.3f} | Tr${r['train']:>+7.2f} Te${r['test']:>+7.2f} Ho${r['holdout']:>+7.2f}")


if __name__ == "__main__":
    main()
