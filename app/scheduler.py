"""APScheduler wiring: runs the daily cycle at the configured hours."""
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .agent.runner import run_daily_cycle
from .config import settings
from .database import SessionLocal

log = logging.getLogger("invoice_chaser.scheduler")


def _job():
    db = SessionLocal()
    try:
        run_daily_cycle(db)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler | None:
    if not settings.scheduler_enabled:
        return None
    tz = ZoneInfo(settings.timezone)
    scheduler = BackgroundScheduler(timezone=tz)
    trigger = CronTrigger(hour=settings.scheduler_hours, minute=0, timezone=tz)
    scheduler.add_job(_job, trigger, id="daily-cycle")
    scheduler.start()
    log.info("Scheduler started, running at hours: %s", settings.scheduler_hours)
    return scheduler
