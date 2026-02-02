"""
Follow Quality Metrics - measures how well we mirror the leader.

Key metrics:
1. Position correlation: correlation between leader net position and ours (with lags)
2. Same-sign %: what % of time we're positioned in the same direction as leader
3. Reaction delay: how quickly we follow when leader changes direction  
4. Event-level follow score: per-trade scoring of follow quality

Uses 2-second buckets to build time-series for correlation analysis.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..data.models import TradeAction

logger = logging.getLogger(__name__)

# Configuration
BUCKET_SIZE_SECONDS = 2  # Aggregate positions into 2-second windows
LAG_WINDOWS = [0, 2, 4, 6, 10, 20]  # Lags to test in seconds


@dataclass
class PositionSnapshot:
    """Position state at a point in time."""
    timestamp: datetime
    token_id: str
    net_shares: Decimal  # Positive = long, negative = short
    direction: str  # "long", "short", or "flat"
    
    def __post_init__(self):
        if self.net_shares > 0:
            self.direction = "long"
        elif self.net_shares < 0:
            self.direction = "short"
        else:
            self.direction = "flat"


@dataclass 
class TradeEvent:
    """Simplified trade event for metrics."""
    timestamp: datetime
    token_id: str
    action: str  # "BUY" or "SELL"
    shares: Decimal
    is_leader: bool


@dataclass
class FollowScore:
    """Score for a single trade."""
    leader_trade_seq: int
    token_id: str
    leader_action: str
    our_response: str  # "followed", "skipped", "opposite", "delayed_follow"
    reaction_time_ms: Optional[int] = None
    score: float = 0.0  # 0-1 score
    
    def __post_init__(self):
        if self.our_response == "followed":
            self.score = 1.0
            if self.reaction_time_ms and self.reaction_time_ms < 1000:
                self.score = 1.2  # Bonus for fast follow
        elif self.our_response == "delayed_follow":
            self.score = 0.5
        elif self.our_response == "skipped":
            self.score = 0.0  # Neutral - could be intentional
        elif self.our_response == "opposite":
            self.score = -0.5  # Penalty for going opposite


@dataclass
class TokenMetrics:
    """Metrics for a single token."""
    token_id: str
    leader_buys: int = 0
    leader_sells: int = 0
    our_buys: int = 0
    our_sells: int = 0
    leader_net_shares: Decimal = Decimal("0")
    our_net_shares: Decimal = Decimal("0")
    
    # Time series (buckets)
    leader_positions: List[Tuple[datetime, Decimal]] = field(default_factory=list)
    our_positions: List[Tuple[datetime, Decimal]] = field(default_factory=list)
    
    # Direction switches
    leader_direction_changes: List[datetime] = field(default_factory=list)
    our_direction_changes: List[datetime] = field(default_factory=list)
    
    # Follow events
    follow_scores: List[FollowScore] = field(default_factory=list)
    
    @property
    def leader_net_direction(self) -> str:
        if self.leader_net_shares > 0:
            return "long"
        elif self.leader_net_shares < 0:
            return "short"
        return "flat"
    
    @property
    def our_net_direction(self) -> str:
        if self.our_net_shares > 0:
            return "long"
        elif self.our_net_shares < 0:
            return "short"
        return "flat"
    
    @property
    def same_direction(self) -> bool:
        """Are we positioned in the same direction as leader?"""
        if self.leader_net_direction == "flat" or self.our_net_direction == "flat":
            return True  # Neutral if either is flat
        return self.leader_net_direction == self.our_net_direction


class FollowMetricsTracker:
    """
    Tracks how well we follow the leader across all tokens.
    
    Usage:
        tracker = FollowMetricsTracker()
        
        # Record leader trades
        tracker.record_leader_trade(timestamp, token_id, "BUY", shares, seq)
        
        # Record our trades (responses)
        tracker.record_our_trade(timestamp, token_id, "BUY", shares, leader_seq)
        
        # Get metrics
        metrics = tracker.calculate_metrics()
    """
    
    def __init__(self, bucket_size_seconds: int = BUCKET_SIZE_SECONDS):
        self.bucket_size = timedelta(seconds=bucket_size_seconds)
        self.token_metrics: Dict[str, TokenMetrics] = defaultdict(lambda: TokenMetrics(token_id=""))
        
        # Timeline for correlation analysis
        self.leader_timeline: Dict[str, Dict[datetime, Decimal]] = defaultdict(dict)
        self.our_timeline: Dict[str, Dict[datetime, Decimal]] = defaultdict(dict)
        
        # Trade matching (leader_seq -> our response)
        self.pending_leader_trades: Dict[int, TradeEvent] = {}
        self.follow_window_seconds = 10  # How long to consider a response as "following"
        
        # Aggregates
        self.total_leader_trades = 0
        self.total_our_trades = 0
        self.followed_count = 0
        self.skipped_count = 0
        self.opposite_count = 0
    
    def _bucket_time(self, ts: datetime) -> datetime:
        """Round timestamp to bucket boundary."""
        seconds = (ts.hour * 3600 + ts.minute * 60 + ts.second)
        bucket_seconds = (seconds // self.bucket_size.seconds) * self.bucket_size.seconds
        return ts.replace(hour=bucket_seconds // 3600, 
                         minute=(bucket_seconds % 3600) // 60,
                         second=bucket_seconds % 60,
                         microsecond=0)
    
    def record_leader_trade(self, timestamp: datetime, token_id: str, 
                           action: str, shares: Decimal, sequence: int) -> None:
        """Record a leader trade."""
        tm = self.token_metrics[token_id]
        tm.token_id = token_id
        
        prev_direction = tm.leader_net_direction
        
        if action == "BUY":
            tm.leader_buys += 1
            tm.leader_net_shares += shares
        else:
            tm.leader_sells += 1
            tm.leader_net_shares -= shares
        
        # Track direction changes
        new_direction = tm.leader_net_direction
        if prev_direction != new_direction and prev_direction != "flat":
            tm.leader_direction_changes.append(timestamp)
        
        # Update timeline
        bucket = self._bucket_time(timestamp)
        self.leader_timeline[token_id][bucket] = tm.leader_net_shares
        
        # Store for matching
        self.pending_leader_trades[sequence] = TradeEvent(
            timestamp=timestamp,
            token_id=token_id,
            action=action,
            shares=shares,
            is_leader=True
        )
        
        self.total_leader_trades += 1
    
    def record_our_trade(self, timestamp: datetime, token_id: str,
                        action: str, shares: Decimal, 
                        leader_sequence: Optional[int] = None) -> None:
        """Record our trade, optionally matching to a leader trade."""
        tm = self.token_metrics[token_id]
        tm.token_id = token_id
        
        prev_direction = tm.our_net_direction
        
        if action == "BUY":
            tm.our_buys += 1
            tm.our_net_shares += shares
        else:
            tm.our_sells += 1
            tm.our_net_shares -= shares
        
        # Track direction changes
        new_direction = tm.our_net_direction
        if prev_direction != new_direction and prev_direction != "flat":
            tm.our_direction_changes.append(timestamp)
        
        # Update timeline
        bucket = self._bucket_time(timestamp)
        self.our_timeline[token_id][bucket] = tm.our_net_shares
        
        self.total_our_trades += 1
        
        # Match to leader trade for follow scoring
        if leader_sequence and leader_sequence in self.pending_leader_trades:
            leader_trade = self.pending_leader_trades[leader_sequence]
            reaction_time_ms = int((timestamp - leader_trade.timestamp).total_seconds() * 1000)
            
            if action == leader_trade.action:
                response = "followed" if reaction_time_ms < 5000 else "delayed_follow"
                self.followed_count += 1
            else:
                response = "opposite"
                self.opposite_count += 1
            
            score = FollowScore(
                leader_trade_seq=leader_sequence,
                token_id=token_id,
                leader_action=leader_trade.action,
                our_response=response,
                reaction_time_ms=reaction_time_ms
            )
            tm.follow_scores.append(score)
            
            # Clean up matched trade
            del self.pending_leader_trades[leader_sequence]
    
    def record_skip(self, sequence: int, token_id: str, reason: str) -> None:
        """Record that we skipped a leader trade."""
        if sequence in self.pending_leader_trades:
            leader_trade = self.pending_leader_trades[sequence]
            tm = self.token_metrics[token_id]
            
            score = FollowScore(
                leader_trade_seq=sequence,
                token_id=token_id,
                leader_action=leader_trade.action,
                our_response="skipped"
            )
            tm.follow_scores.append(score)
            self.skipped_count += 1
            
            del self.pending_leader_trades[sequence]
    
    def calculate_correlation(self, token_id: str, lag_seconds: int = 0) -> Optional[float]:
        """
        Calculate correlation between leader and our positions for a token.
        
        Args:
            token_id: Token to analyze
            lag_seconds: Positive = we lag behind leader, negative = we lead
            
        Returns:
            Correlation coefficient (-1 to 1) or None if insufficient data
        """
        leader_ts = self.leader_timeline.get(token_id, {})
        our_ts = self.our_timeline.get(token_id, {})
        
        if len(leader_ts) < 3 or len(our_ts) < 3:
            return None
        
        # Build aligned series
        all_times = sorted(set(leader_ts.keys()) | set(our_ts.keys()))
        
        leader_values = []
        our_values = []
        
        lag_delta = timedelta(seconds=lag_seconds)
        
        for t in all_times:
            # Leader value at time t
            leader_val = leader_ts.get(t, Decimal("0"))
            
            # Our value at time t + lag (we're lagging behind)
            our_time = t + lag_delta
            our_val = our_ts.get(our_time, Decimal("0"))
            
            leader_values.append(float(leader_val))
            our_values.append(float(our_val))
        
        if len(leader_values) < 3:
            return None
        
        # Calculate Pearson correlation
        try:
            leader_arr = np.array(leader_values)
            our_arr = np.array(our_values)
            
            # Handle constant arrays
            if np.std(leader_arr) == 0 or np.std(our_arr) == 0:
                return 1.0 if np.allclose(leader_arr, our_arr) else 0.0
            
            corr = np.corrcoef(leader_arr, our_arr)[0, 1]
            return float(corr) if not np.isnan(corr) else None
        except Exception:
            return None
    
    def calculate_same_sign_pct(self) -> float:
        """Calculate % of tokens where we're on same side as leader."""
        same_count = 0
        total_with_positions = 0
        
        for tm in self.token_metrics.values():
            if tm.leader_net_shares != 0 or tm.our_net_shares != 0:
                total_with_positions += 1
                if tm.same_direction:
                    same_count += 1
        
        if total_with_positions == 0:
            return 100.0
        return (same_count / total_with_positions) * 100
    
    def calculate_avg_reaction_time(self) -> Optional[float]:
        """Calculate average reaction time for followed trades in ms."""
        reaction_times = []
        for tm in self.token_metrics.values():
            for score in tm.follow_scores:
                if score.our_response in ["followed", "delayed_follow"] and score.reaction_time_ms:
                    reaction_times.append(score.reaction_time_ms)
        
        if not reaction_times:
            return None
        return sum(reaction_times) / len(reaction_times)
    
    def calculate_follow_rate(self) -> float:
        """Calculate % of leader trades we actually followed."""
        total = self.followed_count + self.skipped_count + self.opposite_count
        if total == 0:
            return 0.0
        return (self.followed_count / total) * 100
    
    def get_best_lag(self) -> Tuple[int, float]:
        """Find the lag that gives best correlation."""
        best_lag = 0
        best_corr = -2.0  # Worse than possible
        
        for lag in LAG_WINDOWS:
            correlations = []
            for token_id in self.token_metrics:
                corr = self.calculate_correlation(token_id, lag)
                if corr is not None:
                    correlations.append(corr)
            
            if correlations:
                avg_corr = sum(correlations) / len(correlations)
                if avg_corr > best_corr:
                    best_corr = avg_corr
                    best_lag = lag
        
        return best_lag, best_corr if best_corr > -2.0 else 0.0
    
    def calculate_metrics(self) -> Dict[str, Any]:
        """Calculate all follow quality metrics."""
        best_lag, best_corr = self.get_best_lag()
        avg_reaction_time = self.calculate_avg_reaction_time()
        
        # Calculate correlations at various lags
        lag_correlations = {}
        for lag in LAG_WINDOWS:
            correlations = []
            for token_id in self.token_metrics:
                corr = self.calculate_correlation(token_id, lag)
                if corr is not None:
                    correlations.append(corr)
            if correlations:
                lag_correlations[f"lag_{lag}s"] = sum(correlations) / len(correlations)
        
        # Per-token breakdown
        token_breakdown = {}
        for token_id, tm in self.token_metrics.items():
            short_id = token_id[:16] + "..." if len(token_id) > 16 else token_id
            avg_score = (sum(s.score for s in tm.follow_scores) / len(tm.follow_scores)
                        if tm.follow_scores else 0.0)
            token_breakdown[short_id] = {
                "leader_trades": tm.leader_buys + tm.leader_sells,
                "our_trades": tm.our_buys + tm.our_sells,
                "leader_net": float(tm.leader_net_shares),
                "our_net": float(tm.our_net_shares),
                "same_direction": tm.same_direction,
                "avg_follow_score": round(avg_score, 2)
            }
        
        return {
            "summary": {
                "leader_trades": self.total_leader_trades,
                "our_trades": self.total_our_trades,
                "follow_rate_pct": round(self.calculate_follow_rate(), 1),
                "same_sign_pct": round(self.calculate_same_sign_pct(), 1),
                "avg_reaction_time_ms": round(avg_reaction_time, 0) if avg_reaction_time else None,
                "best_lag_seconds": best_lag,
                "best_correlation": round(best_corr, 3)
            },
            "lag_correlations": {k: round(v, 3) for k, v in lag_correlations.items()},
            "follow_breakdown": {
                "followed": self.followed_count,
                "skipped": self.skipped_count,
                "opposite": self.opposite_count
            },
            "tokens": token_breakdown
        }
    
    def print_summary(self) -> None:
        """Print a human-readable summary of follow metrics."""
        metrics = self.calculate_metrics()
        summary = metrics["summary"]
        
        print()
        print("=" * 70)
        print("  FOLLOW QUALITY METRICS")
        print("=" * 70)
        print()
        print(f"  Leader trades: {summary['leader_trades']}")
        print(f"  Our trades:    {summary['our_trades']}")
        print()
        print(f"  Follow rate:   {summary['follow_rate_pct']:.1f}%")
        print(f"  Same-sign %:   {summary['same_sign_pct']:.1f}%")
        if summary['avg_reaction_time_ms']:
            print(f"  Avg reaction:  {summary['avg_reaction_time_ms']:.0f}ms")
        print()
        print(f"  Best correlation: {summary['best_correlation']:.3f} at {summary['best_lag_seconds']}s lag")
        
        if metrics["lag_correlations"]:
            print()
            print("  Correlation by lag:")
            for lag, corr in metrics["lag_correlations"].items():
                bar = "█" * int(abs(corr) * 20)
                sign = "+" if corr >= 0 else "-"
                print(f"    {lag}: {sign}{abs(corr):.3f} {bar}")
        
        print()
        print("  Follow breakdown:")
        fb = metrics["follow_breakdown"]
        print(f"    Followed: {fb['followed']}")
        print(f"    Skipped:  {fb['skipped']}")
        print(f"    Opposite: {fb['opposite']}")
        print("=" * 70)
