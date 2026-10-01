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

    # ── Mailgun ───────────────────────────────────────────────────────────────
    MAILGUN_API_KEY = config("MAILGUN_API_KEY")
    MAILGUN_DOMAIN = config("MAILGUN_DOMAIN")
    MAIL_FROM = config("MAIL_FROM", "noreply@vantage.com")

    # ── Super Admin ───────────────────────────────────────────────────────────
    SUPER_ADMIN_EMAIL = config("SUPER_ADMIN_EMAIL")
    SUPER_ADMIN_PASSWORD = config("SUPER_ADMIN_PASSWORD")

    # ── Uploads ───────────────────────────────────────────────────────────────
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "app", "static", "uploads")
    VISIT_UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "app", "static", "uploads", "visits")
    ALLOWED_EXTENSIONS = {"pdf"}
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
