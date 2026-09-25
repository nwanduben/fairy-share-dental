"""Canonical, minimal models the rest of the app uses.

The adapter translates raw Open Dental JSON into these and drops every other field
(e.g. provider SSN/license/NPI, patient balances), so nothing extra can leak to the agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

OD_DATETIME = "%Y-%m-%d %H:%M:%S"
OD_DATE = "%Y-%m-%d"

# Statuses that occupy chair time on the schedule. Planned / UnschedList appointments
# are not on the appointment book, so they never block a slot.
OCCUPYING_STATUSES = {"Scheduled", "Complete", "ASAP", "Broken", "PtNote", "PtNoteCompleted"}


class OpenDentalError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Open Dental API {status}: {message}")
        self.status = status
        self.message = message


def parse_od_datetime(value: str) -> datetime:
    return datetime.strptime(value, OD_DATETIME)


def parse_od_date(value: str | None) -> date | None:
    if not value or value.startswith("0001-01-01"):
        return None
    return datetime.strptime(value[:10], OD_DATE).date()


def _bool(value) -> bool:
    return str(value).lower() == "true"


@dataclass(frozen=True)
class ODOperatory:
    op_num: int
    name: str
    prov_dentist: int
    prov_hygienist: int
    is_hygiene: bool
    is_hidden: bool

    @classmethod
    def from_api(cls, d: dict) -> "ODOperatory":
        return cls(
            op_num=int(d["OperatoryNum"]),
            name=str(d.get("OpName", "")),
            prov_dentist=int(d.get("ProvDentist") or 0),
            prov_hygienist=int(d.get("ProvHygienist") or 0),
            is_hygiene=_bool(d.get("IsHygiene")),
            is_hidden=_bool(d.get("IsHidden")),
        )


@dataclass(frozen=True)
class ODProvider:
    prov_num: int
    abbr: str
    is_hidden: bool
    is_secondary: bool

    @classmethod
    def from_api(cls, d: dict) -> "ODProvider":
        return cls(
            prov_num=int(d["ProvNum"]),
            abbr=str(d.get("Abbr", "")),
            is_hidden=_bool(d.get("IsHidden")),
            is_secondary=_bool(d.get("IsSecondary")),
        )


@dataclass(frozen=True)
class ODPatient:
    pat_num: int
    first_name: str
    last_name: str
    preferred: str
    birthdate: date | None
    phones: tuple[str, ...]  # digits only
    status: str

    @classmethod
    def from_api(cls, d: dict) -> "ODPatient":
        phones = tuple(
            p for p in (_digits(d.get(k, "")) for k in ("HmPhone", "WkPhone", "WirelessPhone")) if p
        )
        return cls(
            pat_num=int(d["PatNum"]),
            first_name=str(d.get("FName", "")),
            last_name=str(d.get("LName", "")),
            preferred=str(d.get("Preferred", "")),
            birthdate=parse_od_date(d.get("Birthdate")),
            phones=phones,
            status=str(d.get("PatStatus", "")),
        )


@dataclass(frozen=True)
class ODAppointment:
    apt_num: int
    pat_num: int
    status: str
    start: datetime  # practice-local, naive (Open Dental stores no timezone)
    pattern: str
    op: int
    prov_num: int
    prov_hyg: int
    is_hygiene: bool
    note: str

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=5 * len(self.pattern))

    @property
    def occupies_schedule(self) -> bool:
        return self.status in OCCUPYING_STATUSES

    @classmethod
    def from_api(cls, d: dict) -> "ODAppointment":
        return cls(
            apt_num=int(d["AptNum"]),
            pat_num=int(d.get("PatNum") or 0),
            status=str(d.get("AptStatus", "")),
            start=parse_od_datetime(d["AptDateTime"]),
            pattern=str(d.get("Pattern") or "/XX/"),
            op=int(d.get("Op") or 0),
            prov_num=int(d.get("ProvNum") or 0),
            prov_hyg=int(d.get("ProvHyg") or 0),
            is_hygiene=_bool(d.get("IsHygiene")),
            note=str(d.get("Note") or ""),
        )


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def normalize_phone(value: str | None) -> str:
    """Digits only, US leading 1 removed."""
    digits = _digits(value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def to_e164(value: str | None, default_country_code: str = "1") -> str | None:
    """'(214) 555-0111' -> '+12145550111'. A 10-digit number takes the default
    country code; 11-15 digits is treated as already including one. None if invalid."""
    digits = _digits(value or "")
    if len(digits) == 10:
        return f"+{default_country_code}{digits}"
    if 11 <= len(digits) <= 15:
        return f"+{digits}"
    return None
