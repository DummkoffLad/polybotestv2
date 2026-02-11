"""Analyze how closely we follow the leader with the conviction filter.

Questions to answer:
1. How many of leader's trades do we follow vs skip? By skip reason?
2. Which TOKENS does leader trade that we never enter? What's their outcome?
3. What's the PnL of tokens we skip entirely vs tokens we enter?
4. Does market volatility affect our performance?
5. How much later do we enter vs the leader? What's the price gap?
6. Per-hour breakdown: which hours do we trade heavily vs sit out?
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

    print(f"Analyzing following gap across {len(all_hours)} hours...\n")

    config_overrides = {
        "scaling.our_capital": 50, "scaling.hourly_budget": 45,
        "scaling.k_factor": 1, "scaling.leader_estimated_capital": 900,
    }

    # Aggregate stats
    total_leader_buys = 0
    total_leader_sells = 0
    total_our_buys = 0
    total_our_sells = 0
    skip_reasons_agg = defaultdict(int)

    # Per-token tracking across all hours
    all_token_outcomes = []  # (token_id, hour, leader_spend, leader_entry, we_entered, our_pnl, leader_outcome, resolution)

    # Per-hour tracking
    hour_details = []

    for session, utc_h, hf, split in all_hours:
        strategy = get_strategy("profit_taker")
        replayer = SessionReplayer(hf, strategy, config_overrides=config_overrides)
        try:
            count = replayer.load()
        except: continue
        if count == 0: continue

        config = replayer._merge_config()
        strategy_config = StrategyConfig.from_dict(config)
        strategy.initialize(strategy_config)
        strategy.on_session_start()

        # Track leader's per-token activity for this hour
        leader_tokens = defaultdict(lambda: {
            "buy_count": 0, "buy_dollars": Decimal("0"), "buy_shares": Decimal("0"),
            "sell_count": 0, "sell_dollars": Decimal("0"), "sell_shares": Decimal("0"),
            "first_buy_price": None, "first_buy_minute": 99,
            "last_buy_price": None, "last_buy_minute": -1,
            "side": None,
        })
        # Track what WE do
        our_tokens_entered = set()
        our_entry_prices = {}
        our_entry_minutes = {}
        our_buys_this_hour = 0
        our_sells_this_hour = 0
        leader_buys_this_hour = 0
        leader_sells_this_hour = 0
        hour_skip_reasons = defaultdict(int)

        last_hour = None
        for event in replayer.loader.events:
            all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
            event.context['all_prices'] = all_prices
            last_hour = event.trade.timestamp.hour

            trade = event.trade
            minute = trade.timestamp.minute

            # Track leader activity
            if trade.action == TradeAction.BUY:
                leader_buys_this_hour += 1
                total_leader_buys += 1
                lt = leader_tokens[trade.token_id]
                lt["buy_count"] += 1
                lt["buy_dollars"] += trade.dollars
                lt["buy_shares"] += trade.shares
                lt["side"] = getattr(trade, 'side', None)
                if lt["first_buy_price"] is None:
                    lt["first_buy_price"] = trade.price
                    lt["first_buy_minute"] = minute
                lt["last_buy_price"] = trade.price
                lt["last_buy_minute"] = minute
            else:
                leader_sells_this_hour += 1
                total_leader_sells += 1
                lt = leader_tokens[trade.token_id]
                lt["sell_count"] += 1
                lt["sell_dollars"] += trade.dollars
                lt["sell_shares"] += trade.shares

            decision = strategy.on_event(event)

            if decision.action == DecisionAction.BUY:
                our_buys_this_hour += 1
                total_our_buys += 1
                our_tokens_entered.add(trade.token_id)
                our_entry_prices[trade.token_id] = decision.price
                our_entry_minutes[trade.token_id] = minute
                strategy.on_fill(event, decision)
            elif decision.action == DecisionAction.SELL:
                our_sells_this_hour += 1
                total_our_sells += 1
                strategy.on_fill(event, decision)
            else:
                # Skip - track reason
                reason = decision.metadata.get("exit_reason", "unknown") if decision.metadata else "unknown"
                # Get skip reason from strategy
                # The skip reason is the last one recorded
                pass

        # Get skip reasons from strategy
        for reason, count_val in strategy.skip_reasons.items():
            skip_reasons_agg[reason] += count_val
            hour_skip_reasons[reason] = count_val

        # Resolve positions and calculate PnL
        end_prices = replayer.loader.get_last_prices_for_hour(last_hour) if last_hour is not None else {}

        # Calculate per-token outcomes
        for token_id, lt in leader_tokens.items():
            if lt["buy_count"] == 0:
                continue

            # Leader's outcome for this token
            remaining_shares = lt["buy_shares"] - lt["sell_shares"]
            sell_revenue = float(lt["sell_dollars"])
            buy_cost = float(lt["buy_dollars"])

            # Resolution
            ps = end_prices.get(token_id)
            if ps and ps.bid is not None:
                last_bid = float(ps.bid)
            else:
                avg_entry = float(lt["buy_dollars"] / lt["buy_shares"]) if lt["buy_shares"] > 0 else 0.5
                last_bid = avg_entry

            if last_bid >= 0.50:
                resolution_price = 0.99
                resolution = "WIN"
            else:
                resolution_price = 0.01
                resolution = "LOSE"

            remaining_value = float(remaining_shares) * resolution_price
            leader_total_pnl = sell_revenue + remaining_value - buy_cost

            # Did WE enter this token?
            we_entered = token_id in our_tokens_entered

            # Our PnL for this token (from portfolio)
            our_pnl = 0
            if we_entered:
                pos = strategy.portfolio.get_positions().get(token_id)
                if pos and pos.shares > 0:
                    our_pnl = float(pos.shares) * resolution_price - float(pos.cost_basis)
                # Also include any realized from sells
                # (approximation - full PnL is in strategy.cash)

            # Price gap: how much more did we pay vs leader's first buy?
            price_gap = None
            minute_gap = None
            if we_entered and lt["first_buy_price"] and our_entry_prices.get(token_id):
                price_gap = float(our_entry_prices[token_id]) - float(lt["first_buy_price"])
                minute_gap = our_entry_minutes.get(token_id, 0) - lt["first_buy_minute"]

            all_token_outcomes.append({
                "hour": f"{session}_h{utc_h}",
                "split": split,
                "token_id": token_id,
                "leader_spend": buy_cost,
                "leader_entry": float(lt["first_buy_price"]) if lt["first_buy_price"] else 0,
                "leader_avg_entry": buy_cost / float(lt["buy_shares"]) if lt["buy_shares"] > 0 else 0,
                "leader_pnl": leader_total_pnl,
                "resolution": resolution,
                "we_entered": we_entered,
                "our_entry_price": float(our_entry_prices.get(token_id, 0)),
                "our_entry_minute": our_entry_minutes.get(token_id, -1),
                "leader_first_minute": lt["first_buy_minute"],
                "leader_last_minute": lt["last_buy_minute"],
                "price_gap": price_gap,
                "minute_gap": minute_gap,
                "leader_buy_count": lt["buy_count"],
                "leader_sell_count": lt["sell_count"],
            })

        # Resolve remaining positions for overall PnL
        for token_id, pos in list(strategy.portfolio.get_positions().items()):
            if pos.shares <= 0: continue
            ps = end_prices.get(token_id)
            if ps and ps.bid is not None:
                last_bid = ps.bid
            else:
                last_bid = strategy.our_entries.get(token_id, Decimal("0.50"))
            res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
            dollars = pos.shares * res_price
            strategy.portfolio.apply_sell(token_id, pos.market_id, pos.side, pos.shares, res_price)
            strategy.cash += dollars

        hour_pnl = float(strategy.cash) - float(Decimal("50") * Decimal("2"))

        hour_details.append({
            "hour": f"{session}_h{utc_h}",
            "split": split,
            "pnl": round(hour_pnl, 2),
            "leader_buys": leader_buys_this_hour,
            "leader_sells": leader_sells_this_hour,
            "our_buys": our_buys_this_hour,
            "our_sells": our_sells_this_hour,
            "tokens_traded_leader": len(leader_tokens),
            "tokens_we_entered": len(our_tokens_entered),
            "follow_rate": len(our_tokens_entered) / max(1, len(leader_tokens)) * 100,
            "skip_reasons": dict(hour_skip_reasons),
        })

    # =========================================================================
    # REPORT
    # =========================================================================
    print(f"{'='*120}")
    print(f"  1. OVERALL TRADE COUNTS")
    print(f"{'='*120}")
    print(f"  Leader buys:  {total_leader_buys}")
    print(f"  Leader sells: {total_leader_sells}")
    print(f"  Our buys:     {total_our_buys}  ({total_our_buys/max(1,total_leader_buys)*100:.1f}% of leader buys)")
    print(f"  Our sells:    {total_our_sells}")
    print(f"\n  Skip reasons (aggregated across all hours):")
    for reason, cnt in sorted(skip_reasons_agg.items(), key=lambda x: -x[1]):
        print(f"    {reason:<30} {cnt:>6}  ({cnt/(total_leader_buys+total_leader_sells)*100:.1f}%)")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  2. TOKEN-LEVEL: ENTERED vs SKIPPED")
    print(f"{'='*120}")
    entered = [t for t in all_token_outcomes if t["we_entered"]]
    skipped = [t for t in all_token_outcomes if not t["we_entered"]]
    print(f"  Total unique token-hours: {len(all_token_outcomes)}")
    print(f"  Tokens we entered:  {len(entered)} ({len(entered)/max(1,len(all_token_outcomes))*100:.0f}%)")
    print(f"  Tokens we skipped:  {len(skipped)} ({len(skipped)/max(1,len(all_token_outcomes))*100:.0f}%)")

    # Outcomes for entered vs skipped
    entered_wins = sum(1 for t in entered if t["resolution"] == "WIN")
    skipped_wins = sum(1 for t in skipped if t["resolution"] == "WIN")
    print(f"\n  Entered tokens: {entered_wins}/{len(entered)} WIN ({entered_wins/max(1,len(entered))*100:.0f}% WR)")
    print(f"  Skipped tokens: {skipped_wins}/{len(skipped)} WIN ({skipped_wins/max(1,len(skipped))*100:.0f}% WR)")

    entered_leader_pnl = sum(t["leader_pnl"] for t in entered)
    skipped_leader_pnl = sum(t["leader_pnl"] for t in skipped)
    print(f"\n  Leader PnL on tokens we ENTERED:  ${entered_leader_pnl:+.2f}")
    print(f"  Leader PnL on tokens we SKIPPED:  ${skipped_leader_pnl:+.2f}")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  3. SKIPPED TOKENS BY LEADER SPEND BUCKET")
    print(f"{'='*120}")
    buckets = [(0, 50), (50, 100), (100, 200), (200, 300), (300, 500), (500, 1000), (1000, 99999)]
    for lo, hi in buckets:
        in_bucket = [t for t in all_token_outcomes if lo <= t["leader_spend"] < hi]
        if not in_bucket: continue
        entered_b = [t for t in in_bucket if t["we_entered"]]
        skipped_b = [t for t in in_bucket if not t["we_entered"]]
        wins_entered = sum(1 for t in entered_b if t["resolution"] == "WIN")
        wins_skipped = sum(1 for t in skipped_b if t["resolution"] == "WIN")
        leader_pnl_entered = sum(t["leader_pnl"] for t in entered_b)
        leader_pnl_skipped = sum(t["leader_pnl"] for t in skipped_b)
        label = f"${lo}-${hi}" if hi < 99999 else f"${lo}+"
        print(f"  {label:<12} Total={len(in_bucket):>3} | Entered={len(entered_b):>3} WR={wins_entered}/{max(1,len(entered_b)):>3} ({wins_entered/max(1,len(entered_b))*100:.0f}%) LdrPnL=${leader_pnl_entered:>+8.2f} | Skipped={len(skipped_b):>3} WR={wins_skipped}/{max(1,len(skipped_b)):>3} ({wins_skipped/max(1,len(skipped_b))*100:.0f}%) LdrPnL=${leader_pnl_skipped:>+8.2f}")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  4. ENTRY PRICE GAP (us vs leader)")
    print(f"{'='*120}")
    gaps = [t for t in entered if t["price_gap"] is not None]
    if gaps:
        price_gaps = [t["price_gap"] for t in gaps]
        minute_gaps = [t["minute_gap"] for t in gaps if t["minute_gap"] is not None]
        print(f"  Avg price gap: ${sum(price_gaps)/len(price_gaps):+.4f} (we pay more)")
        print(f"  Median price gap: ${sorted(price_gaps)[len(price_gaps)//2]:+.4f}")
        print(f"  Max price gap: ${max(price_gaps):+.4f}")
        print(f"  Min price gap: ${min(price_gaps):+.4f}")
        if minute_gaps:
            print(f"  Avg minute gap: {sum(minute_gaps)/len(minute_gaps):+.1f} minutes (we enter later)")
            print(f"  Median minute gap: {sorted(minute_gaps)[len(minute_gaps)//2]:+.0f} minutes")

        # Price gap by win/lose
        win_gaps = [t["price_gap"] for t in gaps if t["resolution"] == "WIN"]
        lose_gaps = [t["price_gap"] for t in gaps if t["resolution"] == "LOSE"]
        if win_gaps:
            print(f"\n  Winners avg price gap: ${sum(win_gaps)/len(win_gaps):+.4f}")
        if lose_gaps:
            print(f"  Losers avg price gap:  ${sum(lose_gaps)/len(lose_gaps):+.4f}")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  5. MISSED WINNING TOKENS (skipped but would have won)")
    print(f"{'='*120}")
    missed_winners = [t for t in skipped if t["resolution"] == "WIN"]
    missed_winners.sort(key=lambda x: -x["leader_pnl"])
    print(f"  Total missed winning tokens: {len(missed_winners)}")
    missed_leader_pnl = sum(t["leader_pnl"] for t in missed_winners)
    print(f"  Leader PnL on missed winners: ${missed_leader_pnl:+.2f}")

    # But also missed losers
    missed_losers = [t for t in skipped if t["resolution"] == "LOSE"]
    missed_loser_pnl = sum(t["leader_pnl"] for t in missed_losers)
    print(f"  Total missed losing tokens:  {len(missed_losers)}")
    print(f"  Leader PnL on missed losers: ${missed_loser_pnl:+.2f}")
    print(f"  NET missed PnL (leader's):   ${missed_leader_pnl + missed_loser_pnl:+.2f}")

    # Top 10 missed winners
    print(f"\n  Top 10 missed winners (by leader PnL):")
    for t in missed_winners[:10]:
        print(f"    {t['hour']:<35} spend=${t['leader_spend']:>7.2f} entry=${t['leader_entry']:.3f} PnL=${t['leader_pnl']:>+8.2f} buys={t['leader_buy_count']} firstMin={t['leader_first_minute']}")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  6. PER-HOUR FOLLOWING RATE")
    print(f"{'='*120}")

    # Sort by follow rate
    active_hours = [h for h in hour_details if h["tokens_traded_leader"] > 0]
    active_hours.sort(key=lambda x: x["follow_rate"])

    # Summary stats
    follow_rates = [h["follow_rate"] for h in active_hours]
    print(f"  Avg follow rate: {sum(follow_rates)/len(follow_rates):.0f}% of leader's tokens")
    print(f"  Min follow rate: {min(follow_rates):.0f}%")
    print(f"  Max follow rate: {max(follow_rates):.0f}%")

    # Hours where we follow 0%
    zero_follow = [h for h in active_hours if h["follow_rate"] == 0]
    print(f"\n  Hours with 0% follow rate: {len(zero_follow)}")
    for h in zero_follow[:5]:
        print(f"    {h['hour']:<35} PnL=${h['pnl']:>+6.2f} LdrBuys={h['leader_buys']} LdrTokens={h['tokens_traded_leader']}")

    # Hours where we follow > 50%
    high_follow = [h for h in active_hours if h["follow_rate"] > 50]
    high_follow.sort(key=lambda x: -x["pnl"])
    print(f"\n  Hours with >50% follow rate: {len(high_follow)}")

    # PnL by follow rate bucket
    print(f"\n  PnL by follow rate:")
    for lo, hi in [(0, 1), (1, 25), (25, 50), (50, 75), (75, 101)]:
        bucket = [h for h in active_hours if lo <= h["follow_rate"] < hi]
        if not bucket: continue
        pnl = sum(h["pnl"] for h in bucket)
        avg_pnl = pnl / len(bucket)
        label = f"{lo}-{hi}%" if hi <= 100 else f"{lo}%+"
        print(f"    Follow {label:<10} {len(bucket):>3} hours  Total=${pnl:>+8.2f}  Avg=${avg_pnl:>+5.2f}")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  7. HOURS WHERE WE SIT OUT vs LEADER PROFITS")
    print(f"{'='*120}")

    # Find hours where we make 0 or negative and leader makes money
    for h in hour_details:
        # Calculate leader PnL for this hour
        hour_tokens = [t for t in all_token_outcomes if t["hour"] == h["hour"]]
        h["leader_total_pnl"] = sum(t["leader_pnl"] for t in hour_tokens)

    sit_out_leader_wins = [h for h in hour_details if h["our_buys"] <= 1 and h["leader_total_pnl"] > 20]
    sit_out_leader_wins.sort(key=lambda x: -x["leader_total_pnl"])
    print(f"  Hours we trade <=1 token but leader profits >$20: {len(sit_out_leader_wins)}")
    for h in sit_out_leader_wins[:10]:
        tokens_h = [t for t in all_token_outcomes if t["hour"] == h["hour"]]
        print(f"    {h['hour']:<35} OurPnL=${h['pnl']:>+6.2f} LdrPnL=${h['leader_total_pnl']:>+8.2f} OurBuys={h['our_buys']} LdrTokens={h['tokens_traded_leader']}")
        # Show what the leader did
        for t in sorted(tokens_h, key=lambda x: -x["leader_pnl"])[:3]:
            entered_str = "ENTERED" if t["we_entered"] else "MISSED"
            print(f"      token spend=${t['leader_spend']:>7.2f} res={t['resolution']} LdrPnL=${t['leader_pnl']:>+7.2f} [{entered_str}]")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  8. LEADER ENTRY PRICE vs OUR CONVICTION FILTER")
    print(f"{'='*120}")

    # For tokens we entered: how much did price move between leader's first buy and our entry?
    conviction_entries = [t for t in entered if t["minute_gap"] is not None and t["minute_gap"] > 0]
    if conviction_entries:
        print(f"  Tokens where we entered AFTER leader's first buy: {len(conviction_entries)}")
        print(f"  (conviction filter caused us to wait)")
        avg_price_paid_more = sum(t["price_gap"] for t in conviction_entries) / len(conviction_entries)
        avg_min_delay = sum(t["minute_gap"] for t in conviction_entries) / len(conviction_entries)
        print(f"  Avg extra price paid: ${avg_price_paid_more:+.4f}")
        print(f"  Avg minutes delayed:  {avg_min_delay:+.1f}")

        # How much does this cost us in PnL?
        # If we entered at leader's price instead of our price, we'd buy more shares
        # and make more on winners
        extra_cost_winners = []
        extra_cost_losers = []
        for t in conviction_entries:
            if t["price_gap"] and t["price_gap"] > 0:
                # We paid more. On a $5 position at $0.70 vs $0.65, that's fewer shares
                # Winner: fewer shares * $0.99 = less profit
                # Loser: fewer shares * $0.01 = less loss (slightly better)
                if t["resolution"] == "WIN":
                    extra_cost_winners.append(t["price_gap"])
                else:
                    extra_cost_losers.append(t["price_gap"])

        if extra_cost_winners:
            print(f"\n  On {len(extra_cost_winners)} WINNERS we paid avg ${sum(extra_cost_winners)/len(extra_cost_winners):+.4f} more")
        if extra_cost_losers:
            print(f"  On {len(extra_cost_losers)} LOSERS  we paid avg ${sum(extra_cost_losers)/len(extra_cost_losers):+.4f} more (saved us money)")


if __name__ == "__main__":
    main()
