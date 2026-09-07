from datetime import datetime, timezone
from app.extensions import db

class FieldVisit(db.Model):
    __tablename__ = "field_visits"

    id = db.Column(db.Integer, primary_key=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("business_managers.id"), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey("staffs.id"), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False)

    visit_date = db.Column(db.Date, nullable=False)
    purpose = db.Column(db.String(100), nullable=False)
    issue = db.Column(db.Text, nullable=True)
    action_taken = db.Column(db.Text, nullable=True)
    result = db.Column(db.String(50), nullable=True)
    next_follow_up = db.Column(db.Date, nullable=True)
    image_filename = db.Column(db.String(255), nullable=True)  # <--- Added for visit pictures

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    manager = db.relationship("BusinessManager", back_populates="field_visits")
    staff = db.relationship("Staff", back_populates="field_visits")
    business = db.relationship("Business", back_populates="field_visits")

    def __repr__(self):
        return f"<FieldVisit staff={self.staff_id} business={self.business_id} date={self.visit_date}>"