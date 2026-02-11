"""Deep gap analysis: WHERE exactly do we lose money vs the leader?

Tracks every trade the leader makes and what happened to it:
1. Did we follow the buy? If not, why?
2. Did we follow the sell? If not, why?
3. What was the PnL of each leader trade (scaled to our size)?
"""
import sys
import json
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


def analyze_gap_hour(hour_file):
    """Trace every leader trade and our response to it."""
    # 1) Calculate leader's actual PnL using raw data
    leader_positions = {}
    leader_total_pnl = Decimal("0")
    last_prices = {}
    leader_buys = 0
    leader_sells = 0
    leader_trade_sizes = []

    # Parse events to understand leader
    events_raw = []
    with open(hour_file, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
                events_raw.append(obj)
            except:
                continue

    for obj in events_raw:
        if obj.get("type") == "price_snapshot":
            for tid, pd in obj.get("prices", {}).items():
                if pd.get("bid"):
                    last_prices[tid] = Decimal(str(pd["bid"]))
            continue

        if obj.get("type") != "leader_trade":
            continue

        lt = obj.get("leader_trade", {})
        action = lt.get("action", "")
        token_id = lt.get("token_id", "")
        dollars = Decimal(str(lt.get("leader_dollars", "0")))
        price = Decimal(str(lt.get("leader_price", "0")))
        shares = Decimal(str(lt.get("leader_shares", "0")))

        pc = obj.get("price_context", {})
        if pc and pc.get("bid"):
            last_prices[token_id] = Decimal(str(pc["bid"]))

        leader_trade_sizes.append(float(dollars))

        if action == "BUY":
            leader_buys += 1
            if token_id not in leader_positions:
                leader_positions[token_id] = {"shares": Decimal("0"), "cost_basis": Decimal("0")}
            lp = leader_positions[token_id]
            lp["shares"] += shares
            lp["cost_basis"] += dollars
        elif action == "SELL":
            leader_sells += 1
            lp = leader_positions.get(token_id)
            if lp and lp["shares"] > 0:
                sell_ratio = min(shares / lp["shares"], Decimal("1"))
                cost_for_sold = lp["cost_basis"] * sell_ratio
                proceeds = dollars
                leader_total_pnl += (proceeds - cost_for_sold)
                lp["cost_basis"] -= cost_for_sold
                lp["shares"] = max(Decimal("0"), lp["shares"] - shares)

    # Resolve remaining
    leader_resolve_pnl = Decimal("0")
    for tid, lp in leader_positions.items():
        if lp["shares"] <= 0:
            continue
        bid = last_prices.get(tid, Decimal("0.50"))
        res = Decimal("0.99") if bid >= Decimal("0.50") else Decimal("0.01")
        value = lp["shares"] * res
        leader_resolve_pnl += (value - lp["cost_basis"])

    leader_total_pnl += leader_resolve_pnl

    # 2) Run our strategy and track decisions
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
    strategy.on_session_start()

    our_buys = our_sells = 0
    our_total_deployed = Decimal("0")
    last_all_prices = {}

    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        last_all_prices = all_prices
        event.context['all_prices'] = all_prices
        decision = strategy.on_event(event)
        if decision.action == DecisionAction.BUY:
            our_buys += 1
            our_total_deployed += decision.dollars or Decimal("0")
            strategy.on_fill(event, decision)
        elif decision.action == DecisionAction.SELL:
            our_sells += 1
            strategy.on_fill(event, decision)

    # Resolve our positions
    for token_id, pos in list(strategy.portfolio.get_positions().items()):
        if pos.shares <= 0:
            continue
        price_snap = last_all_prices.get(token_id)
        if price_snap and price_snap.bid and price_snap.bid > 0:
            bid = price_snap.bid
        else:
            bid = strategy.our_entries.get(token_id, Decimal("0.50"))
        res = Decimal("0.99") if bid >= Decimal("0.50") else Decimal("0.01")
        strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res)
        strategy.cash += pos.shares * res

    our_pnl = float(strategy.cash) - float(Decimal("50") * Decimal("2"))

    # Calculate what our "fair share" PnL should be at our scale
    # scale_ratio * boost = 0.0556 * 8 = 0.444
    scale_factor = 0.0556 * 8  # 44.4% of leader
    expected_pnl = float(leader_total_pnl) * scale_factor

    # Trade size distribution
    if leader_trade_sizes:
        below_1 = sum(1 for s in leader_trade_sizes if s < 1)
        below_5 = sum(1 for s in leader_trade_sizes if s < 5)
        below_10 = sum(1 for s in leader_trade_sizes if s < 10)
        below_11 = sum(1 for s in leader_trade_sizes if s < 10.80)  # our MIN_LEADER_TRADE_PCT threshold
        total = len(leader_trade_sizes)
    else:
        below_1 = below_5 = below_10 = below_11 = total = 0

    return {
        "leader_pnl": float(leader_total_pnl),
        "leader_buys": leader_buys,
        "leader_sells": leader_sells,
        "leader_total_trades": total,
        "our_pnl": round(our_pnl, 2),
        "our_buys": our_buys,
        "our_sells": our_sells,
        "our_deployed": float(our_total_deployed),
        "expected_pnl": round(expected_pnl, 2),
        "trades_below_1": below_1,
        "trades_below_5": below_5,
        "trades_below_10": below_10,
        "trades_below_10.8": below_11,
        "skips": dict(strategy.skip_reasons),
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
                all_hours.append((session, utc_h, hf))

    # Analyze
    results = []
    for session, utc_h, hf in all_hours:
        r = analyze_gap_hour(hf)
        if r:
            r["session"] = session
            r["utc_hour"] = utc_h
            results.append(r)

    # Aggregate
    total_leader = sum(r["leader_pnl"] for r in results)
    total_us = sum(r["our_pnl"] for r in results)
    total_expected = sum(r["expected_pnl"] for r in results)
    total_leader_trades = sum(r["leader_total_trades"] for r in results)
    total_our_buys = sum(r["our_buys"] for r in results)
    total_our_sells = sum(r["our_sells"] for r in results)

    # Skip aggregation
    all_skips = defaultdict(int)
    for r in results:
        for reason, cnt in r.get("skips", {}).items():
            all_skips[reason] += cnt

    print(f"{'='*100}")
    print(f"  GAP ANALYSIS: {len(results)} hours")
    print(f"{'='*100}")

    print(f"\n  Leader total PnL:    ${total_leader:>+10.2f}")
    print(f"  Expected at scale:   ${total_expected:>+10.2f}  (leader * 44.4% scale)")
    print(f"  Our actual PnL:      ${total_us:>+10.2f}")
    print(f"  CAPTURE RATE:        {total_us/total_expected*100 if total_expected else 0:.1f}% of expected")
    print(f"  GAP (expected-actual): ${total_expected-total_us:>+10.2f}")

    print(f"\n  TRADE FLOW:")
    print(f"    Leader total events:  {total_leader_trades}")
    print(f"    Leader avg/hour:      {total_leader_trades/len(results):.1f}")
    print(f"    Our buys executed:    {total_our_buys}  ({total_our_buys/total_leader_trades*100:.1f}% of leader trades)")
    print(f"    Our sells executed:   {total_our_sells}")

    # Trade size distribution
    total_below_1 = sum(r["trades_below_1"] for r in results)
    total_below_5 = sum(r["trades_below_5"] for r in results)
    total_below_10 = sum(r["trades_below_10"] for r in results)
    total_below_11 = sum(r["trades_below_10.8"] for r in results)

    print(f"\n  LEADER TRADE SIZE DISTRIBUTION:")
    print(f"    < $1:     {total_below_1:>6}  ({total_below_1/total_leader_trades*100:.1f}%)")
    print(f"    < $5:     {total_below_5:>6}  ({total_below_5/total_leader_trades*100:.1f}%)")
    print(f"    < $10:    {total_below_10:>6}  ({total_below_10/total_leader_trades*100:.1f}%)")
    print(f"    < $10.80: {total_below_11:>6}  ({total_below_11/total_leader_trades*100:.1f}%)  <-- our MIN_LEADER_TRADE_PCT cutoff")
    print(f"    >= $10.80:{total_leader_trades-total_below_11:>6}  ({(total_leader_trades-total_below_11)/total_leader_trades*100:.1f}%)  <-- trades we CAN follow")

    print(f"\n  SKIP REASONS (where we lose edge):")
    total_skips = sum(all_skips.values())
    for reason, cnt in sorted(all_skips.items(), key=lambda x: -x[1])[:12]:
        pct = cnt / total_skips * 100
        print(f"    {reason:>25}: {cnt:>6} ({pct:>5.1f}%)")

    # Quantify the gap sources
    print(f"\n{'='*100}")
    print(f"  GAP SOURCE ANALYSIS")
    print(f"{'='*100}")

    # What % of leader's WINNING trades do we follow?
    # What % of leader's LOSING trades do we follow?
    print(f"\n  Expected at our scale: ${total_expected:+.2f}")
    print(f"  We actually made:     ${total_us:+.2f}")
    print(f"  The gap:              ${total_expected-total_us:+.2f}")
    print(f"\n  WHY the gap exists:")
    print(f"    1. We follow {total_our_buys} buys out of leader's {total_leader_trades} trades ({total_our_buys/total_leader_trades*100:.1f}%)")
    print(f"    2. {total_below_11} trades ({total_below_11/total_leader_trades*100:.1f}%) are below our $10.80 threshold")
    print(f"    3. Even at 44.4% scale, our $45/hour budget limits total deployment")
    print(f"    4. Leader recycles capital (80+ buys/hour) - we can only deploy once per token")

    # Per-session expected vs actual
    print(f"\n  PER-SESSION: Expected vs Actual")
    session_data = defaultdict(lambda: {"leader": 0, "expected": 0, "actual": 0, "hours": 0})
    for r in results:
        sd = session_data[r["session"]]
        sd["leader"] += r["leader_pnl"]
        sd["expected"] += r["expected_pnl"]
        sd["actual"] += r["our_pnl"]
        sd["hours"] += 1

    for session in sorted(session_data.keys()):
        sd = session_data[session]
        capture = sd["actual"] / sd["expected"] * 100 if sd["expected"] else 0
        print(f"    {session}: Leader=${sd['leader']:>+8.0f}  Expected=${sd['expected']:>+6.0f}  Actual=${sd['actual']:>+6.0f}  Capture={capture:>5.1f}%")


if __name__ == "__main__":
    main()
