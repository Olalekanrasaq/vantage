from datetime import datetime, timezone
from app.extensions import db

class StaffActivityReport(db.Model):
    __tablename__ = "staff_activity_reports"

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("staffs.id"), nullable=False)
    report_date = db.Column(db.Date, nullable=False)

    new_leads = db.Column(db.Integer, default=0)
    visits = db.Column(db.Integer, default=0)
    calls = db.Column(db.Integer, default=0)
    recoveries = db.Column(db.Integer, default=0)
    
    challenges = db.Column(db.Text, nullable=True)
    tomorrow_plan = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("staff_id", "report_date", name="uq_staff_daily_activity"),
    )

    # Relationships
    staff = db.relationship("Staff", back_populates="activity_reports")