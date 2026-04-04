# ADR 008 — Operations Dashboard (Inline HTML + Stats API)

**Status:** Accepted  
**Date:** 2026-04-03

## Context

Operators and stakeholders need visibility into the agent's health and throughput: how many exceptions have been processed, what the success rate is, where failures accumulate, and whether the automation is generating value. This information is already stored in `AuditLog` and `DeadLetterEvent` tables; it just needs an accessible surface.

The team does not have a dedicated observability stack (Grafana/Datadog are production-only). A lightweight in-app dashboard is sufficient for the current stage.

## Decision

Expose two endpoints from a `dashboard` FastAPI router:

- `GET /api/dashboard/stats` — queries Postgres and returns JSON (7-day window): `total_processed`, `success_rate_pct`, `avg_processing_ms`, `dead_letter_queue`, `hours_saved_estimate`, `by_exception_type`
- `GET /dashboard` — serves an inline single-page HTML dashboard that polls `/api/dashboard/stats` every 30 seconds

**Technology choices:**
- Vanilla JS + inline CSS (no build step, no npm dependency, no CDN)
- Dark navy theme matching existing internal tooling aesthetic
- 4 stat cards (events processed, success rate, avg processing time, estimated hours saved)
- Exception type breakdown table with color-coded badges
- Auto-refresh every 30 seconds

**ROI estimate:** Each automated exception triage saves ~8 minutes of manual review. `hours_saved = total_events × 8 / 60`. This is displayed as a portfolio metric.

## Alternatives Considered

| Option | Rejected because |
|--------|-----------------|
| Grafana dashboard | Grafana not available in dev/staging; requires metric export pipeline |
| React SPA | Build toolchain overhead for a single internal page |
| Third-party analytics (Amplitude, Mixpanel) | Data sensitivity; external service dependency |
| CLI only (`curl /api/dashboard/stats`) | Not operator-friendly; no stakeholder demo value |

## Consequences

- **Positive:** Zero new dependencies — uses existing FastAPI, SQLAlchemy, and Postgres
- **Positive:** Inline HTML is portable — works behind any reverse proxy without static file serving configuration
- **Positive:** Stats endpoint is JSON-first, making it easy to wire into external tools later
- **Negative:** No authentication on `/dashboard` — suitable for internal network / VPN only; add middleware if exposed publicly
- **Negative:** 7-day window is hardcoded; adjustable only by code change (acceptable for now)
