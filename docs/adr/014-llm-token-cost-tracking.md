# ADR 014 — LLM Token Usage and Cost Tracking

**Status:** Accepted  
**Date:** 2026-04-08

## Context

The `triage_event` node invokes Claude (`claude-sonnet-4-6`) on every webhook event. Token consumption and API cost are invisible by default — the dashboard showed throughput and latency but no signal on model cost or efficiency. As event volume scales, understanding cost-per-event is critical for:

- Capacity planning and budget forecasting
- Validating that prompt length is not drifting (input token growth = prompt bloat)
- Demonstrating operational cost efficiency (low $/event reinforces the automation ROI story)

## Decision

Capture `response.usage_metadata` from the LangChain `AIMessage` response in `triage_event` and propagate it through the state machine to the audit log.

**Data flow:**
```
triage_event → state (llm_input_tokens, llm_output_tokens)
             → record_audit → write_audit_log tool
             → AuditLog (input_tokens, output_tokens, cost_usd columns)
             → /api/dashboard/stats
             → Dashboard cards
```

**Schema changes:**
Three nullable columns added to `audit_logs`:
- `input_tokens INTEGER` — prompt tokens consumed
- `output_tokens INTEGER` — completion tokens generated
- `cost_usd DOUBLE PRECISION` — computed at write time from known pricing

**Cost formula** (claude-sonnet-4-6):
```
cost_usd = (input_tokens × $3.00 + output_tokens × $15.00) / 1,000,000
```

**Migration strategy:** `ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS` runs inside `init_db()` at every startup. Idempotent, no Alembic required for this additive change. Historical rows get `NULL` for token columns (accurate — they predate instrumentation).

**Dashboard additions:**
- **LLM Cost (7d)** card — total spend with avg cost/event as sub-label
- **Tokens Used (7d)** card — total (input + output) with per-direction breakdown

## Alternatives Considered

| Option | Rejected because |
|--------|-----------------|
| Store tokens only in `metadata_` JSONB | Can't aggregate with `SUM`/`AVG` efficiently; requires JSON extraction operators |
| LangFuse for cost tracking | Already optional/disabled in production; adds external dependency for a first-party metric |
| Alembic migration | No migrations exist yet; `ADD COLUMN IF NOT EXISTS` is simpler for a single additive change |
| Pricing as a config value | Model is fixed (`claude-sonnet-4-6`); hardcoding is fine until model changes, at which point the column value is the ground truth anyway |

## Consequences

- **Positive:** Cost visibility in the dashboard with no external service dependency
- **Positive:** Historical records are `NULL` for token columns — honest, not misleading
- **Positive:** Migration is startup-safe and idempotent — no downtime or manual DB ops required
- **Positive:** Output token count validates prompt brevity — the triage prompt should yield 1-word responses (~5 output tokens); drift signals prompt regression
- **Negative:** Pricing is hardcoded at write time — if Anthropic changes rates, historical `cost_usd` values remain at the old rate (acceptable; rates are stable over weeks/months)
- **Negative:** Only the triage LLM call is instrumented — if additional LLM nodes are added later, they must each capture and accumulate their own usage
