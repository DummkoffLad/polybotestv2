# Features Research: v1.2 Production Ready

**Domain:** Live trading on Polymarket prediction markets (copy trading bot)
**Researched:** 2026-02-10
**Confidence:** HIGH (verified with official Polymarket docs and py-clob-client)

## Executive Summary

Moving from dry-run to live trading requires **safety-first architecture** with clear execution boundaries. The bot already has strong strategy logic (profit_taker: Sharpe 0.345, $324 PnL) — the challenge is adding reliable order placement without introducing new failure modes.

**Key feature decisions:**
1. **Market orders (FOK) only** — simplifies execution, matches copy trading speed requirements
2. **Pre-flight safety checks** — validate environment/connectivity before arming
3. **Reconciliation on every poll** — catch execution drift early
4. **Kill switch with manual disarm** — deliberate action required to trade live
5. **NO complex order lifecycle management** — FOK orders either fill or fail immediately, no partial fills to track

**Anti-features (deliberately skip for $50/hr bot):**
- Limit order management and cancellation logic
- Position averaging or re-entry after partial fills
- Order book modeling or spread analysis
- Dynamic position sizing based on liquidity
- Multi-venue execution or routing logic

This is a small-scale copy trading bot. Keep execution simple, monitoring comprehensive, safety paranoid.

---

## Table Stakes (Must Have)

### 1. Order Placement

**Market orders via FOK (Fill-Or-Kill)**
- **What:** Submit orders that execute immediately at best available price or cancel entirely
- **Why table stakes:** Copy trading requires speed. Polymarket min order = $1 market orders. FOK removes partial fill complexity.
- **Complexity:** LOW
- **Implementation:** Use `py_clob_client.post_order()` with `ClobOrderType.FOK`, price = mid ± 5c buffer
- **Notes:**
  - Polymarket represents market orders as limit orders priced to cross the spread
  - FOK = "fill entire order immediately or cancel all" — no partial fills, no resting orders
  - Rate limit: 60 orders/minute per API key (burst 3,500/10s)
  - Min $1 market orders, min 5 shares limit orders (we only need market)

**Order validation before submission**
- **What:** Check token_id, amount > 0, price in [0.01, 0.99], side is valid
- **Why table stakes:** Invalid orders waste API calls and delay execution
- **Complexity:** LOW
- **Implementation:** Pre-submit validation in `place_order()` (already present in live.py)
- **Notes:** Current code validates amount_dollars, shares, market_id, token_id

### 2. Safety Mechanisms

**Explicit arming with pre-flight checks**
- **What:** Require manual arm command after validating environment, connectivity, credentials
- **Why table stakes:** Prevent accidental live trading. Fail fast on misconfiguration.
- **Complexity:** LOW
- **Implementation:**
  - Pre-flight: check POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER_ADDRESS env vars
  - Connectivity: `client.get_ok()` health check
  - API auth: `create_or_derive_api_creds()` succeeds
  - Only then allow `arm()` to succeed
- **Notes:** Current code has arm/disarm pattern, needs comprehensive pre-flight

**Kill switch (manual disarm)**
- **What:** Immediate stop of all order placement, preserve existing positions
- **Why table stakes:** Emergency brake when bot behaves unexpectedly or market goes chaotic
- **Complexity:** LOW
- **Implementation:** `disarm()` sets `_armed = False`, all `place_order()` calls rejected
- **Notes:** Already implemented in live.py, needs CLI command exposure

**Per-hour budget cap enforcement**
- **What:** Track total spent this hour, reject orders exceeding `HOURLY_BUDGET`
- **Why table stakes:** Prevents runaway spending if strategy logic has bug
- **Complexity:** LOW
- **Implementation:** HourlyBudgetMixin already tracks `_spent_this_hour`, enforce hard cap
- **Notes:** Current code has soft budget (strategy aware), needs hard cap (execution enforced)

**Maximum drawdown circuit breaker**
- **What:** If unrealized + realized loss exceeds threshold, stop buying (already in strategy)
- **Why table stakes:** Limits damage when hour goes badly (multiple simultaneous losers)
- **Complexity:** LOW (already implemented)
- **Implementation:** profit_taker has DRAWDOWN_STOP_THRESHOLD = $24
- **Notes:** Strategy-level feature, move to execution adapter for hard enforcement

### 3. Position Reconciliation

**Reconcile expected vs actual positions on every poll**
- **What:** Query actual USDC balance and token holdings, compare to portfolio tracker
- **Why table stakes:** Catch execution drift (failed orders we think succeeded, fills we missed)
- **Complexity:** MEDIUM
- **Implementation:**
  - Add `get_balances()` to LiveExecutionAdapter
  - Use `py_clob_client` balance queries (requires blockchain RPC or Polymarket API)
  - Compare to `Portfolio.positions`, log discrepancies
  - Option: auto-correct portfolio state or alert and stop
- **Notes:** Critical for multi-hour runs. Execution can fail silently (network issues, rejections).

**Order status tracking for FOK orders**
- **What:** After `post_order()`, verify response status = FILLED or REJECTED
- **Why table stakes:** FOK orders resolve immediately, must confirm outcome before continuing
- **Complexity:** LOW
- **Implementation:** Parse `post_order()` response: `success` field + `orderID` (already done in live.py)
- **Notes:** No async tracking needed for FOK — synchronous response tells us everything

### 4. Environment & Configuration

**Secure credential management**
- **What:** Load private key, funder address from environment variables, never hardcode
- **Why table stakes:** Security. Private key = full wallet access.
- **Complexity:** LOW (already implemented)
- **Implementation:** `os.getenv("POLYMARKET_PRIVATE_KEY")`, `POLYMARKET_FUNDER_ADDRESS`
- **Notes:** Current code follows best practice. Document .env.example for setup.

**Rate limit awareness**
- **What:** Track order submission count, pause if approaching 60/min limit
- **Why table stakes:** Avoid throttling during high-activity periods
- **Complexity:** LOW
- **Implementation:** Simple counter with 1-minute sliding window, warn at 50 orders/min
- **Notes:** Polymarket queues over-limit requests (not drops), but delays hurt copy trading

**Connection health monitoring**
- **What:** Periodic health check (every 60s) to Polymarket API
- **Why table stakes:** Detect API outages or network issues before orders fail
- **Complexity:** LOW
- **Implementation:** Call `client.get_ok()` or fetch midpoint for a known token, alert on failure
- **Notes:** If health check fails 3x consecutive, auto-disarm and alert

### 5. Logging & Observability

**Structured order logs**
- **What:** Log every order submission with: timestamp, token_id, side, amount, price, outcome, order_id
- **Why table stakes:** Audit trail for reconciliation, debugging, performance analysis
- **Complexity:** LOW
- **Implementation:** JSON log per order to `data/logs/orders_live_{session_id}.jsonl`
- **Notes:** Current code logs to logger, needs structured file output

**Per-hour PnL summary**
- **What:** Print summary at hour end: trades executed, dollars spent, realized PnL, unrealized PnL
- **Why table stakes:** Quick sanity check that bot is behaving as expected
- **Complexity:** LOW (already exists in dry-run)
- **Implementation:** Reuse existing portfolio PnL calculation, print on hour boundary
- **Notes:** Current runner.py has hourly trade logs, expand to include PnL

**Error alerting**
- **What:** Log errors to console AND file: order rejections, API errors, reconciliation mismatches
- **Why table stakes:** Human must see when things break
- **Complexity:** LOW
- **Implementation:** Python logging to both stdout and `data/logs/errors.log`
- **Notes:** Current code logs to console, add file handler

---

## Differentiators (Competitive Advantage)

### 1. Execution Speed Optimization

**Parallel order submission for simultaneous leader trades**
- **What:** If leader buys 3 tokens in same block, submit our orders in parallel (async)
- **Why differentiator:** Reduces execution lag from 3× poll interval to 1× poll interval
- **Value:** Small latency edge when copying rapid leader activity
- **Complexity:** MEDIUM
- **Implementation:** Use asyncio to submit multiple FOK orders concurrently
- **Notes:** Diminishing returns for current bot (typically 1-2 positions/hour). Consider post-v1.2.

**WebSocket price feeds instead of polling**
- **What:** Subscribe to token price updates via WebSocket for real-time midpoint tracking
- **Why differentiator:** Sub-second price updates vs 10-30s polling lag
- **Value:** Better execution prices on fast-moving markets
- **Complexity:** MEDIUM
- **Implementation:** Polymarket supports WebSocket subscriptions for order book updates
- **Notes:** Overkill for $50/hr bot. Most trades execute fine with polling. Defer.

### 2. Adaptive Execution

**Dynamic price improvement on market orders**
- **What:** Instead of mid ± 5c fixed buffer, calculate buffer based on current spread width
- **Why differentiator:** Narrow spreads = less slippage, wide spreads = ensure fill
- **Value:** ~1-2% better execution price on average
- **Complexity:** MEDIUM
- **Implementation:** Fetch bid/ask from order book, set price = mid + (spread * 0.3)
- **Notes:** Polymarket spreads typically 2-10c. Fixed buffer works fine. Low priority.

**Retry logic for transient failures**
- **What:** If order fails with network error (not rejection), retry 2x with exponential backoff
- **Why differentiator:** Improves fill rate during network hiccups
- **Value:** Avoids missed trades due to temporary connectivity issues
- **Complexity:** LOW
- **Implementation:** Detect network errors vs API rejections, retry network errors only
- **Notes:** Worth implementing. py-clob-client can throw exceptions on network issues.

### 3. Risk Management Enhancements

**Daily loss limit with auto-shutdown**
- **What:** If total loss across all hours today exceeds $X, disarm and stop trading
- **Why differentiator:** Protects against catastrophic strategy failure (bug, regime change)
- **Value:** Caps worst-case daily loss
- **Complexity:** LOW
- **Implementation:** Track daily cumulative PnL, check before each new hour, disarm if < -$100
- **Notes:** Reasonable safeguard. Current bot has per-hour limits but no daily cap.

**Position concentration limits**
- **What:** Reject orders if they'd put >40% of capital in single token
- **Why differentiator:** Diversification safety net (strategy already limits, this is hard cap)
- **Value:** Prevents strategy bugs from creating concentrated positions
- **Complexity:** LOW
- **Implementation:** Check `portfolio.positions` before order, calculate new position pct
- **Notes:** profit_taker naturally diversifies (5x boost = ~$5-10 per position). Low urgency.

---

## Anti-Features (Don't Build)

### Order Lifecycle Complexity

**Limit order management with cancellations/updates**
- **Why not:** FOK market orders are sufficient for copy trading. Leader trades execute immediately, we must match speed. Limit orders add complexity (cancellation logic, expiration handling, partial fills) with no benefit.
- **What to do instead:** Stick to FOK market orders exclusively.

**Partial fill tracking and position averaging**
- **Why not:** FOK orders never partial fill (all-or-nothing). FAK orders (fill-and-kill) could partial fill, but we don't use them. No need to track unfilled portions.
- **What to do instead:** If FOK order fails (rejected), log and move on. Don't retry or adjust size.

**GTC (Good-Til-Cancelled) or GTD (Good-Til-Date) order types**
- **Why not:** Copy trading = immediate execution. Resting orders on book defeat the purpose (we want to match leader timing, not predict future).
- **What to do instead:** Market orders only. Follow leader when they trade, not before.

### Advanced Execution Strategies

**Order book depth modeling**
- **Why not:** Polymarket hourly markets have thin liquidity (often $100-500 on each side). Modeling depth provides no edge for $1-5 orders.
- **What to do instead:** Market orders with fixed buffer. Accept slippage as cost of speed.

**Smart order routing or liquidity seeking**
- **Why not:** Polymarket is single-venue (CLOB). No alternative liquidity sources.
- **What to do instead:** Single-venue execution via py-clob-client.

**Post-only orders for maker rebates**
- **Why not:** Post-only orders rest on book without immediate fill, creating execution uncertainty. Copy trading requires speed, not fee optimization.
- **What to do instead:** Accept taker fees as cost of immediacy.

### Over-Engineered Monitoring

**Real-time dashboard or UI**
- **Why not:** Bot runs on VPS with CLI logging. Adding UI increases complexity (web server, frontend) with no operational benefit.
- **What to do instead:** Structured logs + CLI output. Query logs for analysis.

**Slack/Discord/email alerting**
- **Why not:** Bot runs autonomously for hours. Alert fatigue from every small issue. For $50/hr operation, manual log review is sufficient.
- **What to do instead:** Comprehensive file logging. Review logs daily. Add alerting if bot scales to $500+/hr.

**Machine learning anomaly detection**
- **Why not:** Insufficient data (~200 trades total). ML models would overfit. Simple heuristics (circuit breakers, budget caps) are more robust.
- **What to do instead:** Rule-based risk limits.

---

## Feature Dependencies

**Execution flow for live trading:**

```
1. Startup
   ├─ Load environment variables (PRIVATE_KEY, FUNDER_ADDRESS)
   ├─ Initialize py-clob-client
   ├─ Pre-flight checks (connectivity, auth, balance)
   └─ Require manual arm command

2. Per-poll cycle
   ├─ Detect leader trade (existing)
   ├─ Strategy decides: buy/sell/skip (existing)
   ├─ If BUY/SELL: validate order (NEW)
   │  ├─ Check armed status
   │  ├─ Check hourly budget remaining
   │  ├─ Check rate limits
   │  └─ Check position concentration
   ├─ Submit FOK order via py-clob-client (NEW)
   ├─ Parse order response (FILLED/REJECTED) (NEW)
   ├─ Update portfolio state (existing)
   └─ Log outcome (NEW: structured file)

3. Every 60s (health check)
   ├─ Ping Polymarket API (NEW)
   ├─ Reconcile positions (NEW)
   └─ If failure 3x → auto-disarm (NEW)

4. Every hour boundary
   ├─ Print PnL summary (existing)
   ├─ Reset hourly budget (existing)
   └─ Save state to disk (existing)

5. Shutdown (SIGINT/SIGTERM)
   ├─ Disarm (stop new orders)
   ├─ Cancel pending orders (N/A for FOK)
   ├─ Save final state
   └─ Print session summary
```

**Critical path (must work for bot to function):**
- Pre-flight checks → arm() → place_order() → parse response → update portfolio

**Safety net (prevents disasters):**
- Budget caps, drawdown breakers, kill switch, reconciliation

**Nice-to-have (improves reliability):**
- Health monitoring, retry logic, daily loss limits

---

## MVP Recommendation

**For v1.2 Production Ready, prioritize:**

1. **Pre-flight safety checks** — validate environment before arming (1-2 hours)
2. **FOK market order placement** — integrate py-clob-client FOK orders (2-3 hours)
3. **Order response handling** — parse FILLED/REJECTED, update portfolio (1 hour)
4. **Structured order logging** — JSONL audit trail (1 hour)
5. **Hard budget cap enforcement** — reject orders exceeding hourly limit (30 min)
6. **Kill switch CLI command** — manual disarm for emergencies (30 min)
7. **Basic reconciliation** — compare expected vs actual positions every 5 min (2-3 hours)
8. **Connection health check** — detect API outages, auto-disarm (1-2 hours)

**Total estimated effort: 10-14 hours of focused development**

**Defer to post-v1.2:**
- WebSocket price feeds (polling works fine for current scale)
- Parallel order submission (rarely needed with 1-2 trades/hour)
- Dynamic spread-based pricing (fixed buffer sufficient)
- Daily loss limits (nice-to-have, not critical for initial rollout)
- External alerting (file logs + manual review adequate for now)

**Testing strategy before live:**
1. **Unit tests:** Mock py-clob-client responses (FILLED, REJECTED, network errors)
2. **Integration tests:** Testnet or paper trading if Polymarket supports it
3. **Dry-run validation:** Run live.py with `_armed = False`, log what orders WOULD be placed
4. **Small capital trial:** First live session with $10 budget, verify reconciliation works

---

## Sources

**Official Polymarket Documentation:**
- [Place Single Order - Polymarket Documentation](https://docs.polymarket.com/developers/CLOB/orders/create-order) — order types, time-in-force, minimum sizes
- [API Rate Limits - Polymarket Documentation](https://docs.polymarket.com/quickstart/introduction/rate-limits) — trading endpoint limits, throttling behavior

**py-clob-client:**
- [GitHub - Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client) — official Python SDK, code examples, order management methods

**Trading Bot Best Practices:**
- [Crypto Trading Bots 2026: Complete Guide To Automated Trading | MEXC](https://blog.mexc.com/news/crypto-trading-bots-2026-complete-guide-to-automated-trading/)
- [Step-by-Step Crypto Trading Bot Development Guide (2026)](https://appinventiv.com/blog/crypto-trading-bot-development/)
- [Market Making on Prediction Markets: Complete 2026 Guide](https://newyorkcityservers.com/blog/prediction-market-making-guide)
- [Polymarket Trading Bot Setup Tutorial — Automate Your Prediction Market Trading | TradingVPS](https://tradingvps.io/polymarket-trading-bot-setup-tutorial/)

**API Error Handling:**
- [How to Handle API Rate Limits Gracefully (2026 Guide) | API Status Check Blog](https://apistatuscheck.com/blog/how-to-handle-api-rate-limits)
- [Best practices for handling API rate limits and 429 errors – Docebo Help & Support](https://help.docebo.com/hc/en-us/articles/31803763436946-Best-practices-for-handling-API-rate-limits-and-429-errors)

**Confidence notes:**
- **HIGH confidence** on Polymarket-specific details (order types, rate limits, FOK behavior) — verified with official docs
- **HIGH confidence** on py-clob-client integration — read existing live.py implementation, cross-referenced with GitHub repo
- **MEDIUM confidence** on reconciliation approach — standard pattern for trading bots, but Polymarket-specific balance queries need validation during implementation
- **LOW confidence** on WebSocket performance gains — theoretical benefit, needs real-world testing to quantify

---

*Last updated: 2026-02-10*
