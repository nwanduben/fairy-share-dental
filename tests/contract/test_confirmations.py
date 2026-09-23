"""send_confirmation: only verified appointments, validated destinations, one send per channel."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.notifications import ConfirmationSender
from tests.conftest import FIXED_NOW, SECRET
from tests.contract.test_booking_flow import book_cleaning_for_avery, post


@pytest.fixture
def n8n():
    state = {"calls": [], "status": 200, "body": {"ok": True, "sent": True}}

    def handler(req: httpx.Request):
        state["calls"].append({"headers": dict(req.headers), "json": __import__("json").loads(req.content)})
        return httpx.Response(state["status"], json=state["body"])

    state["transport"] = httpx.MockTransport(handler)
    return state


@pytest.fixture
def client(settings, od, n8n):
    notifier = ConfirmationSender("https://n8n.test/webhook/fsd", "hook-secret", "999111", transport=n8n["transport"])
    app = create_app(settings, od, notifier=notifier)
    app.state.availability.now_local = lambda: FIXED_NOW
    with TestClient(app, headers={"X-Tool-Secret": SECRET}) as c:
        yield c


def booked_ref(client):
    _, _, created = book_cleaning_for_avery(client)
    assert post(client, "verify_appointment", {"appointment_ref": created["appointment_ref"]})["verified"]
    return created["appointment_ref"]


def test_email_confirmation_sent_with_safe_payload(client, n8n):
    ref = booked_ref(client)
    r = post(client, "send_confirmation", {"appointment_ref": ref, "channel": "email", "email": "Avery.Test@Example.com"})
    assert r["sent"] is True
    [call] = n8n["calls"]
    assert call["headers"]["x-fsd-secret"] == "hook-secret"
    body = call["json"]
    assert body["event"] == "appointment.verified" and body["to"] == "avery.test@example.com"
    assert body["patient_first_name"] == "Avery" and body["appointment_type"] == "Cleaning"
    assert body["practice_name"] == "Fairy Share Dental" and body["spoken_time"]
    for forbidden in ("date_of_birth", "1990-04-12", "Testpatient", "PatNum"):
        assert forbidden not in str(body)


def test_sms_normalizes_phone_and_telegram_uses_configured_chat(client, n8n):
    ref = booked_ref(client)
    assert post(client, "send_confirmation", {"appointment_ref": ref, "channel": "sms", "phone": "(214) 555-0101"})["sent"]
    assert post(client, "send_confirmation", {"appointment_ref": ref, "channel": "telegram"})["sent"]
    assert [c["json"]["to"] for c in n8n["calls"]] == ["+12145550101", "999111"]


def test_duplicate_send_is_suppressed(client, n8n):
    ref = booked_ref(client)
    for _ in range(2):
        assert post(client, "send_confirmation", {"appointment_ref": ref, "channel": "telegram"})["sent"]
    assert len(n8n["calls"]) == 1


def test_bad_destinations_rejected(client, n8n):
    ref = booked_ref(client)
    assert post(client, "send_confirmation", {"appointment_ref": ref, "channel": "email", "email": "avery at gmail"})["reason"] == "invalid_email"
    assert post(client, "send_confirmation", {"appointment_ref": ref, "channel": "sms", "phone": "555"})["reason"] == "invalid_phone"
    assert n8n["calls"] == []


def test_unverified_appointment_never_confirmed(client, od, n8n):
    _, _, created = book_cleaning_for_avery(client)
    od.appointments.clear()  # appointment vanished from Open Dental
    r = post(client, "send_confirmation", {"appointment_ref": created["appointment_ref"], "channel": "telegram"})
    assert r["sent"] is False and r["reason"] == "appointment_not_verified"
    assert n8n["calls"] == []


def test_delivery_failure_says_still_booked(client, n8n):
    n8n["status"] = 500
    ref = booked_ref(client)
    r = post(client, "send_confirmation", {"appointment_ref": ref, "channel": "telegram"})
    assert r["sent"] is False and "IS booked" in r["agent_instruction"]


def test_empty_200_from_n8n_is_not_treated_as_sent(client, n8n):
    n8n["body"] = None  # n8n answered 200 but the workflow never reached its Respond node
    ref = booked_ref(client)
    r = post(client, "send_confirmation", {"appointment_ref": ref, "channel": "telegram"})
    assert r["sent"] is False


def test_disabled_when_not_configured(settings, od):
    app = create_app(settings, od)
    app.state.availability.now_local = lambda: FIXED_NOW
    with TestClient(app, headers={"X-Tool-Secret": SECRET}) as c:
        ref = booked_ref(c)
        assert post(c, "send_confirmation", {"appointment_ref": ref, "channel": "telegram"})["reason"] == "confirmations_not_configured"


def test_demo_mode_routes_sms_to_telegram(settings, od, n8n):
    notifier = ConfirmationSender("https://n8n.test/webhook/fsd", "s", "999111",
                                  demo_sms_via_telegram=True, transport=n8n["transport"])
    app = create_app(settings, od, notifier=notifier)
    app.state.availability.now_local = lambda: FIXED_NOW
    with TestClient(app, headers={"X-Tool-Secret": SECRET}) as c:
        ref = booked_ref(c)
        r = post(c, "send_confirmation", {"appointment_ref": ref, "channel": "sms", "phone": "2145550111"})
        assert r["sent"] is True
        assert n8n["calls"][-1]["json"]["channel"] == "telegram"
        assert n8n["calls"][-1]["json"]["to"] == "999111"
