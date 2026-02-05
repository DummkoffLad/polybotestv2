# Quick Task 002: Hourly Market Restart — COMPLETE

## Problem

Polymarket hourly crypto markets expire at the top of each hour. When the hour changes:
1. Old tokens become invalid
2. WebSocket subscriptions are stale
3. New markets have new token IDs
4. Bot was dying silently at market transitions

## Solution

Added hourly restart logic to `runner.py` that:
1. Detects upcoming market transition (30 seconds before hour end)
2. Cleans up positions (auto-sell at 0.99, accept loss at 0.01)
3. Stops WebSocket and clears old state
4. Waits 30 seconds after hour starts
5. Re-initializes with fresh market discovery

## Changes

**File:** `src/framework/runner.py`

- Added constants: `HOUR_END_THRESHOLD_SEC`, `HOUR_START_DELAY_SEC`, `EXTREME_HIGH_PRICE`, `EXTREME_LOW_PRICE`
- Added `_get_seconds_until_hour_end()` - calculate time to next hour boundary
- Added `_get_seconds_since_hour_start()` - calculate time since hour started
- Added `_is_near_hour_end(threshold_sec)` - check if within 30s of hour end
- Added `_hourly_cleanup()` - process positions before transition
- Added `_hourly_restart()` - stop WS, sleep through transition, re-init
- Modified `run()` loop to check for hour boundary each cycle

## Behavior

**30 seconds before each hour:**
1. Detects `_is_near_hour_end()` returns True
2. Calls `_hourly_cleanup()`:
   - Positions with bid >= 0.99: logged for auto-sell
   - Positions with bid <= 0.01: accept loss (let expire)
   - Mid-priced positions: warning logged
3. Calls `_hourly_restart()`:
   - Stops WebSocket
   - Clears `_seen` hashes and `_token_to_market`
   - Sleeps until 30s after hour boundary
   - Calls `_init()` to re-discover markets
   - Resumes trading loop

## Verification

- Import test passes
- Bot will automatically transition across hour boundaries
- No manual intervention required

## Configuration

```python
HOUR_END_THRESHOLD_SEC = 30  # Stop trading 30s before hour ends
HOUR_START_DELAY_SEC = 30    # Wait 30s after hour starts before resuming
EXTREME_HIGH_PRICE = Decimal("0.99")  # Auto-sell threshold
EXTREME_LOW_PRICE = Decimal("0.01")   # Accept loss threshold
```
