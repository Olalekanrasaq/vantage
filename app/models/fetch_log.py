from datetime import datetime, timezone
from app.extensions import db


class FetchLog(db.Model):
    __tablename__ = "fetch_logs"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"),
                           nullable=False)

    report_date = db.Column(db.Date, nullable=False)

    # "success", "not_found", "extraction_error", "gmail_error", "manual_upload"
    status = db.Column(db.String(30), nullable=False)

    message = db.Column(db.Text, nullable=True)  # Error detail if failed
    businesses_extracted = db.Column(db.Integer, default=0)  # Count of rows extracted

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    manager = db.relationship("BusinessManager", back_populates="fetch_logs")

    def __repr__(self):
        return f"<FetchLog manager={self.manager_id} date={self.report_date} status={self.status}>"
