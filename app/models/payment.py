from datetime import datetime, timezone
from app.extensions import db


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"),
                           nullable=False)

    # Monnify transaction reference
    transaction_reference = db.Column(db.String(255), unique=True, nullable=False)
    payment_reference = db.Column(db.String(255), nullable=True)  # Monnify's own ref

    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(10), default="NGN")

    # "one_time" for initial access, "subscription" for monthly renewal
    payment_type = db.Column(db.String(20), nullable=False)

    # "pending", "successful", "failed"
    status = db.Column(db.String(20), default="pending")

    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Raw webhook payload for audit
    webhook_payload = db.Column(db.JSON, nullable=True)

    # Relationships
    manager = db.relationship("BusinessManager", back_populates="payments")

    def __repr__(self):
        return f"<Payment {self.transaction_reference} {self.status}>"
