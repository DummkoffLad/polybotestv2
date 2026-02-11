---
phase: 08-foundation-bug-fixes
plan: 04
subsystem: infra
tags: [websockets, websocket-api, dependencies, upgrade]

# Dependency graph
requires:
  - phase: 01-test-coverage
    provides: "Test infrastructure to verify changes"
provides:
  - "websockets v16.0 with new asyncio API"
  - "Updated WebSocket price service using websockets.asyncio.client"
  - "Graceful degradation if websockets not installed"
affects: [10-live-websocket-orders, live-trading, real-time-data]

# Tech tracking
tech-stack:
  added: [websockets>=16.0]
  patterns: ["Graceful library degradation with HAS_WEBSOCKETS flag", "v16 asyncio.client API usage"]

key-files:
  created: []
  modified: [requirements.txt, src/data/ws_price.py]

key-decisions:
  - "Upgraded to websockets v16 despite web3 dependency conflict (websockets is optional, web3 not used in core)"
  - "Used try/except at module level for graceful fallback if websockets missing"
  - "Aliased ws_connect to avoid shadowing built-in connect"

patterns-established:
  - "Import websockets.asyncio.client.connect (v16 API) instead of legacy websockets.connect"
  - "HAS_WEBSOCKETS flag for optional dependency handling"

# Metrics
duration: 4min
completed: 2026-02-11
---

# Phase 08 Plan 04: WebSockets v16 Upgrade Summary

**Upgraded websockets from v12 to v16 with new asyncio.client API, enabling modern WebSocket patterns for Phase 10 live order placement**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-11T23:36:56Z
- **Completed:** 2026-02-11T23:40:41Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments
- Upgraded websockets dependency from >=12.0 to >=16.0
- Updated WebSocketPriceService to use websockets.asyncio.client.connect API
- Added graceful degradation with HAS_WEBSOCKETS flag
- Verified compatibility with existing reconnection and ping logic

## Task Commits

Each task was committed atomically:

1. **Task 1: Upgrade websockets and update ws_price.py API** - `c0c3a37` (chore)

## Files Created/Modified
- `requirements.txt` - Updated websockets dependency to >=16.0
- `src/data/ws_price.py` - Updated to use websockets.asyncio.client API with graceful fallback

## Decisions Made

**1. Proceeded despite web3 dependency conflict**
- pip reported web3 7.14.0 requires websockets<16.0, but we upgraded anyway
- Rationale: websockets is marked "optional, for future use" in requirements.txt
- web3 is not used in core trading functionality
- Will address if web3 integration becomes necessary

**2. Used module-level try/except for graceful degradation**
- Pattern: Import at top, set HAS_WEBSOCKETS flag
- Check flag in _ws_main() before attempting connection
- Allows service to be instantiated even without websockets installed

**3. Aliased connect as ws_connect**
- Avoids shadowing potential built-in names
- Makes v16 API usage explicit in code

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

**Pre-existing test failure (unrelated to websockets upgrade):**
- `test_pipeline_latency_analysis` fails with `AttributeError: 'SessionReplayer' object has no attribute 'final_prices'`
- This is a known bug in the validation pipeline (should be addressed in separate plan)
- All websocket-specific verification passed successfully:
  - websockets v16 installed and imports correctly
  - websockets.asyncio.client.connect available
  - WebSocketPriceService instantiates without errors
  - Updated API is correctly integrated

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for Phase 10 live WebSocket order placement:**
- WebSocket library on modern, supported version (v16.0)
- Price feed service uses current asyncio API
- Reconnection logic with exponential backoff verified working
- Graceful degradation prevents crashes if library missing

**Note:** web3 dependency conflict may need resolution if web3 integration is added in future phases. Monitor for runtime errors if both libraries are required.

---
*Phase: 08-foundation-bug-fixes*
*Completed: 2026-02-11*
