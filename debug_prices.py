"""Check if the token prices in all_prices match what's in the raw session file."""
import sys
import json
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).parent))

from src.framework.replay.loader import SessionLoader

path = Path("data/sessions/2026-02-04/02-35.jsonl")
loader = SessionLoader(path)
loader.load()

# Find the token ending in 82561879 (from the debug output)
# And check what its actual bid values are across time
target_suffix = "82561879"

print(f"=== Searching for token ending in '{target_suffix}' ===\n")

# Check raw price snapshots
print("--- Raw price_snapshot entries containing this token ---")
with open(path) as f:
    count = 0
    for line in f:
        obj = json.loads(line)
        if obj["type"] == "price_snapshot":
            prices = obj.get("prices", {})
            for tid, vals in prices.items():
                if tid.endswith(target_suffix):
                    ts = obj["timestamp"]
                    bid = vals.get("bid", "?")
                    ask = vals.get("ask", "?")
                    print(f"  {ts}  bid={bid}  ask={ask}  token=...{tid[-20:]}")
                    count += 1
                    if count > 20:
                        break
        if count > 20:
            break

# Check leader trades for this token
print(f"\n--- Leader trades for this token ---")
with open(path) as f:
    count = 0
    for line in f:
        obj = json.loads(line)
        if obj["type"] == "leader_trade":
            lt = obj.get("leader_trade", {})
            tid = lt.get("token_id", "")
            if tid.endswith(target_suffix):
                ts = obj["timestamp"]
                action = lt.get("action")
                dollars = lt.get("leader_dollars")
                price = lt.get("leader_price")
                pc = obj.get("price_context", {})
                pc_bid = pc.get("bid", "?")
                pc_ask = pc.get("ask", "?")
                print(f"  {ts}  {action} ${dollars} @{price}  context: bid={pc_bid} ask={pc_ask}")
                count += 1
                if count > 10:
                    break

# Now check what get_all_prices_at_time returns for the last H10 event
print(f"\n--- all_prices at end of H10 for ALL tokens ---")
# Find the last H10 event
last_h10_event = None
for event in loader.events:
    if event.trade.timestamp.hour == 10:
        last_h10_event = event

if last_h10_event:
    ts = last_h10_event.trade.timestamp
    print(f"Last H10 event: {ts}")
    all_prices = loader.get_all_prices_at_time(ts)
    print(f"Tokens in all_prices: {len(all_prices)}")
    for tid, snap in all_prices.items():
        print(f"  ...{tid[-20:]}  bid={snap.bid}  ask={snap.ask}")

# Also check first H11 event
print(f"\n--- all_prices at start of H11 for ALL tokens ---")
first_h11_event = None
for event in loader.events:
    if event.trade.timestamp.hour == 11:
        first_h11_event = event
        break

if first_h11_event:
    ts = first_h11_event.trade.timestamp
    print(f"First H11 event: {ts}")
    all_prices = loader.get_all_prices_at_time(ts)
    print(f"Tokens in all_prices: {len(all_prices)}")
    for tid, snap in all_prices.items():
        print(f"  ...{tid[-20:]}  bid={snap.bid}  ask={snap.ask}")

# Count total unique tokens across ALL snapshots
print(f"\n--- Token landscape ---")
all_tokens = set()
with open(path) as f:
    for line in f:
        obj = json.loads(line)
        if obj["type"] == "price_snapshot":
            for tid in obj.get("prices", {}).keys():
                all_tokens.add(tid)
print(f"Total unique tokens in session: {len(all_tokens)}")
for t in sorted(all_tokens):
    print(f"  ...{t[-20:]}")
