from datetime import date, timedelta
from flask import render_template, abort
from flask_login import login_required, current_user
from functools import wraps

from app.blueprints.staff import staff_bp
from app.extensions import db
from app.models import DailyReport, WeeklyReport, NTTReport, RetentionReport
from app.blueprints.manager.routes import _get_dashboard_metrics, _get_dashboard_tables


def staff_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "staff":
            abort(403)
        if not current_user.is_active:
            abort(403)
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

    metrics = _get_dashboard_metrics(manager_id, todays_report_date, yesterdays_report_date, date_2_days_ago)

    # Recompute metrics filtered to staff's businesses only
    def _filter_metrics():
        def count_met(model, r_date):
            rows = model.query.filter_by(manager_id=manager_id, report_date=r_date).filter(
                model.business_name.in_(assigned_names)
            ).all()
            total = len(set(r.business_name for r in rows))
            met = sum(1 for r in rows if str(r.target_met).strip().lower() == "true")
            return total, met

        def ntt_count(r_date):
            return NTTReport.query.filter_by(
                manager_id=manager_id, report_date=r_date
            ).filter(NTTReport.business_name.in_(assigned_names)).count()

        def ret_count(r_date):
            return RetentionReport.query.filter_by(
                manager_id=manager_id, report_date=r_date
            ).filter(RetentionReport.business_name.in_(assigned_names)).count()

        def pct(part, total):
            return round(part / total * 100, 1) if total else 0

        def delta(curr, prev):
            return curr - prev

        # if todays_report_date is not available, fallback to yesterdays_report_date
        if not DailyReport.query.filter_by(manager_id=manager_id, report_date=todays_report_date).first():
            todays_report_date = yesterdays_report_date
            yesterdays_report_date = date_2_days_ago
        total, daily_met = count_met(DailyReport, todays_report_date)
        prev_total, prev_daily_met = count_met(DailyReport, yesterdays_report_date)
        wk_total, wk_met = count_met(WeeklyReport, todays_report_date)
        prev_wk_total, prev_wk_met = count_met(WeeklyReport, yesterdays_report_date)
        ntt = ntt_count(todays_report_date)
        prev_ntt = ntt_count(yesterdays_report_date)
        ret = ret_count(todays_report_date)
        prev_ret = ret_count(yesterdays_report_date)

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
            "delta_total": delta(total, prev_total),
            "delta_daily_met": delta(daily_met, prev_daily_met),
            "delta_weekly_met": delta(wk_met, prev_wk_met),
            "delta_ntt": delta(ntt, prev_ntt),
            "delta_retention": delta(ret, prev_ret),
        }

    staff_metrics = _filter_metrics()

    weekly_not_met, ntt_list, retention_list = _get_dashboard_tables(
        manager_id, todays_report_date, business_filter=assigned_names
    )

    return render_template(
        "staff/dashboard.html",
        metrics=staff_metrics,
        weekly_not_met=weekly_not_met,
        ntt_list=ntt_list,
        retention_list=retention_list,
        report_date=todays_report_date,
        assigned_businesses=current_user.assigned_businesses,
    )
