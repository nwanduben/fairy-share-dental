"""BOOK and VERIFY.

CHECK -> BOOK -> VERIFY -> CONFIRM
  * create() re-checks the slot against live Open Dental, then POSTs.
    It NEVER reports "booked" — only "created_pending_verification".
  * verify() reads the appointment back from Open Dental and compares every field
    we asked for. Only verified=True may be spoken to the caller as "booked".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..opendental.client import OpenDentalAPI
from ..opendental.models import OD_DATETIME, ODAppointment, OpenDentalError
from ..practice_config import PracticeConfig
from .availability import AvailabilityService, spoken_datetime
from .schedule import Candidate, find_conflict

log = logging.getLogger("fsd.booking")

NOTE_TAG = "[FSD-AI]"


@dataclass(frozen=True)
class CreateResult:
    status: str  # created_pending_verification | slot_taken | error
    appointment: ODAppointment | None = None
    already_existed: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class VerifyResult:
    verified: bool
    reason: str | None = None
    appointment: ODAppointment | None = None


def build_note(type_display: str, conversation_id: str | None) -> str:
    conv = f" conv={conversation_id}" if conversation_id else ""
    return f"{NOTE_TAG} {type_display} — booked by AI receptionist (demo).{conv}"


class BookingService:
    def __init__(self, cfg: PracticeConfig, od: OpenDentalAPI, availability: AvailabilityService):
        self.cfg = cfg
        self.od = od
        self.availability = availability

    async def create(
        self, cand: Candidate, pat_num: int, is_new_patient: bool, conversation_id: str | None
    ) -> CreateResult:
        appt_type = self.cfg.appointment_types[cand.type_key]
        day = cand.start.date()

        # Idempotency: if this exact appointment already exists (e.g. the tool call was
        # retried after a timeout), return it instead of booking twice.
        mine = await self.od.list_appointments(day, day, pat_num=pat_num)
        for a in mine:
            if a.status == "Scheduled" and a.start == cand.start and a.op == cand.op:
                log.info("idempotent hit apt=%s", a.apt_num)
                return CreateResult("created_pending_verification", a, already_existed=True)

        # CHECK (fresh, uncached): someone may have taken the slot since it was offered.
        if not await self.availability.is_still_free(cand):
            return CreateResult("slot_taken", reason="time no longer available")
        if cand.start < self.availability.now_local():
            return CreateResult("slot_taken", reason="time is in the past")

        payload = {
            "PatNum": pat_num,
            "Op": cand.op,
            "AptDateTime": cand.start.strftime(OD_DATETIME),
            "AptStatus": "Scheduled",
            "Pattern": cand.pattern,
            "ProvNum": cand.prov_num,
            "IsHygiene": "true" if cand.is_hygiene else "false",
            "IsNewPatient": "true" if is_new_patient else "false",
            "Note": build_note(appt_type.display_name, conversation_id),
        }
        if cand.is_hygiene and cand.prov_hyg:
            payload["ProvHyg"] = cand.prov_hyg
        # Only send AppointmentTypeNum when mapped; otherwise Open Dental would not know our types.
        # Pattern is always sent explicitly, so our configured duration wins.
        if appt_type.open_dental_appointment_type_num:
            payload["AppointmentTypeNum"] = appt_type.open_dental_appointment_type_num

        # BOOK
        try:
            apt = await self.od.create_appointment(payload)
        except OpenDentalError as exc:
            log.warning("create_appointment failed status=%s", exc.status)
            return CreateResult("error", reason=f"open_dental_{exc.status}")
        log.info("created apt=%s op=%s start=%s", apt.apt_num, apt.op, apt.start)
        return CreateResult("created_pending_verification", apt)

    async def verify(self, apt_num: int, expected: dict) -> VerifyResult:
        """VERIFY: read back from Open Dental and compare against what we booked."""
        try:
            apt = await self.od.get_appointment(apt_num)
        except OpenDentalError as exc:
            return VerifyResult(False, f"open_dental_{exc.status}")
        if apt is None:
            return VerifyResult(False, "appointment_not_found")

        checks = {
            "patient": apt.pat_num == expected["pat"],
            "status": apt.status == "Scheduled",
            "time": apt.start == datetime.strptime(expected["start"], OD_DATETIME),
            "operatory": apt.op == expected["op"],
            "length": len(apt.pattern) == expected["len"],
            "provider": apt.prov_num == expected["prov"],
        }
        failed = [k for k, ok in checks.items() if not ok]
        if failed:
            log.warning("verify mismatch apt=%s fields=%s", apt_num, failed)
            return VerifyResult(False, "mismatch:" + ",".join(failed), apt)

        # Post-write race check: if another booking landed in the same room/provider
        # at the same time, the older AptNum wins deterministically.
        day = apt.start.date()
        same_day = await self.od.list_appointments(day, day)
        cand = Candidate(expected["t"], apt.op, apt.prov_num, apt.prov_hyg, apt.is_hygiene, apt.start, apt.pattern)
        clash = find_conflict(cand, [a for a in same_day if a.apt_num < apt.apt_num], exclude_apt_num=apt.apt_num)
        if clash is not None:
            log.warning("verify double-booking detected apt=%s clashes_with=%s", apt_num, clash.apt_num)
            return VerifyResult(False, "double_booking_detected", apt)

        return VerifyResult(True, None, apt)

    def summary(self, apt: ODAppointment, type_key: str) -> dict:
        t = self.cfg.appointment_types[type_key]
        busy = apt.prov_hyg if (apt.is_hygiene and apt.prov_hyg) else apt.prov_num
        prov = self.cfg.providers.get(busy)
        return {
            "appointment_type": t.display_name,
            "spoken_time": spoken_datetime(apt.start),
            "date": apt.start.date().isoformat(),
            "time": apt.start.strftime("%H:%M"),
            "duration_minutes": len(apt.pattern) * 5,
            "provider_name": prov.spoken_name if prov else None,
        }
