---
quick: 009-dryrun-vs-sim-parity-check
type: execute
autonomous: true
files_modified:
  - analyze_dryrun_parity.py
  - .planning/quick/009-dryrun-vs-sim-parity-check/009-RESULTS.md
---

<objective>
Compare 15 hours of dry run data (2026-02-06) against simulation to verify decision parity and identify any unrealistic behavior.

Purpose: Validate that the simulation accurately reproduces dry run decisions event-by-event, not just aggregate P&L. If they diverge, identify which system is unrealistic.

Output: Script that runs parity analysis + results markdown with detailed findings.
</objective>

<context>
@.planning/quick/009-dryrun-vs-sim-parity-check (this planning context)
@src/framework/replay/replayer.py (SessionReplayer)
@src/framework/replay/loader.py (config extraction, price snapshots)
@src/framework/replay/processor.py (event processing)
@compare_dryrun_vs_sim.py (existing P&L comparison - extend for decisions)
@data/sessions/2026-02-06/05-30_hour_*.jsonl (session files)
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create decision parity analysis script</name>
  <files>analyze_dryrun_parity.py</files>
  <action>
Create script that:

1. For each session file (05-30_hour_05.jsonl through hour_19):
   - Load session and extract RECORDED config from session_start event
   - Create strategy with RECORDED config (not current defaults)
   - Run SessionReplayer.run() to get simulation decisions

2. Event-by-event comparison:
   - For each leader_trade event in JSONL:
     - Extract dry run decision: event['decision']['action'], event['decision']['skip_reason']
     - Get simulation decision from replay (need to track by sequence number)
   - Compare: action match? skip_reason match?
   - Count: exact_match, action_mismatch, skip_reason_mismatch

3. Track divergences with details:
   - sequence, timestamp, token_id
   - dry_action vs sim_action
   - dry_skip_reason vs sim_skip_reason
   - price context at time of decision

4. For each session, calculate:
   - Parity percentage = exact_matches / total_events
   - Action parity = (BUY matches + SELL matches + SKIP matches) / total
   - Divergence details list

5. Output summary table:
   | Hour | Events | Exact Match | Action Match | Top Divergence |
   And CSV/JSON of all divergences for deep analysis.

IMPORTANT: Use self.loader.original_config from SessionReplayer to get recorded config.
The replayer already extracts config from session_start event via loader.py line 77.
Create fresh strategy instance initialized with this config for each session.

Reference existing compare_dryrun_vs_sim.py for session loading patterns.
  </action>
  <verify>
python analyze_dryrun_parity.py runs without error
Outputs parity table for all 15 hours
Generates divergence details file
  </verify>
  <done>
Script analyzes all 15 sessions and reports:
- Per-session parity percentage
- Total divergences with details
- Clear indication if systems match or not
  </done>
</task>

<task type="auto">
  <name>Task 2: Write results and identify unrealistic behavior</name>
  <files>.planning/quick/009-dryrun-vs-sim-parity-check/009-RESULTS.md</files>
  <action>
Run the analysis script and document findings:

1. Summary statistics:
   - Overall parity percentage across all hours
   - Which hours have 100% match vs divergences
   - Distribution of divergence types

2. Divergence analysis (if any):
   - Pattern identification (same skip_reason? same token? same time of hour?)
   - Root cause (price difference? config difference? code bug?)
   - Which system is correct/realistic

3. Open position analysis:
   - List positions held at end of each hour
   - Final prices from price_snapshot
   - Estimated resolution (bid > 0.5 = UP wins)

4. Conclusion:
   - Are dry run and simulation in parity?
   - If not, what's the root cause?
   - Is there unrealistic behavior to fix?

Format as clear markdown with tables and code blocks for evidence.
  </action>
  <verify>
009-RESULTS.md exists with complete analysis
All sections filled in with actual data from script output
Conclusion clearly states parity status
  </verify>
  <done>
Results document answers the key questions:
- Parity percentage (target: 100% or explanation for differences)
- Any unrealistic behavior identified and explained
- Actionable findings if issues discovered
  </done>
</task>

</tasks>

<verification>
- analyze_dryrun_parity.py runs successfully on all 15 sessions
- Parity percentage calculated and reported
- Divergences (if any) documented with root cause analysis
- Results file contains concrete findings
</verification>

<success_criteria>
- Clear answer: "Dry run and simulation are in X% parity"
- If <100%: Explanation of why and which system is correct
- If issues found: Identified which behavior is unrealistic
- Actionable next steps if fixes needed
</success_criteria>

<output>
After completion, the quick task is complete. No summary needed for quick plans.
</output>
