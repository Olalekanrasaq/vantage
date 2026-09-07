"""
extraction_service.py

Adapts your four extraction functions to:
  - Accept PDF bytes instead of a file path
  - Persist results into the four report tables
  - Link extracted business names to the Business table
  - Log results in FetchLog
"""

import io
from datetime import date
import fitz  # PyMuPDF
import pandas as pd

from app.extensions import db
from app.models import Business, DailyReport, WeeklyReport, NTTReport, RetentionReport, FetchLog


# ── Public entry point ────────────────────────────────────────────────────────

def run_extraction(manager, pdf_bytes: bytes, report_date: date, source: str = "gmail"):
    """
    Called by the scheduler and the manual upload route.
    Runs all four extractions and persists results.
    """
    counts = {"daily": 0, "weekly": 0, "ntt": 0, "retention": 0}
    errors = []

    try:
        daily_df = extract_daily_report(pdf_bytes)
        counts["daily"] = _save_daily(manager, daily_df, report_date, source)
    except Exception as e:
        errors.append(f"daily: {e}")

    try:
        weekly_df = extract_business_report(pdf_bytes)
        counts["weekly"] = _save_weekly(manager, weekly_df, report_date, source)
    except Exception as e:
        errors.append(f"weekly: {e}")

    try:
        ntt_df = extract_ntt_report(pdf_bytes)
        counts["ntt"] = _save_ntt(manager, ntt_df, report_date, source)
    except Exception as e:
        errors.append(f"ntt: {e}")

    try:
        retention_df = extract_retention_report(pdf_bytes)
        counts["retention"] = _save_retention(manager, retention_df, report_date, source)
    except Exception as e:
        errors.append(f"retention: {e}")

    total = sum(counts.values())
    message = (
        f"daily={counts['daily']}, weekly={counts['weekly']}, "
        f"ntt={counts['ntt']}, retention={counts['retention']}"
    )
    if errors:
        message += f" | errors: {'; '.join(errors)}"

    status = "extraction_error" if not total and errors else "success"
    _log(manager.id, report_date, status, message, total)

    if errors and not total:
        raise RuntimeError("; ".join(errors))

    return counts


# ── Extraction functions (adapted from your original code) ────────────────────

def _open_pdf(pdf_bytes: bytes):
    """Open a PDF from bytes using PyMuPDF."""
    return fitz.open(stream=pdf_bytes, filetype="pdf")


def extract_daily_report(pdf_bytes: bytes) -> pd.DataFrame:
    """Extract Daily Terminal Transactions — no terminal_id."""
    doc = _open_pdf(pdf_bytes)
    text = ""
    for page in doc[3:]:
        text += page.get_text("text") + "\n"

    lines = text.split("\n")

    idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if "Daily Terminal Transactions" in line and idx is None:
            idx = i
        if "Weekly Terminal Transactions" in line and end_idx is None:
            end_idx = i

    if idx is None or end_idx is None:
        return pd.DataFrame()

    data_list = lines[idx + 14: end_idx]

    bos = []
    i = 0

    while i < len(data_list):
        if "Page" in data_list[i]:
            i += 16
            continue
        if "Transfer Value" in data_list[i]:
            i += 3
            continue

        start = i + 2
        names_part = []
        while start < len(data_list):
            is_name = data_list[start].strip()
            if is_name[-4:].isdigit():
                break
            names_part.append(is_name)
            start += 1
        i = start

        try:
            bo = {
                "business_name": " ".join(names_part),
                "terminal_serial": str(data_list[i]),
                "target_met": data_list[i + 2],
                "payment_value": float(data_list[i + 3].replace(",", "")),
                "payment_vol": int(data_list[i + 4].replace(",", "")),
                "days_last_transact": data_list[i + 7],
            }
            bos.append(bo)
        except (IndexError, ValueError):
            pass

        i += 8

    return pd.DataFrame(bos)


def extract_business_report(pdf_bytes: bytes) -> pd.DataFrame:
    """Extract Weekly Terminal Transactions."""
    doc = _open_pdf(pdf_bytes)
    text = ""
    for page in doc[35:]:
        text += page.get_text("text") + "\n"

    lines = text.split("\n")

    idx = None
    for i, line in enumerate(lines):
        if "Weekly Terminal Transactions" in line:
            idx = i
            break

    if idx is None:
        return pd.DataFrame()

    try:
        end_idx = lines.index("Non-Transacting Terminals")
    except ValueError:
        return pd.DataFrame()

    data_list = lines[idx + 14: end_idx]

    bos = []
    i = 0

    while i < len(data_list):
        if "Page" in data_list[i]:
            i += 16
            continue
        if "Transfer Value" in data_list[i]:
            i += 3
            continue

        start = i + 2
        names_part = []
        while start < len(data_list):
            is_name = data_list[start].strip()
            if is_name[-3:].isdigit() and len(is_name) > 4:
                break
            names_part.append(is_name)
            start += 1
        i = start

        try:
            bo = {
                "Business Name": " ".join(names_part),
                "target_met": data_list[i + 2],
                "payment_value": float(data_list[i + 3].replace(",", "")),
                "payment_vol": int(data_list[i + 4].replace(",", "")),
                "days_last_transact": data_list[i + 7],
            }
            bos.append(bo)
        except (IndexError, ValueError):
            pass

        i += 8

    return pd.DataFrame(bos)


def extract_ntt_report(pdf_bytes: bytes) -> pd.DataFrame:
    """Extract Non-Transacting Terminals."""
    doc = _open_pdf(pdf_bytes)
    text = ""
    for page in doc[75:]:
        text += page.get_text("text") + "\n"

    lines = text.split("\n")

    try:
        idx = lines.index("Non-Transacting Terminals")
        end_idx = lines.index("Businesses with Sold Cards")
    except ValueError:
        return pd.DataFrame()

    data_list = lines[idx + 10: end_idx]

    bos = []
    i = 0

    while i < len(data_list):
        if "Page" in data_list[i]:
            i += 12
            continue
        if "Transaction" in data_list[i]:
            i += 3
            continue

        start = i + 2
        names_part = []
        while start < len(data_list):
            is_name = data_list[start].strip()
            if is_name[-4:].isdigit() and len(is_name) > 5:
                break
            names_part.append(is_name)
            start += 1
        i = start

        try:
            bo = {
                "Business Name": " ".join(names_part),
                "days_last_transact": data_list[i + 2],
            }
            bos.append(bo)
        except (IndexError, ValueError):
            pass

        i += 5

    return pd.DataFrame(bos)


def extract_retention_report(pdf_bytes: bytes) -> pd.DataFrame:
    """Extract Declined Top Businesses (Retention Report)."""
    doc = _open_pdf(pdf_bytes)
    text = ""
    for page in doc[:10]:
        text += page.get_text("text") + "\n"

    lines = text.split("\n")

    try:
        idx = lines.index("Declined Top Businesses")
        end_idx = lines.index("Recently Recovered Top Businesses")
    except ValueError:
        return pd.DataFrame()

    data_list = lines[idx + 53: end_idx]

    bos = []
    i = 0

    while i < len(data_list):
        if "Note" in data_list[i]:
            i += 57
            continue
        if "Page" in data_list[i]:
            i += 55
            continue

        start = i
        names_part = []
        while start < len(data_list):
            is_name = data_list[start].strip()
            if is_name.isdigit():
                break
            names_part.append(is_name)
            start += 1
        i = start

        try:
            bo = {
                "Business Name": " ".join(names_part),
                "min_volume": data_list[i + 12],
                "vol_meet": data_list[i + 1],
                "days_decline": data_list[i + 13],
            }
            bos.append(bo)
        except (IndexError, ValueError):
            pass

        i += 14

    return pd.DataFrame(bos)


# ── DB persistence helpers ────────────────────────────────────────────────────

def _get_or_create_business(manager_id, name):
    """Get or create a Business record, return its id."""
    name = name.strip()
    business = Business.query.filter_by(manager_id=manager_id, name=name).first()
    if not business:
        business = Business(manager_id=manager_id, name=name)
        db.session.add(business)
        db.session.flush()
    return business.id


def _save_daily(manager, df: pd.DataFrame, report_date: date, source: str) -> int:
    if df.empty:
        return 0
    count = 0
    for _, row in df.iterrows():
        name = str(row.get("business_name", "")).strip()
        if not name:
            continue
        business_id = _get_or_create_business(manager.id, name)
        existing = DailyReport.query.filter_by(
            manager_id=manager.id,
            business_name=name,
            terminal_serial=str(row.get("terminal_serial", "")),
            report_date=report_date,
        ).first()
        if existing:
            existing.target_met = str(row.get("target_met", ""))
            existing.payment_value = float(row.get("payment_value", 0))
            existing.payment_vol = int(row.get("payment_vol", 0))
            existing.days_last_transact = str(row.get("days_last_transact", ""))
            existing.source = source
        else:
            record = DailyReport(
                manager_id=manager.id,
                business_id=business_id,
                report_date=report_date,
                source=source,
                business_name=name,
                terminal_serial=str(row.get("terminal_serial", "")),
                target_met=str(row.get("target_met", "")),
                payment_value=float(row.get("payment_value", 0)),
                payment_vol=int(row.get("payment_vol", 0)),
                days_last_transact=str(row.get("days_last_transact", "")),
            )
            db.session.add(record)
            count += 1
    db.session.commit()
    return count


def _save_weekly(manager, df: pd.DataFrame, report_date: date, source: str) -> int:
    if df.empty:
        return 0
    count = 0
    for _, row in df.iterrows():
        name = str(row.get("Business Name", "")).strip()
        if not name:
            continue
        business_id = _get_or_create_business(manager.id, name)
        existing = WeeklyReport.query.filter_by(
            manager_id=manager.id,
            business_name=name,
            report_date=report_date,
        ).first()
        if existing:
            existing.target_met = str(row.get("target_met", ""))
            existing.payment_value = float(row.get("payment_value", 0))
            existing.payment_vol = int(row.get("payment_vol", 0))
            existing.days_last_transact = str(row.get("days_last_transact", ""))
            existing.source = source
        else:
            record = WeeklyReport(
                manager_id=manager.id,
                business_id=business_id,
                report_date=report_date,
                source=source,
                business_name=name,
                target_met=str(row.get("target_met", "")),
                payment_value=float(row.get("payment_value", 0)),
                payment_vol=int(row.get("payment_vol", 0)),
                days_last_transact=str(row.get("days_last_transact", "")),
            )
            db.session.add(record)
            count += 1
    db.session.commit()
    return count


def _save_ntt(manager, df: pd.DataFrame, report_date: date, source: str) -> int:
    if df.empty:
        return 0
    count = 0
    for _, row in df.iterrows():
        name = str(row.get("Business Name", "")).strip()
        if not name:
            continue
        business_id = _get_or_create_business(manager.id, name)
        existing = NTTReport.query.filter_by(
            manager_id=manager.id,
            business_name=name,
            report_date=report_date,
        ).first()
        if existing:
            existing.days_last_transact = str(row.get("days_last_transact", ""))
            existing.source = source
        else:
            record = NTTReport(
                manager_id=manager.id,
                business_id=business_id,
                report_date=report_date,
                source=source,
                business_name=name,
                days_last_transact=str(row.get("days_last_transact", "")),
            )
            db.session.add(record)
            count += 1
    db.session.commit()
    return count


def _save_retention(manager, df: pd.DataFrame, report_date: date, source: str) -> int:
    if df.empty:
        return 0
    count = 0
    for _, row in df.iterrows():
        name = str(row.get("Business Name", "")).strip()
        if not name:
            continue
        business_id = _get_or_create_business(manager.id, name)
        existing = RetentionReport.query.filter_by(
            manager_id=manager.id,
            business_name=name,
            report_date=report_date,
        ).first()
        if existing:
            existing.min_volume = str(row.get("min_volume", ""))
            existing.vol_meet = str(row.get("vol_meet", ""))
            existing.days_decline = str(row.get("days_decline", ""))
            existing.source = source
        else:
            record = RetentionReport(
                manager_id=manager.id,
                business_id=business_id,
                report_date=report_date,
                source=source,
                business_name=name,
                min_volume=str(row.get("min_volume", "")),
                vol_meet=str(row.get("vol_meet", "")),
                days_decline=str(row.get("days_decline", "")),
            )
            db.session.add(record)
            count += 1
    db.session.commit()
    return count


# ── Fetch log ─────────────────────────────────────────────────────────────────

def _log(manager_id, report_date, status, message, count):
    log = FetchLog(
        manager_id=manager_id,
        report_date=report_date,
        status=status,
        message=message,
        businesses_extracted=count,
    )
    db.session.add(log)
    db.session.commit()
