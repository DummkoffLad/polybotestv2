"""Compare dry run decisions vs simulation decisions event-by-event.

This script validates that the simulation accurately reproduces dry run decisions.
If they diverge, it identifies which system is realistic.

Usage:
    python analyze_dryrun_parity.py
"""
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.framework.replay import SessionReplayer
from src.strategies.profit_taker.strategy import ProfitTakerStrategy


@dataclass
class Divergence:
    """Record of a decision divergence between dry run and simulation."""
    sequence: int
    timestamp: str
    token_id: str
    dry_action: str
    sim_action: str
    dry_skip_reason: Optional[str]
    sim_skip_reason: Optional[str]
    leader_action: str
    leader_dollars: str
    price_bid: Optional[str]
    price_ask: Optional[str]


@dataclass
class SessionParity:
    """Parity results for a single session."""
    session_id: str
    hour: str
    total_events: int
    exact_matches: int
    action_matches: int
    skip_reason_mismatches: int
    divergences: List[Divergence] = field(default_factory=list)

    @property
    def parity_pct(self) -> float:
        if self.total_events == 0:
            return 100.0
        return (self.exact_matches / self.total_events) * 100

    @property
    def action_parity_pct(self) -> float:
        if self.total_events == 0:
            return 100.0
        return (self.action_matches / self.total_events) * 100


def load_config_from_first_session(session_files: List[Path]) -> Dict[str, Any]:
    """Load config from the first session file (usually hour 05 has session_start)."""
    for session_path in session_files:
        with open(session_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "session_start":
                    return data.get("config", {})
    return {}


def load_dry_run_decisions(session_path: Path) -> Dict[int, Dict[str, Any]]:
    """Load dry run decisions from session file, keyed by sequence number."""
    decisions = {}
    with open(session_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            if data.get("type") == "leader_trade":
                seq = data.get("sequence", 0)
                decisions[seq] = {
                    "timestamp": data.get("timestamp", ""),
                    "leader_trade": data.get("leader_trade", {}),
                    "price_context": data.get("price_context", {}),
                    "decision": data.get("decision", {}),
                }
    return decisions


def run_simulation(session_path: Path, dry_decisions: Dict[int, Dict[str, Any]],
                   base_config: Dict[str, Any] = None) -> Dict[int, Dict[str, Any]]:
    """Run simulation and collect decisions keyed by sequence number.

    We need to map our internal event indices to the session's global sequence numbers.
    The session file may start at seq 128 (for hour 06), not seq 1.

    Args:
        session_path: Path to the session file
        dry_decisions: Dry run decisions for sequence mapping
        base_config: Config to use if session file lacks session_start event
    """
    strategy = ProfitTakerStrategy()
    replayer = SessionReplayer(session_path, strategy=strategy)
    replayer.load()

    # We need to process events and capture decisions
    # The replayer processes internally, so we need to run our own processing
    from src.strategies.base import StrategyConfig

    # Use loaded config if available, otherwise fall back to base_config
    config = replayer.loader.original_config
    if not config and base_config:
        config = base_config

    strategy_config = StrategyConfig.from_dict(config)
    strategy.initialize(strategy_config)
    strategy.on_session_start()

    sim_decisions = {}

    # Get sorted sequence numbers from dry run to map indices
    sorted_seqs = sorted(dry_decisions.keys())

    for i, event in enumerate(replayer.loader.events):
        # Get all prices at this time (for cross-token checks)
        all_prices = replayer.loader.get_all_prices_at_time(event.trade.timestamp)
        event.context['all_prices'] = all_prices

        # Get decision from strategy
        decision = strategy.on_event(event)

        # Map internal index to global sequence number
        if i < len(sorted_seqs):
            seq = sorted_seqs[i]
        else:
            seq = i + 1  # Fallback

        sim_decisions[seq] = {
            "action": decision.action.value if decision.action else "SKIP",
            "skip_reason": decision.skip_reason,
            "dollars": str(decision.dollars) if decision.dollars else None,
            "shares": str(decision.shares) if decision.shares else None,
        }

        # Apply fill if not skip
        from src.strategies.base import DecisionAction
        if decision.action in (DecisionAction.BUY, DecisionAction.SELL):
            strategy.on_fill(event, decision)

    strategy.on_session_end()
    return sim_decisions


def compare_sessions(session_path: Path, base_config: Dict[str, Any] = None) -> SessionParity:
    """Compare dry run vs simulation for a single session."""
    hour = session_path.stem.split('_')[-1]

    # Load dry run decisions
    dry_decisions = load_dry_run_decisions(session_path)

    # Run simulation (pass dry_decisions for sequence mapping and base_config for fallback)
    sim_decisions = run_simulation(session_path, dry_decisions, base_config)

    # Compare
    total = 0
    exact_matches = 0
    action_matches = 0
    skip_reason_mismatches = 0
    divergences = []

    for seq, dry in sorted(dry_decisions.items()):
        total += 1
        dry_dec = dry.get("decision", {})
        dry_action = dry_dec.get("action", "SKIP")
        dry_skip = dry_dec.get("skip_reason")

        sim = sim_decisions.get(seq, {})
        sim_action = sim.get("action", "SKIP")
        sim_skip = sim.get("skip_reason")

        # Check action match
        actions_match = dry_action == sim_action
        if actions_match:
            action_matches += 1

        # Check exact match (action + skip reason)
        # Note: skip_reason order differences are cosmetic (both result in SKIP)
        exact = actions_match and (dry_action != "SKIP" or dry_skip == sim_skip)
        if exact:
            exact_matches += 1
        elif actions_match and dry_action == "SKIP":
            # Action matched but skip reason different - cosmetic difference
            skip_reason_mismatches += 1
        else:
            # True divergence - different actions
            leader = dry.get("leader_trade", {})
            prices = dry.get("price_context", {})
            divergences.append(Divergence(
                sequence=seq,
                timestamp=dry.get("timestamp", ""),
                token_id=leader.get("token_id", "")[:20] + "...",  # Truncate
                dry_action=dry_action,
                sim_action=sim_action,
                dry_skip_reason=dry_skip,
                sim_skip_reason=sim_skip,
                leader_action=leader.get("action", ""),
                leader_dollars=str(leader.get("leader_dollars", "")),
                price_bid=prices.get("bid"),
                price_ask=prices.get("ask"),
            ))

    return SessionParity(
        session_id=str(session_path),
        hour=hour,
        total_events=total,
        exact_matches=exact_matches,
        action_matches=action_matches,
        skip_reason_mismatches=skip_reason_mismatches,
        divergences=divergences,
    )


def get_open_positions(session_path: Path) -> Dict[str, Any]:
    """Get final positions and prices from session."""
    final_prices = {}
    positions = {}

    # Run simulation to get positions
    strategy = ProfitTakerStrategy()
    replayer = SessionReplayer(session_path, strategy=strategy)
    replayer.load()
    results = replayer.run()

    state = strategy.get_state()
    positions = state.get("positions", {})

    # Get final prices
    for token_id, price in replayer.loader.final_prices.items():
        if price.bid:
            final_prices[token_id] = {
                "bid": str(price.bid),
                "ask": str(price.ask) if price.ask else None,
            }

    return {
        "positions": positions,
        "final_prices": final_prices,
        "realized_pnl": state.get("realized_pnl", "0"),
        "total_bought": state.get("total_bought", "0"),
        "total_sold": state.get("total_sold", "0"),
    }


def main():
    session_dir = Path("data/sessions/2026-02-06")
    session_files = sorted(session_dir.glob("05-30_hour_*.jsonl"))

    if not session_files:
        print("ERROR: No session files found!")
        return

    print(f"Analyzing {len(session_files)} sessions for dry run vs simulation parity\n")

    # Load config from first session file (usually has session_start event)
    base_config = load_config_from_first_session(session_files)
    if base_config:
        scaling = base_config.get('scaling', {})
        print(f"Config loaded from first session:")
        print(f"  our_capital: ${scaling.get('our_capital')}")
        print(f"  leader_capital: ${scaling.get('leader_capital')}")
        print(f"  hourly_budget: ${scaling.get('hourly_budget')}")
        print()
    else:
        print("WARNING: No config found in any session file - using defaults")
        print()

    # Summary table header
    print(f"{'Hour':<6} {'Events':>7} {'Exact':>8} {'Action':>8} {'Diverge':>8} {'Top Divergence':<40}")
    print("=" * 85)

    all_results: List[SessionParity] = []
    all_divergences: List[Dict] = []

    for session_path in session_files:
        result = compare_sessions(session_path, base_config)
        all_results.append(result)

        # Top divergence for this hour
        top_div = ""
        if result.divergences:
            d = result.divergences[0]
            top_div = f"seq{d.sequence}: {d.dry_action}->{d.sim_action}"
            if d.dry_action == "SKIP":
                top_div += f" ({d.dry_skip_reason})"

        print(f"{result.hour:<6} {result.total_events:>7} "
              f"{result.parity_pct:>7.1f}% {result.action_parity_pct:>7.1f}% "
              f"{len(result.divergences):>8} {top_div:<40}")

        # Collect all divergences for export
        for d in result.divergences:
            all_divergences.append({
                "hour": result.hour,
                "sequence": d.sequence,
                "timestamp": d.timestamp,
                "token_id": d.token_id,
                "dry_action": d.dry_action,
                "sim_action": d.sim_action,
                "dry_skip_reason": d.dry_skip_reason,
                "sim_skip_reason": d.sim_skip_reason,
                "leader_action": d.leader_action,
                "leader_dollars": d.leader_dollars,
                "price_bid": d.price_bid,
                "price_ask": d.price_ask,
            })

    # Summary
    print("=" * 85)
    total_events = sum(r.total_events for r in all_results)
    total_exact = sum(r.exact_matches for r in all_results)
    total_action = sum(r.action_matches for r in all_results)
    total_divergences = sum(len(r.divergences) for r in all_results)

    overall_parity = (total_exact / total_events * 100) if total_events > 0 else 100
    overall_action = (total_action / total_events * 100) if total_events > 0 else 100

    print(f"{'TOTAL':<6} {total_events:>7} {overall_parity:>7.1f}% {overall_action:>7.1f}% {total_divergences:>8}")
    print()

    # Overall verdict
    if total_divergences == 0:
        print("VERDICT: 100% PARITY - Dry run and simulation make identical decisions")
    elif overall_action >= 99.9:
        print(f"VERDICT: {overall_action:.1f}% ACTION PARITY - Only skip reason differences (cosmetic)")
    else:
        print(f"VERDICT: {overall_action:.1f}% ACTION PARITY - {total_divergences} divergences need investigation")

    # Divergence details
    if all_divergences:
        print(f"\n{'='*85}")
        print("DIVERGENCE DETAILS")
        print(f"{'='*85}")
        for d in all_divergences[:20]:  # Show first 20
            print(f"\nHour {d['hour']} Seq {d['sequence']}:")
            print(f"  Token: {d['token_id']}")
            print(f"  Leader: {d['leader_action']} ${d['leader_dollars']}")
            print(f"  Dry run: {d['dry_action']} ({d['dry_skip_reason']})")
            print(f"  Simulation: {d['sim_action']} ({d['sim_skip_reason']})")
            print(f"  Prices: bid={d['price_bid']}, ask={d['price_ask']}")

        if len(all_divergences) > 20:
            print(f"\n... and {len(all_divergences) - 20} more divergences")

    # Save divergences to JSON
    divergence_file = session_dir / "05-30_divergences.json"
    with open(divergence_file, "w") as f:
        json.dump({
            "summary": {
                "total_events": total_events,
                "exact_matches": total_exact,
                "action_matches": total_action,
                "divergences": total_divergences,
                "parity_pct": overall_parity,
                "action_parity_pct": overall_action,
            },
            "divergences": all_divergences,
        }, f, indent=2)
    print(f"\nDivergence details saved to: {divergence_file}")

    # Open position analysis for last hour
    print(f"\n{'='*85}")
    print("OPEN POSITION ANALYSIS (Last Hour)")
    print(f"{'='*85}")

    last_session = session_files[-1]
    pos_info = get_open_positions(last_session)
    positions = pos_info["positions"]

    if positions:
        print(f"\nOpen positions at end of hour {last_session.stem.split('_')[-1]}:")
        for token_id, pos in positions.items():
            shares = Decimal(pos.get("shares", "0"))
            if shares <= 0:
                continue
            avg_price = Decimal(pos.get("avg_price", "0"))
            cost_basis = Decimal(pos.get("cost_basis", "0"))
            final = pos_info["final_prices"].get(token_id, {})
            final_bid = Decimal(final.get("bid", "0")) if final.get("bid") else Decimal("0")

            # Resolution estimate
            if final_bid >= Decimal("0.5"):
                resolution = "WIN (bid >= 0.50)"
                est_value = shares * Decimal("0.99")
            else:
                resolution = "LOSE (bid < 0.50)"
                est_value = Decimal("0")

            unrealized = est_value - cost_basis
            print(f"  {token_id[:20]}...")
            print(f"    Shares: {shares}, Avg: ${avg_price:.4f}, Cost: ${cost_basis:.2f}")
            print(f"    Final bid: ${final_bid:.4f} -> {resolution}")
            print(f"    Est resolution P&L: ${unrealized:+.2f}")
    else:
        print("No open positions at end of session.")

    print(f"\nRealized P&L: ${pos_info['realized_pnl']}")
    print(f"Total bought: ${pos_info['total_bought']}")
    print(f"Total sold: ${pos_info['total_sold']}")


if __name__ == "__main__":
    main()
