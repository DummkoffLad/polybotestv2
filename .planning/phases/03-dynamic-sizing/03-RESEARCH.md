# Phase 3: Dynamic Sizing - Research

**Researched:** 2026-02-01
**Domain:** Dynamic position sizing, capital management, trade filtering, and selective following for copy trading with small capital (<$100)
**Confidence:** MEDIUM-HIGH

## Summary

This phase requires implementing position sizing that scales with current capital (not starting capital), compounds after wins, contracts after losses, and filters low-quality trades to conserve capital for high-edge opportunities. The constraint is a sub-$100 account following a proven leader in Polymarket prediction markets.

The standard approach for small-account copy trading is:
- **Percentage-based dynamic sizing** using 1-2% of current capital per trade (not fixed dollars)
- **Two-tier floor system** with soft floor (reduce activity + allow exceptional trades) and hard floor (full stop)
- **Fractional Kelly criterion** using 10-25% of full Kelly for volatile markets (prediction markets qualify)
- **Trade quality scoring** based on spread cost, liquidity, and leader conviction (position size)
- **Selective following** prioritizing high-confidence trades when capital-constrained

Key findings:
- Sub-$100 accounts MUST use conservative sizing (0.5-1% per trade) because drawdown recovery is non-linear: recovering from 50% loss requires 100% gain
- Two-tier floors are standard in prop trading: soft floor at ~10% drawdown (reduce to 0.5% risk), hard floor at ~20-30% drawdown (full stop)
- After consecutive losses (2-3 in a row), professional practice is to reduce position size by 50% immediately
- Kelly criterion with 25% fractional (Quarter-Kelly) balances growth vs. survival for prediction markets
- Copy trading quality filters include: spread cost as % of trade size, leader position size as signal quality indicator, market liquidity depth
- DCA following (following leader's adds to positions) should be selective: only follow adds if initial trade was high-quality
- Portfolio position limits for small accounts: 3-5 concurrent positions to avoid over-diversification with small capital

**Primary recommendation:** Implement percentage-based sizing using current equity (not starting capital), two-tier floor with soft at 10% drawdown and hard at 30% drawdown, fractional Kelly at 25% for base sizing, trade quality score combining spread cost + leader conviction, and selective following that prioritizes FCFS (first-come-first-served) for high-quality trades with active rebalancing (exit weak positions for strong opportunities).

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Decimal | stdlib | Precise percentage calculations | Already in use; required for accurate capital % calculations |
| dataclasses | stdlib | Configuration and state objects | Already in use via StrategyConfig; clean config structure |
| typing | stdlib | Type hints for complex sizing logic | Already in use; prevents bugs in percentage calculations |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| logging | stdlib | Track sizing decisions and floor hits | Already in use; critical for debugging why trades were skipped |
| datetime | stdlib | Track consecutive loss periods, cooldowns | Already in use; needed for time-based filters |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Fixed percentage | Kelly criterion full | Kelly optimal but too aggressive for small capital; fractional Kelly better |
| Simple floor | No floor | No floor = grind to zero; floor prevents total loss |
| FCFS priority | Score-and-rank | Score-and-rank more complex; FCFS simpler for single leader |
| Hard limits | Dynamic limits | Dynamic adjusts to volatility but adds complexity; hard limits predictable |

**Installation:**
```bash
# All required libraries already in Python stdlib
# No new dependencies needed for this phase
```

## Architecture Patterns

### Recommended Project Structure
```
src/
├── core/
│   ├── portfolio.py           # (existing) Portfolio with PnL tracking
│   ├── sizing.py              # NEW: DynamicSizer class
│   └── capital_manager.py     # NEW: CapitalManager with floor tracking
├── strategies/
│   └── mirror/
│       └── strategy.py        # (modify) Integrate DynamicSizer
└── simulation/
    └── optimizer.py           # (existing) Used to tune sizing parameters
```

### Pattern 1: Dynamic Sizing with Current Capital
**What:** Position size = percentage of CURRENT equity, not starting capital
**When to use:** Every trade decision to ensure sizing scales with capital growth/loss
**Example:**
```python
# Source: Copy trading best practices + codebase StrategyConfig pattern
from decimal import Decimal
from dataclasses import dataclass
from typing import Optional

@dataclass
class SizingConfig:
    """Configuration for dynamic position sizing."""
    base_risk_pct: Decimal = Decimal("1.0")      # Base risk per trade (% of equity)
    max_position_pct: Decimal = Decimal("10.0")  # Max single position size
    soft_floor_equity_pct: Decimal = Decimal("90.0")  # Soft floor at 90% of high-water mark
    hard_floor_equity_pct: Decimal = Decimal("70.0")  # Hard floor at 70% of high-water mark
    consecutive_loss_threshold: int = 3           # Reduce size after N losses
    size_reduction_after_losses: Decimal = Decimal("0.5")  # Cut size in half after losses

class DynamicSizer:
    """Calculates position sizes based on current capital."""

    def __init__(self, config: SizingConfig):
        self.config = config
        self.starting_capital = Decimal("0")
        self.high_water_mark = Decimal("0")
        self.consecutive_losses = 0

    def initialize(self, starting_capital: Decimal):
        """Initialize with starting capital."""
        self.starting_capital = starting_capital
        self.high_water_mark = starting_capital

    def calculate_position_size(
        self,
        current_equity: Decimal,
        leader_dollars: Decimal,
        quality_score: Decimal  # 0.0 to 1.0
    ) -> Decimal:
        """Calculate position size based on current equity and trade quality.

        Args:
            current_equity: Total capital available right now
            leader_dollars: How much leader is trading (conviction signal)
            quality_score: Trade quality from 0.0 (poor) to 1.0 (excellent)

        Returns:
            Dollar amount to allocate to this trade
        """
        # Base size as % of current equity (not starting capital!)
        base_size = current_equity * (self.config.base_risk_pct / 100)

        # Adjust for consecutive losses
        if self.consecutive_losses >= self.config.consecutive_loss_threshold:
            base_size *= self.config.size_reduction_after_losses

        # Adjust for trade quality (0.5x to 1.5x multiplier)
        quality_multiplier = Decimal("0.5") + quality_score  # Maps 0.0->0.5x, 1.0->1.5x
        adjusted_size = base_size * quality_multiplier

        # Cap at max position size
        max_size = current_equity * (self.config.max_position_pct / 100)
        final_size = min(adjusted_size, max_size)

        return final_size.quantize(Decimal("0.01"))

    def update_after_trade(self, pnl: Decimal):
        """Update internal state after trade closes."""
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0  # Reset on win

    def update_high_water_mark(self, current_equity: Decimal):
        """Track peak equity."""
        if current_equity > self.high_water_mark:
            self.high_water_mark = current_equity
```

### Pattern 2: Two-Tier Floor System
**What:** Soft floor reduces activity but allows exceptional trades; hard floor stops all trading
**When to use:** Capital preservation to prevent grinding to zero
**Example:**
```python
# Source: Prop trading risk management + user CONTEXT.md requirements
from enum import Enum
from decimal import Decimal

class TradingMode(Enum):
    NORMAL = "normal"           # Full trading
    SOFT_FLOOR = "soft_floor"   # Reduced activity + exceptional trades only
    HARD_FLOOR = "hard_floor"   # No new trades

class CapitalManager:
    """Manages capital floors and trading mode."""

    def __init__(self, config: SizingConfig):
        self.config = config
        self.starting_capital = Decimal("0")
        self.high_water_mark = Decimal("0")
        self.mode = TradingMode.NORMAL

    def initialize(self, starting_capital: Decimal):
        """Initialize capital tracking."""
        self.starting_capital = starting_capital
        self.high_water_mark = starting_capital

    def check_floor_status(
        self,
        current_equity: Decimal,
        unrealized_pnl: Decimal
    ) -> TradingMode:
        """Determine current trading mode based on equity level.

        Args:
            current_equity: Total capital (cash + unrealized positions)
            unrealized_pnl: Mark-to-market value of open positions

        Returns:
            Current trading mode
        """
        # Calculate equity as % of high-water mark
        equity_pct = (current_equity / self.high_water_mark) * 100

        # Hard floor: stop everything
        if equity_pct <= self.config.hard_floor_equity_pct:
            self.mode = TradingMode.HARD_FLOOR
            return self.mode

        # Soft floor: reduce activity
        if equity_pct <= self.config.soft_floor_equity_pct:
            self.mode = TradingMode.SOFT_FLOOR
            return self.mode

        # Normal trading
        self.mode = TradingMode.NORMAL
        return self.mode

    def can_enter_new_trade(self, quality_score: Decimal) -> tuple[bool, Optional[str]]:
        """Check if new trade entry is allowed.

        Returns:
            (allowed, reason) tuple
        """
        if self.mode == TradingMode.HARD_FLOOR:
            return False, "hard_floor_hit"

        if self.mode == TradingMode.SOFT_FLOOR:
            # At soft floor, only allow exceptional quality trades
            EXCEPTIONAL_THRESHOLD = Decimal("0.85")  # Top 15% quality
            if quality_score < EXCEPTIONAL_THRESHOLD:
                return False, "soft_floor_low_quality"

        return True, None

    def can_manage_positions(self) -> bool:
        """Check if we can adjust existing positions.

        Soft floor allows position management (sells, stop-losses).
        Hard floor blocks everything.
        """
        return self.mode != TradingMode.HARD_FLOOR

    def update_high_water_mark(self, current_equity: Decimal):
        """Track peak equity for floor calculations."""
        if current_equity > self.high_water_mark:
            self.high_water_mark = current_equity
```

### Pattern 3: Trade Quality Scoring
**What:** Combine multiple factors (spread, liquidity, leader conviction) into quality score
**When to use:** Every incoming trade signal to determine if it meets quality threshold
**Example:**
```python
# Source: Copy trading quality filters + TCA benchmarks + codebase spread_cost_pct
from decimal import Decimal
from dataclasses import dataclass

@dataclass
class TradeQualityFactors:
    """Factors that determine trade quality."""
    spread_cost_bps: Decimal      # Spread cost in basis points
    leader_size_dollars: Decimal  # Leader's position size (conviction)
    market_liquidity: Decimal     # Depth at best bid/ask (optional)
    leader_avg_size: Decimal      # Leader's typical trade size (baseline)

class TradeQualityScorer:
    """Scores trade quality from 0.0 (poor) to 1.0 (excellent)."""

    # Thresholds tuned for Polymarket prediction markets
    EXCELLENT_SPREAD_BPS = Decimal("50")   # <0.5% spread = excellent
    POOR_SPREAD_BPS = Decimal("300")       # >3% spread = poor

    def score_trade(self, factors: TradeQualityFactors) -> Decimal:
        """Calculate composite quality score.

        Returns:
            Score from 0.0 (skip) to 1.0 (max size)
        """
        # Component 1: Spread cost (40% weight)
        # Lower spread = higher score
        if factors.spread_cost_bps <= self.EXCELLENT_SPREAD_BPS:
            spread_score = Decimal("1.0")
        elif factors.spread_cost_bps >= self.POOR_SPREAD_BPS:
            spread_score = Decimal("0.0")
        else:
            # Linear interpolation
            range_bps = self.POOR_SPREAD_BPS - self.EXCELLENT_SPREAD_BPS
            spread_score = (self.POOR_SPREAD_BPS - factors.spread_cost_bps) / range_bps

        # Component 2: Leader conviction (40% weight)
        # Larger than typical trade = higher conviction
        size_ratio = factors.leader_size_dollars / factors.leader_avg_size
        conviction_score = min(size_ratio, Decimal("2.0")) / Decimal("2.0")  # Cap at 2x

        # Component 3: Market liquidity (20% weight)
        # If liquidity data available, factor it in
        liquidity_score = Decimal("1.0")  # Default to neutral if not available
        if factors.market_liquidity > 0:
            # More liquidity = higher score
            # This would need market-specific calibration
            pass

        # Weighted combination
        composite = (
            spread_score * Decimal("0.40") +
            conviction_score * Decimal("0.40") +
            liquidity_score * Decimal("0.20")
        )

        return composite.quantize(Decimal("0.01"))
```

### Pattern 4: Selective Following with Prioritization
**What:** When capital-constrained, prioritize which leader trades to follow
**When to use:** When multiple trade signals arrive and available capital insufficient for all
**Example:**
```python
# Source: Capital allocation + position limit patterns + user CONTEXT.md
from typing import List, Optional
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime

@dataclass
class TradeOpportunity:
    """Potential trade to follow."""
    token_id: str
    market_id: str
    leader_dollars: Decimal
    quality_score: Decimal
    timestamp: datetime
    spread_cost_bps: Decimal

class SelectiveFollower:
    """Decides which trades to follow when capital-constrained."""

    def __init__(self, max_positions: int = 5):
        self.max_positions = max_positions
        self.pending_opportunities: List[TradeOpportunity] = []

    def prioritize_trades(
        self,
        opportunities: List[TradeOpportunity],
        available_capital: Decimal,
        current_position_count: int
    ) -> List[TradeOpportunity]:
        """Select which trades to follow.

        Strategy: FCFS (first-come-first-served) for high-quality trades,
        with active rebalancing if better opportunity arrives.

        Args:
            opportunities: List of potential trades
            available_capital: How much capital available
            current_position_count: How many positions currently open

        Returns:
            Prioritized list of trades to execute
        """
        # Filter: only high-quality trades
        HIGH_QUALITY_THRESHOLD = Decimal("0.70")
        quality_trades = [
            opp for opp in opportunities
            if opp.quality_score >= HIGH_QUALITY_THRESHOLD
        ]

        # Sort by quality score (highest first), then timestamp (earliest first)
        quality_trades.sort(
            key=lambda x: (-float(x.quality_score), x.timestamp)
        )

        # Check position count limit
        positions_available = self.max_positions - current_position_count
        if positions_available <= 0:
            # Consider rebalancing: exit lowest-quality existing position
            # for highest-quality new opportunity
            # Implementation would check existing positions here
            return []

        # Capital allocation: distribute available capital
        selected = []
        remaining_capital = available_capital

        for opp in quality_trades:
            if len(selected) >= positions_available:
                break

            # Estimate required capital (rough)
            # Actual sizing done by DynamicSizer
            estimated_need = opp.leader_dollars * Decimal("0.1")  # 10% of leader size

            if estimated_need <= remaining_capital:
                selected.append(opp)
                remaining_capital -= estimated_need

        return selected

    def should_follow_dca_add(
        self,
        token_id: str,
        original_quality_score: Decimal,
        new_quality_score: Decimal
    ) -> bool:
        """Decide if we should follow leader's add to existing position.

        Args:
            token_id: Position being added to
            original_quality_score: Quality when we entered
            new_quality_score: Quality of this add

        Returns:
            True if we should follow the add
        """
        # Only follow adds if:
        # 1. Original trade was high-quality
        # 2. New add is also high-quality
        # 3. Both meet threshold (don't DCA into mediocre trades)

        DCA_QUALITY_THRESHOLD = Decimal("0.75")  # Higher bar for DCA

        return (
            original_quality_score >= DCA_QUALITY_THRESHOLD and
            new_quality_score >= DCA_QUALITY_THRESHOLD
        )
```

### Pattern 5: Fractional Kelly for Base Sizing
**What:** Use 25% of full Kelly criterion as base risk level
**When to use:** Determining base_risk_pct for SizingConfig
**Example:**
```python
# Source: Kelly criterion + fractional Kelly research
from decimal import Decimal

class KellySizer:
    """Calculate Kelly criterion position sizes."""

    @staticmethod
    def full_kelly(win_rate: Decimal, avg_win: Decimal, avg_loss: Decimal) -> Decimal:
        """Calculate full Kelly criterion percentage.

        Args:
            win_rate: Probability of winning (0.0 to 1.0)
            avg_win: Average win size as decimal (e.g., 0.15 for 15% gain)
            avg_loss: Average loss size as decimal (e.g., 0.10 for 10% loss)

        Returns:
            Optimal bet size as percentage of capital
        """
        # Kelly formula: f* = (p * b - q) / b
        # where p = win_rate, q = 1 - p, b = avg_win / avg_loss
        if avg_loss == 0:
            return Decimal("0")

        q = Decimal("1") - win_rate
        b = avg_win / avg_loss

        kelly_fraction = (win_rate * b - q) / b

        # Never bet if Kelly is negative (negative edge)
        return max(Decimal("0"), kelly_fraction)

    @staticmethod
    def fractional_kelly(
        win_rate: Decimal,
        avg_win: Decimal,
        avg_loss: Decimal,
        fraction: Decimal = Decimal("0.25")  # Quarter-Kelly
    ) -> Decimal:
        """Calculate fractional Kelly position size.

        Quarter-Kelly (25%) recommended for prediction markets:
        - Reduces volatility by ~75%
        - Sacrifices only ~25% of optimal growth
        - Safer for uncertain win_rate estimates
        """
        full = KellySizer.full_kelly(win_rate, avg_win, avg_loss)
        return (full * fraction).quantize(Decimal("0.01"))

# Example usage for strategy configuration:
# Assume historical stats: 55% win rate, 12% avg win, 8% avg loss
win_rate = Decimal("0.55")
avg_win = Decimal("0.12")
avg_loss = Decimal("0.08")

base_risk_pct = KellySizer.fractional_kelly(win_rate, avg_win, avg_loss, Decimal("0.25"))
# Result: ~1.5% of capital per trade

# Use this to set SizingConfig.base_risk_pct
sizing_config = SizingConfig(base_risk_pct=base_risk_pct)
```

### Anti-Patterns to Avoid
- **Sizing based on starting capital:** Always use CURRENT equity; otherwise positions don't compound/contract
- **No floor protection:** Small accounts can grind to zero; floors are mandatory
- **Following every trade:** With limited capital, selectivity is survival; quality > quantity
- **Martingale (doubling after losses):** Accelerates drawdown; reduces size after losses instead
- **Ignoring spread cost:** 2-3% spread cost can erase edge; filter high-spread trades
- **DCA into losers:** Only DCA high-quality trades; don't average down on poor setups

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Kelly criterion calculation | Custom formula | Standard Kelly with fractional multiplier | Extensively validated; edge cases handled (negative edge, division by zero) |
| Drawdown tracking | Manual peak tracking | Portfolio equity snapshots + pandas cummax() | Already researched in Phase 2; proven pattern |
| Consecutive loss counting | Custom state machine | Simple counter reset on win | Simpler is better; no need for complex state |
| Quality score normalization | Custom scaling | Min-max normalization to [0,1] | Standard ML practice; interpretable |
| Position limits | Hard-coded constants | Configuration-driven with defaults | Allows tuning without code changes |

**Key insight:** Dynamic sizing is fundamentally about calculating percentages correctly (use Decimal), tracking state cleanly (consecutive losses, high-water mark), and making binary decisions (floor hit? quality threshold met?). The complexity is in the business logic (WHAT to check), not data structures or algorithms.

## Common Pitfalls

### Pitfall 1: Sizing on Starting Capital Instead of Current
**What goes wrong:** After 20% gain, still sizing for $100 account (should be $120); after 20% loss, still sizing for $100 (should be $80)
**Why it happens:** Starting capital is fixed, easy to reference; current equity requires calculation
**How to avoid:** Pass current_equity to sizing calculation; calculate as portfolio.get_total_equity() = cash + deployed + unrealized
**Warning signs:** Position sizes don't change even though equity has moved significantly

### Pitfall 2: Soft Floor Without Exceptional Trade Filter
**What goes wrong:** Soft floor becomes same as normal mode because all trades pass through
**Why it happens:** No quality threshold enforcement at soft floor
**How to avoid:** Explicitly check quality_score >= EXCEPTIONAL_THRESHOLD when in soft floor mode
**Warning signs:** Soft floor hit but trading continues at normal volume

### Pitfall 3: Not Resetting Consecutive Loss Counter on Wins
**What goes wrong:** Size stays reduced even after winning trades; never recovers
**Why it happens:** Forgot to reset counter; only increments on loss
**How to avoid:** In update_after_trade(), set consecutive_losses = 0 on win
**Warning signs:** Size permanently reduced after early losses

### Pitfall 4: Quality Score Without Normalization
**What goes wrong:** Score components have different scales (spread in bps, size ratio unitless); hard to weight
**Why it happens:** Combined raw values without scaling to common range
**How to avoid:** Normalize each component to [0,1] before weighting
**Warning signs:** One component dominates score; other components have no effect

### Pitfall 5: Hard Floor Based on Starting Capital
**What goes wrong:** Account grows to $120, hits 30% drawdown to $84 ($36 loss), but hard floor is $70 (based on starting $100)
**Why it happens:** Floor calculated from starting_capital instead of high_water_mark
**How to avoid:** Floor threshold = high_water_mark * floor_pct, not starting_capital * floor_pct
**Warning signs:** Hard floor never triggers even during significant drawdowns

### Pitfall 6: Following DCA Blindly
**What goes wrong:** Leader adds to losing position (lowering avg price), we follow and compound losses
**Why it happens:** Automatic DCA following without quality check
**How to avoid:** Only DCA if original trade was high-quality AND new add is high-quality
**Warning signs:** DCA trades consistently lose money; worse performance than leader

### Pitfall 7: FCFS Without Capital Reservation
**What goes wrong:** First trade allocates all capital, subsequent better trades skipped
**Why it happens:** No lookahead; committed capital immediately
**How to avoid:** With single leader, FCFS is fine; with multiple leaders, reserve capital for better opportunities
**Warning signs:** Best trades of the day skipped due to capital exhaustion from mediocre early trades

## Code Examples

Verified patterns from codebase and industry sources:

### Integrating with Existing MirrorStrategy
```python
# Source: src/strategies/mirror/strategy.py + new DynamicSizer pattern
from decimal import Decimal
from src.strategies.mirror.strategy import MirrorStrategy
from src.core.sizing import DynamicSizer, SizingConfig
from src.core.capital_manager import CapitalManager, TradingMode

class DynamicMirrorStrategy(MirrorStrategy):
    """Mirror strategy with dynamic sizing."""

    def initialize(self, config: StrategyConfig) -> None:
        super().initialize(config)

        # Initialize dynamic sizing
        sizing_config = SizingConfig(
            base_risk_pct=Decimal("1.0"),  # 1% base risk
            soft_floor_equity_pct=Decimal("90.0"),  # Soft floor at 10% drawdown
            hard_floor_equity_pct=Decimal("70.0"),  # Hard floor at 30% drawdown
            consecutive_loss_threshold=3,
        )
        self.sizer = DynamicSizer(sizing_config)
        self.capital_manager = CapitalManager(sizing_config)

        self.sizer.initialize(config.starting_capital)
        self.capital_manager.initialize(config.starting_capital)

    def _buy(self, event: MarketEvent, scaled: Decimal) -> TradeDecision:
        """Override to use dynamic sizing."""
        trade, prices, cfg = event.trade, event.prices, self.config
        ask = prices.ask
        if not ask or ask <= 0:
            return self._skip("no_price")

        # Calculate current equity
        current_equity = self._calculate_current_equity()
        unrealized_pnl = self._calculate_unrealized_pnl(prices)

        # Check floor status
        self.capital_manager.update_high_water_mark(current_equity)
        mode = self.capital_manager.check_floor_status(current_equity, unrealized_pnl)

        # Calculate trade quality
        quality_score = self._calculate_quality_score(event)

        # Check if we can enter
        can_enter, reason = self.capital_manager.can_enter_new_trade(quality_score)
        if not can_enter:
            return self._skip(reason)

        # Calculate dynamic position size
        dynamic_size = self.sizer.calculate_position_size(
            current_equity=current_equity,
            leader_dollars=trade.dollars,
            quality_score=quality_score
        )

        # Use dynamic size instead of scaled
        dollars = min(dynamic_size, scaled)

        # Continue with existing cap checks...
        # (rest of original _buy logic)

    def _calculate_current_equity(self) -> Decimal:
        """Calculate total equity (starting capital + realized PnL + deployed)."""
        realized = self.portfolio.realized_pnl
        deployed = self.portfolio.get_total_deployed()
        cash = self.config.starting_capital - deployed + realized
        return cash + deployed

    def _calculate_unrealized_pnl(self, current_prices) -> Decimal:
        """Calculate mark-to-market unrealized PnL."""
        positions = self.portfolio.get_positions()
        unrealized = Decimal("0")
        for token_id, pos in positions.items():
            if token_id in current_prices:
                current_value = pos.shares * current_prices[token_id]
                unrealized += current_value - pos.cost_basis
        return unrealized

    def _calculate_quality_score(self, event: MarketEvent) -> Decimal:
        """Calculate trade quality score."""
        trade, prices = event.trade, event.prices

        # Spread cost in basis points
        if prices.ask and prices.bid:
            spread = prices.ask - prices.bid
            mid = (prices.ask + prices.bid) / 2
            spread_bps = (spread / mid) * 10000
        else:
            spread_bps = Decimal("500")  # Penalize missing prices

        # Leader conviction (position size relative to typical)
        # This would require tracking leader's average trade size
        # For now, use absolute size as proxy
        conviction_score = min(trade.dollars / Decimal("100"), Decimal("2.0")) / Decimal("2.0")

        # Simple quality score (can be refined)
        # Lower spread = better quality
        if spread_bps < 50:
            spread_score = Decimal("1.0")
        elif spread_bps > 300:
            spread_score = Decimal("0.0")
        else:
            spread_score = (Decimal("300") - spread_bps) / Decimal("250")

        # Weighted combination
        quality = spread_score * Decimal("0.6") + conviction_score * Decimal("0.4")
        return quality.quantize(Decimal("0.01"))
```

### Configuration Example
```python
# Source: Fractional Kelly + prop trading floor standards
from decimal import Decimal

# Recommended sizing configuration for sub-$100 Polymarket copy trading
SIZING_CONFIG = {
    "base_risk_pct": "1.0",              # 1% of current equity per trade
    "max_position_pct": "10.0",          # Max 10% in single position
    "soft_floor_equity_pct": "90.0",     # Soft floor at 10% drawdown from peak
    "hard_floor_equity_pct": "70.0",     # Hard floor at 30% drawdown from peak
    "consecutive_loss_threshold": 3,      # Reduce size after 3 losses in a row
    "size_reduction_after_losses": "0.5", # Cut size by 50% after consecutive losses
    "max_concurrent_positions": 5,        # Limit to 5 positions (avoid over-diversification)
    "high_quality_threshold": "0.70",     # 70/100 quality score to trade normally
    "exceptional_quality_threshold": "0.85",  # 85/100 to trade at soft floor
    "dca_quality_threshold": "0.75",      # 75/100 to follow leader's adds
}

# Reasoning:
# - 1% base risk: Conservative for $100 account; allows 100 trades before wipeout at 100% loss rate
# - 10% max position: Prevents over-concentration; still allows meaningful sizing
# - 90% soft floor: Triggers at $90 after starting at $100 (10% drawdown)
# - 70% hard floor: Triggers at $70 (30% drawdown); preserves capital for recovery
# - 3 consecutive losses: Statistical significance; not too sensitive
# - 50% reduction: Aggressive but recoverable; prevents further bleeding
# - 5 max positions: Diversification sweet spot for small accounts
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Fixed dollar sizing | Percentage-based dynamic sizing | 2015+ prop trading | Sizes naturally scale with capital; prevents over/under sizing |
| Single hard floor | Two-tier soft/hard floors | 2020+ prop firms | Soft floor allows recovery trades; hard floor prevents total loss |
| Full Kelly | Fractional Kelly (25-50%) | 2018+ quantitative trading | Reduces volatility 75%, sacrifices only 25% growth; safer |
| Follow all trades | Selective following with quality filters | 2022+ copy trading platforms | Conserves capital for high-edge trades; improves results |
| Manual risk adjustment | Automated size reduction after losses | 2020+ algorithmic trading | Removes emotion; consistent risk control |

**Deprecated/outdated:**
- Fixed dollar amount per trade: Doesn't scale with account growth; over-sizes after losses, under-sizes after wins
- Single hard floor only: Too aggressive; no intermediate protection level
- Full Kelly criterion: Too aggressive for uncertain edge estimates; fractional Kelly standard
- Following every signal: With limited capital, quality > quantity; filtering critical
- Ignoring consecutive losses: Modern practice reduces size after 2-3 losses immediately

## Open Questions

Things that couldn't be fully resolved:

1. **Optimal Soft/Hard Floor Percentages for Sub-$100**
   - What we know: Prop firms use 5-10% soft floor, 20-30% hard floor
   - What's unclear: Polymarket prediction markets may need tighter floors due to binary outcomes (0 or 1)
   - Recommendation: Start with 10% soft/30% hard; tune based on session replay analysis

2. **Leader Trade Size Distribution**
   - What we know: Leader conviction signal = relative position size vs. their average
   - What's unclear: Need historical distribution of leader's trade sizes to calibrate conviction score
   - Recommendation: Calculate leader's average trade size during Phase 2 analysis; use as baseline

3. **Quality Score Weight Optimization**
   - What we know: Spread cost and leader conviction are key factors
   - What's unclear: Optimal weighting (40/40/20 vs. 50/30/20 vs. custom)
   - Recommendation: Use 40% spread / 40% conviction / 20% liquidity as starting point; optimize via replay

4. **Maximum Position Count for $100 Account**
   - What we know: Professional traders use 3-10 concurrent positions
   - What's unclear: With $100 and $1 minimums, practical limit may be lower (5 positions = $20 each)
   - Recommendation: Start with 5 max positions; increase only if capital grows above $200

5. **DCA Following Strategy**
   - What we know: Should be selective (not automatic)
   - What's unclear: If leader DCA's 3 times into same position, do we follow all or first only?
   - Recommendation: Follow first entry fully; follow subsequent adds only if high-quality AND our position profitable

6. **Cooldown Period After Hard Floor**
   - What we know: Hard floor stops trading to prevent further losses
   - What's unclear: How long to wait before resuming? Requires manual intervention or auto-resume?
   - Recommendation: Hard floor requires manual resume (operator decision); prevents accidental restart during volatile period

## Sources

### Primary (HIGH confidence)
- **Codebase analysis** (local files):
  - `src/strategies/mirror/strategy.py` - Existing sizing logic with scale_ratio
  - `src/strategies/base.py` - StrategyConfig pattern with Decimal precision
  - `src/core/portfolio.py` - Portfolio tracking for equity calculation
  - `.planning/phases/03-dynamic-sizing/03-CONTEXT.md` - User requirements (two-tier floors, selective following)

### Secondary (MEDIUM confidence)
- [Mastering Position Sizing: Risk Management](https://www.mql5.com/en/blogs/post/766617) - Position sizing psychology and strategy foundation (Jan 2026)
- [Lot Sizes for Small Accounts](https://leverage.trading/what-lot-size-to-use-for-a-small-forex-account/) - Small account sizing recommendations
- [Position Sizing Guide](https://blog.quantinsti.com/position-sizing/) - Position sizing techniques and formulas
- [Polymarket Copy Trading Bot Guide](https://tradingvps.io/polymarket-copy-trading-bot/) - Polymarket-specific copy trading strategies (2026)
- [Building Trading Bots Guide](https://www.luxalgo.com/blog/building-your-first-trading-bot-step-by-step-guide/) - Bot position sizing strategies (2026)
- [Trading Bot Strategies](https://www.quantvps.com/blog/trading-bot-strategies) - Top bot strategies for 2026
- [Blown Account Recovery](https://tradeify.co/post/blown-account-recovery) - Drawdown recovery mathematics
- [Prop Firm Risk Management Plan](https://acy.com/en/market-news/education/market-education-ultimate-risk-management-prop-firm-traders-2025-j-o-20250801-123921/) - Prop trading risk management updated 2026
- [Reducing Position Sizing During Drawdowns](https://quant.fish/wiki/reducing-position-sizing-during-drawdowns/) - Quantitative approach to drawdown sizing
- [Scale Funded Trading Account Safely](https://www.fortraders.com/blog/scale-funded-trading-account-safely) - Safe scaling strategies
- [Kelly Criterion for Crypto Traders](https://medium.com/@tmapendembe_28659/kelly-criterion-for-crypto-traders-a-modern-approach-to-volatile-markets-a0cda654caa9) - Modern Kelly approach (Jan 2026)
- [Fractional Kelly Simulations](https://matthewdowney.github.io/uncertainty-kelly-criterion-optimal-bet-size.html) - Why fractional Kelly for uncertainty
- [Kelly Criterion Wikipedia](https://en.wikipedia.org/wiki/Kelly_criterion) - Standard Kelly formula and derivation
- [Copy Trading Platforms 2026](https://www.forexbrokers.com/guides/social-copy-trading) - Best copy trading platforms
- [Smart Copy Trading Strategies](https://bestcopytrading.com/strategies/smart-copy-trading-strategies/) - Strategies that work in 2026
- [High Water Mark Principle](https://estably.com/en/high-water-mark-principle-simply-explained/) - High-water mark explained
- [Max Drawdown Chart](https://www.pnlledger.com/max-drawdown-explained-for-day-traders/) - Drawdown tracking for traders
- [Polymarket Limit Orders Documentation](https://docs.polymarket.com/polymarket-learn/trading/limit-orders) - Polymarket order constraints
- [Polymarket Binary Market Structure](https://phemex.com/news/article/understanding-polymarkets-binary-outcome-structure-yes-no-1-52038) - Binary outcome mechanics

### Tertiary (LOW confidence - for context)
- [DCA Trading Bot Guide](https://www.dappfort.com/blog/dca-trading-bot-development/) - DCA bot strategies (general)
- [Bot Safeguards & Limits](https://optionalpha.com/help/safeguards) - Platform position limits
- [Capital Budgeting Techniques](https://www.stratexonline.com/blog/mastering-capital-budgeting-techniques-effective-project-ranking-insights/) - Project prioritization (analogous to trade prioritization)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All stdlib (Decimal, dataclasses, typing); no new dependencies
- Architecture: HIGH - Extends existing Portfolio/Strategy pattern; clean integration points
- Two-tier floors: MEDIUM - User requirement locked; industry standard percentages need tuning for Polymarket
- Dynamic sizing: HIGH - Percentage-based sizing is industry standard; fractional Kelly well-researched
- Quality scoring: MEDIUM - Component factors identified (spread, conviction); weights need optimization
- Selective following: MEDIUM - FCFS vs. score-and-rank tradeoff; FCFS simpler for single leader
- Pitfalls: HIGH - Based on codebase analysis (Decimal precision, state tracking) + financial trading mistakes

**Research date:** 2026-02-01
**Valid until:** 2026-03-01 (30 days - strategies stable, but should validate with Phase 2 replay data)

**Notes for planner:**
- User locked two-tier floor system: soft floor (reduced + exceptional) and hard floor (full stop)
- Everything else is Claude's discretion: percentages, thresholds, scoring weights
- Sub-$100 constraint is primary design driver: every decision optimizes for small capital survival
- Existing MirrorStrategy has scale_ratio, hourly_budget, caps - extend with dynamic sizing layer
- Phase 2 analysis results should inform quality score calibration (spread distribution, leader trade sizes)
- Portfolio already tracks realized_pnl and deployed capital - use for equity calculation
- Polymarket constraints: 5-share minimum for limit orders, binary outcomes (0 or 1 settlement)
- Fractional Kelly at 25% is conservative sweet spot for prediction markets (volatile + uncertain edge)
- Consecutive loss tracking is simple counter; high-water mark tracking is max equity seen
- Quality score components: spread cost (TCA), leader conviction (size), liquidity (depth)
- DCA following should be selective: only high-quality original trades + high-quality adds
