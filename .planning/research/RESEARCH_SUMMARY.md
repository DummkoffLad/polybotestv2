# Project Research Summary

**Project:** Polymarket Copy Trading Bot - v1.2 Production Ready
**Domain:** Live trading bot with real-time WebSocket execution
**Researched:** 2026-02-10
**Confidence:** HIGH

## Executive Summary

This is a transition from battle-tested simulation (Sharpe 0.345, $324 PnL) to live trading on Polymarket. The existing stack (py-clob-client, websockets, event-driven architecture) is production-ready with minimal additions. **No new libraries needed for core functionality.** The focus must be on integration patterns, safety mechanisms, and state management rather than adding dependencies.

The recommended approach is safety-first with conservative incremental deployment: fix critical bugs (portfolio position keying), add pre-flight checks, implement reconciliation, then gradually arm live execution with small capital. The existing strategy code is strong and should remain unchanged—all modifications happen in execution and infrastructure layers.

The three most dangerous risks are: (1) runaway spending from uncapped live orders, (2) position tracking corruption from single-key portfolio lookups, and (3) partial fill reconciliation gaps. All three share a root cause: simulation hides real-world messiness. Mitigation requires hard budget enforcement at the API layer, composite position keys (token_id + market_id + side), and continuous reconciliation between local state and exchange positions.

## Key Findings

### Recommended Stack

The existing stack is sufficient with one upgrade and three new integration patterns. **Core recommendation: Use what you have, focus on integration.**

**Core technologies:**
- **py-clob-client 0.34.5** (latest) — Official Polymarket client, already working, supports FOK market orders — keep as-is
- **websockets 12.0 → 16.0** (upgrade) — Industry-standard asyncio WebSocket library, already proven in codebase for price feeds — upgrade for bug fixes, extend for order channel
- **httpx 0.27+** (keep) — HTTP/2 client for REST APIs — no changes needed
- **Built-in logging** (keep) — Python logging module is sufficient for current scale — defer structured logging to post-v1.2

**What NOT to add:**
- websocket-client (inferior to existing websockets)
- CCXT (doesn't support Polymarket)
- Database (JSONL is sufficient)
- Message queue (in-process is fine)
- Structured logging libraries (built-in logging works)
- Monitoring services (file logs adequate for single-bot)

**New integration patterns (no new libraries):**
1. WebSocket user channel for order status updates (use existing websockets library)
2. Order state machine for lifecycle tracking (pure Python)
3. Environment validation with preflight checks (pure Python)

See STACK.md for detailed rationale and Polymarket-specific constraints.

### Expected Features

**Must have (table stakes):**
- **Market orders via FOK (Fill-Or-Kill)** — immediate execution, no partial fills, matches copy trading speed requirements
- **Explicit arming with pre-flight checks** — validate credentials, connectivity, balance before allowing live trading
- **Kill switch (manual disarm)** — emergency brake for unexpected behavior
- **Per-hour budget cap enforcement** — hard limit at execution layer, not just strategy awareness
- **Position reconciliation on every poll** — catch execution drift early (failed orders we think succeeded, fills we missed)
- **Order status tracking** — verify FOK orders are FILLED or REJECTED before continuing
- **Secure credential management** — private key in environment variables, never hardcoded
- **Structured order logs** — JSON audit trail per order (timestamp, token_id, side, amount, price, outcome, order_id)

**Should have (competitive advantage):**
- **Retry logic for transient failures** — network errors get 2-3 retries with exponential backoff, API rejections logged and skipped
- **Daily loss limit with auto-shutdown** — cap worst-case daily loss (e.g., $100 total)
- **Connection health monitoring** — periodic health checks, auto-disarm after 3 consecutive failures

**Defer (v2+):**
- WebSocket price feeds (polling works fine at current scale)
- Parallel order submission (rarely needed with 1-2 trades/hour)
- Dynamic spread-based pricing (fixed buffer sufficient)
- Real-time dashboard or UI (CLI logging adequate)
- External alerting services (manual log review sufficient for now)

**Anti-features (deliberately skip for $50/hr bot):**
- Limit order management with cancellations/updates (FOK market orders are sufficient)
- Position averaging or re-entry after partial fills (adds complexity with no benefit)
- Order book depth modeling (thin liquidity, $1-5 orders don't need modeling)
- Smart order routing (single-venue)
- Post-only orders for maker rebates (copy trading requires speed, not fee optimization)

See FEATURES.md for detailed implementation guidance and testing strategy.

### Architecture Approach

The existing event-driven framework (Runner → Strategy → Adapter) is production-ready and correctly structured. **Key insight: Strategy code is identical in simulation and live execution. The adapter pattern provides the clean separation.**

**Critical bug to fix before live trading:**
Portfolio positions keyed only by `token_id` (string) instead of composite key `(token_id, market_id, side)` (tuple). This causes position corruption when the same token appears in multiple markets or both sides of a market. Fix location: `src/core/portfolio.py` lines 33-41.

**New components to add:**

1. **OrderLifecycleManager** (`src/execution/order_lifecycle.py`) — Track order states (pending → filled/rejected), handle fills from WebSocket or API responses, cleanup stale orders

2. **WebSocketOrderChannel** (`src/data/ws_order.py`) — Subscribe to user-specific order fill events, similar pattern to existing WebSocketPriceService, provides real-time fill notifications

3. **PositionReconciler** (enhance existing `UniversalRunner._maybe_reconcile()`) — Compare local portfolio tracking with exchange positions, store reconciliation history, alert on persistent discrepancies

4. **Experiment script organization** — Move 60+ scripts from root to `experiments/archive/`, `scripts/`, separate research from production code

**Modified components:**

- **UniversalRunner** — Integrate OrderLifecycleManager and WebSocketOrderChannel into startup/shutdown, enhance reconciliation
- **LiveExecutionAdapter** — Return order_id for tracking, add error handling for known API errors, implement retry logic
- **Strategy.on_fill()** — Enhance with OrderFill object containing actual fill price/shares (not just requested amounts)

**Architectural patterns to preserve:**
- Single Responsibility (Strategy decides WHAT, Adapter decides HOW, Portfolio decides WHAT we own)
- Dependency Injection (pass components to Runner, don't hardcode)
- Event Sourcing (SessionRecorder/Replayer already correct, maintain this)
- Async-aware but sync-default (FOK orders are synchronous, add async path for GTC later)

See ARCHITECTURE.md for detailed data flows, build order sequencing, and anti-patterns to avoid.

### Critical Pitfalls

**Top 5 most dangerous pitfalls from simulation to live transition:**

1. **Runaway Spending from Uncapped Live Orders** — Hourly budget exists in strategy code but never enforced at wallet/API layer. One bug can drain wallet in minutes. Prevention: Pre-flight wallet check, hard wallet limit (refuse if balance < hourly_budget * 3), order-level maxCost parameter, kill switch on MAX_TOTAL_SPEND environment variable, hourly budget reconciliation from actual filled orders.

2. **Position Tracking State Corruption** — Portfolio keyed by token_id alone means duplicate tokens across markets corrupt position data. Already a known bug. Prevention: Fix position key to (token_id, market_id, side), add unique position_id, reconciliation on startup, invariant checks before every trade.

3. **Partial Fill Reconciliation Failure** — Simulation assumes perfect fills. Live CLOB orders can partially fill. Code submits "buy 10 shares" but only 6 fill, portfolio adds 10, mismatch corrupts state. Prevention: Post-order verification polling order status, use size_matched from response, reconciliation loop every 5 minutes, track fills by order_id to prevent double-counting.

4. **Stale Price Execution** — Simulation uses historical minute bars. Live WebSocket data has latency. Leader trades at $0.65, price moves to $0.72 by time you execute, 10% slippage destroys profit. Prevention: Orderbook price validation before submission, slippage tolerance check (MAX_SLIPPAGE_PCT = 3%), limit orders with timeout instead of market orders, price staleness check (reject if last update > 5s old).

5. **API Key Exposure and Theft** — Private key in .env file accidentally committed to git, or malicious extension steals keys. Polymarket private key has full custody, no API permission scoping. Prevention: Never commit keys (.gitignore verification), separate bot wallet with only $500 funded, IP whitelisting if supported, weekly key rotation, wallet balance alerts.

**Other high-severity pitfalls:**
- WebSocket disconnection without auto-reconnect (miss entire hour of trades)
- Hourly reset race condition (trades near hour boundary processed incorrectly)
- Market resolution timing (trading on resolved markets with minimal upside)
- Insufficient position monitoring (silent losses overnight with no alerts)

See PITFALLS.md for detailed warning signs, prevention strategies, and code locations for all 18 identified pitfalls.

## Implications for Roadmap

Based on research, the critical path is: fix bugs → add safety → integrate order lifecycle → enable live with monitoring. Codebase cleanup can run in parallel.

### Phase 1: Foundation & Bug Fixes
**Rationale:** Must fix known bugs and organize codebase before building on top of flawed foundation. Zero impact on existing functionality but prevents future pain.

**Delivers:** Clean workspace, correct portfolio tracking, test coverage for critical paths

**Addresses:**
- Fix portfolio composite keying bug (PITFALLS #2 - state corruption)
- Organize experiment scripts into `experiments/archive/` and `scripts/` (ARCHITECTURE - modularity)
- Add unit tests for new portfolio keying
- Create shared trade logic module (`src/core/trade_logic.py`) to prevent simulation/live drift

**Avoids:** Building live trading on top of position tracking bug that will corrupt state

**Research flag:** No research needed - code fixes and refactoring with well-known patterns

### Phase 2: Safety Mechanisms & Environment Hardening
**Rationale:** Fail-fast validation prevents runtime errors. Must be bulletproof before capital deployment.

**Delivers:** Pre-flight checks, budget enforcement, kill switch, credential validation

**Addresses:**
- Pre-flight checks module with credential validation, connectivity testing, balance verification (FEATURES - table stakes, PITFALLS #5 - API key exposure)
- Hard budget cap enforcement at execution layer (PITFALLS #1 - runaway spending)
- Kill switch CLI command for manual disarm (FEATURES - table stakes)
- Environment variable hygiene and .gitignore verification (PITFALLS #5)
- Persistent state management for crash recovery (PITFALLS #13)
- Signal handlers for graceful shutdown (PITFALLS #18)

**Avoids:** Accidental live trading, credential leaks, runaway spending, orphaned orders

**Research flag:** No research needed - standard safety patterns for trading bots

### Phase 3: Order Lifecycle & WebSocket Integration
**Rationale:** Core feature for live execution. Testable in isolation before touching critical path.

**Delivers:** Order state tracking, real-time fill notifications, WebSocket user channel

**Addresses:**
- OrderLifecycleManager for tracking pending → filled/rejected (ARCHITECTURE - new component)
- WebSocketOrderChannel subscribing to user-specific fills (STACK - WebSocket user channel)
- Integration with LiveExecutionAdapter returning order_id (ARCHITECTURE - modified component)
- Retry logic with exponential backoff for transient failures (FEATURES - competitive advantage)
- Order validation (minimum sizes, price ranges, rate limiting) (STACK - Polymarket constraints)
- Error handling for known API errors (STACK - error handling matrix)

**Avoids:** Partial fill reconciliation failures (PITFALLS #3), stale price execution (PITFALLS #4), order minimum violations (PITFALLS #12)

**Research flag:** Medium - WebSocket user channel auth flow not fully detailed in docs, needs testing during implementation

### Phase 4: Position Reconciliation & Monitoring
**Rationale:** Safety net to catch execution drift. Depends on portfolio keying fix and order lifecycle.

**Delivers:** Continuous reconciliation, health monitoring, alerting on discrepancies

**Addresses:**
- Enhance PositionReconciler with composite key support (ARCHITECTURE - modified component)
- Reconciliation every 5 minutes comparing local vs exchange positions (FEATURES - table stakes)
- Connection health monitoring with auto-disarm after failures (FEATURES - competitive advantage)
- Reconciliation audit trail logged to disk (FEATURES - observability)
- Trading halt on critical discrepancies (PITFALLS #9 - insufficient monitoring)

**Avoids:** Silent position drift, missed fills, WebSocket disconnection without detection (PITFALLS #6)

**Research flag:** Low - standard reconciliation patterns, CLOB API for positions query well-documented

### Phase 5: Live Order Placement & Integration Testing
**Rationale:** Real money at stake. Needs all previous phases working. Gradual rollout with small capital.

**Delivers:** Enabled live execution with comprehensive testing protocol

**Addresses:**
- Enable live execution in LiveExecutionAdapter (already exists, needs integration with new components)
- Runner integration with OrderLifecycleManager and WebSocketOrderChannel (ARCHITECTURE - modified component)
- Enhance Strategy.on_fill() with actual fill prices (ARCHITECTURE - modified component)
- Dry-run mode validation (log would-be orders without executing)
- Small capital trial ($10 budget) to verify reconciliation works
- Integration tests with hour boundary trades (PITFALLS #7 - hourly reset race condition)
- Slippage tracking and alerting (PITFALLS #4 - stale prices)

**Avoids:** All critical pitfalls via comprehensive pre-deployment testing

**Research flag:** High - Real money testing uncovers issues simulation can't. Plan for iteration.

### Phase 6: Codebase Cleanup (Parallel/Deferred)
**Rationale:** Refactoring with no behavior change. Can run alongside other phases or be deferred entirely.

**Delivers:** Cleaner code structure, reduced duplication, better maintainability

**Addresses:**
- Directory restructure (move experiment scripts - already done in Phase 1, this is docs update)
- Shared trade logic module (already done in Phase 1, this is integration)
- Documentation updates with script inventory (STACK - codebase modularity)
- Upgrade websockets 12.0 → 16.0 (STACK - recommended upgrade)

**Avoids:** No pitfalls addressed - pure quality-of-life improvement

**Research flag:** No research needed - refactoring and documentation

### Phase Ordering Rationale

**Sequential dependencies (Phases 1-5 must be in order):**
- Phase 1 fixes bugs that would corrupt Phase 4 reconciliation
- Phase 2 safety prevents disasters during Phase 5 testing
- Phase 3 order lifecycle is foundation for Phase 4 reconciliation
- Phase 5 live trading requires all previous phases working

**Parallel opportunity:**
- Phase 6 can run alongside Phases 2-4 or be deferred to post-v1.2

**Risk-based sequencing:**
- Phases 1-2 are LOW RISK (no live trading, no behavior change)
- Phase 3 is MEDIUM RISK (new components, testable in isolation)
- Phase 4 is MEDIUM RISK (non-critical safety net)
- Phase 5 is HIGH RISK (real money, needs all previous phases)

**Critical path for live deployment:** Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5

### Research Flags

**Phases needing deeper research during planning:**
- **Phase 3:** WebSocket user channel authentication flow not fully detailed in Polymarket docs. Needs testing with live API in dry-run mode to validate subscription, auth, and message parsing.
- **Phase 5:** Live testing will uncover edge cases simulation can't (network errors, API rate limits, price staleness). Plan for multiple iterations and small capital trials.

**Phases with standard patterns (skip research-phase):**
- **Phase 1:** Code refactoring and bug fixes with well-known Python patterns
- **Phase 2:** Safety mechanisms are standard trading bot best practices
- **Phase 4:** Reconciliation is standard pattern, CLOB API well-documented
- **Phase 6:** Documentation and code cleanup, no research needed

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | py-clob-client verified working in codebase, websockets proven for price feeds, official Polymarket docs clear on capabilities |
| Features | HIGH | FOK market orders well-documented, table stakes derived from simulation-to-live literature, anti-features based on scale analysis ($50/hr doesn't need HFT features) |
| Architecture | HIGH | Existing codebase analysis shows correct patterns (adapter, event sourcing), new components follow proven trading bot architectures, portfolio keying bug confirmed via code review |
| Pitfalls | MEDIUM | Critical pitfalls (runaway spending, state corruption, partial fills) are high confidence from code review and universal trading bot concerns. Polymarket-specific items (order minimums, resolution timing) medium confidence due to incomplete official docs. |

**Overall confidence:** HIGH

Research is comprehensive enough to proceed with roadmap creation. Gaps are minor and will be resolved during implementation and testing (Phase 3 WebSocket auth, Phase 5 live testing edge cases).

### Gaps to Address

**During Phase 3 implementation:**
- Exact WebSocket user channel authentication flow (documented but not fully detailed) — resolve via test connection in dry-run mode
- CLOB order minimum sizes (inferred as $1 market / 5 shares limit, not explicitly documented) — verify during first order submission

**During Phase 5 testing:**
- Actual slippage on live CLOB vs simulation assumptions — measure and adjust MAX_SLIPPAGE_PCT threshold
- Real-world partial fill behavior (Issue #245 shows 0.05 share rounding discrepancies) — handle via reconciliation rather than preventing
- API rate limits under burst conditions (not explicitly documented) — add rate limiter if rejections observed

**Post-v1.2 decisions:**
- WebSocket price feeds vs polling (current polling works fine, upgrade if latency becomes issue)
- GTC limit orders vs FOK market orders (FOK sufficient, add GTC only if execution quality degrades)
- Daily loss limits threshold ($100 suggested, adjust based on actual live performance)

All gaps are addressable during implementation with live testing. No blocking uncertainties prevent roadmap creation.

## Sources

### Primary (HIGH confidence)
- [Polymarket CLOB Official Documentation](https://docs.polymarket.com/developers/CLOB/introduction) — order types, WebSocket endpoints, rate limits
- [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client) — official Python SDK, order management methods, GitHub issues for error scenarios
- Codebase analysis — `src/core/portfolio.py`, `src/strategies/profit_taker/strategy.py`, `src/execution/live.py`, `src/framework/runner.py` — verified existing architecture and identified bugs

### Secondary (MEDIUM confidence)
- [Crypto Trading Bot Development Guide 2026](https://appinventiv.com/blog/crypto-trading-bot-development/) — safety mechanisms, testing protocols
- [Trading Bot Monitoring Best Practices](https://tickerly.net/trading-bot-crypto-complete-guide-2026/) — alerting thresholds, health checks
- [Backtesting vs Live Trading: Slippage and Liquidity](https://www.luxalgo.com/blog/backtesting-limitations-slippage-and-liquidity-explained/) — simulation-to-live pitfalls
- [websockets PyPI](https://pypi.org/project/websockets/) — library capabilities and asyncio patterns

### Tertiary (LOW confidence, needs validation)
- Community inferences on CLOB order minimums ($1 market, 5 shares limit) — not explicitly documented, verify during testing
- Polymarket API rate limits — inferred from batch order existence, not documented
- WebSocket reconnect behavior specifics — general WebSocket patterns applied to Polymarket context

---
*Research completed: 2026-02-10*
*Ready for roadmap: yes*
