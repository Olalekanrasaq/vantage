from sqlalchemy import and_, case, delete, func, select
from flask import current_app

from app.extensions import db
from app.models import (Business, DailyReport, NTTReport, RetentionReport, Staff,
                        StaffBusinessAssignment, StaffDailyPerformance, WeeklyReport)


def compute_staff_performance(manager_id, report_date):
    """Snapshot each active staff member's performance for one report date.
    The unit is the terminal: an assigned business with 2 terminals counts as 2.
    Runs inside run_extraction's transaction; replaces any earlier snapshot for that date."""
    weekly_target = current_app.config.get("WEEKLY_TARGET", 100_000)
    daily_target = current_app.config.get("DAILY_TARGET", 14_286)

    T = WeeklyReport  # one row per terminal

    daily_met_serials = select(DailyReport.terminal_serial).where(
        DailyReport.manager_id == manager_id,
        DailyReport.report_date == report_date,
        DailyReport.payment_value >= daily_target,
        DailyReport.terminal_serial.isnot(None),
    )
    # NOT IN returns nothing if the list holds a NULL, so exclude NULL and blank serials
    ntt_serials = select(NTTReport.terminal_serial).where(
        NTTReport.manager_id == manager_id,
        NTTReport.report_date == report_date,
        NTTReport.terminal_serial.isnot(None),
        NTTReport.terminal_serial != "",
    )
    declining = select(RetentionReport.business_name).where(
        RetentionReport.manager_id == manager_id,
        RetentionReport.report_date == report_date,
        RetentionReport.business_name.isnot(None),
    )

    def count_if(cond):
        return func.sum(case((cond, 1), else_=0))

    has_terminal = T.id.isnot(None)

    rows = (
        db.session.query(
            Staff.id,
            func.count(T.id),
            count_if(T.payment_value >= weekly_target),
            count_if(T.terminal_serial.in_(daily_met_serials)),
            count_if(and_(has_terminal, ~T.business_name.in_(declining))),
            count_if(and_(has_terminal, ~T.terminal_serial.in_(ntt_serials))),
        )
        .select_from(Staff)
        .outerjoin(StaffBusinessAssignment, StaffBusinessAssignment.staff_id == Staff.id)
        .outerjoin(Business, and_(Business.id == StaffBusinessAssignment.business_id,
                                  Business.is_active.is_(True)))
        .outerjoin(T, and_(T.manager_id == manager_id,
                           T.report_date == report_date,
                           T.business_name == Business.name))
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