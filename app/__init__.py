import os
import logging
from datetime import timedelta

from decouple import config as env
from flask import Flask, flash, redirect, request, url_for
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.middleware.proxy_fix import ProxyFix

from config import config
from app.extensions import db, login_manager, migrate, scheduler, rds, limiter, csrf
from app.models import SuperAdmin, BusinessManager, Staff, StaffBusinessAssignment
from app.services.reminder_service import send_grace_reminders, send_missing_report_alerts
from app.services.storage_service import photo_url
import app.services.cache_service  # noqa: F401  (registers cache invalidation listeners)
from flask_wtf.csrf import CSRFError


def create_app(config_name=None):
    if config_name is None:
        # Read through decouple so the value in .env is honoured
        config_name = env("FLASK_ENV", default="development")

    app = Flask(__name__)
    css_path = os.path.join(app.static_folder, "css", "app.css")
    asset_v = int(os.path.getmtime(css_path)) if os.path.exists(css_path) else 0

    @app.context_processor
    def inject_asset_version():
        return {"asset_v": asset_v}
    app.config.from_object(config[config_name])
    app.permanent_session_lifetime = timedelta(minutes=20)
    app.jinja_env.globals["photo_url"] = photo_url

    # ── Behind nginx: trust one proxy for scheme, host and client IP ──────────
    if app.config.get("BEHIND_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # ── Initialise extensions ─────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    rds.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)
    if app.debug:
        from app.profiler import init_query_profiler
        init_query_profiler(app)
    else:
        app.logger.setLevel(logging.INFO)

    # ── User loader ───────────────────────────────────────────────────────────
    # Flask-Login calls this to reload the user from the session.
    # We prefix IDs (sa-, bm-, st-) to distinguish user types.
    @login_manager.user_loader
    def load_user(user_id: str):
        prefix, _, raw_id = user_id.partition("-")
        if not raw_id.isdigit():
            return None
        uid = int(raw_id)
        if prefix == "sa":
            return db.session.get(SuperAdmin, uid)
        if prefix == "bm":
            return db.session.get(BusinessManager, uid)
        if prefix == "st":
            return (Staff.query
                    .options(joinedload(Staff.manager),
                             selectinload(Staff.business_assignments)
                             .joinedload(StaffBusinessAssignment.business))
                    .filter_by(id=uid)
                    .first())
        return None

    # ── Register blueprints ───────────────────────────────────────────────────
    from app.blueprints.auth import auth_bp
    from app.blueprints.manager import manager_bp
    from app.blueprints.staff import staff_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.payment import payment_bp
    from app.blueprints.recovery import recovery_bp

    app.register_blueprint(recovery_bp, url_prefix="/recovery")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(manager_bp, url_prefix="/manager")
    app.register_blueprint(staff_bp, url_prefix="/staff")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(payment_bp, url_prefix="/payment")

    # ── Root route ────────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return redirect(url_for("auth.landing"))

    @app.after_request
    def cache_versioned_static(resp):
        # Files linked with ?v=... change URL on every rebuild, so they can be cached "forever"
        if request.path.startswith("/static/") and "v" in request.args and resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp
    
    # ── Friendly error responses ──────────────────────────────────────────────
    @app.errorhandler(429)
    def too_many_requests(e):
        if request.is_json:
            return {"error": "Too many requests. Please wait a moment and try again."}, 429
        flash("Too many attempts. Please wait a minute and try again.", "error")
        return redirect(request.referrer or url_for("auth.landing"))

    @app.errorhandler(413)
    def too_large(e):
        if request.is_json:
            return {"error": "File too large. The maximum is 12MB."}, 413
        flash("File too large. The maximum is 12MB.", "error")
        return redirect(request.referrer or url_for("auth.landing"))

    @app.errorhandler(CSRFError)
    def csrf_failed(e):
        if request.is_json:
            return {"error": "Your session expired. Refresh the page and try again."}, 400
        flash("This form expired. Please try again.", "error")
        return redirect(request.referrer or url_for("auth.landing"))

    # ── Create super admin on first run ───────────────────────────────────────
    with app.app_context():
        _seed_super_admin(app)

    # ── Scheduler: in-app only in development. Production runs scheduler_worker.py ──
    if (app.config.get("RUN_SCHEDULER_IN_APP")
            and not scheduler.running
            and (not app.debug or env("WERKZEUG_RUN_MAIN", default="") == "true")):
        scheduler.add_job(send_grace_reminders, "cron", hour=8, minute=0,
                          args=[app], id="grace_reminders", replace_existing=True)
        scheduler.add_job(send_missing_report_alerts, "cron", hour=17, minute=0,
                          args=[app], id="missing_report_alerts", replace_existing=True)
        scheduler.start()

    return app


def _seed_super_admin(app):
    """Create the super admin account if it doesn't exist yet."""
    from sqlalchemy import inspect

    email = app.config.get("SUPER_ADMIN_EMAIL")
    password = app.config.get("SUPER_ADMIN_PASSWORD")
    if not email or not password:
        return

    # Don't run if tables haven't been created yet (e.g. during flask db init/migrate)
    if "super_admins" not in inspect(db.engine).get_table_names():
        return

    if SuperAdmin.query.filter_by(email=email).first():
        return

    admin = SuperAdmin(email=email)
    admin.set_password(password)
    db.session.add(admin)
    try:
        db.session.commit()
        app.logger.info("[Vantage] Super admin created: %s", email)
    except IntegrityError:
        # Another gunicorn worker created it at the same moment
        db.session.rollback()