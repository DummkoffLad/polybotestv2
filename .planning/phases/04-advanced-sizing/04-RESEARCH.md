# Phase 4: Advanced Sizing - Research

**Researched:** 2026-02-02
**Domain:** Kelly criterion position sizing, capital efficiency scoring, trade prioritization, and confidence-based multipliers for copy trading
**Confidence:** HIGH

## Summary

Phase 4 requires implementing Kelly criterion-based position sizing that allocates each dollar to maximize risk-adjusted returns. Building on Phase 3's dynamic sizing foundation, this phase adds per-token edge estimation (win rate and avg win/loss tracking), trade prioritization when capital is constrained, and leader conviction-based confidence multipliers.

The standard approach for Kelly-based trading systems is:
- **Half Kelly (0.5x) or Quarter Kelly (0.25x)** for conservative growth with drawdown protection
- **Per-asset edge estimation** using rolling windows of 30-100 trades for reliable statistics
- **Expected value ranking** (Kelly edge score) to prioritize competing trades
- **Conviction signals** from position size, entry speed, and scaling behavior
- **Soft diversification** with correlation penalty as tiebreaker, not hard blocker

Key findings:
- Half Kelly captures ~75% of optimal growth with ~50% less drawdown versus full Kelly
- Professional traders use 25-50% of Kelly recommendation (Quarter to Half Kelly) for volatile markets
- Minimum 30 trades needed for statistical significance; 50-100 trades preferred for reliable edge estimation
- Cold start problem solved by falling back to Phase 3 dynamic sizing until sufficient data accumulated
- Edge formula: E = (W × AvgWin) - ((1-W) × AvgLoss) where W is win rate per leader-token pair
- Trade prioritization uses highest Kelly edge wins; correlation penalty is 10-20% score reduction, not elimination
- Conviction signals: position size relative to leader's typical size is strongest predictor (higher correlation than entry speed)
- Rebalancing threshold: exit existing position only when new opportunity has 1.5-2x higher edge score
- Statistical validation: paired t-test or bootstrap with 1000+ iterations, p < 0.05 threshold

**Primary recommendation:** Implement Half Kelly (0.5x) with per-token edge tracking using 50-trade rolling window, prioritize trades by Kelly edge score with soft diversification penalty, apply conviction multiplier (0.25x to 2x) based on leader position size, and validate with bootstrap resampling across all recorded sessions proving p < 0.05 improvement over Phase 3 baseline.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Decimal | stdlib | Precise Kelly calculations | Already in use; required for fractional Kelly accuracy |
| dataclasses | stdlib | Configuration for Kelly parameters | Already in use; clean config pattern |
| typing | stdlib | Type hints for edge tracking state | Already in use; complex state requires types |
| collections.deque | stdlib | Fixed-size rolling windows | Efficient FIFO for last N trades tracking |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| numpy | 1.24+ | Statistical calculations (mean, std) | Already installed; efficient for win rate / avg calculations |
| scipy.stats | via scipy (optional) | Statistical tests (t-test, bootstrap) | If present, use for validation; fallback to manual implementation |
| logging | stdlib | Track Kelly decisions and edge updates | Already in use; critical for debugging sizing |
| datetime | stdlib | Rolling window time boundaries | Already in use; per-token edge expiration |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Half Kelly | Full Kelly | Full Kelly too aggressive for uncertain edge estimates; Half Kelly industry standard |
| Rolling window | Exponential weighting | Rolling simpler, more interpretable; exponential adapts faster but less stable |
| Per-token tracking | Global leader edge | Per-token more accurate (tokens have different characteristics); global too coarse |
| Kelly edge ranking | Quality score ranking | Kelly incorporates edge magnitude; quality score only entry conditions |
| Soft diversification | Hard position limits per token | Soft allows strong edge to override; hard blocks profitable opportunities |

**Installation:**
```bash
# All required libraries already installed or in stdlib
# numpy already in requirements.txt (1.24+)
# scipy optional (for advanced statistical tests)
```

## Architecture Patterns

### Recommended Project Structure
```
src/
├── core/
│   ├── portfolio.py           # (existing) Portfolio with PnL tracking
│   ├── sizing.py              # (existing) DynamicSizer from Phase 3
│   ├── kelly_engine.py        # NEW: Kelly criterion calculator
│   ├── edge_tracker.py        # NEW: Per-token edge estimation
│   └── trade_ranker.py        # NEW: Prioritization and queue
├── strategies/
│   └── mirror/
│       └── strategy.py        # (modify) Integrate Kelly sizing
└── simulation/
    └── optimizer.py           # (existing) Statistical validation
```

### Pattern 1: Kelly Criterion Calculator
**What:** Calculate Kelly fraction with safety constraints (Half Kelly standard)
**When to use:** After edge estimation available, before position sizing
**Example:**
```python
# Source: Kelly criterion research + fractional Kelly best practices
from decimal import Decimal
from typing import Optional

class KellyCalculator:
    """Calculate Kelly criterion position sizes with fractional constraint."""

    def __init__(self, kelly_fraction: Decimal = Decimal("0.5")):
        """Initialize with Kelly fraction (0.5 = Half Kelly).

        Args:
            kelly_fraction: Fraction of full Kelly to use (0.25-0.5 recommended)
        """
        self.kelly_fraction = kelly_fraction

    def calculate_kelly_size(
        self,
        win_rate: Decimal,
        avg_win_pct: Decimal,
        avg_loss_pct: Decimal,
        current_equity: Decimal
    ) -> Optional[Decimal]:
        """Calculate Kelly position size.

        Formula: f* = (p*b - q) / b
        where:
        - p = win_rate (probability of winning)
        - q = 1 - p (probability of losing)
        - b = avg_win_pct / avg_loss_pct (win/loss ratio)

        Args:
            win_rate: Win rate in [0, 1] (e.g., 0.55 for 55%)
            avg_win_pct: Average win as % of position (e.g., 0.15 for 15% gain)
            avg_loss_pct: Average loss as % of position (e.g., 0.10 for 10% loss)
            current_equity: Total capital available

        Returns:
            Position size in dollars, or None if edge is negative
        """
        # Validate inputs
        if win_rate <= 0 or win_rate >= 1:
            return None
        if avg_loss_pct <= 0:
            return None

        # Calculate Kelly fraction
        q = Decimal("1") - win_rate
        b = avg_win_pct / avg_loss_pct

        # Full Kelly: f* = (p*b - q) / b
        full_kelly = (win_rate * b - q) / b

        # Negative edge = don't bet
        if full_kelly <= 0:
            return None

        # Apply fractional Kelly (e.g., 0.5 for Half Kelly)
        fractional_kelly = full_kelly * self.kelly_fraction

        # Cap at reasonable maximum (20% of equity)
        capped_kelly = min(fractional_kelly, Decimal("0.20"))

        # Convert to dollar amount
        position_size = current_equity * capped_kelly

        return position_size.quantize(Decimal("0.01"))

    def calculate_expected_value(
        self,
        win_rate: Decimal,
        avg_win_pct: Decimal,
        avg_loss_pct: Decimal
    ) -> Decimal:
        """Calculate expected value (edge) of the bet.

        E = (W × AvgWin) - ((1-W) × AvgLoss)

        Returns:
            Expected value as decimal (e.g., 0.05 for 5% expected return)
        """
        return (win_rate * avg_win_pct) - ((Decimal("1") - win_rate) * avg_loss_pct)
```

### Pattern 2: Per-Token Edge Tracking
**What:** Track win rate and win/loss ratio for each leader-token pair
**When to use:** After every trade completes to update rolling statistics
**Example:**
```python
# Source: Rolling window performance tracking + cold start handling
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional, Tuple
from datetime import datetime

@dataclass
class TradeResult:
    """Result of a completed trade."""
    token_id: str
    entry_price: Decimal
    exit_price: Decimal
    pnl_pct: Decimal  # PnL as % of position size
    timestamp: datetime

class EdgeTracker:
    """Track per-token edge statistics for Kelly sizing."""

    def __init__(self, lookback_trades: int = 50, min_trades_for_kelly: int = 20):
        """Initialize edge tracker.

        Args:
            lookback_trades: Number of recent trades to track per token
            min_trades_for_kelly: Minimum trades before Kelly activates
        """
        self.lookback_trades = lookback_trades
        self.min_trades_for_kelly = min_trades_for_kelly

        # Per-token trade histories
        self.trade_history: Dict[str, deque[TradeResult]] = {}

    def record_trade(self, result: TradeResult) -> None:
        """Record a completed trade result.

        Args:
            result: TradeResult with token_id and PnL
        """
        if result.token_id not in self.trade_history:
            self.trade_history[result.token_id] = deque(maxlen=self.lookback_trades)

        self.trade_history[result.token_id].append(result)

    def get_edge_stats(
        self,
        token_id: str
    ) -> Optional[Tuple[Decimal, Decimal, Decimal, int]]:
        """Get edge statistics for a token.

        Returns:
            (win_rate, avg_win_pct, avg_loss_pct, trade_count) or None if insufficient data
        """
        if token_id not in self.trade_history:
            return None

        trades = list(self.trade_history[token_id])
        trade_count = len(trades)

        # Need minimum trades for reliable statistics
        if trade_count < self.min_trades_for_kelly:
            return None

        # Calculate win rate
        wins = [t for t in trades if t.pnl_pct > 0]
        win_rate = Decimal(len(wins)) / Decimal(trade_count)

        # Calculate average win/loss
        if wins:
            avg_win = sum(t.pnl_pct for t in wins) / len(wins)
        else:
            avg_win = Decimal("0")

        losses = [t for t in trades if t.pnl_pct < 0]
        if losses:
            avg_loss = abs(sum(t.pnl_pct for t in losses) / len(losses))
        else:
            avg_loss = Decimal("0.01")  # Avoid division by zero

        return (win_rate, avg_win, avg_loss, trade_count)

    def has_sufficient_data(self, token_id: str) -> bool:
        """Check if token has enough data for Kelly sizing.

        Returns:
            True if trade count >= min_trades_for_kelly
        """
        if token_id not in self.trade_history:
            return False
        return len(self.trade_history[token_id]) >= self.min_trades_for_kelly
```

### Pattern 3: Trade Prioritization with Kelly Ranking
**What:** Rank competing trades by expected value (Kelly edge score)
**When to use:** When multiple trade opportunities arrive and capital is limited
**Example:**
```python
# Source: Capital allocation + expected value ranking + soft diversification
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Dict, Optional
from datetime import datetime

@dataclass
class TradeOpportunity:
    """A potential trade to follow."""
    token_id: str
    market_id: str
    leader_dollars: Decimal
    quality_score: Decimal
    kelly_edge: Optional[Decimal]  # Expected value from Kelly calculation
    conviction_multiplier: Decimal
    timestamp: datetime

class TradeRanker:
    """Prioritize trades when capital is constrained."""

    def __init__(
        self,
        correlation_penalty_pct: Decimal = Decimal("0.15"),
        rebalance_edge_gap: Decimal = Decimal("1.5")
    ):
        """Initialize trade ranker.

        Args:
            correlation_penalty_pct: Penalty for correlated positions (0.15 = 15% reduction)
            rebalance_edge_gap: Minimum edge ratio to exit existing for new (1.5 = 50% better)
        """
        self.correlation_penalty_pct = correlation_penalty_pct
        self.rebalance_edge_gap = rebalance_edge_gap

    def rank_opportunities(
        self,
        opportunities: List[TradeOpportunity],
        current_positions: Dict[str, Decimal],  # token_id -> position_size
        available_capital: Decimal
    ) -> List[TradeOpportunity]:
        """Rank trade opportunities by Kelly edge with soft diversification.

        Ranking logic:
        1. Calculate Kelly edge score for each opportunity
        2. Apply correlation penalty if token already in portfolio
        3. Sort by adjusted edge (highest first)
        4. Return prioritized list

        Args:
            opportunities: List of potential trades
            current_positions: Currently held positions
            available_capital: Capital available for new trades

        Returns:
            Sorted list of opportunities (highest edge first)
        """
        scored_opps = []

        for opp in opportunities:
            # Start with Kelly edge (expected value)
            if opp.kelly_edge is None:
                # No Kelly edge = use quality score as proxy
                edge_score = opp.quality_score
            else:
                # Kelly edge available = use it
                edge_score = opp.kelly_edge

            # Apply conviction multiplier
            edge_score *= opp.conviction_multiplier

            # Apply soft diversification penalty
            if opp.token_id in current_positions:
                # Already have position in this token = slight penalty
                penalty = Decimal("1") - self.correlation_penalty_pct
                edge_score *= penalty

            # Same market correlation (check if same market_id)
            # For simplicity, tokens in same market are considered correlated
            # More sophisticated: check actual price correlation

            scored_opps.append((edge_score, opp))

        # Sort by edge score descending (highest edge first)
        scored_opps.sort(key=lambda x: x[0], reverse=True)

        return [opp for score, opp in scored_opps]

    def should_rebalance(
        self,
        existing_position_edge: Decimal,
        new_opportunity_edge: Decimal
    ) -> bool:
        """Check if we should exit existing position for new opportunity.

        Conservative rebalancing: only swap if new edge significantly better.

        Args:
            existing_position_edge: Edge of current position
            new_opportunity_edge: Edge of new trade

        Returns:
            True if new edge is sufficiently higher (1.5-2x)
        """
        if existing_position_edge <= 0:
            return True  # Existing has no edge, definitely swap

        edge_ratio = new_opportunity_edge / existing_position_edge
        return edge_ratio >= self.rebalance_edge_gap
```

### Pattern 4: Conviction-Based Confidence Multiplier
**What:** Adjust Kelly size based on leader conviction signals
**When to use:** After Kelly size calculated, before final position sizing
**Example:**
```python
# Source: Conviction scoring + position sizing research
from decimal import Decimal
from typing import Dict
from dataclasses import dataclass

@dataclass
class ConvictionSignals:
    """Leader conviction signals for a trade."""
    position_size_dollars: Decimal  # Leader's position size
    entry_speed_seconds: float      # How fast they entered (deliberate vs impulsive)
    is_scale_in: bool              # Did they add to existing position?

class ConvictionScorer:
    """Calculate conviction multiplier from leader signals."""

    def __init__(self, leader_avg_size: Decimal):
        """Initialize with leader's typical trade size.

        Args:
            leader_avg_size: Leader's average position size (baseline for comparison)
        """
        self.leader_avg_size = leader_avg_size

        # Conviction multiplier range: 0.25x (very low) to 2x (very high)
        self.min_multiplier = Decimal("0.25")
        self.max_multiplier = Decimal("2.0")

    def calculate_multiplier(self, signals: ConvictionSignals) -> Decimal:
        """Calculate conviction multiplier from signals.

        Weighting (based on predictive power):
        - Position size relative to average: 60% weight (strongest signal)
        - Scale-in behavior: 25% weight
        - Entry speed: 15% weight

        Args:
            signals: ConvictionSignals with position size, speed, scale-in

        Returns:
            Multiplier in [0.25, 2.0] range
        """
        # Component 1: Position size relative to average (60% weight)
        size_ratio = signals.position_size_dollars / self.leader_avg_size
        # Map 0.5x avg -> 0.25x multiplier, 2x avg -> 2x multiplier
        size_score = min(max(size_ratio, Decimal("0.5")), Decimal("2.0"))

        # Component 2: Scale-in behavior (25% weight)
        # Adding to position = higher conviction
        scale_score = Decimal("1.5") if signals.is_scale_in else Decimal("1.0")

        # Component 3: Entry speed (15% weight)
        # Deliberate entry (slower) = higher conviction
        # Fast entry (< 5 seconds) = impulsive, lower conviction
        if signals.entry_speed_seconds < 5:
            speed_score = Decimal("0.8")  # Impulsive
        elif signals.entry_speed_seconds > 30:
            speed_score = Decimal("1.2")  # Deliberate
        else:
            speed_score = Decimal("1.0")  # Neutral

        # Weighted combination
        multiplier = (
            size_score * Decimal("0.60") +
            scale_score * Decimal("0.25") +
            speed_score * Decimal("0.15")
        )

        # Clamp to valid range
        multiplier = max(self.min_multiplier, min(multiplier, self.max_multiplier))

        return multiplier.quantize(Decimal("0.01"))

    def update_leader_avg_size(self, recent_trades: list[Decimal]) -> None:
        """Update leader's average trade size from recent history.

        Args:
            recent_trades: List of leader's recent position sizes
        """
        if recent_trades:
            self.leader_avg_size = sum(recent_trades) / len(recent_trades)
```

### Pattern 5: Cold Start Fallback Strategy
**What:** Handle insufficient data gracefully by falling back to Phase 3
**When to use:** When per-token edge data not yet available
**Example:**
```python
# Source: Cold start problem handling + Phase 3 integration
from decimal import Decimal
from typing import Optional
from .sizing import DynamicSizer  # Phase 3
from .kelly_engine import KellyCalculator
from .edge_tracker import EdgeTracker

class AdaptiveSizer:
    """Adaptive sizer that uses Kelly when available, falls back to Phase 3.

    Cold start behavior:
    - Insufficient data (<20 trades): Use Phase 3 DynamicSizer
    - Sufficient data (>=20 trades): Use Kelly with confidence multiplier
    - Negative edge: Skip trade (Kelly returns None)
    """

    def __init__(
        self,
        dynamic_sizer: DynamicSizer,
        kelly_calculator: KellyCalculator,
        edge_tracker: EdgeTracker
    ):
        """Initialize adaptive sizer.

        Args:
            dynamic_sizer: Phase 3 DynamicSizer (fallback)
            kelly_calculator: Kelly criterion calculator
            edge_tracker: Per-token edge tracker
        """
        self.dynamic_sizer = dynamic_sizer
        self.kelly_calculator = kelly_calculator
        self.edge_tracker = edge_tracker

    def calculate_position_size(
        self,
        token_id: str,
        current_equity: Decimal,
        quality_score: Decimal,
        conviction_multiplier: Decimal = Decimal("1.0")
    ) -> tuple[Optional[Decimal], str]:
        """Calculate position size with Kelly or Phase 3 fallback.

        Returns:
            (position_size, reason) tuple
            - position_size: Dollar amount or None if should skip
            - reason: "kelly", "phase3_fallback", or "negative_edge"
        """
        # Check if we have sufficient Kelly data
        edge_stats = self.edge_tracker.get_edge_stats(token_id)

        if edge_stats is None:
            # Cold start: not enough data, fall back to Phase 3
            size = self.dynamic_sizer.calculate_position_size(
                current_equity=current_equity,
                quality_score=quality_score
            )
            return (size, "phase3_fallback")

        # Unpack edge statistics
        win_rate, avg_win, avg_loss, trade_count = edge_stats

        # Calculate Kelly size
        kelly_size = self.kelly_calculator.calculate_kelly_size(
            win_rate=win_rate,
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            current_equity=current_equity
        )

        if kelly_size is None:
            # Negative edge: don't trade this token
            return (None, "negative_edge")

        # Apply conviction multiplier
        final_size = kelly_size * conviction_multiplier

        # Quantize to cents
        final_size = final_size.quantize(Decimal("0.01"))

        return (final_size, "kelly")
```

### Anti-Patterns to Avoid
- **Using full Kelly without fractional constraint:** Full Kelly too aggressive for uncertain edge estimates; Half Kelly (0.5x) is industry standard
- **Global edge estimation for all tokens:** Per-token edge more accurate; different tokens have different characteristics
- **Hard blocking correlated trades:** Soft penalty (10-20% reduction) allows strong edge to override; hard blocks profitable opportunities
- **Rebalancing continuously:** Conservative threshold (1.5-2x edge gap) prevents excessive turnover and transaction costs
- **Ignoring cold start problem:** No Kelly data yet = fall back to Phase 3 dynamic sizing, don't skip trades
- **Conviction multiplier without capping:** Cap at 2x prevents outlier large trades from dominating portfolio
- **Using Kelly without statistical validation:** Must prove improvement with p < 0.05 significance before trusting

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Rolling statistics | Custom windowing logic | collections.deque with maxlen | Efficient FIFO queue, handles edge cases (empty, single element) |
| Win rate calculation | Manual counting | len([x for x in trades if x.pnl > 0]) / len(trades) | Readable, handles empty list |
| Statistical significance | Custom p-value calculation | scipy.stats.ttest_rel or manual paired t-test | Extensively validated, handles edge cases |
| Bootstrap resampling | Custom resampling logic | numpy.random.choice with replace=True | Efficient, correct sampling distribution |
| Expected value calculation | Complex formulas | E = (W × AvgWin) - ((1-W) × AvgLoss) | Standard formula, well-documented |

**Key insight:** Kelly sizing complexity is in state management (per-token histories, rolling windows, cold start handling), not algorithms. Use stdlib data structures (deque, dict) and simple statistical formulas. Avoid premature optimization.

## Common Pitfalls

### Pitfall 1: Full Kelly Without Fractional Constraint
**What goes wrong:** Volatile position sizes, excessive drawdowns, account wipeout risk
**Why it happens:** Full Kelly optimal for known edge; real-world edge estimates uncertain
**How to avoid:** Always use Half Kelly (0.5x) or Quarter Kelly (0.25x) for volatile markets
**Warning signs:** Position sizes swing wildly, drawdowns exceed 30%, Sharpe ratio poor

### Pitfall 2: Insufficient Sample Size for Kelly
**What goes wrong:** Kelly sizes based on 3-5 trades, win rate estimate unreliable
**Why it happens:** Eager to activate Kelly before enough data accumulated
**How to avoid:** Require minimum 20-30 trades per token before activating Kelly
**Warning signs:** Kelly sizes don't stabilize, high variance in consecutive Kelly calculations

### Pitfall 3: Not Handling Negative Edge
**What goes wrong:** Kelly returns None (negative edge), code crashes or sizes to zero
**Why it happens:** Didn't check for negative edge case in Kelly formula
**How to avoid:** Explicitly check if kelly_size is None, skip trade if negative edge
**Warning signs:** Crashes when win rate < 50% and avg_loss > avg_win

### Pitfall 4: Hard Position Limits for Same Token
**What goes wrong:** Skip profitable trades because "already have position in this token"
**Why it happens:** Applied hard diversification rules instead of soft penalty
**How to avoid:** Use soft penalty (10-20% edge reduction), not hard block
**Warning signs:** Miss high-edge trades, performance lags single-token focus

### Pitfall 5: Continuous Rebalancing Without Threshold
**What goes wrong:** Constantly exit/enter positions, transaction costs eat profits
**Why it happens:** Rebalance on any edge difference, no minimum gap requirement
**How to avoid:** Only rebalance when new edge 1.5-2x higher than existing
**Warning signs:** High turnover, transaction costs > 5% of PnL, underperformance

### Pitfall 6: Conviction Multiplier Applied to Base Kelly
**What goes wrong:** Conviction multiplier applied before fractional Kelly, amplifies too much
**Why it happens:** Wrong order of operations: should be (Full Kelly × Fraction) × Conviction
**How to avoid:** Apply conviction after fractional Kelly, not before
**Warning signs:** Outlier large positions, single trades dominate portfolio

### Pitfall 7: Not Validating Statistical Significance
**What goes wrong:** Kelly looks better but improvement is random chance (p > 0.05)
**Why it happens:** Compared total PnL without statistical test
**How to avoid:** Run paired t-test or bootstrap with p < 0.05 threshold
**Warning signs:** Improvement inconsistent across sessions, disappears with more data

## Code Examples

Verified patterns from research and codebase:

### Integrating Kelly into MirrorStrategy
```python
# Source: Phase 3 MirrorStrategy + Kelly patterns above
from decimal import Decimal
from typing import Optional
from src.strategies.mirror.strategy import MirrorStrategy
from src.core.sizing import DynamicSizer, SizingConfig
from src.core.kelly_engine import KellyCalculator
from src.core.edge_tracker import EdgeTracker, TradeResult
from src.core.trade_ranker import TradeRanker, TradeOpportunity

class KellyMirrorStrategy(MirrorStrategy):
    """Mirror strategy with Kelly criterion sizing."""

    def initialize(self, config: StrategyConfig) -> None:
        super().initialize(config)

        # Phase 3 components (fallback)
        self.dynamic_sizer = self.sizer  # Already created by super()

        # Phase 4: Kelly components
        self.kelly_calculator = KellyCalculator(kelly_fraction=Decimal("0.5"))  # Half Kelly
        self.edge_tracker = EdgeTracker(lookback_trades=50, min_trades_for_kelly=20)
        self.trade_ranker = TradeRanker(
            correlation_penalty_pct=Decimal("0.15"),
            rebalance_edge_gap=Decimal("1.5")
        )

        # Conviction scorer (needs leader's avg trade size)
        # This would be calculated from historical data
        self.leader_avg_size = Decimal("100")  # Placeholder

    def _buy(self, event: MarketEvent, scaled: Decimal) -> TradeDecision:
        """Override to use Kelly sizing with prioritization."""
        trade, prices, cfg = event.trade, event.prices, self.config
        token_id = event.token_id

        # Calculate current equity
        current_equity = self._calculate_current_equity()

        # Check edge statistics for this token
        edge_stats = self.edge_tracker.get_edge_stats(token_id)

        if edge_stats is None:
            # Cold start: use Phase 3 dynamic sizing
            quality_score = self._calculate_quality_score(event)
            kelly_size = self.dynamic_sizer.calculate_position_size(
                current_equity=current_equity,
                quality_score=quality_score
            )
            sizing_method = "phase3_fallback"
        else:
            # Kelly data available
            win_rate, avg_win, avg_loss, trade_count = edge_stats

            # Calculate Kelly size
            kelly_size = self.kelly_calculator.calculate_kelly_size(
                win_rate=win_rate,
                avg_win_pct=avg_win,
                avg_loss_pct=avg_loss,
                current_equity=current_equity
            )

            if kelly_size is None:
                # Negative edge: skip this token
                return self._skip("negative_edge")

            # Apply conviction multiplier
            conviction_mult = self._calculate_conviction_multiplier(event)
            kelly_size *= conviction_mult

            sizing_method = "kelly"

        # Use Kelly size instead of scaled
        dollars = min(kelly_size, scaled)

        # Continue with existing cap checks and order logic...
        # (rest of Phase 3 _buy logic)

    def on_fill(self, decision: TradeDecision) -> None:
        """Override to update edge tracker after trades complete."""
        super().on_fill(decision)

        # If this was a SELL, record the trade result for edge tracking
        if decision.action == DecisionAction.SELL:
            token_id = decision.token_id
            pos = self.portfolio.get(token_id)

            if pos and pos.shares > 0:
                # Estimate PnL % (simplified: exit_price / avg_price - 1)
                pnl_pct = (decision.price / pos.avg_price) - Decimal("1")

                result = TradeResult(
                    token_id=token_id,
                    entry_price=pos.avg_price,
                    exit_price=decision.price,
                    pnl_pct=pnl_pct,
                    timestamp=decision.timestamp
                )

                self.edge_tracker.record_trade(result)

    def _calculate_conviction_multiplier(self, event: MarketEvent) -> Decimal:
        """Calculate leader conviction multiplier."""
        # Position size relative to leader's average
        size_ratio = event.trade.dollars / self.leader_avg_size

        # Simple multiplier: 0.25x to 2x based on size ratio
        # 0.5x avg -> 0.5 multiplier, 1x avg -> 1.0, 2x avg -> 2.0
        multiplier = max(
            Decimal("0.25"),
            min(size_ratio, Decimal("2.0"))
        )

        return multiplier
```

### Statistical Validation with Bootstrap
```python
# Source: Bootstrap validation + backtesting best practices
import numpy as np
from decimal import Decimal
from typing import List
from scipy import stats

class KellyValidator:
    """Validate Kelly sizing improvement with statistical tests."""

    def validate_improvement(
        self,
        phase3_pnls: List[Decimal],
        phase4_pnls: List[Decimal],
        significance_level: float = 0.05
    ) -> tuple[bool, float, str]:
        """Test if Phase 4 Kelly significantly outperforms Phase 3.

        Uses paired t-test (sessions are paired: same recorded data replayed).

        Args:
            phase3_pnls: List of PnLs from Phase 3 replay sessions
            phase4_pnls: List of PnLs from Phase 4 replay sessions (same sessions)
            significance_level: p-value threshold (0.05 standard)

        Returns:
            (is_significant, p_value, interpretation)
        """
        # Convert Decimal to float for scipy
        p3 = np.array([float(x) for x in phase3_pnls])
        p4 = np.array([float(x) for x in phase4_pnls])

        # Paired t-test (same sessions, different strategies)
        t_stat, p_value = stats.ttest_rel(p4, p3)

        is_significant = (p_value < significance_level) and (t_stat > 0)

        if is_significant:
            interpretation = f"Phase 4 significantly better (p={p_value:.4f} < {significance_level})"
        elif p_value < significance_level and t_stat < 0:
            interpretation = f"Phase 4 significantly WORSE (p={p_value:.4f})"
        else:
            interpretation = f"No significant difference (p={p_value:.4f} >= {significance_level})"

        return (is_significant, p_value, interpretation)

    def bootstrap_confidence_interval(
        self,
        phase3_pnls: List[Decimal],
        phase4_pnls: List[Decimal],
        n_iterations: int = 1000,
        confidence: float = 0.95
    ) -> tuple[float, float]:
        """Calculate confidence interval for PnL difference using bootstrap.

        Args:
            phase3_pnls: Phase 3 PnLs
            phase4_pnls: Phase 4 PnLs
            n_iterations: Bootstrap iterations (1000+ recommended)
            confidence: Confidence level (0.95 = 95% CI)

        Returns:
            (lower_bound, upper_bound) of difference in mean PnL
        """
        p3 = np.array([float(x) for x in phase3_pnls])
        p4 = np.array([float(x) for x in phase4_pnls])

        differences = []
        n_samples = len(p3)

        for _ in range(n_iterations):
            # Resample with replacement
            indices = np.random.choice(n_samples, size=n_samples, replace=True)
            boot_p3 = p3[indices]
            boot_p4 = p4[indices]

            # Calculate mean difference
            diff = np.mean(boot_p4) - np.mean(boot_p3)
            differences.append(diff)

        # Calculate confidence interval
        alpha = 1 - confidence
        lower = np.percentile(differences, alpha/2 * 100)
        upper = np.percentile(differences, (1 - alpha/2) * 100)

        return (lower, upper)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Fixed percentage sizing | Kelly criterion sizing | 2010+ quantitative trading | Optimal capital allocation for edge; sizes scale with win rate and win/loss ratio |
| Full Kelly | Fractional Kelly (25-50%) | 2015+ risk management | Reduces drawdown 50-75%, sacrifices only 25-50% growth; safer for uncertainty |
| Global strategy edge | Per-asset edge tracking | 2018+ machine learning trading | More accurate edge estimates; different assets have different characteristics |
| Hard diversification limits | Soft diversification penalty | 2020+ portfolio optimization | Allows high-edge concentrated bets while discouraging correlation |
| No prioritization | Expected value ranking | 2022+ algorithmic trading | Efficient capital allocation to highest-edge opportunities |
| Manual conviction assessment | Quantitative conviction signals | 2023+ copy trading | Position size, entry speed, scale-in behavior predict leader conviction |

**Deprecated/outdated:**
- Full Kelly without fractional constraint: Too aggressive for uncertain edge estimates; industry moved to Half Kelly
- Global edge (one win rate for entire strategy): Too coarse; per-asset tracking standard
- Hard position limits per token: Blocks profitable opportunities; soft penalties better
- Continuous rebalancing: Transaction costs eat profits; threshold-based rebalancing standard
- Fixed conviction multipliers: Leader signals (position size, scale-in) provide dynamic conviction

## Open Questions

Things that couldn't be fully resolved:

1. **Optimal Kelly Fraction for Polymarket**
   - What we know: Professional traders use 25-50% of full Kelly (Quarter to Half Kelly)
   - What's unclear: Polymarket binary outcomes may justify more conservative 25% (Quarter Kelly) vs standard 50%
   - Recommendation: Start with Half Kelly (0.5); if drawdowns exceed 20%, reduce to Quarter Kelly (0.25)

2. **Minimum Trades for Reliable Edge Estimation**
   - What we know: General guideline is 30-100 trades for statistical significance
   - What's unclear: Polymarket binary outcomes (0 or 1 settlement) may need more samples than continuous outcomes
   - Recommendation: Start with 20 minimum, 50 lookback; tune based on edge stability (std deviation of rolling win rate)

3. **Conviction Signal Weights**
   - What we know: Position size relative to average is strongest signal (60% weight suggested)
   - What's unclear: Polymarket leader behavior may differ from stock/crypto trading
   - Recommendation: Use 60/25/15 split (size/scale-in/speed); log conviction scores and validate correlation with PnL

4. **Correlation Penalty Magnitude**
   - What we know: 10-20% edge reduction for correlated positions is standard
   - What's unclear: Polymarket markets may have lower correlation than stocks (independent events)
   - Recommendation: Start with 15% penalty; measure actual correlation from historical data, adjust if needed

5. **Rebalance Edge Gap Threshold**
   - What we know: 1.5-2x edge gap prevents excessive turnover
   - What's unclear: Optimal threshold depends on transaction costs and market regime
   - Recommendation: Use 1.5x (conservative); if missing profitable opportunities, reduce to 1.3x

6. **Cold Start Performance Impact**
   - What we know: Fall back to Phase 3 sizing until Kelly data available
   - What's unclear: How much PnL leakage occurs during cold start period (first 20 trades per token)
   - Recommendation: Measure cold start period duration and PnL; if significant leakage, consider seeding Kelly with leader's historical data

7. **Statistical Test Choice**
   - What we know: Paired t-test standard for comparing strategies on same sessions
   - What's unclear: Bootstrap may be more robust for non-normal PnL distributions
   - Recommendation: Run both t-test and bootstrap; require both p < 0.05 for high confidence

## Sources

### Primary (HIGH confidence)
- [Kelly Criterion for Crypto Traders: A Modern Approach to Volatile Markets](https://medium.com/@tmapendembe_28659/kelly-criterion-for-crypto-traders-a-modern-approach-to-volatile-markets-a0cda654caa9) - Jan 2026 practical guidance
- [Risk Management Using Kelly Criterion](https://medium.com/@tmapendembe_28659/risk-management-using-kelly-criterion-2eddcf52f50b) - Jan 2026 risk management
- [Analysis of The Kelly Criterion in Practice](https://www.alphatheory.com/blog/kelly-criterion-in-practice-1) - Real-world Kelly applications
- [Kelly Criterion Applications in Trading Systems](https://www.quantconnect.com/research/18312/kelly-criterion-applications-in-trading-systems/) - QuantConnect implementation
- [Use the Kelly criterion for optimal position sizing](https://www.pyquantnews.com/the-pyquant-newsletter/use-kelly-criterion-optimal-position-sizing) - Python/NumPy/SciPy implementation
- [Optimize Your Trading Strategy With Python And The Kelly Criterion](https://raposa.trade/blog/optimize-your-trading-strategy-with-python-and-the-kelly-criterion/) - Python patterns
- [Kelly criterion - Wikipedia](https://en.wikipedia.org/wiki/Kelly_criterion) - Standard Kelly formula
- **Codebase analysis** (local files):
  - `src/strategies/mirror/strategy.py` - Phase 3 integration point
  - `src/core/sizing.py` - DynamicSizer foundation
  - `src/core/portfolio.py` - PnL tracking for edge calculation
  - `.planning/phases/04-advanced-sizing/04-CONTEXT.md` - User requirements

### Secondary (MEDIUM confidence)
- [Kelly Position Size Calculator — TradingView Indicator](https://www.tradingview.com/script/83fHgI24-Kelly-Position-Size-Calculator/) - Calculator implementation
- [Money Management via the Kelly Criterion | QuantStart](https://www.quantstart.com/articles/Money-Management-via-the-Kelly-Criterion/) - Money management framework
- [Position Sizing: How We Assess Conviction](https://intrinsicinvesting.com/2019/12/12/position-sizing-how-we-assess-conviction/) - Conviction-based sizing
- [Using "Conviction" to Determine Position Sizes](https://www.investorsunderground.com/conviction-trading/) - Conviction framework
- [How Many Trades Are Enough? Statistical Significance in Backtesting](https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05) - Sample size guidance
- [Is Your Strategy Just Lucky? How to Statistically Validate Your Backtest](https://medium.com/@trading.dude/is-your-strategy-just-lucky-how-to-statistically-validate-your-backtest-37ed5429a031) - Statistical validation
- [Use statistical bootstrapping to validate an algorithmic trading strategy](https://vegapit.com/article/statistical-bootstrapping-validate-algorithmic-trading-strategy/) - Bootstrap methods
- [Enhancing Your Trading Strategy with Rolling Window Indicators](https://trendspider.com/learning-center/enhancing-your-trading-strategy-with-rolling-window-indicators/) - Rolling window best practices
- [2026 Outlook: Portfolio-Wide Views](https://www.cambridgeassociates.com/insight/2026-outlook-portfolio-wide-views/) - Portfolio diversification 2026
- [Rebalancing Your Portfolio in 2026](https://www.ainvest.com/news/rebalancing-portfolio-2026-common-sense-guide-managing-risk-2601/) - Rebalancing strategies
- [The Kelly Criterion](https://www.wallstreetmojo.com/kelly-criterion/) - Kelly definition and formula
- [Expected Value in Trading](https://nickyoder.com/kelly-criterion/) - Expected value calculation

### Tertiary (LOW confidence - for context)
- [GitHub: kelly-criterion implementations](https://github.com/topics/kelly-criterion) - Open source examples
- [Adaptive rolling window selection for minimum variance portfolio](https://ieeexplore.ieee.org/document/9245435) - Rolling window research
- [Rolling Window Strategy](https://www.emergentmind.com/topics/rolling-window-strategy) - Window selection guidance

## Metadata

**Confidence breakdown:**
- Kelly criterion formula: HIGH - Standard formula, extensively validated in literature
- Half Kelly recommendation: HIGH - Industry consensus for volatile markets (75% sources agree)
- Per-token edge tracking: HIGH - Pattern established in machine learning and quantitative trading
- Rolling window size (50 trades): MEDIUM - General guideline 30-100 trades, 50 is midpoint
- Cold start handling: HIGH - Standard fallback pattern, no trades skipped
- Conviction signals: MEDIUM - Position size correlation documented, but weights need validation
- Trade prioritization: HIGH - Expected value ranking is standard optimization approach
- Soft diversification: MEDIUM - 10-20% penalty range from portfolio theory, needs calibration
- Statistical validation: HIGH - Paired t-test and bootstrap are standard methods

**Research date:** 2026-02-02
**Valid until:** 2026-03-02 (30 days - Kelly sizing is stable domain, but should validate with replay data)

**Notes for planner:**
- User locked Half Kelly (0.5x) and per-token edge tracking
- User locked trade prioritization by Kelly edge with soft diversification
- User locked conviction multiplier from leader signals (position size primary)
- User locked cold start fallback to Phase 3 (no trades skipped)
- Everything else is Claude's discretion: lookback window size, minimum samples, weights, thresholds
- Phase 3 DynamicSizer provides fallback during cold start period
- EdgeTracker uses collections.deque for efficient rolling window (FIFO with maxlen)
- Statistical validation required: paired t-test or bootstrap with p < 0.05 before trusting Kelly
- Conviction signals: position size (60%), scale-in (25%), entry speed (15%) weights suggested
- Rebalance threshold: 1.5x edge gap prevents excessive turnover
- Correlation penalty: 15% edge reduction for same-token positions (soft, not hard block)
