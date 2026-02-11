"""Test FIXED SELL SIZING — the biggest bug in the strategy.

CRITICAL BUG: Buy uses boost (8x), sell does NOT.
- Leader buys $100 → we buy $44 (scale_ratio * boost = 0.0556 * 8)
- Leader sells $100 → we sell $5.56 (scale_ratio only = 0.0556)
- 8x asymmetry! We accumulate 8x more than we can exit.
- Result: positions pile up at resolution instead of being properly exited.

FIX OPTIONS:
A. Proportional sell: When leader sells X% of their position, we sell X% of ours
B. Boosted sell: Apply the same boost to sell sizing
C. Full exit: When leader makes a significant sell, exit our FULL position
D. Combined: Proportional sell + selective exit for stragglers
"""
import sys
import json
import statistics
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer
from src.data.models import PriceSnapshot
import src.strategies.profit_taker.strategy as pt_module
from src.strategies.profit_taker.strategy import ProfitTakerStrategy

CONFIG_OVERRIDES = {
    "scaling.our_capital": 50,
    "scaling.hourly_budget": 45,
    "scaling.k_factor": 1,
    "scaling.leader_estimated_capital": 900,
}

TRAIN_SESSIONS = {"2026-02-03/05-56", "2026-02-04/02-35", "2026-02-06/05-30"}
TEST_SESSIONS = {"2026-02-05/03-58", "2026-02-05/22-15", "2026-02-07/05-52"}
HOLDOUT_SESSIONS = {"2026-02-08/06-39"}

_original_handle_leader_sell = ProfitTakerStrategy._handle_leader_sell


def set_base_params():
    pt_module.SKIP_PRICE_LOW = Decimal("0.45")
    pt_module.SKIP_PRICE_HIGH = Decimal("0.97")
    pt_module.SCALE_BOOST = Decimal("8")
    pt_module.MIN_LEADER_TRADE_PCT = Decimal("2.0")
    pt_module.DRAWDOWN_REDUCE_THRESHOLD = Decimal("15")
    pt_module.DRAWDOWN_STOP_THRESHOLD = Decimal("25")
    pt_module.LATE_HOUR_REDUCE_MIN = 59
    pt_module.LATE_HOUR_STOP_MIN = 60
    pt_module.PER_MARKET_CAP_PCT = Decimal("50")
    pt_module.PROFIT_TARGET_LOW = Decimal("100")
    pt_module.PROFIT_TARGET_MID = Decimal("100")
    pt_module.PROFIT_TARGET_HIGH = Decimal("50")


# ======================================================================
# VARIANT A: PROPORTIONAL SELL (sell same % as leader)
# ======================================================================
def proportional_sell_handler(self, event):
    """When leader sells X% of their position, we sell X% of ours."""
    from src.strategies.profit_taker.strategy import (
        IGNORE_LEADER_MINISELLS_PCT, MIN_LIMIT_ORDER_SHARES, MIN_OUR_TRADE,
        SLIPPAGE_THRESHOLD, SLIPPAGE_PER_SHARE
    )
    from ..base import TradeDecision
    trade, prices = event.trade, event.prices
    pos = self.portfolio.get(trade.token_id, trade.market_id,
                             __import__('src.strategies.utils', fromlist=['to_side']).to_side(trade.side))
    if pos.shares <= 0:
        return self._skip("no_position")
    bid = prices.bid
    if not bid or bid <= 0:
        return self._skip("no_price")
    if bid >= Decimal("1"):
        return self._skip("invalid_price")
    if self._is_leader_minisell(event):
        return self._skip("leader_minisell")

    # Proportional: sell same % as leader
    lp = self.leader_positions.get(trade.token_id)
    if lp and lp.get("shares", 0) > 0:
        leader_sell_pct = min(trade.shares / lp["shares"], Decimal("1"))
        shares = (pos.shares * leader_sell_pct).quantize(Decimal("0.01"))
    else:
        shares = pos.shares

    shares = min(shares, pos.shares)
    if shares < MIN_LIMIT_ORDER_SHARES:
        if pos.shares >= MIN_LIMIT_ORDER_SHARES:
            shares = MIN_LIMIT_ORDER_SHARES
        else:
            shares = pos.shares

    dollars_approx = shares * bid
    if dollars_approx >= SLIPPAGE_THRESHOLD:
        exec_price = max(bid - SLIPPAGE_PER_SHARE, Decimal("0.01"))
    else:
        exec_price = bid

    dollars = shares * exec_price
    if dollars < MIN_OUR_TRADE:
        return self._skip("sell_too_small")

    self.sells += 1
    return TradeDecision.sell(dollars, shares, exec_price)


# ======================================================================
# VARIANT B: BOOSTED SELL (apply boost to sell sizing too)
# ======================================================================
def boosted_sell_handler(self, event):
    """Apply the same boost to sell sizing that we apply to buys."""
    from src.strategies.profit_taker.strategy import (
        IGNORE_LEADER_MINISELLS_PCT, MIN_LIMIT_ORDER_SHARES, MIN_OUR_TRADE,
        SLIPPAGE_THRESHOLD, SLIPPAGE_PER_SHARE
    )
    from ..base import TradeDecision
    trade, prices = event.trade, event.prices
    pos = self.portfolio.get(trade.token_id, trade.market_id,
                             __import__('src.strategies.utils', fromlist=['to_side']).to_side(trade.side))
    if pos.shares <= 0:
        return self._skip("no_position")
    bid = prices.bid
    if not bid or bid <= 0:
        return self._skip("no_price")
    if bid >= Decimal("1"):
        return self._skip("invalid_price")
    if self._is_leader_minisell(event):
        return self._skip("leader_minisell")

    # Boosted: apply scale_boost to sell sizing
    scaled = trade.dollars * self.scale_ratio * self.scale_boost
    shares = min((scaled / bid).quantize(Decimal("0.01")), pos.shares)

    if shares < MIN_LIMIT_ORDER_SHARES:
        if pos.shares >= MIN_LIMIT_ORDER_SHARES:
            shares = MIN_LIMIT_ORDER_SHARES
        else:
            shares = pos.shares

    dollars_approx = shares * bid
    if dollars_approx >= SLIPPAGE_THRESHOLD:
        exec_price = max(bid - SLIPPAGE_PER_SHARE, Decimal("0.01"))
    else:
        exec_price = bid

    dollars = shares * exec_price
    if dollars < MIN_OUR_TRADE:
        return self._skip("sell_too_small")

    self.sells += 1
    return TradeDecision.sell(dollars, shares, exec_price)


# ======================================================================
# VARIANT C: FULL EXIT (sell ALL when leader sells significantly)
# ======================================================================
def full_exit_handler(self, event):
    """When leader sells (non-mini), exit our entire position."""
    from src.strategies.profit_taker.strategy import (
        IGNORE_LEADER_MINISELLS_PCT, MIN_LIMIT_ORDER_SHARES, MIN_OUR_TRADE,
        SLIPPAGE_THRESHOLD, SLIPPAGE_PER_SHARE
    )
    from ..base import TradeDecision
    trade, prices = event.trade, event.prices
    pos = self.portfolio.get(trade.token_id, trade.market_id,
                             __import__('src.strategies.utils', fromlist=['to_side']).to_side(trade.side))
    if pos.shares <= 0:
        return self._skip("no_position")
    bid = prices.bid
    if not bid or bid <= 0:
        return self._skip("no_price")
    if bid >= Decimal("1"):
        return self._skip("invalid_price")
    if self._is_leader_minisell(event):
        return self._skip("leader_minisell")

    # Full exit: sell everything
    shares = pos.shares

    dollars_approx = shares * bid
    if dollars_approx >= SLIPPAGE_THRESHOLD:
        exec_price = max(bid - SLIPPAGE_PER_SHARE, Decimal("0.01"))
    else:
        exec_price = bid

    dollars = shares * exec_price
    if dollars < MIN_OUR_TRADE:
        return self._skip("sell_too_small")

    self.sells += 1
    return TradeDecision.sell(dollars, shares, exec_price)


def run_hour(hour_file, sell_variant=None, overrides=None):
    """Run one hour with optional sell variant."""
    set_base_params()
    if overrides:
        for k, v in overrides.items():
            setattr(pt_module, k, v)

    # Apply sell variant by monkey-patching
    if sell_variant == "proportional":
        ProfitTakerStrategy._handle_leader_sell = proportional_sell
    elif sell_variant == "boosted":
        ProfitTakerStrategy._handle_leader_sell = boosted_sell
    elif sell_variant == "full_exit":
        ProfitTakerStrategy._handle_leader_sell = full_exit
    else:
        ProfitTakerStrategy._handle_leader_sell = _original_handle_leader_sell

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except Exception:
        return None
    if count == 0:
        return None

    config = replayer._merge_config()
    strategy.initialize(StrategyConfig.from_dict(config))
    strategy.on_session_start()

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour
        decision = strategy.on_event(event)
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    # Resolution
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

    pnl = float(strategy.cash) - 100.0
    return {"pnl": round(pnl, 2), "buys": strategy.buys, "sells": strategy.sells}


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


def discover_hours():
    base = Path("data/sessions")
    train, test, holdout = [], [], []
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                if session in TRAIN_SESSIONS:
                    train.append((session, utc_h, hf))
                elif session in TEST_SESSIONS:
                    test.append((session, utc_h, hf))
                elif session in HOLDOUT_SESSIONS:
                    holdout.append((session, utc_h, hf))
    return train, test, holdout


def compute_stats(pnl_list):
    if not pnl_list:
        return 0, 0, 0, 0, 0, 0, 0
    total = sum(pnl_list)
    avg = total / len(pnl_list)
    wins = sum(1 for p in pnl_list if p > 0)
    losses = sum(1 for p in pnl_list if p < 0)
    wr = wins / max(1, wins + losses) * 100
    std = statistics.stdev(pnl_list) if len(pnl_list) > 1 else 0
    sharpe = avg / std if std > 0 else 0
    return total, avg, wr, wins, losses, std, sharpe


# Standalone versions that don't use relative imports
def proportional_sell(self, event):
    """When leader sells X% of their position, we sell X% of ours."""
    trade, prices = event.trade, event.prices
    from src.strategies.utils import to_side
    pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
    if pos.shares <= 0:
        return self._skip("no_position")
    bid = prices.bid
    if not bid or bid <= 0:
        return self._skip("no_price")
    if bid >= Decimal("1"):
        return self._skip("invalid_price")
    if self._is_leader_minisell(event):
        return self._skip("leader_minisell")

    # Loss protection (same as original)
    if bid <= trade.price and pos.avg_price > 0 and bid < pos.avg_price:
        lp = self.leader_positions.get(trade.token_id)
        if lp and lp.get("shares", 0) > 0:
            leader_avg = lp["cost_basis"] / lp["shares"] if lp["shares"] > 0 else Decimal("0")
            if leader_avg > 0 and trade.price >= leader_avg:
                return self._skip("leader_profit_our_loss")

    # PROPORTIONAL: sell same % as leader
    lp = self.leader_positions.get(trade.token_id)
    if lp and lp.get("shares", 0) > 0:
        leader_sell_pct = min(trade.shares / lp["shares"], Decimal("1"))
        shares = (pos.shares * leader_sell_pct).quantize(Decimal("0.01"))
    else:
        shares = pos.shares

    shares = min(shares, pos.shares)
    if shares < pt_module.MIN_LIMIT_ORDER_SHARES:
        if pos.shares >= pt_module.MIN_LIMIT_ORDER_SHARES:
            shares = pt_module.MIN_LIMIT_ORDER_SHARES
        else:
            shares = pos.shares

    dollars_approx = shares * bid
    exec_price = max(bid - pt_module.SLIPPAGE_PER_SHARE, Decimal("0.01")) if dollars_approx >= pt_module.SLIPPAGE_THRESHOLD else bid
    dollars = shares * exec_price
    if dollars < pt_module.MIN_OUR_TRADE:
        return self._skip("sell_too_small")

    self.sells += 1
    from src.strategies.base import TradeDecision
    return TradeDecision.sell(dollars, shares, exec_price)


def boosted_sell(self, event):
    """Apply boost to sell sizing (match buy sizing)."""
    trade, prices = event.trade, event.prices
    from src.strategies.utils import to_side
    pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
    if pos.shares <= 0:
        return self._skip("no_position")
    bid = prices.bid
    if not bid or bid <= 0:
        return self._skip("no_price")
    if bid >= Decimal("1"):
        return self._skip("invalid_price")
    if self._is_leader_minisell(event):
        return self._skip("leader_minisell")

    # Loss protection
    if bid <= trade.price and pos.avg_price > 0 and bid < pos.avg_price:
        lp = self.leader_positions.get(trade.token_id)
        if lp and lp.get("shares", 0) > 0:
            leader_avg = lp["cost_basis"] / lp["shares"] if lp["shares"] > 0 else Decimal("0")
            if leader_avg > 0 and trade.price >= leader_avg:
                return self._skip("leader_profit_our_loss")

    # BOOSTED: apply boost to sell sizing
    scaled = trade.dollars * self.scale_ratio * self.scale_boost
    shares = min((scaled / bid).quantize(Decimal("0.01")), pos.shares)

    if shares < pt_module.MIN_LIMIT_ORDER_SHARES:
        if pos.shares >= pt_module.MIN_LIMIT_ORDER_SHARES:
            shares = pt_module.MIN_LIMIT_ORDER_SHARES
        else:
            shares = pos.shares

    dollars_approx = shares * bid
    exec_price = max(bid - pt_module.SLIPPAGE_PER_SHARE, Decimal("0.01")) if dollars_approx >= pt_module.SLIPPAGE_THRESHOLD else bid
    dollars = shares * exec_price
    if dollars < pt_module.MIN_OUR_TRADE:
        return self._skip("sell_too_small")

    self.sells += 1
    from src.strategies.base import TradeDecision
    return TradeDecision.sell(dollars, shares, exec_price)


def full_exit(self, event):
    """Exit entire position when leader sells."""
    trade, prices = event.trade, event.prices
    from src.strategies.utils import to_side
    pos = self.portfolio.get(trade.token_id, trade.market_id, to_side(trade.side))
    if pos.shares <= 0:
        return self._skip("no_position")
    bid = prices.bid
    if not bid or bid <= 0:
        return self._skip("no_price")
    if bid >= Decimal("1"):
        return self._skip("invalid_price")
    if self._is_leader_minisell(event):
        return self._skip("leader_minisell")

    # Full exit: sell everything
    shares = pos.shares
    dollars_approx = shares * bid
    exec_price = max(bid - pt_module.SLIPPAGE_PER_SHARE, Decimal("0.01")) if dollars_approx >= pt_module.SLIPPAGE_THRESHOLD else bid
    dollars = shares * exec_price
    if dollars < pt_module.MIN_OUR_TRADE:
        return self._skip("sell_too_small")

    self.sells += 1
    from src.strategies.base import TradeDecision
    return TradeDecision.sell(dollars, shares, exec_price)


VARIANTS = [
    ("BASELINE (broken sell)", None, {}),
    ("PROPORTIONAL sell", "proportional", {}),
    ("BOOSTED sell", "boosted", {}),
    ("FULL EXIT sell", "full_exit", {}),

    # Proportional + skip combos
    ("PROP+skip=0.50", "proportional", {"SKIP_PRICE_LOW": Decimal("0.50")}),
    ("PROP+skip=0.55", "proportional", {"SKIP_PRICE_LOW": Decimal("0.55")}),

    # Boosted + skip combos
    ("BOOST+skip=0.50", "boosted", {"SKIP_PRICE_LOW": Decimal("0.50")}),
    ("BOOST+skip=0.55", "boosted", {"SKIP_PRICE_LOW": Decimal("0.55")}),

    # Full exit + skip combos
    ("FULL+skip=0.50", "full_exit", {"SKIP_PRICE_LOW": Decimal("0.50")}),
    ("FULL+skip=0.55", "full_exit", {"SKIP_PRICE_LOW": Decimal("0.55")}),

    # Proportional with different boosts
    ("PROP+boost=10", "proportional", {"SCALE_BOOST": Decimal("10")}),
    ("PROP+boost=12", "proportional", {"SCALE_BOOST": Decimal("12")}),

    # Proportional + tighter drawdown
    ("PROP+dd_10/20", "proportional", {"DRAWDOWN_REDUCE_THRESHOLD": Decimal("10"),
                                        "DRAWDOWN_STOP_THRESHOLD": Decimal("20")}),

    # Best combos
    ("PROP+skip0.50+boost10", "proportional", {"SKIP_PRICE_LOW": Decimal("0.50"), "SCALE_BOOST": Decimal("10")}),
    ("PROP+skip0.55+boost10", "proportional", {"SKIP_PRICE_LOW": Decimal("0.55"), "SCALE_BOOST": Decimal("10")}),
    ("FULL+skip0.50+boost10", "full_exit", {"SKIP_PRICE_LOW": Decimal("0.50"), "SCALE_BOOST": Decimal("10")}),
]


def main():
    train, test, holdout = discover_hours()
    print(f"TRAIN: {len(train)} hours | TEST: {len(test)} hours | HOLDOUT: {len(holdout)} hours")
    print(f"Total variants: {len(VARIANTS)}\n")

    all_results = {name: {"train": [], "test": [], "holdout": []}
                   for name, _, _ in VARIANTS}

    for idx, (session, utc_h, hf) in enumerate(train):
        for vname, sell_var, ovr in VARIANTS:
            ProfitTakerStrategy._handle_leader_sell = _original_handle_leader_sell
            r = run_hour(hf, sell_variant=sell_var, overrides=ovr)
            if r:
                all_results[vname]["train"].append(r["pnl"])
        if (idx + 1) % 10 == 0:
            print(f"  TRAIN: {idx+1}/{len(train)}...")

    for idx, (session, utc_h, hf) in enumerate(test):
        for vname, sell_var, ovr in VARIANTS:
            ProfitTakerStrategy._handle_leader_sell = _original_handle_leader_sell
            r = run_hour(hf, sell_variant=sell_var, overrides=ovr)
            if r:
                all_results[vname]["test"].append(r["pnl"])
        if (idx + 1) % 10 == 0:
            print(f"  TEST: {idx+1}/{len(test)}...")

    for idx, (session, utc_h, hf) in enumerate(holdout):
        for vname, sell_var, ovr in VARIANTS:
            ProfitTakerStrategy._handle_leader_sell = _original_handle_leader_sell
            r = run_hour(hf, sell_variant=sell_var, overrides=ovr)
            if r:
                all_results[vname]["holdout"].append(r["pnl"])
        if (idx + 1) % 5 == 0:
            print(f"  HOLDOUT: {idx+1}/{len(holdout)}...")

    # Restore
    ProfitTakerStrategy._handle_leader_sell = _original_handle_leader_sell

    print(f"\n{'='*150}")
    print(f"  SELL SIZING FIX EXPERIMENTS — sorted by Combined Sharpe")
    print(f"{'='*150}")
    print(f"  {'Variant':<28} | {'TR $':>6} {'TShp':>6} {'TWR':>4} | {'TE $':>6} {'TShp':>6} {'TWR':>4} | {'COMB':>6} {'CShp':>6} | {'HOLD $':>6} {'HShp':>6}")
    print("-" * 150)

    rows = []
    for vname, _, _ in VARIANTS:
        d = all_results[vname]
        tr_tot, _, tr_wr, _, _, _, tr_shp = compute_stats(d["train"])
        te_tot, _, te_wr, _, _, _, te_shp = compute_stats(d["test"])
        c_all = d["train"] + d["test"]
        c_tot, _, _, _, _, _, c_shp = compute_stats(c_all)
        h_tot, _, _, _, _, _, h_shp = compute_stats(d["holdout"])
        rows.append((c_shp, vname, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp))

    rows.sort(key=lambda x: x[0], reverse=True)
    best_name = rows[0][1] if rows else ""
    for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp in rows:
        robust = tr_shp > 0 and te_shp > 0
        marker = " <-- BEST" if name == best_name else (" ***" if robust and te_shp > 0.01 else "")
        h_str = f"${h_tot:>+4.0f} {h_shp:>+5.3f}" if h_tot != 0 else "   -     -"
        print(f"  {name:<28} | ${tr_tot:>+4.0f} {tr_shp:>+5.3f} {tr_wr:>3.0f}% | ${te_tot:>+4.0f} {te_shp:>+5.3f} {te_wr:>3.0f}% | ${c_tot:>+4.0f} {c_shp:>+5.3f} | {h_str}{marker}")

    # Robust summary
    robust_rows = [r for r in rows if r[3] > 0 and r[6] > 0]  # tr_shp > 0 and te_shp > 0
    if robust_rows:
        print(f"\n  ROBUST (positive on BOTH train & test): {len(robust_rows)}")
        for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp in robust_rows:
            h_str = f"holdout=${h_tot:>+.0f}" if h_tot != 0 else "holdout=N/A"
            print(f"    {name:<28}: combined=${c_tot:>+.0f} Sharpe={c_shp:>+.3f}  {h_str}")

    # Triple
    triple = [r for r in rows if r[3] > 0 and r[6] > 0 and r[9] > 0]
    if triple:
        print(f"\n  TRIPLE-VALIDATED: {len(triple)}")
        for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, h_tot, h_shp in triple:
            print(f"    {name:<28}: train=${tr_tot:>+.0f} test=${te_tot:>+.0f} holdout=${h_tot:>+.0f} Sharpe={c_shp:>+.3f}")


if __name__ == "__main__":
    main()
