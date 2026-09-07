import os
from datetime import timedelta
from flask import Flask
from config import config
from app.extensions import db, login_manager, migrate, scheduler
from app.models import SuperAdmin, BusinessManager, Staff


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.from_object(config[config_name])
    app.permanent_session_lifetime = timedelta(minutes=20)

    # ── Initialise extensions ─────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    # ── User loader ───────────────────────────────────────────────────────────
    # Flask-Login calls this to reload the user from the session.
    # We prefix IDs (sa-, bm-, st-) to distinguish user types.
    @login_manager.user_loader
    def load_user(user_id: str):
        if user_id.startswith("sa-"):
            return SuperAdmin.query.get(int(user_id[3:]))
        elif user_id.startswith("bm-"):
            return BusinessManager.query.get(int(user_id[3:]))
        elif user_id.startswith("st-"):
            return Staff.query.get(int(user_id[3:]))
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
    from flask import redirect, url_for

    @app.route("/")
    def index():
        return redirect(url_for("auth.landing"))

    # ── Create super admin on first run ───────────────────────────────────────
    with app.app_context():
        _seed_super_admin(app)

    return app


def _seed_super_admin(app):
    """Create the super admin account if it doesn't exist yet."""
    from app.models import SuperAdmin
    from sqlalchemy import inspect

    email = app.config.get("SUPER_ADMIN_EMAIL")
    password = app.config.get("SUPER_ADMIN_PASSWORD")

    if not email or not password:
        return

    # Don't run if tables haven't been created yet (e.g. during flask db init/migrate)
    inspector = inspect(db.engine)
    if "super_admins" not in inspector.get_table_names():
        return

    existing = SuperAdmin.query.filter_by(email=email).first()
    if not existing:
        admin = SuperAdmin(email=email)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        print(f"[Vantage] Super admin created: {email}")