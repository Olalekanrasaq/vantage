import os
from decouple import config


class Config:
    # ── Flask ─────────────────────────────────────────────────────────────────
    SECRET_KEY = config("SECRET_KEY")
    BEHIND_PROXY = False
    RUN_SCHEDULER_IN_APP = False

    # ── Database ──────────────────────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI = config("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── Redis / rate limiting ─────────────────────────────────────────────────
    REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")
    RATELIMIT_STORAGE_URI = REDIS_URL
    RATELIMIT_SWALLOW_ERRORS = True          # a Redis outage must not block logins

    # ── Sessions / cookies ────────────────────────────────────────────────────
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # ── Monnify ───────────────────────────────────────────────────────────────
    MONNIFY_API_KEY = config("MONNIFY_API_KEY")
    MONNIFY_SECRET_KEY = config("MONNIFY_SECRET_KEY")
    MONNIFY_CONTRACT_CODE = config("MONNIFY_CONTRACT_CODE")
    MONNIFY_BASE_URL = config("MONNIFY_BASE_URL")

    # ── Billing ───────────────────────────────────────────────────────────────
    SUBSCRIPTION_AMOUNT = config("SUBSCRIPTION_AMOUNT", default=500, cast=int)
    SUBSCRIPTION_MONTHS = config("SUBSCRIPTION_MONTHS", default=1, cast=int)
    APP_URL = config("APP_URL", default=config("RENDER_EXTERNAL_URL", default=""))
    GRACE_DAYS = 5

    # ── Mail (Gmail SMTP) ─────────────────────────────────────────────────────
    MAIL_SERVER = config("MAIL_SERVER", default="smtp.gmail.com")
    MAIL_PORT = config("MAIL_PORT", default=587, cast=int)
    MAIL_USERNAME = config("MAIL_USERNAME")
    MAIL_PASSWORD = config("MAIL_PASSWORD")
    MAIL_FROM = config("MAIL_FROM", default=config("MAIL_USERNAME"))
    BREVO_API_KEY = config("BREVO_API_KEY", default="")

    # ── Super Admin ───────────────────────────────────────────────────────────
    SUPER_ADMIN_EMAIL = config("SUPER_ADMIN_EMAIL")
    SUPER_ADMIN_PASSWORD = config("SUPER_ADMIN_PASSWORD")

    # ── Uploads ───────────────────────────────────────────────────────────────
    ALLOWED_EXTENSIONS = {"pdf"}
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    MAX_CONTENT_LENGTH = 12 * 1024 * 1024    # 12MB max upload
    MAX_FORM_PARTS = 5000                    # bulk assign/unassign forms (Flask 3.1+)

    # ── S3 ────────────────────────────────────────────────────────────────────
    S3_BUCKET = config("S3_BUCKET")
    AWS_REGION = config("AWS_REGION", default="us-east-1")

    WTF_CSRF_TIME_LIMIT = None   # token lives as long as the session (sessions already expire after 20 min idle)
    REPORT_RETENTION_DAYS = config("REPORT_RETENTION_DAYS", default=60, cast=int)

    WEEKLY_TARGET = 100000
    DAILY_TARGET = 14286

    GEMINI_API_KEY = config("GEMINI_API_KEY", default="")

class DevelopmentConfig(Config):
    DEBUG = True
    RUN_SCHEDULER_IN_APP = True              # python run.py keeps the scheduler as today
    RATELIMIT_STORAGE_URI = "memory://"      # no Redis needed just for rate limits in dev


class ProductionConfig(Config):
    DEBUG = False
    BEHIND_PROXY = True
    PREFERRED_URL_SCHEME = "https"
    SESSION_COOKIE_SECURE = config("COOKIE_SECURE", default=True, cast=bool)
    REMEMBER_COOKIE_SECURE = config("COOKIE_SECURE", default=True, cast=bool)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 4,             # one per gunicorn thread
        "max_overflow": 2,
        "pool_timeout": 10,
        "pool_pre_ping": True,      # drop dead connections after a DB restart
        "pool_recycle": 1800,
        "connect_args": {"options": "-c statement_timeout=30000"},  # kill queries over 30s
    }


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}