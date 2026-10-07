from datetime import datetime, timezone
from app.extensions import db


class Business(db.Model):
    __tablename__ = "businesses"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"),
                           nullable=False)

    # Business name as it appears in the PDF report
    name = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    manager = db.relationship("BusinessManager", back_populates="businesses")
    staff_assignments = db.relationship(
        "StaffBusinessAssignment",
        back_populates="business",
        cascade="all, delete-orphan"
    )
    reports = db.relationship("DailyReport", back_populates="business",
                              cascade="all, delete-orphan")
    recovery_tasks = db.relationship("RecoveryTask", back_populates="business", 
                              cascade="all, delete-orphan")
    field_visits = db.relationship("FieldVisit", back_populates="business", 
                              cascade="all, delete-orphan")
    call_logs = db.relationship("CallLog", back_populates="business", 
                                cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("manager_id", "name", name="uq_business_name_per_manager"),
    )

    def __repr__(self):
        return f"<Business {self.name}>"


class StaffBusinessAssignment(db.Model):
    __tablename__ = "staff_business_assignments"

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("staffs.id"), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)
    assigned_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    staff = db.relationship("Staff", back_populates="business_assignments")
    business = db.relationship("Business", back_populates="staff_assignments")

    __table_args__ = (
        db.UniqueConstraint("staff_id", "business_id", name="uq_staff_business"),
        db.Index("ix_assignment_business", "business_id"),
    )

    def __repr__(self):
        return f"<Assignment staff={self.staff_id} business={self.business_id}>"
