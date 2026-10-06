from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import BusinessManager, FetchLog
from app.services.mail_service import send_grace_period_reminder, send_missing_report_alert

LAGOS = ZoneInfo("Africa/Lagos")

def send_grace_reminders(app):
    with app.app_context():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        grace_days = app.config["GRACE_DAYS"]
        today = date.today()

        managers = BusinessManager.query.filter(
            BusinessManager.is_active.is_(True),
            BusinessManager.subscription_expiry < now,
            BusinessManager.subscription_expiry >= now - timedelta(days=grace_days),
        ).all()

        for m in managers:
            if m.last_reminder_on == today or not m.has_access:
                continue
            try:
                send_grace_period_reminder(
                    m, m.subscription_expiry, m.grace_ends_at, m.grace_days_left
                )
                m.last_reminder_on = today
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.warning("Grace reminder failed for %s: %s", m.email, e)

def send_missing_report_alerts(app):
    """Runs at 17:00 Lagos. Alerts managers with access who have no report for the day."""
    with app.app_context():
        report_date = datetime.now(LAGOS).date() - timedelta(days=1)

        for m in BusinessManager.query.filter(BusinessManager.is_active.is_(True)).all():
            if not m.has_access:
                continue

            uploaded = FetchLog.query.filter(
                FetchLog.manager_id == m.id,
                FetchLog.report_date == report_date,
                FetchLog.status == "success",
                FetchLog.businesses_extracted > 0,
            ).first()
            already_alerted = FetchLog.query.filter_by(
                manager_id=m.id, report_date=report_date, status="not_found"
            ).first()
            if uploaded or already_alerted:
                continue

            try:
                send_missing_report_alert(m, report_date)
                db.session.add(FetchLog(
                    manager_id=m.id, report_date=report_date, status="not_found",
                    message="Missing report alert sent", businesses_extracted=0,
                ))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.warning("Missing report alert failed for %s: %s", m.email, e)