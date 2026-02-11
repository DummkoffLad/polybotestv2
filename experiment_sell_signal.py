"""Experiment: Leader sell behavior as a REAL-TIME signal.

Key insight from user: Leader sells most positions BEFORE resolution.
Resolution outcome is NOT the right metric — leader's SELL profitability is.

Signals (all real-time, no future info):
1. Leader sold at profit: sell$ > proportional buy$ on token -> follow harder
2. Leader sold at loss: dumping position -> don't follow / exit
3. Leader re-buys after profitable sell: highest conviction -> max position
4. Leader's average sell price vs buy price: price direction signal
5. Leader's sell/buy ratio: more selling = market moved in leader's favor

How each works at decision time:
- We see leader BUY token X (1st buy) -> track, maybe follow normally
- We see leader SELL token X at higher price -> record sell profit
- We see leader BUY token X again (re-entry after profitable sell) -> BIG follow
- We see leader SELL token X at LOWER price -> DON'T follow / exit if we own

Everything uses only trades ALREADY SEEN (causal, mimics real-time).
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
    """Track leader's per-token activity in real-time.

    All methods use only PAST data (causal).
    """

    def __init__(self):
        self.tokens = defaultdict(lambda: {
            "buy_count": 0, "buy_cost": Decimal("0"), "buy_shares": Decimal("0"),
            "sell_count": 0, "sell_revenue": Decimal("0"), "sell_shares": Decimal("0"),
            "avg_buy_price": Decimal("0"),
            "last_sell_price": Decimal("0"),
            "has_profitable_sell": False,
            "has_loss_sell": False,
            "re_buy_after_sell": False,
            "re_buy_after_profit_sell": False,
        })

    def record_buy(self, token_id, price, dollars, shares):
        tk = self.tokens[token_id]
        tk["buy_count"] += 1
        tk["buy_cost"] += dollars
        tk["buy_shares"] += shares
        if tk["buy_shares"] > 0:
            tk["avg_buy_price"] = tk["buy_cost"] / tk["buy_shares"]
        # Check if this is a re-buy after a sell
        if tk["sell_count"] > 0:
            tk["re_buy_after_sell"] = True
            if tk["has_profitable_sell"]:
                tk["re_buy_after_profit_sell"] = True

    def record_sell(self, token_id, price, dollars, shares):
        tk = self.tokens[token_id]
        tk["sell_count"] += 1
        tk["sell_revenue"] += dollars
        tk["sell_shares"] += shares
        tk["last_sell_price"] = price
        # Is this sell profitable? (sell price > avg buy price)
        if price > tk["avg_buy_price"] and tk["avg_buy_price"] > 0:
            tk["has_profitable_sell"] = True
        elif price < tk["avg_buy_price"] * Decimal("0.95"):  # 5% below entry = loss sell
            tk["has_loss_sell"] = True

    def get_token(self, token_id):
        return self.tokens[token_id]

    def get_sell_pnl(self, token_id):
        """Net PnL from sells so far (sell revenue - proportional buy cost)."""
        tk = self.tokens[token_id]
        if tk["sell_shares"] == 0 or tk["buy_shares"] == 0:
            return Decimal("0")
        avg_cost = tk["buy_cost"] / tk["buy_shares"]
        return tk["sell_revenue"] - (tk["sell_shares"] * avg_cost)

    def has_profitable_sell(self, token_id):
        return self.tokens[token_id]["has_profitable_sell"]

    def has_loss_sell(self, token_id):
        return self.tokens[token_id]["has_loss_sell"]

    def is_rebuy_after_profit(self, token_id):
        return self.tokens[token_id]["re_buy_after_profit_sell"]

    def reset(self):
        self.tokens.clear()


def run_variant(hour_file, capital, budget, boost, skip_low, skip_high, mkt_cap,
                dd_reduce, dd_stop, late_min, late_mult,
                # Conviction (from v2)
                cumulative_min_dollars=0,
                # Sell signals (NEW)
                require_profitable_sell=False,
                skip_loss_sell_tokens=False,
                rebuy_after_profit_boost=1,
                sell_pnl_min=0,
                profitable_sell_boost=1,
                # Combined
                skip_before_minute=0,
                ):
    """
    require_profitable_sell: Only buy if leader has already sold at profit on this token
    skip_loss_sell_tokens: Don't buy tokens where leader sold at a loss
    rebuy_after_profit_boost: Multiply position when leader re-buys after profitable sell
    sell_pnl_min: Only buy after leader's net sell PnL > $X on this token
    profitable_sell_boost: Multiply position when leader has any profitable sell on token
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

    tracker = LeaderTracker()
    base_boost = strategy.scale_boost

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour

        trade = event.trade
        minute = trade.timestamp.minute

        if trade.action.value == "BUY":
            # Record in tracker (real-time, causal)
            tracker.record_buy(trade.token_id, trade.price, trade.dollars, trade.shares)
            tk = tracker.get_token(trade.token_id)

            # Skip before minute
            if minute < skip_before_minute:
                continue

            # Cumulative minimum
            if cumulative_min_dollars > 0 and float(tk["buy_cost"]) < cumulative_min_dollars:
                continue

            # Require profitable sell before we buy
            if require_profitable_sell and not tracker.has_profitable_sell(trade.token_id):
                continue

            # Skip tokens where leader sold at a loss (bad signal)
            if skip_loss_sell_tokens and tracker.has_loss_sell(trade.token_id):
                continue

            # Sell PnL minimum
            if sell_pnl_min > 0 and float(tracker.get_sell_pnl(trade.token_id)) < sell_pnl_min:
                continue

            # Apply boosts
            effective_boost = base_boost

            # Boost when leader has profitable sells on this token
            if profitable_sell_boost > 1 and tracker.has_profitable_sell(trade.token_id):
                effective_boost = effective_boost * Decimal(str(profitable_sell_boost))

            # Extra boost for re-buy after profitable sell
            if rebuy_after_profit_boost > 1 and tracker.is_rebuy_after_profit(trade.token_id):
                effective_boost = effective_boost * Decimal(str(rebuy_after_profit_boost))

            strategy.scale_boost = effective_boost
            decision = strategy.on_event(event)
            strategy.scale_boost = base_boost

            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)
        else:
            # SELL — record in tracker
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

    print(f"Sell-signal experiment across {len(all_hours)} hours...\n")

    BASE = dict(capital=50, budget=45, boost=5, skip_low=0.45, skip_high=0.85,
                mkt_cap=50, dd_reduce=10, dd_stop=20, late_min=40, late_mult=3)

    variants = [
        # Reference
        ("baseline", {}),
        ("cum$300 (from v2)", {"cumulative_min_dollars": 300}),

        # === REQUIRE PROFITABLE SELL (only buy after leader already sold at profit) ===
        ("req_profit_sell", {"require_profitable_sell": True}),
        ("req_profit_sell+cum$100", {"require_profitable_sell": True, "cumulative_min_dollars": 100}),
        ("req_profit_sell+cum$200", {"require_profitable_sell": True, "cumulative_min_dollars": 200}),

        # === SKIP LOSS-SELL TOKENS (don't buy tokens where leader dumped at loss) ===
        ("skip_loss_sell", {"skip_loss_sell_tokens": True}),
        ("skip_loss_sell+cum$200", {"skip_loss_sell_tokens": True, "cumulative_min_dollars": 200}),
        ("skip_loss_sell+cum$300", {"skip_loss_sell_tokens": True, "cumulative_min_dollars": 300}),

        # === SELL PNL MINIMUM (only buy after leader's net sell PnL > $X) ===
        ("sell_pnl>$5", {"sell_pnl_min": 5}),
        ("sell_pnl>$10", {"sell_pnl_min": 10}),
        ("sell_pnl>$20", {"sell_pnl_min": 20}),
        ("sell_pnl>$30", {"sell_pnl_min": 30}),

        # === PROFITABLE SELL BOOST (bigger position when leader sold at profit) ===
        ("prof_sell_1.5x", {"profitable_sell_boost": 1.5}),
        ("prof_sell_2x", {"profitable_sell_boost": 2}),
        ("prof_sell_3x", {"profitable_sell_boost": 3}),

        # === RE-BUY AFTER PROFIT BOOST (strongest conviction) ===
        ("rebuy_profit_2x", {"rebuy_after_profit_boost": 2}),
        ("rebuy_profit_3x", {"rebuy_after_profit_boost": 3}),
        ("rebuy_profit_5x", {"rebuy_after_profit_boost": 5}),

        # === COMBOS: sell signals + conviction ===
        ("cum$300+skip_loss", {"cumulative_min_dollars": 300, "skip_loss_sell_tokens": True}),
        ("cum$300+prof_sell2x", {"cumulative_min_dollars": 300, "profitable_sell_boost": 2}),
        ("cum$300+rebuy_prof3x", {"cumulative_min_dollars": 300, "rebuy_after_profit_boost": 3}),

        # === COMBOS: sell PnL + conviction ===
        ("sell_pnl>$10+cum$200", {"sell_pnl_min": 10, "cumulative_min_dollars": 200}),
        ("sell_pnl>$20+cum$200", {"sell_pnl_min": 20, "cumulative_min_dollars": 200}),

        # === COMBOS: require profit + boost ===
        ("req_prof+rebuy_prof3x", {"require_profitable_sell": True, "rebuy_after_profit_boost": 3}),
        ("req_prof+prof2x+cum$100", {"require_profitable_sell": True, "profitable_sell_boost": 2, "cumulative_min_dollars": 100}),

        # === THE FULL PACKAGE ===
        ("full: cum$300+skip_loss+rebuy3x", {"cumulative_min_dollars": 300, "skip_loss_sell_tokens": True, "rebuy_after_profit_boost": 3}),
        ("full: req_prof+cum$200+rebuy3x", {"require_profitable_sell": True, "cumulative_min_dollars": 200, "rebuy_after_profit_boost": 3}),
    ]

    results = []
    for i, (name, params) in enumerate(variants):
        split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}
        for _, _, hf, split in all_hours:
            pnl = run_variant(hf, **BASE, **params)
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
        print(f"  [{i+1:>2}/{len(variants)}] {name:<35} S={m.get('sharpe',0):+.3f} WR={m.get('wr',0):>4.0f}% ${m.get('total',0):>+7.2f} | Tr${train:>+7.2f} Te${test:>+7.2f} Ho${hold:>+7.2f} | MaxL${m.get('max_loss',0):>+6.1f} DD${m.get('max_dd',0):>5.1f} [{rob}]")

    # Ranking
    print(f"\n{'='*150}")
    print(f"  FINAL RANKING (sorted by Sharpe, robust only)")
    print(f"{'='*150}")
    baseline = next((r for r in results if r["name"] == "baseline"), None)
    base_total = baseline["m"]["total"] if baseline else 0
    base_sharpe = baseline["m"]["sharpe"] if baseline else 0

    print(f"  {'#':>2} {'Name':<37} | {'Sharpe':>7} {'WR%':>5} | {'Train$':>8} {'Test$':>8} {'Hold$':>8} {'Total$':>8} | {'MaxLoss':>8} {'MaxDD':>7} | vs Base")
    print(f"  {'-'*135}")

    for rank, r in enumerate(sorted([r for r in results if r["robust"]], key=lambda x: -x["m"]["sharpe"]), 1):
        m = r["m"]
        delta = m["total"] - base_total
        delta_s = m["sharpe"] - base_sharpe
        marker = " ***" if delta_s > 0.02 else (" **" if delta_s > 0.01 else "")
        print(f"  {rank:>2} {r['name']:<37} | {m['sharpe']:>+6.3f} {m['wr']:>4.0f}% | ${r['train']:>+7.2f} ${r['test']:>+7.2f} ${r['holdout']:>+7.2f} ${m['total']:>+7.2f} | ${m['max_loss']:>+7.2f} ${m['max_dd']:>6.2f} | ${delta:>+6.1f} dS{delta_s:>+.3f}{marker}")

    not_robust = [r for r in results if not r["robust"]]
    if not_robust:
        print(f"\n  NOT ROBUST:")
        for r in sorted(not_robust, key=lambda x: -x["m"].get("sharpe", 0))[:5]:
            m = r["m"]
            print(f"     {r['name']:<37} | {m.get('sharpe',0):>+6.3f} | Tr${r['train']:>+7.2f} Te${r['test']:>+7.2f} Ho${r['holdout']:>+7.2f}")


if __name__ == "__main__":
    main()
