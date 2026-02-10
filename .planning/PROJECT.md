# Polymarket Copy Trading Bot

## What This Is

A Python bot that copies trades from a highly profitable Polymarket whale address, scaling positions down to a small budget (~$50/hour). It monitors the leader's blockchain activity, feeds trades through the profit_taker strategy with conviction-based filtering, and executes via dry-run or live adapters. Includes a full session recording/replay framework for backtesting, a statistical validation pipeline, and strategy comparison infrastructure.

## Core Value

Reproduce the leader's profitable trades at a smaller scale using conviction-based filtering to maximize risk-adjusted returns — focus capital on trades where the leader has proven high confidence.

## Requirements

### Validated

- ✓ Leader trade detection via Polymarket API and Polygon blockchain — existing
- ✓ Event-driven strategy framework with pluggable strategies — existing
- ✓ Execution adapter pattern (dry-run vs live) with safety isolation — existing
- ✓ Session recording to JSONL for offline replay — existing
- ✓ Session replay through any strategy with PnL tracking — existing
- ✓ Portfolio tracking with cost basis and realized/unrealized PnL — existing
- ✓ Risk caps: per-market, per-side, global exposure — existing
- ✓ 7+ strategy implementations (mirror, momentum, conservative, aggressive, spread-aware, velocity, price-level, profit_taker) — existing
- ✓ Grid search optimizer across strategies and execution modes — existing
- ✓ YAML-based configuration with env var overrides — existing
- ✓ Statistical validation: bootstrap CIs, paired t-tests, sample size checks — v1.1
- ✓ Strategy comparison: side-by-side equity curves, metrics tables, decision matrices — v1.1
- ✓ QuantStats HTML tear sheets per strategy — v1.1
- ✓ Regime analysis: session classification by time/volatility — v1.1
- ✓ Codebase cleanup: shared utils, mixins, consolidated enums — v1.1
- ✓ profit_taker with conviction filter: Sharpe 0.345, $324 PnL, 50% WR — v1.1
- ✓ Train/test/holdout validation splits (46h/32h/8h) — v1.1
- ✓ Out-of-sample validation: positive on all data splits — v1.1

### Active

- [ ] Codebase modularity: single source of truth for trade logic (sim/runner/live share code)
- [ ] Root directory cleanup: remove/organize 60+ experiment scripts
- [ ] Detection latency optimization: minimize time from leader trade to our detection
- [ ] Execution speed optimization: minimize time from decision to order placed
- [ ] Live WebSocket order placement: market and limit orders on Polymarket
- [ ] Order lifecycle management: detect rejections/cancellations and adapt quickly
- [ ] Live environment hardening: handle env var issues, connectivity, edge cases
- [ ] Live execution monitoring and alerting

### Out of Scope

- ML-based strategy optimization — insufficient data (~200 trades), will overfit
- UI or dashboard — CLI output is sufficient
- Mobile/web interface — this is a local bot
- Multi-leader tracking — single leader focus
- Order book depth modeling — Polymarket 1h markets have thin books

## Context

- Leader is an identified Polymarket whale with verified profitability
- Leader makes ~$154/hr across 86 hours of recorded data ($13,250 total)
- profit_taker captures $324 of theoretical $739 maximum (44% of perfect mirror)
- Budget is ~$50/hour, making conviction-based filtering critical
- Best config: cumulative $300 conviction threshold, $500 price override, 5x boost, $50/hr budget
- Train/test/holdout split: 46h/32h/8h — all positive
- 100+ strategy variants tested; conviction filter is the key edge
- Bot has never run live — all experience is dry-run and replay
- 60+ experimental analysis scripts in root (cleanup needed)

## Constraints

- **Budget**: ~$50/hour — conviction filtering ensures capital goes to highest-confidence trades
- **Tech stack**: Python, existing architecture — build on what's here, don't rewrite
- **Execution**: Polymarket CLOB API via py-clob-client — constrained by API limits and minimum order sizes
- **Minimum orders**: Polymarket requires min $1 market orders, min 5 shares limit orders
- **Data**: 86 hours of recorded sessions across 6 days (Feb 3-8, 2026)

## Current Milestone: v1.2 Production Ready

**Goal:** Clean up codebase for modularity and scalability, then enable live WebSocket trading with robust order management.

**Target features:**
- Codebase cleanup: shared trade logic modules, remove clutter, optimize speed
- Live trading: WebSocket order placement, rejection handling, environment hardening

**Best Strategy Config (2026-02-09):**
- Sharpe 0.345, PnL $324, WR 50%, MaxLoss -$26
- Key params: cum$300, dd12/24, b5, hi85, late3, budget50, noLo@500
- Train $194, Test $114, Holdout $16 — all positive

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Focus on simulation before live | Need confidence in numbers before risking real money | ✓ Good (v1.0+v1.1 complete) |
| Single leader strategy | Identified whale is proven profitable | ✓ Good |
| Small budget optimization | Under $100 means sizing is the critical variable | ✓ Good |
| Conservative as baseline | Only profitable strategy in 12-session overnight test | ✓ Good (baseline established, then beaten) |
| Conviction filter at $300 cumulative | Leader's cumulative spend predicts win rate | ✓ Good (Sharpe 0.345) |
| Price override at $500 conviction | At $500+ conviction, leader has 95% WR, safe to buy cheap tokens | ✓ Good (+$58 PnL from override) |
| Manual optimization over formal GSD phases | Direct experimentation faster than building analysis infrastructure | ✓ Good (achieved goal in 4 days) |
| Train/test/holdout validation | Prevent overfitting with proper data splits | ✓ Good (positive on all splits) |
| Pattern discovery deferred | v1.2 research track, not blocking strategy work | — Pending |

---
*Last updated: 2026-02-09 after v1.2 milestone start*
