from datetime import datetime, timezone
from app.extensions import db

class CallLog(db.Model):
    __tablename__ = 'call_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey('business_managers.id'), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('staffs.id'), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey('businesses.id'), nullable=False)
    
    call_date = db.Column(db.Date, nullable=False)
    purpose = db.Column(db.String(100), nullable=False)
    customer_response = db.Column(db.Text, nullable=True)
    result = db.Column(db.String(50), nullable=False)
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.Index("ix_call_manager_date", "manager_id", "call_date"),
        db.Index("ix_call_staff_date", "staff_id", "call_date"),
    )

    # Relationships
    manager = db.relationship("BusinessManager", back_populates="call_logs")
    staff = db.relationship("Staff", back_populates="call_logs")
    business = db.relationship("Business", back_populates="call_logs")