import csv
import io

from datetime import date, timedelta
from flask import render_template, abort, redirect, url_for, flash, request, current_app, Response
from flask_login import login_required, current_user, logout_user
from functools import wraps

from app.blueprints.staff import staff_bp
from app.extensions import db
from app.models import DailyReport, WeeklyReport, NTTReport, RetentionReport, StaffActivityReport, RecoveryTask, FieldVisit, Business, CallLog
from app.blueprints.manager.routes import _get_dashboard_tables, _get_dashboard_metrics, TABLE_FRAGMENTS, _fragment, _cached_table, TABLE_VARS
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy import select
from app.services.storage_service import upload_visit_photo, delete_photo, ImageError
from app.services.cache_service import cached, freeze_page, bump_manager_version
from config import Config

daily_target = Config.DAILY_TARGET
weekly_target = Config.WEEKLY_TARGET

def staff_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "staff":
            abort(403)
        if not current_user.is_active:
            abort(403)
        if not current_user.manager.has_access:
            logout_user()
            flash("Your manager has not subscribed. Please contact your manager.", "error")
            return redirect(url_for("auth.staff_login"))
        return f(*args, **kwargs)
    return decorated

@staff_bp.route("/dashboard")
@login_required
@staff_required
def dashboard():
    today = date.today()
    todays_report_date = today - timedelta(days=1)
    yesterdays_report_date = today - timedelta(days=2)
    date_2_days_ago = today - timedelta(days=3)

    manager_id = current_user.manager_id
    assigned = current_user.assigned_businesses          # read once
    assigned_names = [b.name for b in assigned]

    weekly_page = request.args.get('weekly_page', 1, type=int)
    ntt_page = request.args.get('ntt_page', 1, type=int)
    retention_page = request.args.get('retention_page', 1, type=int)

    # AJAX pagination: compute and render only the clicked table
    scope = f"s{current_user.id}"
    pages = {"weekly": weekly_page, "ntt": ntt_page, "retention": retention_page}

    target = request.headers.get("X-Requested-Table")
    if target in TABLE_FRAGMENTS:
        only, template = TABLE_FRAGMENTS[target]
        ctx = dict.fromkeys(TABLE_VARS.values())
        ctx[TABLE_VARS[only]] = _cached_table(only, manager_id, todays_report_date, yesterdays_report_date,
                                              pages[only], business_filter=assigned_names, scope=scope)
        return _fragment(template, **ctx)

    assigned_tasks = (RecoveryTask.query
                      .filter_by(staff_id=current_user.id, is_cleared=False)
                      .options(joinedload(RecoveryTask.business))
                      .order_by(RecoveryTask.created_at.desc())
                      .all())

    metrics = cached(manager_id, (scope, "metrics"), lambda: _get_dashboard_metrics(
        manager_id, todays_report_date, yesterdays_report_date, date_2_days_ago,
        business_names=assigned_names, staff_id=current_user.id))
    tables = {TABLE_VARS[k]: _cached_table(k, manager_id, todays_report_date, yesterdays_report_date,
                                           pages[k], business_filter=assigned_names, scope=scope)
              for k in TABLE_VARS}

    return render_template(
        "staff/dashboard.html",
        metrics=metrics,
        report_date=metrics["as_of"],
        assigned_businesses=assigned,
        assigned_tasks=assigned_tasks,
        **tables,
    )

@staff_bp.route("/report/submit", methods=["GET", "POST"])
@login_required
@staff_required
def submit_report():
    today = date.today()
    existing = StaffActivityReport.query.filter_by(staff_id=current_user.id, report_date=today).first()

    if request.method == "POST":
        # Extract form data
        new_leads = request.form.get("new_leads", 0, type=int)
        visits = request.form.get("visits", 0, type=int)
        calls = request.form.get("calls", 0, type=int)
        recoveries = request.form.get("recoveries", 0, type=int)
        challenges = request.form.get("challenges", "").strip()
        tomorrow_plan = request.form.get("tomorrow_plan", "").strip()

        if existing:
            # Update the existing record
            existing.new_leads = new_leads
            existing.visits = visits
            existing.calls = calls
            existing.recoveries = recoveries
            existing.challenges = challenges
            existing.tomorrow_plan = tomorrow_plan
            
            flash("Daily report updated successfully!", "success")
        else:
            # Create a brand new record
            report = StaffActivityReport(
                staff_id=current_user.id,
                report_date=today,
                new_leads=new_leads,
                visits=visits,
                calls=calls,
                recoveries=recoveries,
                challenges=challenges,
                tomorrow_plan=tomorrow_plan
            )
            db.session.add(report)
            flash("Daily report submitted successfully!", "success")
            
        db.session.commit()
        return redirect(url_for("staff.dashboard"))

    # Passing 'existing' to the template allows you to pre-fill the form inputs
    return render_template("staff/submit_report.html", today=today, existing=existing)

@staff_bp.route("/recovery-task/<int:task_id>/update", methods=["POST"])
@login_required
@staff_required
def update_recovery_task(task_id):
    task = RecoveryTask.query.filter_by(id=task_id, staff_id=current_user.id).first_or_404()
    new_status = request.form.get("status", "PENDING")
    
    if new_status in ["PENDING", "IN_PROGRESS", "RESOLVED"]:
        task.status = new_status
        if new_status == "RESOLVED":
            from datetime import datetime, timezone
            task.resolved_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("Recovery task status updated.", "success")

    return redirect(url_for("staff.dashboard"))

@staff_bp.route("/visits", methods=["GET", "POST"])
@login_required
@staff_required
def visits():
    today = date.today()
    assigned_businesses = current_user.assigned_businesses

    if request.method == "POST":
        log_type = request.form.get("log_type")

        # ==========================================
        # HANDLE FIELD VISIT SUBMISSION
        # ==========================================
        if log_type == "visit":
            business_id = request.form.get("business_id", type=int)
            purpose = request.form.get("purpose", "").strip()
            issue = request.form.get("issue", "").strip()
            action_taken = request.form.get("action_taken", "").strip()
            result = request.form.get("result", "Pending")
            next_follow_up_str = request.form.get("next_follow_up", "").strip()

            next_follow_up = None
            if next_follow_up_str:
                try:
                    next_follow_up = date.fromisoformat(next_follow_up_str)
                except ValueError:
                    pass

            if not business_id or not purpose:
                flash("Business and purpose are required.", "error")
                return redirect(url_for("staff.visits"))

            if business_id not in {b.id for b in assigned_businesses}:
                flash("You can only log activity for your assigned businesses.", "error")
                return redirect(url_for("staff.visits"))

            # Handle image upload
            file = request.files.get("visit_image")
            if not file or not file.filename:
                flash("A visit picture/proof is mandatory to log a field visit.", "error")
                return redirect(url_for("staff.visits"))

            try:
                image_key = upload_visit_photo(file, current_user.manager_id, today)
            except ImageError as e:
                flash(str(e), "error")
                return redirect(url_for("staff.visits"))
            except Exception:
                current_app.logger.exception("Visit photo upload failed")
                flash("Could not upload the photo. Please try again.", "error")
                return redirect(url_for("staff.visits"))

            visit = FieldVisit(
                manager_id=current_user.manager_id,
                staff_id=current_user.id,
                business_id=business_id,
                visit_date=today,
                purpose=purpose,
                issue=issue,
                action_taken=action_taken,
                result=result,
                next_follow_up=next_follow_up,
                image_filename=image_key,
            )
            db.session.add(visit)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                delete_photo(image_key)     # no orphaned object if the DB write fails
                raise
            flash("Field visit logged successfully.", "success")
            
        # ==========================================
        # HANDLE CALL LOG SUBMISSION
        # ==========================================
        elif log_type == "call":
            business_id = request.form.get("business_id", type=int)
            purpose = request.form.get("purpose", "").strip()
            customer_response = request.form.get("customer_response", "").strip()
            result = request.form.get("result", "Pending")
            
            if not business_id or not purpose:
                flash("Business and purpose are required.", "error")
                return redirect(url_for("staff.visits"))

            if business_id not in {b.id for b in assigned_businesses}:
                flash("You can only log activity for your assigned businesses.", "error")
                return redirect(url_for("staff.visits"))
                
            call_log = CallLog(
                manager_id=current_user.manager_id,
                staff_id=current_user.id,
                business_id=business_id,
                call_date=today,
                purpose=purpose,
                customer_response=customer_response,
                result=result
            )
            db.session.add(call_log)
            db.session.commit()
            flash("Call record logged successfully.", "success")

        return redirect(url_for("staff.visits"))

    # Fetch both histories
    staff_visits = (FieldVisit.query.filter_by(staff_id=current_user.id)
                    .options(selectinload(FieldVisit.business))
                    .order_by(FieldVisit.visit_date.desc(), FieldVisit.id.desc()).all())
    staff_calls = (CallLog.query.filter_by(staff_id=current_user.id)
                   .options(selectinload(CallLog.business))
                   .order_by(CallLog.call_date.desc(), CallLog.id.desc()).all())
    
    return render_template(
        "staff/visits.html",
        visits=staff_visits,
        calls=staff_calls,
        assigned_businesses=assigned_businesses,
        today=today
    )

STAFF_BIZ_FILTERS = ("search", "ntt_only", "target_met_only", "target_not_met_only")
BUSINESSES_PER_PAGE = 10

@staff_bp.route("/businesses")
@login_required
@staff_required
def businesses():
    f = {k: (request.args.get(k) or "").strip() for k in STAFF_BIZ_FILTERS}
    page = request.args.get("page", 1, type=int)

    manager_id = current_user.manager_id
    assigned_names = [b.name for b in current_user.assigned_businesses]  # preloaded by load_user

    today = date.today()
    latest_date = today - timedelta(days=1)
    if not db.session.query(WeeklyReport.id).filter_by(manager_id=manager_id, report_date=latest_date).first():
        latest_date = today - timedelta(days=2)

    query = WeeklyReport.query.filter(
        WeeklyReport.manager_id == manager_id,
        WeeklyReport.report_date == latest_date,
        WeeklyReport.business_name.in_(assigned_names),
    )

    if f["search"]:
        query = query.filter(WeeklyReport.business_name.ilike(f"%{f['search']}%"))

    if f["target_met_only"]:
        query = query.filter(WeeklyReport.payment_value >= weekly_target)
    elif f["target_not_met_only"]:
        query = query.filter(WeeklyReport.payment_value < weekly_target)

    if f["ntt_only"]:
        ntt_names = select(NTTReport.business_name).where(
            NTTReport.manager_id == manager_id, NTTReport.report_date == latest_date)
        query = query.filter(WeeklyReport.business_name.in_(ntt_names))

    query = query.order_by(WeeklyReport.payment_value.desc(), WeeklyReport.id.desc())

    # ── CSV: all matching assigned businesses ─────────────────────────────────
    if request.args.get("download") == "csv":
        rows = query.all()

        def generate_csv():
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["Business Name", "Value (NGN)", "Volume", "Target Met", "Days Inactive"])
            for r in rows:
                writer.writerow([r.business_name, r.payment_value or 0, r.payment_vol or 0,
                                 r.target_met or "False", r.days_last_transact or ""])
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)

        return Response(
            generate_csv(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename=my_assigned_businesses_{latest_date}.csv"},
        )

    # ── One page ──────────────────────────────────────────────────────────────
    pagination = query.paginate(page=page, per_page=BUSINESSES_PER_PAGE, error_out=False)

    filter_args = {k: v for k, v in f.items() if v}

    return render_template(
        "staff/businesses.html",
        weekly_rows=pagination.items,
        pagination=pagination,
        filter_args=filter_args,
        latest_date=latest_date,
        **f,
    )