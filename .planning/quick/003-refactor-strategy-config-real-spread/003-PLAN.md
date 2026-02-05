---
type: quick
id: "003"
title: "Refactor strategy config and spread handling to use real data"
autonomous: true
files_modified:
  - src/strategies/base.py
  - src/strategies/mirror/strategy.py
  - src/strategies/conservative/strategy.py
  - src/simulation/full_optimizer.py
  - config/config.yaml
must_haves:
  truths:
    - "Strategies use actual bid/ask spread from PriceSnapshot, not fake constants"
    - "full_optimizer uses config.yaml leader_estimated_capital ($900), not hardcoded $800"
    - "Config.yaml simulation section documents deprecation of fake spread params"
  artifacts:
    - path: "src/strategies/base.py"
      provides: "Helper function to calculate real spread from PriceSnapshot"
      contains: "calculate_actual_spread_pct"
    - path: "src/strategies/mirror/strategy.py"
      provides: "Cost check using real spread"
      pattern: "actual_spread.*prices"
    - path: "src/strategies/conservative/strategy.py"
      provides: "Cost check using real spread"
      pattern: "actual_spread.*prices"
  key_links:
    - from: "strategy cost check"
      to: "PriceSnapshot.bid/ask"
      via: "calculate_actual_spread_pct helper"
---

<objective>
Refactor strategy cost calculations to use REAL spread data from recorded bid/ask prices instead of fake constants.

Purpose: Current strategies use `cfg.spread_cost_pct` (constant 2.5%) for cost decisions, but we have real bid/ask data in PriceSnapshot. Using real spreads gives accurate cost-based skip decisions.

Output:
- Helper in base.py to calculate spread from prices
- Mirror and conservative strategies use real spread in cost_too_high check
- full_optimizer uses config.yaml values, not hardcoded overrides
- Config.yaml documents deprecated simulation spread params
</objective>

<execution_context>
@C:\Users\santi\.claude/get-shit-done/workflows/execute-plan.md
@C:\Users\santi\.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@config/config.yaml
@src/strategies/base.py
@src/strategies/mirror/strategy.py
@src/strategies/conservative/strategy.py
@src/simulation/full_optimizer.py
@src/data/models.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add real spread helper and update strategy cost checks</name>
  <files>
    src/strategies/base.py
    src/strategies/mirror/strategy.py
    src/strategies/conservative/strategy.py
  </files>
  <action>
    1. In base.py, add helper function:
       ```python
       def calculate_actual_spread_pct(prices: "PriceSnapshot") -> Decimal:
           """Calculate actual spread percentage from bid/ask prices.

           Returns spread as percentage: (ask - bid) / mid * 100
           Falls back to 0 if prices unavailable (let other checks handle it).
           """
           if not prices.bid or not prices.ask or prices.bid <= 0 or prices.ask <= 0:
               return Decimal("0")
           mid = (prices.bid + prices.ask) / 2
           if mid <= 0:
               return Decimal("0")
           return ((prices.ask - prices.bid) / mid) * 100
       ```
       (Import TYPE_CHECKING and add type hint for PriceSnapshot)

    2. In mirror/strategy.py _buy() method (around line 235-238):
       - Replace: `if drift + cfg.spread_cost_pct + cfg.slippage_cost_pct > cfg.max_total_cost_pct`
       - With: Calculate actual_spread_pct from prices, use in cost check
       - Import calculate_actual_spread_pct from base
       - Keep cfg.slippage_cost_pct (latency cost still applies)
       - Logic: `drift + actual_spread_pct + cfg.slippage_cost_pct > cfg.max_total_cost_pct`

    3. In conservative/strategy.py _buy() method (around line 136-139):
       - Same change as mirror: use actual spread instead of cfg.spread_cost_pct
       - Conservative uses MAX_TOTAL_COST_PCT (6%), not cfg.max_total_cost_pct
       - Import calculate_actual_spread_pct from base
  </action>
  <verify>
    Run: `python -c "from src.strategies.base import calculate_actual_spread_pct; from decimal import Decimal; from src.data.models import PriceSnapshot; p = PriceSnapshot('test', Decimal('0.48'), Decimal('0.52')); print(f'Spread: {calculate_actual_spread_pct(p):.2f}%')"`
    Expected: "Spread: 8.00%"
  </verify>
  <done>
    - Helper function exists and calculates spread correctly
    - Mirror strategy uses actual spread in cost check
    - Conservative strategy uses actual spread in cost check
  </done>
</task>

<task type="auto">
  <name>Task 2: Fix full_optimizer and document config</name>
  <files>
    src/simulation/full_optimizer.py
    config/config.yaml
  </files>
  <action>
    1. In full_optimizer.py:
       - Change default leader_capital from Decimal("800") to Decimal("900") to match config.yaml
       - Line 112: `def __init__(self, session_path: Path, leader_capital: Decimal = Decimal("900"))`
       - Line 248: `def run_full_optimization(session_path: str, leader_capital: float = 900.0)`
       - Line 274: `parser.add_argument("--leader-capital", type=float, default=900.0)`
       - Rationale: Config.yaml says `leader_estimated_capital: 900`, optimizer should match

    2. In config/config.yaml simulation section (lines 228-243):
       - Add comment explaining that simulated_spread_pct is for fallback only
       - Note that strategies now use real bid/ask spread from price data
       - Keep the values for backward compatibility but document they're deprecated for cost decisions

       Update simulation section to:
       ```yaml
       simulation:
         fill_model: immediate
         # DEPRECATED for cost decisions: Strategies now use real bid/ask spread from price data.
         # These values only apply as fallback if price data is missing (rare).
         simulated_spread_pct: 2.5       # Fallback spread (real spread preferred)
         simulated_slippage_pct: 1.0     # Latency/execution slippage (still used)
         simulated_latency_cost_pct: 0.0 # Additional latency cost
         fees_pct: 0.0
         random_seed: 42
       ```
  </action>
  <verify>
    1. Check full_optimizer default: `grep -n "Decimal.*900" src/simulation/full_optimizer.py`
    2. Check config comment: `grep -n "DEPRECATED" config/config.yaml`
  </verify>
  <done>
    - full_optimizer defaults match config.yaml ($900 leader capital)
    - Config.yaml documents that simulated_spread_pct is deprecated for cost decisions
  </done>
</task>

<task type="auto">
  <name>Task 3: Run tests and verify replay still works</name>
  <files></files>
  <action>
    1. Run existing unit tests to ensure changes don't break anything:
       `python -m pytest tests/unit/test_strategies.py -v`

    2. Run a quick replay to verify strategies still function:
       `python -m src.simulation.full_optimizer collected/sessions/YOUR_LATEST_SESSION.jsonl --leader-capital 900`
       (Use most recent session file in collected/sessions/)

    3. Check that cost_too_high skip reason still appears in output (proves the check is running)
  </action>
  <verify>
    - All unit tests pass
    - Replay completes without errors
    - Strategies show expected skip reasons (including cost_too_high where spreads are high)
  </verify>
  <done>
    - Tests pass
    - Replay runs successfully with real spread calculations
    - No regressions in strategy behavior
  </done>
</task>

</tasks>

<verification>
1. Unit tests pass: `python -m pytest tests/unit/test_strategies.py -v`
2. Helper function works correctly for edge cases (zero prices, missing bid/ask)
3. Strategies use real spread from PriceSnapshot, not cfg.spread_cost_pct
4. full_optimizer uses $900 default, matching config.yaml
</verification>

<success_criteria>
- [ ] calculate_actual_spread_pct helper exists in base.py
- [ ] MirrorStrategy cost check uses actual spread from prices
- [ ] ConservativeMirrorStrategy cost check uses actual spread from prices
- [ ] full_optimizer default leader_capital is 900 (not 800)
- [ ] config.yaml simulation section documents deprecated spread params
- [ ] All tests pass
</success_criteria>

<output>
After completion, create `.planning/quick/003-refactor-strategy-config-real-spread/003-SUMMARY.md`
</output>
