# Polymarket Copytrader Performance Analysis

## 🎯 Executive Summary

**Why you're losing money compared to the leader:**

Your copytrader has **structural disadvantages** that cause systematic underperformance:

1. **Latency (6-10s)** - You detect trades AFTER the leader already moved the market
2. **Spread Loss** - You PAY the spread on every trade; leader may EARN it as market maker  
3. **Market Orders** - You use market orders (takers); leader uses limit orders (makers)
4. **Unrealistic Simulation** - Your sim assumed 0.6% costs; reality is 3-5%

**Estimated Impact:** These factors combined can cause -40% to -100% underperformance vs leader returns.

---

## 🔴 Issues Identified & Fixes Applied

### Issue 1: Latency-Induced Adverse Selection (CRITICAL)

**Problem:** Blockchain detection takes 6-10 seconds. By the time you execute, the market has already moved against you.

**Fix Applied:**
- Added cost-aware execution check in `_execute_buy()` 
- New constants in `mirror_runner.py`:
  - `TOTAL_EXECUTION_COST_PCT = 3%` (spread + slippage)
  - `MAX_TOTAL_COST_PCT = 8%` (skip threshold)
- Trades are now skipped if total cost (price drift + execution) exceeds 8%

```python
# New check in _execute_buy():
if total_cost_pct > MAX_TOTAL_COST_PCT:
    self.stats.record_skip("cost_too_high")
    return False
```

### Issue 2: Unrealistic Simulation Costs

**Problem:** 
- Old defaults: `spread=0.5%, slippage=0.1%` = 0.6% total
- Reality: `spread=2-4%, slippage=1%, latency=1%` = 4-6% total

**Fix Applied:**
- Updated `sim_adapter.py` defaults:
  - `spread_pct: 2.5%` (was 0.5%)
  - `slippage_pct: 1.0%` (was 0.1%)
  - Added `latency_cost_pct: 1.0%` (new)

- Updated `config.yaml` simulation section with realistic values

### Issue 3: Price Drift Tolerance Too High

**Problem:** 
- Old setting: `max_buy_price_drift_pct: 15%`
- This allowed buying even when price moved 15% against you!

**Fix Applied:**
- Reduced to `max_buy_price_drift_pct: 5%` in `config.yaml`
- Reduced `max_spread_pct: 8%` (was 15%)

### Issue 4: Scaling Assumed Price = 0.5

**Problem:**
```python
# Old code assumed all prices are 0.5:
target_up_shares = target_up_dollars * 2  # Wrong!
```

At price 0.10: Should be 10 shares/dollar, not 2.
At price 0.90: Should be 1.1 shares/dollar, not 2.

**Fix Applied:**
- Updated `scaling.py` to accept actual prices:
```python
def compute_target(..., up_price=None, down_price=None):
    if up_price and up_price > Decimal("0"):
        target_up_shares = target_up_dollars / up_price
```

### Issue 5: Selling at a Loss When Leader Profited

**Problem:**
- When leader sells, they may be taking profit (sold above their entry)
- But due to our worse entry price, the SAME sell could be a LOSS for us
- Following this sell locks in our loss unnecessarily

**Fix Applied:**
- Added `LeaderPositionTracker` class to track leader's average entry price
- Added sell loss protection in `_execute_sell()`:
  - If WE would lose money AND leader profited → **BLOCK the sell**
  - If leader also took a loss → **ALLOW the sell** (follow their risk-cutting)
- New config option: `block_loss_sells_if_leader_profit: true`

```python
# Logic in _execute_sell():
if our_sell_price < our_avg_entry:  # We'd lose
    if leader_sell_price >= leader_avg_entry:  # Leader profited
        # BLOCK - don't lock in our loss when leader made money
        return False
    else:
        # Leader also losing - follow their risk management
        pass
```

**Example:**
- Leader bought at $0.40, we bought at $0.55 (due to latency)
- Leader sells at $0.50 → Leader: +25% profit
- If we sell at $0.50 → We: -9% loss
- **Result:** Sell blocked because leader profited but we'd lose

---

## 📊 Quantified Impact

| Issue | Per-Trade Cost | Impact on Returns |
|-------|---------------|-------------------|
| Latency (6-10s) | 1-3% | -15% to -30% annually |
| Spread (paying vs earning) | 2-4% | -20% to -40% annually |
| Price drift before execution | 0.5-2% | -5% to -15% annually |
| Market order slippage | 0.5-1% | -5% to -10% annually |

**Total:** You may be paying 4-10% MORE per trade than the leader.

---

## 🛠 Files Modified

1. **`src/strategy/mirror_runner.py`**
   - Added latency/cost constants and documentation
   - Added cost-aware execution check in `_execute_buy()`
   - Pass `leader_price` through the execution chain
   - Added `LeaderPositionTracker` class to track leader's avg entry prices
   - Added `LeaderPosition` dataclass for position tracking
   - Added sell loss protection in `_execute_sell()` - blocks sells where we'd lose but leader profited

2. **`src/strategy/burst_buffer.py`**
   - Added `leader_price` field to `BurstAction` dataclass

3. **`src/strategy/mirror_stats.py`**
   - Added `"cost_too_high"` skip reason
   - Added `"leader_profit_our_loss"` skip reason for blocked sells

4. **`src/execution/sim_adapter.py`**
   - Realistic default costs (2.5% spread, 1% slippage, 1% latency)
   - Updated docstrings explaining cost model

5. **`src/core/scaling.py`**
   - Updated `compute_target()` to accept actual prices
   - Fixed incorrect price=0.5 assumption

6. **`src/config.py`**
   - Added `block_loss_sells_if_leader_profit` option to SafetyConfig

7. **`config/config.yaml`**
   - Reduced `max_buy_price_drift_pct: 5%` (was 15%)
   - Reduced `max_spread_pct: 8%` (was 15%)
   - Added `block_loss_sells_if_leader_profit: true`
   - Updated simulation costs to realistic values

---

## 🎯 Recommendations for Further Improvement

### High Priority

1. **Use Limit Orders Instead of Market Orders**
   - Place limit orders at or near the leader's entry price
   - Accept that some trades won't fill (better than bad fills)
   - This alone could save 2-4% per trade

2. **Reduce Detection Latency**
   - Current: 6-10s via blockchain
   - Investigate mempool monitoring for ~1-2s detection
   - Every second of latency = ~0.1-0.3% worse price

3. **Choose Leaders Who Aren't Market Makers**
   - If the leader earns the spread and you pay it, you start every trade -4%
   - Look for leaders who use market orders (takers)

### Medium Priority

4. **Implement Spread-Based Order Sizing**
   - Wide spread (>4%) = smaller position
   - Narrow spread (<2%) = larger position
   - Risk-adjust based on execution cost

5. **Track Realized Slippage**
   - Log `expected_price` vs `actual_fill_price`
   - Use historical slippage to improve estimates

6. **Add Time-of-Day Analysis**
   - Some hours have better liquidity
   - Skip or reduce size during low-liquidity periods

### Low Priority

7. **Multi-Leader Diversification**
   - Don't rely on single leader
   - Diversify across leaders with different styles

8. **Machine Learning for Cost Prediction**
   - Train model on historical slippage data
   - Predict expected cost before each trade

---

## 🧪 Testing

Run the test suite to verify changes:
```bash
python -m pytest tests/ -v --tb=short
```

Expected: **35 passed** ✅

---

## 📝 Summary

The core insight is: **Copy trading is structurally disadvantaged**.

The leader gets the best prices (first mover advantage). You get whatever's left after:
- Detection delay (6-10s)
- Network/API latency
- Market impact from your order
- Spread that you pay

The fixes in this commit make the simulation more realistic and add safeguards to skip trades where costs exceed acceptable thresholds. However, the fundamental structural issues can only be addressed through:

1. Faster detection (mempool monitoring)
2. Smarter execution (limit orders, timing optimization)
3. Better leader selection (takers, not makers)

**Remember:** If the leader makes +50%, you might realistically expect +10% to +30% after all costs. The goal is to minimize the gap, not eliminate it.
