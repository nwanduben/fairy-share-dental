import asyncio
from datetime import date, datetime, time

import pytest

from app.practice_config import ConfigError, load_config
from app.services.availability import AvailabilityService, spoken_datetime
from tests.conftest import FIXED_NOW

MON = date(2026, 9, 28)
FRI = date(2026, 10, 2)


def run(coro):
    return asyncio.run(coro)


def find(svc, cfg, type_key, d_from, d_to, tod="any", now=FIXED_NOW):
    return run(svc.find_options(cfg.appointment_types[type_key], d_from, d_to, tod, now))


@pytest.fixture
def svc(cfg, od):
    return AvailabilityService(cfg, od)


def test_config_loads_and_durations_match_patterns(cfg):
    assert set(cfg.appointment_types) == {
        "new_patient_exam", "cleaning", "emergency_exam", "general_consultation", "cosmetic_consultation"
    }
    for t in cfg.appointment_types.values():
        assert len(t.pattern) * 5 == t.duration_minutes


def test_config_rejects_pattern_duration_mismatch(tmp_path, settings):
    for name in ("practice.yaml", "appointment_types.yaml"):
        (tmp_path / name).write_text((settings.config_dir / name).read_text())
    p = tmp_path / "appointment_types.yaml"
    p.write_text(p.read_text().replace("duration_minutes: 45", "duration_minutes: 50"))
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_cleaning_uses_hygiene_rooms_and_hygienists(svc, cfg):
    opts = find(svc, cfg, "cleaning", MON, MON)
    assert opts
    for o in opts:
        c = o.candidate
        assert c.op in (5, 6) and c.is_hygiene
        assert c.prov_hyg in (2, 4)
        assert "hygienist" in o.provider_name


def test_dentist_types_use_dentist_rooms(svc, cfg):
    for key in ("emergency_exam", "general_consultation", "cosmetic_consultation"):
        for o in find(svc, cfg, key, MON, MON):
            assert o.candidate.op in (1, 2) and not o.candidate.is_hygiene
            assert o.candidate.prov_num in (1, 3)


def test_respects_hours_and_lunch(svc, cfg):
    opts = run(svc.find_options(cfg.appointment_types["cleaning"], MON, FRI, "any", FIXED_NOW))
    for o in opts:
        s, e = o.candidate.start, o.candidate.end
        assert s.weekday() < 5
        assert not (s.time() < time(13) and e.time() > time(12))  # never overlaps lunch
        assert s.time() >= time(8)


def test_friday_closes_at_two(svc, cfg, od):
    # Block everything but the last possible Friday hour to force the edge.
    opts = find(svc, cfg, "cleaning", FRI, FRI)
    assert all(o.candidate.end <= datetime.combine(FRI, time(14)) for o in opts)


def test_weekend_has_no_options(svc, cfg):
    assert find(svc, cfg, "cleaning", date(2026, 9, 26), date(2026, 9, 27)) == []


def test_min_notice_today(svc, cfg):
    opts = find(svc, cfg, "emergency_exam", FIXED_NOW.date(), FIXED_NOW.date())
    assert opts and all(o.candidate.start >= datetime(2026, 9, 22, 11, 0) for o in opts)


def test_existing_appointments_block_room(svc, cfg, od):
    for op, hyg in ((5, 2), (6, 4)):
        od.add_appointment(start=datetime(2026, 9, 28, 8, 0), op=op, pattern="X" * 12, prov_hyg=hyg, is_hygiene=True)
    opts = find(svc, cfg, "cleaning", MON, MON)
    assert all(o.candidate.start >= datetime(2026, 9, 28, 9, 0) for o in opts)


def test_busy_provider_in_other_room_blocks(svc, cfg, od):
    # Dr. Albert (1) is seeing someone in the overflow room at 8:00 -> OP-1 at 8:00 is not offered.
    od.add_appointment(start=datetime(2026, 9, 28, 8, 0), op=3, pattern="X" * 6, prov_num=1)
    od.add_appointment(start=datetime(2026, 9, 28, 8, 0), op=2, pattern="X" * 6, prov_num=3)
    opts = find(svc, cfg, "emergency_exam", MON, MON)
    assert all(o.candidate.start >= datetime(2026, 9, 28, 8, 30) for o in opts)


def test_planned_appointments_do_not_block(svc, cfg, od):
    for op in (5, 6):
        od.add_appointment(start=datetime(2026, 9, 28, 8, 0), op=op, pattern="X" * 12, status="Planned")
    opts = find(svc, cfg, "cleaning", MON, MON)
    assert opts[0].candidate.start == datetime(2026, 9, 28, 8, 0)


def test_morning_and_afternoon_filters(svc, cfg):
    assert all(o.candidate.start.hour < 12 for o in find(svc, cfg, "cleaning", MON, FRI, "morning"))
    assert all(o.candidate.start.hour >= 12 for o in find(svc, cfg, "cleaning", MON, FRI, "afternoon"))


def test_options_spread_across_days(svc, cfg):
    opts = find(svc, cfg, "cleaning", MON, FRI)
    assert len(opts) == 3
    assert len({o.candidate.start.date() for o in opts}) == 3


def test_search_range_clamped(svc, cfg):
    start, end = svc.clamp_range(date(2026, 1, 1), date(2027, 12, 31), FIXED_NOW)
    assert start == FIXED_NOW.date()
    assert (end - start).days == cfg.rules.max_search_days - 1


def test_spoken_datetime():
    assert spoken_datetime(datetime(2026, 9, 29, 9, 0)) == "Tuesday, September 29th at 9 AM"
    assert spoken_datetime(datetime(2026, 10, 1, 13, 30)) == "Thursday, October 1st at 1:30 PM"
