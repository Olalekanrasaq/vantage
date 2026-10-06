from datetime import date, timedelta
from flask import render_template, abort, redirect, url_for, flash, request, current_app, Response
from flask_login import login_required, current_user, logout_user
from functools import wraps

from app.blueprints.staff import staff_bp
from app.extensions import db
from app.models import DailyReport, WeeklyReport, NTTReport, RetentionReport, StaffActivityReport, RecoveryTask, FieldVisit, Business, CallLog
from app.blueprints.manager.routes import _get_dashboard_metrics, _get_dashboard_tables

import os
from werkzeug.utils import secure_filename

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
    assigned_names = [b.name for b in current_user.assigned_businesses]
    assigned_ids = [b.id for b in current_user.assigned_businesses]

    metrics = _get_dashboard_metrics(manager_id, todays_report_date, yesterdays_report_date, date_2_days_ago)

    # Fetch recovery tasks assigned to this staff member
    assigned_tasks = RecoveryTask.query.filter_by(
        staff_id=current_user.id,
        is_cleared=False
    ).order_by(RecoveryTask.created_at.desc()).all()

    # Recompute metrics filtered to staff's businesses only
    def _filter_metrics():
        nonlocal todays_report_date, yesterdays_report_date

        def count_met(model, r_date, target):
            rows = model.query.filter_by(manager_id=manager_id, report_date=r_date).filter(
                model.business_name.in_(assigned_names)
            ).all()
            total = len(set(r.business_name for r in rows))
            met = sum(1 for r in rows if int(r.payment_value) >= target)
            return total, met

        def ntt_count(r_date):
            return NTTReport.query.filter_by(
                manager_id=manager_id, report_date=r_date
            ).filter(NTTReport.business_name.in_(assigned_names)).count()

        def ret_count(r_date):
            return RetentionReport.query.filter_by(
                manager_id=manager_id, report_date=r_date
            ).filter(RetentionReport.business_name.in_(assigned_names)).count()

        def pend_count():
            return RecoveryTask.query.filter_by(
                manager_id=manager_id, staff_id=current_user.id, status='PENDING'
            ).count()

        def pct(part, total):
            return round(part / total * 100, 1) if total else 0

        def delta(curr, prev):
            return curr - prev

        daily_target = 14286
        weekly_target = 100000
        # if todays_report_date is not available, fallback to yesterdays_report_date
        if not DailyReport.query.filter_by(manager_id=manager_id, report_date=todays_report_date).first():
            todays_report_date = yesterdays_report_date
            yesterdays_report_date = date_2_days_ago
        total, daily_met = count_met(DailyReport, todays_report_date, daily_target)
        prev_total, prev_daily_met = count_met(DailyReport, yesterdays_report_date, daily_target)
        wk_total, wk_met = count_met(WeeklyReport, todays_report_date, weekly_target)
        prev_wk_total, prev_wk_met = count_met(WeeklyReport, yesterdays_report_date, weekly_target)
        ntt = ntt_count(todays_report_date)
        prev_ntt = ntt_count(yesterdays_report_date)
        ret = ret_count(todays_report_date)
        prev_ret = ret_count(yesterdays_report_date)
        pend_tasks = pend_count()

        return {
            "total_businesses": total,
            "daily_met": daily_met,
            "daily_met_pct": pct(daily_met, total),
            "weekly_met": wk_met,
            "weekly_met_pct": pct(wk_met, wk_total),
            "weekly_not_met": wk_total - wk_met,
            "ntt_count": ntt,
            "ntt_pct": pct(ntt, total),
            "retention_count": ret,
            "pending_tasks": pend_tasks or 0,
            "delta_total": delta(total, prev_total),
            "delta_daily_met": delta(daily_met, prev_daily_met),
            "delta_weekly_met": delta(wk_met, prev_wk_met),
            "delta_ntt": delta(ntt, prev_ntt),
            "delta_retention": delta(ret, prev_ret),
        }

    staff_metrics = _filter_metrics()

    weekly_not_met, ntt_list, retention_list = _get_dashboard_tables(
        manager_id, todays_report_date, yesterdays_report_date, business_filter=assigned_names
    )

    return render_template(
        "staff/dashboard.html",
        metrics=staff_metrics,
        weekly_not_met=weekly_not_met,
        ntt_list=ntt_list,
        retention_list=retention_list,
        report_date=todays_report_date,
        assigned_businesses=current_user.assigned_businesses,
        assigned_tasks=assigned_tasks,
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

            # Handle image upload
            file = request.files.get("visit_image")
            
            # Check if file is completely missing or empty
            if not file or not file.filename:
                flash("A visit picture/proof is mandatory to log a field visit.", "error")
                return redirect(url_for("staff.visits"))

            # Proceed to process the uploaded image
            ext = file.filename.rsplit(".", 1)[1].lower() if "." in file.filename else ""
            if ext in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
                filename = secure_filename(f"visit_{current_user.id}_{today}_{file.filename}")
                upload_folder = current_app.config["VISIT_UPLOAD_FOLDER"]
                os.makedirs(upload_folder, exist_ok=True)
                file.save(os.path.join(upload_folder, filename))
                image_filename = filename
            else:
                flash("Invalid image format. Allowed formats: png, jpg, jpeg, webp.", "error")
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
                image_filename=image_filename
            )
            db.session.add(visit)
            db.session.commit()
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
    staff_visits = FieldVisit.query.filter_by(staff_id=current_user.id).order_by(FieldVisit.visit_date.desc()).all()
    staff_calls = CallLog.query.filter_by(staff_id=current_user.id).order_by(CallLog.call_date.desc()).all()

    return render_template(
        "staff/visits.html",
        visits=staff_visits,
        calls=staff_calls,
        assigned_businesses=assigned_businesses,
        today=today
    )

@staff_bp.route("/businesses")
@login_required
@staff_required
def businesses():
    today = date.today()
    latest_date = today - timedelta(days=1)
    date_2_days_ago = today - timedelta(days=2)

    search = request.args.get("search", "").strip()
    ntt_only = request.args.get("ntt_only", "")
    target_met_only = request.args.get("target_met_only", "")
    target_not_met_only = request.args.get("target_not_met_only", "")
    download = request.args.get("download", "")

    # Get names of businesses assigned specifically to this staff member
    assigned_names = [b.name for b in current_user.assigned_businesses]

    if not assigned_names:
        if download == "csv":
            return Response("No assigned businesses found\n", mimetype="text/csv")
        return render_template("staff/businesses.html", weekly_rows=[], search=search, ntt_only=ntt_only, target_met_only=target_met_only, target_not_met_only=target_not_met_only, latest_date=latest_date)

    if not WeeklyReport.query.filter_by(manager_id=current_user.manager_id, report_date=latest_date).first():
        latest_date = date_2_days_ago

    query = db.session.query(WeeklyReport).filter(
        WeeklyReport.manager_id == current_user.manager_id,
        WeeklyReport.report_date == latest_date,
        WeeklyReport.business_name.in_(assigned_names)
    )

    if search:
        query = query.filter(WeeklyReport.business_name.ilike(f"%{search}%"))

    if target_met_only:
        query = query.filter(WeeklyReport.payment_value >= 100000)
    elif target_not_met_only:
        query = query.filter(WeeklyReport.payment_value < 100000)

    weekly_rows = query.order_by(WeeklyReport.payment_value.desc()).all()

    if ntt_only:
        ntt_names = {
            r.business_name for r in
            NTTReport.query.filter_by(manager_id=current_user.manager_id, report_date=latest_date).all()
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
            headers={"Content-Disposition": f"attachment;filename=my_assigned_businesses_{latest_date}.csv"}
        )

    all_businesses = Business.query.filter_by(manager_id=current_user.manager_id, is_active=True).all()
    biz_map = {b.name: b for b in all_businesses}

    return render_template(
        "staff/businesses.html",
        weekly_rows=weekly_rows,
        biz_map=biz_map,
        search=search,
        ntt_only=ntt_only,
        target_met_only=target_met_only,
        target_not_met_only=target_not_met_only,
        latest_date=latest_date,
    )