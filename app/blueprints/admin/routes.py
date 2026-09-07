from flask import render_template, abort
from flask_login import login_required, current_user
from app.blueprints.admin import admin_bp
from app.models import BusinessManager, Staff, Payment, FetchLog


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "super_admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated


@admin_bp.route("/dashboard")
@login_required
@admin_required
def dashboard():
    managers = BusinessManager.query.order_by(BusinessManager.created_at.desc()).all()
    recent_payments = Payment.query.order_by(Payment.created_at.desc()).limit(20).all()
    recent_logs = FetchLog.query.order_by(FetchLog.created_at.desc()).limit(50).all()

    return render_template(
        "admin/dashboard.html",
        managers=managers,
        recent_payments=recent_payments,
        recent_logs=recent_logs,
    )
