from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


class SuperAdmin(UserMixin, db.Model):
    __tablename__ = "super_admins"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Flask-Login requires a unique ID per user type
    # Prefix "sa-" distinguishes from manager and staff IDs
    def get_id(self):
        return f"sa-{self.id}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def role(self):
        return "super_admin"

    def __repr__(self):
        return f"<SuperAdmin {self.email}>"


class BusinessManager(UserMixin, db.Model):
    __tablename__ = "business_managers"

    id = db.Column(db.Integer, primary_key=True)

    # Google OAuth identity
    # google_id = db.Column(db.String(255), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    # profile_picture = db.Column(db.String(500), nullable=True)

    # Gmail API credentials (stored as JSON string)
    # gmail_credentials = db.Column(db.Text, nullable=True)

    # Account status
    is_active = db.Column(db.Boolean, default=False)  # True after payment
    subscription_expiry = db.Column(db.DateTime, nullable=True)

    # Scheduler flag — reset daily
    # report_fetched_today = db.Column(db.Boolean, default=False)

    # created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
    #                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    staffs = db.relationship("Staff", back_populates="manager",
                                cascade="all, delete-orphan")
    businesses = db.relationship("Business", back_populates="manager",
                                cascade="all, delete-orphan")
    payments = db.relationship("Payment", back_populates="manager",
                                cascade="all, delete-orphan")
    fetch_logs = db.relationship("FetchLog", back_populates="manager",
                                cascade="all, delete-orphan")
    recovery_tasks = db.relationship("RecoveryTask", back_populates="manager", 
                                cascade="all, delete-orphan")

    def get_id(self):
        return f"bm-{self.id}"

    def set_password(self, password):
            self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def role(self):
        return "manager"

    @property
    def subscription_is_active(self):
        if not self.is_active:
            return False
        if self.subscription_expiry is None:
            return False
        expiry = self.subscription_expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) <= expiry
    def __repr__(self):
        return f"<BusinessManager {self.email}>"
