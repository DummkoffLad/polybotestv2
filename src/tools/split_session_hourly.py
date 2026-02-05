"""Split session JSONL files by market hour (ET timezone).

This tool enables granular analysis of strategy performance by hour,
useful for identifying time-of-day effects and regime differences.

Usage:
    python -m src.tools.split_session_hourly data/sessions/2026-02-03/05-56.jsonl

Output:
    Creates files like: data/sessions/2026-02-03/05-56_hour_05.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET_ZONE = ZoneInfo('America/New_York')


def split_session_by_hour(input_path: Path) -> dict[int, list[str]]:
    """Split session events by hour (ET timezone).

    Args:
        input_path: Path to session JSONL file

    Returns:
        Dict mapping hour (0-23) to list of JSON line strings
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Session file not found: {input_path}")

    hourly_events: dict[int, list[str]] = defaultdict(list)
    session_start_line: str | None = None

    with open(input_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Parse JSON
            try:
                # Handle prefixed log format: [MODE=XXX] {...}
                json_str = line
                if json_str.startswith("[MODE="):
                    bracket_end = json_str.find("]")
                    if bracket_end > 0:
                        json_str = json_str[bracket_end + 1:].strip()

                data = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            # Capture session_start for including in each hour file
            event_type = data.get("type")
            if event_type == "session_start":
                session_start_line = line
                continue

            # Get timestamp and convert to ET
            timestamp_str = data.get("timestamp")
            if not timestamp_str:
                # Try extracting from leader_trade or data fields
                leader_trade = data.get("leader_trade", {})
                timestamp_str = leader_trade.get("timestamp")
                if not timestamp_str:
                    # Skip events without timestamp
                    continue

            try:
                # Parse timestamp (may have timezone info or be UTC)
                ts_clean = timestamp_str.replace(" ET", "").replace(" UTC", "")
                timestamp = datetime.fromisoformat(ts_clean)

                # If naive, assume UTC
                if timestamp.tzinfo is None:
                    from datetime import timezone as tz
                    timestamp = timestamp.replace(tzinfo=tz.utc)

                # Convert to ET
                et_time = timestamp.astimezone(ET_ZONE)
                hour = et_time.hour

                # Add to appropriate hour bucket
                hourly_events[hour].append(line)

            except (ValueError, AttributeError):
                # Skip events with invalid timestamps
                continue

    return hourly_events, session_start_line


def write_hourly_files(input_path: Path, hourly_events: dict, session_start_line: str | None):
    """Write hourly session files.

    Args:
        input_path: Original session file path
        hourly_events: Dict mapping hour to event lines
        session_start_line: Session start event to include in each file
    """
    output_dir = input_path.parent
    base_name = input_path.stem  # e.g., "05-56" from "05-56.jsonl"

    for hour in sorted(hourly_events.keys()):
        output_path = output_dir / f"{base_name}_hour_{hour:02d}.jsonl"

        with open(output_path, 'w', encoding='utf-8') as f:
            # Include session_start in each hour file for config context
            if session_start_line:
                f.write(session_start_line + '\n')

            # Write all events for this hour
            for line in hourly_events[hour]:
                f.write(line + '\n')

        print(f"  Hour {hour:02d} ET: {len(hourly_events[hour]):4d} events -> {output_path.name}")


def main():
    parser = argparse.ArgumentParser(
        description="Split session JSONL file by market hour (ET timezone)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.tools.split_session_hourly data/sessions/2026-02-03/05-56.jsonl
  python -m src.tools.split_session_hourly data/sessions/2026-02-04/02-35.jsonl

Output:
  Creates files like: data/sessions/2026-02-03/05-56_hour_05.jsonl
                      data/sessions/2026-02-03/05-56_hour_06.jsonl
        """
    )
    parser.add_argument('session_file', type=Path, help='Path to session JSONL file')

    args = parser.parse_args()
    input_path: Path = args.session_file

    print(f"Splitting session by hour (ET timezone): {input_path}")
    print()

    try:
        hourly_events, session_start_line = split_session_by_hour(input_path)

        if not hourly_events:
            print("ERROR: No events with valid timestamps found")
            sys.exit(1)

        total_events = sum(len(events) for events in hourly_events.values())
        print(f"Found {total_events} events across {len(hourly_events)} hours")
        print()

        write_hourly_files(input_path, hourly_events, session_start_line)

        print()
        print(f"SUCCESS: Created {len(hourly_events)} hourly session files")

    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
