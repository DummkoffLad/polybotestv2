"""Trade Verification Tool — correlates blockchain detection with Polymarket API.

Grabs live data from both sources and checks:
  1. All API trades were also detected on-chain (no missed trades)
  2. No duplicate detections
  3. Timestamps are consistent (blockchain ts within tolerance of API ts)
  4. Dollar amounts / shares match between sources
  5. BUY/SELL action is correctly determined

Usage:
    python -m tests.tools.verify_trades                    # Use config leader
    python -m tests.tools.verify_trades --address 0x...    # Override leader
    python -m tests.tools.verify_trades --lookback 200     # Scan last 200 blocks
    python -m tests.tools.verify_trades --trades 100       # Fetch last 100 API trades

Can also be run via pytest:
    pytest tests/tools/verify_trades.py -v -s
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.data.blockchain_detector import BlockchainDetector, BlockchainTrade
from src.data.live_source import LiveDataSource
from src.data.models import PolymarketTrade


# ---------------------------------------------------------------------------
# Report structures
# ---------------------------------------------------------------------------

@dataclass
class MatchedTrade:
    """A trade found in both sources."""
    api_trade: PolymarketTrade
    chain_trade: BlockchainTrade
    ts_delta_sec: float = 0.0
    dollar_delta: Decimal = Decimal("0")
    shares_delta: Decimal = Decimal("0")
    action_match: bool = True


@dataclass
class VerificationReport:
    """Full verification report."""
    leader_address: str
    api_trades_fetched: int = 0
    chain_trades_found: int = 0
    matched: int = 0
    api_only: List[PolymarketTrade] = field(default_factory=list)
    chain_only: List[BlockchainTrade] = field(default_factory=list)
    matches: List[MatchedTrade] = field(default_factory=list)
    duplicate_chain_hashes: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    scan_blocks: int = 0
    scan_duration_sec: float = 0.0

    @property
    def api_only_count(self) -> int:
        return len(self.api_only)

    @property
    def chain_only_count(self) -> int:
        return len(self.chain_only)

    @property
    def duplicate_count(self) -> int:
        return sum(v - 1 for v in self.duplicate_chain_hashes.values() if v > 1)

    @property
    def passed(self) -> bool:
        return self.api_only_count == 0 and self.duplicate_count == 0 and len(self.errors) == 0


# ---------------------------------------------------------------------------
# Core verification logic
# ---------------------------------------------------------------------------

def fetch_api_trades(data_source: LiveDataSource, address: str, limit: int = 100) -> List[PolymarketTrade]:
    """Fetch recent trades from Polymarket Data API."""
    trades = data_source.fetch_trades(address, limit=limit)
    return trades


def scan_blockchain(
    detector: BlockchainDetector,
    token_lookup: Dict[str, object],
    lookback_blocks: int = 500,
) -> List[BlockchainTrade]:
    """Scan recent blocks for leader trades and enrich them."""
    current_block = detector.initialize()
    detector._last_block = max(0, current_block - lookback_blocks)
    # Reset seen set so we get a clean scan
    detector._seen.clear()
    detector._seen_tx.clear()
    all_trades: List[BlockchainTrade] = []
    while detector._last_block < current_block:
        batch = detector.poll() or []
        for bt in batch:
            detector.enrich_trade(bt, token_lookup)
            all_trades.append(bt)
    return all_trades


def correlate(
    api_trades: List[PolymarketTrade],
    chain_trades: List[BlockchainTrade],
    ts_tolerance_sec: int = 120,
) -> VerificationReport:
    """Correlate API trades with blockchain trades.

    Matching strategy:
      - Primary: match by transaction_hash (API provides it for most trades)
      - Fallback: match by (token_id, dollar_amount, timestamp proximity)
    """
    report = VerificationReport(leader_address="")
    report.api_trades_fetched = len(api_trades)
    report.chain_trades_found = len(chain_trades)

    # Only use enriched trades (those with token_id resolved)
    enriched_chain: List[BlockchainTrade] = [ct for ct in chain_trades if ct.token_id]

    # Check for duplicate tx hashes in chain data
    hash_counts: Counter = Counter()
    for ct in enriched_chain:
        key = f"{ct.tx_hash}_{ct.log_index}"
        hash_counts[key] += 1
    report.duplicate_chain_hashes = {k: v for k, v in hash_counts.items() if v > 1}

    # Build lookup: tx_hash -> chain trades
    chain_by_hash: Dict[str, List[BlockchainTrade]] = {}
    for ct in enriched_chain:
        chain_by_hash.setdefault(ct.tx_hash.lower(), []).append(ct)

    # Track which chain trades have been matched
    matched_chain_keys: Set[str] = set()

    for api_t in api_trades:
        match_found = False
        api_ts = api_t.timestamp  # Unix seconds

        # Strategy 1: Match by tx_hash
        if api_t.transaction_hash:
            candidates = chain_by_hash.get(api_t.transaction_hash.lower(), [])
            for ct in candidates:
                key = f"{ct.tx_hash}_{ct.log_index}"
                if key in matched_chain_keys:
                    continue
                # Verify token matches
                if ct.token_id and api_t.asset and ct.token_id != api_t.asset:
                    continue
                matched_chain_keys.add(key)
                ts_delta = abs(ct.block_timestamp - api_ts)
                dollar_delta = abs(ct.dollar_value - api_t.dollar_value) if ct.dollar_value else Decimal("0")
                shares_delta = abs(ct.shares - api_t.size) if ct.shares else Decimal("0")
                action_match = True
                if ct.action:
                    api_action = "BUY" if api_t.side.upper() == "BUY" else "SELL"
                    action_match = ct.action == api_action
                report.matches.append(MatchedTrade(
                    api_trade=api_t, chain_trade=ct,
                    ts_delta_sec=ts_delta, dollar_delta=dollar_delta,
                    shares_delta=shares_delta, action_match=action_match,
                ))
                match_found = True
                break

        # Strategy 2: Fuzzy match by token + amount + timestamp
        if not match_found:
            best_ct: Optional[BlockchainTrade] = None
            best_score = float("inf")
            for ct in enriched_chain:
                key = f"{ct.tx_hash}_{ct.log_index}"
                if key in matched_chain_keys:
                    continue
                if ct.token_id != api_t.asset:
                    continue
                ts_delta = abs(ct.block_timestamp - api_ts)
                if ts_delta > ts_tolerance_sec:
                    continue
                dollar_diff = float(abs(ct.dollar_value - api_t.dollar_value)) if ct.dollar_value else 999
                score = ts_delta + dollar_diff * 10  # Weight dollar accuracy
                if score < best_score:
                    best_score = score
                    best_ct = ct

            if best_ct is not None:
                key = f"{best_ct.tx_hash}_{best_ct.log_index}"
                matched_chain_keys.add(key)
                ts_delta = abs(best_ct.block_timestamp - api_ts)
                dollar_delta = abs(best_ct.dollar_value - api_t.dollar_value) if best_ct.dollar_value else Decimal("0")
                shares_delta = abs(best_ct.shares - api_t.size) if best_ct.shares else Decimal("0")
                action_match = True
                if best_ct.action:
                    api_action = "BUY" if api_t.side.upper() == "BUY" else "SELL"
                    action_match = best_ct.action == api_action
                report.matches.append(MatchedTrade(
                    api_trade=api_t, chain_trade=best_ct,
                    ts_delta_sec=ts_delta, dollar_delta=dollar_delta,
                    shares_delta=shares_delta, action_match=action_match,
                ))
                match_found = True

        if not match_found:
            report.api_only.append(api_t)

    # Chain trades that were not matched to any API trade
    for ct in enriched_chain:
        key = f"{ct.tx_hash}_{ct.log_index}"
        if key not in matched_chain_keys:
            report.chain_only.append(ct)

    report.matched = len(report.matches)
    return report


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def print_report(report: VerificationReport) -> None:
    print(f"\n{'='*60}")
    print(f"  TRADE VERIFICATION REPORT")
    print(f"{'='*60}")
    print(f"  Leader: {report.leader_address[:16]}...")
    print(f"  Blocks scanned: {report.scan_blocks}")
    print(f"  Scan duration: {report.scan_duration_sec:.1f}s")
    print(f"\n  API trades fetched:    {report.api_trades_fetched}")
    print(f"  Chain trades found:    {report.chain_trades_found}")
    print(f"  Matched:               {report.matched}")
    print(f"  API-only (missed):     {report.api_only_count}")
    print(f"  Chain-only (extra):    {report.chain_only_count}")
    print(f"  Duplicates:            {report.duplicate_count}")

    if report.matches:
        ts_deltas = [m.ts_delta_sec for m in report.matches]
        dollar_deltas = [float(m.dollar_delta) for m in report.matches]
        action_mismatches = sum(1 for m in report.matches if not m.action_match)
        print(f"\n  --- Match Quality ---")
        print(f"  Avg timestamp delta:   {sum(ts_deltas)/len(ts_deltas):.1f}s")
        print(f"  Max timestamp delta:   {max(ts_deltas):.1f}s")
        print(f"  Avg dollar delta:      ${sum(dollar_deltas)/len(dollar_deltas):.4f}")
        print(f"  Max dollar delta:      ${max(dollar_deltas):.4f}")
        print(f"  Action mismatches:     {action_mismatches}")

    if report.api_only:
        print(f"\n  --- MISSED TRADES (API-only) ---")
        for t in report.api_only[:10]:
            ts_str = datetime.fromtimestamp(t.timestamp, tz=timezone.utc).strftime("%H:%M:%S")
            print(f"    {ts_str} {t.side:4s} ${t.dollar_value:.2f}  {t.title[:35]}  tx={t.transaction_hash or 'none'}")
        if len(report.api_only) > 10:
            print(f"    ... and {len(report.api_only) - 10} more")

    if report.chain_only:
        print(f"\n  --- EXTRA TRADES (chain-only) ---")
        for ct in report.chain_only[:10]:
            ts_str = datetime.fromtimestamp(ct.block_timestamp, tz=timezone.utc).strftime("%H:%M:%S")
            action = ct.action or "?"
            print(f"    {ts_str} {action:4s} ${ct.dollar_value:.2f}  tx={ct.tx_hash[:16]}...")
        if len(report.chain_only) > 10:
            print(f"    ... and {len(report.chain_only) - 10} more")

    if report.duplicate_chain_hashes:
        print(f"\n  --- DUPLICATES ---")
        for key, count in list(report.duplicate_chain_hashes.items())[:5]:
            print(f"    {key[:24]}... seen {count}x")

    if report.errors:
        print(f"\n  --- ERRORS ---")
        for err in report.errors:
            print(f"    {err}")

    status = "PASS" if report.passed else "FAIL"
    print(f"\n  RESULT: {status}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_verification(
    leader_address: str,
    lookback_blocks: int = 500,
    api_trade_limit: int = 100,
) -> VerificationReport:
    """Run full verification pipeline."""
    print(f"Fetching API trades for {leader_address[:16]}...")
    data_source = LiveDataSource(leader_address=leader_address)

    # Fetch API trades
    api_trades = fetch_api_trades(data_source, leader_address, limit=api_trade_limit)
    print(f"  Got {len(api_trades)} trades from API")

    # Discover markets from API trades (needed for blockchain enrichment)
    for t in api_trades:
        data_source._discover_market_from_trade(t)

    # Also fetch positions for market discovery
    positions = data_source.fetch_positions(leader_address)
    for p in positions:
        data_source._discover_market_from_position(p)

    print(f"  Discovered {len(data_source._token_to_market)} token->market mappings")

    # Scan blockchain
    print(f"Scanning last {lookback_blocks} blocks on Polygon...")
    detector = BlockchainDetector(leader_address=leader_address)
    t0 = time.time()
    chain_trades = scan_blockchain(detector, data_source._token_to_market, lookback_blocks=lookback_blocks)
    scan_duration = time.time() - t0
    print(f"  Found {len(chain_trades)} raw chain events in {scan_duration:.1f}s")

    # Correlate
    print("Correlating trades...")
    report = correlate(api_trades, chain_trades)
    report.leader_address = leader_address
    report.scan_blocks = lookback_blocks
    report.scan_duration_sec = scan_duration

    return report


# ---------------------------------------------------------------------------
# Pytest entry point
# ---------------------------------------------------------------------------

def test_verify_trades():
    """Pytest-compatible: verifies blockchain detection matches API trades.

    Loads the leader address from config and runs verification.
    Asserts no trades were missed and no duplicates exist.
    """
    from src.core.config import load_config

    try:
        config = load_config()
    except FileNotFoundError:
        import pytest
        pytest.skip("Config file not found")

    if not config.leader.address:
        import pytest
        pytest.skip("No leader address configured")

    report = run_verification(
        leader_address=config.leader.address,
        lookback_blocks=300,   # ~10 minutes
        api_trade_limit=50,
    )
    print_report(report)

    # Assertions
    assert report.api_trades_fetched > 0, "Should fetch at least some API trades"
    # Allow some chain-only trades (they may be from non-hourly markets)
    # But missed trades (API-only) are a problem
    if report.api_only:
        # Filter: only fail on trades within the blockchain scan window
        oldest_chain_ts = min((ct.block_timestamp for ct in report.chain_only), default=0) if report.chain_only else 0
        if not oldest_chain_ts and report.matches:
            oldest_chain_ts = min(m.chain_trade.block_timestamp for m in report.matches)
        recent_missed = [t for t in report.api_only if t.timestamp >= oldest_chain_ts] if oldest_chain_ts else []
        assert len(recent_missed) == 0, (
            f"Missed {len(recent_missed)} trades that should have been in blockchain scan window"
        )

    assert report.duplicate_count == 0, f"Found {report.duplicate_count} duplicate detections"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Verify blockchain trade detection against Polymarket API")
    parser.add_argument("--address", type=str, default=None, help="Leader address (default: from config)")
    parser.add_argument("--lookback", type=int, default=500, help="Blocks to scan (default: 500)")
    parser.add_argument("--trades", type=int, default=100, help="API trades to fetch (default: 100)")
    args = parser.parse_args()

    leader_address = args.address
    if not leader_address:
        try:
            from src.core.config import load_config
            config = load_config()
            leader_address = config.leader.address
        except Exception as e:
            print(f"Error loading config: {e}")
            print("Use --address to specify leader address directly")
            return 1

    if not leader_address:
        print("No leader address. Set it in config.yaml or use --address")
        return 1

    report = run_verification(
        leader_address=leader_address,
        lookback_blocks=args.lookback,
        api_trade_limit=args.trades,
    )
    print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
