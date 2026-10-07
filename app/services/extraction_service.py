"""
extraction_service.py

Adapts your four extraction functions to:
  - Accept PDF bytes instead of a file path
  - Persist results into the four report tables
  - Link extracted business names to the Business table
  - Log results in FetchLog

Behaviour:
  - A report only counts if the required sections (REQUIRED) contain records
    and no section raises. Otherwise nothing is saved, the failure is logged,
    the super admin is emailed, and ExtractionError is raised.
  - A successful upload REPLACES every row for that manager and report date,
    so re-uploading a corrected report leaves only the latest data.
"""

import io
from datetime import date
import fitz  # PyMuPDF
import pandas as pd

from app.extensions import db
from app.models import Business, DailyReport, WeeklyReport, NTTReport, RetentionReport, FetchLog
from flask import current_app
from app.services.mail_service import send_extraction_failure_alert


class ExtractionError(Exception):
    """Raised when a report cannot be processed. Nothing is saved when this happens."""


# Sections that must contain records for an upload to count as valid.
# ntt and retention may legitimately be empty on a good day.
REQUIRED = ("daily", "weekly")


# ── Public entry point ────────────────────────────────────────────────────────

def run_extraction(manager, pdf_bytes: bytes, report_date: date, source: str = "gmail"):
    """
    Called by the scheduler and the manual upload route.
    Extracts all four sections, validates them, then replaces that report
    date's rows in one transaction. Raises ExtractionError on failure.
    """
    extractors = {
        "daily": extract_daily_report,
        "weekly": extract_business_report,
        "ntt": extract_ntt_report,
        "retention": extract_retention_report,
    }

    # 1. Extract everything first. Nothing touches the database yet.
    frames, problems = {}, []
    for name, fn in extractors.items():
        try:
            frames[name] = fn(pdf_bytes)
        except Exception as e:
            frames[name] = pd.DataFrame()
            problems.append(f"{name}: {e}")

    # 2. Validate. Existing data for this date is untouched if this fails.
    problems += [f"{n}: no records found" for n in REQUIRED if frames[n].empty]
    if problems:
        _fail(manager, report_date, "; ".join(problems))  # raises

    # 3. Replace this date's rows atomically.
    try:
        biz_map = {
            name: bid for name, bid in
            db.session.query(Business.name, Business.id).filter(Business.manager_id == manager.id)
        }
        counts = {
            "daily": _replace(DailyReport, manager, report_date,
                              _daily_rows(manager, frames["daily"], source, biz_map)),
            "weekly": _replace(WeeklyReport, manager, report_date,
                               _weekly_rows(manager, frames["weekly"], source, biz_map)),
            "ntt": _replace(NTTReport, manager, report_date,
                            _ntt_rows(manager, frames["ntt"], source, biz_map)),
            "retention": _replace(RetentionReport, manager, report_date,
                                  _retention_rows(manager, frames["retention"], source, biz_map)),
        }
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        _fail(manager, report_date, f"save: {e}")  # raises

    message = ", ".join(f"{k}={v}" for k, v in counts.items())
    _log(manager.id, report_date, "success", message, sum(counts.values()))
    return counts


def _fail(manager, report_date, message):
    """Log the failure, alert the super admin only, then raise ExtractionError."""
    _log(manager.id, report_date, "extraction_error", message, 0)
    try:
        send_extraction_failure_alert(manager, report_date, message)
    except Exception as e:
        current_app.logger.warning("Extraction alert email failed: %s", e)
    raise ExtractionError(message)


# ── Extraction functions (adapted from your original code) ────────────────────

def _open_pdf(pdf_bytes: bytes):
    """Open a PDF from bytes using PyMuPDF."""
    return fitz.open(stream=pdf_bytes, filetype="pdf")


def extract_daily_report(pdf_bytes: bytes) -> pd.DataFrame:
    """Extract Daily Terminal Transactions - no terminal_id."""
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

    while i+5 < len(data_list):
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
    for page in doc[15:]:
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

    while i+5 < len(data_list):
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
    for page in doc[35:]:
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

    while i+3 < len(data_list):
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
                "terminal_serial": data_list[i],
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

    while i+10 < len(data_list):
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

def _get_or_create_business(manager_id, name, biz_map):
    """Get or create a Business record, return its id. biz_map avoids one SELECT per row."""
    name = name.strip()
    if name not in biz_map:
        business = Business(manager_id=manager_id, name=name)
        db.session.add(business)
        db.session.flush()
        biz_map[name] = business.id
    return biz_map[name]


def _replace(model, manager, report_date, rows: dict) -> int:
    """
    Delete this manager's rows for the report date, then insert the new ones.
    The caller commits, so all four tables are replaced in one transaction.
    """
    model.query.filter_by(manager_id=manager.id, report_date=report_date).delete(
        synchronize_session=False
    )
    for kwargs in rows.values():
        db.session.add(model(manager_id=manager.id, report_date=report_date, **kwargs))
    return len(rows)


# Row builders. Dicts are keyed on the same columns as each table's unique
# constraint, so duplicate rows inside one PDF collapse to the last one.

def _daily_rows(manager, df: pd.DataFrame, source: str, biz_map: dict) -> dict:
    rows = {}
    for _, row in df.iterrows():
        name = str(row.get("business_name", "")).strip()
        if not name:
            continue
        serial = str(row.get("terminal_serial", ""))
        rows[(name, serial)] = dict(
            business_id=_get_or_create_business(manager.id, name, biz_map),
            source=source,
            business_name=name,
            terminal_serial=serial,
            target_met=str(row.get("target_met", "")),
            payment_value=float(row.get("payment_value", 0)),
            payment_vol=int(row.get("payment_vol", 0)),
            days_last_transact=str(row.get("days_last_transact", "")),
        )
    return rows


def _weekly_rows(manager, df: pd.DataFrame, source: str, biz_map: dict) -> dict:
    rows = {}
    for _, row in df.iterrows():
        name = str(row.get("Business Name", "")).strip()
        if not name:
            continue
        rows[name] = dict(
            business_id=_get_or_create_business(manager.id, name, biz_map),
            source=source,
            business_name=name,
            target_met=str(row.get("target_met", "")),
            payment_value=float(row.get("payment_value", 0)),
            payment_vol=int(row.get("payment_vol", 0)),
            days_last_transact=str(row.get("days_last_transact", "")),
        )
    return rows


def _ntt_rows(manager, df: pd.DataFrame, source: str, biz_map: dict) -> dict:
    rows = {}
    for _, row in df.iterrows():
        name = str(row.get("Business Name", "")).strip()
        if not name:
            continue
        rows[name] = dict(
            business_id=_get_or_create_business(manager.id, name, biz_map),
            source=source,
            business_name=name,
            terminal_serial=str(row.get("terminal_serial", row.get("Terminal Serial", ""))).strip(),
            days_last_transact=str(row.get("days_last_transact", "")),
        )
    return rows


def _retention_rows(manager, df: pd.DataFrame, source: str, biz_map: dict) -> dict:
    rows = {}
    for _, row in df.iterrows():
        name = str(row.get("Business Name", "")).strip()
        if not name:
            continue
        rows[name] = dict(
            business_id=_get_or_create_business(manager.id, name, biz_map),
            source=source,
            business_name=name,
            min_volume=str(row.get("min_volume", "")),
            vol_meet=str(row.get("vol_meet", "")),
            days_decline=str(row.get("days_decline", "")),
        )
    return rows


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