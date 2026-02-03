# Quick Task 001: Fix WebSocket Reconnection

## Problem

WebSocket logs "reconnecting..." but doesn't actually reconnect. The thread dies silently and the bot continues without price data.

## Root Cause

In `_run_loop()`, if an exception escapes `_ws_main()` (e.g., unhandled error outside the retry loop), the thread exits and never restarts. The `while self._running:` loop is inside `_ws_main()`, but errors in `_run_loop()` itself kill the thread.

## Solution

1. Wrap the entire `_run_loop()` in a retry loop so the thread never dies while `self._running` is True
2. Add exponential backoff with max delay (2s → 4s → 8s → ... → 60s max)
3. Add reconnect counter and logging to track reconnection attempts
4. Log when reconnection succeeds

## Tasks

### Task 1: Fix _run_loop with outer retry

Modify `_run_loop()` to:
- Wrap `run_until_complete(_ws_main())` in a while loop
- Catch all exceptions and retry
- Use exponential backoff with 60s max delay
- Log reconnection attempts with counter

### Task 2: Improve _ws_main logging

- Log successful connection
- Log reconnection attempt number
- Use debug level for routine reconnects, warning for repeated failures

## Files to Modify

- `src/data/ws_price.py`

## Verification

- Run dry-run for a few minutes
- Manually disconnect network briefly
- Verify WS reconnects and prices resume
