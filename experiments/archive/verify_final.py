"""Quick verification of the final strategy parameters."""
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

CONFIG_OVERRIDES = {
    "scaling.our_capital": 50,
    "scaling.hourly_budget": 45,
    "scaling.k_factor": 1,
    "scaling.leader_estimated_capital": 900,
}


def liquidate_at_resolution(strategy, all_prices):
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        price_snap = all_prices.get(token_id)
        if price_snap and price_snap.bid is not None:
            last_bid = price_snap.bid
        else:
            last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
        res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
        dollars = pos.shares * res_price
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
        strategy.cash += dollars


def get_utc_hour_from_file(path):
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

    all_pnls = []
    session_pnls = defaultdict(list)
    skip_totals = defaultdict(int)

    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            utc_h = get_utc_hour_from_file(hf)
            if utc_h < 0:
                continue

            session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"

            strategy = get_strategy("profit_taker")
            replayer = SessionReplayer(hf, strategy, config_overrides=CONFIG_OVERRIDES)
            try:
                count = replayer.load()
            except:
                continue
            if count == 0:
                continue

            config = replayer._merge_config()
            strategy_config = StrategyConfig.from_dict(config)
            strategy.initialize(strategy_config)
            strategy.on_session_start()

            last_hour = None
            for i, event in enumerate(replayer.loader.events):
                all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
                event.context['all_prices'] = all_prices
                last_hour = event.trade.timestamp.hour
                decision = strategy.on_event(event)
                if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
                    strategy.on_fill(event, decision)

            # Use end-of-hour prices for resolution (matches live runner behavior)
            end_of_hour_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}
            liquidate_at_resolution(strategy, end_of_hour_prices)
            pnl = float(strategy.cash) - float(Decimal("50") * Decimal("2"))

            all_pnls.append(round(pnl, 2))
            session_pnls[session].append(round(pnl, 2))
            for reason, cnt in strategy.skip_reasons.items():
                skip_totals[reason] += cnt

    total = sum(all_pnls)
    avg = total / len(all_pnls)
    wins = sum(1 for p in all_pnls if p > 0)
    losses = sum(1 for p in all_pnls if p < 0)
    wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    std = statistics.stdev(all_pnls)
    sharpe = avg / std if std > 0 else 0

    print(f"{'='*80}")
    print(f"  FINAL STRATEGY VERIFICATION ({len(all_pnls)} hourly trials)")
    print(f"{'='*80}")
    print(f"\n  Total PnL:  ${total:+.2f}")
    print(f"  Avg/hour:   ${avg:+.2f}")
    print(f"  Win rate:   {wr:.1f}% ({wins}W/{losses}L)")
    print(f"  Std dev:    ${std:.2f}")
    print(f"  Sharpe:     {sharpe:+.3f}")
    print(f"  Max win:    ${max(all_pnls):+.2f}")
    print(f"  Max loss:   ${min(all_pnls):+.2f}")
    print(f"  >$20 loss:  {sum(1 for p in all_pnls if p < -20)} hours")
    print(f"  >$30 loss:  {sum(1 for p in all_pnls if p < -30)} hours")

    print(f"\n  PER-SESSION:")
    for session in sorted(session_pnls.keys()):
        pnls = session_pnls[session]
        s_total = sum(pnls)
        s_wins = sum(1 for p in pnls if p > 0)
        s_losses = sum(1 for p in pnls if p < 0)
        s_wr = s_wins / (s_wins + s_losses) * 100 if (s_wins + s_losses) > 0 else 0
        print(f"    {session}: ${s_total:>+7.2f}  {s_wins}W/{s_losses}L ({s_wr:.0f}%)  {len(pnls)} hours")

    print(f"\n  SKIP REASONS (top 10):")
    for reason, cnt in sorted(skip_totals.items(), key=lambda x: -x[1])[:10]:
        print(f"    {reason:>25}: {cnt}")


if __name__ == "__main__":
    main()
