from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import scheduling
from app.database import Base


@pytest.fixture
def availability_db(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    monkeypatch.setattr(scheduling, "provider_busy", lambda *_args, **_kwargs: [])
    # 07:00 Monday in Asia/Ho_Chi_Minh; the generated window covers one working day.
    monkeypatch.setattr(
        scheduling,
        "utcnow",
        lambda: datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc),
    )
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("duration_minutes", [30, 60, 90, 180])
def test_every_slot_finishes_within_working_hours(availability_db, duration_minutes):
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    slots = scheduling.available_slots_for_owner(
        availability_db,
        "working-hours-test",
        duration_minutes=duration_minutes,
        days=1,
    )

    assert slots
    for slot in slots:
        local_start = slot.astimezone(zone)
        local_end = (slot + timedelta(minutes=duration_minutes)).astimezone(zone)
        closing_time = local_start.replace(hour=17, minute=0, second=0, microsecond=0)
        assert local_end <= closing_time


def test_three_hour_interview_is_not_offered_after_14_00(availability_db):
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    slots = scheduling.available_slots_for_owner(
        availability_db,
        "long-interview-test",
        duration_minutes=180,
        days=1,
    )

    assert max(slot.astimezone(zone).hour for slot in slots) == 14
