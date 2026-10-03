# Polymarket Strategy Experiments

A Python project for experimenting with copy-trading strategies, recorded-session replay, and portfolio analysis on Polymarket data.

## Repository status

The public checkout is incomplete. Several modules import `src.data`, but that package is missing from the tracked files. This prevents important execution paths and tests from running as a complete application.

The CLI still accepts `--preflight` and `--collect`, but those commands report that their modules were removed. The old `--replay` and `--compare` examples are not supported by the current argument parser.

## What is in the code

- Strategy implementations sharing a common event and decision interface.
- Session recording and replay components.
- Portfolio tracking, position sizing, and exposure limits.
- Analysis of returns, drawdowns, and execution assumptions.
- Strategy comparison and parameter-search utilities.
- Unit and integration tests.

These describe the components present in the repository, not a claim that the full system currently runs.

## Inspect the project

The requirements file specifies Python 3.11 or later. Create and activate a virtual environment, then install the dependencies:

```bash
python -m pip install -r requirements.txt
python main.py --help
```

Configuration options are documented in [config/config.example.yaml](config/config.example.yaml). The test suite is under `tests/`; collection currently depends on the missing data package.

Recorded sessions and local configuration are not included in the repository. Restoring `src.data` and checking imports, test collection, and replay behavior are the next steps toward a reproducible demo.

## Execution status

The code includes a live-order adapter. The CLI currently skips the removed preflight checks, so the earlier README's safety guarantees do not describe the current implementation. Live trading is not a validated use of this checkout. Simulation results also depend on input data and fill assumptions.

## Code map

- `src/strategies/`: strategy interface and implementations.
- `src/framework/`: runner, recording, and session replay.
- `src/execution/`: execution adapters.
- `src/core/`: portfolio, sizing, and trade logic.
- `src/analysis/`, `src/statistics/`, and `src/comparison/`: evaluation and reporting.
- `src/simulation/` and `src/validation/`: experiments and validation utilities.
- `experiments/archive/`: earlier analysis scripts.

## License

Private - for authorized use only.
