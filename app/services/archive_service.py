"""Nightly archive: copy report rows older than REPORT_RETENTION_DAYS to S3, then delete them."""
import csv
import gzip
import io
import logging
from datetime import date, datetime, timedelta, timezone

import boto3
from sqlalchemy import delete, select

from app.extensions import db
from app.models import DailyReport, WeeklyReport, NTTReport, RetentionReport

log = logging.getLogger(__name__)

ARCHIVED_MODELS = (DailyReport, WeeklyReport, NTTReport, RetentionReport)


def archive_old_reports(app):
    with app.app_context():
        cutoff = date.today() - timedelta(days=app.config["REPORT_RETENTION_DAYS"])
        bucket = app.config["S3_BUCKET"]
        s3 = boto3.client("s3", region_name=app.config["AWS_REGION"])
        run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        for model in ARCHIVED_MODELS:
            table = model.__table__
            old_dates = db.session.execute(
                select(table.c.report_date)
                .where(table.c.report_date < cutoff)
                .distinct()
                .order_by(table.c.report_date)
            ).scalars().all()

            for d in old_dates:
                try:
                    body, n_rows = _export_day(table, d)
                    key = f"archive/{table.name}/report_date={d.isoformat()}/{run_ts}.csv.gz"
                    s3.put_object(Bucket=bucket, Key=key, Body=body,
                                  ContentType="application/gzip",
                                  ServerSideEncryption="AES256")

                    # Only reached if the upload succeeded
                    db.session.execute(delete(table).where(table.c.report_date == d))
                    db.session.commit()
                    log.info("Archived %s rows of %s for %s to s3://%s/%s",
                             n_rows, table.name, d, bucket, key)
                except Exception:
                    db.session.rollback()
                    log.exception("Archive failed for %s on %s; rows kept, will retry tomorrow",
                                  table.name, d)
                    break   # stop this table for tonight; move on to the next one


def _export_day(table, d):
    """All rows of one table for one report date, as gzipped CSV bytes."""
    buf = io.BytesIO()
    n = 0
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        writer = csv.writer(text)
        writer.writerow(table.columns.keys())
        rows = db.session.execute(
            select(table).where(table.c.report_date == d).execution_options(yield_per=5000)
        )
        for row in rows:
            writer.writerow(row)
            n += 1
        text.flush()
        text.detach()   # leave the gzip stream open for the with-block to close
    return buf.getvalue(), n