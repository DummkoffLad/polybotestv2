"""Deep leader vs us comparison — position by position, hour by hour.

Goal: Find how much money the leader ACTUALLY makes and how much we COULD capture.

Analysis sections:
1. Leader's actual PnL (sells + resolution) — the ceiling
2. "Perfect mirror" PnL — if we perfectly scaled leader at 50/900
3. Our actual PnL with current strategy
4. Gap breakdown: why we capture less than perfect mirror
5. Per-price-range breakdown of gaps
6. What happens if we lower min_trade_pct (capture more trades)
7. What happens if we increase capital
"""
import sys
import json
from pathlib import Path
from decimal import Decimal
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from src.strategies import get_strategy
from src.strategies.base import StrategyConfig, DecisionAction
from src.framework.replay.replayer import SessionReplayer
from src.data.models import PriceSnapshot

# Session split
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


def parse_leader_trades(hour_file):
    """Parse all leader trades and price snapshots from one hour file."""
    leader_trades = []
    last_prices = {}  # token_id -> last bid

    with open(hour_file, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue

            if obj.get("type") == "price_snapshot":
                for token_id, pdata in obj.get("prices", {}).items():
                    bid = pdata.get("bid")
                    if bid is not None:
                        last_prices[token_id] = Decimal(str(bid))
                continue

            if obj.get("type") == "leader_trade":
                lt = obj.get("leader_trade", {})
                pc = obj.get("price_context", {})
                token_id = lt.get("token_id", "")

                # Update prices from context
                if pc:
                    bid = pc.get("bid")
                    if bid is not None:
                        last_prices[token_id] = Decimal(str(bid))

                ts_str = lt.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                except Exception:
                    continue

                leader_trades.append({
                    "timestamp": ts,
                    "token_id": token_id,
                    "action": lt.get("action", ""),
                    "price": Decimal(str(lt.get("leader_price", 0))),
                    "dollars": Decimal(str(lt.get("leader_dollars", 0))),
                    "shares": Decimal(str(lt.get("leader_shares", 0))),
                    "side": lt.get("side", ""),
                    "ask": Decimal(str(pc.get("ask", 0))) if pc.get("ask") else None,
                    "bid": Decimal(str(pc.get("bid", 0))) if pc.get("bid") else None,
                    "minute": ts.minute,
                })

    return leader_trades, last_prices


def compute_leader_pnl(leader_trades, last_prices):
    """Compute leader's actual PnL: mid-hour sell proceeds + resolution - buy cost."""
    positions = {}  # token_id -> {shares, cost, entry_price, side}
    total_buy_cost = Decimal("0")
    total_sell_proceeds = Decimal("0")
    sell_pnl = Decimal("0")

    for t in leader_trades:
        token_id = t["token_id"]
        if t["action"] == "BUY":
            if token_id not in positions:
                positions[token_id] = {"shares": Decimal("0"), "cost": Decimal("0"),
                                       "entry_price": t["price"], "side": t["side"]}
            pos = positions[token_id]
            pos["shares"] += t["shares"]
            pos["cost"] += t["dollars"]
            total_buy_cost += t["dollars"]
        elif t["action"] == "SELL":
            pos = positions.get(token_id)
            if pos and pos["shares"] > 0:
                sell_ratio = min(t["shares"] / pos["shares"], Decimal("1"))
                sell_cost_basis = pos["cost"] * sell_ratio
                pos["cost"] -= sell_cost_basis
                pos["shares"] = max(Decimal("0"), pos["shares"] - t["shares"])
                sell_pnl += t["dollars"] - sell_cost_basis
            total_sell_proceeds += t["dollars"]

    # Resolve remaining at hour end
    resolution_pnl = Decimal("0")
    position_details = []
    for token_id, pos in positions.items():
        if pos["shares"] <= 0:
            continue
        last_bid = last_prices.get(token_id, Decimal("0.50"))
        if last_bid >= Decimal("0.50"):
            res_price = Decimal("0.99")
            outcome = "WIN"
        else:
            res_price = Decimal("0.01")
            outcome = "LOSE"
        value = pos["shares"] * res_price
        pnl = value - pos["cost"]
        resolution_pnl += pnl
        position_details.append({
            "token_id": token_id,
            "shares": pos["shares"],
            "cost": pos["cost"],
            "entry_price": pos["entry_price"],
            "last_bid": last_bid,
            "res_price": res_price,
            "pnl": pnl,
            "outcome": outcome,
        })

    total_pnl = sell_pnl + resolution_pnl
    return {
        "total_pnl": total_pnl,
        "sell_pnl": sell_pnl,
        "resolution_pnl": resolution_pnl,
        "buy_cost": total_buy_cost,
        "sell_proceeds": total_sell_proceeds,
        "positions_at_end": position_details,
        "num_tokens": len([p for p in positions.values() if p["shares"] > 0 or p["cost"] > 0]),
    }


def compute_perfect_mirror_pnl(leader_trades, last_prices, our_capital, leader_capital):
    """Perfect mirror: scale every leader trade by our_capital/leader_capital.
    No filters, no skips, no caps — just proportional copy."""
    ratio = our_capital / leader_capital
    positions = {}  # token_id -> {shares, cost, entry_price}
    total_buy_cost = Decimal("0")
    total_sell_proceeds = Decimal("0")
    sell_pnl = Decimal("0")

    for t in leader_trades:
        token_id = t["token_id"]
        our_dollars = t["dollars"] * ratio
        # Use the same price as leader (best case)
        our_shares = t["shares"] * ratio

        if t["action"] == "BUY":
            if token_id not in positions:
                positions[token_id] = {"shares": Decimal("0"), "cost": Decimal("0"),
                                       "entry_price": t["price"]}
            pos = positions[token_id]
            pos["shares"] += our_shares
            pos["cost"] += our_dollars
            total_buy_cost += our_dollars
        elif t["action"] == "SELL":
            pos = positions.get(token_id)
            if pos and pos["shares"] > 0:
                sell_ratio = min(our_shares / pos["shares"], Decimal("1"))
                sell_cost_basis = pos["cost"] * sell_ratio
                pos["cost"] -= sell_cost_basis
                pos["shares"] = max(Decimal("0"), pos["shares"] - our_shares)
                sell_pnl += our_dollars - sell_cost_basis
            total_sell_proceeds += our_dollars

    # Resolve
    resolution_pnl = Decimal("0")
    for token_id, pos in positions.items():
        if pos["shares"] <= 0:
            continue
        last_bid = last_prices.get(token_id, Decimal("0.50"))
        if last_bid >= Decimal("0.50"):
            res_price = Decimal("0.99")
        else:
            res_price = Decimal("0.01")
        value = pos["shares"] * res_price
        pnl = value - pos["cost"]
        resolution_pnl += pnl

    return {
        "total_pnl": sell_pnl + resolution_pnl,
        "sell_pnl": sell_pnl,
        "resolution_pnl": resolution_pnl,
        "buy_cost": total_buy_cost,
    }


def run_strategy_variant(hour_file, config_overrides):
    """Run strategy with given config and return PnL + details."""
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

    # Resolve
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

    pnl = float(strategy.cash) - float(Decimal(str(config_overrides.get("scaling.our_capital", 50))) * Decimal("2"))
    return {
        "pnl": round(pnl, 2),
        "buys": strategy.buys,
        "sells": strategy.sells,
        "skip_reasons": dict(strategy.skip_reasons),
        "tokens_entered": len(strategy.our_entries) + len([p for p in strategy.portfolio.get_positions().values() if p.shares <= 0]),
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

    print(f"{'='*130}")
    print(f"  LEADER vs US — DEEP POSITION COMPARISON ({len(all_hours)} hours)")
    print(f"{'='*130}")

    # =====================================================================
    # SECTION 1: LEADER'S ACTUAL PnL
    # =====================================================================
    leader_totals = {"pnl": Decimal("0"), "sell_pnl": Decimal("0"),
                     "res_pnl": Decimal("0"), "buy_cost": Decimal("0")}
    mirror_totals = {"pnl": Decimal("0"), "sell_pnl": Decimal("0"),
                     "res_pnl": Decimal("0"), "buy_cost": Decimal("0")}

    # Strategy variants to test
    BASE_CONFIG = {
        "scaling.our_capital": 50, "scaling.hourly_budget": 45,
        "scaling.k_factor": 1, "scaling.leader_estimated_capital": 900,
    }

    # Collect per-hour data
    hour_data = []
    by_split = defaultdict(lambda: {"leader": Decimal("0"), "mirror": Decimal("0"),
                                      "current": 0.0, "hours": 0})

    for session, utc_h, hf in all_hours:
        split = get_split(session)
        leader_trades, last_prices = parse_leader_trades(hf)
        if not leader_trades:
            continue

        leader_pnl = compute_leader_pnl(leader_trades, last_prices)
        mirror_pnl = compute_perfect_mirror_pnl(
            leader_trades, last_prices,
            our_capital=Decimal("50"), leader_capital=Decimal("900"))

        # Run current strategy
        current = run_strategy_variant(hf, BASE_CONFIG)
        current_pnl = current["pnl"] if current else 0.0

        leader_totals["pnl"] += leader_pnl["total_pnl"]
        leader_totals["sell_pnl"] += leader_pnl["sell_pnl"]
        leader_totals["res_pnl"] += leader_pnl["resolution_pnl"]
        leader_totals["buy_cost"] += leader_pnl["buy_cost"]

        mirror_totals["pnl"] += mirror_pnl["total_pnl"]
        mirror_totals["sell_pnl"] += mirror_pnl["sell_pnl"]
        mirror_totals["res_pnl"] += mirror_pnl["resolution_pnl"]
        mirror_totals["buy_cost"] += mirror_pnl["buy_cost"]

        by_split[split]["leader"] += leader_pnl["total_pnl"]
        by_split[split]["mirror"] += mirror_pnl["total_pnl"]
        by_split[split]["current"] += current_pnl
        by_split[split]["hours"] += 1

        hour_data.append({
            "session": session, "hour": utc_h, "split": split, "file": hf,
            "leader_pnl": leader_pnl, "mirror_pnl": mirror_pnl,
            "current_pnl": current_pnl,
            "leader_trades": leader_trades, "last_prices": last_prices,
            "current_detail": current,
        })

    # Print leader totals
    print(f"\n  1. LEADER'S ACTUAL PnL (all {len(hour_data)} hours)")
    print(f"     Total PnL:      ${float(leader_totals['pnl']):>+10.2f}")
    print(f"     From sells:     ${float(leader_totals['sell_pnl']):>+10.2f}")
    print(f"     From resolution:${float(leader_totals['res_pnl']):>+10.2f}")
    print(f"     Total bought:   ${float(leader_totals['buy_cost']):>10.2f}")
    print(f"     Per hour avg:   ${float(leader_totals['pnl'])/max(1,len(hour_data)):>+10.2f}")

    # =====================================================================
    # SECTION 2: PERFECT MIRROR vs CURRENT vs LEADER (by split)
    # =====================================================================
    print(f"\n  2. COMPARISON BY SPLIT")
    print(f"     {'Split':<10} | {'Hours':>5} | {'Leader$':>10} | {'Mirror$':>10} | {'Current$':>10} | {'Mirror/Ldr':>10} | {'Cur/Mirror':>10}")
    print(f"     {'-'*80}")

    for split in ["TRAIN", "TEST", "HOLDOUT"]:
        s = by_split[split]
        if s["hours"] == 0:
            continue
        mirror_ldr = float(s["mirror"] / s["leader"] * 100) if s["leader"] != 0 else 0
        cur_mirror = (s["current"] / float(s["mirror"]) * 100) if float(s["mirror"]) != 0 else 0
        print(f"     {split:<10} | {s['hours']:>5} | ${float(s['leader']):>+9.2f} | ${float(s['mirror']):>+9.2f} | ${s['current']:>+9.2f} | {mirror_ldr:>8.1f}% | {cur_mirror:>8.1f}%")

    total_leader = float(leader_totals["pnl"])
    total_mirror = float(mirror_totals["pnl"])
    total_current = sum(h["current_pnl"] for h in hour_data)
    mirror_ldr = (total_mirror / total_leader * 100) if total_leader != 0 else 0
    cur_mirror = (total_current / total_mirror * 100) if total_mirror != 0 else 0
    print(f"     {'TOTAL':<10} | {len(hour_data):>5} | ${total_leader:>+9.2f} | ${total_mirror:>+9.2f} | ${total_current:>+9.2f} | {mirror_ldr:>8.1f}% | {cur_mirror:>8.1f}%")

    gap_mirror_to_current = total_mirror - total_current
    print(f"\n     GAP (mirror - current): ${gap_mirror_to_current:>+.2f} — money we're LEAVING on the table")
    print(f"     GAP (leader - current): ${total_leader - total_current:>+.2f}")

    # =====================================================================
    # SECTION 3: HOUR-BY-HOUR TABLE
    # =====================================================================
    print(f"\n  3. HOUR-BY-HOUR COMPARISON (sorted by leader PnL)")
    print(f"     {'Session':<26} {'H':>2} {'Split':<6} | {'Leader$':>8} {'Mirror$':>8} {'Current$':>8} | {'MirCap':>7} {'CurCap':>7}")
    print(f"     {'-'*100}")

    for h in sorted(hour_data, key=lambda x: float(x["leader_pnl"]["total_pnl"]), reverse=True):
        lp = float(h["leader_pnl"]["total_pnl"])
        mp = float(h["mirror_pnl"]["total_pnl"])
        cp = h["current_pnl"]
        mir_cap = (mp / lp * 100) if lp != 0 else 0
        cur_cap = (cp / lp * 100) if lp != 0 else 0
        marker = ""
        if lp > 10 and cp < 0:
            marker = " *** MISSED"
        elif lp > 10 and cp < mp * 0.3:
            marker = " * LOW CAPTURE"
        print(f"     {h['session']:<26} {h['hour']:>2} {h['split']:<6} | ${lp:>+7.2f} ${mp:>+7.2f} ${cp:>+7.2f} | {mir_cap:>6.1f}% {cur_cap:>6.1f}%{marker}")

    # =====================================================================
    # SECTION 4: GAP ANALYSIS — WHY we capture less than perfect mirror
    # =====================================================================
    print(f"\n  4. GAP ANALYSIS — Why do we capture less than perfect mirror?")

    # Aggregate skip reasons from current strategy
    all_skips = defaultdict(int)
    for h in hour_data:
        if h["current_detail"]:
            for reason, cnt in h["current_detail"]["skip_reasons"].items():
                all_skips[reason] += cnt

    total_skips = sum(all_skips.values())
    print(f"\n     Skip reasons (total {total_skips} skips):")
    for reason, cnt in sorted(all_skips.items(), key=lambda x: -x[1])[:15]:
        pct = cnt / total_skips * 100 if total_skips > 0 else 0
        print(f"       {reason:<30}: {cnt:>6} ({pct:>5.1f}%)")

    # =====================================================================
    # SECTION 5: TEST VARIANT STRATEGIES
    # =====================================================================
    print(f"\n  5. STRATEGY VARIANTS — What if we change parameters?")
    print(f"     Testing on ALL hours to understand ceiling...\n")

    variants = [
        ("Current (base)", BASE_CONFIG),
        ("min_pct=1.0%", {**BASE_CONFIG, "min_leader_trade_pct": 1.0}),
        ("min_pct=0.5%", {**BASE_CONFIG, "min_leader_trade_pct": 0.5}),
        ("min_pct=0%", {**BASE_CONFIG, "min_leader_trade_pct": 0.0}),
        ("capital=$100", {**BASE_CONFIG, "scaling.our_capital": 100, "scaling.hourly_budget": 90}),
        ("capital=$200", {**BASE_CONFIG, "scaling.our_capital": 200, "scaling.hourly_budget": 180}),
        ("cap$100+min1%", {**BASE_CONFIG, "scaling.our_capital": 100, "scaling.hourly_budget": 90,
                           "min_leader_trade_pct": 1.0}),
        ("cap$100+min0.5%", {**BASE_CONFIG, "scaling.our_capital": 100, "scaling.hourly_budget": 90,
                              "min_leader_trade_pct": 0.5}),
        ("skip_low=0.35", {**BASE_CONFIG, "skip_price_low": 0.35}),
        ("skip_low=0.55", {**BASE_CONFIG, "skip_price_low": 0.55}),
        ("boost=12x", {**BASE_CONFIG, "scale_boost": 12}),
        ("boost=16x", {**BASE_CONFIG, "scale_boost": 16}),
        ("mkt_cap=70%", {**BASE_CONFIG, "per_market_cap_pct": 70}),
        ("mkt_cap=90%", {**BASE_CONFIG, "per_market_cap_pct": 90}),
    ]

    # We need to patch strategy constants for some variants
    import src.strategies.profit_taker.strategy as pt_mod

    variant_results = {}
    for vname, vconfig in variants:
        # Save originals
        orig_min_pct = pt_mod.MIN_LEADER_TRADE_PCT
        orig_skip_low = pt_mod.SKIP_PRICE_LOW
        orig_boost = pt_mod.SCALE_BOOST
        orig_mkt_cap = pt_mod.PER_MARKET_CAP_PCT

        # Apply variant overrides to module-level constants
        if "min_leader_trade_pct" in vconfig:
            pt_mod.MIN_LEADER_TRADE_PCT = Decimal(str(vconfig.pop("min_leader_trade_pct")))
        if "skip_price_low" in vconfig:
            pt_mod.SKIP_PRICE_LOW = Decimal(str(vconfig.pop("skip_price_low")))
        if "scale_boost" in vconfig:
            pt_mod.SCALE_BOOST = Decimal(str(vconfig.pop("scale_boost")))
        if "per_market_cap_pct" in vconfig:
            pt_mod.PER_MARKET_CAP_PCT = Decimal(str(vconfig.pop("per_market_cap_pct")))

        split_pnl = {"TRAIN": 0.0, "TEST": 0.0, "HOLDOUT": 0.0}
        split_hours = {"TRAIN": 0, "TEST": 0, "HOLDOUT": 0}
        total_buys = 0

        for h in hour_data:
            result = run_strategy_variant(h["file"], {
                "scaling.our_capital": vconfig.get("scaling.our_capital", 50),
                "scaling.hourly_budget": vconfig.get("scaling.hourly_budget", 45),
                "scaling.k_factor": 1,
                "scaling.leader_estimated_capital": 900,
            })
            if result:
                split_pnl[h["split"]] += result["pnl"]
                split_hours[h["split"]] += 1
                total_buys += result["buys"]

        # Restore
        pt_mod.MIN_LEADER_TRADE_PCT = orig_min_pct
        pt_mod.SKIP_PRICE_LOW = orig_skip_low
        pt_mod.SCALE_BOOST = orig_boost
        pt_mod.PER_MARKET_CAP_PCT = orig_mkt_cap

        combined = split_pnl["TRAIN"] + split_pnl["TEST"] + split_pnl["HOLDOUT"]
        total_hrs = split_hours["TRAIN"] + split_hours["TEST"] + split_hours["HOLDOUT"]
        variant_results[vname] = {
            "train": split_pnl["TRAIN"], "test": split_pnl["TEST"],
            "holdout": split_pnl["HOLDOUT"], "combined": combined,
            "hours": total_hrs, "buys": total_buys,
        }

    # Print variant results
    print(f"     {'Variant':<22} | {'Train$':>8} {'Test$':>8} {'Hold$':>8} | {'Combined$':>10} {'$/hr':>6} | {'Buys':>5} | {'Robust':>6}")
    print(f"     {'-'*100}")
    for vname, vr in sorted(variant_results.items(), key=lambda x: -x[1]["combined"]):
        robust = "YES" if vr["train"] > 0 and vr["test"] > 0 and vr["holdout"] > 0 else "no"
        per_hr = vr["combined"] / max(1, vr["hours"])
        print(f"     {vname:<22} | ${vr['train']:>+7.2f} ${vr['test']:>+7.2f} ${vr['holdout']:>+7.2f} | ${vr['combined']:>+9.2f} ${per_hr:>+5.2f} | {vr['buys']:>5} | {robust:>6}")

    # =====================================================================
    # SECTION 6: POSITION SIMILARITY — Token by token
    # =====================================================================
    print(f"\n  6. POSITION SIMILARITY — Do we enter the same tokens as leader?")

    total_leader_tokens = 0
    total_we_entered = 0
    total_we_missed = 0
    missed_token_pnl = Decimal("0")
    entered_token_pnl = Decimal("0")
    missed_by_price = defaultdict(lambda: {"count": 0, "leader_pnl": Decimal("0")})

    for h in hour_data:
        if not h["current_detail"]:
            continue
        # Leader's tokens this hour
        leader_tokens = set()
        leader_token_entry_price = {}
        for t in h["leader_trades"]:
            if t["action"] == "BUY":
                leader_tokens.add(t["token_id"])
                if t["token_id"] not in leader_token_entry_price:
                    leader_token_entry_price[t["token_id"]] = t["price"]

        total_leader_tokens += len(leader_tokens)

        # Our tokens this hour — check which leader tokens we entered
        # Build from leader trades + our decisions
        our_entered_tokens = set()
        # Parse from skip reasons: if we bought, we entered
        for t in h["leader_trades"]:
            if t["action"] == "BUY" and t["token_id"] in leader_tokens:
                # Check if we have a position in this token by looking at leader_pnl positions
                pass  # Need a different approach

        # Better: count our buys and which tokens they correspond to
        # Re-run isn't needed — we can use current_detail skip_reasons loosely
        # But actually we need to know WHICH tokens we entered
        # Let's track at a higher level using leader positions

        for pos in h["leader_pnl"]["positions_at_end"]:
            token_id = pos["token_id"]
            entry_price = pos["entry_price"]
            # Categorize
            if entry_price < Decimal("0.45"):
                pk = "<0.45"
            elif entry_price < Decimal("0.65"):
                pk = "0.45-0.65"
            else:
                pk = "0.65+"

        # Track missed vs entered at token level using the mirror approach
        # If mirror PnL ≈ current PnL for a token, we captured it
        # If mirror PnL >> current PnL, we missed it

    # A simpler approach: count unique tokens with leader buys vs our buys per hour
    print(f"\n     (Tracking leader tokens entered vs our tokens entered)")

    token_match_hours = 0
    token_mismatch_hours = 0
    for h in hour_data:
        leader_buy_tokens = set()
        for t in h["leader_trades"]:
            if t["action"] == "BUY":
                leader_buy_tokens.add(t["token_id"])

        our_buy_count = h["current_detail"]["buys"] if h["current_detail"] else 0
        leader_unique = len(leader_buy_tokens)

        if our_buy_count > 0 and leader_unique > 0:
            token_match_hours += 1
        elif leader_unique > 0 and our_buy_count == 0:
            token_mismatch_hours += 1

    print(f"     Hours where we trade at all: {token_match_hours}/{len(hour_data)}")
    print(f"     Hours where leader trades but we don't: {token_mismatch_hours}")

    # =====================================================================
    # SECTION 7: BIGGEST OPPORTUNITIES
    # =====================================================================
    print(f"\n  7. BIGGEST MISSED OPPORTUNITIES (leader won big, we didn't)")
    print(f"     {'Session':<26} {'H':>2} | {'Leader$':>8} {'Mirror$':>8} {'Us$':>8} | {'Gap$':>8}")
    print(f"     {'-'*80}")

    biggest_gaps = sorted(hour_data,
                          key=lambda x: float(x["mirror_pnl"]["total_pnl"]) - x["current_pnl"],
                          reverse=True)[:15]
    for h in biggest_gaps:
        lp = float(h["leader_pnl"]["total_pnl"])
        mp = float(h["mirror_pnl"]["total_pnl"])
        cp = h["current_pnl"]
        gap = mp - cp
        print(f"     {h['session']:<26} {h['hour']:>2} | ${lp:>+7.2f} ${mp:>+7.2f} ${cp:>+7.2f} | ${gap:>+7.2f}")
        # Show leader's positions
        for pos in h["leader_pnl"]["positions_at_end"][:3]:
            entry = float(pos["entry_price"])
            pnl = float(pos["pnl"])
            print(f"       ...{pos['token_id'][-8:]} entry=${entry:.3f} -> {pos['outcome']} (PnL ${pnl:+.2f})")

    # =====================================================================
    # SECTION 8: LEADER'S EDGE BY HOUR OF DAY
    # =====================================================================
    print(f"\n  8. LEADER PnL BY UTC HOUR")
    by_utc_hour = defaultdict(lambda: {"leader": Decimal("0"), "mirror": Decimal("0"),
                                         "current": 0.0, "count": 0})
    for h in hour_data:
        bh = by_utc_hour[h["hour"]]
        bh["leader"] += h["leader_pnl"]["total_pnl"]
        bh["mirror"] += h["mirror_pnl"]["total_pnl"]
        bh["current"] += h["current_pnl"]
        bh["count"] += 1

    print(f"     {'UTC Hour':>8} | {'Count':>5} | {'Leader$/hr':>10} | {'Mirror$/hr':>10} | {'Cur$/hr':>10}")
    print(f"     {'-'*65}")
    for hr in sorted(by_utc_hour.keys()):
        bh = by_utc_hour[hr]
        n = max(1, bh["count"])
        print(f"     {hr:>8} | {bh['count']:>5} | ${float(bh['leader'])/n:>+9.2f} | ${float(bh['mirror'])/n:>+9.2f} | ${bh['current']/n:>+9.2f}")

    # =====================================================================
    # SECTION 9: SUMMARY & RECOMMENDATIONS
    # =====================================================================
    print(f"\n{'='*130}")
    print(f"  SUMMARY & RECOMMENDATIONS")
    print(f"{'='*130}")
    print(f"\n  Leader total PnL:    ${total_leader:>+10.2f} ({len(hour_data)} hours)")
    print(f"  Perfect mirror PnL:  ${total_mirror:>+10.2f} (proportional copy at 50/900)")
    print(f"  Our current PnL:     ${total_current:>+10.2f} (with all filters)")
    print(f"  Mirror capture rate: {mirror_ldr:.1f}% of leader")
    print(f"  Our capture rate:    {cur_mirror:.1f}% of mirror, {(total_current/total_leader*100) if total_leader != 0 else 0:.1f}% of leader")
    print(f"\n  Best variant: {max(variant_results.items(), key=lambda x: x[1]['combined'])[0]}")
    best = max(variant_results.items(), key=lambda x: x[1]["combined"])
    print(f"  Best combined PnL: ${best[1]['combined']:+.2f}")


if __name__ == "__main__":
    main()
