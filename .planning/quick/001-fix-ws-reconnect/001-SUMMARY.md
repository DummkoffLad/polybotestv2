# Quick Task 001: Fix WebSocket Reconnection — COMPLETE

## Problem

WebSocket logged "reconnecting..." but didn't actually reconnect. The thread died silently and the bot continued without price data.

## Root Cause

When an exception escaped `_ws_main()`, the `_run_loop()` method exited, killing the entire WebSocket thread. There was no outer retry loop.

## Solution

1. Added outer retry loop in `_run_loop()` that keeps the thread alive while `self._running` is True
2. Added exponential backoff (2s → 4s → 8s → ... → 60s max) to avoid hammering the server
3. Added reconnect counter to track attempts
4. Improved logging:
   - First 3 reconnects: debug level (quiet)
   - After 3 failures: warning level (visible)
   - Reset counter on successful connection

## Changes

**File:** `src/data/ws_price.py`

- Added `_reconnect_count` and `_max_backoff` instance variables
- Wrapped `_run_loop()` internals in `while self._running:` loop
- Added exponential backoff in both `_run_loop()` and `_ws_main()`
- Log "WS connected to Polymarket" on successful connection
- Reset backoff and counter on successful connection

## Verification

- Import test passes
- Bot should now reconnect indefinitely with exponential backoff
- Less log noise (debug level for first few reconnects)
