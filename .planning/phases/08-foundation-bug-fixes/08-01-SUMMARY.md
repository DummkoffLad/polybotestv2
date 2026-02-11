---
phase: 08-foundation-bug-fixes
plan: 01
subsystem: portfolio
status: complete
type: tdd
tags: [bugfix, portfolio, testing, tdd]

requires:
  - v1.1 profit_taker strategy with conviction filtering

provides:
  - Portfolio with composite (token_id, market_id, side) keying
  - Independent position tracking for same token across markets/sides
  - Blocking bug fix for live trading deployment

affects:
  - Phase 12: Live Dry-Run (no longer blocked by this bug)
  - Any future multi-market trading strategies

tech-stack:
  added: []
  patterns: [TDD red-green-refactor cycle]

key-files:
  created: []
  modified:
    - src/core/portfolio.py
    - tests/unit/test_portfolio.py

decisions:
  - id: composite-key-format
    choice: "token_id|market_id|side"
    rationale: "Simple string format, easy to debug, compatible with dict keys"
  - id: has-position-signature
    choice: "has_position(token_id, market_id, side)"
    rationale: "Zero external callers found, safe to change signature"

metrics:
  duration: 2min
  completed: 2026-02-11
  commits: 2
---

# Phase 08 Plan 01: Portfolio Composite Position Keying Summary

**One-liner:** Fixed critical bug where Portfolio positions keyed only by token_id instead of (token_id, market_id, side), corrupting state when same token appears in multiple markets or sides.

## What Was Built

### Problem Statement
Portfolio._positions was keyed only by `token_id`, causing same token in different markets or on different sides to incorrectly accumulate into a single position. This is a **blocking bug for live deployment** - would corrupt portfolio state in real trading.

### Solution Implemented
Implemented composite keying using TDD (red-green-refactor):

**RED Phase:**
- Updated existing `test_multiple_markets_same_token_id` to assert correct behavior (2 independent positions)
- Added `test_same_token_different_sides_independent` - verify UP/DOWN sides are independent
- Added `test_has_position_composite_key` - verify has_position checks all 3 components
- Added `test_get_positions_composite_keys` - verify returned dict uses composite keys
- All tests failed, proving bug exists

**GREEN Phase:**
- Added `_position_key(token_id, market_id, side)` static method returning "token_id|market_id|side"
- Updated `get()` to use composite key for dict lookup and position creation
- Updated `has_position()` signature from `(token_id)` to `(token_id, market_id, side)`
- Updated `get_positions()` to return composite-keyed dict
- Updated `calculate_pnl()` to iterate position values (not keys)
- Updated `to_dict()` to use composite keys
- All 28 tests passed

**REFACTOR Phase:**
- No changes needed - code already clean

### Key Features
1. **Composite keying:** Positions keyed by `(token_id, market_id, side)` tuple
2. **Backward compatible:** All callers already passed these 3 params
3. **Zero external callers:** `has_position` had no external callers, safe signature change
4. **Full test coverage:** 4 new tests + all 24 existing tests pass

## Technical Implementation

### Changes Made

**src/core/portfolio.py:**
- Added `_position_key()` static method to generate composite key string
- Changed `_positions` dict to use composite keys instead of bare token_id
- Updated all methods that interact with `_positions` dict

**tests/unit/test_portfolio.py:**
- Updated 1 existing test to expect correct behavior
- Added 3 new tests for composite key scenarios

### Architecture Decisions

**Key format: "token_id|market_id|side"**
- Simple delimiter-based string
- Easy to debug (readable in logs/debugger)
- Compatible with Python dict keys
- Alternative considered: tuple keys (rejected: harder to serialize)

**has_position signature change:**
- Changed from `(token_id)` to `(token_id, market_id, side)`
- Safe because grep confirmed zero external callers
- Only defined in portfolio.py and tested in test_portfolio.py

## Verification Results

```bash
python -m pytest tests/unit/test_portfolio.py -v
============================= test session starts =============================
collected 28 items

tests/unit/test_portfolio.py::test_single_buy_cost_basis PASSED          [  3%]
tests/unit/test_portfolio.py::test_multiple_buys_cost_basis PASSED       [  7%]
tests/unit/test_portfolio.py::test_buy_different_tokens_independent PASSED [ 10%]
[... 22 more tests ...]
tests/unit/test_portfolio.py::test_multiple_markets_same_token_id PASSED [ 89%]
tests/unit/test_portfolio.py::test_same_token_different_sides_independent PASSED [ 92%]
tests/unit/test_portfolio.py::test_has_position_composite_key PASSED     [ 96%]
tests/unit/test_portfolio.py::test_get_positions_composite_keys PASSED   [100%]

============================= 28 passed in 0.04s
```

**All tests pass:**
- 24 existing tests (backward compatibility verified)
- 4 new composite key tests (new functionality verified)

**Test coverage:**
- Same token in different markets → independent positions ✓
- Same token with different sides → independent positions ✓
- has_position checks all 3 components ✓
- get_positions returns composite-keyed dict ✓

## Decisions Made

| Decision | Choice | Rationale | Alternatives Considered |
|----------|--------|-----------|------------------------|
| Composite key format | "token_id\|market_id\|side" string | Simple, readable, dict-compatible | Tuple keys (harder to serialize) |
| has_position signature | Changed to (token_id, market_id, side) | Zero external callers found | Add new method (unnecessary complexity) |
| TDD approach | Full red-green-refactor cycle | Critical bug, needed confidence | Direct fix (riskier for core component) |

## Next Phase Readiness

**Blockers removed:**
- ✓ Portfolio position keying bug FIXED
- ✓ Live deployment no longer blocked by this issue

**Dependencies satisfied:**
- Phase 12 (Live Dry-Run) can now proceed safely

**No new blockers introduced.**

## Links & References

**Related files:**
- `src/core/portfolio.py` - Core implementation
- `tests/unit/test_portfolio.py` - Full test coverage
- `src/strategies/*/strategy.py` - Strategy callers (no changes needed)
- `src/framework/replay/replayer.py` - Replay framework (no changes needed)

**Phase context:**
- Phase 08: Foundation & Bug Fixes
- Wave 1: Critical bug fixes before live deployment
- This was the highest priority bug blocking live trading

## Deviations from Plan

None - plan executed exactly as written.

The plan correctly identified:
1. Zero external callers of has_position (verified via grep)
2. All callers already pass (token_id, market_id, side) to Portfolio methods
3. Change is internal to Portfolio class
4. Full TDD cycle ensures correctness

## Statistics

**Commits:** 2 (RED + GREEN, no REFACTOR needed)
- `2341f92` test(08-01): add failing tests for composite position keying
- `169f8ee` feat(08-01): implement composite position keying

**Files modified:** 2
- `src/core/portfolio.py` (20 insertions, 12 deletions)
- `tests/unit/test_portfolio.py` (68 insertions, 15 deletions)

**Tests:**
- Before: 24 tests passing (1 documented bug)
- After: 28 tests passing (all correct behavior)

**Duration:** 2 minutes (TDD cycle from RED to GREEN)

**Lines of code:** +88 (mostly test coverage)
