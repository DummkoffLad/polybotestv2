"""Debug S2 H10 specifically - show what positions exist and what prices are used for resolution."""
import sys
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer

CONFIG_OVERRIDES = {
    "scaling.our_capital": 50,
    "scaling.hourly_budget": 45,
    "scaling.k_factor": 1,
    "scaling.leader_estimated_capital": 900,
}


def run_debug():
    # Run continuous on S2 full session, stop and inspect at H10->H11 boundary
    session_path = Path("data/sessions/2026-02-04/02-35.jsonl")
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(session_path, strategy, config_overrides=CONFIG_OVERRIDES)
    replayer.load()

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    current_hour = None
    last_all_prices = {}

    for i, event in enumerate(replayer.loader.events):
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event_hour = event.trade.timestamp.hour

        # At the H10->H11 boundary, inspect BEFORE and AFTER
        if current_hour == 10 and event_hour == 11:
            print("=" * 80)
            print("  H10 -> H11 BOUNDARY (CONTINUOUS)")
            print("=" * 80)
            print(f"\n  Cash before resolve: ${strategy.cash:.2f}")
            print(f"  Deployed: ${strategy.portfolio.get_total_deployed():.2f}")
            print(f"\n  Open positions at end of H10:")
            for tid, pos in strategy.portfolio.get_positions().items():
                if pos.shares <= 0:
                    continue
                # Price from LAST event of H10
                last_snap = last_all_prices.get(tid)
                last_bid = last_snap.bid if last_snap and last_snap.bid else Decimal("0")
                last_res = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")

                # Price from FIRST event of H11
                next_snap = all_prices.get(tid)
                next_bid = next_snap.bid if next_snap and next_snap.bid else Decimal("0")
                next_res = Decimal("0.99") if next_bid >= Decimal("0.50") else Decimal("0.01")

                cost = pos.cost_basis
                pnl_last = pos.shares * last_res - cost
                pnl_next = pos.shares * next_res - cost

                flip = "FLIP!" if last_res != next_res else "same"
                print(f"\n    Token: ...{tid[-8:]}")
                print(f"      Shares: {pos.shares}, Cost: ${cost:.2f}, Avg: ${pos.avg_price:.4f}")
                print(f"      Last H10 bid: {last_bid:.4f} -> res ${last_res} -> PnL ${pnl_last:+.2f}")
                print(f"      First H11 bid: {next_bid:.4f} -> res ${next_res} -> PnL ${pnl_next:+.2f}")
                print(f"      Difference: ${pnl_next - pnl_last:+.2f}  [{flip}]")

            print(f"\n  Strategy will use FIRST H11 prices for resolution.")
            print(f"  Independent run would use LAST H10 prices.\n")

            # Now let on_event process (which triggers liquidation)
            event.context['all_prices'] = all_prices
            decision = strategy.on_event(event)
            print(f"  Cash AFTER resolve: ${strategy.cash:.2f}")
            print(f"  Positions after: {len([p for p in strategy.portfolio.get_positions().values() if p.shares > 0])}")

            if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                strategy.on_fill(event, decision)
            break

        current_hour = event_hour
        last_all_prices = all_prices
        event.context['all_prices'] = all_prices
        decision = strategy.on_event(event)
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    # Also run independent H10 for comparison
    print("\n" + "=" * 80)
    print("  H10 INDEPENDENT (using last H10 prices)")
    print("=" * 80)

    # Find H10 hour file
    import json
    hour_files = sorted(Path("data/sessions/2026-02-04").glob("02-35_hour_*.jsonl"))
    h10_file = None
    for f in hour_files:
        with open(f, 'r') as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                    if obj.get("type") in ("leader_trade", "fill"):
                        ts = obj.get("timestamp", "")
                        if "T" in ts and int(ts.split("T")[1][:2]) == 10:
                            h10_file = f
                            break
                except:
                    continue
            if h10_file:
                break

    if not h10_file:
        print("  Could not find H10 hour file")
        return

    print(f"  File: {h10_file.name}")
    strategy2 = get_strategy("profit_taker")
    replayer2 = SessionReplayer(h10_file, strategy2, config_overrides=CONFIG_OVERRIDES)
    replayer2.load()
    config2 = replayer2._merge_config()
    strategy2.initialize(StrategyConfig.from_dict(config2))
    strategy2.on_session_start()

    last_prices2 = {}
    for i, event in enumerate(replayer2.loader.events):
        ap = replayer2.loader.get_all_prices_at_time(event.trade.timestamp)
        last_prices2 = ap
        event.context['all_prices'] = ap
        dec = strategy2.on_event(event)
        if dec.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy2.on_fill(event, dec)

    print(f"\n  Cash before resolve: ${strategy2.cash:.2f}")
    print(f"  Deployed: ${strategy2.portfolio.get_total_deployed():.2f}")
    print(f"\n  Open positions at end of H10:")
    for tid, pos in strategy2.portfolio.get_positions().items():
        if pos.shares <= 0:
            continue
        snap = last_prices2.get(tid)
        bid = snap.bid if snap and snap.bid else Decimal("0")
        res = Decimal("0.99") if bid >= Decimal("0.50") else Decimal("0.01")
        cost = pos.cost_basis
        pnl = pos.shares * res - cost
        print(f"    ...{tid[-8:]}: {pos.shares} shares, cost=${cost:.2f}, bid={bid:.4f}, res=${res}, PnL=${pnl:+.2f}")

    # Force resolve
    for tid, pos in list(strategy2.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        snap = last_prices2.get(tid)
        bid = snap.bid if snap and snap.bid else Decimal("0.50")
        res = Decimal("0.99") if bid >= Decimal("0.50") else Decimal("0.01")
        strategy2.cash += pos.shares * res
        strategy2.portfolio.apply_sell(tid, pos.market_id, pos.side, pos.shares, res)

    print(f"\n  Cash AFTER resolve: ${strategy2.cash:.2f}")
    print(f"  PnL: ${strategy2.cash - Decimal('100'):+.2f}")


if __name__ == "__main__":
    run_debug()
