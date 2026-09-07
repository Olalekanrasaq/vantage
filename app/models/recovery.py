from datetime import datetime, timezone
from app.extensions import db

class RecoveryTask(db.Model):
    __tablename__ = "recovery_tasks"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey("staffs.id"), nullable=True)
    terminal_serial = db.Column(db.String(100), nullable=True)

    status = db.Column(db.String(20), default="PENDING") 
    priority = db.Column(db.String(20), default="HIGH") 
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    manager = db.relationship("BusinessManager", back_populates="recovery_tasks")
    business = db.relationship("Business", back_populates="recovery_tasks")
    staff = db.relationship("Staff", back_populates="recovery_tasks")