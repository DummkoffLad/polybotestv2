---
quick: 008
type: execute
autonomous: true
files_modified:
  - src/strategies/profit_taker/strategy.py
---

<objective>
Add trailing stop feature to profit_taker strategy and fix missing metrics.

Purpose: The profit_taker docstring mentions "TRAILING_STOP_PCT: Optional trailing stop" but it's not implemented. Adding trailing stops allows locking in profits when price runs up then reverses. Also align reporting metrics with conservative_mirror (total_bought/total_sold).

Output: Enhanced profit_taker with working trailing stop and consistent metrics.
</objective>

<context>
@src/strategies/profit_taker/strategy.py
@src/strategies/conservative/strategy.py (for metrics parity reference)
@.planning/quick/005-profit-taker-hourly-viz-docs/005-SUMMARY.md (prior work)
@.planning/quick/007-optimize-strategies-for-consistency/007-SUMMARY.md (optimization done)
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add trailing stop to profit_taker</name>
  <files>src/strategies/profit_taker/strategy.py</files>
  <action>
Implement the trailing stop feature mentioned in the docstring:

1. Add constant `TRAILING_STOP_PCT = Decimal("8")` (8% trailing stop - conservative value)

2. Add tracking dict `self.high_water_marks: Dict[str, Decimal] = {}` in __init__ and initialize()
   - Tracks highest bid price seen for each token since entry

3. In `on_event()` loop where we check profit targets (lines ~127-143):
   - Update high water mark: `high_water_marks[token_id] = max(current_bid, high_water_marks.get(token_id, entry_price))`
   - After profit target check, add trailing stop check:
     - If `high_water_marks[token_id] > our_entries[token_id]` (we've been in profit)
     - And `(high_water_marks[token_id] - current_bid) / high_water_marks[token_id] * 100 >= TRAILING_STOP_PCT`
     - Then exit with reason "trailing_stop"

4. Add `self.trailing_stops = 0` counter, increment on trailing stop exit

5. Clear high_water_mark when position fully exited (in on_fill)

6. Add trailing_stops to get_state() and on_session_end() returns

Key logic: Trailing stop only triggers AFTER price has run up from entry. If entry was $0.50 and high was $0.60, trailing stop triggers when price drops 8% from $0.60 (to $0.552).
  </action>
  <verify>
Run: `python -c "from src.strategies.profit_taker.strategy import ProfitTakerStrategy, TRAILING_STOP_PCT; print(f'TRAILING_STOP_PCT={TRAILING_STOP_PCT}')"` - should print 8
  </verify>
  <done>
Trailing stop feature implemented with 8% threshold. High water marks tracked per token. Exit reason "trailing_stop" added.
  </done>
</task>

<task type="auto">
  <name>Task 2: Add missing metrics for parity with conservative</name>
  <files>src/strategies/profit_taker/strategy.py</files>
  <action>
Add total_bought/total_sold tracking to match conservative_mirror's reporting:

1. In get_state() return dict, add:
   - "total_bought": str(self.portfolio.total_bought)
   - "total_sold": str(self.portfolio.total_sold)

2. In on_session_end() return dict, add:
   - "total_bought": str(self.portfolio.total_bought)
   - "total_sold": str(self.portfolio.total_sold)

This provides consistent metrics across all strategies for comparison tools.
  </action>
  <verify>
Run: `python -c "from src.strategies.profit_taker.strategy import ProfitTakerStrategy; s = ProfitTakerStrategy(); print('total_bought' in s.get_state())"` - should print True
  </verify>
  <done>
get_state() and on_session_end() now include total_bought and total_sold fields.
  </done>
</task>

<task type="auto">
  <name>Task 3: Verify strategy still loads and runs</name>
  <files>src/strategies/profit_taker/strategy.py</files>
  <action>
Run quick validation to ensure changes don't break strategy:

1. Import test: `python -c "from src.strategies.profit_taker.strategy import ProfitTakerStrategy; print('OK')"`

2. Run existing tests: `python -m pytest tests/strategies/test_profit_taker.py -v --tb=short`

3. Quick replay test (if tests pass): `python -m src.simulation.replay data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker --quiet`

If any failures, fix before committing.
  </action>
  <verify>
All commands exit with code 0. Tests pass. Replay completes without errors.
  </verify>
  <done>
Strategy loads correctly, all tests pass, replay works with new trailing stop feature.
  </done>
</task>

</tasks>

<verification>
1. `python -c "from src.strategies.profit_taker.strategy import TRAILING_STOP_PCT; print(TRAILING_STOP_PCT)"` outputs 8
2. `python -m pytest tests/strategies/test_profit_taker.py -v` passes
3. Strategy get_state() includes: trailing_stops, total_bought, total_sold
</verification>

<success_criteria>
- Trailing stop feature implemented and documented in docstring
- High water mark tracking for each position
- Exit reason "trailing_stop" triggers when price drops 8% from high
- Metrics parity with conservative_mirror (total_bought, total_sold)
- All existing tests still pass
</success_criteria>
