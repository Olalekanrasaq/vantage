from datetime import date, timedelta
from flask import render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required, current_user
from flask import abort
from functools import wraps
from sqlalchemy import func, distinct

from app.blueprints.manager import manager_bp
from app.extensions import db
from app.models import (
    Staff, Business, StaffBusinessAssignment, BusinessManager,
    DailyReport, WeeklyReport, NTTReport, RetentionReport, FetchLog
)
from app.services.extraction_service import run_extraction


def manager_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(403)
        if not isinstance(current_user, BusinessManager):
            abort(403)
        if not current_user.is_active:
            return redirect(url_for("payment.subscribe"))
        return f(*args, **kwargs)
    return decorated


def _get_dashboard_metrics(manager_id, report_date, prev_date, date_2_days_ago):
    """Compute all dashboard metrics for a given date and its previous date."""

    def _daily_metrics(r_date, p_date):
        # use prev_date if r_date is not available and use 
        if not DailyReport.query.filter_by(manager_id=manager_id, report_date=r_date).first():
            r_date = p_date
        rows = DailyReport.query.filter_by(manager_id=manager_id, report_date=r_date).all()
        total = len(set(r.business_name for r in rows))
        met = sum(1 for r in rows if str(r.target_met).strip().lower() == "true")
        return total, met

    def _weekly_metrics(r_date, p_date):
        # use prev_date if r_date is not available
        if not WeeklyReport.query.filter_by(manager_id=manager_id, report_date=r_date).first():
            r_date = p_date
        rows = WeeklyReport.query.filter_by(manager_id=manager_id, report_date=r_date).all()
        total = len(set(r.business_name for r in rows))
        met = sum(1 for r in rows if str(r.target_met).strip().lower() == "true")
        return total, met

    def _ntt_count(r_date, p_date):
        # use prev_date if r_date is not available
        if not NTTReport.query.filter_by(manager_id=manager_id, report_date=r_date).first():
            r_date = p_date
        return NTTReport.query.filter_by(manager_id=manager_id, report_date=r_date).count()

    def _retention_count(r_date, p_date):
        # use prev_date if r_date is not available
        if not RetentionReport.query.filter_by(manager_id=manager_id, report_date=r_date).first():
            r_date = p_date
        return RetentionReport.query.filter_by(manager_id=manager_id, report_date=r_date).count()

    # Today
    total_biz, daily_met = _daily_metrics(report_date, prev_date)
    weekly_total, weekly_met = _weekly_metrics(report_date, prev_date)
    ntt_count = _ntt_count(report_date, prev_date)
    retention_count = _retention_count(report_date, prev_date)

    # Previous day
    prev_total, prev_daily_met = _daily_metrics(prev_date, date_2_days_ago)
    prev_weekly_total, prev_weekly_met = _weekly_metrics(prev_date, date_2_days_ago)
    prev_ntt = _ntt_count(prev_date, date_2_days_ago)
    prev_retention = _retention_count(prev_date, date_2_days_ago)

    def pct(part, total):
        return round(part / total * 100, 1) if total else 0

    def delta(curr, prev):
        return curr - prev

    return {
        # Current counts
        "total_businesses": total_biz,
        "daily_met": daily_met,
        "daily_met_pct": pct(daily_met, total_biz),
        "weekly_met": weekly_met,
        "weekly_met_pct": pct(weekly_met, weekly_total),
        "weekly_not_met": weekly_total - weekly_met,
        "ntt_count": ntt_count,
        "ntt_pct": pct(ntt_count, total_biz),
        "retention_count": retention_count,
        # Deltas vs previous day
        "delta_total": delta(total_biz, prev_total),
        "delta_daily_met": delta(daily_met, prev_daily_met),
        "delta_weekly_met": delta(weekly_met, prev_weekly_met),
        "delta_ntt": delta(ntt_count, prev_ntt),
        "delta_retention": delta(retention_count, prev_retention),
    }


def _get_dashboard_tables(manager_id, report_date, prev_date, business_filter=None):
    """
    Fetch the three dashboard tables.
    business_filter: optional list of business names to restrict to (for staff).
    """
    # Weekly not-met table
    # if report_date is not available, fallback to prev_date
    if not WeeklyReport.query.filter_by(manager_id=manager_id, report_date=report_date).first():
        report_date = prev_date
    weekly_q = WeeklyReport.query.filter_by(
        manager_id=manager_id,
        report_date=report_date,
    ).filter(
        db.func.lower(WeeklyReport.target_met) != "true"
    )

    # NTT table
    if not NTTReport.query.filter_by(manager_id=manager_id, report_date=report_date).first():
        report_date = prev_date
    ntt_q = NTTReport.query.filter_by(
        manager_id=manager_id,
        report_date=report_date,
    )

    # Retention table
    if not RetentionReport.query.filter_by(manager_id=manager_id, report_date=report_date).first():
        report_date = prev_date
    retention_q = RetentionReport.query.filter_by(
        manager_id=manager_id,
        report_date=report_date,
    )

    if business_filter is not None:
        weekly_q = weekly_q.filter(WeeklyReport.business_name.in_(business_filter))
        ntt_q = ntt_q.filter(NTTReport.business_name.in_(business_filter))
        retention_q = retention_q.filter(RetentionReport.business_name.in_(business_filter))

    weekly_not_met = weekly_q.order_by(WeeklyReport.payment_value.desc()).all()
    ntt_list = ntt_q.order_by(NTTReport.days_last_transact.asc()).all()
    retention_list = retention_q.order_by(RetentionReport.days_decline.asc()).all()

    return weekly_not_met, ntt_list, retention_list


# ── Dashboard ─────────────────────────────────────────────────────────────────

@manager_bp.route("/dashboard")
@login_required
@manager_required
def dashboard():
    today = date.today()
    todays_report_date = today - timedelta(days=1)
    yesterdays_report_date = today - timedelta(days=2)
    date_2_days_ago = today - timedelta(days=3)

    metrics = _get_dashboard_metrics(
        current_user.id, todays_report_date, yesterdays_report_date, date_2_days_ago
    )
    weekly_not_met, ntt_list, retention_list = _get_dashboard_tables(
        current_user.id, todays_report_date, yesterdays_report_date
    )
    staffs = Staff.query.filter_by(manager_id=current_user.id, is_active=True).all()

    return render_template(
        "manager/dashboard.html",
        metrics=metrics,
        weekly_not_met=weekly_not_met,
        ntt_list=ntt_list,
        retention_list=retention_list,
        report_date=todays_report_date,
        staffs=staffs,
    )


# ── Analysis ──────────────────────────────────────────────────────────────────

@manager_bp.route("/analysis", methods=["GET", "POST"])
@login_required
@manager_required
def analysis():
    results = None
    date_1 = date_2 = None

    if request.method == "POST":
        try:
            date_1 = date.fromisoformat(request.form.get("date_1"))
            date_2 = date.fromisoformat(request.form.get("date_2"))
        except (ValueError, TypeError):
            flash("Invalid date selection.", "error")
            return render_template("manager/analysis.html")

        report_date_1 = date_1 - timedelta(days=1)
        report_date_2 = date_2 - timedelta(days=1)

        metrics_1 = _get_dashboard_metrics(current_user.id, report_date_1,
                                           report_date_1 - timedelta(days=1))
        metrics_2 = _get_dashboard_metrics(current_user.id, report_date_2,
                                           report_date_2 - timedelta(days=1))

        wk1, ntt1, ret1 = _get_dashboard_tables(current_user.id, report_date_1, report_date_1 - timedelta(days=1))
        wk2, ntt2, ret2 = _get_dashboard_tables(current_user.id, report_date_2, report_date_2 - timedelta(days=1))

        results = {
            "date_1": date_1, "date_2": date_2,
            "metrics_1": metrics_1, "metrics_2": metrics_2,
            "weekly_not_met_1": wk1, "weekly_not_met_2": wk2,
            "ntt_1": ntt1, "ntt_2": ntt2,
            "retention_1": ret1, "retention_2": ret2,
        }

    return render_template("manager/analysis.html", results=results,
                           date_1=date_1, date_2=date_2)


# ── Staff management ──────────────────────────────────────────────────────────

@manager_bp.route("/staffs")
@login_required
@manager_required
def staffs():
    all_staffs = Staff.query.filter_by(manager_id=current_user.id).all()
    return render_template("manager/staff_setup.html", staffs=all_staffs)


@manager_bp.route("/staffs/add", methods=["POST"])
@login_required
@manager_required
def add_staff():
    username = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("manager.staffs"))

    existing = Staff.query.filter_by(
        manager_id=current_user.id, username=username
    ).first()
    if existing:
        flash(f"A staff with username '{username}' already exists.", "error")
        return redirect(url_for("manager.staffs"))

    staff = Staff(manager_id=current_user.id, username=username, full_name=full_name)
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


# ── Business assignment ───────────────────────────────────────────────────────

PAYMENT_BANDS = {
    "lt100k":    (0,          100,000),
    "100k_500k": (100,000,    500,000),
    "500k_1m":   (500,000,  1,000,000),
    "1m_2m":   (1,000,000,  2,000,000),
    "gt2m":    (2,000,000,  float("inf")),
}

@manager_bp.route("/businesses")
@login_required
@manager_required
def businesses():
    today = date.today()
    latest_date = today - timedelta(days=1)
    date_2_days_ago = today - timedelta(days=2)

    search = request.args.get("search", "").strip()
    band = request.args.get("band", "")
    ntt_only = request.args.get("ntt_only", "")

    # Start from the latest weekly report as the source of businesses
    # if latest_date is not available, fallback to 2 days ago
    if not WeeklyReport.query.filter_by(manager_id=current_user.id, report_date=latest_date).first():
        latest_date = date_2_days_ago

    query = db.session.query(WeeklyReport).filter_by(
        manager_id=current_user.id,
        report_date=latest_date,
    )

    if search:
        query = query.filter(WeeklyReport.business_name.ilike(f"%{search}%"))

    if band and band in PAYMENT_BANDS:
        low, high = PAYMENT_BANDS[band]
        query = query.filter(WeeklyReport.payment_value >= low)
        if high != float("inf"):
            query = query.filter(WeeklyReport.payment_value < high)

    weekly_rows = query.order_by(WeeklyReport.payment_value.desc()).all()

    # If NTT filter is active, restrict to businesses in NTT report
    if ntt_only:
        ntt_names = {
            r.business_name for r in
            NTTReport.query.filter_by(
                manager_id=current_user.id, report_date=latest_date
            ).all()
        }
        weekly_rows = [r for r in weekly_rows if r.business_name in ntt_names]

    # Build a map of business_name → assignment info
    all_businesses = Business.query.filter_by(
        manager_id=current_user.id, is_active=True
    ).all()
    biz_map = {b.name: b for b in all_businesses}

    all_staffs = Staff.query.filter_by(
        manager_id=current_user.id, is_active=True
    ).all()

    return render_template(
        "manager/business_setup.html",
        weekly_rows=weekly_rows,
        biz_map=biz_map,
        staffs=all_staffs,
        search=search,
        band=band,
        ntt_only=ntt_only,
        payment_bands=PAYMENT_BANDS,
        latest_date=latest_date,
    )


@manager_bp.route("/businesses/assign", methods=["POST"])
@login_required
@manager_required
def assign_business():
    staff_id = request.form.get("staff_id", type=int)
    business_name = request.form.get("business_name", "").strip()

    staff = Staff.query.filter_by(id=staff_id, manager_id=current_user.id).first_or_404()

    # Get or create the Business record
    business = Business.query.filter_by(
        manager_id=current_user.id, name=business_name
    ).first()
    if not business:
        business = Business(manager_id=current_user.id, name=business_name)
        db.session.add(business)
        db.session.flush()

    existing = StaffBusinessAssignment.query.filter_by(
        staff_id=staff_id, business_id=business.id
    ).first()
    if existing:
        flash(f"'{business_name}' is already assigned to {staff.username}.", "warning")
        return redirect(url_for("manager.businesses") + _preserve_filters())

    assignment = StaffBusinessAssignment(staff_id=staff_id, business_id=business.id)
    db.session.add(assignment)
    db.session.commit()
    flash(f"'{business_name}' assigned to {staff.username}.", "success")
    return redirect(url_for("manager.businesses") + _preserve_filters())


@manager_bp.route("/businesses/unassign/<int:assignment_id>", methods=["POST"])
@login_required
@manager_required
def unassign_business(assignment_id):
    assignment = StaffBusinessAssignment.query.get_or_404(assignment_id)
    db.session.delete(assignment)
    db.session.commit()
    flash("Business unassigned.", "info")
    return redirect(url_for("manager.businesses") + _preserve_filters())


def _preserve_filters():
    """Carry search/filter params back to the businesses page after POST."""
    params = []
    for key in ("search", "band", "ntt_only"):
        val = request.form.get(key, "")
        if val:
            params.append(f"{key}={val}")
    return ("?" + "&".join(params)) if params else ""


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
        except Exception as e:
            flash(f"Extraction failed: {str(e)}", "error")

    return render_template("manager/upload.html")
