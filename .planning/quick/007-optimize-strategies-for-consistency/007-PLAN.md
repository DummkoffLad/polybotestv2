---
quick_task: 007
type: execute
autonomous: true
files_modified:
  - src/strategies/conservative/strategy.py
  - src/strategies/profit_taker/strategy.py
  - .planning/quick/007-optimize-strategies-for-consistency/RESULTS.md
---

<objective>
Optimize conservative_mirror and profit_taker strategy parameters to maximize returns while maintaining consistency across all 3 sessions (2026-02-03, 2026-02-04, 2026-02-05).

Purpose: Find parameter combinations that perform well across different market conditions, avoiding overfitting to a single session.
Output: Updated strategy parameters (if improvements found) and documented optimization results.
</objective>

<context>
@.planning/STATE.md
@src/simulation/full_optimizer.py
@src/strategies/conservative/strategy.py
@src/strategies/profit_taker/strategy.py
</context>

<data_available>
Session Data (36 hours total):
- 2026-02-03: 14 hourly files (hours 00-13) + main file
- 2026-02-04: 18 hourly files (hours 00-14, 21-23) + main file
- 2026-02-05: 1 main file

Run against main files for full sessions: 05-56.jsonl, 02-35.jsonl, 03-58.jsonl
</data_available>

<tasks>

<task type="auto">
  <name>Task 1: Baseline and Parameter Grid Search</name>
  <files>
    - src/simulation/full_optimizer.py (read only - use existing run_single_aggregated)
    - .planning/quick/007-optimize-strategies-for-consistency/RESULTS.md (create)
  </files>
  <action>
1. Run baseline using full_optimizer against data/sessions/ with LIMIT_AGGRESSIVE mode:
   ```python
   python -m src.simulation.full_optimizer data/sessions/
   ```
   This runs all strategies across ALL sessions. Record baseline PnL for conservative_mirror and profit_taker.

2. Create parameter grid for conservative_mirror:
   - K_FACTOR_MULT: [0.5, 0.7, 0.9]
   - CASH_RESERVE_PCT: [15, 20, 25]
   - MAX_TOTAL_COST_PCT: [5, 6, 7]

3. Create parameter grid for profit_taker:
   - PROFIT_TARGET_LOW: [25, 35, 45]
   - PROFIT_TARGET_MID: [15, 20, 25]
   - PROFIT_TARGET_HIGH: [8, 12, 16]

4. For EACH parameter combination:
   a. Modify strategy file with new params
   b. Run optimizer against data/sessions/
   c. Record: total_pnl, resolved_pnl, per-session results
   d. Calculate consistency score: stddev of per-session PnL (lower is better)

5. Rank by: resolved_pnl / (1 + stddev_pnl) to balance returns with consistency
  </action>
  <verify>RESULTS.md contains baseline and all grid search results with per-session breakdown</verify>
  <done>Complete parameter sweep with consistency metrics documented</done>
</task>

<task type="auto">
  <name>Task 2: Apply Best Parameters and Verify</name>
  <files>
    - src/strategies/conservative/strategy.py
    - src/strategies/profit_taker/strategy.py
  </files>
  <action>
1. From Task 1 results, identify best parameter combination for each strategy
   - Must have positive total PnL
   - Must have consistency score better than baseline
   - Must not have any session with severe loss (> -$5)

2. Update strategy files ONLY if improvement found:
   - Conservative: Update K_FACTOR_MULT, CASH_RESERVE_PCT, MAX_TOTAL_COST_PCT
   - Profit Taker: Update PROFIT_TARGET_LOW/MID/HIGH

3. Re-run full optimizer to confirm improvement:
   ```python
   python -m src.simulation.full_optimizer data/sessions/
   ```

4. If NO improvement found (baseline is already optimal):
   - Document in RESULTS.md that current params are optimal
   - Do NOT change strategy files
  </action>
  <verify>
    - python -m src.simulation.full_optimizer data/sessions/ shows expected results
    - If params changed: new PnL >= baseline PnL with better consistency
  </verify>
  <done>
    - Best parameters applied (or baseline confirmed optimal)
    - Final verification run shows consistent performance
  </done>
</task>

<task type="auto">
  <name>Task 3: Document and Commit</name>
  <files>
    - .planning/quick/007-optimize-strategies-for-consistency/RESULTS.md
    - .planning/STATE.md
  </files>
  <action>
1. Finalize RESULTS.md with:
   - Summary table: baseline vs optimized for each strategy
   - Per-session breakdown showing consistency
   - Recommendation: which strategy + execution mode is best overall

2. Update STATE.md:
   - Add quick task 007 to completed list
   - Update decisions if new optimal params found

3. Commit changes:
   ```bash
   git add -A
   git commit -m "perf(quick-007): optimize conservative_mirror and profit_taker params

   - Tested parameter grid across 3 sessions (36 hours)
   - [Updated/Confirmed] conservative_mirror params
   - [Updated/Confirmed] profit_taker params
   - Documented results in .planning/quick/007-*/RESULTS.md"
   ```
  </action>
  <verify>git log -1 shows commit with optimization results</verify>
  <done>
    - RESULTS.md complete with optimization summary
    - STATE.md updated
    - Changes committed
  </done>
</task>

</tasks>

<verification>
- Full optimizer runs successfully on data/sessions/
- conservative_mirror and profit_taker show consistent results across all 3 sessions
- No session has catastrophic loss (> -$5)
- Documentation captures all tested parameters and rationale
</verification>

<success_criteria>
- Parameter grid search completed for both strategies
- Best params identified using consistency-weighted scoring
- Strategy files updated ONLY if genuine improvement found
- Results documented with per-session breakdown
- Changes committed to git
</success_criteria>

<output>
After completion, update this file with:
## RESULTS
[Summary of optimization findings]
</output>
