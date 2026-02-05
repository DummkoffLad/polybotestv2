---
phase: quick
plan: 006
type: execute
wave: 1
depends_on: []
files_modified:
  - src/tools/visualize_portfolio.py
autonomous: true

must_haves:
  truths:
    - "Hourly charts show both UP and DOWN token prices in same subplot per market"
    - "Summary chart shows portfolio value, cash, realized P&L, unrealized P&L"
    - "Summary chart is generated as separate file automatically (not just with --hourly)"
  artifacts:
    - path: "src/tools/visualize_portfolio.py"
      provides: "Updated visualization with proper market grouping and summary"
      contains: "create_summary_chart"
  key_links:
    - from: "visualize_portfolio.py"
      to: "price_history by market"
      via: "token_info mapping"
      pattern: "token_info.*market_id"
---

<objective>
Fix visualization tool to properly display trading data:
1. Verify UP and DOWN tokens appear in same subplot per market (may already work)
2. Ensure summary chart shows all key metrics clearly: unrealized P&L, realized P&L, portfolio value, and cash
3. Always generate summary chart (currently works) and make hourly the default behavior

Purpose: Better understand trading performance with clear visual separation of market pairs and financial metrics
Output: Updated visualize_portfolio.py with improved charts
</objective>

<execution_context>
@C:\Users\santi\.claude/get-shit-done/workflows/execute-plan.md
@C:\Users\santi\.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.planning/CONTEXT-profit-taker-viz.md
@src/tools/visualize_portfolio.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Verify and enhance market grouping in hourly charts</name>
  <files>src/tools/visualize_portfolio.py</files>
  <action>
The hourly chart code already groups UP and DOWN tokens by market_id. Verify this works correctly:

1. Run the tool with --hourly flag on a test session
2. Check that each subplot shows two lines (UP in blue, DOWN in red)
3. If working, no code changes needed for this part

If NOT working (only seeing one token per subplot):
- Debug the token_info population from leader_trade events
- Ensure market_id grouping in create_hourly_charts is correct
- Check that both UP and DOWN price histories are being collected

Test: `python -m src.tools.visualize_portfolio data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker --hourly`
Inspect output HTML to confirm UP and DOWN lines appear together per market.
  </action>
  <verify>Open any hourly chart HTML and visually confirm two price lines (UP blue, DOWN red) per market subplot</verify>
  <done>Hourly charts display both UP and DOWN tokens in same subplot for each market</done>
</task>

<task type="auto">
  <name>Task 2: Improve summary chart with clearer cash display</name>
  <files>src/tools/visualize_portfolio.py</files>
  <action>
Update create_summary_chart to show clearer financial metrics:

1. Change "Cash Flow (Spent vs Received)" subplot to show:
   - Net Cash position (received - spent) as primary line
   - Keep spent/received as lighter secondary lines for context

2. Reorder subplots for clearer narrative:
   - Row 1: Portfolio Value (position holdings at current prices)
   - Row 2: Net Cash (how much cash we have/owe)
   - Row 3: Unrealized P&L (paper gains/losses)
   - Row 4: Realized P&L (locked in gains/losses)

3. Add Total P&L line to one of the subplots (realized + unrealized)

The code changes in create_summary_chart():
- Add net_cash trace (already calculated but not plotted prominently)
- Reorder subplot_titles tuple
- Reorder the trace additions to match new layout
- Add total_pnl trace alongside realized or unrealized
  </action>
  <verify>`python -m src.tools.visualize_portfolio data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker` produces summary chart with improved layout</verify>
  <done>Summary chart shows Portfolio Value, Net Cash, Unrealized P&L, and Realized P&L in clear, logical order</done>
</task>

<task type="auto">
  <name>Task 3: Make hourly charts default behavior</name>
  <files>src/tools/visualize_portfolio.py</files>
  <action>
Change the --hourly flag behavior:

1. Remove --hourly flag or change default to True
2. Add --no-hourly flag to skip hourly chart generation
3. Update help text accordingly

In main():
- Change: `parser.add_argument('--hourly', action='store_true', help='Generate per-hour charts')`
- To: `parser.add_argument('--no-hourly', action='store_true', help='Skip per-hour charts')`
- Change: `if args.hourly:` to `if not args.no_hourly:`

This makes the default behavior generate both summary AND hourly charts.
  </action>
  <verify>`python -m src.tools.visualize_portfolio data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker` generates BOTH summary and hourly charts without needing --hourly flag</verify>
  <done>Running visualize_portfolio without flags generates both summary and hourly charts by default</done>
</task>

</tasks>

<verification>
Run full visualization and check outputs:
```bash
python -m src.tools.visualize_portfolio data/sessions/2026-02-03/05-56.jsonl --strategy profit_taker
```

Verify:
1. Summary chart exists: data/reports/portfolio_05-56_profit_taker.html
2. Hourly charts exist: data/reports/05-56_hourly/05-56_hour_XX.html
3. Open summary chart - see 4 clear panels with Portfolio Value, Cash, Unrealized P&L, Realized P&L
4. Open any hourly chart - see UP (blue) and DOWN (red) price lines in same subplot per market
</verification>

<success_criteria>
- Summary chart has 4 subplots: Portfolio Value, Net Cash, Unrealized P&L, Realized P&L
- Hourly charts show UP and DOWN tokens together per market (2 lines per subplot)
- Both charts generated by default (no flags needed)
- All existing tests pass (if any)
</success_criteria>

<output>
After completion, create `.planning/quick/006-fix-viz-up-down-pnl-charts/006-SUMMARY.md`
</output>
