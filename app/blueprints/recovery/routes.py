from datetime import date, timedelta
from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from flask import abort

from sqlalchemy import select, func
from sqlalchemy.orm import joinedload

from app.blueprints.recovery import recovery_bp
from app.blueprints.manager.routes import manager_required
from app.extensions import db
from app.models import NTTReport, RetentionReport, RecoveryTask, Staff, Business, BusinessManager


@recovery_bp.route("/center")
@login_required
@manager_required
def center():
    # Latest NTT snapshot in one query (was max() + fetch)
    latest = (select(func.max(NTTReport.report_date))
              .correlate(None)
              .where(NTTReport.manager_id == current_user.id)
              .scalar_subquery())
    ntt_rows = NTTReport.query.filter(
        NTTReport.manager_id == current_user.id,
        NTTReport.report_date == latest,
    ).all()
    latest_date = ntt_rows[0].report_date if ntt_rows else None

    # Business and staff come back in the same query (no per-task lookups)
    tasks = (RecoveryTask.query
             .filter_by(manager_id=current_user.id, is_cleared=False)
             .options(joinedload(RecoveryTask.business), joinedload(RecoveryTask.staff))
             .order_by(RecoveryTask.created_at.desc())
             .all())
    tasked_business_names = {t.business.name for t in tasks if t.status != "RESOLVED"}

    staffs = Staff.query.filter_by(manager_id=current_user.id, is_active=True).all()

    return render_template(
        "recovery/center.html",
        ntt_rows=ntt_rows,
        tasks=tasks,
        tasked_business_names=tasked_business_names,
        staffs=staffs,
        report_date=latest_date,
    )

@recovery_bp.route("/task/create", methods=["POST"])
@login_required
@manager_required
def create_task():
    business_name = request.form.get("business_name", "").strip()
    staff_id = request.form.get("staff_id", type=int)
    if staff_id and not Staff.query.filter_by(id=staff_id, manager_id=current_user.id).first():
        flash("Invalid staff selection.", "error")
        return redirect(url_for("recovery.center"))
    terminal_serial = request.form.get("terminal_serial", "").strip()  # <--- Ensure this is captured from form
    notes = request.form.get("notes", "").strip()
    days_inactive = request.form.get("days_inactive", "0")

    if not business_name:
        flash("Business name is required.", "error")
        return redirect(url_for("recovery.center"))

    try:
        days = int(days_inactive)
    except ValueError:
        days = 0

    if days >= 15:
        priority = "CRITICAL"
    elif days >= 8:
        priority = "HIGH"
    elif days >= 4:
        priority = "AT_RISK"
    else:
        priority = "WATCH"

    business = Business.query.filter_by(manager_id=current_user.id, name=business_name).first()
    if not business:
        flash("Business not found.", "error")
        return redirect(url_for("recovery.center"))

    task = RecoveryTask(
        manager_id=current_user.id,
        business_id=business.id,
        staff_id=staff_id if staff_id else None,
        terminal_serial=terminal_serial if terminal_serial else None,
        days_inactive=days,
        priority=priority,
        status="PENDING"
    )
    db.session.add(task)
    db.session.commit()

    flash(f"Recovery task created for {business_name}.", "success")
    return redirect(url_for("recovery.center"))


@recovery_bp.route("/task/<int:task_id>/update", methods=["POST"])
@login_required
@manager_required
def update_task_status(task_id):
    task = RecoveryTask.query.filter_by(id=task_id, manager_id=current_user.id).first_or_404()
    new_status = request.form.get("status", "PENDING")
    
    if new_status in ["PENDING", "IN_PROGRESS", "RESOLVED"]:
        task.status = new_status
        if new_status == "RESOLVED":
            from datetime import datetime, timezone
            task.resolved_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("Task status updated.", "success")

    return redirect(url_for("recovery.center"))

@recovery_bp.route("/task/<int:task_id>/clear", methods=["POST"])
@login_required
@manager_required
def clear_task(task_id):
    task = RecoveryTask.query.filter_by(id=task_id, manager_id=current_user.id).first_or_404()
    
    task.is_cleared = True
    db.session.commit()
    
    flash("Recovery task cleared from active queue.", "success")
    return redirect(url_for("recovery.center"))