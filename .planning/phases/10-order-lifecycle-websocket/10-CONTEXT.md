# Phase 10: Order Lifecycle & WebSocket - Context

**Gathered:** 2026-02-11
**Status:** Ready for planning

<domain>
## Phase Boundary

Track order state from submission to fill/rejection with real-time WebSocket notifications for our own orders. Includes retry logic, structured audit logging, and WebSocket connection management. Leader signal source (blockchain) is unchanged. Position reconciliation is Phase 11. Safety mechanisms (budget caps, kill switch) are Phase 9.

</domain>

<decisions>
## Implementation Decisions

### Order state tracking
- State granularity: Claude's discretion based on what Polymarket CLOB API actually returns
- Unconfirmed orders (WebSocket drops before fill confirmation): Claude's discretion on safest approach (REST fallback vs timeout-to-unknown)
- External orders (manual trades): Claude's discretion on whether to track or ignore
- Concurrent orders: Allow concurrent order placement — bot does NOT wait for each order to resolve before placing the next
- Budget tracking must account for in-flight (pending) orders to avoid double-spending

### Retry & failure policy
- Retry behavior on submission failure: Claude's discretion on retry count and backoff based on Polymarket API behavior
- Rejection alerting: Claude's discretion based on severity (e.g., insufficient balance is more urgent than market-closed)
- Consecutive failure threshold: Claude's discretion on auto-disarm threshold
- Slippage tolerance: Claude's discretion based on simulation data price movement patterns

### Audit log design
- Log location: Hourly JSON files in daily folders (e.g., `logs/2026-02-11/orders_14.jsonl`)
- Log fields: Claude's discretion on which fields beyond basics (timestamp, token_id, side, amount, price, outcome, order_id) — include strategy context fields useful for debugging and performance analysis
- Skipped trades: Claude's discretion on whether to log skipped signals
- Console output: Both live output AND quiet mode, configurable (default to live, --quiet flag to suppress)

### WebSocket lifecycle
- Trading on disconnect: Pause all new order placement until WebSocket is reconnected — no trading blind
- Post-reconnect sync: Claude's discretion on whether to query REST for missed events or let Phase 11 reconciliation handle drift
- Reconnection strategy: Claude's discretion on timeout and backoff behavior with escalating alerts
- WebSocket scope: Only for OUR order fill notifications — leader signals continue via existing blockchain data pipeline

### Claude's Discretion
- Order state machine granularity (based on CLOB API response states)
- Retry count and exponential backoff parameters
- Whether to log skipped trades alongside actual orders
- REST fallback behavior for unconfirmed orders
- Auto-disarm threshold for consecutive failures
- Slippage tolerance
- Reconnection timeout and escalation strategy
- Audit log field selection beyond required minimum

</decisions>

<specifics>
## Specific Ideas

- Leader signals come from blockchain method (faster than WebSocket) — this phase's WebSocket is strictly for our own order confirmations
- Hourly log files in daily folders match the bot's hourly budget cycle — makes it easy to correlate logs with trading hours
- Live console output by default so the user can watch the bot work, with --quiet for headless/background operation

</specifics>

<deferred>
## Deferred Ideas

- WebSocket subscription for leader signals — investigate if Polymarket API supports this (could reduce latency vs blockchain method)
- Position reconciliation after reconnect — Phase 11 handles this with 5-minute reconciliation loop

</deferred>

---

*Phase: 10-order-lifecycle-websocket*
*Context gathered: 2026-02-11*
