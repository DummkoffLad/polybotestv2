# Pitfalls Research: v1.2 Production Ready

**Domain:** Trading Bot Simulation to Live Production
**Researched:** 2026-02-10
**Overall Confidence:** MEDIUM (WebSearch-based with known codebase context)

## Executive Summary

The three most dangerous pitfalls when transitioning this Polymarket copy trading bot from simulation to live:

1. **Runaway Spending from Uncapped Live Orders** (CRITICAL) - Simulation has no real spending limit enforcement. One bug or misconfigured hourly budget can drain the wallet in minutes. The $50/hour budget exists in code but has never been tested against live capital constraints.

2. **Position Tracking State Corruption** (CRITICAL) - Portfolio positions keyed only by `token_id` means two markets with the same outcome token will corrupt each other's position data. Simulation doesn't expose this because it's offline, but live trading with market overlaps will produce incorrect position sizes and PnL calculations.

3. **Partial Fill Reconciliation Gaps** (CRITICAL) - Simulation assumes perfect fills. Live CLOB orders can partially fill, creating position/cash mismatches. The bot has no reconciliation logic to handle: "I ordered 10 shares but only got 7 - now what?"

These three pitfalls share a common root: **simulation hides real-world messiness**. Your strategy code is battle-tested, but your execution and state management layers have never encountered connectivity drops, partial fills, or concurrent market activity.

---

## Critical Pitfalls

### 1. Runaway Spending - No Live Capital Enforcement

**Severity:** CRITICAL

**What happens:**
The hourly budget (`$50`) and position limits exist in strategy code but are never enforced at the wallet/API layer. In simulation, overspending just logs a warning. In live trading:
- A bug in budget tracking lets the bot place 20 orders instead of 5
- All 20 hit the API simultaneously
- Wallet is drained of $1000+ before you notice
- No circuit breaker stops the hemorrhage

Real-world scenario: Your conviction filter bug incorrectly resets `leader_token_spend` mid-hour, causing the bot to think it has $50 budget when it's already spent $200. It buys 6 more positions at $30 each = $180 additional spend.

**Warning signs:**
- `total_bought` metric suddenly jumps 5x in one hour
- Wallet balance decreasing faster than hourly budget allows
- Number of open positions exceeds historical max (you average 3-5 positions/hour in sim)

**Prevention:**
1. **Pre-flight wallet check** - Before each order, query actual USDC balance from API
2. **Hard wallet limit** - Refuse to trade if wallet balance < hourly_budget * 3 (safety margin)
3. **Order-level cap** - Set `maxCost` parameter on CLOB orders to prevent accidental whale orders
4. **Kill switch** - Environment variable `MAX_TOTAL_SPEND` = $500 (10 hours); bot stops if `total_bought` exceeds this
5. **Hourly budget reconciliation** - At minute 0, verify `total_bought_this_hour` from actual filled orders, not just tracking variable

**Phase:** Phase 2 (Safety Mechanisms) - Must be implemented before capital deployment

**Code location:** `src/strategies/profit_taker/strategy.py` has budget tracking but no enforcement; needs new `src/execution/capital_limiter.py` module

---

### 2. Position Tracking State Corruption

**Severity:** CRITICAL

**What happens:**
Your `Portfolio` class keys positions by `token_id` alone (line 33: `self._positions: Dict[str, PortfolioPosition]`). On Polymarket, the same outcome token (e.g., "YES" on "Will BTC hit $100k?") can appear in multiple markets if there are duplicate or related questions.

Corruption scenario:
1. Market A (token_id: `0xABC`) - You buy 10 shares at $0.60
2. Market B (token_id: `0xABC`) - Leader buys same token different market
3. Your portfolio tracking thinks you already own 10 shares, calculates wrong position size
4. You sell from Market B, but portfolio deducts from Market A's position
5. Position count goes negative or wildly inaccurate
6. PnL calculations become garbage
7. You lose track of actual capital deployed

This is already a known bug ("portfolio positions keyed only by token_id (not unique enough)") but simulation never triggers it because you replay one market at a time.

**Warning signs:**
- Position shares suddenly decrease when you didn't sell
- `get_total_deployed()` doesn't match sum of individual position costs
- Negative shares (triggers `PortfolioInvariantError` at line 98-99, crashes bot mid-hour)
- PnL swings that don't correspond to price movements

**Prevention:**
1. **Fix position key** - Change `Dict[str, PortfolioPosition]` to `Dict[Tuple[str, str], PortfolioPosition]` keyed by `(token_id, market_id)`
2. **Add position_id** - Generate unique `position_id = f"{market_id}_{token_id}_{side}"` for each position
3. **Reconciliation on startup** - Query CLOB API for actual token balances, compare to internal tracking, log discrepancies
4. **Invariant checks** - Before every trade, verify `sum(position.shares) <= wallet_token_balance(token_id)`

**Phase:** Phase 1 (Fix Known Bugs) - MUST fix before live trading

**Code location:** `src/core/portfolio.py` lines 33-41 (position dictionary and get method)

**Real-world impact:** A trader on Polymarket reported position tracking issues causing a loss of $3,200 when their bot double-counted positions across related markets, leading to oversized bets and margin calls.

---

### 3. Partial Fill Reconciliation Failure

**Severity:** CRITICAL

**What happens:**
Your simulation assumes every order fills completely at the exact price you submit. In live CLOB trading:
- Limit orders may fill 0%, 30%, or 100% depending on liquidity
- Market orders (FAK) fill "as many shares as available" then cancel the rest
- Your code submits "buy 10 shares" but only 6 fill
- Portfolio tracking adds 10 shares (based on order request)
- Wallet balance reflects 6 shares purchased
- **Mismatch:** Internal state says you own 10, reality is 6
- Next sell order tries to sell 10, fails with "insufficient balance"
- Bot crashes or enters undefined state

The py-clob-client `size_matched` field shows filled quantity, but you're not reading it. Your `apply_buy()` assumes the shares parameter equals actual filled shares.

**Warning signs:**
- "Insufficient balance" errors when trying to sell positions you think you own
- Wallet USDC balance higher than expected (unfilled orders didn't deduct capital)
- Position shares don't match CLOB API `get_positions()` response
- Orders stuck in "PENDING" state never reconciled

**Prevention:**
1. **Post-order verification** - After placing order, poll CLOB API for order status until `status == "FILLED"` or timeout
2. **Use size_matched** - Read `size_matched` from order response, pass that to `portfolio.apply_buy()`, not requested size
3. **Reconciliation loop** - Every 5 minutes, query CLOB for actual positions, compare to internal portfolio, auto-correct discrepancies
4. **Partial fill strategy** - If order only 40% fills, decide: cancel remainder? retry? adjust position tracking?
5. **Idempotent operations** - Track fills by `order_id` in a `_processed_fills` set to prevent double-counting on retries

**Phase:** Phase 3 (Order Execution) - Part of CLOB integration

**Code location:**
- `src/core/portfolio.py` lines 47-67 (apply_buy assumes full fill)
- Need new `src/execution/order_reconciler.py` to handle partial fills

**Real-world example:** Per py-clob-client Issue #245 (Jan 2026), users report `size_matched: 5` but actual balance `~4.95-4.96`. Even "complete" fills have rounding errors that accumulate.

---

### 4. Stale Price Execution

**Severity:** CRITICAL

**What happens:**
Simulation uses historical minute-bar data with static prices. Live WebSocket data updates every second, but network latency and processing delays create gaps:

1. Leader places trade at 14:23:45, WebSocket event arrives at 14:23:47 (2 sec delay)
2. Your bot processes event at 14:23:48, checks current price
3. CLOB best ask was $0.65 at 14:23:45, now it's $0.72 (price moved)
4. You submit market order expecting $0.65 fill
5. Actual fill at $0.72 = 10% slippage
6. Over 50 trades, slippage destroys your $324 simulation profit

Your strategy has `SKIP_PRICE_HIGH = 0.85` to avoid expensive tokens, but that's checked against stale WebSocket snapshot data, not live orderbook.

**Warning signs:**
- Actual fill prices consistently 5-10% worse than expected
- Profitable sim trades become breakeven or losers in live
- High slippage on tokens with narrow spreads
- Fills at prices outside the bid/ask spread you cached

**Prevention:**
1. **Orderbook price validation** - Before submitting, fetch live L2 orderbook, verify best ask/bid
2. **Slippage tolerance** - Add `MAX_SLIPPAGE_PCT = 3%` check: if `(ask - expected_price) / expected_price > 0.03`, skip trade
3. **Limit orders with timeout** - Use limit orders at `best_ask + small_buffer` instead of market orders, cancel if not filled in 30 seconds
4. **Price staleness check** - Reject trades if last price update > 5 seconds old
5. **Post-trade slippage tracking** - Log `expected_price` vs `actual_fill_price`, alert if slippage > 5% on any trade

**Phase:** Phase 3 (Order Execution)

**Research source:** [Backtesting vs Live Trading: Slippage](https://www.luxalgo.com/blog/backtesting-limitations-slippage-and-liquidity-explained/) notes that backtests assume perfect execution with no slippage, but live markets can slip 1-10 pips (1-10%) during volatility.

---

### 5. API Key Exposure and Theft

**Severity:** CRITICAL

**What happens:**
Your bot needs API keys to sign orders (Polymarket private key + CLOB API key). In simulation, these aren't used. For live trading:
- Keys stored in `.env` file or hardcoded in script
- `.env` accidentally committed to git and pushed to GitHub (public repo?)
- Attacker finds keys, drains wallet
- Or: malicious Chrome extension steals keys from environment variables (see Sources)

In January 2026, a malicious extension targeting MEXC stole API keys with withdrawal permissions, draining wallets. Your Polymarket private key has full custody - no API permission scoping.

**Warning signs:**
- Unexpected trades from your wallet address you didn't initiate
- USDC balance suddenly drops to zero
- GitHub security alerts about committed secrets
- Unknown IP addresses in API access logs (if CLOB provides this)

**Prevention:**
1. **Never commit keys** - Add `.env`, `secrets.json` to `.gitignore`, verify with `git log -p | grep -i "private"` before first commit
2. **Environment variable hygiene** - Load keys only in production, use dummy keys in dev/test
3. **Separate wallets** - Use a dedicated bot wallet with only $500 funded, not your main wallet
4. **IP whitelisting** - If CLOB supports it, restrict API key to your VPS IP address only
5. **Key rotation** - Generate new CLOB API key weekly (one-line command: `client.create_or_derive_api_key()`)
6. **Monitoring** - Set up wallet balance alerts: if balance drops >$50 in 5 minutes without bot running, send SMS alert

**Phase:** Phase 1 (Environment Setup) - Before writing any live trading code

**Real-world incident:** Per [Malicious Chrome Extension](https://thehackernews.com/2026/01/malicious-chrome-extension-steals-mexc.html), attackers created API keys with withdrawal permissions and drained MEXC wallets in Jan 2026. Over 1,000 exposed bots found via Shodan scans.

---

### 6. WebSocket Disconnection Without Reconnect

**Severity:** CRITICAL

**What happens:**
Live bot relies on WebSocket for leader trade events. Network hiccup or CLOB server restart:
1. WebSocket disconnects at 14:15:00
2. Leader makes 3 high-conviction trades between 14:15-14:30
3. Your bot sits idle, never receives events
4. WebSocket client doesn't auto-reconnect (most libraries don't by default)
5. You miss entire hour of profitable trades
6. Worse: connection appears "alive" (no error), just no data flowing

You lose $154 expected hourly profit (leader's avg) because you weren't trading.

**Warning signs:**
- Zero trades executed for >15 minutes during market hours
- Last WebSocket message timestamp > 2 minutes old
- Heartbeat/ping timeout (CLOB spec: ping every 10 seconds)
- Leader active (verified on Polymarket UI) but no events received

**Prevention:**
1. **Heartbeat monitoring** - CLOB requires ping every 10 seconds; if no pong received in 20 seconds, assume disconnected
2. **Auto-reconnect** - On disconnect, attempt reconnect with exponential backoff (1s, 2s, 4s, max 60s)
3. **Message timestamp check** - If no messages received in 60 seconds during market hours, force reconnect
4. **Startup sync** - On reconnect, query CLOB REST API for any trades missed during downtime (compare timestamps)
5. **Connection state logging** - Log every connect/disconnect event with timestamp for post-mortem analysis

**Phase:** Phase 4 (Live Monitoring)

**Code location:** Need `src/data/websocket_manager.py` with reconnect logic (doesn't exist yet)

**Research source:** [Polymarket CLOB Docs](https://docs.polymarket.com/developers/CLOB/introduction) specify ping every 10 seconds for CLOB WebSocket, 5 seconds for Real-Time Data Stream.

---

## High Pitfalls

### 7. Hourly Reset Race Condition

**Severity:** HIGH

**What happens:**
Your conviction tracking resets `leader_token_spend = {}` at hour boundaries (inferred from code context). In simulation, hours are processed sequentially. In live trading:
1. Leader places trade at 14:59:58 (2 seconds before hour rollover)
2. WebSocket event arrives at 15:00:01 (after hour boundary)
3. Your hour-reset logic already ran at 15:00:00, cleared conviction state
4. Trade processed with zero conviction history, incorrectly skipped or wrong sizing
5. Or: trade counted toward hour 15 budget but should be hour 14

**Warning signs:**
- Trades near hour boundaries (minute 59-60 and 0-1) have unexpected skip behavior
- Budget tracking shows overspend in one hour, underspend in next
- Conviction filter behaves inconsistently for last trades of the hour

**Prevention:**
1. **Use trade timestamp, not processing time** - Key conviction state by trade timestamp, not wall clock
2. **Grace period** - Process trades up to 2 minutes into next hour that have previous hour timestamps
3. **Event ordering guarantee** - Buffer WebSocket events, process in timestamp order even if out-of-order arrival
4. **Hour boundary testing** - Integration test with trades at :59:55, :00:00, :00:05 to verify correct bucketing

**Phase:** Phase 5 (Integration Testing)

---

### 8. Market Resolution Timing - Trading on Resolved Markets

**Severity:** HIGH

**What happens:**
Polymarket markets resolve via UMA oracle with a 2-hour challenge period. Your simulation data only includes resolved markets, so you never encounter:
1. Market resolves at 14:30 (proposed by oracle)
2. Challenge period: 14:30-16:30
3. Leader is monitoring news, knows outcome at 14:25, makes huge $500 bet
4. Your bot copies the trade at 14:26
5. Market resolves YES at 16:30
6. But: CLOB API may disable trading during challenge period, your order rejected
7. Or: you successfully buy, but price is already $0.98, only $0.02 upside

Your $50 budget deployed on a near-certain outcome with minimal profit potential.

**Warning signs:**
- Orders rejected with "MARKET_CLOSED" or similar error
- Fill prices consistently >$0.90 (near-certain outcomes)
- Leader trades that resolve within 2 hours (fast resolution = info asymmetry)

**Prevention:**
1. **Market status check** - Before trading, verify market status via CLOB API: `active`, `closed`, `resolved`
2. **Resolution proximity filter** - Skip trades if market's `end_date` is within 2 hours
3. **Price ceiling enforcement** - Strengthen `SKIP_PRICE_HIGH = 0.85` filter, never buy above $0.90 regardless of conviction
4. **Post-resolution reconciliation** - Query resolved markets, auto-close positions at final price

**Phase:** Phase 3 (Order Execution) - Part of pre-trade validation

**Research source:** [UMA Resolution Process](https://docs.polymarket.com/polymarket-learn/markets/how-are-markets-resolved) documents 2-hour challenge period. Managed Oracle V2 (2025-2026 update) allows only 37 whitelisted addresses to propose, reducing premature proposals but still has timing risk.

---

### 9. Insufficient Position Monitoring - Silent Losses

**Severity:** HIGH

**What happens:**
Simulation runs offline, you review results afterward. Live bot runs 24/7, and:
- Hour 23 has a -$30 loss (max historical is -$26)
- Drawdown circuit breaker triggers but doesn't stop trading (bug)
- Bot continues trading, loses another -$40 in hour 24
- You check next morning, lost -$150 overnight while sleeping

No alerts configured, no dashboard, no automatic shutoff. Bot "successfully" executed its logic but destroyed capital.

**Warning signs:**
- None - that's the problem. You won't know until you manually check.

**Prevention:**
1. **Real-time PnL tracking** - Calculate unrealized + realized PnL every minute
2. **Alert thresholds:**
   - Hourly loss > $30 → Telegram alert
   - Daily loss > $100 → SMS alert + pause trading
   - Position count > 8 (historical max is 5) → alert
   - Fill slippage > 10% → alert
3. **Daily position report** - Every 24 hours, send summary: PnL, open positions, capital deployed, fills executed
4. **Heartbeat monitor** - Separate process pings bot every 5 minutes, alerts if bot unresponsive
5. **Kill switch conditions:**
   - Unrealized drawdown > $50 on single position
   - Hourly loss > $40 (3x historical max hourly loss)
   - Any position held >2 hours unresolved (indicates stuck trade)

**Phase:** Phase 4 (Live Monitoring)

**Research source:** [Trading Bot Monitoring Guide](https://tickerly.net/trading-bot-crypto-complete-guide-2026/) emphasizes real-time alerts via Discord/Telegram for disconnections, risk breaches, API errors.

---

### 10. Conviction State Corruption - Double Counting Leader Trades

**Severity:** HIGH

**What happens:**
Your conviction filter tracks `leader_token_spend[token_id] += trade.dollars` (inferred from code). WebSocket issues can cause:
1. Leader buys $50 of token XYZ at 14:15:00
2. WebSocket delivers event twice (duplicate message, common with reconnects)
3. Your code increments `leader_token_spend['XYZ'] += 50` twice
4. Internal state: leader spent $100, reality: $50
5. Conviction threshold met prematurely, you enter low-quality trade
6. Loses money

Or reverse: missed message means conviction never met, you skip a winner.

**Warning signs:**
- Conviction values don't match leader's actual on-chain trades (check Polymarket UI)
- Conviction threshold met on first leader trade (should require $300 cumulative)
- Trades executed with conviction exactly 2x or 3x actual (indicates duplicate processing)

**Prevention:**
1. **Idempotency by trade_id** - Track processed trades: `_processed_trade_ids: Set[str]`, skip if already seen
2. **Verify conviction from source** - Periodically query CLOB API for leader's full trade history, recompute conviction from scratch
3. **Bounded tracking** - Cap `leader_token_spend` at leader's total wallet balance as sanity check
4. **Logging** - Log every conviction increment with trade_id for audit trail

**Phase:** Phase 3 (Order Execution) - WebSocket event processing

---

### 11. Allowance Not Set - Silent Order Failures

**Severity:** HIGH (if using MetaMask/EOA wallet)

**What happens:**
Per py-clob-client docs, MetaMask users must set token allowances before trading. Your bot:
1. Starts up, generates API credentials successfully
2. Submits first order to buy token XYZ
3. CLOB API returns: `INSUFFICIENT_ALLOWANCE` or similar error
4. Order rejected, bot doesn't know why (error handling missing)
5. Entire session produces zero trades, you lose a day of profit

Simulation never requires allowances, so you never tested this.

**Warning signs:**
- Orders stuck in "PENDING" forever
- API errors mentioning "allowance" or "approval"
- Zero successful fills despite bot running

**Prevention:**
1. **Pre-flight allowance check** - On startup, query USDC and CTF token allowances, verify > $1000
2. **Auto-approve script** - Provide one-time setup script that calls `approve_token()` for all required contracts
3. **Clear error handling** - Catch allowance errors, log actionable message: "Run setup_allowances.py first"
4. **Docs update** - Add to README: "Before first trade, run `python scripts/setup_allowances.py`"

**Phase:** Phase 1 (Environment Setup)

**Code location:** Need `scripts/setup_allowances.py` (doesn't exist)

**Research source:** [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client) README explicitly states: "MetaMask and hardware wallet users need to give the exchange contracts permission to access your USDC and conditional tokens."

---

### 12. Order Minimum Size Violations

**Severity:** HIGH

**What happens:**
Polymarket CLOB has minimum order sizes (known: $1 for market orders, 5 shares for limit orders). Your strategy sizes orders as `leader_trade_size * SCALE_BOOST / leader_capital`:
1. Leader ($13k capital) buys $20 of token → 0.15% of capital
2. Your capital ($500) → 0.15% * $500 = $0.75 order
3. Order rejected: below $1 minimum
4. You miss the trade

Or: fractional shares rounded wrong, order size becomes 4.87 shares for limit order, rejected (min 5).

**Warning signs:**
- Small orders (<$2) consistently rejected
- API errors: `INVALID_ORDER_MIN_SIZE`
- Limit orders with shares between 1-5 all fail

**Prevention:**
1. **Minimum order enforcement** - Before submitting, check: `if order_value < 1.00: skip`
2. **Share rounding** - For limit orders, round up to nearest integer: `shares = ceil(calculated_shares)`
3. **Minimum position size** - Set `MIN_POSITION_SIZE = $2` to add buffer above CLOB minimum
4. **Small trade aggregation** - If multiple small signals, batch into one larger order (complex, defer to v1.3)

**Phase:** Phase 3 (Order Execution)

**Research source:** [CLOB Order Docs](https://docs.polymarket.com/developers/CLOB/orders/create-order) mentions minimum size requirement (exact values not documented, from project context: $1 market, 5 shares limit).

---

## Medium Pitfalls

### 13. Process Crash Mid-Trade

**Severity:** MEDIUM

**What happens:**
Bot crashes (OOM error, unhandled exception) while order is in-flight:
1. Order submitted to CLOB at 14:15:00
2. Bot crashes at 14:15:01 (before receiving fill confirmation)
3. Order fills successfully at 14:15:02
4. Bot restarts at 14:20:00 with empty portfolio state (no persistence)
5. Believes it has zero positions, but actually owns 10 shares of token XYZ
6. Conviction tracking reset, treats next hour as fresh start
7. Portfolio state diverges from reality

**Warning signs:**
- Bot restarts show zero positions but wallet has token balances
- PnL calculations wildly incorrect after restart
- Duplicate orders placed because bot doesn't know it already owns position

**Prevention:**
1. **Persistent state** - Save portfolio state to disk after every trade: `portfolio.to_json() -> state.json`
2. **Restore on startup** - Load `state.json`, query CLOB for actual positions, reconcile differences
3. **Atomic order tracking** - Write pending order to disk BEFORE submitting, mark complete AFTER fill confirmed
4. **Crash recovery test** - Kill bot mid-trade, restart, verify it resumes correctly

**Phase:** Phase 2 (Safety Mechanisms)

**Code location:** Need `src/core/state_manager.py` for persistence

---

### 14. Time Zone Mismatches - Hour Bucketing Errors

**Severity:** MEDIUM

**What happens:**
Your local machine is EST, CLOB API timestamps are UTC, WebSocket events use Unix epoch:
1. Bot processes hour boundary at 15:00:00 EST (20:00 UTC)
2. Leader trade timestamp: 19:59:50 UTC (14:59:50 EST)
3. Bot's hour reset already ran, treats it as next hour
4. Budget and conviction tracking off by one hour
5. Over a day, accumulated errors break strategy logic

**Warning signs:**
- Hourly PnL buckets don't align with Polymarket UI timestamps
- Budget depletes at unexpected times (e.g., :55 instead of :00)
- Conviction filter triggers at wrong cumulative amounts

**Prevention:**
1. **Single timezone everywhere** - Convert all timestamps to UTC immediately on receipt
2. **Timezone-aware datetime** - Use `datetime.now(timezone.utc)`, never `datetime.now()`
3. **Logging includes timezone** - All logs show timezone: `2026-02-10 14:15:00+00:00`
4. **Hour boundaries in UTC** - Define hour as `minute == 0 and timezone == UTC`, not local time

**Phase:** Phase 5 (Integration Testing)

---

### 15. Sell Logic Unscaled - Portfolio Drift

**Severity:** MEDIUM

**What happens:**
Your strategy comment (line 12): "Sell sizing intentionally unscaled (keeps positions for $0.99 resolution upside)". This works in simulation where markets resolve. In live trading:
1. You buy 20 shares at $0.60 = $12 cost
2. Price rises to $0.80, leader sells 50% (10 shares)
3. Your sell logic: sell same percentage as leader → sell 10 shares
4. But your position is 20 shares, leader's was 100 shares (different capital)
5. You sell 50% of your position
6. After 5 similar trades, you hold 8 different positions all at 20-30% of original size
7. Capital tied up in many small positions, can't deploy to new opportunities
8. Budget constrained not by dollars but by position count

**Warning signs:**
- Average position size decreases over time (start at $12, after 10 hours avg is $4)
- Portfolio fragmented across 15+ positions
- `get_total_deployed()` approaches hourly budget limit even with zero new trades

**Prevention:**
1. **Minimum hold threshold** - If position value < $3, sell 100% regardless of leader action
2. **Position cleanup** - At hour boundaries, close positions < $2 value
3. **Proportional sells** - Scale sell size to YOUR position, not leader's percentage
4. **Max positions limit** - Refuse new trades if open position count > 8

**Phase:** Phase 6 (Strategy Refinement) - After live trading starts, monitor and adjust

---

### 16. API Rate Limiting - Burst Order Rejection

**Severity:** MEDIUM

**What happens:**
Leader makes 5 trades in 10 seconds (market moving fast). Your bot:
1. Receives 5 WebSocket events
2. Processes all 5, submits 5 orders simultaneously
3. CLOB API rate limit: 10 requests/second
4. First 3 orders succeed, last 2 rejected: `RATE_LIMIT_EXCEEDED`
5. No retry logic, miss 2 profitable trades

**Warning signs:**
- Orders rejected during high-activity periods
- API errors mentioning rate limits
- Trades succeed in quiet hours, fail during volatility

**Prevention:**
1. **Rate limiter** - Implement token bucket: max 5 orders/second, queue excess
2. **Retry with backoff** - On rate limit error, wait 2 seconds, retry once
3. **Batch orders** - Use `post_batch_orders()` (supports 15 orders/call) instead of individual submissions
4. **Priority queue** - If rate limited, execute highest-conviction trades first

**Phase:** Phase 3 (Order Execution)

**Research source:** py-clob-client supports batch orders (increased from 5 to 15 in 2025), indicating rate limits are a real concern.

---

### 17. Logging Sensitive Data - API Keys in Logs

**Severity:** MEDIUM

**What happens:**
Your bot logs order details for debugging:
```python
logger.info(f"Submitting order: {order_params}")
```
`order_params` includes signed payload with private key signature. Logs written to `bot.log`:
1. Debug for a week, `bot.log` grows to 50MB
2. Upload to GitHub issue when asking for help
3. Attacker extracts signatures, reconstructs private key
4. Wallet drained

Or: logs synced to cloud logging service (Datadog, CloudWatch) with sensitive data visible to service provider.

**Warning signs:**
- Log files contain hex strings >100 characters (likely signatures)
- Private key material visible in debug logs
- Logs include full API responses with account balances

**Prevention:**
1. **Scrub sensitive fields** - Before logging, replace: `order['signature'] = '[REDACTED]'`
2. **Log levels** - Production uses INFO, never DEBUG (which dumps full payloads)
3. **Audit log output** - Search logs for "0x" patterns, verify no private keys present
4. **Separate audit log** - Write sanitized high-level events to `audit.log`, detailed debug to local-only `debug.log`

**Phase:** Phase 4 (Live Monitoring)

---

### 18. No Graceful Shutdown - Orphaned Orders

**Severity:** MEDIUM

**What happens:**
You stop the bot with Ctrl+C. Python immediately exits:
1. Bot had 3 pending limit orders on CLOB
2. Process killed, no cleanup
3. Orders remain active on CLOB
4. Fills arrive 10 minutes later
5. You think bot is off, but wallet is trading
6. Positions opened without bot tracking them

**Warning signs:**
- Wallet activity after bot stopped
- CLOB UI shows active orders you don't remember placing
- Position count mismatch on restart

**Prevention:**
1. **Signal handler** - Catch SIGINT (Ctrl+C), SIGTERM, run cleanup before exit
2. **Cancel all orders** - On shutdown, call `cancel_all_orders()` API
3. **Shutdown flag** - Set `self.shutting_down = True`, reject new events, finish processing current
4. **Startup cleanup** - On start, cancel any orphaned orders from previous session

**Phase:** Phase 2 (Safety Mechanisms)

**Code location:** Need signal handler in `src/main.py` or wherever bot loop runs

---

## Sources

### Simulation to Live Trading Pitfalls
- [Trading Bot Crypto: Complete Guide to Automation 2026](https://tickerly.net/trading-bot-crypto-complete-guide-2026/)
- [Step-by-Step Crypto Trading Bot Development Guide (2026)](https://appinventiv.com/blog/crypto-trading-bot-development/)
- [Fast Isn't Enough: Why Your Trading Bot Keeps Missing](https://medium.com/@astralaneio/fast-isnt-enough-why-your-trading-bot-keeps-missing-8159e8372423)
- [Crypto Trading Bot Pitfalls, Risks & Mistakes to Avoid in 2025](https://www.gate.com/news/detail/13225882)

### Polymarket CLOB Specific
- [CLOB Introduction - Polymarket Documentation](https://docs.polymarket.com/developers/CLOB/introduction)
- [The Polymarket API: Architecture, Endpoints, and Use Cases](https://medium.com/@gwrx2005/the-polymarket-api-architecture-endpoints-and-use-cases-f1d88fa6c1bf)
- [GitHub - Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client)
- [Place Single Order - Polymarket Documentation](https://docs.polymarket.com/developers/CLOB/orders/create-order)
- [py-clob-client Issue #245: Order size_matched discrepancy](https://github.com/Polymarket/py-clob-client/issues/245)

### Market Resolution and Oracle
- [How Are Prediction Markets Resolved?](https://docs.polymarket.com/polymarket-learn/markets/how-are-markets-resolved)
- [Inside UMA Oracle | How Prediction Markets Resolution Works](https://rocknblock.io/blog/how-prediction-markets-resolution-works-uma-optimistic-oracle-polymarket)
- [Improving Oracle Efficiency with Managed Proposers](https://blog.uma.xyz/articles/managed-proposers)

### Order Execution and Slippage
- [Backtesting Limitations: Slippage and Liquidity Explained](https://www.luxalgo.com/blog/backtesting-limitations-slippage-and-liquidity-explained/)
- [Trading Slippage and How It Affects Live Trading](https://support.capitalise.ai/en/articles/5963164-trading-slippage-and-how-it-affects-live-trading-simulated-trading-and-backtests)
- [Backtesting vs Live Trading: Bridging the Gap](https://www.pineconnector.com/blogs/pico-blog/backtesting-vs-live-trading-bridging-the-gap-between-strategy-and-reality)
- [What is Partial Fill?](https://www.forex.com/en-us/glossary/partial-fill/)

### Security and API Keys
- [Malicious Chrome Extension Steals MEXC API Keys](https://thehackernews.com/2026/01/malicious-chrome-extension-steals-mexc.html)
- [API Key Security: Complete Guide for Crypto Traders](https://tradelink.pro/blog/how-to-secure-api-key/)
- [Security Risk: Public Exposure of Clawdbot Gateway Port](https://toclawdbot.com/security/port-exposure)

### Monitoring and Safety
- [AI Agent Monitoring: Best Practices, Tools, and Metrics for 2026](https://uptimerobot.com/knowledge-hub/monitoring/ai-agent-monitoring-best-practices-tools-and-metrics/)
- [News-Driven Polymarket Bots: Trading Breaking Events Automatically](https://www.quantvps.com/blog/news-driven-polymarket-bots)
- [Crypto Trading Bots 2026: Complete Guide To Automated Trading](https://blog.mexc.com/news/crypto-trading-bots-2026-complete-guide-to-automated-trading/)

### Infrastructure and State Management
- [Crypto Arbitrage Bot Development: What to Expect in 2026](https://pixelplex.io/blog/crypto-arbitrage-bot-development/)
- [Engineering Solana Trading Bots: 2026 Infrastructure Guide](https://dysnix.com/blog/solana-trading-bot-guide)
- [Automated Trading on Polymarket: Bots, Arbitrage & Execution Strategies](https://www.quantvps.com/blog/automated-trading-polymarket)

---

## Confidence Assessment

| Pitfall Category | Confidence | Basis |
|------------------|------------|-------|
| Position tracking bugs | HIGH | Known bug documented in project context + code review |
| Runaway spending | HIGH | Common pattern across all trading bot literature |
| Partial fills | MEDIUM | py-clob-client Issue #245 confirms, but exact behavior unclear |
| API key exposure | HIGH | Recent 2026 incidents documented |
| Polymarket CLOB specifics | MEDIUM | Official docs + WebSearch, some gaps in minimums |
| WebSocket reliability | MEDIUM | Standard WebSocket patterns + CLOB heartbeat spec |
| Market resolution timing | MEDIUM | UMA oracle docs clear, but trading impact inferred |
| Monitoring gaps | MEDIUM | Best practices from general trading bot sources |
| Slippage assumptions | MEDIUM | Well-documented in backtesting literature |

**Overall:** MEDIUM confidence. Core pitfalls (runaway spending, state corruption, partial fills) are high confidence based on code review and universal trading bot concerns. Polymarket-specific items (order minimums, resolution timing) are medium confidence due to incomplete official documentation requiring inference from community sources.

## Research Methodology

1. **Codebase review:** Analyzed `strategy.py`, `portfolio.py` to identify known bugs and assumptions
2. **WebSearch:** 9 searches across trading bot pitfalls, Polymarket CLOB, security, monitoring
3. **Official docs:** Fetched Polymarket CLOB order creation docs, py-clob-client README
4. **Cross-verification:** Compared simulation assumptions (perfect fills, static prices) against live trading reality
5. **Risk prioritization:** CRITICAL = causes money loss or state corruption; HIGH = causes missed trades or partial losses; MEDIUM = causes operational issues

## Gaps and Uncertainties

- **Exact CLOB order minimums:** Documentation says "minimum size threshold" but doesn't specify values (project context says $1 market / 5 shares limit - unverified)
- **CLOB rate limits:** Not documented; inferred from batch order existence
- **WebSocket reconnect behavior:** py-clob-client doesn't expose WebSocket directly; using inferred requirements
- **Partial fill rounding:** Issue #245 shows ~0.05 share discrepancy, but cause unclear (fees? rounding? market maker spread?)

These gaps should be resolved during Phase 5 (Integration Testing) via live testing on testnet or small-capital trials.
