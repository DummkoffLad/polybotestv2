---
quick: 005
type: execute
autonomous: true
files_modified:
  - docs/strategies/profit_taker.md
  - src/tools/split_session_hourly.py
  - src/tools/visualize_trades.py
---

<objective>
Document profit_taker strategy, create hourly session splitting tool, and build trade visualization for verification.

Purpose: Enable analysis of strategy performance by hour (ET timezone) and visual verification that trades are happening at the right times/prices.
Output: Strategy docs, hourly splitting tool, trade visualization tool
</objective>

<context>
@src/strategies/profit_taker/strategy.py
@src/framework/replay.py
@optimize_profit_taker.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Document profit_taker strategy</name>
  <files>docs/strategies/profit_taker.md</files>
  <action>
Create strategy documentation covering:

1. **Overview**: Copy buys, dynamic profit targets, follow leader exit
2. **Parameters** (from strategy.py):
   - PROFIT_TARGET_LOW = 35% (prices < $0.30)
   - PROFIT_TARGET_MID = 20% (prices $0.30-$0.60)
   - PROFIT_TARGET_HIGH = 12% (prices > $0.60)
   - IGNORE_LEADER_MINISELLS_PCT = 10%
   - MIN_LEADER_TRADE_PCT = 1%
   - MAX_TOTAL_COST_PCT = 6%
   - CASH_RESERVE_PCT = 15%
   - PER_MARKET_CAP_PCT = 25%
   - PER_SIDE_PCT = 20%
   - GLOBAL_EXPOSURE_PCT = 85%

3. **Logic flow**:
   - Check all positions for profit targets on every event (uses all_prices context)
   - Handle leader buy (copy with risk controls)
   - Handle leader sell (follow unless mini-sell or our-loss/their-profit)
   - Exit triggers: profit target, extreme price (0.99), leader exit

4. **Comparison vs conservative_mirror**: What's different and why
5. **Current performance**: +$16.30 vs conservative +$4.32 on 2 sessions
  </action>
  <verify>File exists at docs/strategies/profit_taker.md, contains all parameters and logic sections</verify>
  <done>Complete strategy documentation with parameters, logic, and comparison</done>
</task>

<task type="auto">
  <name>Task 2: Create hourly session splitting tool</name>
  <files>src/tools/split_session_hourly.py</files>
  <action>
Create CLI tool that splits session JSONL files by market hour (ET timezone).

Requirements:
1. Read session JSONL file
2. Convert timestamps to ET timezone (use zoneinfo.ZoneInfo('America/New_York'))
3. Group events by hour (e.g., 10:00-10:59 ET = hour 10)
4. Write separate files: {session_name}_hour_{HH}.jsonl
5. Output summary showing event count per hour

Usage: python -m src.tools.split_session_hourly data/sessions/2026-02-03/05-56.jsonl

Output format:
- data/sessions/2026-02-03/05-56_hour_05.jsonl
- data/sessions/2026-02-03/05-56_hour_06.jsonl
- etc.

Key considerations:
- Markets are hourly - need to understand performance by trading hour
- ET timezone because Polymarket operates on US hours
- Include session_start event in each output file with config
- Preserve chronological order within each hour file
  </action>
  <verify>python -m src.tools.split_session_hourly data/sessions/2026-02-03/05-56.jsonl creates hour files</verify>
  <done>Hourly splitting tool works on existing session files</done>
</task>

<task type="auto">
  <name>Task 3: Create trade visualization tool</name>
  <files>src/tools/visualize_trades.py</files>
  <action>
Create tool that generates price chart with trade markers for visual verification.

Requirements:
1. Load session file and replay through strategy
2. Extract price history from price_snapshot events
3. Plot price over time for each traded token
4. Mark BUY entries (green up arrow) and SELL exits (red down arrow)
5. Show profit target line based on entry price
6. Generate interactive HTML using Plotly (already in project: use Scattergl for performance)

Usage: python -m src.tools.visualize_trades data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker

Output: data/reports/trade_viz_{session}_{strategy}.html

Chart should show:
- Price line (bid price over time)
- Entry markers (green triangles at buy price)
- Exit markers (red triangles at sell price)
- Horizontal lines showing profit target for each position
- Hover data: timestamp, price, shares, profit%

Use existing Plotly patterns from src/analysis/reports.py (CDN, Scattergl).
  </action>
  <verify>python -m src.tools.visualize_trades data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker creates HTML chart</verify>
  <done>Trade visualization showing entries/exits on price chart</done>
</task>

<task type="auto">
  <name>Task 4: Re-optimize and update parameters</name>
  <files>optimize_profit_taker.py, src/strategies/profit_taker/strategy.py</files>
  <action>
1. Run existing optimizer: python optimize_profit_taker.py
2. Capture the top performing parameter combination
3. If top result is better than current (LOW=35, MID=20, HIGH=12):
   - Update the constants in src/strategies/profit_taker/strategy.py
   - Update docs/strategies/profit_taker.md with new values
4. If current values are already optimal (or within $0.50), keep them

Note: Grid search tests 120 combinations. Watch for:
- Overfitting risk with only 2 sessions
- Prefer parameters that win on BOTH sessions (not just total)
- Consider robustness over raw performance
  </action>
  <verify>Optimizer runs successfully, strategy file reflects best parameters</verify>
  <done>Parameters are verified optimal (or updated if better found)</done>
</task>

</tasks>

<verification>
- docs/strategies/profit_taker.md exists with complete documentation
- src/tools/split_session_hourly.py creates hourly session files
- src/tools/visualize_trades.py creates trade visualization HTML
- profit_taker parameters are verified optimal via grid search
</verification>

<success_criteria>
1. Strategy fully documented with all parameters and logic
2. Can split any session into hourly files for granular analysis
3. Can visually verify trades happen at expected prices/times
4. Parameters confirmed optimal (or updated to better values)
</success_criteria>

<output>
After completion, create `.planning/quick/005-profit-taker-hourly-viz-docs/005-SUMMARY.md`
</output>
