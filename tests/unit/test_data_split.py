"""
Unit tests for DataSplitManager - tracks in-sample vs out-of-sample sessions.

Critical for preventing data leakage in Phase 5 validation.
"""
from pathlib import Path
import json
import pytest
import tempfile
import shutil

from src.validation.data_split import DataSplitManager


class TestDataSplitManager:
    """Test suite for DataSplitManager."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test isolation."""
        tmpdir = tempfile.mkdtemp()
        yield Path(tmpdir)
        shutil.rmtree(tmpdir)

    @pytest.fixture
    def sessions_dir(self, temp_dir):
        """Create mock sessions directory with sample files."""
        sessions = temp_dir / "sessions"
        sessions.mkdir()

        # Create mock session files
        (sessions / "session_001.jsonl").touch()
        (sessions / "session_002.jsonl").touch()
        (sessions / "session_003.jsonl").touch()

        return sessions

    @pytest.fixture
    def manager(self, temp_dir, sessions_dir):
        """Create DataSplitManager instance."""
        metadata_path = temp_dir / "split_metadata.json"
        return DataSplitManager(
            sessions_dir=sessions_dir,
            metadata_path=metadata_path
        )

    # --- Basic Classification Tests ---

    def test_mark_in_sample(self, manager):
        """Test marking a session as in-sample."""
        manager.mark_in_sample("session_001")

        assert manager.is_in_sample("session_001")
        assert not manager.is_out_of_sample("session_001")

    def test_mark_out_of_sample(self, manager):
        """Test marking a session as out-of-sample."""
        manager.mark_out_of_sample("session_002")

        assert manager.is_out_of_sample("session_002")
        assert not manager.is_in_sample("session_002")

    def test_initial_state_empty(self, manager):
        """Test that new manager has no classified sessions."""
        assert len(manager.get_in_sample_sessions()) == 0
        assert len(manager.get_validation_sessions()) == 0

    # --- Data Leakage Prevention Tests ---

    def test_prevent_in_sample_becoming_out_of_sample(self, manager):
        """Critical: Once in-sample, cannot become out-of-sample (data leakage prevention)."""
        manager.mark_in_sample("session_001")

        with pytest.raises(ValueError, match="already in-sample"):
            manager.mark_out_of_sample("session_001")

    def test_out_of_sample_can_stay_out_of_sample(self, manager):
        """Idempotent: marking OOS session as OOS again is safe."""
        manager.mark_out_of_sample("session_002")
        manager.mark_out_of_sample("session_002")  # Should not raise

        assert manager.is_out_of_sample("session_002")

    def test_validate_no_leakage_raises_for_in_sample(self, manager):
        """Test validate_no_leakage rejects in-sample sessions."""
        manager.mark_in_sample("session_001")
        manager.mark_out_of_sample("session_002")

        # Should raise for in-sample session
        with pytest.raises(ValueError, match="in-sample"):
            manager.validate_no_leakage(["session_001", "session_002"])

    def test_validate_no_leakage_passes_for_clean_sessions(self, manager):
        """Test validate_no_leakage passes when all sessions are OOS or unclassified."""
        manager.mark_out_of_sample("session_002")

        # Should not raise
        manager.validate_no_leakage(["session_002", "session_003"])

    # --- Session Retrieval Tests ---

    def test_get_validation_sessions_returns_paths(self, manager, sessions_dir):
        """Test get_validation_sessions returns Path objects to OOS session files."""
        manager.mark_out_of_sample("session_002")
        manager.mark_out_of_sample("session_003")

        validation_sessions = manager.get_validation_sessions()

        assert len(validation_sessions) == 2
        assert all(isinstance(p, Path) for p in validation_sessions)
        assert sessions_dir / "session_002.jsonl" in validation_sessions
        assert sessions_dir / "session_003.jsonl" in validation_sessions

    def test_get_in_sample_sessions_returns_ids(self, manager):
        """Test get_in_sample_sessions returns session IDs (strings)."""
        manager.mark_in_sample("session_001")
        manager.mark_in_sample("session_003")

        in_sample = manager.get_in_sample_sessions()

        assert len(in_sample) == 2
        assert "session_001" in in_sample
        assert "session_003" in in_sample

    # --- Persistence Tests ---

    def test_save_and_load_roundtrip(self, temp_dir, sessions_dir):
        """Test that save/load preserves all classifications."""
        metadata_path = temp_dir / "split_metadata.json"

        # Create manager, classify sessions, save
        manager1 = DataSplitManager(sessions_dir, metadata_path)
        manager1.mark_in_sample("session_001")
        manager1.mark_out_of_sample("session_002")
        manager1.mark_out_of_sample("session_003")
        manager1.save()

        # Load into new manager instance
        manager2 = DataSplitManager(sessions_dir, metadata_path)
        manager2.load()

        # Verify classifications preserved
        assert manager2.is_in_sample("session_001")
        assert manager2.is_out_of_sample("session_002")
        assert manager2.is_out_of_sample("session_003")
        assert len(manager2.get_in_sample_sessions()) == 1
        assert len(manager2.get_validation_sessions()) == 2

    def test_load_nonexistent_file_creates_empty_state(self, temp_dir, sessions_dir):
        """Test loading from non-existent file initializes empty manager."""
        metadata_path = temp_dir / "nonexistent.json"

        manager = DataSplitManager(sessions_dir, metadata_path)
        manager.load()  # Should not raise

        assert len(manager.get_in_sample_sessions()) == 0
        assert len(manager.get_validation_sessions()) == 0

    def test_save_creates_valid_json(self, manager, temp_dir):
        """Test that saved metadata is valid JSON."""
        manager.mark_in_sample("session_001")
        manager.mark_out_of_sample("session_002")
        manager.save()

        metadata_path = temp_dir / "split_metadata.json"
        assert metadata_path.exists()

        # Verify valid JSON
        with open(metadata_path) as f:
            data = json.load(f)

        assert "in_sample_sessions" in data
        assert "out_of_sample_sessions" in data
        assert isinstance(data["in_sample_sessions"], list)
        assert isinstance(data["out_of_sample_sessions"], list)

    # --- Session Discovery Tests ---

    def test_discover_sessions(self, manager, sessions_dir):
        """Test discovering all available session files."""
        discovered = manager.discover_sessions()

        assert len(discovered) == 3
        assert "session_001" in discovered
        assert "session_002" in discovered
        assert "session_003" in discovered

    def test_get_unclassified_sessions(self, manager):
        """Test getting sessions that are neither in-sample nor out-of-sample."""
        manager.mark_in_sample("session_001")

        unclassified = manager.get_unclassified_sessions()

        assert len(unclassified) == 2
        assert "session_002" in unclassified
        assert "session_003" in unclassified
