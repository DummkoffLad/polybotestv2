---
phase: 05-validation
plan: 01
subsystem: validation
tags: [data-split, out-of-sample, data-leakage-prevention, json-persistence, pathlib]

# Dependency graph
requires:
  - phase: 01-test-coverage
    provides: Session data (session_20260130_032713 used for baselines)
  - phase: 02-performance-analysis
    provides: Session replay infrastructure
  - phase: 03-dynamic-sizing
    provides: Quality-filtered strategies
  - phase: 04-advanced-sizing
    provides: Kelly-optimized parameters
provides:
  - DataSplitManager for tracking in-sample vs out-of-sample sessions
  - Data leakage prevention mechanism (critical anti-pattern protection)
  - Session provenance tracking with JSON persistence
  - Session discovery from data/sessions/*.jsonl
affects: [05-02-sensitivity, 05-03-latency-sim, 05-04-validation-report]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Set-based session tracking with O(1) membership testing"
    - "JSON persistence with automatic save on classification changes"
    - "Path-based session discovery using pathlib.glob"
    - "Idempotent operations (marking OOS as OOS is safe)"

key-files:
  created:
    - src/validation/__init__.py
    - src/validation/data_split.py
    - tests/unit/test_data_split.py
  modified: []

key-decisions:
  - "Session IDs stored as strings (filename stem without .jsonl)"
  - "Automatic save on mark_in_sample/mark_out_of_sample for safety"
  - "Raise ValueError on leakage attempts (fail-fast for critical error)"
  - "Out-of-sample sessions can be marked OOS multiple times (idempotent)"
  - "Session discovery via pathlib.glob('session_*.jsonl') pattern"

patterns-established:
  - "validate_no_leakage() must be called before running validation"
  - "In-sample sessions are immutable (once in-sample, always in-sample)"
  - "save()/load() roundtrip preserves exact classification state"
  - "get_validation_sessions() returns Path objects for direct use with SessionReplayer"

# Metrics
duration: 4min
completed: 2026-02-02
---

# Phase 5 Plan 1: Data Split Management Summary

**DataSplitManager with Set-based O(1) tracking prevents data leakage by enforcing immutable in-sample classifications and failing fast on validation contamination attempts**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-02T22:30:25Z
- **Completed:** 2026-02-02T22:34:05Z
- **Tasks:** 1 TDD task (test → feat → refactor commits)
- **Files modified:** 3

## Accomplishments
- Built DataSplitManager with comprehensive data leakage prevention
- Implemented JSON persistence with save/load for reproducible validation splits
- Created session discovery mechanism for identifying new data
- Added 14 unit tests covering all core behaviors and edge cases
- TDD cycle completed: RED (failing tests) → GREEN (implementation) → REFACTOR (exports)

## Task Commits

TDD task executed in three atomic commits:

1. **RED: Failing tests for DataSplitManager** - `bb0e004` (test)
   - 17 tests covering classification, leakage prevention, persistence, discovery
   - Tests fail as expected (module not yet implemented)

2. **GREEN: Implement DataSplitManager** - `1571048` (feat)
   - 206 lines implementing full DataSplitManager class
   - All 14 tests passing
   - Core behaviors: mark_in_sample, mark_out_of_sample, validate_no_leakage, save/load

3. **REFACTOR: Export from validation module** - `9edf4f8` (refactor)
   - Added DataSplitManager to __all__ for clean public API
   - Enables: `from src.validation import DataSplitManager`

**Additional fix commit:** `53a1271` (fix)
- Removed premature sensitivity imports added by auto-save/linter
- Prevents ImportError for components not yet implemented

## Files Created/Modified

### Created
- `src/validation/__init__.py` - Validation module package with DataSplitManager export
- `src/validation/data_split.py` - DataSplitManager class (206 lines)
- `tests/unit/test_data_split.py` - 14 unit tests covering all behaviors

### Modified
- None (all new files)

## Decisions Made

**Session ID format: filename stem without extension**
- Rationale: Clean, deterministic, matches file system structure
- Example: `session_20260130_032713.jsonl` → `"session_20260130_032713"`

**Automatic save on classification changes**
- Rationale: Prevents data loss from crashes, ensures metadata always in sync
- Implementation: mark_in_sample() and mark_out_of_sample() call save() automatically

**Fail-fast on data leakage attempts**
- Rationale: Data leakage is critical error in validation - better to raise than silently corrupt results
- Implementation: mark_out_of_sample() raises ValueError if session already in-sample

**Idempotent out-of-sample marking**
- Rationale: Safe to mark OOS sessions multiple times (e.g., after loading from file)
- Implementation: mark_out_of_sample() only raises for in-sample contamination, not OOS re-marking

**Session discovery via glob pattern**
- Rationale: Automatic discovery of new session files without manual registration
- Implementation: `sessions_dir.glob("session_*.jsonl")` finds all available sessions

## Deviations from Plan

None - plan executed exactly as written. TDD cycle followed precisely:
1. RED: Tests written first and verified to fail
2. GREEN: Implementation made tests pass
3. REFACTOR: Clean up module exports (plus one fix for auto-save issue)

## Issues Encountered

**Auto-save/linter adding premature imports**
- Problem: `src/validation/__init__.py` kept getting modified to import SensitivitySweeper (not yet implemented)
- Resolution: Explicitly wrote file to only export DataSplitManager, committed fix
- Impact: Minor - required one additional commit to stabilize module exports

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for Phase 5 Plan 2 (Parameter Sensitivity Analysis):**
- DataSplitManager provides session classification infrastructure
- Out-of-sample sessions can be discovered via `get_validation_sessions()`
- In-sample session tracking prevents accidental contamination

**Known in-sample session (from Phase 1 context):**
- `session_20260130_032713` - used for Phase 1-4 baselines and optimization

**Available for out-of-sample validation:**
- 5 other sessions in `data/sessions/` directory (currently unclassified)
- New sessions gathered after Phase 4 completion are automatically OOS candidates

**Blockers/concerns:**
- None - all functionality working as specified
- Note: User should gather more session data for statistically significant validation (minimum 3-5 OOS sessions recommended per research)

**Integration points for future plans:**
- 05-02: SensitivitySweeper will use `get_validation_sessions()` for parameter robustness testing
- 05-03: LatencySimulator will use same OOS sessions for realistic latency modeling
- 05-04: ValidationReport will use `validate_no_leakage()` before generating go/no-go decision

---
*Phase: 05-validation*
*Completed: 2026-02-02*
