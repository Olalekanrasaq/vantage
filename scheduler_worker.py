"""
Runs the scheduled jobs in production. Start exactly ONE of these per deployment.

    python scheduler_worker.py                 # run on schedule (systemd keeps it alive)
    python scheduler_worker.py --run <job_id>  # run one job immediately, then exit (for testing)
"""
import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

from app import create_app
from app.services.reminder_service import send_grace_reminders, send_missing_report_alerts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vantage.scheduler")

app = create_app("production")

JOBS = {
    "grace_reminders": (send_grace_reminders, {"hour": 8, "minute": 0}),
    "missing_report_alerts": (send_missing_report_alerts, {"hour": 17, "minute": 0}),
}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--run":
        job_id = sys.argv[2]
        if job_id not in JOBS:
            sys.exit(f"Unknown job '{job_id}'. Choose from: {', '.join(JOBS)}")
        log.info("Running %s once", job_id)
        JOBS[job_id][0](app)
        return

    sched = BlockingScheduler(timezone="Africa/Lagos")
    for job_id, (func, when) in JOBS.items():
        sched.add_job(func, "cron", args=[app], id=job_id, replace_existing=True,
                      coalesce=True, misfire_grace_time=3600, **when)
        log.info("Scheduled %s at %02d:%02d Africa/Lagos", job_id, when["hour"], when["minute"])

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")


if __name__ == "__main__":
    main()