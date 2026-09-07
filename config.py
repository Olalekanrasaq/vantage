import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Flask ─────────────────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    
    # ── Database ──────────────────────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── Monnify ───────────────────────────────────────────────────────────────
    MONNIFY_API_KEY = os.environ.get("MONNIFY_API_KEY")
    MONNIFY_SECRET_KEY = os.environ.get("MONNIFY_SECRET_KEY")
    MONNIFY_CONTRACT_CODE = os.environ.get("MONNIFY_CONTRACT_CODE")
    MONNIFY_BASE_URL = os.environ.get("MONNIFY_BASE_URL", "https://sandbox.monnify.com")

    # ── Mailgun ───────────────────────────────────────────────────────────────
    MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY")
    MAILGUN_DOMAIN = os.environ.get("MAILGUN_DOMAIN")
    MAIL_FROM = os.environ.get("MAIL_FROM", "noreply@vantage.com")

    # ── Super Admin ───────────────────────────────────────────────────────────
    SUPER_ADMIN_EMAIL = os.environ.get("SUPER_ADMIN_EMAIL")
    SUPER_ADMIN_PASSWORD = os.environ.get("SUPER_ADMIN_PASSWORD")

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
