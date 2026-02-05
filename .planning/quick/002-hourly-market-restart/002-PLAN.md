# Quick Task 002: Hourly Market Restart

## Problem

Polymarket hourly crypto markets expire at the top of each hour. When markets transition:
1. Old tokens become invalid
2. WebSocket subscriptions are stale
3. New markets have new token IDs
4. Bot continues collecting data on dead markets

## Solution

Add hourly restart logic to runner.py that:
1. Detects upcoming market transition (30 seconds before hour end)
2. Auto-sells positions at 0.99 (profit), accepts loss at 0.01
3. Gracefully shuts down
4. Waits 30 seconds after hour start
5. Restarts with fresh state and new market discovery

## Tasks

### Task 1: Add hourly transition detection

In runner.py, add:
- `_get_seconds_until_hour_end()` - calculate time to next hour boundary
- `_is_near_hour_end(threshold_sec=30)` - returns True if within 30s of hour end
- `_get_seconds_since_hour_start()` - for restart delay

### Task 2: Add position cleanup before restart

- Check all open positions
- Positions with bid >= 0.99: auto-sell
- Positions with bid <= 0.01: mark as lost (don't sell, let expire)
- Log cleanup actions

### Task 3: Add restart loop logic

In `run()` method:
- Check `_is_near_hour_end()` each cycle
- If True: trigger `_hourly_cleanup()` then `_restart()`
- `_restart()`:
  1. Stop WebSocket
  2. Clear subscriptions
  3. Sleep until 30s after hour
  4. Re-initialize data source
  5. Discover new markets
  6. Start WebSocket fresh

### Task 4: Reset WebSocket properly

Add to WebSocketPriceService:
- `reset()` method that clears subscribed tokens
- Or just stop() and create new instance

## Files to Modify

- `src/framework/runner.py` — main logic
- `src/data/ws_price.py` — add reset capability (optional)

## Verification

- Run dry-run across an hour boundary
- Verify positions cleaned up
- Verify new markets discovered after restart
- Verify WebSocket subscribed to new tokens
