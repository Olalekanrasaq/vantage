from datetime import datetime, timezone
from app.extensions import db

class RecoveryTask(db.Model):
    __tablename__ = "recovery_tasks"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey("staffs.id"), nullable=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False) # <--- Links to Business model
    
    terminal_serial = db.Column(db.String(100), nullable=True)
    days_inactive = db.Column(db.Integer, nullable=True)
    priority = db.Column(db.String(20), default="MEDIUM")
    status = db.Column(db.String(20), default="PENDING")
    is_cleared = db.Column(db.Boolean, default=False, server_default=db.false(), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.Index("ix_recovery_manager_status", "manager_id", "status"),
        db.Index("ix_recovery_staff_status", "staff_id", "status"),
    )

    # Relationships
    business = db.relationship("Business", back_populates="recovery_tasks")
    staff = db.relationship("Staff", back_populates="recovery_tasks")
    manager = db.relationship("BusinessManager", back_populates="recovery_tasks")