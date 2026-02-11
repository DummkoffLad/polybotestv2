"""Experiment: Contrarian buy signal — buy when leader profit-sells low-conviction tokens.

Discovery from analyze_scalps.py:
- When leader sells a token at profit (sell > avg buy) on low-conviction (<$300) tokens,
  the token resolves WIN 70% of the time.
- Idea: when we see leader sell at profit on a token where we have no position,
  BUY a small position and hold to resolution.
- This captures the scalp-zone money without needing to match leader's sell timing.

Testing across train/test/holdout to verify robustness.
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
from src.data.models import TradeAction
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


def run_with_contrarian(hour_file, capital, budget, boost, skip_low, skip_high, mkt_cap,
                         dd_reduce, dd_stop, late_min, late_mult, cumulative_min_dollars,
                         # Contrarian params
                         contrarian_enabled=False,
                         contrarian_max_spend=300,  # Only on low-conviction tokens
                         contrarian_min_margin=0.0,  # Min sell_price - avg_buy for signal
                         contrarian_dollars=3.0,     # Fixed position size per contrarian buy
                         contrarian_min_ask=0.30,    # Don't buy below this ask
                         contrarian_max_ask=0.85,    # Don't buy above this ask
                         ):
    """Run one hour with conviction strategy + optional contrarian buys."""
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
    pt_mod.CUMULATIVE_MIN_LEADER_DOLLARS = Decimal(str(cumulative_min_dollars))

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

    # Contrarian tracking (per-token, per-hour)
    leader_token_buys = defaultdict(lambda: {"dollars": Decimal("0"), "shares": Decimal("0")})
    contrarian_positions = {}  # token_id -> {"shares": Decimal, "cost": Decimal}
    contrarian_buys = 0
    contrarian_budget_used = Decimal("0")
    MAX_CONTRARIAN_BUDGET = Decimal(str(capital * 0.3))  # Max 30% of capital for contrarian

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour

        trade = event.trade

        if trade.action == TradeAction.BUY:
            # Track for contrarian signal
            lb = leader_token_buys[trade.token_id]
            lb["dollars"] += trade.dollars
            lb["shares"] += trade.shares

        elif trade.action == TradeAction.SELL and contrarian_enabled:
            lb = leader_token_buys[trade.token_id]
            # Check if this is a profitable sell on a low-conviction token
            if (lb["dollars"] > 0 and
                float(lb["dollars"]) < contrarian_max_spend and
                lb["shares"] > 0 and
                trade.token_id not in contrarian_positions):

                avg_buy = lb["dollars"] / lb["shares"]
                sell_price = trade.price
                margin = float(sell_price - avg_buy)

                if margin >= contrarian_min_margin and margin > 0:
                    # Leader sold at profit on low-conviction token — contrarian buy signal!
                    prices = event.prices
                    ask = prices.ask if prices and prices.ask else None

                    if (ask and
                        Decimal(str(contrarian_min_ask)) <= ask <= Decimal(str(contrarian_max_ask)) and
                        contrarian_budget_used < MAX_CONTRARIAN_BUDGET and
                        strategy.cash > Decimal(str(contrarian_dollars))):

                        buy_dollars = Decimal(str(contrarian_dollars))
                        buy_shares = (buy_dollars / ask).quantize(Decimal("0.01"))

                        if buy_shares > 0:
                            contrarian_positions[trade.token_id] = {
                                "shares": buy_shares,
                                "cost": buy_dollars,
                                "price": ask,
                                "market_id": trade.market_id,
                                "side": trade.side,
                            }
                            strategy.cash -= buy_dollars
                            contrarian_budget_used += buy_dollars
                            contrarian_buys += 1

        # Normal strategy processing
        decision = strategy.on_event(event)
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    # Resolve normal positions
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

    # Resolve contrarian positions
    for token_id, cp in contrarian_positions.items():
        ps = end_prices.get(token_id)
        if ps and ps.bid is not None:
            last_bid = ps.bid
        else:
            last_bid = cp["price"]  # Use entry as fallback
        res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
        strategy.cash += cp["shares"] * res_price

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

    print(f"Testing contrarian buy signal across {len(all_hours)} hours...\n")

    BASE = dict(capital=50, budget=45, boost=5, skip_low=0.45, skip_high=0.85,
                mkt_cap=50, dd_reduce=12, dd_stop=24, late_min=40, late_mult=3,
                cumulative_min_dollars=300)

    variants = [
        # Reference: current best (no contrarian)
        ("baseline_conviction", {}),

        # Contrarian: basic (any profit sell)
        ("contrarian_$3", {"contrarian_enabled": True, "contrarian_dollars": 3.0}),
        ("contrarian_$5", {"contrarian_enabled": True, "contrarian_dollars": 5.0}),
        ("contrarian_$2", {"contrarian_enabled": True, "contrarian_dollars": 2.0}),

        # Contrarian: require min margin
        ("ctr_margin>$0.02", {"contrarian_enabled": True, "contrarian_dollars": 3.0, "contrarian_min_margin": 0.02}),
        ("ctr_margin>$0.05", {"contrarian_enabled": True, "contrarian_dollars": 3.0, "contrarian_min_margin": 0.05}),

        # Contrarian: tighter price range (avoid cheap/expensive)
        ("ctr_ask0.45-0.85", {"contrarian_enabled": True, "contrarian_dollars": 3.0, "contrarian_min_ask": 0.45}),
        ("ctr_ask0.55-0.85", {"contrarian_enabled": True, "contrarian_dollars": 3.0, "contrarian_min_ask": 0.55}),

        # Contrarian: lower max spend (only truly low-conviction tokens)
        ("ctr_max$150", {"contrarian_enabled": True, "contrarian_dollars": 3.0, "contrarian_max_spend": 150}),
        ("ctr_max$200", {"contrarian_enabled": True, "contrarian_dollars": 3.0, "contrarian_max_spend": 200}),

        # Contrarian: bigger positions on high-ask tokens
        ("ctr_$5_ask55+", {"contrarian_enabled": True, "contrarian_dollars": 5.0, "contrarian_min_ask": 0.55}),

        # Combined: margin + ask filter
        ("ctr_margin+ask55", {"contrarian_enabled": True, "contrarian_dollars": 3.0,
                               "contrarian_min_margin": 0.02, "contrarian_min_ask": 0.55}),
        ("ctr_m5c+ask55+$5", {"contrarian_enabled": True, "contrarian_dollars": 5.0,
                               "contrarian_min_margin": 0.05, "contrarian_min_ask": 0.55}),
    ]

    results = []
    for i, (name, params) in enumerate(variants):
        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}
        for _, _, hf, split in all_hours:
            pnl = run_with_contrarian(hf, **BASE, **params)
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
        print(f"  [{i+1:>2}/{len(variants)}] {name:<25} S={m.get('sharpe',0):+.3f} WR={m.get('wr',0):>4.0f}% total={m.get('total',0):>+7.2f} | Tr={train:>+7.2f} Te={test:>+7.2f} Ho={hold:>+7.2f} | ML={m.get('max_loss',0):>+6.1f} DD={m.get('max_dd',0):>5.1f} [{rob}]")

    # Ranking
    print(f"\n{'='*140}")
    print(f"  RANKING (sorted by Sharpe)")
    print(f"{'='*140}")
    baseline = next((r for r in results if r["name"] == "baseline_conviction"), None)
    base_sharpe = baseline["m"]["sharpe"] if baseline else 0
    base_total = baseline["m"]["total"] if baseline else 0

    for rank, r in enumerate(sorted(results, key=lambda x: -x["m"].get("sharpe", 0)), 1):
        m = r["m"]
        rob = "YES" if r["robust"] else "no"
        ds = m["sharpe"] - base_sharpe
        dt = m["total"] - base_total
        marker = " ***" if ds > 0.005 else ""
        print(f"  {rank:>2} {r['name']:<25} S={m['sharpe']:>+.3f} WR={m['wr']:>4.0f}% total={m['total']:>+7.2f} | Tr={r['train']:>+7.2f} Te={r['test']:>+7.2f} Ho={r['holdout']:>+7.2f} | dS={ds:>+.3f} d$={dt:>+.1f} [{rob}]{marker}")


if __name__ == "__main__":
    main()
