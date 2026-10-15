from datetime import date, timedelta
from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from flask import abort

from sqlalchemy import select, func, cast, Integer
from sqlalchemy.orm import joinedload

from app.blueprints.recovery import recovery_bp
from app.blueprints.manager.routes import manager_required
from app.extensions import db
from app.models import NTTReport, RetentionReport, RecoveryTask, Staff, Business, BusinessManager

NTT_PER_PAGE = 10

def _back():
    """Return to the same queue page after a POST."""
    page = request.form.get("page", type=int)
    return redirect(url_for("recovery.center", page=page) if page and page > 1
                    else url_for("recovery.center"))

@recovery_bp.route("/center")
@login_required
@manager_required
def center():
    latest_date = (db.session.query(func.max(NTTReport.report_date))
                   .filter(NTTReport.manager_id == current_user.id)
                   .scalar())

    # Longest-inactive first; "12 days" or "12" both sort as 12, blanks go last
    days_num = cast(func.nullif(
        func.regexp_replace(NTTReport.days_last_transact, "[^0-9]", "", "g"), ""), Integer)

    page = request.args.get("page", 1, type=int)
    pagination = (NTTReport.query
                  .filter(NTTReport.manager_id == current_user.id,
                          NTTReport.report_date == latest_date)
                  .order_by(days_num.desc().nullslast(),
                            NTTReport.business_name, NTTReport.terminal_serial)
                  .paginate(page=page, per_page=NTT_PER_PAGE, error_out=False))

    # Business and staff come back in the same query (no per-task lookups)
    tasks = (RecoveryTask.query
             .filter_by(manager_id=current_user.id, is_cleared=False)
             .options(joinedload(RecoveryTask.business), joinedload(RecoveryTask.staff))
             .order_by(RecoveryTask.created_at.desc())
             .all())

    # A task covers one terminal, so match on business + serial
    tasked_keys = {(t.business.name, t.terminal_serial or "")
                   for t in tasks if t.status != "RESOLVED"}

    staffs = Staff.query.filter_by(manager_id=current_user.id, is_active=True).all()

    return render_template(
        "recovery/center.html",
        ntt_rows=pagination.items,
        pagination=pagination,
        tasks=tasks,
        tasked_keys=tasked_keys,
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
        return _back()
    terminal_serial = request.form.get("terminal_serial", "").strip()  # <--- Ensure this is captured from form
    notes = request.form.get("notes", "").strip()
    days_inactive = request.form.get("days_inactive", "0")

    if not business_name:
        flash("Business name is required.", "error")
        return _back()

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
        return _back()

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
    return _back()


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

    return _back()

@recovery_bp.route("/task/<int:task_id>/clear", methods=["POST"])
@login_required
@manager_required
def clear_task(task_id):
    task = RecoveryTask.query.filter_by(id=task_id, manager_id=current_user.id).first_or_404()
    
    task.is_cleared = True
    db.session.commit()
    
    flash("Recovery task cleared from active queue.", "success")
    return _back()