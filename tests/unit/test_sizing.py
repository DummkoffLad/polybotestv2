"""Unit tests for DynamicSizer with percentage-based position sizing.

Tests verify:
- Position size scales with current equity (not fixed capital)
- Size reduces after consecutive losses
- Size resets on win
- Quality score multiplier adjusts size
- Max position cap enforced
- High-water mark tracks peak equity
- All calculations use Decimal with quantize
"""
import pytest
from decimal import Decimal

from src.core.sizing import SizingConfig, DynamicSizer


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def make_config(**overrides) -> SizingConfig:
    """Create SizingConfig with sensible defaults.

    Defaults:
        base_risk_pct=1.0, max_position_pct=10.0, consecutive_loss_threshold=3
        size_reduction_after_losses=0.5, max_concurrent_positions=5
    """
    defaults = {
        "base_risk_pct": Decimal("1.0"),
        "max_position_pct": Decimal("10.0"),
        "consecutive_loss_threshold": 3,
        "size_reduction_after_losses": Decimal("0.5"),
        "max_concurrent_positions": 5,
    }
    defaults.update(overrides)
    return SizingConfig(**defaults)


def make_sizer(**config_overrides) -> DynamicSizer:
    """Create DynamicSizer with default config, initialized at $100."""
    config = make_config(**config_overrides)
    sizer = DynamicSizer(config)
    sizer.initialize(Decimal("100"))
    return sizer


# ============================================================================
# BASIC POSITION SIZING TESTS
# ============================================================================

def test_basic_position_size_with_neutral_quality():
    """$100 equity, quality 0.50 -> base $1.00 * 1.0 multiplier = $1.00"""
    sizer = make_sizer()

    size = sizer.calculate_position_size(
        current_equity=Decimal("100"),
        quality_score=Decimal("0.50")
    )

    assert size == Decimal("1.00")


def test_position_size_with_high_quality():
    """$100 equity, quality 1.00 -> base $1.00 * 1.5 multiplier = $1.50"""
    sizer = make_sizer()

    size = sizer.calculate_position_size(
        current_equity=Decimal("100"),
        quality_score=Decimal("1.00")
    )

    assert size == Decimal("1.50")


def test_position_size_with_low_quality():
    """$100 equity, quality 0.00 -> base $1.00 * 0.5 multiplier = $0.50"""
    sizer = make_sizer()

    size = sizer.calculate_position_size(
        current_equity=Decimal("100"),
        quality_score=Decimal("0.00")
    )

    assert size == Decimal("0.50")


# ============================================================================
# EQUITY SCALING TESTS
# ============================================================================

def test_position_size_scales_up_with_equity():
    """$120 equity (after gains), quality 0.50 -> base $1.20 * 1.0 = $1.20"""
    sizer = make_sizer()

    size = sizer.calculate_position_size(
        current_equity=Decimal("120"),
        quality_score=Decimal("0.50")
    )

    assert size == Decimal("1.20")


def test_position_size_scales_down_with_equity():
    """$80 equity (after losses), quality 0.50 -> base $0.80 * 1.0 = $0.80"""
    sizer = make_sizer()

    size = sizer.calculate_position_size(
        current_equity=Decimal("80"),
        quality_score=Decimal("0.50")
    )

    assert size == Decimal("0.80")


# ============================================================================
# CONSECUTIVE LOSS REDUCTION TESTS
# ============================================================================

def test_size_reduces_after_consecutive_losses():
    """After 3 consecutive losses, size reduces by 50%."""
    sizer = make_sizer()

    # Record 3 losses
    sizer.update_after_trade(Decimal("-5"))
    sizer.update_after_trade(Decimal("-3"))
    sizer.update_after_trade(Decimal("-2"))

    size = sizer.calculate_position_size(
        current_equity=Decimal("100"),
        quality_score=Decimal("0.50")
    )

    # Base $1.00 * 0.5 reduction * 1.0 quality = $0.50
    assert size == Decimal("0.50")


def test_size_resets_after_win_following_losses():
    """After 3 losses then 1 win, size resets to normal."""
    sizer = make_sizer()

    # Record 3 losses
    sizer.update_after_trade(Decimal("-5"))
    sizer.update_after_trade(Decimal("-3"))
    sizer.update_after_trade(Decimal("-2"))

    # Record 1 win (resets consecutive_losses)
    sizer.update_after_trade(Decimal("4"))

    size = sizer.calculate_position_size(
        current_equity=Decimal("100"),
        quality_score=Decimal("0.50")
    )

    # Back to normal: $1.00 * 1.0 quality = $1.00
    assert size == Decimal("1.00")


def test_consecutive_losses_below_threshold_no_reduction():
    """2 consecutive losses (below threshold of 3) -> no reduction."""
    sizer = make_sizer()

    # Record 2 losses (below threshold)
    sizer.update_after_trade(Decimal("-5"))
    sizer.update_after_trade(Decimal("-3"))

    size = sizer.calculate_position_size(
        current_equity=Decimal("100"),
        quality_score=Decimal("0.50")
    )

    # No reduction: $1.00 * 1.0 quality = $1.00
    assert size == Decimal("1.00")


# ============================================================================
# MAX POSITION CAP TESTS
# ============================================================================

def test_max_position_cap_enforced():
    """Max position cap: $100 equity, quality 1.0 with max_position_pct=1.0 -> capped at $1.00 not $1.50"""
    sizer = make_sizer(max_position_pct=Decimal("1.0"))  # Cap at 1% of equity

    size = sizer.calculate_position_size(
        current_equity=Decimal("100"),
        quality_score=Decimal("1.00")  # Would be $1.50 without cap
    )

    # Capped at 1% of $100 = $1.00
    assert size == Decimal("1.00")


def test_max_position_cap_not_hit():
    """Max position cap doesn't affect sizes below the cap."""
    sizer = make_sizer(max_position_pct=Decimal("10.0"))  # Cap at 10% of equity

    size = sizer.calculate_position_size(
        current_equity=Decimal("100"),
        quality_score=Decimal("1.00")  # $1.50, well below $10 cap
    )

    # Not capped: $1.50
    assert size == Decimal("1.50")


# ============================================================================
# HIGH-WATER MARK TESTS
# ============================================================================

def test_high_water_mark_initialization():
    """High-water mark initializes at starting capital."""
    sizer = make_sizer()

    assert sizer.high_water_mark == Decimal("100")


def test_high_water_mark_increases():
    """High-water mark increases when equity grows."""
    sizer = make_sizer()

    sizer.update_high_water_mark(Decimal("120"))

    assert sizer.high_water_mark == Decimal("120")


def test_high_water_mark_does_not_decrease():
    """High-water mark stays at peak even when equity drops."""
    sizer = make_sizer()

    # Increase to $120
    sizer.update_high_water_mark(Decimal("120"))

    # Try to update to $110 (should stay at $120)
    sizer.update_high_water_mark(Decimal("110"))

    assert sizer.high_water_mark == Decimal("120")


# ============================================================================
# EDGE CASE TESTS
# ============================================================================

def test_zero_equity_returns_zero_size():
    """$0 equity -> $0.00 position size"""
    sizer = make_sizer()

    size = sizer.calculate_position_size(
        current_equity=Decimal("0"),
        quality_score=Decimal("0.50")
    )

    assert size == Decimal("0.00")


def test_quality_score_boundary_values():
    """Test quality score at exact boundaries 0.0 and 1.0."""
    sizer = make_sizer()

    # Quality 0.0 -> multiplier 0.5
    size_min = sizer.calculate_position_size(Decimal("100"), Decimal("0.0"))
    assert size_min == Decimal("0.50")

    # Quality 1.0 -> multiplier 1.5
    size_max = sizer.calculate_position_size(Decimal("100"), Decimal("1.0"))
    assert size_max == Decimal("1.50")


def test_decimal_precision_maintained():
    """Verify calculations use Decimal and quantize to 0.01."""
    sizer = make_sizer()

    # Use equity that creates non-round result
    size = sizer.calculate_position_size(
        current_equity=Decimal("103.33"),
        quality_score=Decimal("0.50")
    )

    # $103.33 * 1% * 1.0 multiplier = $1.0333 -> quantized to $1.03
    assert size == Decimal("1.03")
    assert isinstance(size, Decimal)


# ============================================================================
# COMBINED SCENARIO TESTS
# ============================================================================

def test_combined_loss_reduction_and_quality():
    """Consecutive losses + quality adjustment both apply."""
    sizer = make_sizer()

    # Record 3 losses
    sizer.update_after_trade(Decimal("-5"))
    sizer.update_after_trade(Decimal("-3"))
    sizer.update_after_trade(Decimal("-2"))

    size = sizer.calculate_position_size(
        current_equity=Decimal("100"),
        quality_score=Decimal("1.00")  # High quality
    )

    # Base $1.00 * 0.5 reduction * 1.5 quality = $0.75
    assert size == Decimal("0.75")


def test_combined_equity_scaling_and_cap():
    """Equity scaling + cap enforcement."""
    sizer = make_sizer(max_position_pct=Decimal("2.0"))  # 2% cap

    size = sizer.calculate_position_size(
        current_equity=Decimal("150"),
        quality_score=Decimal("1.00")
    )

    # Base $1.50 * 1.5 quality = $2.25, capped at 2% of $150 = $3.00
    # So $2.25 is not capped
    assert size == Decimal("2.25")

    # Now try with higher quality that would exceed cap
    size_capped = sizer.calculate_position_size(
        current_equity=Decimal("150"),
        quality_score=Decimal("1.00")
    )

    # Already tested above, but let's test a scenario where cap binds
    sizer_low_cap = make_sizer(max_position_pct=Decimal("1.0"))
    size_capped = sizer_low_cap.calculate_position_size(
        current_equity=Decimal("150"),
        quality_score=Decimal("1.00")
    )

    # Base $1.50 * 1.5 quality = $2.25, capped at 1% of $150 = $1.50
    assert size_capped == Decimal("1.50")
