"""Verify that strategy.py implementation matches experiment_combos.py results.

Expected: bgt50+noLo500 => Sharpe ~0.345, PnL ~$323.52, all splits positive.
"""
import sys
import json
import math
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer

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


def run_hour(hour_file, capital=50):
    """Run one hour using strategy.py as-is (no overrides)."""
    config_overrides = {
        "scaling.our_capital": capital,
        "scaling.hourly_budget": 50,
        "scaling.k_factor": 1,
        "scaling.leader_estimated_capital": 900,
    }

    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=config_overrides)
    try:
        count = replayer.load()
    except Exception:
        return None
    if count == 0:
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
    return round(pnl, 2)


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

    print(f"Verifying strategy.py across {len(all_hours)} hours...\n")

    split_pnl = {"TRAIN": [], "TEST": [], "HOLDOUT": []}
    for _, _, hf, split in all_hours:
        pnl = run_hour(hf)
        if pnl is not None:
            split_pnl[split].append(pnl)

    all_pnls = split_pnl["TRAIN"] + split_pnl["TEST"] + split_pnl["HOLDOUT"]
    n = len(all_pnls)
    mean = sum(all_pnls) / n
    var = sum((x - mean)**2 for x in all_pnls) / max(1, n-1)
    std = math.sqrt(var) if var > 0 else 0.001
    sharpe = mean / std
    total = sum(all_pnls)
    winning = sum(1 for x in all_pnls if x > 0)
    s = sorted(all_pnls)

    train = sum(split_pnl["TRAIN"])
    test = sum(split_pnl["TEST"])
    hold = sum(split_pnl["HOLDOUT"])

    print(f"  Total PnL:  ${total:.2f}")
    print(f"  Sharpe:     {sharpe:.4f}")
    print(f"  WR:         {winning/n*100:.1f}%")
    print(f"  MaxLoss:    ${s[0]:.2f}")
    print(f"  Train:      ${train:.2f}")
    print(f"  Test:       ${test:.2f}")
    print(f"  Holdout:    ${hold:.2f}")
    print(f"  Robust:     {'YES' if train > 0 and test > 0 and hold > 0 else 'NO'}")

    print(f"\n  Expected: PnL ~$323.52, Sharpe ~0.345")
    pnl_match = abs(total - 323.52) < 1.0
    sharpe_match = abs(sharpe - 0.345) < 0.01
    print(f"  PnL match:    {'YES' if pnl_match else 'NO'} (delta=${total - 323.52:.2f})")
    print(f"  Sharpe match: {'YES' if sharpe_match else 'NO'} (delta={sharpe - 0.345:.4f})")

    if pnl_match and sharpe_match:
        print(f"\n  VERIFICATION PASSED")
    else:
        print(f"\n  VERIFICATION FAILED - check implementation")


if __name__ == "__main__":
    main()
