from datetime import datetime, timezone
from app.extensions import db


class DailyReport(db.Model):
    __tablename__ = "daily_reports"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=True)
    report_date = db.Column(db.Date, nullable=False)
    source = db.Column(db.String(20), default="gmail")

    # Extracted fields
    business_name = db.Column(db.String(255), nullable=False)
    terminal_serial = db.Column(db.String(100), nullable=True)
    target_met = db.Column(db.String(20), nullable=True)
    payment_value = db.Column(db.Numeric(15, 2), default=0)
    payment_vol = db.Column(db.Integer, default=0)
    days_last_transact = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    business = db.relationship("Business", back_populates="reports")

    __table_args__ = (
        db.UniqueConstraint("manager_id", "business_name", "terminal_serial", "report_date",
                            name="uq_daily_report"),
        db.Index("ix_daily_manager_date", "manager_id", "report_date"),
    )

    def __repr__(self):
        return f"<DailyReport {self.business_name} {self.report_date}>"


class WeeklyReport(db.Model):
    __tablename__ = "weekly_reports"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=True)
    report_date = db.Column(db.Date, nullable=False)
    source = db.Column(db.String(20), default="gmail")

    # Extracted fields
    business_name = db.Column(db.String(255), nullable=False)
    target_met = db.Column(db.String(20), nullable=True)
    payment_value = db.Column(db.Numeric(15, 2), default=0)
    payment_vol = db.Column(db.Integer, default=0)
    days_last_transact = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("manager_id", "business_name", "report_date",
                            name="uq_weekly_report"),
        db.Index("ix_weekly_manager_date", "manager_id", "report_date"),
    )

    def __repr__(self):
        return f"<WeeklyReport {self.business_name} {self.report_date}>"


class NTTReport(db.Model):
    __tablename__ = "ntt_reports"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=True)
    report_date = db.Column(db.Date, nullable=False)
    source = db.Column(db.String(20), default="gmail")

    # Extracted fields
    business_name = db.Column(db.String(255), nullable=False)
    terminal_serial = db.Column(db.String(100), nullable=True)
    days_last_transact = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("manager_id", "business_name", "report_date",
                            name="uq_ntt_report"),
        db.Index("ix_ntt_manager_date", "manager_id", "report_date"),
    )

    def __repr__(self):
        return f"<NTTReport {self.business_name} {self.report_date}>"


class RetentionReport(db.Model):
    __tablename__ = "retention_reports"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=True)
    report_date = db.Column(db.Date, nullable=False)
    source = db.Column(db.String(20), default="gmail")

    # Extracted fields
    business_name = db.Column(db.String(255), nullable=False)
    min_volume = db.Column(db.String(50), nullable=True)
    vol_meet = db.Column(db.String(50), nullable=True)
    days_decline = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("manager_id", "business_name", "report_date",
                            name="uq_retention_report"),
        db.Index("ix_retention_manager_date", "manager_id", "report_date"),
    )

    def __repr__(self):
        return f"<RetentionReport {self.business_name} {self.report_date}>"
