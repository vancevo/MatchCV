from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import scheduling
from app.database import Base
from app.models import InterviewPolicy


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


def test_wide_working_hours_still_cover_the_full_three_week_window(availability_db):
    # 6 working days x 9h/day = 54 slots/week; a flat "first 120 slots" cap used to run out partway
    # through week 3, silently shrinking the public scheduling page's 21-day range to ~2 weeks.
    availability_db.add(InterviewPolicy(
        owner_id="wide-hours-test", working_days=[0, 1, 2, 3, 4, 5],
        working_start_hour=9, working_end_hour=18,
    ))
    availability_db.flush()
    slots = scheduling.available_slots_for_owner(
        availability_db, "wide-hours-test", duration_minutes=60, days=21,
    )
    last_slot_day = max(slots).date()
    window_start_day = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc).date()
    assert (last_slot_day - window_start_day).days >= 18


def test_three_hour_interview_is_not_offered_after_14_00(availability_db):
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    slots = scheduling.available_slots_for_owner(
        availability_db,
        "long-interview-test",
        duration_minutes=180,
        days=1,
    )

    assert max(slot.astimezone(zone).hour for slot in slots) == 14
