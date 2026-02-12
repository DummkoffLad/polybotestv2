# Phase 9: Safety Mechanisms - Context

**Gathered:** 2026-02-11
**Status:** Ready for planning

<domain>
## Phase Boundary

Prevent catastrophic losses with fail-fast validation and runtime protection. Covers: pre-flight checks, budget cap enforcement, kill switch, and credential hygiene. Does NOT include order lifecycle, position reconciliation, or monitoring dashboards (those are Phases 10-11).

</domain>

<decisions>
## Implementation Decisions

### Pre-flight behavior
- Tiered check severity: critical checks (credentials, connectivity) are hard stop; advisory checks (wallet balance) are warnings
- Minimum wallet balance is configurable (default $50, i.e., 1 hour of budget)
- Pre-flight summary table printed on startup showing each check + pass/warn status
- Summary table includes current config values (budget, conviction threshold, etc.) so user can verify settings at a glance

### Budget enforcement
- Budget tracks **net spend** per hour (buys minus sell proceeds) — selling frees up budget within the hour
- When hourly budget limit is hit: reject the order + log an alert, continue running (resume next hour or when sells free budget)
- Budget resets at hour boundaries (consistent with existing strategy hour tracking)
- Console-only alerting for now; external alerts deferred

### Kill switch design
- Triggered via CLI command (e.g., `python main.py --kill` or separate kill script)
- Restart clears the kill state — kill only affects the current session
- Console-only alerting when kill switch fires

### Operational feedback
- Periodic safety heartbeat printed every N minutes (e.g., "Alive | Budget $32/$50 | 3 positions | No alerts")
- Console-only logging for now — external alerting is a future concern

### Claude's Discretion
- Exact logging level (minimal vs moderate) for safety events during trading
- Pre-flight check frequency: startup-only vs startup + periodic re-checks during operation
- Kill switch mechanism: OS signal-based (instant) vs flag-check (slight delay) — pick what fits the async architecture
- What happens to open positions on kill: stop trading + keep positions (we know from backtesting that selling destroys value) vs another approach
- Per-trade maximum cap: whether to add one on top of hourly budget, or rely on strategy's boost (5x) for individual trade sizing
- Budget config location: config file, env var, or both
- Whether budget enforcement reuses the existing HOURLY_BUDGET strategy param or adds a separate hard cap at execution layer

</decisions>

<specifics>
## Specific Ideas

- Budget should track net spend so selling winners frees up capital to deploy on new high-conviction trades within the same hour
- Pre-flight summary should feel like a checklist you can glance at before walking away — config values visible so you catch misconfigurations
- Kill switch is a "stop the bleeding" button, not a "close everything" button — positions resolve naturally on Polymarket

</specifics>

<deferred>
## Deferred Ideas

- External alerting (Telegram, Discord, email notifications) — future phase (OBS-02 in requirements)
- Real-time dashboard / web UI for monitoring — future phase (OBS-01 in requirements)

</deferred>

---

*Phase: 09-safety-mechanisms*
*Context gathered: 2026-02-11*
