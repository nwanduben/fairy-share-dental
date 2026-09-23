"""End-to-end tool flow against the mock Open Dental: CHECK -> BOOK -> VERIFY -> CONFIRM."""
import json
from datetime import datetime

from fastapi.testclient import TestClient

from app.opendental.models import OpenDentalError

AVERY = {"first_name": "Avery", "last_name": "Testpatient", "date_of_birth": "1990-04-12"}
NEXT_WEEK = {"date_from": "2026-09-28", "date_to": "2026-10-02"}


def post(client, tool, body):
    r = client.post(f"/tools/{tool}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def book_cleaning_for_avery(client):
    pat = post(client, "lookup_patient", AVERY)
    assert pat["status"] == "found"
    avail = post(client, "get_availability", {"appointment_type": "cleaning", **NEXT_WEEK})
    slot = avail["options"][0]
    created = post(client, "create_appointment", {"slot_id": slot["slot_id"], "patient_ref": pat["patient_ref"], "conversation_id": "conv-1"})
    return pat, slot, created


def test_requires_tool_secret(app):
    with TestClient(app) as c:
        assert c.post("/tools/lookup_patient", json=AVERY).status_code == 401
        assert c.post("/tools/lookup_patient", json=AVERY, headers={"X-Tool-Secret": "wrong"}).status_code == 401


def test_existing_patient_books_cleaning_and_verifies(client, od):
    pat, slot, created = book_cleaning_for_avery(client)
    assert created["status"] == "created_pending_verification"
    assert "NOT CONFIRMED" in created["agent_instruction"]

    verified = post(client, "verify_appointment", {"appointment_ref": created["appointment_ref"]})
    assert verified["verified"] is True
    assert verified["appointment"]["spoken_time"] == slot["spoken_time"]
    assert verified["appointment"]["duration_minutes"] == 60

    [apt] = od.appointments.values()
    assert apt.op in (5, 6) and apt.is_hygiene and apt.prov_hyg in (2, 4)
    assert apt.pattern == "/XXXXXXXXXX/"
    assert apt.note.startswith("[FSD-AI] Cleaning")


def test_verify_fails_if_appointment_not_in_open_dental(client, od):
    od.drop_created_appointments = True
    _, _, created = book_cleaning_for_avery(client)
    verified = post(client, "verify_appointment", {"appointment_ref": created["appointment_ref"]})
    assert verified["verified"] is False
    assert "Do NOT say" in verified["agent_instruction"]


def test_verify_fails_on_field_mismatch(client, od):
    _, _, created = book_cleaning_for_avery(client)
    [apt] = od.appointments.values()
    od.appointments[apt.apt_num] = apt.__class__(**{**apt.__dict__, "start": datetime(2026, 9, 28, 15, 0)})
    verified = post(client, "verify_appointment", {"appointment_ref": created["appointment_ref"]})
    assert verified["verified"] is False and "time" in verified["reason"]


def test_slot_taken_between_offer_and_booking(client, od):
    pat = post(client, "lookup_patient", AVERY)
    slot = post(client, "get_availability", {"appointment_type": "emergency_exam", **NEXT_WEEK})["options"][0]
    # Someone else grabs every dentist room at that time
    start = datetime.fromisoformat("2026-09-28T08:00:00")
    for op, prov in ((1, 1), (2, 3)):
        od.add_appointment(start=start, op=op, prov_num=prov, pattern="X" * 6)
    created = post(client, "create_appointment", {"slot_id": slot["slot_id"], "patient_ref": pat["patient_ref"]})
    assert created["status"] == "slot_taken" and created["ok"] is False
    assert len([a for a in od.appointments.values() if a.pat_num]) == 0


def test_retry_is_idempotent(client, od):
    pat, slot, first = book_cleaning_for_avery(client)
    again = post(client, "create_appointment", {"slot_id": slot["slot_id"], "patient_ref": pat["patient_ref"]})
    assert again["status"] == "created_pending_verification"
    assert len(od.appointments) == 1


def test_new_patient_is_created_once_and_booked(client, od):
    before = len(od.patients)
    new = {"first_name": "Casey", "last_name": "Fsdtest", "date_of_birth": "1995-06-01", "phone": "(214) 555-0177"}
    assert post(client, "lookup_patient", new)["status"] == "not_found"
    slot = post(client, "get_availability", {"appointment_type": "new_patient_exam", "is_new_patient": True, **NEXT_WEEK})["options"][0]
    created = post(client, "create_appointment", {"slot_id": slot["slot_id"], "new_patient": new})
    assert created["status"] == "created_pending_verification"
    assert post(client, "verify_appointment", {"appointment_ref": created["appointment_ref"]})["verified"] is True
    assert len(od.patients) == before + 1
    # Caller calls back and books again as "new" -> matched, not duplicated
    slot2 = post(client, "get_availability", {"appointment_type": "cleaning", **NEXT_WEEK})["options"][1]
    post(client, "create_appointment", {"slot_id": slot2["slot_id"], "new_patient": new})
    assert len(od.patients) == before + 1


def test_new_patient_exam_not_offered_to_existing_patient(client):
    r = post(client, "get_availability", {"appointment_type": "new_patient_exam", **NEXT_WEEK})
    assert r["ok"] is False and r["error"] == "type_for_new_patients_only"


def test_multiple_matches_resolved_by_phone(client):
    riley = {"first_name": "Riley", "last_name": "Sampleton", "date_of_birth": "1978-01-30"}
    assert post(client, "lookup_patient", riley)["status"] == "multiple"
    assert post(client, "lookup_patient", {**riley, "phone": "+1 469-555-0199"})["status"] == "found"


def test_tampered_or_invented_slot_rejected(client):
    pat = post(client, "lookup_patient", AVERY)
    r = post(client, "create_appointment", {"slot_id": "slot_made_up.abc", "patient_ref": pat["patient_ref"]})
    assert r["ok"] is False and r["error"] == "invalid_slot_id"


def test_open_dental_outage_never_claims_booking(client, od):
    od.fail_create_appointment = OpenDentalError(503, "down")
    _, _, created = book_cleaning_for_avery(client)
    assert created["ok"] is False and "appointment_ref" not in created
    assert "do NOT say" in created["agent_instruction"]


def test_no_raw_ids_or_sensitive_fields_leak(client):
    pat, _, created = book_cleaning_for_avery(client)
    verified = post(client, "verify_appointment", {"appointment_ref": created["appointment_ref"]})
    blob = json.dumps([pat, created, verified])
    for forbidden in ("PatNum", "AptNum", "SSN", "Birthdate", "1990-04-12", "2145550101"):
        assert forbidden not in blob


def test_get_appointments_lists_upcoming(client):
    pat, _, created = book_cleaning_for_avery(client)
    r = post(client, "get_appointments", {"patient_ref": pat["patient_ref"]})
    assert r["upcoming_appointments"][0]["appointment_type"] == "Cleaning"
