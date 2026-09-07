from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


class Staff(UserMixin, db.Model):
    __tablename__ = "staffs"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"),
                           nullable=False)

    username = db.Column(db.String(100), nullable=False)
    full_name = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    manager = db.relationship("BusinessManager", back_populates="staffs")
    business_assignments = db.relationship(
        "StaffBusinessAssignment",
        back_populates="staff",
        cascade="all, delete-orphan"
    )
    recovery_tasks = db.relationship("RecoveryTask", back_populates="staff", 
                                    cascade="all, delete-orphan")
    activity_reports = db.relationship("StaffActivityReport", back_populates="staff", 
                                    cascade="all, delete-orphan")
    field_visits = db.relationship("FieldVisit", back_populates="staff", 
                                    cascade="all, delete-orphan")

    # Username must be unique per manager (two managers can have a staff named "john")
    __table_args__ = (
        db.UniqueConstraint("manager_id", "username", name="uq_staff_username_per_manager"),
    )

    def get_id(self):
        return f"st-{self.id}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def role(self):
        return "staff"

    @property
    def assigned_businesses(self):
        return [a.business for a in self.business_assignments if a.business.is_active]

    def __repr__(self):
        return f"<Staff {self.username} (manager_id={self.manager_id})>"
