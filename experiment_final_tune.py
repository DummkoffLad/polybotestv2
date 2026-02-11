"""Final tuning: combine best conviction + sell signals.

Winners so far:
- cum$300: Sharpe 0.331, PnL $233, MaxLoss -$26, DD $34
- req_profit_sell: Sharpe 0.244, PnL $243, but same MaxLoss -$43
- sell_pnl>$5-10: Very low losses but less PnL

Now fine-tune:
1. Exact cumulative threshold ($250-$400 range)
2. cum + req_profit_sell combos
3. cum + sell_pnl combos
4. Test with different base boosts (3-7)
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


class LeaderTracker:
    def __init__(self):
        self.tokens = defaultdict(lambda: {
            "buy_count": 0, "buy_cost": Decimal("0"), "buy_shares": Decimal("0"),
            "sell_count": 0, "sell_revenue": Decimal("0"), "sell_shares": Decimal("0"),
            "avg_buy_price": Decimal("0"),
            "has_profitable_sell": False,
        })

    def record_buy(self, token_id, price, dollars, shares):
        tk = self.tokens[token_id]
        tk["buy_count"] += 1
        tk["buy_cost"] += dollars
        tk["buy_shares"] += shares
        if tk["buy_shares"] > 0:
            tk["avg_buy_price"] = tk["buy_cost"] / tk["buy_shares"]

    def record_sell(self, token_id, price, dollars, shares):
        tk = self.tokens[token_id]
        tk["sell_count"] += 1
        tk["sell_revenue"] += dollars
        tk["sell_shares"] += shares
        if price > tk["avg_buy_price"] and tk["avg_buy_price"] > 0:
            tk["has_profitable_sell"] = True

    def get_token(self, token_id):
        return self.tokens[token_id]

    def get_sell_pnl(self, token_id):
        tk = self.tokens[token_id]
        if tk["sell_shares"] == 0 or tk["buy_shares"] == 0:
            return Decimal("0")
        avg_cost = tk["buy_cost"] / tk["buy_shares"]
        return tk["sell_revenue"] - (tk["sell_shares"] * avg_cost)


def run_variant(hour_file, capital, budget, boost, skip_low, skip_high, mkt_cap,
                dd_reduce, dd_stop, late_min, late_mult,
                cumulative_min_dollars=0, require_profitable_sell=False,
                sell_pnl_min=0):
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

    tracker = LeaderTracker()

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour
        trade = event.trade

        if trade.action.value == "BUY":
            tracker.record_buy(trade.token_id, trade.price, trade.dollars, trade.shares)
            tk = tracker.get_token(trade.token_id)

            # Cumulative minimum
            if cumulative_min_dollars > 0 and float(tk["buy_cost"]) < cumulative_min_dollars:
                continue

            # Require profitable sell
            if require_profitable_sell and not tk["has_profitable_sell"]:
                continue

            # Sell PnL minimum
            if sell_pnl_min > 0 and float(tracker.get_sell_pnl(trade.token_id)) < sell_pnl_min:
                continue

            decision = strategy.on_event(event)
            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)
        else:
            tracker.record_sell(trade.token_id, trade.price, trade.dollars, trade.shares)
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

    print(f"Final tuning across {len(all_hours)} hours...\n")

    variants = [
        # References
        ("baseline b5+hi85+late3", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3)),

        # === FINE-TUNE CUM THRESHOLD ===
        ("cum$250", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=250)),
        ("cum$275", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=275)),
        ("cum$300", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300)),
        ("cum$350", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=350)),
        ("cum$400", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=400)),

        # === CUM + REQ PROFIT SELL ===
        ("cum$200+req_prof", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=200, require_profitable_sell=True)),
        ("cum$250+req_prof", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=250, require_profitable_sell=True)),
        ("cum$300+req_prof", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300, require_profitable_sell=True)),

        # === CUM + SELL PNL ===
        ("cum$250+sellpnl>$5", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=250, sell_pnl_min=5)),
        ("cum$300+sellpnl>$5", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300, sell_pnl_min=5)),

        # === REQ PROFIT SELL ONLY ===
        ("req_prof_sell", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, require_profitable_sell=True)),

        # === CUM$300 WITH DIFFERENT BOOSTS ===
        ("cum$300 b3+late3", dict(boost=3, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300)),
        ("cum$300 b4+late3", dict(boost=4, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300)),
        ("cum$300 b6+late3", dict(boost=6, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300)),
        ("cum$300 b7+late3", dict(boost=7, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300)),
        ("cum$300 b8+late3", dict(boost=8, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300)),

        # === CUM$300 WITH DIFFERENT LATE MULT ===
        ("cum$300 late2x", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=2, cumulative_min_dollars=300)),
        ("cum$300 late4x", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=4, cumulative_min_dollars=300)),
        ("cum$300 late5x", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=5, cumulative_min_dollars=300)),

        # === CUM$300 WITH WIDER/NARROWER PRICE RANGE ===
        ("cum$300 hi90", dict(boost=5, skip_low=0.45, skip_high=0.90, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300)),
        ("cum$300 hi80", dict(boost=5, skip_low=0.45, skip_high=0.80, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300)),
        ("cum$300 lo50", dict(boost=5, skip_low=0.50, skip_high=0.85, mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3, cumulative_min_dollars=300)),

        # === CUM$300 WITH DD TUNING ===
        ("cum$300 dd8/16", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=8, dd_stop=16, late_min=40, late_mult=3, cumulative_min_dollars=300)),
        ("cum$300 dd12/24", dict(boost=5, skip_low=0.45, skip_high=0.85, mkt_cap=50, dd_reduce=12, dd_stop=24, late_min=40, late_mult=3, cumulative_min_dollars=300)),
    ]

    results = []
    for i, (name, params) in enumerate(variants):
        full_params = dict(capital=50, budget=45, **params)
        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}
        for _, _, hf, split in all_hours:
            pnl = run_variant(hf, **full_params)
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
    print(f"  FINAL RANKING (sorted by Sharpe)")
    print(f"{'='*140}")
    baseline = next((r for r in results if "baseline" in r["name"]), None)
    base_total = baseline["m"]["total"] if baseline else 0
    base_sharpe = baseline["m"]["sharpe"] if baseline else 0

    print(f"  {'#':>2} {'Name':<28} | {'Sharpe':>7} {'WR%':>5} | {'Train$':>8} {'Test$':>8} {'Hold$':>8} {'Total$':>8} | {'MaxLoss':>8} {'MaxDD':>7} | vs Base")
    print(f"  {'-'*125}")

    for rank, r in enumerate(sorted([r for r in results if r["robust"]], key=lambda x: -x["m"]["sharpe"]), 1):
        m = r["m"]
        delta = m["total"] - base_total
        delta_s = m["sharpe"] - base_sharpe
        marker = " ***" if delta_s > 0.05 else (" **" if delta_s > 0.02 else "")
        print(f"  {rank:>2} {r['name']:<28} | {m['sharpe']:>+6.3f} {m['wr']:>4.0f}% | ${r['train']:>+7.2f} ${r['test']:>+7.2f} ${r['holdout']:>+7.2f} ${m['total']:>+7.2f} | ${m['max_loss']:>+7.2f} ${m['max_dd']:>6.2f} | ${delta:>+6.1f} dS{delta_s:>+.3f}{marker}")

    not_robust = [r for r in results if not r["robust"]]
    if not_robust:
        print(f"\n  NOT ROBUST:")
        for r in sorted(not_robust, key=lambda x: -x["m"].get("sharpe", 0)):
            m = r["m"]
            print(f"     {r['name']:<28} | {m.get('sharpe',0):>+6.3f} | Tr${r['train']:>+7.2f} Te${r['test']:>+7.2f} Ho${r['holdout']:>+7.2f}")


if __name__ == "__main__":
    main()
