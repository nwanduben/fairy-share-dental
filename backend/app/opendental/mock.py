"""In-memory Open Dental stand-in with the same interface as LiveOpenDentalClient.

Seeded with the operatories/providers observed in the Open Dental test DB plus
SYNTHETIC patients. Used for tests and offline demos (OD_MODE=mock).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from .models import (
    ODAppointment,
    ODOperatory,
    ODPatient,
    ODProvider,
    OpenDentalError,
    normalize_phone,
    parse_od_datetime,
)


class MockOpenDentalClient:
    def __init__(self, seed: bool = True):
        self.operatories = [
            ODOperatory(1, "Dr. Brian Albert", 1, 0, False, False),
            ODOperatory(2, "Dr. Sarah Lexington", 3, 0, False, False),
            ODOperatory(3, "Doctor Overflow", 0, 0, False, False),
            ODOperatory(4, "Operatory 4", 0, 0, False, True),
            ODOperatory(5, "Tina", 1, 2, True, False),
            ODOperatory(6, "Bruce", 3, 4, True, False),
        ]
        self.providers = [
            ODProvider(1, "DOC1", False, False),
            ODProvider(2, "HYG1", False, True),
            ODProvider(3, "DOC2", False, False),
            ODProvider(4, "HYG2", False, True),
        ]
        self.patients: dict[int, ODPatient] = {}
        self.appointments: dict[int, ODAppointment] = {}
        self._next_pat = 1000
        self._next_apt = 5000
        self.calls: list[str] = []
        # Test hooks
        self.before_create_appointment: Callable[[dict], None] | None = None
        self.fail_create_appointment: OpenDentalError | None = None
        self.drop_created_appointments = False  # simulate "POST said OK but it isn't there"
        if seed:
            self._seed()

    def _seed(self) -> None:
        # Synthetic, obviously fake patients.
        self.add_patient("Testpatient", "Avery", date(1990, 4, 12), "2145550101")
        self.add_patient("Testpatient", "Jordan", date(1985, 11, 3), "2145550102")
        self.add_patient("Sampleton", "Riley", date(1978, 1, 30), "2145550103")
        self.add_patient("Sampleton", "Riley", date(1978, 1, 30), "4695550199")  # duplicate-name case

    async def aclose(self) -> None:
        return None

    # ---------- helpers for tests ----------
    def add_patient(self, last, first, birthdate, phone=None) -> ODPatient:
        self._next_pat += 1
        p = ODPatient(self._next_pat, first, last, "", birthdate, (normalize_phone(phone),) if phone else (), "Patient")
        self.patients[p.pat_num] = p
        return p

    def add_appointment(self, **kw) -> ODAppointment:
        self._next_apt += 1
        apt = ODAppointment(
            apt_num=self._next_apt,
            pat_num=kw.get("pat_num", 0),
            status=kw.get("status", "Scheduled"),
            start=kw["start"],
            pattern=kw.get("pattern", "/XX/"),
            op=kw["op"],
            prov_num=kw.get("prov_num", 0),
            prov_hyg=kw.get("prov_hyg", 0),
            is_hygiene=kw.get("is_hygiene", False),
            note=kw.get("note", ""),
        )
        self.appointments[apt.apt_num] = apt
        return apt

    # ---------- interface ----------
    async def list_operatories(self):
        self.calls.append("GET /operatories")
        return list(self.operatories)

    async def list_providers(self):
        self.calls.append("GET /providers")
        return list(self.providers)

    async def search_patients(self, last_name, first_name, birthdate):
        self.calls.append("GET /patients/Simple")
        ln, fn = last_name.lower(), first_name.lower()
        return [
            p for p in self.patients.values()
            if p.last_name.lower().startswith(ln)
            and p.first_name.lower().startswith(fn)
            and (birthdate is None or p.birthdate == birthdate)
        ]

    async def create_patient(self, last_name, first_name, birthdate, phone):
        self.calls.append("POST /patients")
        return self.add_patient(last_name, first_name, birthdate, phone)

    async def list_appointments(self, date_start, date_end, pat_num=None):
        self.calls.append("GET /appointments")
        return [
            a for a in self.appointments.values()
            if date_start <= a.start.date() <= date_end and (pat_num is None or a.pat_num == pat_num)
        ]

    async def get_appointment(self, apt_num):
        self.calls.append("GET /appointments/{AptNum}")
        return self.appointments.get(int(apt_num))

    async def create_appointment(self, payload: dict[str, Any]):
        self.calls.append("POST /appointments")
        if self.before_create_appointment:
            self.before_create_appointment(payload)
        if self.fail_create_appointment:
            raise self.fail_create_appointment
        if int(payload["PatNum"]) not in self.patients:
            raise OpenDentalError(400, "PatNum is invalid")
        apt = self.add_appointment(
            pat_num=int(payload["PatNum"]),
            status=payload.get("AptStatus", "Scheduled"),
            start=parse_od_datetime(payload["AptDateTime"]),
            pattern=payload.get("Pattern", "/XX/"),
            op=int(payload["Op"]),
            prov_num=int(payload.get("ProvNum", 0)),
            prov_hyg=int(payload.get("ProvHyg", 0)),
            is_hygiene=str(payload.get("IsHygiene", "false")).lower() == "true",
            note=payload.get("Note", ""),
        )
        if self.drop_created_appointments:
            del self.appointments[apt.apt_num]
        return apt

    async def get_slots(self, date_start, date_end, prov_num, op_num, length_minutes):
        self.calls.append("GET /appointments/Slots")
        return []  # mirrors the test DB: no provider schedules
