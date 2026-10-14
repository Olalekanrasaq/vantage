from sqlalchemy import and_, case, delete, func, select
from flask import current_app

from app.extensions import db
from app.models import (Business, DailyReport, NTTReport, RetentionReport, Staff,
                        StaffBusinessAssignment, StaffDailyPerformance, WeeklyReport)


def compute_staff_performance(manager_id, report_date):
    """Snapshot each active staff member's business performance for one report date.
    Runs inside run_extraction's transaction; replaces any earlier snapshot for that date."""
    weekly_target = current_app.config.get("WEEKLY_TARGET", 100_000)
    daily_target = current_app.config.get("DAILY_TARGET", 14_286)

    def names(model, *conds):
        return select(model.business_name).where(
            model.manager_id == manager_id,
            model.report_date == report_date,
            model.business_name.isnot(None),
            *conds,
        )

    weekly_met = names(WeeklyReport, WeeklyReport.payment_value >= weekly_target)
    daily_met = names(DailyReport, DailyReport.payment_value >= daily_target)
    declining = names(RetentionReport)
    non_transacting = names(NTTReport)

    def count_if(cond):
        return func.sum(case((cond, 1), else_=0))

    rows = (
        db.session.query(
            Staff.id,
            func.count(Business.id),
            count_if(Business.name.in_(weekly_met)),
            count_if(Business.name.in_(daily_met)),
            count_if(~Business.name.in_(declining)),
            count_if(~Business.name.in_(non_transacting)),
        )
        .select_from(Staff)
        .outerjoin(StaffBusinessAssignment, StaffBusinessAssignment.staff_id == Staff.id)
        .outerjoin(Business, and_(Business.id == StaffBusinessAssignment.business_id,
                                  Business.is_active.is_(True)))
        .filter(Staff.manager_id == manager_id, Staff.is_active.is_(True))
        .group_by(Staff.id)
        .all()
    )

    db.session.execute(
        delete(StaffDailyPerformance).where(
            StaffDailyPerformance.manager_id == manager_id,
            StaffDailyPerformance.report_date == report_date,
        )
    )
    db.session.add_all([
        StaffDailyPerformance(
            manager_id=manager_id, staff_id=staff_id, report_date=report_date,
            assigned=assigned, weekly_target_met=wk or 0, daily_target_met=dy or 0,
            not_declining=nd or 0, transacting=tx or 0,
        )
        for staff_id, assigned, wk, dy, nd, tx in rows
    ])