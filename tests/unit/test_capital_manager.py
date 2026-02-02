"""Comprehensive TDD tests for CapitalManager two-tier floor system.

Tests cover:
- TradingMode transitions (NORMAL -> SOFT_FLOOR -> HARD_FLOOR)
- High water mark tracking
- Entry gating based on mode and quality score
- Position management gating based on mode
- Recovery back to NORMAL
- Edge cases (zero equity, zero HWM)
"""
import pytest
from decimal import Decimal

from src.core.capital_manager import TradingMode, CapitalManager


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def make_capital_manager(
    soft_floor_pct: Decimal = Decimal("90.0"),
    hard_floor_pct: Decimal = Decimal("70.0"),
    exceptional_quality_threshold: Decimal = Decimal("0.85")
) -> CapitalManager:
    """Create CapitalManager with specified thresholds."""
    return CapitalManager(
        soft_floor_pct=soft_floor_pct,
        hard_floor_pct=hard_floor_pct,
        exceptional_quality_threshold=exceptional_quality_threshold
    )


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================

def test_initialize_sets_hwm_and_normal_mode():
    """Initialize with $100 should set HWM=$100 and mode=NORMAL."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    assert cm.high_water_mark == Decimal("100.00")
    assert cm.mode == TradingMode.NORMAL


# ============================================================================
# MODE DETECTION TESTS
# ============================================================================

def test_normal_mode_above_soft_floor():
    """Equity at 95% of HWM (5% DD) should remain NORMAL."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    mode = cm.check_floor_status(Decimal("95.00"))
    assert mode == TradingMode.NORMAL
    assert cm.mode == TradingMode.NORMAL


def test_soft_floor_at_threshold():
    """Equity at exactly 90% of HWM (10% DD) should trigger SOFT_FLOOR."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    mode = cm.check_floor_status(Decimal("90.00"))
    assert mode == TradingMode.SOFT_FLOOR
    assert cm.mode == TradingMode.SOFT_FLOOR


def test_soft_floor_below_threshold():
    """Equity at 89% of HWM (11% DD) should be SOFT_FLOOR."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    mode = cm.check_floor_status(Decimal("89.00"))
    assert mode == TradingMode.SOFT_FLOOR
    assert cm.mode == TradingMode.SOFT_FLOOR


def test_hard_floor_at_threshold():
    """Equity at exactly 70% of HWM (30% DD) should trigger HARD_FLOOR."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    mode = cm.check_floor_status(Decimal("70.00"))
    assert mode == TradingMode.HARD_FLOOR
    assert cm.mode == TradingMode.HARD_FLOOR


def test_hard_floor_below_threshold():
    """Equity at 69% of HWM (31% DD) should be HARD_FLOOR."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    mode = cm.check_floor_status(Decimal("69.00"))
    assert mode == TradingMode.HARD_FLOOR
    assert cm.mode == TradingMode.HARD_FLOOR


# ============================================================================
# RECOVERY TESTS
# ============================================================================

def test_recovery_from_hard_floor_to_normal():
    """Equity recovering from hard floor ($70) to $91 should return to NORMAL."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    # Drop to hard floor
    mode = cm.check_floor_status(Decimal("70.00"))
    assert mode == TradingMode.HARD_FLOOR

    # Recover above soft floor threshold (90%)
    mode = cm.check_floor_status(Decimal("91.00"))
    assert mode == TradingMode.NORMAL
    assert cm.mode == TradingMode.NORMAL


# ============================================================================
# HIGH WATER MARK TRACKING TESTS
# ============================================================================

def test_hwm_increases_with_equity():
    """HWM should update when equity exceeds it."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    cm.update_high_water_mark(Decimal("120.00"))
    assert cm.high_water_mark == Decimal("120.00")


def test_hwm_does_not_decrease():
    """HWM should never decrease."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    cm.update_high_water_mark(Decimal("120.00"))
    cm.update_high_water_mark(Decimal("110.00"))

    assert cm.high_water_mark == Decimal("120.00")  # Should stay at 120


def test_soft_floor_recalculates_with_new_hwm():
    """Soft floor threshold should recalculate when HWM increases.

    $100 HWM: soft floor at $90 (90%)
    $120 HWM: soft floor at $108 (90% of 120)
    """
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    # Update HWM to $120
    cm.update_high_water_mark(Decimal("120.00"))

    # $108 is exactly 90% of $120, should trigger SOFT_FLOOR
    mode = cm.check_floor_status(Decimal("108.00"))
    assert mode == TradingMode.SOFT_FLOOR


# ============================================================================
# ENTRY GATING TESTS
# ============================================================================

def test_can_enter_normal_mode_any_quality():
    """In NORMAL mode, any quality score should allow entry."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))
    cm.check_floor_status(Decimal("95.00"))  # NORMAL mode

    can_enter, reason = cm.can_enter_new_trade(Decimal("0.50"))
    assert can_enter is True
    assert reason is None


def test_can_enter_soft_floor_exceptional_quality():
    """In SOFT_FLOOR mode, quality >= 0.85 should allow entry."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))
    cm.check_floor_status(Decimal("90.00"))  # SOFT_FLOOR mode

    can_enter, reason = cm.can_enter_new_trade(Decimal("0.90"))
    assert can_enter is True
    assert reason is None


def test_cannot_enter_soft_floor_low_quality():
    """In SOFT_FLOOR mode, quality < 0.85 should block entry."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))
    cm.check_floor_status(Decimal("90.00"))  # SOFT_FLOOR mode

    can_enter, reason = cm.can_enter_new_trade(Decimal("0.80"))
    assert can_enter is False
    assert reason == "soft_floor_low_quality"


def test_cannot_enter_hard_floor_any_quality():
    """In HARD_FLOOR mode, even perfect quality should block entry."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))
    cm.check_floor_status(Decimal("70.00"))  # HARD_FLOOR mode

    can_enter, reason = cm.can_enter_new_trade(Decimal("1.0"))
    assert can_enter is False
    assert reason == "hard_floor_hit"


# ============================================================================
# POSITION MANAGEMENT GATING TESTS
# ============================================================================

def test_can_manage_positions_normal_mode():
    """In NORMAL mode, position management should be allowed."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))
    cm.check_floor_status(Decimal("95.00"))  # NORMAL mode

    assert cm.can_manage_positions() is True


def test_can_manage_positions_soft_floor():
    """In SOFT_FLOOR mode, position management should be allowed."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))
    cm.check_floor_status(Decimal("90.00"))  # SOFT_FLOOR mode

    assert cm.can_manage_positions() is True


def test_cannot_manage_positions_hard_floor():
    """In HARD_FLOOR mode, position management should be blocked."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))
    cm.check_floor_status(Decimal("70.00"))  # HARD_FLOOR mode

    assert cm.can_manage_positions() is False


# ============================================================================
# EDGE CASE TESTS
# ============================================================================

def test_zero_equity_triggers_hard_floor():
    """Zero equity should trigger HARD_FLOOR mode."""
    cm = make_capital_manager()
    cm.initialize(Decimal("100.00"))

    mode = cm.check_floor_status(Decimal("0.00"))
    assert mode == TradingMode.HARD_FLOOR


def test_zero_hwm_handles_gracefully():
    """Zero HWM (uninitialized) should return HARD_FLOOR without division error."""
    cm = make_capital_manager()
    # Don't initialize - HWM defaults to 0

    mode = cm.check_floor_status(Decimal("100.00"))
    assert mode == TradingMode.HARD_FLOOR


# ============================================================================
# THRESHOLD CUSTOMIZATION TESTS
# ============================================================================

def test_custom_soft_floor_threshold():
    """Custom soft floor percentage should be respected."""
    cm = make_capital_manager(soft_floor_pct=Decimal("95.0"))  # 5% DD triggers soft floor
    cm.initialize(Decimal("100.00"))

    # At 94%, should be in SOFT_FLOOR
    mode = cm.check_floor_status(Decimal("94.00"))
    assert mode == TradingMode.SOFT_FLOOR


def test_custom_hard_floor_threshold():
    """Custom hard floor percentage should be respected."""
    cm = make_capital_manager(hard_floor_pct=Decimal("80.0"))  # 20% DD triggers hard floor
    cm.initialize(Decimal("100.00"))

    # At 79%, should be in HARD_FLOOR
    mode = cm.check_floor_status(Decimal("79.00"))
    assert mode == TradingMode.HARD_FLOOR


def test_custom_exceptional_quality_threshold():
    """Custom exceptional quality threshold should be respected."""
    cm = make_capital_manager(exceptional_quality_threshold=Decimal("0.90"))
    cm.initialize(Decimal("100.00"))
    cm.check_floor_status(Decimal("90.00"))  # SOFT_FLOOR mode

    # Quality 0.89 should fail (below 0.90 threshold)
    can_enter, reason = cm.can_enter_new_trade(Decimal("0.89"))
    assert can_enter is False
    assert reason == "soft_floor_low_quality"

    # Quality 0.90 should pass (at threshold)
    can_enter, reason = cm.can_enter_new_trade(Decimal("0.90"))
    assert can_enter is True
    assert reason is None
