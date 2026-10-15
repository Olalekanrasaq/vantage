import os
import csv
import io
import json
import html
import markdown as md
from xhtml2pdf import pisa
from collections import Counter, defaultdict
from types import SimpleNamespace

from google import genai
from google.genai import types
from decouple import config
from collections import Counter
from decimal import Decimal

from datetime import date, timedelta, datetime, timezone
from flask import render_template, redirect, url_for, request, flash, current_app, Response, jsonify, session, make_response, abort, send_file
from flask_login import login_required, current_user, logout_user
from flask import abort
from functools import wraps, lru_cache
from sqlalchemy import func, distinct, case, update, select, literal_column, union_all, and_
from sqlalchemy.orm import selectinload, joinedload, contains_eager

from app.blueprints.manager import manager_bp
from app.extensions import db, limiter
from app.models import (
    Staff, Business, StaffBusinessAssignment, BusinessManager,
    DailyReport, WeeklyReport, NTTReport, RetentionReport, FetchLog,
    StaffActivityReport, RecoveryTask, FieldVisit, CallLog, DailyBriefing, StaffDailyPerformance
)
from app.services.extraction_service import run_extraction, ExtractionError
from app.services.cache_service import cached, freeze_page, bump_manager_version
from app.services.briefing_service import generate_briefing, briefing_performance
from config import Config

daily_target = Config.DAILY_TARGET
weekly_target = Config.WEEKLY_TARGET

staff_loader = selectinload(Staff.business_assignments).joinedload(StaffBusinessAssignment.business)

def manager_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not isinstance(current_user, BusinessManager):
            abort(403)
        if not current_user.has_access:
            manager_id = current_user.id
            logout_user()
            session["pending_manager_id"] = manager_id
            flash("Your subscription has expired. Please renew to continue.", "error")
            return redirect(url_for("payment.checkout"))
        return f(*args, **kwargs)
    return decorated

def _summaries(manager_id, dates, business_names=None):
    """One round trip: {(kind, date): (rows, distinct_businesses, met)} for all four report types."""
    kinds = (
        ("daily", DailyReport, daily_target),
        ("weekly", WeeklyReport, weekly_target),
        ("ntt", NTTReport, None),
        ("retention", RetentionReport, None),
    )
    parts = []
    for kind, model, target in kinds:
        met = (func.coalesce(func.sum(case((model.payment_value >= target, 1), else_=0)), 0)
               if target is not None else literal_column("0"))
        q = (select(
                literal_column(f"'{kind}'").label("kind"),
                model.report_date.label("d"),
                func.count().label("n_rows"),
                func.count().label("n_biz"),          # a terminal counts as one business
                met.label("n_met"))
             .where(model.manager_id == manager_id, model.report_date.in_(dates))
             .group_by(model.report_date))
        if business_names is not None:
            q = q.where(model.business_name.in_(business_names))
        parts.append(q)

    out = {}
    for kind, d, n_rows, n_biz, n_met in db.session.execute(union_all(*parts)):
        out[(kind, d)] = (n_rows, n_biz, int(n_met))
    return out


def _first_with_data(summ, kind, candidates):
    col = 1 if kind in ("daily", "weekly") else 0     # distinct businesses vs raw rows
    for d in candidates:
        row = summ.get((kind, d))
        if row and row[col]:
            return d, row
    return candidates[0], (0, 0, 0)


def _cur_prev(summ, kind, dates):
    """Current = first of the two newest dates with data; previous = the next one after it."""
    cur_d, cur = _first_with_data(summ, kind, dates[:2])
    start = dates.index(cur_d) + 1
    _, prev = _first_with_data(summ, kind, dates[start:start + 2])
    return cur_d, cur, prev


def _get_dashboard_metrics(manager_id, report_date, prev_date, date_2_days_ago,
                           business_names=None, staff_id=None):
    dates = [report_date, prev_date, date_2_days_ago, date_2_days_ago - timedelta(days=1)]
    summ = _summaries(manager_id, dates, business_names)

    as_of, d, pd_ = _cur_prev(summ, "daily", dates)
    _, w, pw = _cur_prev(summ, "weekly", dates)
    _, n, pn = _cur_prev(summ, "ntt", dates)
    _, r, pr = _cur_prev(summ, "retention", dates)

    pending_q = db.session.query(func.count()).select_from(RecoveryTask).filter(
        RecoveryTask.manager_id == manager_id, RecoveryTask.status == 'PENDING')
    if staff_id is not None:
        pending_q = pending_q.filter(RecoveryTask.staff_id == staff_id)
    pending_tasks = pending_q.scalar() or 0

    def pct(part, total):
        return round(part / total * 100, 1) if total else 0

    total_biz, daily_met = d[1], d[2]
    weekly_total, weekly_met = w[1], w[2]
    return {
        "as_of": as_of,
        "total_businesses": total_biz,
        "daily_met": daily_met,
        "daily_met_pct": pct(daily_met, total_biz),
        "weekly_met": weekly_met,
        "weekly_met_pct": pct(weekly_met, weekly_total),
        "weekly_not_met": weekly_total - weekly_met,
        "ntt_count": n[0],
        "ntt_pct": pct(n[0], total_biz),
        "retention_count": r[0],
        "pending_tasks": pending_tasks,
        "delta_total": total_biz - pd_[1],
        "delta_daily_met": daily_met - pd_[2],
        "delta_weekly_met": weekly_met - pw[2],
        "delta_ntt": n[0] - pn[0],
        "delta_retention": r[0] - pr[0],
    }


def _table_query(model, manager_id, report_date, prev_date, business_names=None, extra=None):
    # Picks report_date if it has rows, else prev_date, inside the same query (no extra round trip)
    latest = (select(func.max(model.report_date))
              .correlate(None)
              .where(model.manager_id == manager_id,
                     model.report_date.in_([report_date, prev_date]))
              .scalar_subquery())
    q = model.query.filter(model.manager_id == manager_id, model.report_date == latest)
    if extra is not None:
        q = q.filter(extra)
    if business_names is not None:
        q = q.filter(model.business_name.in_(business_names))
    return q


def _get_dashboard_tables(manager_id, report_date, prev_date, business_filter=None,
                          weekly_page=1, ntt_page=1, retention_page=1, only=None):
    weekly_not_met = ntt_list = retention_list = None
    if only in (None, "weekly"):
        weekly_not_met = (_table_query(WeeklyReport, manager_id, report_date, prev_date,
                                       business_filter, WeeklyReport.payment_value < weekly_target)
                          .order_by(WeeklyReport.payment_value.desc(), WeeklyReport.id.desc())
                          .paginate(page=weekly_page, per_page=10, error_out=False))
    if only in (None, "ntt"):
        ntt_list = (_table_query(NTTReport, manager_id, report_date, prev_date, business_filter)
                    .order_by(NTTReport.days_last_transact.asc(), NTTReport.id.desc())
                    .paginate(page=ntt_page, per_page=10, error_out=False))
    if only in (None, "retention"):
        retention_list = (_table_query(RetentionReport, manager_id, report_date, prev_date, business_filter)
                          .order_by(RetentionReport.days_decline.asc(), RetentionReport.id.desc())
                          .paginate(page=retention_page, per_page=10, error_out=False))
    return weekly_not_met, ntt_list, retention_list

TABLE_VARS = {"weekly": "weekly_not_met", "ntt": "ntt_list", "retention": "retention_list"}
TABLE_INDEX = {"weekly": 0, "ntt": 1, "retention": 2}

def _cached_table(kind, manager_id, report_date, prev_date, page, business_filter=None, scope="m"):
    """One page of one dashboard table, from cache when possible."""
    def compute():
        tables = _get_dashboard_tables(
            manager_id, report_date, prev_date, business_filter=business_filter,
            only=kind, **{f"{kind}_page": page})
        return freeze_page(tables[TABLE_INDEX[kind]])
    return cached(manager_id, (scope, kind, page), compute)

# ── Dashboard ─────────────────────────────────────────────────────────────────

import html
import io
import markdown as md
from flask import abort, send_file
from xhtml2pdf import pisa
from app.services.briefing_service import briefing_performance


@manager_bp.route("/briefing/<briefing_date>/pdf")
@login_required
@manager_required
def briefing_pdf(briefing_date):
    try:
        day = date.fromisoformat(briefing_date)
    except ValueError:
        abort(404)

    briefing = DailyBriefing.query.filter_by(manager_id=current_user.id, briefing_date=day).first_or_404()
    perf_rows = briefing_performance(current_user.id, day) if briefing.includes_performance else []

    # Escape first so any HTML typed into staff notes is shown as text, never interpreted
    body_html = md.markdown(html.escape(briefing.content))

    page = render_template("manager/briefing_pdf.html", briefing=briefing,
                           perf_rows=perf_rows, body_html=body_html)
    out = io.BytesIO()
    result = pisa.CreatePDF(page, dest=out, link_callback=lambda uri, rel: "")  # no external fetches
    if result.err:
        abort(500)
    out.seek(0)
    return send_file(out, mimetype="application/pdf", as_attachment=True,
                     download_name=f"staff-briefing-{day.isoformat()}.pdf")

@manager_bp.route("/briefing/generate", methods=["POST"])
@login_required
@manager_required
@limiter.limit("5 per day", key_func=lambda: current_user.get_id())
def generate_briefing_now():
    day = date.today() - timedelta(days=1)
    existing = DailyBriefing.query.filter_by(manager_id=current_user.id, briefing_date=day).first()
    report_in = db.session.query(StaffDailyPerformance.id).filter_by(
        manager_id=current_user.id, report_date=day).first() is not None
    if existing and (existing.includes_performance or not report_in):
        flash("Yesterday's briefing is already up to date.", "info")
        return redirect(url_for("manager.dashboard"))
    
    try:
        briefing = generate_briefing(current_user.id, day)
    except Exception:
        current_app.logger.exception("Manual briefing failed for manager %s", current_user.id)
        flash("Could not generate the briefing right now. Please try again shortly.", "error")
        return redirect(url_for("manager.dashboard"))

    if briefing is None:
        flash("Your staff logged no calls or visits yesterday, so there is nothing to summarise.", "info")
    else:
        flash("Yesterday's briefing is ready.", "success")
    return redirect(url_for("manager.dashboard"))

def _latest_staff_reports(manager_id, page):
    # Latest date among THIS manager's staff only (it was a global max before)
    max_date = (db.session.query(func.max(StaffActivityReport.report_date))
                .select_from(StaffActivityReport)
                .join(Staff, StaffActivityReport.staff_id == Staff.id)
                .filter(Staff.manager_id == manager_id)
                .scalar_subquery())
    return (StaffActivityReport.query.join(Staff)
            .options(contains_eager(StaffActivityReport.staff))
            .filter(Staff.manager_id == manager_id, StaffActivityReport.report_date == max_date)
            .order_by(StaffActivityReport.created_at.desc())
            .paginate(page=page, per_page=10, error_out=False))


def _fragment(template, **ctx):
    resp = make_response(render_template(template, **ctx))
    resp.headers["Vary"] = "X-Requested-Table"
    return resp


TABLE_FRAGMENTS = {
    "weekly-table-content": ("weekly", "partials/weekly_table.html"),
    "ntt-table-content": ("ntt", "partials/ntt_table.html"),
    "retention-table-content": ("retention", "partials/retention_table.html"),
}


@manager_bp.route("/dashboard")
@login_required
@manager_required
def dashboard():
    weekly_page = request.args.get('weekly_page', 1, type=int)
    ntt_page = request.args.get('ntt_page', 1, type=int)
    retention_page = request.args.get('retention_page', 1, type=int)
    staff_page = request.args.get('staff_page', 1, type=int)

    today = date.today()
    todays_report_date = today - timedelta(days=1)
    yesterdays_report_date = today - timedelta(days=2)
    date_2_days_ago = today - timedelta(days=3)

    # AJAX pagination: compute and render only the table that was clicked
    mid = current_user.id
    pages = {"weekly": weekly_page, "ntt": ntt_page, "retention": retention_page}

    # AJAX pagination: one table, from cache
    target = request.headers.get("X-Requested-Table")
    if target == "staff-table-content":
        return _fragment("partials/staff_table.html",
                         latest_staff_reports=_latest_staff_reports(mid, staff_page))
    if target in TABLE_FRAGMENTS:
        only, template = TABLE_FRAGMENTS[target]
        ctx = dict.fromkeys(TABLE_VARS.values())
        ctx[TABLE_VARS[only]] = _cached_table(only, mid, todays_report_date, yesterdays_report_date, pages[only])
        return _fragment(template, **ctx)

    # Full page
    metrics = cached(mid, ("m", "metrics"), lambda: _get_dashboard_metrics(
        mid, todays_report_date, yesterdays_report_date, date_2_days_ago))
    tables = {TABLE_VARS[k]: _cached_table(k, mid, todays_report_date, yesterdays_report_date, pages[k])
              for k in TABLE_VARS}

    staffs = Staff.query.filter_by(manager_id=mid, is_active=True).options(staff_loader).all()
    latest_staff_reports = _latest_staff_reports(mid, staff_page)
    yesterday = date.today() - timedelta(days=1)
    briefing = DailyBriefing.query.filter_by(manager_id=mid, briefing_date=yesterday).first()

    # True if yesterday's report has been uploaded and processed
    report_in = db.session.query(StaffDailyPerformance.id).filter_by(
        manager_id=mid, report_date=yesterday).first() is not None
    perf_rows = briefing_performance(mid, date.today() - timedelta(days=1)) if briefing and briefing.includes_performance else []

    return render_template(
        "manager/dashboard.html",
        metrics=metrics,
        report_date=metrics["as_of"],
        staffs=staffs,
        latest_staff_reports=latest_staff_reports,
        briefing=briefing,
        report_in=report_in,
        perf_rows=perf_rows,
        **tables,
    )

# ── Staff management ──────────────────────────────────────────────────────────

@manager_bp.route("/staffs")
@login_required
@manager_required
def staffs():
    all_staffs = Staff.query.filter_by(manager_id=current_user.id).options(staff_loader).all()
    return render_template("manager/staff_setup.html", staffs=all_staffs)


@manager_bp.route("/staffs/add", methods=["POST"])
@login_required
@manager_required
def add_staff():
    username = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    password = request.form.get("password", "")
    phone = request.form.get("phone", "")

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("manager.staffs"))

    existing = Staff.query.filter_by(
        manager_id=current_user.id, username=username
    ).first()
    if existing:
        flash(f"A staff with username '{username}' already exists.", "error")
        return redirect(url_for("manager.staffs"))

    staff = Staff(manager_id=current_user.id, username=username, full_name=full_name, phone=phone)
    staff.set_password(password)
    db.session.add(staff)
    db.session.commit()
    flash(f"Staff '{username}' added successfully.", "success")
    return redirect(url_for("manager.staffs"))


@manager_bp.route("/staffs/<int:staff_id>/deactivate", methods=["POST"])
@login_required
@manager_required
def deactivate_staff(staff_id):
    staff = Staff.query.filter_by(id=staff_id, manager_id=current_user.id).first_or_404()
    staff.is_active = False
    db.session.commit()
    flash(f"Staff '{staff.username}' has been deactivated.", "info")
    return redirect(url_for("manager.staffs"))

@manager_bp.route("/staffs/<int:staff_id>/activate", methods=["POST"])
@login_required
@manager_required
def activate_staff(staff_id):
    staff = Staff.query.filter_by(id=staff_id, manager_id=current_user.id).first_or_404()
    staff.is_active = True
    db.session.commit()
    flash(f"Staff '{staff.username}' has been activated.", "info")
    return redirect(url_for("manager.staffs"))

@manager_bp.route("/staff/<int:staff_id>/edit", methods=["POST"])
@login_required
@manager_required
def edit_staff(staff_id):
    staff = Staff.query.filter_by(id=staff_id, manager_id=current_user.id).first_or_404()
    
    username = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    password = request.form.get("password", "").strip()
    phone = request.form.get("phone", "").strip()

    if not username:
        flash("Username cannot be empty.", "error")
        return redirect(url_for("manager.staffs"))

    # Check if username is taken by another staff member
    existing_user = Staff.query.filter(
        Staff.manager_id == current_user.id,
        Staff.username == username,
        Staff.id != staff_id,
    ).first()
    if existing_user:
        flash("Username is already taken by another staff member.", "error")
        return redirect(url_for("manager.staffs"))

    staff.username = username
    staff.full_name = full_name
    staff.phone = phone
    
    # Only update password if a new one was provided
    if password:
        # Adjust password hashing method based on what you use in your app (e.g., generate_password_hash)
        staff.set_password(password)

    db.session.commit()
    flash(f"Staff member '{staff.username}' updated successfully.", "success")
    return redirect(url_for("manager.staffs"))


@manager_bp.route("/staff/<int:staff_id>/delete", methods=["POST"])
@login_required
@manager_required
def delete_staff(staff_id):
    staff = Staff.query.filter_by(id=staff_id, manager_id=current_user.id).first_or_404()
    
    username = staff.username
    
    # Optional safety: Delete or unassign associated business links if necessary
    StaffBusinessAssignment.query.filter_by(staff_id=staff.id).delete()
    
    db.session.delete(staff)
    db.session.commit()
    
    flash(f"Staff member '{username}' has been deleted.", "success")
    return redirect(url_for("manager.staffs"))

# ── Business assignment ───────────────────────────────────────────────────────

PAYMENT_BANDS = {
    "lt100k":    (0,          100_000),
    "100k_500k": (100_000,    500_000),
    "500k_1m":   (500_000,  1_000_000),
    "1m_2m":     (1_000_000,  2_000_000),
    "gt2m":      (2_000_000,  float("inf")),
}

BULK_FILTER_KEYS = ("search", "band", "ntt_only", "target_met_only", "target_not_met_only", "newly_added_only")
BUSINESSES_PER_PAGE = 10


def _preserve_filters():
    """Carry search/filter params (and the page) back to the businesses page after POST."""
    params = []
    for key in BULK_FILTER_KEYS + ("page",):
        val = request.form.get(key) or request.args.get(key, "")
        if val:
            params.append(f"{key}={val}")
    return ("?" + "&".join(params)) if params else ""


def _business_filters(src):
    """Filter values from request.args (GET) or request.form (POST)."""
    return {k: (src.get(k) or "").strip() for k in BULK_FILTER_KEYS}

def _filtered_weekly_query(manager_id, f):
    """Businesses page: one row per business, totalled over all its terminals.
    A business is listed when ANY of its terminals matches the filters.
    Returns (query, latest_date)."""
    today = date.today()
    latest_date = today - timedelta(days=1)
    if not db.session.query(WeeklyReport.id).filter_by(manager_id=manager_id, report_date=latest_date).first():
        latest_date = today - timedelta(days=2)

    # 1. Terminal-level filters decide which businesses match
    matching = select(WeeklyReport.business_name).where(
        WeeklyReport.manager_id == manager_id,
        WeeklyReport.report_date == latest_date,
    )
    if f["search"]:
        matching = matching.where(WeeklyReport.business_name.ilike(f"%{f['search']}%"))

    if f["band"] in PAYMENT_BANDS:
        low, high = PAYMENT_BANDS[f["band"]]
        matching = matching.where(WeeklyReport.payment_value >= low)
        if high != float("inf"):
            matching = matching.where(WeeklyReport.payment_value < high)

    if f["target_met_only"]:
        matching = matching.where(WeeklyReport.payment_value >= weekly_target)
    elif f["target_not_met_only"]:
        matching = matching.where(WeeklyReport.payment_value < weekly_target)

    if f["ntt_only"]:
        ntt_names = select(NTTReport.business_name).where(
            NTTReport.manager_id == manager_id, NTTReport.report_date == latest_date)
        matching = matching.where(WeeklyReport.business_name.in_(ntt_names))

    # 2. One row per matching business, over ALL its terminals
    total_value = func.sum(WeeklyReport.payment_value)
    query = (
        db.session.query(
            WeeklyReport.business_name.label("business_name"),
            func.count().label("terminal_count"),
            func.sum(case((WeeklyReport.payment_value >= weekly_target, 1), else_=0)).label("terminals_met"),
            total_value.label("payment_value"),
            func.sum(WeeklyReport.payment_vol).label("payment_vol"),
            func.max(Business.first_seen_date).label("first_seen_date"),
        )
        .outerjoin(Business, and_(Business.manager_id == WeeklyReport.manager_id,
                                  Business.name == WeeklyReport.business_name))
        .filter(WeeklyReport.manager_id == manager_id,
                WeeklyReport.report_date == latest_date,
                WeeklyReport.business_name.in_(matching))
    )
    if f["newly_added_only"]:
        query = query.filter(Business.first_seen_date >= today - timedelta(days=7))

    query = query.group_by(WeeklyReport.business_name).order_by(
        total_value.desc(), WeeklyReport.business_name)
    return query, latest_date


def _selected_business_names():
    """Names posted by a bulk form: the ticked ones, or every business matching the filters."""
    if request.form.get("all_matching"):
        query, _ = _filtered_weekly_query(current_user.id, _business_filters(request.form))
        return [r.business_name for r in query.all()]
    return request.form.getlist("business_names")

@manager_bp.route("/businesses")
@login_required
@manager_required
def businesses():
    f = _business_filters(request.args)
    query, latest_date = _filtered_weekly_query(current_user.id, f)

    # ── CSV: every matching business, not just one page ───────────────────────
    if request.args.get("download") == "csv":
        rows = query.all()

        def generate_csv():
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["Business Name", "Terminals", "Terminals Meeting Target",
                             "Total Value (NGN)", "Total Volume", "Date Added"])
            for r in rows:
                writer.writerow([r.business_name, r.terminal_count, r.terminals_met or 0,
                                 r.payment_value or 0, r.payment_vol or 0, r.first_seen_date or ""])
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

        return Response(
            generate_csv(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename=vantage_businesses_{latest_date}.csv"},
        )

    # ── One page of results ───────────────────────────────────────────────────
    page = request.args.get("page", 1, type=int)
    pagination = query.paginate(page=page, per_page=BUSINESSES_PER_PAGE, error_out=False)
    names = [r.business_name for r in pagination.items]

    # Terminal breakdown for the businesses on this page only
    terminals = defaultdict(list)
    if names:
        for t in (WeeklyReport.query
                  .filter(WeeklyReport.manager_id == current_user.id,
                          WeeklyReport.report_date == latest_date,
                          WeeklyReport.business_name.in_(names))
                  .order_by(WeeklyReport.payment_value.desc())):
            terminals[t.business_name].append(t)

    weekly_rows = []
    for r in pagination.items:
        ts = terminals[r.business_name]
        days = [int(t.days_last_transact) for t in ts
                if (t.days_last_transact or "").strip().isdigit()]
        weekly_rows.append(SimpleNamespace(
            business_name=r.business_name,
            payment_value=r.payment_value or 0,
            payment_vol=r.payment_vol or 0,
            first_seen_date=r.first_seen_date,
            terminal_count=r.terminal_count,
            terminals_met=r.terminals_met or 0,
            target_met="True" if r.terminals_met == r.terminal_count else "False",
            days_last_transact=max(days) if days else None,   # longest-inactive terminal
            terminals=ts,
        ))

    # Load assignments only for the businesses on this page
    page_businesses = (
        Business.query
        .filter(Business.manager_id == current_user.id,
                Business.is_active.is_(True),
                Business.name.in_(names))
        .options(selectinload(Business.staff_assignments)
                 .joinedload(StaffBusinessAssignment.staff))
        .all()
    ) if names else []
    biz_map = {b.name: b for b in page_businesses}

    staffs = Staff.query.filter_by(manager_id=current_user.id, is_active=True).all()

    return render_template(
        "manager/business_setup.html",
        weekly_rows=weekly_rows,
        pagination=pagination,
        filter_args={k: v for k, v in f.items() if v},
        biz_map=biz_map,
        staffs=staffs,
        latest_date=latest_date,
        payment_bands=PAYMENT_BANDS,
        **f,
    )

@manager_bp.route("/businesses/assign", methods=["POST"])
@login_required
@manager_required
def assign_business():
    staff_id = request.form.get("staff_id", type=int)
    business_name = request.form.get("business_name", "").strip()
    
    # Check if a staff member was selected
    if not staff_id:
        flash("Please select a staff member.", "error")
        return redirect(url_for("manager.businesses") + _preserve_filters())
        
    staff = Staff.query.filter_by(id=staff_id, manager_id=current_user.id).first_or_404()

    # Get or create the Business record
    business = Business.query.filter_by(
        manager_id=current_user.id, name=business_name
    ).first()
    
    if not business:
        business = Business(manager_id=current_user.id, name=business_name, is_active=True)
        db.session.add(business)
        db.session.commit() # Commit so it gets an ID
        bump_manager_version(current_user.id)

    existing = StaffBusinessAssignment.query.filter_by(
        business_id=business.id
    ).first()
    
    if existing:
        # Fetch the name of the staff who currently owns this assignment
        current_staff = Staff.query.get(existing.staff_id)
        current_staff_name = current_staff.username if current_staff else "another staff member"
        
        # Block the new assignment and alert the manager
        flash(f"'{business_name}' is already assigned to {current_staff_name}. You must remove that assignment before reassigning.", "error")
        return redirect(url_for("manager.businesses") + _preserve_filters())

    assignment = StaffBusinessAssignment(staff_id=staff_id, business_id=business.id)
    db.session.add(assignment)
    db.session.commit()
    bump_manager_version(current_user.id)
    flash(f"'{business_name}' assigned to {staff.username}.", "success")
    return redirect(url_for("manager.businesses") + _preserve_filters())

@manager_bp.route("/businesses/assign/bulk", methods=["POST"])
@login_required
@manager_required
def assign_business_bulk():
    """
    No mode: if any selected business is already assigned, show a confirmation page.
    mode=keep | reassign: perform the assignment. All checks are redone server-side.
    """
    mode = request.form.get("mode", "check")
    staff_ids = [int(s) for s in request.form.getlist("staff_ids") if s.isdigit()]
    names = list(dict.fromkeys(n.strip() for n in _selected_business_names() if n.strip()))
    back = url_for("manager.businesses") + _preserve_filters()

    if not staff_ids or not names:
        flash("You must select at least one staff member and one business.", "error")
        return redirect(back)

    staffs = (Staff.query
              .filter(Staff.id.in_(staff_ids), Staff.manager_id == current_user.id)
              .order_by(Staff.id).all())
    if not staffs:
        flash("Invalid staff selection.", "error")
        return redirect(back)

    # Read-only lookups: existing Business rows and their current assignments
    biz_by_name = {
        b.name: b for b in Business.query.filter(
            Business.manager_id == current_user.id, Business.name.in_(names))
    }
    current = {}
    if biz_by_name:
        current = {
            a.business_id: a for a in StaffBusinessAssignment.query.filter(
                StaffBusinessAssignment.business_id.in_([b.id for b in biz_by_name.values()]))
        }
    owner_counts = Counter(a.staff_id for a in current.values())

    # ── Step 1: check ─────────────────────────────────────────────────────────
    if mode not in ("keep", "reassign"):
        if not owner_counts:
            mode = "reassign"  # nothing to conflict with, go straight through
        else:
            owners_by_id = {
                s.id: s for s in Staff.query.filter(
                    Staff.id.in_(list(owner_counts)), Staff.manager_id == current_user.id)
            }
            selected_ids = {s.id for s in staffs}
            owners = sorted(
                ({"name": owners_by_id[sid].full_name or owners_by_id[sid].username,
                  "count": n, "selected": sid in selected_ids}
                 for sid, n in owner_counts.items() if sid in owners_by_id),
                key=lambda o: -o["count"],
            )
            already = sum(owner_counts.values())
            flash(
                f"{already} of the {len(names)} businesses you selected are already assigned: "
                + ", ".join(f"{o['name']} ({o['count']})" for o in owners) + ".",
                "warning",
            )
            return render_template(
                "manager/bulk_confirm.html",
                owners=owners, total=len(names), already=already,
                unassigned=len(names) - already, names=names, staffs=staffs,
                filters={k: request.form.get(k) for k in BULK_FILTER_KEYS if request.form.get(k)},
                back_url=back,
            )

    # ── Step 2: perform ───────────────────────────────────────────────────────
    new_biz = []
    for n in names:
        if n not in biz_by_name:
            b = Business(manager_id=current_user.id, name=n, is_active=True)
            biz_by_name[n] = b
            new_biz.append(b)
    if new_biz:
        db.session.add_all(new_biz)
        db.session.flush()  # assigns ids

    now = datetime.now(timezone.utc)
    load = {s.id: 0 for s in staffs}     # evenly shared across the selected staff
    moves = {}                           # target staff_id -> existing assignment ids to repoint
    added = moved = kept = unchanged = 0

    for name in names:
        biz = biz_by_name[name]
        existing = current.get(biz.id)

        if existing and mode == "keep":
            kept += 1
            continue

        sid = min(load, key=load.get)    # least-loaded selected staff, ties go in staff order
        load[sid] += 1

        if existing is None:
            db.session.add(StaffBusinessAssignment(staff_id=sid, business_id=biz.id))
            added += 1
        elif existing.staff_id != sid:
            moves.setdefault(sid, []).append(existing.id)
            moved += 1
        else:
            unchanged += 1

    for target_staff_id, ids in moves.items():
        db.session.execute(
            update(StaffBusinessAssignment)
            .where(StaffBusinessAssignment.id.in_(ids))
            .values(staff_id=target_staff_id, assigned_at=now)
            .execution_options(synchronize_session=False)
        )

    db.session.commit()  # one commit for everything
    bump_manager_version(current_user.id)

    staff_label = ", ".join(s.full_name or s.username for s in staffs)
    parts = [f"Assigned {added} business(es) across {staff_label}"]
    if moved:
        parts.append(f"reassigned {moved}")
    if unchanged:
        parts.append(f"{unchanged} already with the right staff")
    if kept:
        parts.append(f"kept {kept} with their current owners")
    flash(", ".join(parts) + ".", "success")
    return redirect(back)

@manager_bp.route("/businesses/unassign/<int:assignment_id>", methods=["POST"])
@login_required
@manager_required
def unassign_business(assignment_id):
    assignment = (StaffBusinessAssignment.query
                  .join(Staff, StaffBusinessAssignment.staff_id == Staff.id)
                  .filter(StaffBusinessAssignment.id == assignment_id,
                          Staff.manager_id == current_user.id)
                  .first_or_404())
    db.session.delete(assignment)
    db.session.commit()
    bump_manager_version(current_user.id)
    flash("Business unassigned.", "info")
    return redirect(url_for("manager.businesses") + _preserve_filters())

@manager_bp.route("/businesses/unassign/bulk", methods=["POST"])
@login_required
@manager_required
def unassign_business_bulk():
    """Remove the assignment (whoever holds it) for every selected business."""
    names = list(dict.fromkeys(n.strip() for n in _selected_business_names() if n.strip()))
    back = url_for("manager.businesses") + _preserve_filters()
    if not names:
        flash("Select at least one business.", "error")
        return redirect(back)

    rows = (db.session.query(StaffBusinessAssignment.id, Staff.full_name, Staff.username)
            .join(Staff, StaffBusinessAssignment.staff_id == Staff.id)
            .join(Business, StaffBusinessAssignment.business_id == Business.id)
            .filter(Business.manager_id == current_user.id, Business.name.in_(names))
            .all())
    if not rows:
        flash("None of the selected businesses are currently assigned.", "info")
        return redirect(back)

    StaffBusinessAssignment.query.filter(
        StaffBusinessAssignment.id.in_([r.id for r in rows])
    ).delete(synchronize_session=False)
    db.session.commit()
    bump_manager_version(current_user.id)

    by_staff = Counter(r.full_name or r.username for r in rows)
    flash(f"Unassigned {len(rows)} business(es): "
          + ", ".join(f"{n} ({c})" for n, c in by_staff.most_common()) + ".", "success")
    return redirect(back)


@manager_bp.route("/staffs/<int:staff_id>/unassign", methods=["POST"])
@login_required
@manager_required
def unassign_staff_businesses(staff_id):
    """Unassign all (all=1) or the selected business_ids from one staff member."""
    staff = Staff.query.filter_by(id=staff_id, manager_id=current_user.id).first_or_404()
    q = StaffBusinessAssignment.query.filter(StaffBusinessAssignment.staff_id == staff.id)

    if request.form.get("all") != "1":
        ids = [int(i) for i in request.form.getlist("business_ids") if i.isdigit()]
        if not ids:
            flash("Select at least one business to unassign.", "error")
            return redirect(url_for("manager.staffs"))
        q = q.filter(StaffBusinessAssignment.business_id.in_(ids))

    n = q.delete(synchronize_session=False)
    db.session.commit()
    bump_manager_version(current_user.id)
    flash(f"Unassigned {n} business(es) from {staff.full_name or staff.username}.", "success")
    return redirect(url_for("manager.staffs"))


@manager_bp.route("/staffs/unassign-all", methods=["POST"])
@login_required
@manager_required
def unassign_all_businesses():
    """Remove every business assignment for every staff member of this manager."""
    n = StaffBusinessAssignment.query.filter(
        StaffBusinessAssignment.staff_id.in_(
            select(Staff.id).where(Staff.manager_id == current_user.id))
    ).delete(synchronize_session=False)
    db.session.commit()
    bump_manager_version(current_user.id)
    flash(f"Unassigned {n} business(es) from all staff.", "success")
    return redirect(url_for("manager.staffs"))

# ── Manual upload ─────────────────────────────────────────────────────────────

@manager_bp.route("/upload", methods=["GET", "POST"])
@login_required
@manager_required
def upload():
    if request.method == "POST":
        report_date_str = request.form.get("report_date")
        file = request.files.get("pdf_file")

        if not file or not report_date_str:
            flash("Please select a file and a report date.", "error")
            return render_template("manager/upload.html")

        if not file.filename.lower().endswith(".pdf"):
            flash("Only PDF files are accepted.", "error")
            return render_template("manager/upload.html")

        try:
            report_date = date.fromisoformat(report_date_str)
        except ValueError:
            flash("Invalid date format.", "error")
            return render_template("manager/upload.html")

        pdf_bytes = file.read()

        try:
            counts = run_extraction(
                manager=current_user,
                pdf_bytes=pdf_bytes,
                report_date=report_date,
                source="manual_upload",
            )
            
            flash(
                f"Report for {report_date} processed — "
                f"daily: {counts['daily']}, weekly: {counts['weekly']}, "
                f"ntt: {counts['ntt']}, retention: {counts['retention']} records.",
                "success"
            )
        except ExtractionError:
            flash("Extraction of data failed. Check the report you uploaded and try again.", "error")
        except Exception:
            current_app.logger.exception("Unexpected error during report upload")
            flash("Something went wrong while processing your report. Please try again.", "error")

    return render_template("manager/upload.html")

@manager_bp.route("/leaderboard", methods=["GET", "POST"])
@login_required
@manager_required
def leaderboard():
    staffs = Staff.query.filter_by(manager_id=current_user.id, is_active=True).all()
    
    leaderboard_data = []
    for staff in staffs:
        # Calculate resolved recovery tasks
        resolved_tasks = RecoveryTask.query.filter_by(
            staff_id=staff.id, status="RESOLVED"
        ).count()

        # Aggregate activity metrics from staff activity reports
        activities = StaffActivityReport.query.filter_by(staff_id=staff.id).all()
        total_visits = sum(a.visits for a in activities)
        total_calls = sum(a.calls for a in activities)
        total_recoveries = sum(a.recoveries for a in activities)
        total_leads = sum(a.new_leads for a in activities)

        # Simple composite score formula (can be tweaked based on your business weighting)
        score = (resolved_tasks * 10) + (total_recoveries * 5) + (total_visits * 2) + total_calls + (total_leads * 3)

        leaderboard_data.append({
            "staff": staff,
            "resolved_tasks": resolved_tasks,
            "visits": total_visits,
            "calls": total_calls,
            "recoveries": total_recoveries,
            "leads": total_leads,
            "score": score
        })

    # Sort descending by calculated score
    leaderboard_data.sort(key=lambda x: x["score"], reverse=True)

    return render_template("manager/leaderboard.html", leaderboard=leaderboard_data)

@manager_bp.route("/visits")
@login_required
@manager_required
def field_visits():
    page = request.args.get('page', 1, type=int)
    calls_page = request.args.get('calls_page', 1, type=int)
    
    # 1. Fetch Field Visits
    visits = (FieldVisit.query
        .filter_by(manager_id=current_user.id)
        .options(selectinload(FieldVisit.business), selectinload(FieldVisit.staff))
        .order_by(FieldVisit.visit_date.desc(), FieldVisit.id.desc())
        .paginate(page=page, per_page=10, error_out=False))
        
    # 2. Fetch Call Logs (Past two days only)
    two_days_ago = date.today() - timedelta(days=2)
    calls = (CallLog.query
        .filter(CallLog.manager_id == current_user.id,
                CallLog.call_date >= two_days_ago)
        .options(selectinload(CallLog.business), selectinload(CallLog.staff))
        .order_by(CallLog.call_date.desc(), CallLog.id.desc())
        .paginate(page=calls_page, per_page=10, error_out=False))
        
    return render_template("manager/field_visits.html", visits=visits, calls=calls)