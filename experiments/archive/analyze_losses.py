"""Analyze losing hours: what causes big losses?"""
import sys
import json
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

pt_module.MIN_LEADER_TRADE_PCT = Decimal("1.2")


def run_hour_detailed(hour_file: Path) -> dict:
    """Run a single hour and track detailed trade data."""
    strategy = get_strategy("profit_taker")
    replayer = SessionReplayer(hour_file, strategy, config_overrides=CONFIG_OVERRIDES)
    try:
        count = replayer.load()
    except:
        return None
    if count == 0:
        return None

    config = replayer._merge_config()
    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.scale_boost = Decimal("8")
    strategy.on_session_start()

    trades = []
    last_all_prices = {}

    for i, event in enumerate(replayer.loader.events):
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        last_all_prices = all_prices
        event.context['all_prices'] = all_prices
        decision = strategy.on_event(event)

        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            # Record pre-fill state for loss tracking
            pos = strategy.portfolio.get(event.trade.token_id, event.trade.market_id,
                                        __import__('src.strategies.utils', fromlist=['to_side']).to_side(event.trade.side))
            avg_before = pos.avg_price if pos.shares > 0 else Decimal("0")
            cash_before = float(strategy.cash)

            strategy.on_fill(event, decision)

            trades.append({
                "time": str(event.trade.timestamp),
                "minute": event.trade.timestamp.minute,
                "action": decision.action.value,
                "dollars": float(decision.dollars or 0),
                "price": float(decision.price or 0),
                "shares": float(decision.shares or 0),
                "token": event.trade.token_id[-8:],
                "avg_before": float(avg_before),
                "cash_after": float(strategy.cash),
                "cash_change": float(strategy.cash) - cash_before,
            })

    # Liquidate and track
    cash_before_resolve = float(strategy.cash)
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        price_snap = last_all_prices.get(token_id)
        if price_snap and price_snap.bid and price_snap.bid > 0:
            last_bid = price_snap.bid
        else:
            last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
        res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
        dollars = pos.shares * res_price
        cost = pos.cost_basis

        trades.append({
            "time": "RESOLVE",
            "minute": 59,
            "action": "RESOLVE",
            "dollars": float(dollars),
            "price": float(res_price),
            "shares": float(pos.shares),
            "token": token_id[-8:],
            "cost": float(cost),
            "pnl": float(dollars - cost),
            "bid_at_resolve": float(last_bid),
        })
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars

    initial = float(Decimal("50") * Decimal("2"))
    pnl = float(strategy.cash) - initial

    # Compute mid-hour realized losses
    mid_hour_losses = sum(t["cash_change"] for t in trades if t["action"] == "sell" and t["cash_change"] < 0) if trades else 0

    return {
        "pnl": round(pnl, 2),
        "trades": trades,
        "buys": sum(1 for t in trades if t["action"] == "buy"),
        "sells": sum(1 for t in trades if t["action"] == "sell"),
        "mid_hour_loss": round(mid_hour_losses, 2),
        "resolve_pnl": round(float(strategy.cash) - cash_before_resolve - (initial - cash_before_resolve), 2),
    }


def get_utc_hour_from_file(path: Path) -> int:
    with open(path, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
                if obj.get("type") in ("leader_trade", "fill"):
                    ts = obj.get("timestamp", "")
                    if "T" in ts:
                        return int(ts.split("T")[1][:2])
            except:
                continue
    return -1


def main():
    base = Path("data/sessions")
    all_hours = []

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h < 0:
                continue
            result = run_hour_detailed(hf)
            if result:
                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                all_hours.append({
                    "file": str(hf),
                    "session": session,
                    "utc_hour": utc_h,
                    **result,
                })

    # Sort by PnL ascending (worst first)
    all_hours.sort(key=lambda x: x["pnl"])

    print(f"{'='*100}")
    print(f"  WORST 15 HOURS (out of {len(all_hours)})")
    print(f"{'='*100}")

    for h in all_hours[:15]:
        print(f"\n  {h['session']} H{h['utc_hour']:02d}: PnL=${h['pnl']:+.2f}  (B{h['buys']}/S{h['sells']})")

        # Show trades
        for t in h["trades"]:
            if t["action"] == "buy":
                print(f"    min{t['minute']:02d} BUY  ${t['dollars']:.2f} @{t['price']:.4f}  token=...{t['token']}  cash=${t['cash_after']:.2f}")
            elif t["action"] == "sell":
                pnl_flag = "LOSS" if t["cash_change"] < 0 else "gain"
                print(f"    min{t['minute']:02d} SELL ${t['dollars']:.2f} @{t['price']:.4f}  token=...{t['token']}  cash=${t['cash_after']:.2f}  ({pnl_flag}: ${t['cash_change']:+.2f})")
            elif t["action"] == "RESOLVE":
                tag = "WIN" if t["pnl"] > 0 else "LOSE"
                print(f"    RESOLVE {t['token']}: ${t['dollars']:.2f} @{t['price']}  cost=${t['cost']:.2f}  PnL=${t['pnl']:+.2f}  bid={t['bid_at_resolve']:.4f} [{tag}]")

    # Summary statistics
    print(f"\n{'='*100}")
    print(f"  LOSS PATTERNS")
    print(f"{'='*100}")

    losing_hours = [h for h in all_hours if h["pnl"] < 0]
    winning_hours = [h for h in all_hours if h["pnl"] > 0]

    print(f"\n  Losing hours: {len(losing_hours)}")
    print(f"  Winning hours: {len(winning_hours)}")
    print(f"  Avg loss: ${sum(h['pnl'] for h in losing_hours)/len(losing_hours) if losing_hours else 0:.2f}")
    print(f"  Avg win:  ${sum(h['pnl'] for h in winning_hours)/len(winning_hours) if winning_hours else 0:.2f}")

    # Trades by minute bucket (early vs late)
    print(f"\n  TIMING ANALYSIS (buys by minute bucket):")
    early_wins = early_losses = late_wins = late_losses = 0
    for h in all_hours:
        buy_minutes = [t["minute"] for t in h["trades"] if t["action"] == "buy"]
        if not buy_minutes:
            continue
        avg_min = sum(buy_minutes) / len(buy_minutes)
        if avg_min < 30:
            if h["pnl"] > 0:
                early_wins += 1
            else:
                early_losses += 1
        else:
            if h["pnl"] > 0:
                late_wins += 1
            else:
                late_losses += 1

    total_early = early_wins + early_losses
    total_late = late_wins + late_losses
    print(f"    Early (<30min avg): {early_wins}W/{early_losses}L = {early_wins/total_early*100 if total_early else 0:.0f}% WR")
    print(f"    Late (>=30min avg): {late_wins}W/{late_losses}L = {late_wins/total_late*100 if total_late else 0:.0f}% WR")

    # Concentration analysis
    print(f"\n  CONCENTRATION ANALYSIS:")
    for h in all_hours[:10]:
        resolve_trades = [t for t in h["trades"] if t["action"] == "RESOLVE"]
        if resolve_trades:
            total_cost = sum(t["cost"] for t in resolve_trades)
            max_cost = max(t["cost"] for t in resolve_trades)
            concentration = max_cost / total_cost * 100 if total_cost > 0 else 0
            print(f"    {h['session']} H{h['utc_hour']:02d}: {len(resolve_trades)} pos, max={concentration:.0f}% of deployed, PnL=${h['pnl']:+.2f}")


if __name__ == "__main__":
    main()
