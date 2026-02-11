"""Advanced experiments based on deep analysis findings.

KEY INSIGHTS:
1. Mid-hour selling: +$323, Resolution: -$135 (we make money selling, lose holding)
2. Entry $0.45-$0.65: 32-39% WR, loses $69 (mid-hour sells good, resolution kills)
3. Entry $0.65+: 71-84% WR, makes $256 (sweet spot)
4. Late entries (min 45-60): 87% WR (leader most accurate late)
5. Stop-losses HURT, time-based exits HURT
6. Leader loses on low-price entries (-$4,591)

EXPERIMENTS:
A. Higher skip_low thresholds (0.55, 0.60, 0.65, 0.70)
B. Tiered boost: lower boost for mid-price entries, higher for high-price
C. Late-entry bonus: bigger positions for minute 40+
D. Combined: best skip_low + tiered boost + late bonus
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
import src.strategies.profit_taker.strategy as pt_module

CONFIG_OVERRIDES = {
    "scaling.our_capital": 50,
    "scaling.hourly_budget": 45,
    "scaling.k_factor": 1,
    "scaling.leader_estimated_capital": 900,
}

TRAIN_SESSIONS = {"2026-02-03/05-56", "2026-02-04/02-35", "2026-02-06/05-30"}
TEST_SESSIONS = {"2026-02-05/03-58", "2026-02-05/22-15", "2026-02-07/05-52"}


def set_base_params():
    """Set base params (current best) before each experiment."""
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


# ---- EXPERIMENT: TIERED BOOST ----
# Monkey-patch the strategy to use different boost based on entry price
_original_handle_leader_buy = None

def make_tiered_buy_handler(low_boost, mid_boost, high_boost, late_boost_mult=Decimal("1")):
    """Create a buy handler with tiered boost by entry price and optional late bonus."""
    def _tiered_handle_leader_buy(self, event):
        trade, prices = event.trade, event.prices
        ask = prices.ask
        if ask and ask > 0 and ask < Decimal("1"):
            # Tiered boost by price
            if ask < Decimal("0.55"):
                self.scale_boost = low_boost
            elif ask < Decimal("0.65"):
                self.scale_boost = mid_boost
            else:
                self.scale_boost = high_boost

            # Late-entry bonus
            minute = event.trade.timestamp.minute
            if minute >= 40 and late_boost_mult > Decimal("1"):
                self.scale_boost = self.scale_boost * late_boost_mult

        return _original_handle_leader_buy(self, event)
    return _tiered_handle_leader_buy


def run_hour(hour_file, variant_setup=None):
    """Run one hour with optional variant setup function."""
    set_base_params()
    if variant_setup:
        variant_setup()

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
    train_hours = []
    test_hours = []
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h >= 0:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                if session in TRAIN_SESSIONS:
                    train_hours.append((session, utc_h, hf))
                elif session in TEST_SESSIONS:
                    test_hours.append((session, utc_h, hf))
    return train_hours, test_hours


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


def main():
    global _original_handle_leader_buy
    from src.strategies.profit_taker.strategy import ProfitTakerStrategy
    _original_handle_leader_buy = ProfitTakerStrategy._handle_leader_buy

    train_hours, test_hours = discover_hours()
    print(f"TRAIN: {len(train_hours)} hours | TEST: {len(test_hours)} hours\n")

    # =========================================================
    # Define all variants
    # =========================================================
    variants = []

    # --- A: BASELINE ---
    def v_baseline():
        pass
    variants.append(("BASELINE (skip=0.45)", v_baseline))

    # --- B: HIGHER SKIP_LOW ---
    for sl in ["0.50", "0.55", "0.60", "0.65", "0.70"]:
        def make_v(val):
            def v():
                pt_module.SKIP_PRICE_LOW = Decimal(val)
            return v
        variants.append((f"skip={sl}", make_v(sl)))

    # --- C: HIGHER SKIP + HIGHER BOOST (compensate for fewer trades) ---
    for sl, boost in [("0.55", "10"), ("0.55", "12"), ("0.60", "10"), ("0.60", "12"),
                       ("0.65", "10"), ("0.65", "12"), ("0.65", "14")]:
        def make_v(s, b):
            def v():
                pt_module.SKIP_PRICE_LOW = Decimal(s)
                pt_module.SCALE_BOOST = Decimal(b)
            return v
        variants.append((f"skip={sl}+boost={boost}", make_v(sl, boost)))

    # --- D: HIGHER SKIP + HIGHER MIN_PCT (both filters) ---
    for sl, mp in [("0.55", "2.5"), ("0.55", "3.0"), ("0.60", "2.5"), ("0.65", "2.5")]:
        def make_v(s, m):
            def v():
                pt_module.SKIP_PRICE_LOW = Decimal(s)
                pt_module.MIN_LEADER_TRADE_PCT = Decimal(m)
            return v
        variants.append((f"skip={sl}+pct={mp}", make_v(sl, mp)))

    # --- E: TIERED BOOST (lower for mid-price, higher for high-price) ---
    tiered_configs = [
        ("tier_0/4/8", Decimal("0"), Decimal("4"), Decimal("8"), Decimal("1")),
        ("tier_0/4/10", Decimal("0"), Decimal("4"), Decimal("10"), Decimal("1")),
        ("tier_0/6/10", Decimal("0"), Decimal("6"), Decimal("10"), Decimal("1")),
        ("tier_4/6/10", Decimal("4"), Decimal("6"), Decimal("10"), Decimal("1")),
        ("tier_0/4/12", Decimal("0"), Decimal("4"), Decimal("12"), Decimal("1")),
        ("tier_0/0/10", Decimal("0"), Decimal("0"), Decimal("10"), Decimal("1")),
        ("tier_0/0/12", Decimal("0"), Decimal("0"), Decimal("12"), Decimal("1")),
    ]
    for name, lb, mb, hb, lm in tiered_configs:
        def make_v(low_b, mid_b, high_b, late_m):
            def v():
                pt_module.SKIP_PRICE_LOW = Decimal("0.45")  # Keep 0.45 skip
                ProfitTakerStrategy._handle_leader_buy = make_tiered_buy_handler(low_b, mid_b, high_b, late_m)
            return v
        variants.append((name, make_v(lb, mb, hb, lm)))

    # --- F: LATE-ENTRY BONUS (bigger positions for min 40+) ---
    for late_mult in ["1.5", "2.0"]:
        def make_v(lm):
            def v():
                pt_module.SKIP_PRICE_LOW = Decimal("0.45")
                ProfitTakerStrategy._handle_leader_buy = make_tiered_buy_handler(
                    Decimal("8"), Decimal("8"), Decimal("8"), Decimal(lm)
                )
            return v
        variants.append((f"late_bonus={lm}x", make_v(late_mult)))

    # --- G: BEST COMBOS: skip + tiered + late ---
    combo_configs = [
        ("skip=0.55+tier_0/8/10", "0.55", Decimal("0"), Decimal("8"), Decimal("10"), Decimal("1")),
        ("skip=0.60+tier_0/8/10", "0.60", Decimal("0"), Decimal("8"), Decimal("10"), Decimal("1")),
        ("skip=0.55+tier_0/8/12", "0.55", Decimal("0"), Decimal("8"), Decimal("12"), Decimal("1")),
        ("skip=0.60+tier_0/8/12+late1.5", "0.60", Decimal("0"), Decimal("8"), Decimal("12"), Decimal("1.5")),
        ("skip=0.55+late1.5", "0.55", Decimal("8"), Decimal("8"), Decimal("8"), Decimal("1.5")),
        ("skip=0.65+boost12+late1.5", "0.65", Decimal("12"), Decimal("12"), Decimal("12"), Decimal("1.5")),
    ]
    for name, sl, lb, mb, hb, lm in combo_configs:
        def make_v(skip, low_b, mid_b, high_b, late_m):
            def v():
                pt_module.SKIP_PRICE_LOW = Decimal(skip)
                ProfitTakerStrategy._handle_leader_buy = make_tiered_buy_handler(low_b, mid_b, high_b, late_m)
            return v
        variants.append((name, make_v(sl, lb, mb, hb, lm)))

    # --- H: TIGHTER PROFIT TARGETS for low entries ---
    for pt_mid, pt_low in [("15", "25"), ("20", "30"), ("10", "20")]:
        def make_v(pm, pl):
            def v():
                pt_module.PROFIT_TARGET_MID = Decimal(pm)
                pt_module.PROFIT_TARGET_LOW = Decimal(pl)
            return v
        variants.append((f"PT_mid={pt_mid}_low={pt_low}", make_v(pt_mid, pt_low)))

    # --- I: TIGHTER TARGETS + SKIP ---
    for sl, pt_mid in [("0.55", "15"), ("0.55", "20"), ("0.60", "15")]:
        def make_v(s, pm):
            def v():
                pt_module.SKIP_PRICE_LOW = Decimal(s)
                pt_module.PROFIT_TARGET_MID = Decimal(pm)
                pt_module.PROFIT_TARGET_LOW = Decimal("30")
            return v
        variants.append((f"skip={sl}+PT={pt_mid}", make_v(sl, pt_mid)))

    print(f"Total variants: {len(variants)}\n")

    # =========================================================
    # Run experiments
    # =========================================================
    all_results = {name: {"train": [], "test": []} for name, _ in variants}

    for idx, (session, utc_h, hf) in enumerate(train_hours):
        for name, setup_fn in variants:
            # Restore original buy handler before each run
            ProfitTakerStrategy._handle_leader_buy = _original_handle_leader_buy
            r = run_hour(hf, setup_fn)
            if r:
                all_results[name]["train"].append(r["pnl"])
        if (idx + 1) % 10 == 0:
            print(f"  TRAIN: {idx+1}/{len(train_hours)}...")

    for idx, (session, utc_h, hf) in enumerate(test_hours):
        for name, setup_fn in variants:
            ProfitTakerStrategy._handle_leader_buy = _original_handle_leader_buy
            r = run_hour(hf, setup_fn)
            if r:
                all_results[name]["test"].append(r["pnl"])
        if (idx + 1) % 10 == 0:
            print(f"  TEST: {idx+1}/{len(test_hours)}...")

    # Restore original
    ProfitTakerStrategy._handle_leader_buy = _original_handle_leader_buy

    # =========================================================
    # Display results
    # =========================================================
    print(f"\n{'='*130}")
    print(f"  ADVANCED EXPERIMENTS — sorted by Combined Sharpe")
    print(f"{'='*130}")
    print(f"\n  {'Variant':<32} | {'TR $':>6} {'TRShp':>6} {'TRWR':>5} | {'TE $':>6} {'TEShp':>6} {'TEWR':>5} | {'COMB $':>7} {'CShp':>6} {'CWR':>5}")
    print("-" * 130)

    rows = []
    for name, _ in variants:
        data = all_results[name]
        tr_tot, _, tr_wr, _, _, _, tr_shp = compute_stats(data["train"])
        te_tot, _, te_wr, _, _, _, te_shp = compute_stats(data["test"])
        c_all = data["train"] + data["test"]
        c_tot, _, c_wr, _, _, _, c_shp = compute_stats(c_all)
        rows.append((c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, c_wr))

    rows.sort(key=lambda x: x[0], reverse=True)
    best = rows[0][1] if rows else ""
    for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, c_wr in rows:
        consistent = te_shp > 0 and tr_shp > 0
        marker = " <-- BEST" if name == best else (" ***" if consistent and te_shp > 0.05 else "")
        print(f"  {name:<32} | ${tr_tot:>+4.0f} {tr_shp:>+5.3f} {tr_wr:>4.0f}% | ${te_tot:>+4.0f} {te_shp:>+5.3f} {te_wr:>4.0f}% | ${c_tot:>+5.0f} {c_shp:>+5.3f} {c_wr:>4.0f}%{marker}")

    # Also show top 10 with train>0 and test>0 (most robust)
    robust = [(c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, c_wr)
              for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, c_wr in rows
              if tr_tot > 0 and te_tot > 0]
    if robust:
        print(f"\n{'='*130}")
        print(f"  ROBUST VARIANTS (positive on BOTH train AND test) — {len(robust)} found:")
        print(f"{'='*130}")
        for c_shp, name, tr_tot, tr_shp, tr_wr, te_tot, te_shp, te_wr, c_tot, c_wr in robust:
            print(f"  {name:<32} | ${tr_tot:>+4.0f} {tr_shp:>+5.3f} {tr_wr:>4.0f}% | ${te_tot:>+4.0f} {te_shp:>+5.3f} {te_wr:>4.0f}% | ${c_tot:>+5.0f} {c_shp:>+5.3f} {c_wr:>4.0f}%")

    # Save results
    results_json = {}
    for name, _ in variants:
        data = all_results[name]
        tr_tot, _, tr_wr, _, _, _, tr_shp = compute_stats(data["train"])
        te_tot, _, te_wr, _, _, _, te_shp = compute_stats(data["test"])
        c_all = data["train"] + data["test"]
        c_tot, _, c_wr, _, _, _, c_shp = compute_stats(c_all)
        results_json[name] = {
            "train_pnl": round(tr_tot, 2), "train_sharpe": round(tr_shp, 4),
            "train_wr": round(tr_wr, 1),
            "test_pnl": round(te_tot, 2), "test_sharpe": round(te_shp, 4),
            "test_wr": round(te_wr, 1),
            "combined_pnl": round(c_tot, 2), "combined_sharpe": round(c_shp, 4),
            "combined_wr": round(c_wr, 1),
        }
    with open("experiment_advanced_results.json", "w") as f:
        json.dump(results_json, f, indent=2)
    print(f"\nResults saved to experiment_advanced_results.json")


if __name__ == "__main__":
    main()
