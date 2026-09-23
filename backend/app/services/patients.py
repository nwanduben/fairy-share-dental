"""Patient identification. Matches on exact first name + last name + date of birth.

Privacy rule: never return details of a patient the caller has not already proven
(name + DOB). The agent only ever receives a match status and an opaque patient_ref.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..opendental.client import OpenDentalAPI
from ..opendental.models import ODPatient, normalize_phone

EXCLUDED_STATUSES = {"Deleted", "Archived", "Deceased"}


@dataclass(frozen=True)
class LookupResult:
    status: str  # found | not_found | multiple
    patient: ODPatient | None = None


def _norm(s: str) -> str:
    return " ".join(s.strip().lower().replace("-", " ").split())


class PatientService:
    def __init__(self, od: OpenDentalAPI):
        self.od = od

    async def lookup(self, first_name: str, last_name: str, dob: date, phone: str | None = None) -> LookupResult:
        results = await self.od.search_patients(last_name.strip(), first_name.strip(), dob)
        fn, ln = _norm(first_name), _norm(last_name)
        matches = [
            p for p in results
            if p.status not in EXCLUDED_STATUSES
            and p.birthdate == dob
            and _norm(p.last_name) == ln
            and fn in {_norm(p.first_name), _norm(p.preferred)}
        ]
        if len(matches) > 1 and phone:
            digits = normalize_phone(phone)
            by_phone = [p for p in matches if digits and digits in {normalize_phone(x) for x in p.phones}]
            if by_phone:
                matches = by_phone
        if not matches:
            return LookupResult("not_found")
        if len(matches) > 1:
            return LookupResult("multiple")
        return LookupResult("found", matches[0])

    async def find_or_create(self, first_name: str, last_name: str, dob: date, phone: str | None) -> tuple[ODPatient, bool]:
        """Returns (patient, created). Reuses an exact match so we never create duplicates."""
        existing = await self.lookup(first_name, last_name, dob, phone)
        if existing.status == "found":
            return existing.patient, False
        if existing.status == "multiple":
            raise AmbiguousPatient()
        created = await self.od.create_patient(
            last_name.strip().title(), first_name.strip().title(), dob, normalize_phone(phone) or None
        )
        return created, True


class AmbiguousPatient(Exception):
    pass
