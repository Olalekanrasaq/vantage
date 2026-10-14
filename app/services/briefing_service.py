import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from flask import current_app
from google import genai
from google.genai import types
from sqlalchemy import func, select

from app.extensions import db, rds
from app.models import (Business, BusinessManager, CallLog, DailyBriefing, FieldVisit,
                        Staff, StaffDailyPerformance)

log = logging.getLogger(__name__)

MODEL = "gemini-3.1-flash-lite"
MAX_TEXT = 200          # cap free-text fields so one long note can't inflate the cost
MAX_FAILURES = 3        # per manager per day, so a failing model isn't retried forever

SYSTEM_INSTRUCTION = """
You write a short daily briefing for a business manager about their field staff.
Use ONLY the data provided. Never invent names, numbers or events.

Write in markdown with these sections:
1. **Overview**: 2 to 3 sentences on the team's day.
2. **Staff**: one short paragraph per staff member: calls and visits made, notable outcomes,
   and any concern (for example no activity, many unreachable calls, escalations).
   If business_performance is present, it is shown to the manager as a separate table, so do not
   list its numbers; only point out what stands out (for example the weakest staff on targets).
3. **Follow-ups**: bullet list of next follow-up dates, escalations and issues needing the manager.

Keep it under 400 words. Use the activity_per_staff counts as given; do not recount.
"""


@lru_cache(maxsize=1)
def _client():
    return genai.Client(api_key=current_app.config["GEMINI_API_KEY"])


def _clip(text):
    text = (text or "").strip()
    return text[:MAX_TEXT] + "..." if len(text) > MAX_TEXT else text


def _table(cols, rows):
    return {"cols": cols, "rows": rows}


def _build_context(manager_id, day):
    """Returns (context_dict, has_performance, has_any_data)."""
    names = dict(db.session.execute(
        select(Staff.id, func.coalesce(Staff.full_name, Staff.username))
        .where(Staff.manager_id == manager_id)
    ).all())
    active_ids = db.session.execute(
        select(Staff.id).where(Staff.manager_id == manager_id, Staff.is_active.is_(True))
    ).scalars().all()

    calls = db.session.execute(
        select(CallLog.staff_id, Business.name, CallLog.purpose,
               CallLog.customer_response, CallLog.result)
        .select_from(CallLog).join(Business, CallLog.business_id == Business.id)
        .where(CallLog.manager_id == manager_id, CallLog.call_date == day)
    ).all()

    visits = db.session.execute(
        select(FieldVisit.staff_id, Business.name, FieldVisit.purpose, FieldVisit.issue,
               FieldVisit.action_taken, FieldVisit.result, FieldVisit.next_follow_up)
        .select_from(FieldVisit).join(Business, FieldVisit.business_id == Business.id)
        .where(FieldVisit.manager_id == manager_id, FieldVisit.visit_date == day)
    ).all()

    perf = db.session.execute(
        select(StaffDailyPerformance.staff_id, StaffDailyPerformance.assigned,
               StaffDailyPerformance.weekly_target_met, StaffDailyPerformance.daily_target_met,
               StaffDailyPerformance.not_declining, StaffDailyPerformance.transacting)
        .where(StaffDailyPerformance.manager_id == manager_id,
               StaffDailyPerformance.report_date == day)
    ).all()

    counts = defaultdict(Counter)
    for row in calls:
        counts[row.staff_id]["calls"] += 1
    for row in visits:
        counts[row.staff_id]["visits"] += 1

    name = lambda sid: names.get(sid, "Unknown staff")
    ctx = {
        "date": day.isoformat(),
        "activity_per_staff": _table(
            ["staff", "calls", "visits"],
            [[name(sid), counts[sid]["calls"], counts[sid]["visits"]] for sid in active_ids]),
        "calls": _table(
            ["staff", "business", "purpose", "customer_response", "result"],
            [[name(c.staff_id), c.name, c.purpose, _clip(c.customer_response), c.result] for c in calls]),
        "field_visits": _table(
            ["staff", "business", "purpose", "issue", "action_taken", "result", "next_follow_up"],
            [[name(v.staff_id), v.name, v.purpose, _clip(v.issue), _clip(v.action_taken),
              v.result, v.next_follow_up] for v in visits]),
    }
    if perf:
        ctx["business_performance"] = _table(
            ["staff", "assigned", "weekly_target_met", "daily_target_met", "not_declining", "transacting"],
            [[name(p.staff_id), p.assigned, p.weekly_target_met, p.daily_target_met,
              p.not_declining, p.transacting] for p in perf])

    return ctx, bool(perf), bool(calls or visits or perf)

def briefing_performance(manager_id, day):
    """Per-staff performance rows for the briefing table (dashboard and PDF)."""
    return db.session.execute(
        select(func.coalesce(Staff.full_name, Staff.username).label("staff"),
               StaffDailyPerformance.assigned,
               StaffDailyPerformance.weekly_target_met,
               StaffDailyPerformance.daily_target_met,
               StaffDailyPerformance.not_declining,
               StaffDailyPerformance.transacting)
        .select_from(StaffDailyPerformance)
        .join(Staff, Staff.id == StaffDailyPerformance.staff_id)
        .where(StaffDailyPerformance.manager_id == manager_id,
               StaffDailyPerformance.report_date == day)
        .order_by(Staff.username)
    ).all()

def generate_briefing(manager_id, day):
    """Create or refresh the briefing for `day`. Returns it, or None if there was no data."""
    ctx, has_perf, has_data = _build_context(manager_id, day)
    if not has_data:
        return None

    resp = _client().models.generate_content(
        model=MODEL,
        contents="Data:\n" + json.dumps(ctx, separators=(",", ":"), default=str),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.3,
            max_output_tokens=2048,
        ),
    )
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError("Model returned an empty briefing")

    usage = getattr(resp, "usage_metadata", None)
    briefing = (DailyBriefing.query.filter_by(manager_id=manager_id, briefing_date=day).first()
                or DailyBriefing(manager_id=manager_id, briefing_date=day))
    briefing.content = text
    briefing.includes_performance = has_perf
    briefing.input_tokens = getattr(usage, "prompt_token_count", None)
    briefing.output_tokens = ((getattr(usage, "candidates_token_count", 0) or 0)
                              + (getattr(usage, "thoughts_token_count", 0) or 0))
    briefing.created_at = datetime.now(timezone.utc)
    db.session.add(briefing)
    db.session.commit()
    return briefing


def refresh_briefings(app):
    """Scheduler job (every 5 min): briefings with performance for managers whose report is in."""
    with app.app_context():
        day = date.today() - timedelta(days=1)
        report_in = select(StaffDailyPerformance.manager_id).where(
            StaffDailyPerformance.report_date == day).distinct()
        already_done = select(DailyBriefing.manager_id).where(
            DailyBriefing.briefing_date == day, DailyBriefing.includes_performance.is_(True))

        managers = BusinessManager.query.filter(
            BusinessManager.id.in_(report_in), ~BusinessManager.id.in_(already_done)).all()

        for m in managers:
            if not m.has_access:
                continue
            fail_key = f"briefing_fail:{m.id}:{day}"
            try:
                if int(rds.client.get(fail_key) or 0) >= MAX_FAILURES:
                    continue
            except Exception:
                pass
            try:
                generate_briefing(m.id, day)
                log.info("Briefing written for manager %s, %s", m.id, day)
            except Exception:
                db.session.rollback()
                log.exception("Briefing failed for manager %s, %s", m.id, day)
                try:
                    rds.client.incr(fail_key)
                    rds.client.expire(fail_key, 86400)
                except Exception:
                    pass