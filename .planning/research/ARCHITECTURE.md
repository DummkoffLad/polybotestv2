# Architecture Research: Strategy Debugging & Comparison Integration

**Domain:** Event-driven trading bot with session replay
**Researched:** 2026-02-03
**Confidence:** HIGH (existing codebase reviewed + verified patterns)

## Executive Summary

The existing architecture already contains 80% of the debugging/comparison infrastructure needed. The system uses event sourcing via JSONL session recordings, SessionReplayer for deterministic replay, and analysis components (attribution, equity tracking, drawdown, slippage). The remaining 20% is organizational: refactor SimpleOptimizer into a proper StrategyComparator, enhance TradeAttributor with failure categorization, and add visual diff tooling.

**Key architectural insight:** Don't build new infrastructure. Extend what exists. The event-driven pattern with session replay IS the debugging architecture.

## Existing Architecture Analysis

### Current Components

```
src/
├── strategies/
│   ├── base.py                 # Strategy ABC, TradeDecision, registry
│   ├── mirror.py              # 9 concrete strategies
│   └── ...
├── framework/
│   └── replay.py              # SessionReplayer (deterministic event replay)
├── simulation/
│   └── optimizer.py           # SimpleOptimizer (multi-strategy runner)
├── analysis/
│   ├── attribution.py         # TradeAttributor (entry/exit/PnL)
│   ├── equity_tracker.py      # EquityTracker (equity curve)
│   ├── drawdown.py            # DrawdownAnalyzer
│   ├── slippage.py            # SlippageAnalyzer (3 gap types)
│   └── reports.py             # ReportGenerator (console + charts)
└── data/
    └── models.py              # MarketEvent, LeaderTrade, PriceSnapshot
```

### Data Flow (Current)

```
Session Recording (.jsonl)
  ↓
SessionReplayer.load()
  → Parse events (price_snapshot, leader_trade)
  → Deduplicate (content-based dedup key)
  → Store chronologically
  ↓
SessionReplayer.run(strategy, config_overrides)
  → strategy.on_event(event) → TradeDecision
  → strategy.on_fill(event, decision)
  → [if track_analysis=True]
      → TradeAttributor.record_entry()
      → SlippageAnalyzer.measure_trade_slippage()
      → EquityTracker.record_snapshot()
  ↓
ReplayResult
  → realized_pnl, unrealized_pnl, resolved_pnl
  → trades list (ExecutedTrade records)
  → analysis dict (attribution, equity_df, drawdown, slippage)
```

### Integration Points (Already Working)

1. **Event Sourcing Foundation**
   - Session recordings = immutable event log
   - SessionReplayer = replay engine with historical price snapshots
   - Content-based deduplication prevents duplicate OrderFilled events

2. **Analysis Pipeline (Phase 5 validation)**
   - TradeAttributor: per-trade entry/exit with realized PnL
   - EquityTracker: timestamped equity snapshots → DataFrame
   - DrawdownAnalyzer: max drawdown, duration, recovery time
   - SlippageAnalyzer: execution gap, sizing gap, selection gap
   - ReportGenerator: console summaries + matplotlib charts

3. **Multi-Strategy Comparison (Exists as SimpleOptimizer)**
   - SimpleOptimizer.run_all_strategies() runs all registered strategies
   - SimpleOptimizer.run_grid() for parameter sweeps
   - Comparison table with buys/sells/PnL side-by-side
   - Best strategy selection via resolved_pnl score

**What already works:**
- Run same session through multiple strategies ✓
- Per-trade attribution with entry/exit ✓
- Slippage/sizing/selection gap analysis ✓
- Comparison table output ✓

## What Needs to Be Built

### Component 1: StrategyComparator (Refactor of SimpleOptimizer)

**Current state:** SimpleOptimizer mixes optimization (grid search) with comparison (run_all_strategies).

**Needed change:** Extract comparison logic into dedicated StrategyComparator.

```python
# src/debugging/comparator.py

class StrategyComparator:
    """Run multiple strategies on same session and compare results."""

    def __init__(self, session_path: Path):
        self.session_path = session_path
        self.results: Dict[str, ComparisonResult] = {}

    def add_strategy(self, strategy: Strategy, config_overrides: Dict = None):
        """Queue a strategy for comparison."""
        pass

    def run_comparison(self, track_analysis: bool = True) -> ComparisonReport:
        """Run all queued strategies and produce comparison report.

        For each strategy:
        1. Create SessionReplayer
        2. Run with track_analysis=True
        3. Collect ReplayResult + analysis

        Returns:
            ComparisonReport with:
            - results: Dict[strategy_name, ReplayResult]
            - ranked: List[strategy_name] sorted by resolved_pnl
            - diff_matrix: Trade-by-trade differences
            - failure_analysis: Categorized skip reasons
        """
        pass

    def generate_diff_report(self) -> DiffReport:
        """Compare trade decisions event-by-event.

        For each event:
        - Which strategies bought?
        - Which strategies skipped? (with reasons)
        - Which strategies sold?

        Produces:
        - Decision matrix (event x strategy)
        - Divergence points (where strategies disagreed)
        - Skip reason distribution by strategy
        """
        pass
```

**Integration:** StrategyComparator uses SessionReplayer internally (composition, not duplication).

### Component 2: Enhanced TradeAttributor with Failure Categorization

**Current state:** TradeAttributor tracks entry/exit/PnL but doesn't categorize WHY trades failed.

**Needed enhancement:** Add failure mode taxonomy.

```python
# Extend AttributedTrade dataclass

@dataclass
class AttributedTrade:
    # ... existing fields ...

    # Failure mode analysis (new)
    failure_mode: Optional[str] = None  # "timing", "sizing", "filter", "capital"
    failure_detail: Optional[str] = None  # Detailed explanation
    counterfactual_pnl: Optional[Decimal] = None  # What if we had followed?

    def categorize_failure(self, skip_reason: str, leader_outcome: Decimal):
        """Categorize why this trade failed and compute counterfactual.

        Failure taxonomy:
        - "timing": We skipped due to late signal, trade was profitable
        - "sizing": We traded but undersized, left PnL on table
        - "filter": Strategy filter rejected, but trade was good
        - "capital": Insufficient capital, trade was profitable
        - "correct_skip": We skipped, trade would have lost
        """
        pass
```

**Usage:**
```python
# During replay, for skipped trades:
if decision.action == DecisionAction.SKIP:
    # Record skip in TradeAttributor
    attributor.record_skip(
        event=event,
        skip_reason=decision.skip_reason,
        strategy_name=strategy.name
    )

# After session ends, compute counterfactuals:
attributor.compute_counterfactuals(final_prices)
```

### Component 3: Visual Diff Generator

**Current state:** Comparison output is text table only.

**Needed:** Visual timeline showing where strategies diverged.

```python
# src/debugging/visual_diff.py

class VisualDiffGenerator:
    """Generate visual comparison charts for strategies."""

    def generate_decision_timeline(self, comparison: ComparisonReport) -> Path:
        """Timeline chart showing buy/sell/skip decisions per strategy.

        X-axis: Time
        Y-axis: Strategies (one row per strategy)
        Colors: Green=buy, Red=sell, Gray=skip

        Makes divergence points visually obvious.
        """
        pass

    def generate_pnl_attribution_chart(self, comparison: ComparisonReport) -> Path:
        """Waterfall chart showing where PnL differences came from.

        For each strategy pair:
        - Starting equity (same)
        - Trade 1 diff
        - Trade 2 diff
        - ...
        - Final equity diff

        Shows exactly which trades caused performance divergence.
        """
        pass

    def generate_failure_mode_breakdown(self, attributed_trades: List[AttributedTrade]) -> Path:
        """Pie chart of failure modes.

        Categories:
        - Correct skips (saved money)
        - Timing failures (late to profitable trades)
        - Sizing failures (too small)
        - Filter failures (rejected good trades)
        - Capital failures (couldn't execute)
        """
        pass
```

### Component 4: FailureModeAnalyzer

**Purpose:** Aggregate failure patterns across strategies.

```python
# src/debugging/failure_analyzer.py

@dataclass
class FailurePattern:
    """A recurring failure pattern across multiple trades."""
    failure_mode: str
    occurrence_count: int
    total_missed_pnl: Decimal
    example_trades: List[AttributedTrade]
    fix_suggestion: str  # Actionable suggestion

class FailureModeAnalyzer:
    """Analyze failure patterns and suggest fixes."""

    def analyze_failures(self, attributed_trades: List[AttributedTrade]) -> FailureReport:
        """Identify recurring failure patterns.

        Patterns detected:
        1. "Repeated capital constraints" → suggest higher hourly_budget
        2. "Filter rejects profitable trades" → suggest looser filters
        3. "Consistent undersizing" → suggest higher k_factor
        4. "Late to moves" → architectural latency issue

        Returns:
            FailureReport with:
            - patterns: List[FailurePattern]
            - ranked_by_impact: Sorted by total_missed_pnl
            - fix_suggestions: Actionable recommendations
        """
        pass
```

## New Components Summary

| Component | Purpose | Integration Point | Lines of Code |
|-----------|---------|------------------|---------------|
| StrategyComparator | Multi-strategy runner | Uses SessionReplayer | ~200 |
| Enhanced TradeAttributor | Failure categorization | Extends existing AttributedTrade | ~100 |
| VisualDiffGenerator | Matplotlib charts | Uses ComparisonReport | ~300 |
| FailureModeAnalyzer | Pattern detection | Uses AttributedTrade list | ~200 |
| ComparisonReport | Data structure | Aggregates ReplayResults | ~50 |

**Total new code:** ~850 lines (vs 6000+ lines existing analysis infrastructure)

## Modified Components

### SessionReplayer (Minor Enhancement)

**Add:** Event-level callback hook for decision capture.

```python
# In SessionReplayer.run()

# NEW: Optional decision callback
if decision_callback:
    decision_callback(event, decision, strategy.name)
```

**Why:** Allows StrategyComparator to capture decisions event-by-event for diff generation.

### SimpleOptimizer (Refactor)

**Change:** Extract comparison logic → StrategyComparator, keep optimization logic.

```python
# OLD: SimpleOptimizer.run_all_strategies()
# NEW: StrategyComparator.run_comparison()

# OLD: SimpleOptimizer.print_summary()
# NEW: ComparisonReport.print_table()
```

**Why:** Single Responsibility Principle. Optimizer optimizes, Comparator compares.

## Data Flow (Enhanced)

```
Session Recording (.jsonl)
  ↓
StrategyComparator.add_strategy(strategy_1)
StrategyComparator.add_strategy(strategy_2)
StrategyComparator.add_strategy(strategy_3)
  ↓
StrategyComparator.run_comparison()
  ↓
  For each strategy:
    SessionReplayer.load()
    SessionReplayer.run(track_analysis=True, decision_callback=capture_fn)
      → TradeAttributor (with failure categorization)
      → EquityTracker
      → SlippageAnalyzer
    → ReplayResult + analysis dict
  ↓
  Aggregate:
    ComparisonReport
      → results: Dict[strategy_name, ReplayResult]
      → ranked: List[strategy_name]
      → diff_matrix: DataFrame (event x strategy x decision)
      → failure_summary: Dict[strategy_name, FailureReport]
  ↓
  Output:
    1. Console table (existing)
    2. Visual timeline (new: VisualDiffGenerator)
    3. PnL attribution waterfall (new)
    4. Failure mode breakdown (new: FailureModeAnalyzer)
    5. Fix suggestions (new)
```

## Build Order (Recommended Phases)

### Phase 1: Comparison Infrastructure (Foundation)
**Goal:** Refactor existing optimizer into clean comparator.

1. Create `src/debugging/__init__.py`
2. Create `ComparisonReport` dataclass
3. Refactor `SimpleOptimizer` → extract `StrategyComparator`
4. Add decision callback hook to `SessionReplayer`
5. Generate diff matrix (event x strategy decisions)

**Output:** Clean comparison runner with decision matrix

**Tests:** Compare 3 strategies, verify decision matrix captures all events

### Phase 2: Failure Analysis (Attribution Enhancement)
**Goal:** Categorize WHY trades failed, not just THAT they failed.

1. Extend `AttributedTrade` with failure fields
2. Add `categorize_failure()` method
3. Implement failure taxonomy (timing, sizing, filter, capital, correct_skip)
4. Compute counterfactual PnL for skipped trades
5. Create `FailureModeAnalyzer`

**Output:** Per-trade failure categorization + aggregated patterns

**Tests:** Verify failure modes correctly categorized, counterfactuals accurate

### Phase 3: Visual Diff (Charts & Reports)
**Goal:** Make comparison visual, not just tabular.

1. Create `VisualDiffGenerator`
2. Decision timeline chart (strategies x time)
3. PnL attribution waterfall (cumulative diff)
4. Failure mode pie chart
5. Save charts to `data/reports/comparison/`

**Output:** Visual comparison suite

**Tests:** Generate charts for 3-strategy comparison, verify readability

### Phase 4: Actionable Insights (Intelligence Layer)
**Goal:** Don't just report failures, suggest fixes.

1. Pattern detection in `FailureModeAnalyzer`
2. Fix suggestion engine (capital → increase budget, filter → loosen, etc.)
3. Impact ranking (which failure costs most PnL?)
4. Configuration diff tool (show config differences that caused divergence)

**Output:** Actionable failure report with fix suggestions

**Tests:** Verify suggestions match known failure modes

## Integration with Existing Systems

### With Validation Pipeline (Phase 5)

The validation pipeline uses SessionReplayer for train/test splits. StrategyComparator integrates seamlessly:

```python
# Validation pipeline can use StrategyComparator
comparator = StrategyComparator(test_session_path)
for strategy in strategies_to_validate:
    comparator.add_strategy(strategy, config)

report = comparator.run_comparison()
# Pick best performer on test set
```

### With Runner (Live Trading)

FailureModeAnalyzer insights inform live strategy selection:

```python
# After validation, analyze failure modes
analyzer = FailureModeAnalyzer()
failure_report = analyzer.analyze_failures(attributed_trades)

# If "repeated capital constraints" detected → increase hourly_budget
# If "filter rejects profitable trades" → switch to less conservative strategy
```

### With Analysis Components

StrategyComparator USES existing analysis components, doesn't replace them:

```python
# StrategyComparator internally calls:
replayer.run(track_analysis=True)
# Which populates:
result.analysis = {
    'trade_summary': TradeAttributor.get_summary(),
    'drawdown': DrawdownAnalyzer.analyze(),
    'slippage': SlippageAnalyzer.aggregate_slippage(),
    # ... etc
}
```

**No duplication.** Comparison is a thin orchestration layer over existing analysis.

## Architectural Patterns Applied

### 1. Event Sourcing (Already Implemented)

Session recordings are append-only event logs. This enables perfect replay determinism and time travel debugging.

**Source:** [Event Sourcing with Event Stores and Versioning in 2026](https://www.johal.in/event-sourcing-with-event-stores-and-versioning-in-2026/) confirms modern trading systems use append-only event stores for tamper-evident audit trails.

### 2. Replay as Debugging (Core Pattern)

The ability to replay historical events through different strategies IS the debugging architecture. This is not a new pattern to build—it's the foundation already in place.

**Source:** [Event Sourcing pattern - Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing) notes that "a hallmark of event sourcing is replayability – the ability to reprocess past events, which is useful for evolving systems when business requirements change by applying new logic to historical events without altering original data."

### 3. Strategy Pattern + Registry

Existing strategy registry (`register_strategy`, `get_strategy`, `list_strategies`) enables dynamic strategy loading for comparison without hardcoding.

### 4. Observer Pattern (Decision Callback)

Adding decision callback to SessionReplayer follows observer pattern: comparator observes each decision without coupling.

### 5. FMEA-Inspired Failure Categorization

FailureModeAnalyzer applies failure mode taxonomy from systems engineering to trading.

**Source:** [Failure Mode and Effects Analysis (FMEA)](https://asq.org/quality-resources/fmea) methodology categorizes failures by Severity, Occurrence, Detection. We adapt this to trading: failure_mode (type), occurrence_count (frequency), total_missed_pnl (severity).

### 6. Comparison Best Practices (2026 Standards)

Modern backtesting requires standardized test conditions (same session, same starting capital, same events) to ensure fair comparison.

**Source:** [How to Compare Two Trading Strategies Using Backtest Results](https://www.fxreplay.com/learn/how-to-compare-two-trading-strategies-using-backtest-results) emphasizes: "Always standardize your test conditions, otherwise the results aren't comparable."

## Anti-Patterns to Avoid

### 1. Don't Build Parallel Infrastructure

**Wrong:** Create new "DebugRunner" that duplicates SessionReplayer logic.

**Right:** Compose SessionReplayer. StrategyComparator is a thin wrapper.

### 2. Don't Mutate Strategies During Comparison

**Wrong:** Run strategy_1, mutate its state, run strategy_2 with contaminated state.

**Right:** Each strategy gets fresh instance via `get_strategy(name)`.

### 3. Don't Compare Apples to Oranges

**Wrong:** Run strategies with different config_overrides and compare PnL.

**Right:** Comparison uses same base config. Parameter sweeps are separate (optimizer, not comparator).

### 4. Don't Ignore Statistical Significance

**Wrong:** Declare strategy A "better" than B based on single session.

**Right:** Comparison is per-session. Validation pipeline runs multiple sessions for significance.

### 5. Don't Over-Engineer Failure Categorization

**Wrong:** Create 50 failure subcategories that overlap and confuse.

**Right:** Start with 5 clear categories (timing, sizing, filter, capital, correct_skip). Expand only if needed.

## Summary: Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| **Refactor, don't rebuild** | 80% of infrastructure exists. Extract and enhance, don't duplicate. |
| **Comparator uses Replayer** | Composition over duplication. SessionReplayer is the engine. |
| **Failure categorization in AttributedTrade** | Per-trade detail enables aggregation. Store raw data, compute patterns later. |
| **Visual diff is separate** | Charts are presentation layer. ComparisonReport is data layer. Decouple. |
| **Event-level callback hook** | Observer pattern. Comparator observes decisions without coupling. |
| **Failure taxonomy: 5 categories** | Simple, actionable, expandable. Avoid analysis paralysis. |
| **Fix suggestions, not just reports** | Actionable intelligence. "Increase hourly_budget by 20%" not just "capital constrained". |

## Confidence Assessment

| Area | Confidence | Source |
|------|------------|--------|
| Event sourcing foundation | HIGH | Existing codebase implements event sourcing correctly |
| SessionReplayer integration | HIGH | Codebase reviewed, replay.py is production-ready |
| Analysis components | HIGH | Phase 5 validation pipeline uses them successfully |
| Comparison patterns | MEDIUM | WebSearch + industry patterns, not yet implemented |
| Failure categorization | MEDIUM | FMEA methodology adapted to trading, needs testing |
| Visual diff design | MEDIUM | Standard matplotlib charts, straightforward implementation |

## Open Questions

1. **Counterfactual PnL accuracy:** How to compute "what if we had followed?" when we don't have fill prices we would have gotten?
   - **Answer:** Use leader's execution price as proxy. Conservative estimate.

2. **Failure mode taxonomy completeness:** Are 5 categories enough?
   - **Answer:** Start with 5, expand if patterns emerge that don't fit.

3. **Comparison session selection:** Which sessions are representative for comparison?
   - **Answer:** Validation pipeline's test set. Don't cherry-pick winning sessions.

4. **Fix suggestion reliability:** Can we automatically suggest config changes?
   - **Answer:** Start with simple heuristics (capital → budget, sizing → k_factor). Improve iteratively.

## Sources

### Architecture Patterns
- [Architectural Design Patterns for High-Frequency Algo Trading Bots | Medium](https://medium.com/@halljames9963/architectural-design-patterns-for-high-frequency-algo-trading-bots-c84f5083d704)
- [Event Sourcing pattern - Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)
- [Event Sourcing with Event Stores and Versioning in 2026](https://www.johal.in/event-sourcing-with-event-stores-and-versioning-in-2026/)
- [Trading System Architecture 2026 | From Microservices to Agentic Mesh](https://www.tuvoc.com/blog/trading-system-architecture-microservices-agentic-mesh/)

### Comparison & Backtesting
- [How to Compare Two Trading Strategies Using Backtest Results | FX Replay](https://www.fxreplay.com/learn/how-to-compare-two-trading-strategies-using-backtest-results)
- [Top Backtesting Features Every Trader Should Look For in 2026 | FX Replay](https://www.fxreplay.com/learn/top-backtesting-features-every-trader-should-look-for-in-2026)

### Trade Attribution
- [P&L Attribution | La Cima Group](https://www.lacimagroup.com/pnl-attribution/)
- [Running PnL Analytics: Risk and Drawdowns | TradesViz Blog](https://www.tradesviz.com/blog/running-pnl-risk-analysis/)

### Failure Analysis
- [What is FMEA? Failure Mode & Effects Analysis | ASQ](https://asq.org/quality-resources/fmea)
- [Failure mode analysis - Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/resiliency/failure-mode-analysis)
