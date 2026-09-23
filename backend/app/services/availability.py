"""CHECK: find real, bookable times for an appointment type."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from ..opendental.client import OpenDentalAPI
from ..practice_config import AppointmentType, PracticeConfig
from .schedule import Candidate, candidate_for, find_conflict

MORNING_END = time(12, 0)


@dataclass(frozen=True)
class Option:
    candidate: Candidate
    spoken_time: str
    provider_name: str


class AvailabilityService:
    def __init__(self, cfg: PracticeConfig, od: OpenDentalAPI):
        self.cfg = cfg
        self.od = od

    def now_local(self) -> datetime:
        """Current practice-local time as a naive datetime (Open Dental has no timezone)."""
        return datetime.now(self.cfg.timezone).replace(tzinfo=None, microsecond=0)

    def clamp_range(self, date_from: date | None, date_to: date | None, now: datetime) -> tuple[date, date]:
        today = now.date()
        start = max(date_from or today, today)
        max_end = start + timedelta(days=self.cfg.rules.max_search_days - 1)
        end = min(date_to or (start + timedelta(days=6)), max_end)
        return start, end

    async def find_options(
        self,
        appt_type: AppointmentType,
        date_from: date | None,
        date_to: date | None,
        time_of_day: str = "any",
        now: datetime | None = None,
    ) -> list[Option]:
        now = now or self.now_local()
        start, end = self.clamp_range(date_from, date_to, now)
        if end < start:
            return []
        earliest = now + timedelta(minutes=self.cfg.rules.min_notice_minutes)

        existing = await self.od.list_appointments(start, end)
        windows = await self._windows(appt_type, start, end)

        candidates: list[Candidate] = []
        step = timedelta(minutes=self.cfg.rules.start_increment_minutes)
        length = timedelta(minutes=appt_type.duration_minutes)
        for op_num, seg_start, seg_end in windows:
            t = seg_start
            while t + length <= seg_end:
                if t >= earliest and _matches_time_of_day(t, time_of_day):
                    cand = candidate_for(self.cfg, appt_type, op_num, t)
                    if find_conflict(cand, existing) is None:
                        candidates.append(cand)
                t += step

        return [self._to_option(c) for c in _spread(candidates, self.cfg.rules.max_options_offered)]

    async def is_still_free(self, cand: Candidate) -> bool:
        """Fresh re-check straight from Open Dental (no cache) right before booking."""
        day = cand.start.date()
        existing = await self.od.list_appointments(day, day)
        return find_conflict(cand, existing) is None

    async def _windows(self, appt_type: AppointmentType, start: date, end: date):
        """(op, window_start, window_end) segments where this type may be booked."""
        if self.cfg.availability_source == "slots":
            out = []
            for op_num in appt_type.operatories:
                cand = candidate_for(self.cfg, appt_type, op_num, datetime.combine(start, time()))
                slots = await self.od.get_slots(start, end, cand.busy_provider, op_num, appt_type.duration_minutes)
                out.extend((op_num, s, e) for s, e, _prov, slot_op in slots if slot_op in (0, op_num))
            return out

        out = []
        day = start
        while day <= end:
            hours = self.cfg.hours.get(day.weekday())
            if hours and day not in self.cfg.closed_dates:
                for seg_start, seg_end in _open_segments(day, hours):
                    out.extend((op_num, seg_start, seg_end) for op_num in appt_type.operatories)
            day += timedelta(days=1)
        return out

    def _to_option(self, cand: Candidate) -> Option:
        return Option(
            candidate=cand,
            spoken_time=spoken_datetime(cand.start),
            provider_name=self.cfg.providers[cand.busy_provider].spoken_name,
        )


def _open_segments(day: date, hours) -> list[tuple[datetime, datetime]]:
    segments = [(datetime.combine(day, hours.open), datetime.combine(day, hours.close))]
    for b_start, b_end in hours.breaks:
        bs, be = datetime.combine(day, b_start), datetime.combine(day, b_end)
        nxt = []
        for s, e in segments:
            if be <= s or bs >= e:
                nxt.append((s, e))
                continue
            if s < bs:
                nxt.append((s, bs))
            if be < e:
                nxt.append((be, e))
        segments = nxt
    return segments


def _matches_time_of_day(t: datetime, time_of_day: str) -> bool:
    if time_of_day == "morning":
        return t.time() < MORNING_END
    if time_of_day == "afternoon":
        return t.time() >= MORNING_END
    return True


def _spread(candidates: list[Candidate], limit: int) -> list[Candidate]:
    """Pick up to `limit` distinct start times, spread across days, then across the day."""
    by_time: dict[datetime, Candidate] = {}
    for c in sorted(candidates, key=lambda c: (c.start, c.op)):
        by_time.setdefault(c.start, c)  # one room per start time
    ordered = list(by_time.values())

    chosen: list[Candidate] = []
    seen_days: set[date] = set()
    for c in ordered:  # pass 1: earliest time on each distinct day
        if len(chosen) >= limit:
            break
        if c.start.date() not in seen_days:
            chosen.append(c)
            seen_days.add(c.start.date())
    for c in ordered:  # pass 2: fill with times >= 2h from anything chosen that day
        if len(chosen) >= limit:
            break
        if c in chosen:
            continue
        if all(abs(c.start - x.start) >= timedelta(hours=2) for x in chosen if x.start.date() == c.start.date()):
            chosen.append(c)
    for c in ordered:  # pass 3: anything left
        if len(chosen) >= limit:
            break
        if c not in chosen:
            chosen.append(c)
    return sorted(chosen, key=lambda c: c.start)


def spoken_datetime(dt: datetime) -> str:
    day = dt.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    hour = dt.hour % 12 or 12
    minutes = f":{dt.minute:02d}" if dt.minute else ""
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt:%A}, {dt:%B} {day}{suffix} at {hour}{minutes} {ampm}"
