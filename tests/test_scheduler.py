from app import scheduler
from app.config import settings


def test_scheduler_starts_with_configured_hours(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    monkeypatch.setattr(settings, "scheduler_hours", "10,16")
    sched = scheduler.start_scheduler()
    try:
        job = sched.get_job("daily-cycle")
        assert job is not None
        assert "hour='10,16'" in str(job.trigger)
    finally:
        sched.shutdown(wait=False)


def test_scheduler_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    assert scheduler.start_scheduler() is None
