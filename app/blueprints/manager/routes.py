import os
import json
from google import genai
from google.genai import types

from datetime import date, timedelta
from flask import render_template, redirect, url_for, request, flash, current_app, Response, jsonify
from flask_login import login_required, current_user
from flask import abort
from functools import wraps
from sqlalchemy import func, distinct

from app.blueprints.manager import manager_bp
from app.extensions import db
from app.models import (
    Staff, Business, StaffBusinessAssignment, BusinessManager,
    DailyReport, WeeklyReport, NTTReport, RetentionReport, FetchLog,
    StaffActivityReport, RecoveryTask, FieldVisit
)
from app.services.extraction_service import run_extraction
from dotenv import load_dotenv

load_dotenv()

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
    "lt100k":    (0,          100_000),
    "100k_500k": (100_000,    500_000),
    "500k_1m":   (500_000,  1_000_000),
    "1m_2m":   (1_000_000,  2_000_000),
    "gt2m":    (2_000_000,  float("inf")),
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
    target_met_only = request.args.get("target_met_only", "") 
    target_not_met_only = request.args.get("target_not_met_only", "") # <--- New filter

    download = request.args.get("download", "")

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
            
    # Apply Target filters (mutually exclusive logic)
    if target_met_only:
        query = query.filter(db.func.lower(WeeklyReport.target_met) == "true")
    elif target_not_met_only:
        query = query.filter(db.func.lower(WeeklyReport.target_met) != "true")

    weekly_rows = query.order_by(WeeklyReport.payment_value.desc()).all()

    if ntt_only:
        ntt_names = {
            r.business_name for r in
            NTTReport.query.filter_by(manager_id=current_user.id, report_date=latest_date).all()
        }
        weekly_rows = [r for r in weekly_rows if r.business_name in ntt_names]
        
    # --- DOWNLOAD LOGIC ---
    if download == "csv":
        def generate_csv():
            yield "Business Name,Value (NGN),Volume,Target Met,Days Inactive\n"
            for r in weekly_rows:
                name = f'"{r.business_name}"' 
                val = r.payment_value or 0
                vol = r.payment_vol or 0
                t_met = r.target_met or 'False'
                days = r.days_last_transact or ''
                yield f"{name},{val},{vol},{t_met},{days}\n"
        
        return Response(
            generate_csv(), 
            mimetype="text/csv", 
            headers={"Content-Disposition": f"attachment;filename=vantage_businesses_{latest_date}.csv"}
        )

    all_businesses = Business.query.filter_by(manager_id=current_user.id, is_active=True).all()
    biz_map = {b.name: b for b in all_businesses}
    all_staffs = Staff.query.filter_by(manager_id=current_user.id, is_active=True).all()

    return render_template(
        "manager/business_setup.html",
        weekly_rows=weekly_rows,
        biz_map=biz_map,
        staffs=all_staffs,
        search=search,
        band=band,
        ntt_only=ntt_only,
        target_met_only=target_met_only, 
        target_not_met_only=target_not_met_only, # <--- Pass to template
        payment_bands=PAYMENT_BANDS,
        latest_date=latest_date,
    )

def _preserve_filters():
    """Carry search/filter params back to the businesses page after POST."""
    params = []
    # Added target_not_met_only to the preserved list
    for key in ("search", "band", "ntt_only", "target_met_only", "target_not_met_only"): 
        val = request.form.get(key, "")
        if val:
            params.append(f"{key}={val}")
    return ("?" + "&".join(params)) if params else ""


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
    visits = FieldVisit.query.filter_by(manager_id=current_user.id).order_by(FieldVisit.visit_date.desc()).all()
    return render_template("manager/field_visits.html", visits=visits)

@manager_bp.route("/api/ai-analyze", methods=["POST"])
@login_required
@manager_required
def ai_analyze():
    user_prompt = request.json.get("prompt")
    if not user_prompt:
        return jsonify({"error": "Prompt is required"}), 400

    # Gather the Manager's Data Context (Last 30 Days for deep insights)
    thirty_days_ago = date.today() - timedelta(days=30)
    
    # 1. Staff Context
    staffs = Staff.query.filter_by(manager_id=current_user.id).all()
    staff_data = [{"username": s.username} for s in staffs]

    # 2. Daily & Weekly Reports Context
    # Adjust these attribute names to match your exact database columns
    daily_reports = DailyReport.query.filter(
        DailyReport.manager_id == current_user.id,
        DailyReport.report_date >= thirty_days_ago
    ).all()
    daily_data = [{"business": r.business_name, "date": str(r.report_date), "value": int(r.payment_value), "volume": r.payment_vol, "target": r.target_met} for r in daily_reports]

    weekly_reports = WeeklyReport.query.filter(
        WeeklyReport.manager_id == current_user.id,
        WeeklyReport.report_date >= thirty_days_ago
    ).all()
    weekly_data = [{"business": r.business_name, "date": str(r.report_date), "value": int(r.payment_value), "volume": r.payment_vol, "target_met": r.target_met} for r in weekly_reports]

    # 3. Field Visits Context
    visits = FieldVisit.query.join(Business).filter(
        Business.manager_id == current_user.id,
        FieldVisit.visit_date >= thirty_days_ago
    ).all()
    visit_data = [{"business": v.business.name, "date": str(v.visit_date), "issues": v.issue, "action": v.action_taken, "status": v.result} for v in visits]

    # 4. NTT & Retention Context (Assuming you have these models)
    ntt_reports = NTTReport.query.filter(
        NTTReport.manager_id == current_user.id, 
        NTTReport.report_date >= thirty_days_ago
    ).all()
    ntt_data = [{"business": n.business_name, "days_inactive": n.days_last_transact, "date": str(n.report_date)} for n in ntt_reports]

    retention_reports = RetentionReport.query.filter(
        RetentionReport.manager_id == current_user.id, 
        RetentionReport.report_date >= thirty_days_ago
    ).all()
    retention_data = [{"business": n.business_name, "days_decline": n.days_decline, "date": str(n.report_date), "expected_vol": n.min_volume, "actual_vol": n.vol_meet} for n in retention_reports]
    # Combine EVERYTHING into one massive context dictionary
    context_data = {
        "staff_team": staff_data,
        "daily_performance_30_days": daily_data,
        "weekly_performance_30_days": weekly_data,
        "field_visits_30_days": visit_data,
        "inactive_terminals_ntt": ntt_data,
        "retention_review": retention_data
    }

    system_instruction = """
    You are an elite AI Data Analyst for a business manager. 
    You are answering a question based ONLY on the provided JSON data context containing daily, weekly, NTT, retention and field visit data.
    
    You MUST return your response in valid JSON format exactly matching this structure:
    {
      "text_response": "Your detailed analysis, explanations, and insights in markdown format.",
      "has_table": boolean (true if a table is requested or useful, false otherwise),
      "table_headers": ["Column 1", "Column 2"] (empty if has_table is false),
      "table_data": [
         ["Row 1 Data", "Row 1 Data"],
         ["Row 2 Data", "Row 2 Data"]
      ] (empty if has_table is false)
    }
    """

    full_prompt = f"Context Data:\n{json.dumps(context_data)}\n\nManager's Question:\n{user_prompt}"

    try:
        api_key = os.environ.get('GEMINI_API_KEY')
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=full_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json"
                )
        )
        
        return jsonify(json.loads(response.text))

    except Exception as e:
        print(f"Gemini API Error: {e}")
        return jsonify({"error": "Failed to generate AI response. Please try again."}), 500