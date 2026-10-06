from datetime import datetime, timezone, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db
from flask import current_app


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
    last_reminder_on = db.Column(db.Date, nullable=True)

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
    field_visits = db.relationship("FieldVisit", back_populates="manager", 
                                cascade="all, delete-orphan")
    call_logs = db.relationship("CallLog", back_populates="manager", 
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

    def _expiry_utc(self):
        e = self.subscription_expiry
        if e is None:
            return None
        return e.replace(tzinfo=timezone.utc) if e.tzinfo is None else e.astimezone(timezone.utc)

    @property
    def grace_ends_at(self):
        e = self._expiry_utc()
        return e + timedelta(days=current_app.config["GRACE_DAYS"]) if e else None

    @property
    def subscription_status(self):
        """'unpaid' | 'active' | 'grace' | 'expired'"""
        e = self._expiry_utc()
        if not self.is_active or e is None:
            return "unpaid"
        now = datetime.now(timezone.utc)
        if now <= e:
            return "active"
        if now <= self.grace_ends_at:
            return "grace"
        return "expired"

    @property
    def has_access(self):
        return self.subscription_status in ("active", "grace")

    @property
    def subscription_is_active(self):  # still used by admin template
        return self.subscription_status == "active"

    @property
    def grace_days_left(self):
        if self.subscription_status != "grace":
            return 0
        return max(0, (self.grace_ends_at - datetime.now(timezone.utc)).days + 1)
    
    def __repr__(self):
        return f"<BusinessManager {self.email}>"
