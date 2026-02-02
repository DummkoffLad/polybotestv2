"""
DataSplitManager - tracks which sessions are in-sample vs out-of-sample.

Critical component for preventing data leakage in validation. Once a session is
marked in-sample (used for optimization), it can never be used for validation.

New sessions gathered after optimization are automatically out-of-sample candidates.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set


@dataclass
class DataSplitManager:
    """Manages the in-sample vs out-of-sample split for validation.

    Attributes:
        sessions_dir: Directory containing session .jsonl files
        metadata_path: Path to JSON file storing split metadata
        in_sample_sessions: Set of session IDs used for optimization
        out_of_sample_sessions: Set of session IDs reserved for validation

    Example:
        >>> manager = DataSplitManager(
        ...     sessions_dir=Path("data/sessions"),
        ...     metadata_path=Path("data/validation/split_metadata.json")
        ... )
        >>> manager.mark_in_sample("session_20260130_032713")
        >>> manager.mark_out_of_sample("session_20260202_120000")
        >>> manager.save()
    """

    sessions_dir: Path
    metadata_path: Path
    in_sample_sessions: Set[str] = field(default_factory=set)
    out_of_sample_sessions: Set[str] = field(default_factory=set)

    def __post_init__(self):
        """Ensure paths are Path objects."""
        self.sessions_dir = Path(self.sessions_dir)
        self.metadata_path = Path(self.metadata_path)

    # --- Classification Methods ---

    def mark_in_sample(self, session_id: str) -> None:
        """Mark a session as in-sample (used for optimization).

        Args:
            session_id: Session identifier (filename stem without .jsonl)

        Note:
            Automatically persists to metadata file.
        """
        self.in_sample_sessions.add(session_id)
        # Remove from OOS if it was there (shouldn't happen, but be defensive)
        self.out_of_sample_sessions.discard(session_id)
        self.save()

    def mark_out_of_sample(self, session_id: str) -> None:
        """Mark a session as out-of-sample (reserved for validation).

        Args:
            session_id: Session identifier (filename stem without .jsonl)

        Raises:
            ValueError: If session is already marked in-sample (data leakage prevention)

        Note:
            Automatically persists to metadata file.
        """
        if session_id in self.in_sample_sessions:
            raise ValueError(
                f"Session {session_id} is already in-sample and cannot be used for validation. "
                "This would cause data leakage."
            )

        self.out_of_sample_sessions.add(session_id)
        self.save()

    def is_in_sample(self, session_id: str) -> bool:
        """Check if a session is in-sample.

        Args:
            session_id: Session identifier

        Returns:
            True if session is in-sample, False otherwise
        """
        return session_id in self.in_sample_sessions

    def is_out_of_sample(self, session_id: str) -> bool:
        """Check if a session is out-of-sample.

        Args:
            session_id: Session identifier

        Returns:
            True if session is out-of-sample, False otherwise
        """
        return session_id in self.out_of_sample_sessions

    def validate_no_leakage(self, session_ids: List[str]) -> None:
        """Validate that none of the given sessions are in-sample.

        This is the critical data leakage prevention check. Call this before
        running validation to ensure clean out-of-sample testing.

        Args:
            session_ids: List of session identifiers to validate

        Raises:
            ValueError: If any session is in-sample
        """
        in_sample_found = [sid for sid in session_ids if sid in self.in_sample_sessions]

        if in_sample_found:
            raise ValueError(
                f"Data leakage detected! The following sessions are in-sample and "
                f"cannot be used for validation: {in_sample_found}"
            )

    # --- Retrieval Methods ---

    def get_in_sample_sessions(self) -> List[str]:
        """Get list of in-sample session IDs.

        Returns:
            List of session identifiers (sorted for determinism)
        """
        return sorted(self.in_sample_sessions)

    def get_validation_sessions(self) -> List[Path]:
        """Get paths to all out-of-sample session files.

        Returns:
            List of Path objects to .jsonl session files (sorted for determinism)
        """
        paths = [
            self.sessions_dir / f"{session_id}.jsonl"
            for session_id in sorted(self.out_of_sample_sessions)
        ]
        return paths

    # --- Session Discovery ---

    def discover_sessions(self) -> List[str]:
        """Discover all available session files in sessions_dir.

        Returns:
            List of session IDs (filename stems without .jsonl extension)
        """
        if not self.sessions_dir.exists():
            return []

        session_files = self.sessions_dir.glob("session_*.jsonl")
        return sorted(path.stem for path in session_files)

    def get_unclassified_sessions(self) -> List[str]:
        """Get sessions that are neither in-sample nor out-of-sample.

        Useful for identifying newly gathered data that needs classification.

        Returns:
            List of unclassified session IDs
        """
        all_sessions = set(self.discover_sessions())
        classified = self.in_sample_sessions | self.out_of_sample_sessions
        unclassified = all_sessions - classified
        return sorted(unclassified)

    # --- Persistence ---

    def save(self) -> None:
        """Save split metadata to JSON file.

        Creates parent directories if they don't exist.
        """
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "in_sample_sessions": sorted(self.in_sample_sessions),
            "out_of_sample_sessions": sorted(self.out_of_sample_sessions),
        }

        with open(self.metadata_path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self) -> None:
        """Load split metadata from JSON file.

        If file doesn't exist, initializes empty state (no error raised).
        """
        if not self.metadata_path.exists():
            self.in_sample_sessions = set()
            self.out_of_sample_sessions = set()
            return

        with open(self.metadata_path) as f:
            data = json.load(f)

        self.in_sample_sessions = set(data.get("in_sample_sessions", []))
        self.out_of_sample_sessions = set(data.get("out_of_sample_sessions", []))
