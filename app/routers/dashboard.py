"""Operations dashboard for the order exception agent.

GET /dashboard      — serves a single-page HTML dashboard
GET /api/dashboard/stats — returns JSON stats (consumed by the HTML page)
"""
from datetime import datetime, timedelta, timezone
from textwrap import dedent

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.db import AuditLog, DeadLetterEvent

logger = structlog.get_logger()
router = APIRouter(tags=["dashboard"])

_SEVEN_DAYS = timedelta(days=7)


@router.get("/api/dashboard/stats")
async def get_stats(request: Request):
    from app.config import get_settings
    settings = get_settings()
    seven_days_ago = datetime.now(timezone.utc) - _SEVEN_DAYS

    async with AsyncSessionLocal() as session:
        total_7d = await session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.created_at >= seven_days_ago)
        )
        avg_ms = await session.scalar(
            select(func.avg(AuditLog.processing_time_ms)).where(
                AuditLog.created_at >= seven_days_ago
            )
        )
        dead_letters = await session.scalar(
            select(func.count(DeadLetterEvent.id)).where(DeadLetterEvent.resolved_at.is_(None))
        )

        by_type_rows = (
            await session.execute(
                select(AuditLog.exception_type, func.count(AuditLog.id))
                .where(AuditLog.created_at >= seven_days_ago)
                .group_by(AuditLog.exception_type)
                .order_by(func.count(AuditLog.id).desc())
            )
        ).all()

    total = total_7d or 0
    success_rate = round((total - (dead_letters or 0)) / max(total, 1) * 100, 1)
    # Estimate ROI: each automated exception triage saves ~8 minutes of manual review
    hours_saved = round(total * 8 / 60, 1)

    return {
        "window": "7d",
        "shadow_mode": settings.agent_mode == "shadow",
        "total_processed": total,
        "success_rate_pct": success_rate,
        "avg_processing_ms": round(avg_ms or 0),
        "dead_letter_queue": dead_letters or 0,
        "hours_saved_estimate": hours_saved,
        "by_exception_type": [
            {"type": r[0] or "unknown", "count": r[1]} for r in by_type_rows
        ],
    }


_DASHBOARD_HTML = dedent("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Order Exception Agent — Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  header { background: #1e293b; padding: 1.25rem 2rem;
           border-bottom: 1px solid #334155; display: flex;
           align-items: center; justify-content: space-between; }
  header h1 { font-size: 1.1rem; font-weight: 600; color: #f1f5f9; }
  header span { font-size: 0.75rem; color: #64748b; }
  .container { max-width: 1100px; margin: 0 auto; padding: 2rem; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
           gap: 1rem; margin-bottom: 2rem; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
          padding: 1.25rem; }
  .card .label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: .5rem; }
  .card .value { font-size: 2rem; font-weight: 700; color: #f8fafc; }
  .card .sub { font-size: 0.75rem; color: #64748b; margin-top: .25rem; }
  .card.green .value { color: #4ade80; }
  .card.yellow .value { color: #facc15; }
  .card.red .value { color: #f87171; }
  .card.blue .value { color: #60a5fa; }
  h2 { font-size: 0.875rem; font-weight: 600; color: #94a3b8;
       text-transform: uppercase; letter-spacing: .05em; margin-bottom: 1rem; }
  table { width: 100%; border-collapse: collapse; background: #1e293b;
          border-radius: 10px; overflow: hidden;
          border: 1px solid #334155; }
  th, td { padding: .75rem 1rem; text-align: left; font-size: .875rem; }
  th { color: #94a3b8; font-weight: 500; border-bottom: 1px solid #334155; }
  td { color: #e2e8f0; border-bottom: 1px solid #1e293b; }
  tr:last-child td { border-bottom: none; }
  .badge { display: inline-block; padding: .2rem .6rem; border-radius: 999px;
           font-size: .7rem; font-weight: 600; }
  .badge.fraud { background: #7f1d1d; color: #fca5a5; }
  .badge.address { background: #713f12; color: #fde68a; }
  .badge.high_value { background: #1e3a5f; color: #93c5fd; }
  .badge.payment { background: #4a1d96; color: #c4b5fd; }
  .badge.unknown { background: #1e293b; color: #94a3b8; }
  .refresh { font-size: .7rem; color: #475569; }
  .shadow-banner { background: #78350f; border-bottom: 2px solid #d97706;
                   padding: .6rem 2rem; font-size: .8rem; font-weight: 600;
                   color: #fde68a; letter-spacing: .03em; display: none; }
</style>
</head>
<body>
<div class="shadow-banner" id="shadow-banner">
  ⚠ SHADOW MODE — mutations are logged but not applied to Shopify
</div>
<header>
  <h1>🤖 Order Exception Agent</h1>
  <span class="refresh" id="last-updated">Loading…</span>
</header>
<div class="container">
  <div class="cards" id="cards">
    <div class="card"><div class="label">Loading…</div><div class="value">—</div></div>
  </div>
  <h2>Exception Type Breakdown — Last 7 Days</h2>
  <table><thead><tr><th>Exception Type</th><th>Count</th></tr></thead>
  <tbody id="type-rows"><tr><td colspan="2">Loading…</td></tr></tbody></table>
</div>
<script>
const BADGE = {fraud_risk:'fraud',address_invalid:'address',high_value:'high_value',payment_issue:'payment'};
async function load() {
  try {
    const r = await fetch('/api/dashboard/stats');
    const d = await r.json();
    document.getElementById('last-updated').textContent =
      'Last updated: ' + new Date().toLocaleTimeString();
    document.getElementById('shadow-banner').style.display = d.shadow_mode ? 'block' : 'none';
    document.getElementById('cards').innerHTML = `
      <div class="card blue">
        <div class="label">Events Processed (7d)</div>
        <div class="value">${d.total_processed}</div>
        <div class="sub">webhook events routed</div>
      </div>
      <div class="card ${d.success_rate_pct >= 95 ? 'green' : d.success_rate_pct >= 80 ? 'yellow' : 'red'}">
        <div class="label">Success Rate</div>
        <div class="value">${d.success_rate_pct}%</div>
        <div class="sub">${d.dead_letter_queue} in dead-letter queue</div>
      </div>
      <div class="card">
        <div class="label">Avg Processing Time</div>
        <div class="value">${(d.avg_processing_ms/1000).toFixed(1)}s</div>
        <div class="sub">end-to-end per event</div>
      </div>
      <div class="card green">
        <div class="label">Est. Hours Saved</div>
        <div class="value">${d.hours_saved_estimate}h</div>
        <div class="sub">vs manual triage @ 8 min/event</div>
      </div>`;
    document.getElementById('type-rows').innerHTML = d.by_exception_type.length
      ? d.by_exception_type.map(e => `<tr>
          <td><span class="badge ${BADGE[e.type]||'unknown'}">${e.type}</span></td>
          <td>${e.count}</td></tr>`).join('')
      : '<tr><td colspan="2" style="color:#475569">No data yet</td></tr>';
  } catch(e) {
    document.getElementById('cards').innerHTML =
      '<div class="card red"><div class="label">Error</div><div class="value">—</div>' +
      '<div class="sub">' + e.message + '</div></div>';
  }
}
load();
setInterval(load, 30000);
</script>
</body>
</html>
""")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return HTMLResponse(content=_DASHBOARD_HTML)
