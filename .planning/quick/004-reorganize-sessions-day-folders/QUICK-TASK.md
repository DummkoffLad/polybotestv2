# Quick Task 004: Reorganize Sessions into Day Folders

## Summary
Reorganized session storage from flat files to day-based folder structure with hourly naming.

## Changes

### Migration
- Migrated existing sessions to new format:
  - `session_20260203_055651.jsonl` -> `2026-02-03/05-56.jsonl`
  - `session_20260204_023545.jsonl` -> `2026-02-04/02-35.jsonl`

### SessionRecorder (`src/framework/recorder.py`)
- Updated `start_session()` to save in new format: `data/sessions/YYYY-MM-DD/HH-MM.jsonl`
- Creates day directories automatically

### FullOptimizer (`src/simulation/full_optimizer.py`)
- Added `find_sessions()` helper to locate sessions in files or folders
- Updated to accept either single session files OR day folders
- Aggregates results across all sessions in a folder
- Shows session count and list in output

### Test Updates
- Updated `test_validation_pipeline.py` to auto-discover sessions in new format

## New Session Format

**Old:**
```
data/sessions/
  session_20260203_055651.jsonl
  session_20260204_023545.jsonl
```

**New:**
```
data/sessions/
  2026-02-03/
    05-56.jsonl
  2026-02-04/
    02-35.jsonl
```

## Usage

```bash
# Single session
python -m src.simulation.full_optimizer data/sessions/2026-02-03/05-56.jsonl

# All sessions in day folder
python -m src.simulation.full_optimizer data/sessions/2026-02-03/

# All sessions across all days
python -m src.simulation.full_optimizer data/sessions/
```

## Test Results
- 591 tests passing
- Optimizer works with both single files and folders
