"""Analyze leader's buy-sell-rebuy momentum accumulation pattern.

Hypothesis: Leader buys, sells a portion at profit, then rebuys on the dip.
Net effect: more shares at better average price + locked-in profit.

Questions:
1. How often does leader rebuy after selling on the same token?
2. Is the rebuy price lower than the sell price? (confirming dip-buying)
3. What's the net share accumulation from this pattern?
4. On tokens with this pattern, what's the resolution WR?
5. Can we detect this pattern in real-time and follow?
"""
import sys
import json
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

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

    print(f"Analyzing momentum accumulation across {len(all_hours)} hours...\n")

    all_tokens = []  # Per-token analysis

    for session, utc_h, hf, split in all_hours:
        events = []
        with open(hf, 'r') as f:
            for line in f:
                try: events.append(json.loads(line))
                except: continue

        # Parse trades in chronological order per token
        token_trades = defaultdict(list)  # token_id -> [(minute, second, action, price, shares, dollars)]
        end_prices = {}

        for evt in events:
            if evt.get("type") == "leader_trade":
                lt = evt.get("leader_trade", {})
                ts_str = lt.get("timestamp", evt.get("timestamp", ""))
                minute = 0
                second = 0
                if "T" in ts_str:
                    parts = ts_str.split("T")[1].split(":")
                    if len(parts) >= 2:
                        minute = int(parts[1])
                        if len(parts) > 2:
                            sec_str = parts[2].split(".")[0].split("+")[0].split("-")[0]
                            second = int(sec_str)

                token_id = lt.get("token_id", "")
                action = lt.get("action", "")
                price = Decimal(str(lt.get("leader_price", 0)))
                shares = Decimal(str(lt.get("leader_shares", 0)))
                dollars = Decimal(str(lt.get("leader_dollars", 0)))

                token_trades[token_id].append({
                    "minute": minute, "second": second,
                    "action": action, "price": price,
                    "shares": shares, "dollars": dollars,
                })

            elif evt.get("type") == "price_snapshot":
                prices = evt.get("prices", {})
                for token_id, pdata in prices.items():
                    if isinstance(pdata, dict) and pdata.get("bid") is not None:
                        end_prices[token_id] = Decimal(str(pdata["bid"]))

        # Analyze each token's trade sequence
        for token_id, trades in token_trades.items():
            buys = [t for t in trades if t["action"] == "BUY"]
            sells = [t for t in trades if t["action"] == "SELL"]

            if not buys:
                continue

            total_buy_dollars = sum(t["dollars"] for t in buys)
            total_buy_shares = sum(t["shares"] for t in buys)
            total_sell_dollars = sum(t["dollars"] for t in sells)
            total_sell_shares = sum(t["shares"] for t in sells)

            if total_buy_shares == 0:
                continue

            avg_buy = total_buy_dollars / total_buy_shares
            avg_sell = total_sell_dollars / total_sell_shares if total_sell_shares > 0 else Decimal("0")

            # Detect buy-sell-rebuy sequences
            # Walk through trades chronologically
            has_sold = False
            rebuys_after_sell = []
            sells_list = []
            running_shares = Decimal("0")
            running_cost = Decimal("0")
            peak_shares = Decimal("0")

            # Track the actual sequence
            sequence = []  # List of (action, price, minute) for pattern detection
            for t in trades:
                sequence.append(t["action"])
                if t["action"] == "BUY":
                    running_shares += t["shares"]
                    running_cost += t["dollars"]
                    peak_shares = max(peak_shares, running_shares)
                    if has_sold:
                        # This is a REBUY after sell
                        rebuys_after_sell.append(t)
                elif t["action"] == "SELL":
                    has_sold = True
                    sells_list.append(t)
                    running_shares -= t["shares"]

            # Classify pattern
            has_rebuy = len(rebuys_after_sell) > 0
            rebuy_dollars = sum(t["dollars"] for t in rebuys_after_sell)
            rebuy_shares = sum(t["shares"] for t in rebuys_after_sell)

            # Compare rebuy prices vs sell prices
            avg_rebuy_price = (rebuy_dollars / rebuy_shares) if rebuy_shares > 0 else Decimal("0")
            avg_sell_price = avg_sell

            # Resolution
            ep = end_prices.get(token_id)
            last_bid = float(ep) if ep else 0.50
            resolution = "WIN" if last_bid >= 0.50 else "LOSE"

            remaining = total_buy_shares - total_sell_shares
            res_price = Decimal("0.99") if resolution == "WIN" else Decimal("0.01")
            remaining_value = remaining * res_price if remaining > 0 else Decimal("0")
            net_pnl = float(total_sell_dollars + remaining_value - total_buy_dollars)

            all_tokens.append({
                "hour": f"{session}_h{utc_h}",
                "split": split,
                "token_id": token_id[-8:],
                "total_buy": float(total_buy_dollars),
                "total_sell": float(total_sell_dollars),
                "buy_count": len(buys),
                "sell_count": len(sells),
                "avg_buy": float(avg_buy),
                "avg_sell": float(avg_sell),
                "has_rebuy": has_rebuy,
                "rebuy_count": len(rebuys_after_sell),
                "rebuy_dollars": float(rebuy_dollars),
                "avg_rebuy_price": float(avg_rebuy_price),
                "resolution": resolution,
                "net_pnl": net_pnl,
                "remaining_shares": float(remaining),
                "total_shares_bought": float(total_buy_shares),
                "peak_shares": float(peak_shares),
                "sequence_summary": "".join("B" if a == "BUY" else "S" for a in sequence[:20]),
            })

    # =========================================================================
    # REPORT
    # =========================================================================
    print(f"{'='*120}")
    print(f"  1. HOW OFTEN DOES LEADER REBUY AFTER SELLING?")
    print(f"{'='*120}")
    with_sells = [t for t in all_tokens if t["sell_count"] > 0]
    with_rebuy = [t for t in all_tokens if t["has_rebuy"]]
    no_rebuy = [t for t in with_sells if not t["has_rebuy"]]
    no_sells = [t for t in all_tokens if t["sell_count"] == 0]

    print(f"  Total token-hours: {len(all_tokens)}")
    print(f"  No sells at all:   {len(no_sells)} ({len(no_sells)/len(all_tokens)*100:.0f}%)")
    print(f"  Sells, NO rebuy:   {len(no_rebuy)} ({len(no_rebuy)/len(all_tokens)*100:.0f}%)")
    print(f"  Sells + REBUY:     {len(with_rebuy)} ({len(with_rebuy)/len(all_tokens)*100:.0f}%)")

    # WR comparison
    rebuy_wins = sum(1 for t in with_rebuy if t["resolution"] == "WIN")
    no_rebuy_wins = sum(1 for t in no_rebuy if t["resolution"] == "WIN")
    no_sell_wins = sum(1 for t in no_sells if t["resolution"] == "WIN")

    print(f"\n  Resolution WR:")
    print(f"    No sells:      {no_sell_wins}/{len(no_sells)} ({no_sell_wins/max(1,len(no_sells))*100:.0f}%)")
    print(f"    Sell, no rebuy: {no_rebuy_wins}/{len(no_rebuy)} ({no_rebuy_wins/max(1,len(no_rebuy))*100:.0f}%)")
    print(f"    Sell + REBUY:   {rebuy_wins}/{len(with_rebuy)} ({rebuy_wins/max(1,len(with_rebuy))*100:.0f}%)")

    # PnL
    print(f"\n  Net PnL:")
    print(f"    No sells:       ${sum(t['net_pnl'] for t in no_sells):>+10.2f} (avg ${sum(t['net_pnl'] for t in no_sells)/max(1,len(no_sells)):>+.2f})")
    print(f"    Sell, no rebuy: ${sum(t['net_pnl'] for t in no_rebuy):>+10.2f} (avg ${sum(t['net_pnl'] for t in no_rebuy)/max(1,len(no_rebuy)):>+.2f})")
    print(f"    Sell + REBUY:   ${sum(t['net_pnl'] for t in with_rebuy):>+10.2f} (avg ${sum(t['net_pnl'] for t in with_rebuy)/max(1,len(with_rebuy)):>+.2f})")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  2. REBUY PRICE vs SELL PRICE (is leader buying dips?)")
    print(f"{'='*120}")
    rebuy_with_prices = [t for t in with_rebuy if t["avg_rebuy_price"] > 0 and t["avg_sell"] > 0]
    if rebuy_with_prices:
        dip_buyers = [t for t in rebuy_with_prices if t["avg_rebuy_price"] < t["avg_sell"]]
        expensive_rebuyers = [t for t in rebuy_with_prices if t["avg_rebuy_price"] >= t["avg_sell"]]

        print(f"  Tokens with rebuy price data: {len(rebuy_with_prices)}")
        print(f"  Rebuy CHEAPER than sell (dip buy):    {len(dip_buyers)} ({len(dip_buyers)/len(rebuy_with_prices)*100:.0f}%)")
        print(f"  Rebuy MORE EXPENSIVE than sell:       {len(expensive_rebuyers)} ({len(expensive_rebuyers)/len(rebuy_with_prices)*100:.0f}%)")

        # Average price improvement
        improvements = [t["avg_sell"] - t["avg_rebuy_price"] for t in rebuy_with_prices]
        print(f"\n  Avg sell-rebuy gap: ${sum(improvements)/len(improvements):+.4f}")
        print(f"  (positive = rebuy cheaper = dip buying)")

        # WR by dip vs expensive rebuy
        dip_wins = sum(1 for t in dip_buyers if t["resolution"] == "WIN")
        exp_wins = sum(1 for t in expensive_rebuyers if t["resolution"] == "WIN")
        print(f"\n  Dip buyers:      {dip_wins}/{len(dip_buyers)} WIN ({dip_wins/max(1,len(dip_buyers))*100:.0f}%)")
        print(f"  Expensive rebuy: {exp_wins}/{len(expensive_rebuyers)} WIN ({exp_wins/max(1,len(expensive_rebuyers))*100:.0f}%)")

        # PnL
        dip_pnl = sum(t["net_pnl"] for t in dip_buyers)
        exp_pnl = sum(t["net_pnl"] for t in expensive_rebuyers)
        print(f"  Dip buyer PnL:      ${dip_pnl:>+10.2f}")
        print(f"  Expensive rebuy PnL: ${exp_pnl:>+10.2f}")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  3. REBUY PATTERN BY CONVICTION LEVEL (spend bucket)")
    print(f"{'='*120}")
    for lo, hi in [(0, 100), (100, 200), (200, 300), (300, 500), (500, 1000), (1000, 99999)]:
        bucket = [t for t in all_tokens if lo <= t["total_buy"] < hi]
        if not bucket: continue
        rebuys = [t for t in bucket if t["has_rebuy"]]
        wins_all = sum(1 for t in bucket if t["resolution"] == "WIN")
        wins_rebuy = sum(1 for t in rebuys if t["resolution"] == "WIN")
        pnl_all = sum(t["net_pnl"] for t in bucket)
        pnl_rebuy = sum(t["net_pnl"] for t in rebuys)
        label = f"${lo}-${hi}" if hi < 99999 else f"${lo}+"
        print(f"  {label:<12} Total={len(bucket):>3} Rebuys={len(rebuys):>3} ({len(rebuys)/max(1,len(bucket))*100:.0f}%) | allWR={wins_all}/{len(bucket)} ({wins_all/max(1,len(bucket))*100:.0f}%) rebuyWR={wins_rebuy}/{max(1,len(rebuys))} ({wins_rebuy/max(1,len(rebuys))*100:.0f}%) | allPnL=${pnl_all:>+8.2f} rebuyPnL=${pnl_rebuy:>+8.2f}")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  4. TRADE SEQUENCE PATTERNS (first 20 actions)")
    print(f"{'='*120}")
    # Count common patterns
    pattern_counts = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0, "spend": []})
    for t in all_tokens:
        seq = t["sequence_summary"][:10]  # First 10 actions
        pc = pattern_counts[seq]
        pc["count"] += 1
        if t["resolution"] == "WIN":
            pc["wins"] += 1
        pc["pnl"] += t["net_pnl"]
        pc["spend"].append(t["total_buy"])

    # Sort by frequency
    sorted_patterns = sorted(pattern_counts.items(), key=lambda x: -x[1]["count"])
    print(f"  Top 20 trade sequences (B=buy, S=sell):")
    print(f"  {'Pattern':<15} {'Count':>5} {'WR':>6} {'PnL':>10} {'AvgSpend':>10}")
    for pat, info in sorted_patterns[:20]:
        wr = info["wins"] / max(1, info["count"]) * 100
        avg_spend = sum(info["spend"]) / len(info["spend"])
        print(f"  {pat:<15} {info['count']:>5} {wr:>5.0f}% ${info['pnl']:>+9.2f} ${avg_spend:>9.2f}")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  5. REAL-TIME SIGNAL: REBUY AS CONVICTION CONFIRMATION")
    print(f"{'='*120}")
    print(f"  Idea: If leader sells then rebuys the SAME token, that's a strong")
    print(f"  confirmation signal. Can we use this to enter even on low-conviction tokens?")

    # For low-conviction tokens (< $300 spend), how does rebuy affect WR?
    low_conv = [t for t in all_tokens if t["total_buy"] < 300]
    low_conv_rebuy = [t for t in low_conv if t["has_rebuy"]]
    low_conv_no_rebuy = [t for t in low_conv if t["sell_count"] > 0 and not t["has_rebuy"]]
    low_conv_no_sell = [t for t in low_conv if t["sell_count"] == 0]

    print(f"\n  LOW CONVICTION (<$300 spend):")
    print(f"    No sells:      {len(low_conv_no_sell)} tokens, WR={sum(1 for t in low_conv_no_sell if t['resolution']=='WIN')}/{len(low_conv_no_sell)} ({sum(1 for t in low_conv_no_sell if t['resolution']=='WIN')/max(1,len(low_conv_no_sell))*100:.0f}%)")
    print(f"    Sell, no rebuy: {len(low_conv_no_rebuy)} tokens, WR={sum(1 for t in low_conv_no_rebuy if t['resolution']=='WIN')}/{len(low_conv_no_rebuy)} ({sum(1 for t in low_conv_no_rebuy if t['resolution']=='WIN')/max(1,len(low_conv_no_rebuy))*100:.0f}%)")
    print(f"    Sell + REBUY:   {len(low_conv_rebuy)} tokens, WR={sum(1 for t in low_conv_rebuy if t['resolution']=='WIN')}/{len(low_conv_rebuy)} ({sum(1 for t in low_conv_rebuy if t['resolution']=='WIN')/max(1,len(low_conv_rebuy))*100:.0f}%)")

    # High conviction (>= $300)
    high_conv = [t for t in all_tokens if t["total_buy"] >= 300]
    high_conv_rebuy = [t for t in high_conv if t["has_rebuy"]]
    high_conv_no_rebuy = [t for t in high_conv if t["sell_count"] > 0 and not t["has_rebuy"]]

    print(f"\n  HIGH CONVICTION (>=$300 spend):")
    print(f"    Sell, no rebuy: {len(high_conv_no_rebuy)} tokens, WR={sum(1 for t in high_conv_no_rebuy if t['resolution']=='WIN')}/{len(high_conv_no_rebuy)} ({sum(1 for t in high_conv_no_rebuy if t['resolution']=='WIN')/max(1,len(high_conv_no_rebuy))*100:.0f}%)")
    print(f"    Sell + REBUY:   {len(high_conv_rebuy)} tokens, WR={sum(1 for t in high_conv_rebuy if t['resolution']=='WIN')}/{len(high_conv_rebuy)} ({sum(1 for t in high_conv_rebuy if t['resolution']=='WIN')/max(1,len(high_conv_rebuy))*100:.0f}%)")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  6. CUMULATIVE SPEND TRAJECTORY — DO REBUYS PUSH OVER $300?")
    print(f"{'='*120}")
    # For tokens that start below $300 but with rebuys push over
    for t in all_tokens:
        # Check if non-rebuy portion is under $300 but total is over
        pre_rebuy_spend = t["total_buy"] - t["rebuy_dollars"]
        t["pre_rebuy_spend"] = pre_rebuy_spend

    # Tokens where initial buys < $300 but rebuys push total over $300
    pushed_over = [t for t in all_tokens if t["pre_rebuy_spend"] < 300 and t["total_buy"] >= 300 and t["has_rebuy"]]
    stayed_under = [t for t in all_tokens if t["total_buy"] < 300 and t["has_rebuy"]]
    already_over = [t for t in all_tokens if t["pre_rebuy_spend"] >= 300 and t["has_rebuy"]]

    print(f"  Tokens with rebuy: {len(with_rebuy)}")
    print(f"    Already over $300 before rebuy:  {len(already_over)}")
    print(f"    Rebuys PUSHED over $300:         {len(pushed_over)}")
    print(f"    Stayed under $300 after rebuy:   {len(stayed_under)}")

    if pushed_over:
        pw = sum(1 for t in pushed_over if t["resolution"] == "WIN")
        print(f"\n  Tokens pushed over $300 by rebuys: WR={pw}/{len(pushed_over)} ({pw/len(pushed_over)*100:.0f}%)")
        print(f"  Net PnL: ${sum(t['net_pnl'] for t in pushed_over):+.2f}")
        print(f"  These are tokens where OUR conviction filter catches them BECAUSE of the rebuy")

    # What about tokens where rebuy happens but stays under $300?
    if stayed_under:
        sw = sum(1 for t in stayed_under if t["resolution"] == "WIN")
        print(f"\n  Tokens with rebuy but stayed under $300: WR={sw}/{len(stayed_under)} ({sw/len(stayed_under)*100:.0f}%)")
        print(f"  Net PnL: ${sum(t['net_pnl'] for t in stayed_under):+.2f}")
        print(f"  → Could REBUY be used as an alternative conviction signal?")

    # =========================================================================
    print(f"\n{'='*120}")
    print(f"  7. DETAILED EXAMPLES OF BUY-SELL-REBUY PATTERNS")
    print(f"{'='*120}")

    # Show a few examples
    examples = sorted(with_rebuy, key=lambda x: -x["total_buy"])[:5]
    for t in examples:
        print(f"\n  Token ...{t['token_id']} in {t['hour']} [{t['split']}]")
        print(f"    Sequence: {t['sequence_summary']}")
        print(f"    Total buy: ${t['total_buy']:.2f} ({t['buy_count']} buys), Sells: ${t['total_sell']:.2f} ({t['sell_count']} sells)")
        print(f"    Avg buy: ${t['avg_buy']:.4f}, Avg sell: ${t['avg_sell']:.4f}, Avg rebuy: ${t['avg_rebuy_price']:.4f}")
        print(f"    Rebuys: {t['rebuy_count']} for ${t['rebuy_dollars']:.2f}")
        print(f"    Resolution: {t['resolution']}, Net PnL: ${t['net_pnl']:+.2f}")
        if t["avg_rebuy_price"] > 0 and t["avg_sell"] > 0:
            gap = t["avg_sell"] - t["avg_rebuy_price"]
            print(f"    Sell→Rebuy price gap: ${gap:+.4f} ({'CHEAPER rebuy' if gap > 0 else 'MORE EXPENSIVE rebuy'})")


if __name__ == "__main__":
    main()
