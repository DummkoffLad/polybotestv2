# Codebase Concerns

**Analysis Date:** 2026-01-30

## Tech Debt

**Bare Exception Handlers (Silent Failures):**
- Issue: Multiple bare `except:` blocks that silently catch and ignore all exceptions without logging
- Files:
  - `src/framework/runner.py` (lines 237, 338)
  - `src/framework/replay.py` (lines 123, 135-136, 147-148)
  - `src/simulation/optimizer_old.py` (line 596)
- Impact: Errors are hidden from operators; debugging is extremely difficult when code fails silently. Network errors, data corruption, or logic errors disappear without trace.
- Fix approach: Replace all bare `except:` blocks with `except Exception as e: logger.error(...)` with context about what operation failed. Preserve exception information in logs.

**Broken Budget Logic in Optimizer (Legacy Code):**
- Issue: `src/simulation/optimizer_old.py` contains intentionally broken budget tracking logic (lines 229-241) designed to match old session behavior
- Files: `src/simulation/optimizer_old.py` (lines 226-268, 428-440)
- Impact: Creates confusion about what the "correct" algorithm is. New strategies may inherit broken assumptions. Current code has two conflicting implementations of capital tracking.
- Fix approach: Remove `use_broken_budget` parameter entirely. Keep only fixed logic. Create regression tests that explicitly verify the correct behavior differs from old sessions.

**Incomplete Strategy Integration (TODO):**
- Issue: `src/simulation/limit_order_sim.py` line 358 has TODO to integrate with SessionReplayer but integration is missing
- Files: `src/simulation/limit_order_sim.py` (line 358)
- Impact: Limit order execution mode exists but cannot be used in replay workflow. Code is partially implemented and untestable.
- Fix approach: Complete the integration with SessionReplayer or remove limit order simulation entirely if not needed.

**Unconfirmed API Endpoints:**
- Issue: Multiple TODOs in `config/config.example.yaml` (lines 307-314) indicate Polymarket API endpoints are not confirmed
- Files: `config/config.example.yaml`
- Impact: Live mode will fail or use wrong endpoints. Critical pre-live confirmation is missing.
- Fix approach: Before any live trading, confirm exact endpoint URLs with Polymarket API documentation. Update config and create validation checks.

## Known Bugs

**Bare Except Silencing Price Updates:**
- Symptom: Price updates fail silently in runner when websocket or price service has issues
- Files: `src/framework/runner.py` (line 237: `except: pass`)
- Trigger: Occurs when `price_service.get_prices()` throws any exception during blockchain event processing
- Workaround: None - prices simply become unavailable until next event
- Fix: Log the exception with context: `except Exception as e: logger.warning(f"Price fetch failed for {bt.token_id}: {e}")`

**Silent JSON Parse Failures in Replay:**
- Symptom: Session replay skips events without reporting when JSON parsing fails
- Files: `src/framework/replay.py` (lines 122-124)
- Trigger: Malformed JSON in session file (extra commas, missing quotes, etc.)
- Workaround: None - corrupted events are silently dropped
- Fix: Log skipped events: `except json.JSONDecodeError as e: logger.debug(f"Skipped line {line_num}: {e}")`

**Preflight Configuration Not Enforced:**
- Symptom: `LiveExecutionAdapter.arm()` returns False but doesn't distinguish between missing credentials vs preflight failure
- Files: `src/execution/live.py` (lines 61-62)
- Trigger: When `_preflight_passed` is False, unclear if it failed validation or was never checked
- Workaround: Always call preflight before attempting to arm
- Fix: Throw explicit exception with reason instead of returning False

## Security Considerations

**Private Key Exposure in Logs:**
- Risk: If exception is raised during order signing, full exception trace might be logged with private key details
- Files: `src/execution/live.py` (line 152: generic exception logging)
- Impact: Private key could leak into log files if SDK exception includes key material
- Current mitigation: Exception handling catches but doesn't inspect payload
- Recommendations:
  - Sanitize exception messages before logging (remove sensitive fields)
  - Use try-except to isolate key-sensitive operations
  - Review py-clob-client exception format to know what could leak

**Environment Variable Configuration:**
- Risk: Private key is loaded from `.env` but if server crashes, `.env` might be included in memory dumps
- Files: `src/execution/live.py` (lines 35-37)
- Impact: Private key exposure if system is compromised or if debug tools are available
- Current mitigation: Environment variables are loaded once at startup
- Recommendations:
  - Consider using encrypted key storage or key management service for production
  - Ensure `.env` has restricted file permissions (chmod 600)
  - Add documentation warning about .env security

**Config File Contains Placeholder Credentials:**
- Risk: `config/config.example.yaml` has TODO comments where real addresses should go (lines 19, 34)
- Files: `config/config.example.yaml`
- Impact: Users might accidentally commit actual addresses/keys in config files
- Current mitigation: Example file is meant to be copied and edited
- Recommendations:
  - Make config loader validate that placeholder values are not used
  - Add pre-flight check that refuses to run with TODO addresses
  - Generate random/example addresses in template to prevent copy-paste errors

**Order Validation Insufficient:**
- Risk: `src/execution/live.py` validates that amounts > 0 but doesn't validate upper bounds
- Files: `src/execution/live.py` (lines 104-110)
- Impact: Could accidentally send extremely large orders if scaling calculation breaks
- Current mitigation: Portfolio risk caps and mode separation
- Recommendations:
  - Add sanity checks: reject orders > 2x expected max size
  - Add emergency circuit breaker that refuses orders > total capital
  - Log all order attempts with context for audit trail

## Performance Bottlenecks

**Synchronous HTTP Calls During Live Trading:**
- Problem: All price fetches and position queries use synchronous httpx blocking calls
- Files: `src/data/live_source.py` (lines 43-61)
- Cause: `httpx.Client` is used without async. Each API call blocks event loop during polling
- Improvement path:
  - Switch to `httpx.AsyncClient` for non-blocking IO
  - Allow parallel requests for positions + prices
  - Add connection pooling and request batching

**Portfolio State Recalculation on Every Fill:**
- Problem: Portfolio exposure calculations (get_total_deployed, get_market_exposure) iterate all positions repeatedly
- Files: `src/core/portfolio.py`, called from `src/simulation/optimizer_old.py` (lines 271-288)
- Cause: No caching of aggregate values; every property getter recalculates from scratch
- Improvement path:
  - Cache total_deployed and invalidate on apply_buy/apply_sell
  - Use incremental updates instead of full recalculation
  - Benchmark: current approach may be O(n) per decision in simulation with large portfolios

**Strategy Optimizer Grid Search Not Parallelized:**
- Problem: `src/simulation/optimizer.py` tests parameter combinations sequentially
- Files: `src/simulation/optimizer.py` (line 23 has ProcessPoolExecutor import but may not use effectively)
- Cause: Grid search over parameter ranges runs single-threaded
- Improvement path:
  - Use process pool for parameter combination testing
  - Implement result caching to avoid re-testing identical configs
  - Add early stopping for unpromising parameter regions

## Fragile Areas

**SessionReplayer Event Parsing Is Fragile:**
- Files: `src/framework/replay.py` (lines 107-175)
- Why fragile: Handles three different log formats (session_start, market_event, log messages) with lenient parsing. Format changes break silently.
- Safe modification:
  - Add format version to session files to detect format changes
  - Create explicit parsers for each format instead of type checking
  - Test that all three formats are read correctly
- Test coverage: Only one integration test (`tests/integration/test_dry_run_smoke.py`) that depends on specific session file

**Portfolio Invariant Enforcement Incomplete:**
- Files: `src/core/portfolio.py` (lines 12-99)
- Why fragile: Checks for negative shares but doesn't validate that sum of positions matches total_bought - total_sold. Could diverge silently.
- Safe modification:
  - Add periodic audit() method that validates invariants
  - Store hash of positions to detect unexpected mutations
  - Call audit after session end to catch issues
- Test coverage: No tests for portfolio invariant violations

**Live Order Execution Missing Status Polling:**
- Files: `src/execution/live.py` (lines 93-154)
- Why fragile: Orders are placed but status polling is minimal. FOK orders might partially fill without retry logic.
- Safe modification:
  - Add order status polling loop after placement
  - Implement exponential backoff for incomplete fills
  - Create explicit "order pending" state
- Test coverage: No integration test with actual Polymarket API

**Decision Trace Comparison Tool Brittle:**
- Files: `compare_trades.py`
- Why fragile: Compares decision traces but differences could be due to timestamp precision, numeric rounding, or actual logic divergence without clear indication
- Safe modification:
  - Add configurable tolerance for numeric comparisons
  - Report which specific fields differ
  - Create schema validation before comparison
- Test coverage: Tool is standalone utility without tests

## Scaling Limits

**Leader Position Tracking Memory Usage:**
- Current capacity: Caches entire leader position history in-memory dictionary
- Limit: With 100+ concurrent markets and full trade history, memory could grow unbounded
- Scaling path:
  - Implement LRU cache with size limit (e.g., last 1000 trades per market)
  - Periodically prune old positions
  - Add memory usage monitoring

**Session Replay Event Deduplication:**
- Current capacity: Full event list held in memory (`SessionReplayer.events` list)
- Limit: Replaying 100k+ events will consume significant memory; dedup set grows linearly
- Scaling path:
  - Stream events instead of loading all at once
  - Use disk-backed dedup (SQLite) instead of in-memory set
  - Process events in batches

**Price Snapshot Storage:**
- Current capacity: All historical prices stored in `SessionReplayer.final_prices` dict
- Limit: With 50+ markets and hourly markets, snapshot count could exceed 1000s
- Scaling path:
  - Implement circular buffer for price history
  - Archive old prices to file storage
  - Add price history retention policy

## Dependencies at Risk

**py-clob-client (Critical for Live Trading):**
- Risk: SDK is third-party dependency for Polymarket order placement; API changes could break trading
- Impact: Live mode completely non-functional if SDK breaks or Polymarket API changes
- Migration plan:
  - Maintain abstraction layer (ExecutionAdapter) to swap implementations
  - Add fallback to REST API if SDK becomes unavailable
  - Monitor SDK repository for breaking changes

**httpx (HTTP Client):**
- Risk: Used for all API calls; version incompatibility could cause failures
- Impact: Cannot fetch positions, trades, or market data if httpx breaks
- Migration plan:
  - Keep httpx version pinned in requirements.txt
  - Have fallback using standard library urllib if needed
  - Test with new httpx versions before deployment

**PyYAML Configuration Loading:**
- Risk: YAML parser could have security issues or format compatibility changes
- Impact: Configuration cannot be loaded, bot cannot start
- Migration plan:
  - Validate YAML schema after loading
  - Add JSON as alternative config format
  - Use safe_load (already done, line 82 of `src/core/config.py`)

## Missing Critical Features

**Order Cancellation Not Implemented:**
- Problem: No cancel order workflow exists; if an order is placed and immediately market conditions worsen, it cannot be cancelled
- Blocks:
  - Cannot implement stop-loss orders
  - Cannot adjust exposure quickly if correlation changes
  - Risk caps cannot be enforced retroactively
- Files: `src/execution/base.py` has empty cancel_order stub (line 77)
- Recommendation: Implement cancel workflow with FOK (Fill or Kill) for safety, with timeout/retry logic

**Rate Limiting Not Enforced:**
- Problem: No rate limit tracking between API calls; could trigger Polymarket API throttling
- Blocks:
  - Cannot operate at high frequency
  - Requests could fail with 429 responses
- Files: `src/data/live_source.py` has `_rate_limit` method stub (line 45) but is minimal
- Recommendation: Implement proper rate limit queue with exponential backoff

**Websocket Price Feed Implementation Missing:**
- Problem: `config/config.example.yaml` (line 26) has `use_websocket: false` but websocket code is not implemented
- Blocks:
  - Cannot achieve sub-second latency for price updates
  - Polling strategy is inefficient
- Files: `src/framework/runner.py` (line 197) mentions WebSocket init but with bare except
- Recommendation: Complete websocket implementation with reconnection logic

**Circuit Breaker Pattern Not Enforced:**
- Problem: README mentions circuit breakers (line 11) but no implementation visible
- Blocks:
  - Cannot automatically stop trading if errors increase
  - Cannot protect against cascading failures
- Files: No circuit breaker code found in codebase
- Recommendation: Implement circuit breaker that tracks error rate and disarms live mode if threshold exceeded

## Test Coverage Gaps

**No Unit Tests for Portfolio:**
- What's not tested: `src/core/portfolio.py` apply_buy, apply_sell, invariant enforcement
- Files: `src/core/portfolio.py` (entire file)
- Risk: Portfolio bugs (wrong avg_price calculation, negative shares) would go undetected until live trading
- Priority: HIGH - portfolio is core risk control

**No Tests for LiveExecutionAdapter:**
- What's not tested: Order placement, error handling, armed/disarmed state transitions
- Files: `src/execution/live.py` (entire file)
- Risk: Order logic bugs only discovered during live trading; cannot mock Polymarket API safely
- Priority: HIGH - execution is critical for safety

**No Tests for Configuration Loading:**
- What's not tested: Config parsing, validation, missing required fields, invalid values
- Files: `src/core/config.py` (entire file)
- Risk: Invalid configuration silently uses defaults; trader unaware they're not configured correctly
- Priority: MEDIUM - affects many aspects

**No Tests for Preflight Checks:**
- What's not tested: Preflight validation logic, what constitutes pass/fail
- Files: Not visible in codebase - may be in main.py
- Risk: Preflight might pass with invalid credentials, allowing bad LIVE attempts
- Priority: HIGH - safety critical

**No Integration Tests for Strategy Decision Making:**
- What's not tested: Strategy selection logic, edge cases in scaling/capping
- Files: `src/strategies/` implementations
- Risk: Strategy bugs only found after real trading
- Priority: MEDIUM - each strategy should have decision test cases

**Session Replay Only Has Smoke Test:**
- What's not tested: Correct event ordering, timestamp handling, price accuracy
- Files: `tests/integration/test_dry_run_smoke.py` (single integration test)
- Risk: Replay results could be wrong without detection
- Priority: MEDIUM - replay is used for backtesting

**No Error Path Tests:**
- What's not tested: Network failures, malformed API responses, missing fields, timeouts
- Files: All API integration points
- Risk: Error handling code is untested; edge cases could crash bot unexpectedly
- Priority: MEDIUM - resilience requires error testing

## Recording Data & Process Improvements

**Priority: HIGH — Recording quality directly determines backtest/analysis reliability.**

These are questions to answer and improvements to make to the session recording system
(`src/framework/recorder.py`) before trusting recorded data for strategy decisions.

### Questions to Resolve
1. **What additional data should we capture per event?** Current recording misses:
   - Order book depth (not just best bid/ask — how much liquidity at each level?)
   - Volume context (what's the 1h/24h volume for this market at event time?)
   - Market metadata (time until resolution, total liquidity, number of active traders)
   - Other leader activity (are multiple leaders trading the same market simultaneously?)
   - Our portfolio state at decision time (what was our exposure when we made/skipped the trade?)

2. **Is the price snapshot interval (2s) correct?** For momentum detection:
   - 2s may miss fast spikes — should we capture on every price change instead?
   - Should we record both polling snapshots AND event-triggered snapshots?
   - What resolution do we need for the "afternoon spike" pattern analysis?

3. **What market-wide context matters?** For pattern detection:
   - Overall market sentiment indicators (total Polymarket volume, trending markets)
   - Correlated market movements (do YES tokens in related markets move together?)
   - Time-of-day patterns require consistent timezone handling — is UTC enough?

4. **What's missing for accurate replay?**
   - Latency: time between leader trade and our detection isn't recorded
   - Slippage: difference between decision price and fill price (critical for live accuracy)
   - Rejection data: when orders fail, why? (rate limit, insufficient funds, API error)

### Improvements to Implement

**Data Enrichment:**
- Record portfolio state snapshot with each event (total deployed, per-market exposure, available capital)
- Add market metadata fields (resolution date, total volume, liquidity depth)
- Capture detection latency (`leader_trade.timestamp` vs `datetime.now()` delta)
- Record the full decision trace: all factors that led to trade/skip, not just skip_reason string

**Recording Reliability:**
- Line 158: bare `except: pass` in `_record_price_snapshot()` silently drops price failures — should log
- No validation that recorded data can actually be replayed (roundtrip test needed)
- No checksums or event counts to detect truncated/corrupted session files
- File is flushed per-write (good) but no fsync — crash could lose last buffer

**Analysis-Ready Format:**
- Current JSONL is good for streaming but hard to query — consider also writing a SQLite summary
- Add session-level statistics in `session_end` record (total events, markets seen, decisions made)
- Tag events with market category/type for filtering during analysis
- Include config hash so you can track which config produced which results

**Process Improvements:**
- Automate post-session analysis: script that reads session JSONL and produces summary stats
- Build a "data quality report" that flags sessions with missing prices, gaps in snapshots, etc.
- Create a recording validation step that runs after each session to catch issues immediately
- Store sessions with metadata index (date, strategy, markets, PnL) for easy lookup

### Pattern Analysis Preparation
To investigate the "afternoon spike" hypothesis and other market patterns:
- Need consistent recording across multiple full trading days (not just when bot is actively trading)
- Record market-wide price movements, not just markets the leader trades
- Timestamp precision should be milliseconds for latency analysis
- Consider a separate lightweight "market monitor" that records prices for all active markets continuously, independent of the trading bot

---

*Concerns audit: 2026-01-30*
