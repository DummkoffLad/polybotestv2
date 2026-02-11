"""Deep leader behavior analysis - find the hidden signal.

Focus areas:
1. What makes our 17 "us lose, leader win" hours different?
2. Leader re-entries (sell then re-buy = stronger conviction)
3. Leader's early vs late activity as a REAL-TIME signal
4. Per-token: leader entry price vs our entry price (are we buying different things?)
5. Leader "vote of no confidence" — does the leader SELL heavily before resolution on losers?
"""
import sys
import json
import math
from pathlib import Path
from decimal import Decimal
from collections import defaultdict
from datetime import datetime

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


def analyze_hour_file(hour_file):
    """Parse one hour file and return detailed leader + our behavior."""
    events = []
    with open(hour_file, "r") as f:
        for line in f:
            try:
                obj = json.loads(line)
                events.append(obj)
            except Exception:
                continue

    # Leader trade-by-trade analysis
    leader_tokens = defaultdict(lambda: {
        "buys": [], "sells": [], "side": None,
        "total_buy_shares": Decimal("0"), "total_buy_cost": Decimal("0"),
        "total_sell_shares": Decimal("0"), "total_sell_revenue": Decimal("0"),
        "re_entries": 0,  # count of buy-after-sell sequences
        "has_sold": False,
        "first_buy_minute": 99, "last_buy_minute": 0,
        "first_sell_minute": 99,
        "entry_price": None,
    })

    utc_hour = -1
    end_prices = {}

    for evt in events:
        if evt.get("type") == "leader_trade":
            lt = evt.get("leader_trade", {})
            ts_str = lt.get("timestamp", evt.get("timestamp", ""))
            if "T" in ts_str:
                utc_hour = int(ts_str.split("T")[1][:2])
                minute = int(ts_str.split("T")[1][3:5])
            else:
                minute = 0

            token_id = lt.get("token_id", "")
            action = lt.get("action", "")
            price = Decimal(str(lt.get("leader_price", 0)))
            shares = Decimal(str(lt.get("leader_shares", 0)))
            dollars = Decimal(str(lt.get("leader_dollars", 0)))
            side = lt.get("side", "")

            tk = leader_tokens[token_id]
            tk["side"] = side

            if action == "BUY":
                tk["buys"].append({"minute": minute, "price": float(price),
                                   "shares": float(shares), "dollars": float(dollars)})
                tk["total_buy_shares"] += shares
                tk["total_buy_cost"] += dollars
                if tk["entry_price"] is None:
                    tk["entry_price"] = price
                tk["first_buy_minute"] = min(tk["first_buy_minute"], minute)
                tk["last_buy_minute"] = max(tk["last_buy_minute"], minute)
                # Count re-entries
                if tk["has_sold"]:
                    tk["re_entries"] += 1
            elif action == "SELL":
                tk["sells"].append({"minute": minute, "price": float(price),
                                    "shares": float(shares), "dollars": float(dollars)})
                tk["total_sell_shares"] += shares
                tk["total_sell_revenue"] += dollars
                tk["has_sold"] = True
                if tk["first_sell_minute"] > minute:
                    tk["first_sell_minute"] = minute

        elif evt.get("type") == "price_snapshot":
            prices = evt.get("prices", {})
            for token_id, pdata in prices.items():
                bid = pdata.get("bid") if isinstance(pdata, dict) else None
                ask = pdata.get("ask") if isinstance(pdata, dict) else None
                if bid is not None:
                    end_prices[token_id] = {"bid": Decimal(str(bid)),
                                            "ask": Decimal(str(ask)) if ask else None}

    # Determine resolution per token
    for token_id, tk in leader_tokens.items():
        if tk["total_buy_shares"] == 0:
            continue
        avg_entry = tk["total_buy_cost"] / tk["total_buy_shares"]
        remaining = tk["total_buy_shares"] - tk["total_sell_shares"]

        # Use end price for resolution guess
        ep = end_prices.get(token_id, {})
        last_bid = ep.get("bid", Decimal("0.50"))
        if last_bid is None:
            last_bid = Decimal("0.50")

        if remaining > 0:
            res_price = Decimal("0.99") if last_bid >= Decimal("0.50") else Decimal("0.01")
            tk["resolution"] = "WIN" if res_price == Decimal("0.99") else "LOSE"
            remaining_cost = remaining * avg_entry
            tk["resolution_pnl"] = float(remaining * res_price - remaining_cost)
        else:
            tk["resolution"] = "SOLD"
            tk["resolution_pnl"] = 0

        # Sell PnL (profit from mid-hour sells)
        sell_pnl = float(tk["total_sell_revenue"]) - float(min(tk["total_sell_shares"], tk["total_buy_shares"]) * avg_entry)
        tk["sell_pnl"] = sell_pnl
        tk["avg_entry"] = float(avg_entry)

        # Key: did leader sell BEFORE a loss? (sell -> then lose at resolution)
        pct_sold = float(tk["total_sell_shares"]) / max(0.01, float(tk["total_buy_shares"])) * 100
        tk["pct_sold"] = pct_sold

        # Leader's NET PnL on this token
        tk["net_pnl"] = sell_pnl + tk["resolution_pnl"]

        # Time-to-first-sell (how quickly did leader start selling?)
        tk["time_to_first_sell"] = tk["first_sell_minute"] if tk["has_sold"] else 99

    return {
        "utc_hour": utc_hour,
        "leader_tokens": dict(leader_tokens),
        "end_prices": end_prices,
    }


def run_our_strategy(hour_file, capital=50, budget=45):
    """Run our current strategy and return per-token results."""
    orig = {}
    for attr in ["SCALE_BOOST", "SKIP_PRICE_LOW", "SKIP_PRICE_HIGH", "PER_MARKET_CAP_PCT",
                 "DRAWDOWN_REDUCE_THRESHOLD", "DRAWDOWN_STOP_THRESHOLD",
                 "LATE_ENTRY_BOOST_MIN", "LATE_ENTRY_BOOST_MULT"]:
        orig[attr] = getattr(pt_mod, attr)

    # Current best config
    pt_mod.SCALE_BOOST = Decimal("5")
    pt_mod.SKIP_PRICE_LOW = Decimal("0.45")
    pt_mod.SKIP_PRICE_HIGH = Decimal("0.85")
    pt_mod.PER_MARKET_CAP_PCT = Decimal("50")
    pt_mod.DRAWDOWN_REDUCE_THRESHOLD = Decimal("10")
    pt_mod.DRAWDOWN_STOP_THRESHOLD = Decimal("20")
    pt_mod.LATE_ENTRY_BOOST_MIN = 40
    pt_mod.LATE_ENTRY_BOOST_MULT = Decimal("3")

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

    # Track what we actually buy
    our_token_buys = defaultdict(lambda: {"shares": Decimal("0"), "cost": Decimal("0"), "entry_price": None})
    our_token_sells = defaultdict(lambda: {"shares": Decimal("0"), "revenue": Decimal("0")})

    last_hour = None
    for event in replayer.loader.events:
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices
        last_hour = event.trade.timestamp.hour
        decision = strategy.on_event(event)
        if decision.action == DecisionAction.BUY:
            strategy.on_fill(event, decision)
            tid = event.trade.token_id
            b = our_token_buys[tid]
            # Approximate — we don't know exact fill but it's close to leader price
            b["shares"] += Decimal(str(decision.shares)) if hasattr(decision, 'shares') else Decimal("0")
            if b["entry_price"] is None:
                b["entry_price"] = event.trade.price
        elif decision.action == DecisionAction.SELL:
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

    pnl = float(strategy.cash) - float(Decimal(str(capital)) * Decimal("2"))

    for k, v in orig.items():
        setattr(pt_mod, k, v)

    return {
        "pnl": round(pnl, 2),
        "buys": strategy.buys,
        "sells": strategy.sells,
        "our_entries": {k: float(v) for k, v in strategy.our_entries.items()},
    }


def main():
    base = Path("data/sessions")
    all_hours = []
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        for hf in sorted(date_dir.glob("*_hour_*.jsonl")):
            with open(hf, 'r') as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                        if obj.get("type") in ("leader_trade", "fill"):
                            ts = obj.get("timestamp", "")
                            if "T" in ts:
                                utc_h = int(ts.split("T")[1][:2])
                                session = f"{date_dir.name}/{hf.stem.split('_hour_')[0]}"
                                all_hours.append((session, utc_h, hf, get_split(session)))
                                break
                    except Exception:
                        continue

    print(f"Analyzing {len(all_hours)} hours for leader behavior patterns...\n")

    # Collect data per hour
    hour_data = []
    for session, utc_h, hf, split in all_hours:
        leader_info = analyze_hour_file(hf)
        our_info = run_our_strategy(hf)
        our_pnl = our_info["pnl"] if our_info else 0

        # Leader summary
        leader_pnl = 0
        leader_buy_total = 0
        leader_sell_total = 0
        tokens_traded = 0
        re_entries_total = 0
        early_sells = 0  # sells before minute 15
        late_buys = 0    # buys after minute 40
        tokens_with_re_entry = 0
        token_details = []

        for token_id, tk in leader_info["leader_tokens"].items():
            if tk["total_buy_shares"] == 0:
                continue
            tokens_traded += 1
            leader_pnl += tk.get("net_pnl", 0)
            leader_buy_total += float(tk["total_buy_cost"])
            leader_sell_total += float(tk["total_sell_revenue"])
            re_entries_total += tk["re_entries"]
            if tk["re_entries"] > 0:
                tokens_with_re_entry += 1

            # Count early sells (before minute 15)
            for s in tk["sells"]:
                if s["minute"] < 15:
                    early_sells += 1

            # Count late buys (after minute 40)
            for b in tk["buys"]:
                if b["minute"] >= 40:
                    late_buys += 1

            token_details.append({
                "token_id": token_id,
                "side": tk["side"],
                "entry_price": tk.get("avg_entry", 0),
                "pct_sold": tk.get("pct_sold", 0),
                "resolution": tk.get("resolution", "?"),
                "net_pnl": tk.get("net_pnl", 0),
                "re_entries": tk["re_entries"],
                "first_buy_min": tk["first_buy_minute"],
                "last_buy_min": tk["last_buy_minute"],
                "time_to_first_sell": tk["time_to_first_sell"],
                "sell_pnl": tk.get("sell_pnl", 0),
                "resolution_pnl": tk.get("resolution_pnl", 0),
                "buy_cost": float(tk["total_buy_cost"]),
                "buys": tk["buys"],
                "sells": tk["sells"],
            })

        # Our entries for this hour
        our_entries = our_info.get("our_entries", {}) if our_info else {}

        # What tokens did we buy vs what the leader traded?
        our_tokens = set(our_entries.keys())
        leader_token_ids = set(tk_id for tk_id, tk in leader_info["leader_tokens"].items()
                               if tk["total_buy_shares"] > 0)
        overlap = our_tokens & leader_token_ids

        hour_data.append({
            "session": session, "hour": utc_h, "split": split,
            "our_pnl": our_pnl,
            "leader_pnl": round(leader_pnl, 2),
            "leader_buy_total": round(leader_buy_total, 2),
            "leader_sell_total": round(leader_sell_total, 2),
            "tokens_traded": tokens_traded,
            "re_entries_total": re_entries_total,
            "tokens_with_re_entry": tokens_with_re_entry,
            "early_sells": early_sells,
            "late_buys": late_buys,
            "token_details": token_details,
            "our_entries": our_entries,
            "our_tokens": our_tokens,
            "leader_tokens": leader_token_ids,
            "overlap": overlap,
            "our_buys": our_info["buys"] if our_info else 0,
        })

    # =========================================================================
    # ANALYSIS 1: What distinguishes "us lose, leader win" hours?
    # =========================================================================
    print(f"{'='*120}")
    print(f"  ANALYSIS 1: What distinguishes the 17 'us lose, leader win' hours?")
    print(f"{'='*120}")

    diverge_hours = [h for h in hour_data if h["our_pnl"] < 0 and h["leader_pnl"] > 0]
    good_hours = [h for h in hour_data if h["our_pnl"] > 5]
    all_loss_hours = [h for h in hour_data if h["our_pnl"] < 0]

    def avg(lst):
        return sum(lst) / max(1, len(lst))

    print(f"\n  {'Metric':<35} | {'Diverge (N={len(diverge_hours)})':>20} | {'Good (N={len(good_hours)})':>20} | {'All Loss (N={len(all_loss_hours)})':>20}")
    print(f"  {'-'*100}")

    metrics = [
        ("Avg our PnL", [h["our_pnl"] for h in diverge_hours], [h["our_pnl"] for h in good_hours], [h["our_pnl"] for h in all_loss_hours]),
        ("Avg leader PnL", [h["leader_pnl"] for h in diverge_hours], [h["leader_pnl"] for h in good_hours], [h["leader_pnl"] for h in all_loss_hours]),
        ("Avg tokens traded", [h["tokens_traded"] for h in diverge_hours], [h["tokens_traded"] for h in good_hours], [h["tokens_traded"] for h in all_loss_hours]),
        ("Avg re-entries", [h["re_entries_total"] for h in diverge_hours], [h["re_entries_total"] for h in good_hours], [h["re_entries_total"] for h in all_loss_hours]),
        ("Avg tokens w/ re-entry", [h["tokens_with_re_entry"] for h in diverge_hours], [h["tokens_with_re_entry"] for h in good_hours], [h["tokens_with_re_entry"] for h in all_loss_hours]),
        ("Avg early sells (<15m)", [h["early_sells"] for h in diverge_hours], [h["early_sells"] for h in good_hours], [h["early_sells"] for h in all_loss_hours]),
        ("Avg late buys (40m+)", [h["late_buys"] for h in diverge_hours], [h["late_buys"] for h in good_hours], [h["late_buys"] for h in all_loss_hours]),
        ("Avg leader buy total", [h["leader_buy_total"] for h in diverge_hours], [h["leader_buy_total"] for h in good_hours], [h["leader_buy_total"] for h in all_loss_hours]),
        ("Avg our buys count", [h["our_buys"] for h in diverge_hours], [h["our_buys"] for h in good_hours], [h["our_buys"] for h in all_loss_hours]),
        ("Token overlap (our/leader)", [len(h["overlap"])/max(1,len(h["leader_tokens"])) for h in diverge_hours],
                                       [len(h["overlap"])/max(1,len(h["leader_tokens"])) for h in good_hours],
                                       [len(h["overlap"])/max(1,len(h["leader_tokens"])) for h in all_loss_hours]),
    ]

    for name, div_vals, good_vals, loss_vals in metrics:
        print(f"  {name:<35} | {avg(div_vals):>20.2f} | {avg(good_vals):>20.2f} | {avg(loss_vals):>20.2f}")

    # =========================================================================
    # ANALYSIS 2: Leader re-entry signal — does it predict outcome?
    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  ANALYSIS 2: Leader RE-ENTRY signal (sell then buy back)")
    print(f"{'='*120}")

    # Per-token: did re-entry predict winning?
    re_entry_tokens = []
    no_re_entry_tokens = []
    for h in hour_data:
        for td in h["token_details"]:
            if td["resolution"] == "SOLD":
                continue
            if td["re_entries"] > 0:
                re_entry_tokens.append(td)
            else:
                no_re_entry_tokens.append(td)

    re_win = sum(1 for t in re_entry_tokens if t["resolution"] == "WIN")
    re_lose = sum(1 for t in re_entry_tokens if t["resolution"] == "LOSE")
    no_re_win = sum(1 for t in no_re_entry_tokens if t["resolution"] == "WIN")
    no_re_lose = sum(1 for t in no_re_entry_tokens if t["resolution"] == "LOSE")

    print(f"\n  WITH re-entry: {len(re_entry_tokens)} tokens | {re_win}W / {re_lose}L | WR={re_win/max(1,re_win+re_lose)*100:.0f}%")
    print(f"  NO re-entry:   {len(no_re_entry_tokens)} tokens | {no_re_win}W / {no_re_lose}L | WR={no_re_win/max(1,no_re_win+no_re_lose)*100:.0f}%")
    print(f"\n  Re-entry avg net PnL:    ${avg([t['net_pnl'] for t in re_entry_tokens]):+.2f}")
    print(f"  No re-entry avg net PnL: ${avg([t['net_pnl'] for t in no_re_entry_tokens]):+.2f}")

    # By number of re-entries
    print(f"\n  By re-entry count:")
    for n_re in [0, 1, 2, 3]:
        tokens = [t for t in re_entry_tokens + no_re_entry_tokens
                  if t["re_entries"] == n_re and t["resolution"] != "SOLD"]
        if not tokens:
            continue
        wins = sum(1 for t in tokens if t["resolution"] == "WIN")
        total = len(tokens)
        print(f"    {n_re} re-entries: {total:>3} tokens | WR={wins/total*100:.0f}% | Avg PnL=${avg([t['net_pnl'] for t in tokens]):+.2f}")

    more = [t for t in re_entry_tokens + no_re_entry_tokens
            if t["re_entries"] >= 4 and t["resolution"] != "SOLD"]
    if more:
        wins = sum(1 for t in more if t["resolution"] == "WIN")
        print(f"    4+ re-entries: {len(more):>3} tokens | WR={wins/len(more)*100:.0f}% | Avg PnL=${avg([t['net_pnl'] for t in more]):+.2f}")

    # =========================================================================
    # ANALYSIS 3: Leader's FIRST SELL timing as signal
    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  ANALYSIS 3: Leader's first sell timing vs outcome")
    print(f"{'='*120}")

    all_tokens_with_res = [td for h in hour_data for td in h["token_details"]
                           if td["resolution"] in ("WIN", "LOSE")]

    # Bucket by time-to-first-sell
    sell_time_buckets = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": []})
    for t in all_tokens_with_res:
        ttfs = t["time_to_first_sell"]
        if ttfs < 5:
            key = "0-5min"
        elif ttfs < 10:
            key = "5-10min"
        elif ttfs < 15:
            key = "10-15min"
        elif ttfs < 20:
            key = "15-20min"
        elif ttfs < 30:
            key = "20-30min"
        elif ttfs < 40:
            key = "30-40min"
        elif ttfs < 50:
            key = "40-50min"
        elif ttfs < 60:
            key = "50-60min"
        else:
            key = "never sold"

        b = sell_time_buckets[key]
        if t["resolution"] == "WIN":
            b["wins"] += 1
        else:
            b["losses"] += 1
        b["pnl"].append(t["net_pnl"])

    print(f"\n  {'First sell time':>15} | {'Count':>5} | {'WR':>5} | {'Avg PnL':>8}")
    for key in ["0-5min", "5-10min", "10-15min", "15-20min", "20-30min",
                "30-40min", "40-50min", "50-60min", "never sold"]:
        b = sell_time_buckets.get(key)
        if not b:
            continue
        total = b["wins"] + b["losses"]
        wr = b["wins"] / total * 100
        print(f"  {key:>15} | {total:>5} | {wr:>4.0f}% | ${avg(b['pnl']):>+7.2f}")

    # =========================================================================
    # ANALYSIS 4: Does % sold predict resolution?
    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  ANALYSIS 4: Leader sell intensity vs resolution outcome")
    print(f"{'='*120}")

    pct_sold_buckets = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": []})
    for t in all_tokens_with_res:
        pct = t["pct_sold"]
        if pct == 0:
            key = "0% (held)"
        elif pct < 30:
            key = "1-30%"
        elif pct < 50:
            key = "30-50%"
        elif pct < 70:
            key = "50-70%"
        elif pct < 90:
            key = "70-90%"
        elif pct < 100:
            key = "90-100%"
        else:
            key = "100%+ (over-sold)"

        b = pct_sold_buckets[key]
        if t["resolution"] == "WIN":
            b["wins"] += 1
        else:
            b["losses"] += 1
        b["pnl"].append(t["net_pnl"])

    print(f"\n  {'% sold mid-hour':>20} | {'Count':>5} | {'WR':>5} | {'Avg Net PnL':>10} | {'Avg Res PnL':>10}")
    for key in ["0% (held)", "1-30%", "30-50%", "50-70%", "70-90%", "90-100%", "100%+ (over-sold)"]:
        b = pct_sold_buckets.get(key)
        if not b:
            continue
        total = b["wins"] + b["losses"]
        wr = b["wins"] / total * 100
        tokens = [t for t in all_tokens_with_res if
                  (key == "0% (held)" and t["pct_sold"] == 0) or
                  (key == "1-30%" and 0 < t["pct_sold"] < 30) or
                  (key == "30-50%" and 30 <= t["pct_sold"] < 50) or
                  (key == "50-70%" and 50 <= t["pct_sold"] < 70) or
                  (key == "70-90%" and 70 <= t["pct_sold"] < 90) or
                  (key == "90-100%" and 90 <= t["pct_sold"] < 100) or
                  (key == "100%+ (over-sold)" and t["pct_sold"] >= 100)]
        avg_res = avg([t["resolution_pnl"] for t in tokens]) if tokens else 0
        print(f"  {key:>20} | {total:>5} | {wr:>4.0f}% | ${avg(b['pnl']):>+9.2f} | ${avg_res:>+9.2f}")

    # =========================================================================
    # ANALYSIS 5: Entry price of tokens WE buy vs leader's winning tokens
    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  ANALYSIS 5: Our entry prices vs leader's entry prices")
    print(f"{'='*120}")

    # For diverge hours, which tokens did we buy and at what price?
    print(f"\n  In DIVERGE hours (us lose, leader win):")
    for h in diverge_hours[:10]:
        print(f"\n  {h['session']} H{h['hour']:02d} | Us=${h['our_pnl']:+.2f} | Leader=${h['leader_pnl']:+.2f}")

        # Our entries
        our_e = h["our_entries"]
        if our_e:
            for tid, price in our_e.items():
                # Find matching leader token
                ltk = next((td for td in h["token_details"] if td["token_id"] == tid), None)
                if ltk:
                    res_label = ltk["resolution"]
                    print(f"    WE BOUGHT: ...{tid[-8:]} @ ${price:.3f} | Leader entry ${ltk['entry_price']:.3f} | {res_label} | LdrPnL ${ltk['net_pnl']:+.2f}")
                else:
                    print(f"    WE BOUGHT: ...{tid[-8:]} @ ${price:.3f} | (not in leader tokens)")
        else:
            print(f"    (we made no buys)")

        # Leader tokens we DIDN'T buy
        for td in h["token_details"]:
            if td["token_id"] not in our_e:
                print(f"    WE MISSED: ...{td['token_id'][-8:]} @ ${td['entry_price']:.3f} | {td['resolution']} | LdrPnL ${td['net_pnl']:+.2f} | {td['side']}")

    # =========================================================================
    # ANALYSIS 6: Leader early activity as a confidence signal
    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  ANALYSIS 6: Leader's early-hour activity level as confidence signal")
    print(f"{'='*120}")

    # For each hour, count leader's buy volume in first 10 minutes
    for h in hour_data:
        first_10_buys = 0
        first_10_cost = 0
        for td in h["token_details"]:
            for b in td.get("buys", []):
                if b["minute"] < 10:
                    first_10_buys += 1
                    first_10_cost += b["dollars"]
        h["first_10_cost"] = first_10_cost
        h["first_10_buys"] = first_10_buys

    # Bucket hours by early activity
    for threshold in [100, 200, 300, 500]:
        high_activity = [h for h in hour_data if h["first_10_cost"] >= threshold]
        low_activity = [h for h in hour_data if h["first_10_cost"] < threshold]
        if not high_activity or not low_activity:
            continue
        ha_wr = sum(1 for h in high_activity if h["our_pnl"] > 0) / len(high_activity) * 100
        la_wr = sum(1 for h in low_activity if h["our_pnl"] > 0) / len(low_activity) * 100
        ha_pnl = avg([h["our_pnl"] for h in high_activity])
        la_pnl = avg([h["our_pnl"] for h in low_activity])
        print(f"  First 10min buy cost >= ${threshold}: {len(high_activity)} hours, WR={ha_wr:.0f}%, avg PnL ${ha_pnl:+.2f}")
        print(f"  First 10min buy cost <  ${threshold}: {len(low_activity)} hours, WR={la_wr:.0f}%, avg PnL ${la_pnl:+.2f}")
        print()

    # =========================================================================
    # ANALYSIS 7: Which tokens have the BEST/WORST outcomes and why?
    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  ANALYSIS 7: Token-level patterns - what predicts WIN vs LOSE?")
    print(f"{'='*120}")

    winning_tokens = [t for t in all_tokens_with_res if t["resolution"] == "WIN"]
    losing_tokens = [t for t in all_tokens_with_res if t["resolution"] == "LOSE"]

    print(f"\n  {'Metric':<30} | {'Winners (N={len(winning_tokens)})':>20} | {'Losers (N={len(losing_tokens)})':>20}")
    print(f"  {'-'*75}")

    token_metrics = [
        ("Avg entry price", [t["entry_price"] for t in winning_tokens], [t["entry_price"] for t in losing_tokens]),
        ("Avg % sold mid-hour", [t["pct_sold"] for t in winning_tokens], [t["pct_sold"] for t in losing_tokens]),
        ("Avg re-entries", [t["re_entries"] for t in winning_tokens], [t["re_entries"] for t in losing_tokens]),
        ("Avg first buy minute", [t["first_buy_min"] for t in winning_tokens], [t["first_buy_min"] for t in losing_tokens]),
        ("Avg last buy minute", [t["last_buy_min"] for t in winning_tokens], [t["last_buy_min"] for t in losing_tokens]),
        ("Avg time to first sell", [t["time_to_first_sell"] for t in winning_tokens if t["time_to_first_sell"] < 60],
                                   [t["time_to_first_sell"] for t in losing_tokens if t["time_to_first_sell"] < 60]),
        ("Avg buy cost ($)", [t["buy_cost"] for t in winning_tokens], [t["buy_cost"] for t in losing_tokens]),
    ]

    for name, win_vals, lose_vals in token_metrics:
        if win_vals and lose_vals:
            print(f"  {name:<30} | {avg(win_vals):>20.2f} | {avg(lose_vals):>20.2f}")

    # =========================================================================
    # ANALYSIS 8: Leader's NET DIRECTION per hour (UP vs DOWN emphasis)
    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  ANALYSIS 8: Leader's side emphasis (UP vs DOWN) per hour")
    print(f"{'='*120}")

    for h in hour_data:
        up_cost = sum(td["buy_cost"] for td in h["token_details"] if td["side"] == "UP")
        down_cost = sum(td["buy_cost"] for td in h["token_details"] if td["side"] == "DOWN")
        total = up_cost + down_cost
        h["up_pct"] = up_cost / max(0.01, total) * 100
        h["down_pct"] = down_cost / max(0.01, total) * 100
        h["side_skew"] = abs(h["up_pct"] - 50)  # How skewed toward one side

    # Does side skew predict our outcome?
    skew_buckets = defaultdict(lambda: {"pnl": [], "count": 0})
    for h in hour_data:
        skew = h["side_skew"]
        if skew < 10:
            key = "balanced (<10%)"
        elif skew < 25:
            key = "moderate (10-25%)"
        else:
            key = "skewed (25%+)"
        skew_buckets[key]["pnl"].append(h["our_pnl"])
        skew_buckets[key]["count"] += 1

    print(f"\n  {'Side skew':>20} | {'Count':>5} | {'Our WR':>7} | {'Avg PnL':>8} | {'Total PnL':>10}")
    for key in ["balanced (<10%)", "moderate (10-25%)", "skewed (25%+)"]:
        b = skew_buckets.get(key)
        if not b:
            continue
        wr = sum(1 for x in b["pnl"] if x > 0) / b["count"] * 100
        print(f"  {key:>20} | {b['count']:>5} | {wr:>5.0f}% | ${avg(b['pnl']):>+7.2f} | ${sum(b['pnl']):>+9.2f}")

    # =========================================================================
    # ANALYSIS 9: Our MISSED tokens — profitable ones we skipped
    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  ANALYSIS 9: Tokens we MISSED that were profitable for leader")
    print(f"{'='*120}")

    missed_wins = 0
    missed_losses = 0
    missed_win_pnl = 0
    missed_loss_pnl = 0
    missed_by_price_high = 0
    missed_by_price_low = 0
    missed_by_other = 0

    for h in hour_data:
        for td in h["token_details"]:
            if td["token_id"] in h["our_entries"]:
                continue  # We bought this one
            # We missed this token
            entry = td["entry_price"]
            if td["resolution"] == "WIN":
                missed_wins += 1
                missed_win_pnl += td["net_pnl"]
                if entry > 0.85:
                    missed_by_price_high += 1
                elif entry < 0.45:
                    missed_by_price_low += 1
                else:
                    missed_by_other += 1
            elif td["resolution"] == "LOSE":
                missed_losses += 1
                missed_loss_pnl += td["net_pnl"]

    print(f"\n  Missed tokens that WON:  {missed_wins} (total leader PnL: ${missed_win_pnl:+.2f})")
    print(f"  Missed tokens that LOST: {missed_losses} (total leader PnL: ${missed_loss_pnl:+.2f})")
    print(f"  Net missed opportunity: ${missed_win_pnl + missed_loss_pnl:+.2f}")
    print(f"\n  WHY we missed winning tokens:")
    print(f"    Entry > $0.85 (SKIP_PRICE_HIGH): {missed_by_price_high}")
    print(f"    Entry < $0.45 (SKIP_PRICE_LOW):  {missed_by_price_low}")
    print(f"    Other (min_pct, budget, timing):  {missed_by_other}")

    # =========================================================================
    # ANALYSIS 10: "Conviction" signal — leader buy $ in first 5 min per token
    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  ANALYSIS 10: Leader conviction (buy $ in first 5 min) vs outcome")
    print(f"{'='*120}")

    # Re-parse for early buy cost per token
    for h in hour_data:
        for td in h["token_details"]:
            # Already have first_buy_min — use buy_cost as proxy for conviction
            # Higher cost = more conviction
            pass

    conviction_buckets = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": []})
    for t in all_tokens_with_res:
        cost = t["buy_cost"]
        if cost < 50:
            key = "<$50"
        elif cost < 100:
            key = "$50-100"
        elif cost < 200:
            key = "$100-200"
        elif cost < 500:
            key = "$200-500"
        else:
            key = "$500+"

        b = conviction_buckets[key]
        if t["resolution"] == "WIN":
            b["wins"] += 1
        else:
            b["losses"] += 1
        b["pnl"].append(t["net_pnl"])

    print(f"\n  {'Leader buy cost':>15} | {'Count':>5} | {'WR':>5} | {'Avg PnL':>8}")
    for key in ["<$50", "$50-100", "$100-200", "$200-500", "$500+"]:
        b = conviction_buckets.get(key)
        if not b:
            continue
        total = b["wins"] + b["losses"]
        wr = b["wins"] / total * 100
        print(f"  {key:>15} | {total:>5} | {wr:>4.0f}% | ${avg(b['pnl']):>+7.2f}")


if __name__ == "__main__":
    main()
