"""Scheduling primitives shared by availability and booking: candidates and conflicts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..opendental.models import ODAppointment
from ..practice_config import AppointmentType, PracticeConfig


@dataclass(frozen=True)
class Candidate:
    type_key: str
    op: int
    prov_num: int      # Open Dental ProvNum (dentist; supervising dentist for hygiene)
    prov_hyg: int      # Open Dental ProvHyg (0 unless hygiene)
    is_hygiene: bool
    start: datetime    # practice-local, naive
    pattern: str

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=5 * len(self.pattern))

    @property
    def busy_provider(self) -> int:
        """The provider whose chair time this appointment consumes."""
        return self.prov_hyg if self.is_hygiene else self.prov_num


def candidate_for(cfg: PracticeConfig, appt_type: AppointmentType, op_num: int, start: datetime) -> Candidate:
    op = cfg.operatories[op_num]
    return Candidate(
        type_key=appt_type.key,
        op=op_num,
        prov_num=op.dentist or 0,
        prov_hyg=(op.hygienist or 0) if appt_type.is_hygiene else 0,
        is_hygiene=appt_type.is_hygiene,
        start=start,
        pattern=appt_type.pattern,
    )


def _busy_provider(a: ODAppointment) -> int:
    return a.prov_hyg if (a.is_hygiene and a.prov_hyg) else a.prov_num


def find_conflict(
    cand: Candidate, appointments: list[ODAppointment], exclude_apt_num: int | None = None
) -> ODAppointment | None:
    """Return an existing appointment that blocks the candidate, or None.

    Blocks if it overlaps in time AND (same operatory OR same busy provider).
    Open Dental does not document double-booking protection on POST /appointments,
    so this check is ours to make.
    """
    for a in appointments:
        if a.apt_num == exclude_apt_num or not a.occupies_schedule:
            continue
        if not (a.start < cand.end and cand.start < a.end):
            continue
        if a.op == cand.op:
            return a
        if cand.busy_provider and _busy_provider(a) == cand.busy_provider:
            return a
    return None
