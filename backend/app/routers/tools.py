"""ElevenLabs webhook tools.

Every response is HTTP 200 JSON with:
  ok                 - did the tool itself succeed
  agent_instruction  - what the agent must (and must not) say next
The agent never receives raw Open Dental IDs or fields beyond what it needs.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from ..opendental.models import OD_DATETIME, OpenDentalError, normalize_phone, to_e164
from ..refs import InvalidRef
from ..services.notifications import EMAIL_RE
from ..security import require_tool_secret
from ..services.availability import AvailabilityService
from ..services.booking import NOTE_TAG, BookingService
from ..services.patients import AmbiguousPatient, PatientService
from ..services.schedule import Candidate

log = logging.getLogger("fsd.tools")
router = APIRouter(prefix="/tools", dependencies=[Depends(require_tool_secret)])

SYSTEM_TROUBLE = (
    "The scheduling system is not responding. Apologize briefly, do NOT say anything is booked, "
    "and offer to have a team member call them back."
)


# ---------- request models ----------
def _parse_date(v: str | None) -> date | None:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError as exc:
        raise ValueError("dates must be YYYY-MM-DD") from exc


class PatientIdentity(BaseModel):
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(min_length=1, max_length=60)
    date_of_birth: date
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _dob(cls, v):
        d = _parse_date(v)
        if d is None or d >= date.today() or d.year < 1900:
            raise ValueError("date_of_birth must be a real past date in YYYY-MM-DD")
        return d


class LookupPatientRequest(PatientIdentity):
    conversation_id: str | None = None


class GetAppointmentsRequest(BaseModel):
    patient_ref: str
    conversation_id: str | None = None


class GetAvailabilityRequest(BaseModel):
    appointment_type: str
    date_from: date | None = None
    date_to: date | None = None
    time_of_day: Literal["morning", "afternoon", "any"] = "any"
    is_new_patient: bool = False
    conversation_id: str | None = None

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def _dates(cls, v):
        return _parse_date(v)


class CreateAppointmentRequest(BaseModel):
    slot_id: str
    patient_ref: str | None = None
    new_patient: PatientIdentity | None = None
    conversation_id: str | None = None


class VerifyAppointmentRequest(BaseModel):
    appointment_ref: str
    conversation_id: str | None = None


class SendConfirmationRequest(BaseModel):
    appointment_ref: str
    channel: Literal["email", "sms", "whatsapp", "telegram"]
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    conversation_id: str | None = None


# ---------- dependencies ----------
def _svc(request: Request):
    s = request.app.state
    return s.cfg, s.signer, s.patients, s.availability, s.booking


# ---------- tools ----------
@router.post("/lookup_patient")
async def lookup_patient(body: LookupPatientRequest, request: Request):
    cfg, signer, patients, _, _ = _svc(request)
    try:
        res = await patients.lookup(body.first_name, body.last_name, body.date_of_birth, body.phone)
    except OpenDentalError:
        return _trouble("lookup_patient", body.conversation_id)
    _log("lookup_patient", body.conversation_id, res.status)
    if res.status == "found":
        return {
            "ok": True,
            "status": "found",
            "patient_ref": signer.sign("pat", {"p": res.patient.pat_num, "new": False, "fn": body.first_name.strip().title()}),
            "agent_instruction": "Existing patient confirmed. Continue booking. Do not read back any record details.",
        }
    if res.status == "multiple":
        return {
            "ok": True,
            "status": "multiple",
            "agent_instruction": "More than one record matches. Ask for the phone number on file and call lookup_patient again with it.",
        }
    return {
        "ok": True,
        "status": "not_found",
        "agent_instruction": (
            "No record found. Double-check the spelling and date of birth once. If still not found, "
            "treat them as a new patient and collect first name, last name, date of birth and a phone number."
        ),
    }


@router.post("/get_appointments")
async def get_appointments(body: GetAppointmentsRequest, request: Request):
    cfg, signer, _, availability, _ = _svc(request)
    try:
        pat_num = signer.verify("pat", body.patient_ref)["p"]
    except InvalidRef:
        return _bad_ref("patient_ref")
    now = availability.now_local()
    try:
        appts = await request.app.state.od.list_appointments(now.date(), now.date() + timedelta(days=365), pat_num=pat_num)
    except OpenDentalError:
        return _trouble("get_appointments", body.conversation_id)
    upcoming = sorted((a for a in appts if a.status == "Scheduled" and a.start >= now), key=lambda a: a.start)
    items = []
    for a in upcoming[:5]:
        type_name = next(
            (t.display_name for t in cfg.appointment_types.values() if f"{NOTE_TAG} {t.display_name}" in a.note),
            "appointment",
        )
        items.append({"appointment_type": type_name, "spoken_time": _spoken(a.start)})
    _log("get_appointments", body.conversation_id, f"count={len(items)}")
    return {
        "ok": True,
        "upcoming_appointments": items,
        "agent_instruction": (
            "Mention any upcoming appointment of the same type before booking another one."
            if items else "No upcoming appointments."
        ),
    }


@router.post("/get_availability")
async def get_availability(body: GetAvailabilityRequest, request: Request):
    cfg, signer, _, availability, _ = _svc(request)
    appt_type = cfg.appointment_types.get(body.appointment_type)
    if appt_type is None:
        return {
            "ok": False,
            "error": "unknown_appointment_type",
            "valid_types": list(cfg.appointment_types),
            "agent_instruction": "Pick the closest valid appointment_type and call again. Do not mention this error.",
        }
    if appt_type.new_patients_only and not body.is_new_patient:
        return {
            "ok": False,
            "error": "type_for_new_patients_only",
            "agent_instruction": "New Patient Exams are for new patients. For existing patients offer a cleaning or a general consultation.",
        }
    now = availability.now_local()
    start, end = availability.clamp_range(body.date_from, body.date_to, now)
    try:
        options = await availability.find_options(appt_type, body.date_from, body.date_to, body.time_of_day, now)
    except OpenDentalError:
        return _trouble("get_availability", body.conversation_id)
    ttl = cfg.rules.slot_offer_ttl_minutes * 60
    out = [
        {
            "slot_id": signer.sign("slot", _cand_to_dict(o.candidate), ttl_seconds=ttl),
            "spoken_time": o.spoken_time,
            "provider_name": o.provider_name,
        }
        for o in options
    ]
    _log("get_availability", body.conversation_id, f"type={appt_type.key} options={len(out)}")
    return {
        "ok": True,
        "appointment_type": appt_type.display_name,
        "duration_minutes": appt_type.duration_minutes,
        "searched_from": start.isoformat(),
        "searched_to": end.isoformat(),
        "options": out,
        "agent_instruction": (
            "Offer these exact times only (never invent others). These are NOT booked or held."
            if out else "No openings in that range. Offer to search a different week or time of day."
        ),
    }


@router.post("/create_appointment")
async def create_appointment(body: CreateAppointmentRequest, request: Request):
    cfg, signer, patients, _, booking = _svc(request)
    try:
        cand = _cand_from_dict(signer.verify("slot", body.slot_id))
    except InvalidRef as exc:
        if "expired" in str(exc):
            return {
                "ok": False,
                "status": "slot_expired",
                "agent_instruction": "That time offer expired. Call get_availability again and re-offer times. Do NOT say it is booked.",
            }
        return _bad_ref("slot_id")

    # Resolve the patient
    created_patient = False
    if body.patient_ref:
        try:
            pref = signer.verify("pat", body.patient_ref)
        except InvalidRef:
            return _bad_ref("patient_ref")
        pat_num, is_new, first_name = pref["p"], bool(pref.get("new")), pref.get("fn", "")
    elif body.new_patient:
        np = body.new_patient
        try:
            patient, created_patient = await patients.find_or_create(np.first_name, np.last_name, np.date_of_birth, np.phone)
        except AmbiguousPatient:
            return {
                "ok": False,
                "status": "patient_ambiguous",
                "agent_instruction": "Could not uniquely identify the patient. Ask for the phone number on file, call lookup_patient, then retry.",
            }
        except OpenDentalError:
            return _trouble("create_appointment", body.conversation_id)
        pat_num, is_new, first_name = patient.pat_num, created_patient, np.first_name.strip().title()
    else:
        return {
            "ok": False,
            "status": "missing_patient",
            "agent_instruction": "Provide patient_ref (existing patient) or new_patient details.",
        }

    appt_type = cfg.appointment_types[cand.type_key]
    if appt_type.new_patients_only and not is_new:
        return {
            "ok": False,
            "status": "type_for_new_patients_only",
            "agent_instruction": "This caller already has a record. Offer a cleaning or general consultation instead.",
        }

    try:
        result = await booking.create(cand, pat_num, is_new, body.conversation_id)
    except OpenDentalError:
        return _trouble("create_appointment", body.conversation_id)
    _log("create_appointment", body.conversation_id, result.status)

    resp: dict = {"ok": result.status == "created_pending_verification", "status": result.status}
    if created_patient or body.new_patient:
        resp["patient_ref"] = signer.sign("pat", {"p": pat_num, "new": is_new, "fn": first_name})
    if result.status == "created_pending_verification":
        apt = result.appointment
        resp["appointment_ref"] = signer.sign("apt", {
            "a": apt.apt_num, "pat": pat_num, "start": cand.start.strftime(OD_DATETIME),
            "op": cand.op, "len": len(cand.pattern), "prov": cand.prov_num, "t": cand.type_key, "fn": first_name,
        })
        resp["agent_instruction"] = (
            "NOT CONFIRMED YET. Call verify_appointment with appointment_ref now. "
            "Do not tell the caller they are booked until verify_appointment returns verified=true."
        )
    elif result.status == "slot_taken":
        resp["agent_instruction"] = "That time was just taken. Apologize, call get_availability again and offer new times."
    else:
        resp["agent_instruction"] = SYSTEM_TROUBLE
    return resp


@router.post("/verify_appointment")
async def verify_appointment(body: VerifyAppointmentRequest, request: Request):
    cfg, signer, _, _, booking = _svc(request)
    try:
        expected = signer.verify("apt", body.appointment_ref)
    except InvalidRef:
        return _bad_ref("appointment_ref")
    res = await booking.verify(expected["a"], expected)
    _log("verify_appointment", body.conversation_id, f"verified={res.verified} reason={res.reason}")
    if res.verified:
        return {
            "ok": True,
            "verified": True,
            "appointment": booking.summary(res.appointment, expected["t"]),
            "agent_instruction": "Verified in the practice schedule. You may now confirm the booking to the caller using these exact details.",
        }
    return {
        "ok": True,
        "verified": False,
        "reason": res.reason,
        "agent_instruction": (
            "NOT verified. Do NOT say the appointment is booked. Apologize, explain you could not confirm "
            "that time, and offer other times (call get_availability) or a call back from the team."
        ),
    }


@router.post("/send_confirmation")
async def send_confirmation(body: SendConfirmationRequest, request: Request):
    cfg, signer, _, _, booking = _svc(request)
    notifier = request.app.state.notifier
    try:
        expected = signer.verify("apt", body.appointment_ref)
    except InvalidRef:
        return _bad_ref("appointment_ref")

    if not notifier.enabled:
        return {"ok": False, "sent": False, "reason": "confirmations_not_configured",
                "agent_instruction": "Confirmations are unavailable right now. The appointment is still booked. Let the caller know the office will follow up."}

    # Destination validation
    if body.channel == "email":
        destination = (body.email or "").strip().lower()
        if not EMAIL_RE.match(destination):
            return {"ok": False, "sent": False, "reason": "invalid_email",
                    "agent_instruction": "That email address doesn't look complete. Ask the caller to spell it again, then retry."}
    elif body.channel == "sms":
        digits = normalize_phone(body.phone)
        if len(digits) != 10:
            return {"ok": False, "sent": False, "reason": "invalid_phone",
                    "agent_instruction": "Ask for a 10-digit US mobile number, then retry."}
        destination = f"+1{digits}"
        if notifier.demo_sms_via_telegram and notifier.telegram_chat_id:
            # Demo mode: no Twilio account, so the "text" is delivered to the demo Telegram chat.
            _log("send_confirmation", body.conversation_id, "sms_routed_to_telegram_demo")
            body = body.model_copy(update={"channel": "telegram"})
            destination = notifier.telegram_chat_id
    elif body.channel == "whatsapp":
        # WhatsApp reaches any country, so accept international numbers.
        destination = to_e164(body.phone) or (notifier.whatsapp_number or "")
        if not destination:
            return {"ok": False, "sent": False, "reason": "invalid_phone",
                    "agent_instruction": "Ask for the WhatsApp number including the country code, then retry."}
    else:  # telegram
        destination = notifier.telegram_chat_id
        if not destination:
            return {"ok": False, "sent": False, "reason": "telegram_not_configured",
                    "agent_instruction": "Offer an email or text confirmation instead."}

    # Only ever confirm what Open Dental confirms right now.
    res = await booking.verify(expected["a"], expected)
    if not res.verified:
        _log("send_confirmation", body.conversation_id, f"blocked_unverified reason={res.reason}")
        return {"ok": False, "sent": False, "reason": "appointment_not_verified",
                "agent_instruction": "Do NOT send or promise a confirmation. The appointment could not be verified. Offer a call back from the team."}

    details = {
        "practice_name": cfg.name,
        "practice_phone": cfg.phone_display,
        "practice_city": cfg.city,
        "patient_first_name": expected.get("fn") or "there",
        **booking.summary(res.appointment, expected["t"]),
    }
    result = await notifier.send(expected["a"], body.channel, destination, details, body.conversation_id)
    _log("send_confirmation", body.conversation_id, f"channel={body.channel} sent={result.sent} reason={result.reason}")
    if result.sent:
        where = {"email": "by email", "sms": "by text", "whatsapp": "on WhatsApp",
                 "telegram": "on Telegram"}[body.channel]
        return {"ok": True, "sent": True, "channel": body.channel,
                "agent_instruction": f"Tell the caller their confirmation is on its way {where}."}
    return {"ok": False, "sent": False, "reason": result.reason,
            "agent_instruction": "The appointment IS booked, but the confirmation message could not be sent. Apologize briefly and say the office will follow up."}


# ---------- helpers ----------
def _cand_to_dict(c: Candidate) -> dict:
    return {"t": c.type_key, "o": c.op, "p": c.prov_num, "h": c.prov_hyg, "y": int(c.is_hygiene),
            "s": c.start.strftime(OD_DATETIME), "pt": c.pattern}


def _cand_from_dict(d: dict) -> Candidate:
    return Candidate(d["t"], d["o"], d["p"], d["h"], bool(d["y"]), datetime.strptime(d["s"], OD_DATETIME), d["pt"])


def _spoken(dt: datetime) -> str:
    from ..services.availability import spoken_datetime
    return spoken_datetime(dt)


def _bad_ref(name: str) -> dict:
    return {"ok": False, "error": f"invalid_{name}",
            "agent_instruction": f"The {name} was not recognized. Use only values returned by previous tool calls, verbatim."}


def _trouble(tool: str, conversation_id: str | None) -> dict:
    _log(tool, conversation_id, "open_dental_error")
    return {"ok": False, "error": "scheduling_system_unavailable", "agent_instruction": SYSTEM_TROUBLE}


def _log(tool: str, conversation_id: str | None, outcome: str) -> None:
    # No names, DOBs or phone numbers in logs.
    log.info("tool=%s conv=%s outcome=%s", tool, conversation_id or "-", outcome)
