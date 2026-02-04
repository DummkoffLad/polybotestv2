# Polymarket Copy Trading Bot

## What This Is

A Python bot that copies trades from a highly profitable Polymarket whale address, scaling positions down to a small budget (~$100). It monitors the leader's blockchain activity, feeds trades through pluggable strategies, and executes via dry-run or live adapters. Includes a full session recording/replay framework for backtesting and strategy optimization.

## Core Value

Reproduce the leader's profitable trades at a smaller scale with sizing that maximizes returns while protecting capital — every dollar matters at this budget.

## Requirements

### Validated

- ✓ Leader trade detection via Polymarket API and Polygon blockchain — existing
- ✓ Event-driven strategy framework with pluggable strategies — existing
- ✓ Execution adapter pattern (dry-run vs live) with safety isolation — existing
- ✓ Session recording to JSONL for offline replay — existing
- ✓ Session replay through any strategy with PnL tracking — existing
- ✓ Portfolio tracking with cost basis and realized/unrealized PnL — existing
- ✓ Risk caps: per-market ($8), per-side ($5), global exposure ($40) — existing
- ✓ 7 strategy implementations (mirror, momentum, conservative, aggressive, spread-aware, velocity, price-level) — existing
- ✓ Grid search optimizer across strategies and execution modes — existing
- ✓ YAML-based configuration with env var overrides — existing

### Active

- [ ] Strategy comparison tooling to understand why conservative wins vs others
- [ ] Per-trade attribution showing exactly which trades hurt/helped each strategy
- [ ] Root cause analysis of strategy failure modes (sizing vs timing vs selection)
- [ ] New strategy design based on conservative's winning patterns
- [ ] Validation framework on fresh recorded sessions
- [ ] Small-scale live testing if simulation results are promising

### Out of Scope

- Live trading deployment — focus is simulation quality first
- New data sources or leader discovery — leader is already identified
- UI or dashboard — CLI output is sufficient
- Mobile/web interface — this is a local bot
- Multi-leader tracking — single leader focus

## Context

- Leader is an identified Polymarket whale with verified profitability
- External testing has reproduced leader's results at smaller scale
- Budget is under $100, making position sizing the primary challenge
- Current risk caps ($8/market, $40 global) may be too rigid for small accounts
- Bot has never run live — all experience is dry-run and replay
- Sizing is the suspected main source of profit leakage vs leader
- The simulation framework is complete but realism hasn't been validated end-to-end

## Constraints

- **Budget**: ~$100 — every position must be sized carefully, no room for waste
- **Tech stack**: Python, existing architecture — build on what's here, don't rewrite
- **Execution**: Polymarket CLOB API via py-clob-client — constrained by API limits and minimum order sizes
- **Minimum orders**: Polymarket requires min $1 market orders, min 5 shares limit orders
- **No live trading**: All work validated through simulation only

## Current Milestone: v1.1 Beat Conservative

**Goal:** Build a strategy that outperforms conservative on fresh recorded sessions

**Target features:**
- Strategy debugging to understand why conservative wins
- Per-trade failure analysis for underperforming strategies
- New strategy based on insights from conservative's patterns
- Validation on fresh data + small live test

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Focus on simulation before live | Need confidence in numbers before risking real money | ✓ Good (v1.0 complete) |
| Single leader strategy | Identified whale is proven profitable | ✓ Good |
| Small budget optimization | Under $100 means sizing is the critical variable | ✓ Good |
| Conservative as baseline | Only profitable strategy in 12-session overnight test | — Pending |
| Pattern discovery deferred | v1.2 research track, not blocking strategy work | — Pending |

---
*Last updated: 2026-02-03 after v1.1 milestone start*
