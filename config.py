import os
from decouple import config


class Config:
    # ── Flask ─────────────────────────────────────────────────────────────────
    SECRET_KEY = config("SECRET_KEY")
    
    # ── Database ──────────────────────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI = config("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── Monnify ───────────────────────────────────────────────────────────────
    MONNIFY_API_KEY = config("MONNIFY_API_KEY")
    MONNIFY_SECRET_KEY = config("MONNIFY_SECRET_KEY")
    MONNIFY_CONTRACT_CODE = config("MONNIFY_CONTRACT_CODE")
    MONNIFY_BASE_URL = config("MONNIFY_BASE_URL")

     # ── Billing ───────────────────────────────────────────────────────────────
    SUBSCRIPTION_AMOUNT = config("SUBSCRIPTION_AMOUNT", default=5000, cast=int)
    # SUBSCRIPTION_DAYS = config("SUBSCRIPTION_DAYS", default=30, cast=int)
    SUBSCRIPTION_MONTHS = config("SUBSCRIPTION_MONTHS", default=1, cast=int)
    APP_URL = config("APP_URL")  # e.g. https://vantage.example.com, no trailing slash
    GRACE_DAYS = 5

    # ── Mail (Gmail SMTP) ─────────────────────────────────────────────────────
    MAIL_SERVER = config("MAIL_SERVER", default="smtp.gmail.com")
    MAIL_PORT = config("MAIL_PORT", default=587, cast=int)
    MAIL_USERNAME = config("MAIL_USERNAME")
    MAIL_PASSWORD = config("MAIL_PASSWORD")
    MAIL_FROM = config("MAIL_FROM", default=config("MAIL_USERNAME"))

    # ── Super Admin ───────────────────────────────────────────────────────────
    SUPER_ADMIN_EMAIL = config("SUPER_ADMIN_EMAIL")
    SUPER_ADMIN_PASSWORD = config("SUPER_ADMIN_PASSWORD")

    # ── Uploads ───────────────────────────────────────────────────────────────
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "app", "static", "uploads")
    ALLOWED_EXTENSIONS = {"pdf"}
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    MAX_CONTENT_LENGTH = 12 * 1024 * 1024  # 12MB max upload
    S3_BUCKET = "cocosurf-gear-miami"
    AWS_REGION = "us-east-1"   # use your bucket's region

    # Werkzeug form parts
    MAX_FORM_PARTS = 5000

class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
