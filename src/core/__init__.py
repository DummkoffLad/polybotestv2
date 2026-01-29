"""Core abstractions - no external dependencies."""

from .types import (
    ExecutionMode,
    MarketPhase,
    Side,
    OrderType,
    OrderStatus,
    MarketId,
    Position,
    Exposure,
    LeaderSnapshot,
    MySnapshot,
    OrderRequest,
    OrderResponse,
    DecisionTarget,
    Decision,
    MarketMetadata,
    PriceSnapshot,
)
from .clock import Clock, SystemClock, SimulatedClock, create_clock
from .scaling import ScalingConfig, ScalingCalculator, ScalingResult
from .caps import CapsConfig, CapEnforcer, CappedTarget
from .state_machine import (
    StateMachineConfig,
    MarketStateMachine,
    GlobalStateMachine,
    MarketState,
)
from .decision import DecisionConfig, DecisionEngine

__all__ = [
    # Types
    "ExecutionMode",
    "MarketPhase",
    "Side",
    "OrderType",
    "OrderStatus",
    "MarketId",
    "Position",
    "Exposure",
    "LeaderSnapshot",
    "MySnapshot",
    "OrderRequest",
    "OrderResponse",
    "DecisionTarget",
    "Decision",
    "MarketMetadata",
    "PriceSnapshot",
    # Clock
    "Clock",
    "SystemClock",
    "SimulatedClock",
    "create_clock",
    # Scaling
    "ScalingConfig",
    "ScalingCalculator",
    "ScalingResult",
    # Caps
    "CapsConfig",
    "CapEnforcer",
    "CappedTarget",
    # State Machine
    "StateMachineConfig",
    "MarketStateMachine",
    "GlobalStateMachine",
    "MarketState",
    # Decision
    "DecisionConfig",
    "DecisionEngine",
]
