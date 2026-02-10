# Feature Landscape: Production Live Copy Trading Bot

**Domain:** Production-ready copy trading for prediction markets (Polymarket)
**Researched:** 2026-02-09
**Confidence:** MEDIUM (WebSearch verified with Polymarket docs, copy trading best practices)

## Table Stakes

Features users expect from production live trading systems. Missing = unacceptable risk or incomplete product.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Order State Machine** | Track order lifecycle (pending → filled → rejected) | Medium | FIX protocol standard: Submitted, Accepted, Working, Filled, Cancelled, Rejected. Critical for reconciliation. |
| **Position Reconciliation** | Verify our positions match exchange reality | Medium | Every 5 minutes fetch from API, compare to internal state. Prevents divergence after API failures. Currently exists but needs hardening. |
| **Kill Switch** | Emergency stop all trading | Low | Manual trigger + automatic circuit breakers. Industry standard for capital protection. |
| **Daily Loss Limits** | Auto-pause if drawdown exceeds threshold | Low | Stop trading if -15% to -20% in 24h. Already have hourly drawdown ($12/$24), extend to daily. |
| **Order Timeout Handling** | Cancel stale limit orders automatically | Medium | Polymarket orders can sit unfilled. Need timeout (30-60s) then cancel or convert to market. |
| **Idempotent Order Placement** | Prevent duplicate orders on retry | Medium | Use idempotency keys (UUID per logical order). Critical for reconnection scenarios. |
| **Connection Recovery** | Resume trading after WebSocket drop | Medium | Existing WebSocket has reconnect. Extend to: refetch positions, replay missed events, verify no duplicates. |
| **Order Rejection Handling** | Gracefully handle API rejections | Low | Polymarket rejects: insufficient balance, invalid price, minimum not met. Log, skip, don't crash. |
| **Minimum Order Validation** | Enforce Polymarket minimums pre-submission | Low | Market orders >= $1, limit orders >= 5 shares. Validate BEFORE API call to avoid rejection spam. Already exists in TradeDecision. |
| **Balance Checking** | Verify sufficient capital before order | Low | Check available balance before placing order. Prevents "insufficient funds" rejections. |
| **Execution Logs** | Audit trail of all orders placed | Low | Append-only log: timestamp, order details, API response, fill status. Essential for debugging and compliance. |
| **Error Retry Logic** | Retry transient API errors with backoff | Medium | Network errors, rate limits: exponential backoff (1s, 2s, 4s). Non-retryable errors (invalid market): fail immediately. |
| **Order Cancellation** | Cancel pending orders on demand | Low | Needed for hourly cleanup, kill switch, manual intervention. Already in ExecutionAdapter interface. |

## Differentiators

Features that set production bots apart. Not expected, but provide competitive edge.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Partial Fill Handling** | Maximize capital efficiency on slow fills | High | Track partially filled limit orders, adjust remaining size, handle multi-part fills. Rare on Polymarket but possible. |
| **Smart Order Routing** | Choose market vs limit based on urgency | Medium | Leader trade at minute 5 → limit order (time available). Minute 55 → market order (urgency). Improves fill rate + reduces slippage. |
| **Fill Quality Monitoring** | Track slippage and execution quality | Medium | Compare intended price vs actual fill. Alert if slippage consistently > 2%. Drives strategy improvements. Already have slippage analysis module. |
| **Position Size Limits** | Cap per-token exposure automatically | Low | Max $20 per position or 40% of hourly budget. Prevents over-concentration from rapid leader trades. |
| **Rate Limit Awareness** | Throttle requests proactively | Medium | Polymarket likely has limits. Track request count, stay under threshold, queue orders if needed. Better than hitting 429 errors. |
| **Duplicate Detection Hardening** | Prevent same trade execution twice | Medium | Already have content-based keys. Add: database-backed dedup (MongoDB), cross-restart persistence, 24h window. Prevents costly mistakes. |
| **Order Amendment** | Modify pending limit order price | Medium | Leader buys more → update our limit price up. Faster than cancel + resubmit. Requires WebSocket order updates. |
| **Live Profit/Loss Tracking** | Real-time P&L updates during session | Low | Calculate unrealized P&L from current bid/ask. Alert on significant moves. Already have portfolio tracking. |
| **Multi-Strategy Coordination** | Run multiple strategies without conflicts | High | Ensure profit_taker + conservative don't double-buy same token. Shared order manager, position tracking. Defer to post-MVP. |
| **Graceful Degradation** | Continue trading on partial failures | Medium | If WebSocket price feed fails, fall back to REST API polling. Slower but functional. Resilience over perfection. |

## Anti-Features

Features to explicitly NOT build yet. Common mistakes or premature optimization.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Automatic Position Exits** | We validated selling destroys value | Track position to resolution, manual exits only. Profit_taker strategy proved: hold = optimal. |
| **Complex Order Types** | Polymarket CLOB is simple (market/limit only) | Stick to market orders for speed, limit orders for price improvement. No stop-loss, no trailing stops on-chain. |
| **Multi-Exchange Support** | Polymarket-specific logic throughout | Build for Polymarket only. Other exchanges have different APIs, order books, fee structures. |
| **High-Frequency Trading** | Our edge is strategy, not speed | Leader trades are 1-3 per hour. No need for microsecond latency or co-location. |
| **Portfolio Rebalancing** | Copy trading follows leader, not portfolio theory | Don't rebalance across tokens. Each position is independent. Exit at resolution only. |
| **Margin/Leverage** | Polymarket is cash markets only | No margin trading. Each trade requires full capital upfront. |
| **Backtesting Against Live Fills** | We have replay system for historical testing | Don't build separate backtester. Replay recorded sessions for optimization (already works). |
| **GUI Dashboard** | CLI-first, logs + Grafana later | Focus on reliability. Add visualization post-MVP (Prometheus metrics → Grafana). |
| **Dynamic Strategy Switching** | Strategy selection is pre-session decision | Don't auto-switch strategies mid-session. Pick strategy at start, run to completion. |
| **Social Features** | Not a retail product | No leaderboards, sharing, or social trading. B2B tool for serious traders. |

## Feature Dependencies

```
Core Foundation (already exists):
├── ExecutionAdapter interface → OrderRequest/OrderResponse types
├── Portfolio tracking → Position reconciliation
└── SessionRecorder → Execution logs

Live Trading Prerequisites:
├── Order State Machine
│   ├── Requires: OrderStatus enum extension
│   └── Enables: Timeout handling, partial fills
├── Idempotent Order Placement
│   ├── Requires: UUID generation per order
│   └── Enables: Safe retries, duplicate prevention
├── Connection Recovery
│   ├── Requires: Position reconciliation
│   └── Enables: Resilient operation
└── Kill Switch
    ├── Requires: Order cancellation
    └── Enables: Daily loss limits, emergency stop

Advanced Features (post-MVP):
├── Smart Order Routing
│   ├── Requires: Order state machine
│   └── Enables: Fill quality monitoring
└── Partial Fill Handling
    ├── Requires: Order state machine
    └── Enables: Order amendment
```

## MVP Recommendation

For safe live trading MVP, prioritize:

### Phase 1: Order Lifecycle (Critical Path)
1. **Order State Machine** - Track pending/filled/rejected lifecycle
2. **Idempotent Order Placement** - Prevent duplicate orders
3. **Order Timeout Handling** - Cancel stale limit orders (30-60s)
4. **Order Rejection Handling** - Gracefully handle API errors

### Phase 2: Safety Mechanisms (Risk Mitigation)
5. **Kill Switch** - Manual emergency stop + API endpoint
6. **Daily Loss Limits** - Auto-pause on -15% daily drawdown
7. **Balance Checking** - Pre-validate sufficient capital
8. **Execution Logs** - Append-only audit trail

### Phase 3: Operational Reliability (Production Polish)
9. **Connection Recovery** - Resume after WebSocket drop
10. **Position Reconciliation Hardening** - Compare every 5min, alert on divergence
11. **Error Retry Logic** - Exponential backoff on transient errors
12. **Rate Limit Awareness** - Proactive throttling

Defer to post-MVP:
- **Partial Fill Handling**: Rare on Polymarket (high liquidity markets), can handle manually
- **Smart Order Routing**: Market orders work, limit order logic is optimization
- **Order Amendment**: Cancel + resubmit is simpler, amendment is edge case
- **Multi-Strategy Coordination**: Run one strategy at a time initially
- **Fill Quality Monitoring**: Nice-to-have, slippage analysis exists for post-session
- **Graceful Degradation**: WebSocket is reliable, REST fallback is complexity

## Production Readiness Checklist

Before going live with real capital:

**Safety:**
- [ ] Kill switch tested (manual trigger stops all trading)
- [ ] Daily loss limit tested (auto-pauses at -15%)
- [ ] Order timeout tested (stale orders cancelled after 60s)
- [ ] Duplicate prevention tested (same order not placed twice)
- [ ] Balance check tested (insufficient funds rejected gracefully)

**Reliability:**
- [ ] WebSocket reconnection tested (resume after disconnect)
- [ ] Position reconciliation tested (divergence detected and alerted)
- [ ] API error handling tested (retries transient, skips permanent)
- [ ] Order state transitions tested (pending → filled, pending → rejected)
- [ ] Execution logs verified (all orders logged with full details)

**Operational:**
- [ ] Monitoring in place (order fill rate, rejection rate, P&L)
- [ ] Alerting configured (kill switch trigger, loss limit hit, reconciliation mismatch)
- [ ] Runbook documented (how to start, stop, emergency procedures)
- [ ] Capital controls set (max per order, max daily deployment)
- [ ] Manual override tested (can cancel orders, disable trading mid-session)

**Validation:**
- [ ] Dry-run tested 7 days (no crashes, positions reconcile)
- [ ] Paper trading tested 7 days (simulated fills match strategy expectations)
- [ ] Small-capital test 7 days ($5/hour, verify fills, P&L tracking)
- [ ] Strategy parameters validated (train/test/holdout split, Sharpe > 0.3)

## Complexity vs Impact Matrix

**High Impact, Low Complexity (Do First):**
- Kill switch
- Balance checking
- Order rejection handling
- Execution logs
- Daily loss limits

**High Impact, Medium Complexity (Critical Path):**
- Order state machine
- Idempotent order placement
- Order timeout handling
- Connection recovery
- Position reconciliation hardening

**Medium Impact, Low Complexity (Quick Wins):**
- Live P&L tracking
- Position size limits

**Medium Impact, Medium Complexity (Phase 2+):**
- Smart order routing
- Fill quality monitoring
- Rate limit awareness
- Error retry logic

**Low Impact, High Complexity (Defer):**
- Partial fill handling
- Order amendment
- Multi-strategy coordination
- Graceful degradation

## Sources

### Copy Trading Best Practices
- [Step-by-Step Crypto Trading Bot Development Guide (2026)](https://appinventiv.com/blog/crypto-trading-bot-development/)
- [Copy Trading Bot Features - Cryptohopper](https://www.cryptohopper.com/features/copy-bot)
- [Polymarket Copy Trading Bot 2026 Guide](https://tradingvps.io/polymarket-copy-trading-bot/)

### Order Management & State Machines
- [Order State Changes - FIX Trading Community](https://www.fixtrading.org/online-specification/order-state-changes/)
- [Rejected Orders in Futures Trading: Causes & Fixes in 2026](https://blog.pickmytrade.trade/rejected-orders-futures-trading-causes-fixes-2026/)

### Safety Mechanisms & Circuit Breakers
- [Crypto Trading Bot Development: Technical Guide](https://shivlab.com/blog/crypto-trading-bot-development-guide/)
- [Trading Bot Crypto: Complete Guide to Automation 2026](https://tickerly.net/trading-bot-crypto-complete-guide-2026/)

### Idempotency & Duplicate Prevention
- [What Is an Idempotency Key? Preventing Duplicate Crypto Orders](https://www.tokenmetrics.com/blog/idempotency-keys-order-placement?74e29fd5_page=7)
- [Idempotency Keys Prevent Duplicate Trades in Digital Finance](https://www.ainvest.com/news/idempotency-keys-prevent-duplicate-trades-digital-finance-2508/)

### Position Reconciliation
- [What is Trade Reconciliation? Importance and Challenges](https://www.highradius.com/resources/Blog/trade-reconciliation/)
- [A guide to Cash and position reconciliation](https://www.limina.com/blog/cash-position-reconciliation-guide)

### Polymarket CLOB API
- [WSS Overview - Polymarket Documentation](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)
- [The Polymarket API: Architecture, Endpoints, and Use Cases](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf)
- [How to Use Polymarket API: Complete Developer Guide (2026)](https://hypereal.tech/a/polymarket-api)

### System Architecture
- [A Modular Architecture for Systematic Quantitative Trading Systems](https://hiya31.medium.com/a-modular-architecture-for-systematic-quantitative-trading-systems-2a8d46463570)
- [Designing a Production-Style Algorithmic Trading Platform](https://medium.com/@kaur.exe/designing-a-production-style-algorithmic-trading-platform-5dc326faacc8)
