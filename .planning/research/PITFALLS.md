# Domain Pitfalls: Copy Trading Bot Simulation to Live

**Domain:** Copy trading bot transitioning from simulation to live trading on Polymarket
**Researched:** 2026-02-09
**Confidence:** HIGH (based on official docs, community issues, and codebase analysis)

---

## CRITICAL PITFALLS

These mistakes cause immediate money loss or catastrophic failure. Must be addressed before live trading.

---

### Pitfall 1: Environment Variable Misconfiguration Leading to Wrong Network/Wallet

**What goes wrong:** Wrong private key, wrong funder address, or wrong signature type causes orders to:
- Submit to wrong wallet (lose access to funds)
- Fail silently with wrong signature type (think you're trading but you're not)
- Use testnet credentials on mainnet (orders rejected, miss trades)
- Expose private key in logs/errors/commits

**Why it happens:**
- Multiple .env files (.env, .env.local, .env.production) get out of sync
- Copy-paste errors when moving keys between environments
- Private key from testnet used in production config
- Signature type mismatch (EOA=0, POLY_PROXY=1, GNOSIS_SAFE=2)
- .env file accidentally committed to git

**Consequences:**
- MONEY LOSS: Orders go to wrong wallet, can't recover funds
- SILENT FAILURE: Wrong signature type = rejected orders you don't see
- SECURITY BREACH: Private key exposed in git history
- MISSED TRADES: Credential errors during high-conviction entries

**Prevention:**
1. **Validation at startup** - Verify env vars match expected wallet BEFORE arming
   ```python
   # Check: Does funder address match derived address from private key?
   # Check: Can we query balance? Does it match expected amount?
   # Check: Is signature_type correct for this wallet type?
   ```

2. **Preflight checks** (already in code but needs hardening):
   - Query wallet balance, verify it's > 0 and matches expected
   - Submit tiny test order ($1) to unused market, verify it executes
   - Log wallet address on every startup for manual verification
   - NEVER log private key (already handled, but verify)

3. **Environment isolation**:
   - Use separate .env files: `.env.testnet`, `.env.mainnet`
   - Script to load correct file: `python run.py --env mainnet`
   - Add `.env*` to .gitignore (already present, verify)
   - Use environment variable validation library (pydantic-settings)

4. **Security hardening**:
   - Store private keys in secret manager (AWS Secrets, 1Password, Vault)
   - Never echo/print private key even in debug mode
   - Use read-only API keys for data fetching where possible
   - Rotate keys periodically

**Detection (warning signs):**
- Startup logs show different address than expected
- Balance queries fail or show $0 when wallet has funds
- All orders return "unauthorized" or "invalid signature"
- py-clob-client returns `get_ok() = False`

**Phase to address:** Phase 1 (Environment Setup & Validation)

**Reference:** Current code has basic env loading (`src/core/config.py` lines 84-96) but lacks validation.

**Sources:**
- [Security concerns with env variables](https://securityboulevard.com/2025/12/are-environment-variables-still-safe-for-secrets-in-2026/)
- Current `.env.example` and `config.py` analysis

---

### Pitfall 2: WebSocket Connection Drops Without Detection/Reconnection

**What goes wrong:**
- WebSocket drops, bot thinks it's connected, misses leader trades
- Infinite reconnection loop during high volatility
- Heartbeat stops, connection hangs but doesn't close
- Reconnect happens but subscriptions not restored

**Why it happens:**
- Network instability (wifi drops, ISP issues)
- Polymarket server restarts or maintenance
- No heartbeat/PING sent (connection times out server-side)
- Reconnection logic has bugs (race conditions, missing resubscribe)
- Connection appears healthy but data stops flowing

**Consequences:**
- MONEY LOSS: Miss high-conviction leader trades during connection gap
- STALE DATA: Make decisions on outdated prices (slippage, wrong side)
- RESOURCE LEAK: Reconnection loop creates 100+ connections
- SILENT FAILURE: Connection looks ok but events not arriving

**Prevention:**
1. **Heartbeat implementation**:
   - Send PING every 10 seconds (Polymarket CLOB requirement)
   - Track last received message timestamp
   - Alarm if no message received in 30 seconds
   - Force reconnect if 60 seconds without data

2. **Reconnection with exponential backoff**:
   ```python
   # Initial: 1s, then 2s, 4s, 8s, max 60s
   # Add jitter to avoid thundering herd
   backoff = min(60, base_delay * (2 ** attempt)) + random(0, 1)
   ```

3. **Connection health monitoring**:
   - Track: messages/sec, last message time, connection state
   - Alert if message rate drops to zero
   - Log connection state changes (connecting, connected, disconnected)
   - Dashboard showing connection health

4. **Subscription restoration**:
   - Track all active subscriptions (markets, tokens, user events)
   - On reconnect: resubscribe to ALL previous subscriptions
   - Fetch missed data via REST API after reconnect gap
   - Verify subscription confirmed before resuming trading

5. **Graceful degradation**:
   - If WS down: fall back to REST polling (higher latency but works)
   - Pause trading during reconnection (don't trade on stale data)
   - Resume only after connection stable for 30+ seconds

**Detection (warning signs):**
- No leader trades received for 5+ minutes (leader is very active)
- Last price update timestamp is stale (> 1 minute old)
- WebSocket library logs connection errors
- Order submissions timing out

**Phase to address:** Phase 2 (WebSocket Reliability & Monitoring)

**Reference:** Community reports show data streams stopping after 20 minutes, reconnection mechanisms broken.

**Sources:**
- [Polymarket WebSocket Overview](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)
- [WebSocket data stream stops after some time](https://github.com/Polymarket/real-time-data-client/issues/26)
- [Websocket reconnection mechanism isn't working](https://github.com/Polymarket/rs-clob-client/issues/185)
- [News-Driven Polymarket Bots Guide](https://www.quantvps.com/blog/news-driven-polymarket-bots)

---

### Pitfall 3: Order Rejections Not Detected or Handled

**What goes wrong:**
- FOK order rejected (insufficient liquidity), bot thinks it executed
- Order below minimum size ($1 market, 5 shares limit) rejected silently
- Balance insufficient, order rejected, bot doesn't decrement budget
- Partial fill treated as full fill (size validation bug)

**Why it happens:**
- py-clob-client returns `success: false` but code doesn't check
- Async order submission: response comes back after next decision
- Minimum size validation missing (Polymarket: $1 market orders, 5 shares limit)
- No retry logic for transient failures
- Budget tracking assumes order filled before confirmation

**Consequences:**
- MONEY LOSS: Think you bought, didn't, miss the trade resolution
- BUDGET LEAK: Hourly budget depleted tracking phantom orders
- OVEREXPOSURE: Retry logic submits duplicate orders
- SILENT FAILURE: Strategy says "bought at 0.55" but no position

**Prevention:**
1. **Synchronous order confirmation** (already present but verify):
   ```python
   resp = self._client.post_order(signed, OrderType.FOK)
   success = resp.get("success", False)  # Line 140 in live.py
   if not success:
       # CRITICAL: Don't update portfolio, don't decrement budget
       logger.warning(f"ORDER REJECTED: {resp.get('errorMsg')}")
   ```

2. **Minimum size validation** (BEFORE submission):
   ```python
   if request.action == "BUY" and request.amount_dollars < 1.0:
       return OrderResponse(status=REJECTED, error="Below $1 minimum")
   if request.action == "SELL" and request.shares < 5:
       return OrderResponse(status=REJECTED, error="Below 5 share minimum")
   ```

3. **Post-order verification**:
   - After order submission: query positions via REST API
   - Verify position increased by expected amount
   - If mismatch: log alert, don't count order as filled
   - Reconcile portfolio state every 5 minutes

4. **Budget tracking with confirmations**:
   ```python
   # Reserve budget when submitting order
   budget_manager.reserve(amount)

   # On fill confirmation: commit reservation
   budget_manager.commit(order_id)

   # On rejection: release reservation
   budget_manager.release(order_id)
   ```

5. **Rejection alerting**:
   - Track rejection rate (should be < 5%)
   - Alert if rejection rate spikes (liquidity dried up?)
   - Log rejection reasons for debugging
   - If 3+ consecutive rejections: pause trading, investigate

**Detection (warning signs):**
- Simulation shows +$324 PnL, live shows $0 (orders not filling)
- Hourly budget depletes but positions not increasing
- Leader makes 8 trades, bot only has 2 positions
- Logs show "ORDER FILLED" but balance unchanged

**Phase to address:** Phase 3 (Order Management & Confirmation)

**Reference:** Current code has basic rejection handling (`live.py` lines 140-150) but lacks budget reconciliation.

**Sources:**
- [Polymarket Place Order Docs](https://docs.polymarket.com/developers/CLOB/orders/create-order)
- [FOK order decimal places error](https://github.com/Polymarket/py-clob-client/issues/121)

---

### Pitfall 4: Slippage & Timing Lag in Copy Trading

**What goes wrong:**
- Leader buys at 0.55c, you buy at 0.68c (slippage ate your edge)
- Leader's trade moves market, you chase the pump
- By the time you see trade (API lag), liquidity dried up
- Multiple bots copying same leader = thundering herd slippage

**Why it happens:**
- API latency: Leader's trade confirmed on-chain → API update → you fetch = 1-5 seconds lag
- Order book thin: Leader's $50 trade moves price 10 cents
- FOK aggressive pricing: current code uses `mid + 0.05` for buys (line 125)
- Multiple followers: 10 bots see same trade, all buy simultaneously
- News events: Leader trades on breaking news, you're too slow

**Consequences:**
- MONEY LOSS: Slippage erodes Sharpe 0.345 → 0.20 or negative
- ADVERSE SELECTION: Only catch trades where market moved against you
- OVERPAYING: Simulation assumes mid price, live pays ask
- MISSED TRADES: Slippage so bad, FOK order doesn't fill

**Prevention:**
1. **Slippage cost modeling** (already in config but verify usage):
   - `spread_cost_pct = 2%` - Cost to cross spread
   - `slippage_cost_pct = 1%` - Additional slippage from market impact
   - `max_total_cost_pct = 8%` - Maximum acceptable total cost
   - Strategy should CHECK these costs BEFORE submitting order

2. **Timing lag measurement**:
   - Track: Leader trade timestamp → You see it → You submit order
   - Target: < 2 seconds end-to-end (WebSocket helps)
   - Alert if lag > 5 seconds (infrastructure problem)
   - Record slippage per trade: (your fill price - leader fill price)

3. **Aggressive limit pricing** (current code already does this):
   ```python
   # Buy: mid + 0.05 (pay up to cross spread)
   price = min(0.99, mid + 0.05)  # Line 125
   # Sell: mid - 0.05 (accept lower price to fill)
   price = max(0.01, mid - 0.05)  # Line 129
   ```

4. **Slippage circuit breaker**:
   ```python
   if abs(fill_price - expected_price) > 0.15:  # 15 cent slippage
       logger.error("EXCESSIVE SLIPPAGE - pause trading")
       self.pause_trading()
   ```

5. **Anti-thundering-herd**:
   - Add random jitter: 0-500ms delay before submitting
   - Reduces chance all bots submit simultaneously
   - Small enough delay doesn't hurt edge

**Detection (warning signs):**
- Live PnL significantly worse than simulation
- Average buy price > leader's average by 10+ cents
- Fill rate drops (many FOK orders rejected)
- Hourly Sharpe 0.345 → 0.10 or negative

**Phase to address:** Phase 4 (Slippage Monitoring & Mitigation)

**Reference:** Config has slippage parameters but strategy doesn't enforce limits before submission.

**Sources:**
- [Understanding Slippage in Copy Trading](https://copytrading.combiz.org/blogs/understanding-slippage-in-copy-trading-and-how-to-avoid-it)
- [Copy Trading Slippage Explanation](https://www.toobit.com/en-US/support/copy-trading-with-zero-slippage-explained)
- [Crypto Slippage in Arbitrage Bots](https://medium.com/@swaphunt/slippage-in-crypto-swaps-why-your-arbitrage-bot-keeps-crying-and-what-i-did-about-it-e561c0603e86)

---

## MODERATE PITFALLS

These cause delays, degraded performance, or technical debt. Address during phased rollout.

---

### Pitfall 5: Regression Bugs from Codebase Refactoring

**What goes wrong:**
- Refactor conviction tracking logic, breaks cumulative spend tracking
- Move files around, import paths break in production
- Simplify code, accidentally remove critical edge case handling
- Test passes but behavior subtly different (WR drops 50% → 45%)

**Why it happens:**
- No comprehensive regression test suite
- Simulation test on old data doesn't catch new bugs
- Code cleanup removes "weird" logic that was actually critical
- Refactor changes timing (order of operations matters in trading)

**Consequences:**
- MONEY LOSS: "Improved" code loses money in production
- SILENT DEGRADATION: PnL slowly declines, hard to pinpoint cause
- PRODUCTION INCIDENT: Bot crashes, misses trading hours
- ROLLBACK PAIN: Can't easily revert to working version

**Prevention:**
1. **Comprehensive test coverage**:
   - Unit tests for all strategy logic (conviction, drawdown, late-entry)
   - Integration tests for order submission flow
   - Regression test: replay 86-hour dataset, verify PnL matches
   - Property-based tests: conviction always increases, never resets mid-hour

2. **Automated validation pipeline**:
   ```bash
   # Before any deployment
   pytest tests/
   python scripts/regression_test.py --expected-pnl 324 --tolerance 5
   python scripts/validate_config.py
   ```

3. **Staged rollout**:
   - Step 1: Run refactored code in DRY_RUN mode for 24 hours
   - Step 2: Paper trade (track decisions but don't execute)
   - Step 3: Live with 10% capital for 8 hours
   - Step 4: Full capital only if metrics match baseline

4. **Behavior checksums**:
   - Log: "Hour 1 decisions: [buy MARKET_A@0.55, skip MARKET_B@0.89]"
   - Compare logs before/after refactor on same data
   - Any difference = investigate thoroughly

5. **Git discipline**:
   - Feature branches for all changes
   - PR reviews required (even solo developer: review your own code)
   - Tag releases: `v1.0-simulation-only`, `v1.1-live-ready`
   - Easy rollback: `git checkout v1.0-simulation-only`

**Detection (warning signs):**
- Live PnL diverges from simulation baseline
- WR drops from 50% to 45% (still positive but degraded)
- Logs show different decisions on same data
- Tests pass but "feels wrong"

**Phase to address:** Phase 5 (Testing & Validation Pipeline)

**Reference:** Extensive simulation results in memory, but no automated regression suite.

**Sources:**
- [Regression Testing Guide 2026](https://www.leapwork.com/blog/regression-testing)
- [Regression Testing in Agile](https://www.aiotests.com/blog/regression-testing-in-agile)
- [Trading Bot Development](https://appinventiv.com/blog/crypto-trading-bot-development/)

---

### Pitfall 6: Race Condition: Own Orders Interpreted as Leader Orders

**What goes wrong:**
- You submit order via py-clob-client
- WebSocket receives your order as "new trade event"
- Bot thinks: "Leader bought! I should buy too!"
- Infinite loop: your buy triggers another buy → another buy → ...

**Why it happens:**
- WebSocket subscribed to all trades for a market
- Your own trades appear in same stream as leader trades
- No filtering: "Is this MY order or leader's order?"
- Async timing: Order submitted → confirmed → WebSocket event (race)

**Consequences:**
- MONEY LOSS: Deploy 2x-10x intended capital before catching error
- BUDGET VIOLATION: Hourly budget $50 → spend $500 in 30 seconds
- POSITION OVEREXPOSURE: Intended 5 shares → end up with 50 shares
- EXCHANGE BAN: Spam exchange with orders, get rate-limited or banned

**Prevention:**
1. **Order tracking** (similar to documented Hyperliquid solution):
   ```python
   # Before submitting order
   my_pending_orders.add(order_id)

   # In WebSocket event handler
   if event.order_id in my_pending_orders:
       logger.info("Received confirmation of MY order")
       my_pending_orders.remove(order_id)
       return  # Don't process as leader trade

   # Process as leader trade
   self.handle_leader_trade(event)
   ```

2. **Address filtering**:
   ```python
   # In WebSocket handler
   if event.user_address == self.my_address:
       return  # Ignore my own trades
   if event.user_address != self.leader_address:
       return  # Ignore other users' trades
   ```

3. **Idempotency keys**:
   - Tag each order with unique ID
   - On WebSocket event: check if order_id already processed
   - Deduplicate events (some exchanges send duplicate messages)

4. **Circuit breaker for runaway trading**:
   ```python
   if orders_this_minute > 10:
       logger.error("RUNAWAY TRADING DETECTED")
       self.emergency_stop()
   ```

**Detection (warning signs):**
- Logs show repeated "Leader bought X" for same market
- Position size 10x larger than expected
- Hourly budget depleted in first minute
- Order submission rate spikes to 10+/second

**Phase to address:** Phase 2 (WebSocket Event Handling)

**Reference:** No evidence of this filter in current codebase. `live_source.py` fetches trades but no deduplication logic visible.

**Sources:**
- [Building Copy Trading Bot with Spot Order Mirroring](https://docs.chainstack.com/docs/hyperliquid-copy-trading-websocket)
- [WebSocket Overview Polymarket](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)

---

### Pitfall 7: Strategy Overfitting to Historical Data

**What goes wrong:**
- Sharpe 0.345 in simulation, -0.10 in live trading
- Train/test/holdout all profitable, but live loses money
- Parameter changes that "improved" backtest hurt live performance
- Market regime changed, strategy no longer works

**Why it happens:**
- Optimized parameters on 86 hours of data (small sample)
- Leader behavior changes (they learned, or market changed)
- Data leakage: Used future information in simulation
- Overfitted to specific market conditions (bull market, high volatility)

**Consequences:**
- MONEY LOSS: Profitable backtest → losing live trades
- FALSE CONFIDENCE: Thought strategy was robust, wasn't
- WASTED TIME: Spent weeks optimizing parameters that don't generalize
- CAPITAL RISK: Deploy full $50/hour, lose it all

**Prevention:**
1. **Walk-forward validation** (beyond train/test/holdout):
   - Train on Week 1, test on Week 2
   - Retrain on Week 1-2, test on Week 3
   - Verify performance doesn't degrade over time

2. **Out-of-sample testing** (critical):
   - Hold back LATEST data for final validation
   - Never touch holdout set until final test
   - If holdout fails: don't deploy, investigate

3. **Conservative parameterization**:
   - Prefer simple rules over complex
   - Wide parameter ranges that work (not single magic number)
   - Robust to +/- 20% parameter changes

4. **Live monitoring with kill switch**:
   - Track live Sharpe ratio hour-by-hour
   - If 8-hour Sharpe < 0: STOP trading
   - If 24-hour Sharpe < 0.1: reduce capital 50%
   - Manual review before resuming

5. **Regime detection**:
   - Track: Leader WR, leader trade frequency, market volatility
   - Alert if metrics diverge from historical (leader WR 95% → 60%)
   - Consider: "This regime looks different, should I trade?"

**Detection (warning signs):**
- Live WR 50% → 35% (below profitable threshold)
- Leader behavior changes (trade size, frequency, timing)
- Macro environment shifts (regulation, Polymarket policy change)
- Conviction filter no longer predicts winners

**Phase to address:** Phase 6 (Live Performance Monitoring)

**Reference:** Memory shows robust train/test/holdout split, but small sample size (86 hours).

**Sources:**
- [Trading Bot Common Mistakes](https://monday.com/blog/ai-agents/best-ai-trading-bot-for-beginners/)
- [Overfitting in Trading Strategies](https://appinventiv.com/blog/crypto-trading-bot-development/)

---

### Pitfall 8: Insufficient Liquidity for Position Sizing

**What goes wrong:**
- Strategy wants $10 position, order book only has $3 at acceptable price
- FOK order rejected due to insufficient liquidity
- Market order executes but slippage 20%+ (ate your edge)
- Large position accumulates slowly, miss the edge window

**Why it happens:**
- Polymarket markets can be thin (especially low-volume tokens)
- Order book depth not checked before submission
- Simulation assumes infinite liquidity at mid price
- Your order IS the market (you move price with each trade)

**Consequences:**
- FILLS MISS: High-conviction trades don't execute
- SLIPPAGE: Partial fills at progressively worse prices
- ADVERSE SELECTION: Only fill when market moves against you
- STRATEGY DEGRADATION: Can't deploy capital at good prices

**Prevention:**
1. **Pre-trade liquidity check**:
   ```python
   orderbook = client.get_order_book(token_id)
   available_liquidity = sum(level['size'] for level in orderbook['asks'][:5])

   if request.amount_dollars > available_liquidity * 0.5:
       # Reduce size or skip trade
       logger.warning(f"Insufficient liquidity: want ${request.amount_dollars}, available ${available_liquidity}")
   ```

2. **Dynamic position sizing**:
   ```python
   target_size = base_size * liquidity_multiplier
   liquidity_multiplier = min(1.0, available_liquidity / base_size / 2.0)
   ```

3. **Fallback to smaller orders**:
   - Try: $10 order (rejected)
   - Retry: $5 order (filled)
   - Better: partial fill than no fill

4. **Market hours awareness**:
   - Low liquidity at night (US timezone)
   - High liquidity during news events
   - Track: Fill rate by hour-of-day, skip low-liquidity hours

**Detection (warning signs):**
- High rejection rate for FOK orders (> 20%)
- Orders fill at prices far from mid (> 10 cents slippage)
- Can't deploy full hourly budget despite signals

**Phase to address:** Phase 4 (Order Sizing & Liquidity)

**Sources:**
- [Slippage in Copy Trading](https://copytrading.combiz.org/blogs/understanding-slippage-in-copy-trading-and-how-to-avoid-it)
- [Market Making on Prediction Markets](https://newyorkcityservers.com/blog/prediction-market-making-guide)

---

## MINOR PITFALLS

These cause annoyance but are fixable. Address opportunistically.

---

### Pitfall 9: Logging Verbosity Extremes

**What goes wrong:**
- Too verbose: Logs fill disk, can't find critical errors
- Too quiet: Production issue, no logs to debug
- Sensitive data logged (private keys, API secrets)
- Logs not structured, can't parse for alerts

**Why it happens:**
- Debug mode left on in production
- No log rotation (logs grow to GB)
- Print statements instead of proper logging
- No logging levels (everything is INFO)

**Consequences:**
- DEBUGGING PAIN: Can't diagnose production issues
- SECURITY RISK: Private key appears in log file
- DISK FULL: Logs consume all space, bot crashes
- NOISE: Can't spot critical warnings in sea of debug logs

**Prevention:**
1. **Structured logging levels**:
   - DEBUG: Strategy decisions, calculations
   - INFO: Orders submitted, positions opened/closed
   - WARNING: Rejections, retries, connection issues
   - ERROR: Exceptions, failures requiring action
   - CRITICAL: Emergency stop, catastrophic failures

2. **Production log config**:
   ```python
   logging.basicConfig(
       level=logging.INFO,  # Not DEBUG in production
       format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
       handlers=[
           logging.FileHandler('bot.log'),
           logging.StreamHandler()  # Also to console
       ]
   )
   ```

3. **Log rotation**:
   ```python
   from logging.handlers import RotatingFileHandler
   handler = RotatingFileHandler('bot.log', maxBytes=10*1024*1024, backupCount=5)
   # Keep 5 files of 10MB each = 50MB max
   ```

4. **Sensitive data filtering**:
   ```python
   # NEVER log private keys
   logger.info(f"Using wallet: {address}")  # OK
   logger.debug(f"Private key: {private_key}")  # NEVER DO THIS

   # Redact in logs
   logger.info(f"API key: {api_key[:8]}...{api_key[-4:]}")
   ```

5. **Structured logging for parsing**:
   ```python
   logger.info("ORDER_SUBMITTED", extra={
       "order_id": order_id,
       "market_id": market_id,
       "side": side,
       "amount": float(amount)
   })
   ```

**Phase to address:** Phase 1 (Initial Setup)

**Sources:**
- [Trading Bot Development Best Practices](https://appinventiv.com/blog/crypto-trading-bot-development/)

---

### Pitfall 10: Clock Skew and Timestamp Mismatches

**What goes wrong:**
- System clock 5 minutes fast, order timestamps wrong
- Timestamp comparison bugs (timezone-naive vs timezone-aware)
- Staleness check fails (think data is stale when it's fresh)
- Hourly budget reset at wrong time (59 minutes vs 61 minutes)

**Why it happens:**
- System clock not synced with NTP
- Mixing timezone-aware and timezone-naive datetimes
- Server timezone different from UTC
- Polymarket API returns UTC, system uses local time

**Consequences:**
- MISSED TRADES: Think data is stale, skip valid trades
- BUDGET ERRORS: Budget resets at wrong time
- LOG CONFUSION: Timestamps don't match order book timestamps
- DEBUGGING PAIN: Event ordering appears wrong

**Prevention:**
1. **Always use UTC timezone-aware datetimes**:
   ```python
   from datetime import datetime, timezone
   now = datetime.now(timezone.utc)  # Always timezone-aware
   ```

2. **NTP sync check at startup**:
   ```bash
   # Verify system clock synced
   timedatectl status  # Should show "System clock synchronized: yes"
   ```

3. **Timestamp validation**:
   ```python
   # Verify API timestamp is reasonable
   api_time = datetime.fromisoformat(api_timestamp)
   now = datetime.now(timezone.utc)
   if abs((api_time - now).total_seconds()) > 300:  # 5 minutes
       logger.error(f"Clock skew detected: API time {api_time}, system time {now}")
   ```

**Phase to address:** Phase 1 (Initial Setup)

**Sources:**
- Codebase analysis shows timezone-aware datetimes used (`src/data/live_source.py`)

---

### Pitfall 11: API Rate Limiting and Throttling

**What goes wrong:**
- Poll API too frequently, get rate-limited or banned
- Rate limit hit during critical trade decision
- No backoff, retry loop makes it worse
- Different endpoints have different rate limits

**Why it happens:**
- No rate limiting in code
- Aggressive polling (every 100ms instead of 1s)
- Retry logic without backoff
- Multiple threads/processes hitting same API

**Consequences:**
- MISSED TRADES: Can't submit order, rate-limited
- TEMPORARY BAN: IP blocked for 1 hour
- RESOURCE WASTE: Spam API with requests that get rejected
- DATA STALENESS: Can't fetch fresh prices

**Prevention:**
1. **Rate limiting in code** (already present, verify):
   ```python
   # src/data/live_source.py has _rate_limit() method
   def _rate_limit(self, endpoint: str) -> None:
       # Ensure minimum 1s between requests per endpoint
   ```

2. **Exponential backoff on errors**:
   ```python
   for attempt in range(5):
       try:
           resp = client.get(url)
           if resp.status_code == 429:  # Rate limited
               sleep_time = (2 ** attempt) + random.uniform(0, 1)
               time.sleep(sleep_time)
               continue
           break
       except Exception:
           pass
   ```

3. **Caching**:
   - Cache market metadata (doesn't change often)
   - Cache order book for 1-2 seconds (reduce API load)

**Phase to address:** Phase 2 (API Reliability)

**Reference:** Basic rate limiting present in `live_source.py` line 41-45.

**Sources:**
- [Polymarket API Documentation](https://docs.polymarket.com/)

---

## PHASE-SPECIFIC WARNINGS

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| **Phase 1: Environment Setup** | Wrong env vars, clock skew | Validation script, NTP check |
| **Phase 2: WebSocket Integration** | Connection drops, race condition | Heartbeat, reconnect logic, order deduplication |
| **Phase 3: Order Management** | Rejections not handled, budget leak | Confirmation flow, min size validation |
| **Phase 4: Slippage & Liquidity** | Excessive slippage, thin orderbooks | Pre-trade liquidity check, slippage monitoring |
| **Phase 5: Testing Pipeline** | Regressions from refactoring | Regression test suite, staged rollout |
| **Phase 6: Live Monitoring** | Strategy overfitting, regime change | Kill switch, Sharpe monitoring, regime detection |

---

## CONFIDENCE ASSESSMENT

| Area | Confidence | Rationale |
|------|------------|-----------|
| Environment Variables | HIGH | Official docs + security research + codebase analysis |
| WebSocket Reliability | HIGH | Official docs + GitHub issues + community reports |
| Order Rejections | HIGH | py-clob-client docs + Polymarket API docs |
| Slippage & Timing | MEDIUM | Community reports, some specifics not verified |
| Regression Testing | MEDIUM | General trading bot best practices, not Polymarket-specific |
| Overfitting | MEDIUM | Based on research memory (86-hour sample is small) |
| Race Conditions | HIGH | Documented in Hyperliquid/Polymarket community |
| Liquidity Issues | MEDIUM | General copy trading issue, not Polymarket-specific data |

---

## VERIFICATION NOTES

**Verified with official sources:**
- Polymarket WebSocket docs (heartbeat, reconnection)
- py-clob-client minimum sizes ($1 market, 5 shares limit)
- Environment variable security concerns
- Order confirmation flow in py-clob-client

**Verified with codebase analysis:**
- Current env loading has basic validation but needs hardening
- Order rejection handling present but budget reconciliation missing
- Timezone-aware datetimes used correctly
- Rate limiting present in live_source.py

**Requires validation:**
- Actual slippage in live trading (will measure during Phase 4)
- WebSocket stability with current py-clob-client version
- Optimal polling intervals for REST fallback

---

## CRITICAL ACTION ITEMS

**Before deploying to live trading:**

1. [ ] **Environment validation script** - Verify env vars match expected wallet
2. [ ] **Preflight test order** - Submit $1 test order, verify execution
3. [ ] **WebSocket heartbeat** - Implement PING every 10 seconds
4. [ ] **Order confirmation flow** - Budget only decrements on confirmed fill
5. [ ] **Minimum size validation** - Reject orders below $1 / 5 shares
6. [ ] **Order deduplication** - Track own orders, don't copy yourself
7. [ ] **Regression test suite** - Replay 86-hour dataset, verify PnL within 5%
8. [ ] **Kill switch** - Stop trading if 8-hour Sharpe < 0
9. [ ] **Connection monitoring** - Alert if no data received for 60 seconds
10. [ ] **Slippage tracking** - Log fill price vs expected price

---

## SOURCES

### Official Documentation
- [Polymarket WebSocket Overview](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)
- [Polymarket Place Order API](https://docs.polymarket.com/developers/CLOB/orders/create-order)
- [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client)

### Community Issues & Reports
- [WebSocket data stream stops after some time](https://github.com/Polymarket/real-time-data-client/issues/26)
- [Websocket reconnection mechanism not working](https://github.com/Polymarket/rs-clob-client/issues/185)
- [FOK order decimal places error](https://github.com/Polymarket/py-clob-client/issues/121)

### Copy Trading Best Practices
- [Building Copy Trading Bot with Order Mirroring](https://docs.chainstack.com/docs/hyperliquid-copy-trading-websocket)
- [Understanding Slippage in Copy Trading](https://copytrading.combiz.org/blogs/understanding-slippage-in-copy-trading-and-how-to-avoid-it)
- [News-Driven Polymarket Bots Guide](https://www.quantvps.com/blog/news-driven-polymarket-bots)

### Trading Bot Development
- [Step-by-Step Crypto Trading Bot Development Guide (2026)](https://appinventiv.com/blog/crypto-trading-bot-development/)
- [How to Build an AI Trading Bot](https://www.alchemy.com/blog/how-to-build-an-ai-trading-bot)
- [Market Making on Prediction Markets Guide](https://newyorkcityservers.com/blog/prediction-market-making-guide)

### Testing & Quality Assurance
- [Regression Testing Guide 2026](https://www.leapwork.com/blog/regression-testing)
- [Regression Testing in Agile](https://www.aiotests.com/blog/regression-testing-in-agile)

### Security
- [Are Environment Variables Safe for Secrets in 2026?](https://securityboulevard.com/2025/12/are-environment-variables-still-safe-for-secrets-in-2026/)

---

**END OF PITFALLS DOCUMENTATION**

**Next step for orchestrator:** Use these pitfalls to inform roadmap phase structure and research requirements for each phase.
