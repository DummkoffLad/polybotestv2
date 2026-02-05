# Profit Taker Strategy

**Status:** Active (outperforming baseline)
**Baseline:** conservative_mirror
**Performance:** +$16.30 vs +$4.32 (2 sessions)

## Overview

The profit_taker strategy copies the leader's buy signals but exits positions based on profit targets or following the leader's sell signal, whichever comes first. The core hypothesis is that we can capture the leader's entry signals while avoiding holding through drawdowns by taking profits earlier.

### Key Behaviors

1. **Copy leader BUYs** - Mirror entry signals with standard risk controls
2. **Dynamic profit targets** - Exit when unrealized profit reaches target percentage based on entry price
3. **Follow leader exits** - Sell when leader sells (unless it's a mini-sell/rebalancing)
4. **Extreme price protection** - Exit if price reaches 0.99 (ceiling)

## Parameters

### Profit Targets (Dynamic)

The strategy uses different profit targets based on entry price level. Lower-priced entries have more room to run, while higher-priced entries are closer to the $1 ceiling.

```python
PROFIT_TARGET_LOW = 35%   # Prices < $0.30 - let winners run
PROFIT_TARGET_MID = 20%   # Prices $0.30-$0.60 - moderate targets
PROFIT_TARGET_HIGH = 12%  # Prices > $0.60 - take profits earlier
```

**Rationale:** A token at $0.10 can 9x to $0.90 (900% gain), but a token at $0.80 can only reach $0.99 (24% max gain). Dynamic targets account for this ceiling effect.

### Trading Controls

```python
IGNORE_LEADER_MINISELLS_PCT = 10%  # Ignore sells < 10% of leader position
MIN_LEADER_TRADE_PCT = 1%          # Skip trades < 1% of leader capital
MAX_TOTAL_COST_PCT = 6%            # Max drift + spread + slippage
```

### Risk Limits

```python
CASH_RESERVE_PCT = 15%       # Keep 15% uninvested
PER_MARKET_CAP_PCT = 25%     # Max 25% per market
PER_SIDE_PCT = 20%           # Max 20% per side (UP or DOWN)
GLOBAL_EXPOSURE_PCT = 85%    # Max 85% total deployed
```

## Logic Flow

### On Every Event

1. **Check ALL positions for profit targets** (not just current token)
   - Uses `all_prices` context to get current prices for all held tokens
   - Compares current bid vs entry price
   - Exits if profit >= dynamic target for that price level

2. **Check extreme prices** (bid >= $0.99)
   - Auto-exit to capture near-maximum value

### On Leader BUY

3. **Validate price** - Ask must be valid (0 < ask < 1)
4. **Cost check** - Total costs (drift + spread + slippage) < 6%
5. **Calculate size** - Scale leader's dollars by our capital ratio
6. **Apply capacity limits:**
   - Available after cash reserve (15%)
   - Hourly budget remaining
   - Per-market cap (25%)
   - Per-side cap (20%)
   - Global exposure cap (85%)
7. **Execute BUY** - Place limit order at ask price
8. **Record entry price** - Track for profit target calculation

### On Leader SELL

9. **Check if we hold position** - Skip if no position
10. **Ignore mini-sells** - Leader selling < 10% is likely rebalancing
11. **Loss protection** - Don't sell at our loss if leader is selling at profit
12. **Follow the sell** - Scale leader's sell by our capital ratio
13. **Execute SELL** - Exit position at bid price
14. **Clear entry tracking** - Remove from tracking if fully exited

## Comparison vs Conservative Mirror

| Feature | Conservative Mirror | Profit Taker |
|---------|-------------------|--------------|
| **Entry logic** | Copy all leader buys | Copy all leader buys |
| **Exit logic** | Only follow leader sells | Profit targets OR leader sells |
| **Profit targets** | None | Dynamic (12-35%) based on entry price |
| **Mini-sell handling** | Ignore < 10% | Ignore < 10% |
| **Loss protection** | Yes (don't sell our loss if leader profitable) | Yes (same) |
| **Risk controls** | Standard | Slightly tighter (6% vs 8% cost threshold) |
| **Cash reserve** | 20% | 15% (more aggressive deployment) |

**Key difference:** Conservative mirror only exits when the leader exits. Profit taker exits earlier when hitting profit targets, attempting to "take profits while they're available" rather than riding through potential drawdowns.

## Current Performance

Based on 2 sessions (2026-02-03, 2026-02-04):

```
profit_taker:        +$16.30 total
conservative_mirror: +$4.32 total

Improvement: +$11.98 (+277%)
```

**Session breakdown:**
- Session 1 (2026-02-03 overnight): Both strategies profitable, profit_taker outperformed
- Session 2 (2026-02-04 daytime): Both strategies lost, but profit_taker lost less

**Key metrics:**
- More profit exits (taking gains earlier)
- Fewer drawdowns from holding through price declines
- Similar entry count (both copy leader buys)

## Known Limitations

1. **Small sample size:** Only 2 sessions - needs more data for statistical confidence
2. **Overfitting risk:** Dynamic profit targets optimized on same data - needs out-of-sample validation
3. **Market regime sensitivity:** Performance may vary in different volatility regimes
4. **Ceiling-constrained markets:** Only works for markets with clear price ceiling ($1.00)

## Next Steps

1. **Phase 8 (Failure Mode Analysis):** Track per-trade max unrealized profit to validate profit-taking timing
2. **Phase 10 (Validation):** Test on 30% holdout set to confirm edge is real
3. **Regime analysis:** Compare performance in overnight vs daytime, high vs low volatility
4. **Parameter sensitivity:** Verify current targets (35/20/12) are robust, not overfitted

## Implementation Notes

- Entry price tracked in `self.our_entries` dict (token_id -> Decimal)
- Profit check happens on EVERY event, not just when leader trades that token
- Uses `all_prices` from event context to get current prices for all tokens
- Clears entry tracking when position fully exited to avoid memory leak
- Counts profit exits separately in metrics (`self.profit_exits`)
