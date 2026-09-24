"""The /schedule staff viewer: password gate, read-only, shows Open Dental data."""
from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FIXED_NOW, SECRET

PASSWORD = "let-me-in-please"


@pytest.fixture
def app(settings, od):
    app = create_app(replace(settings, admin_password=PASSWORD), od)
    app.state.availability.now_local = lambda: FIXED_NOW
    od.add_appointment(
        start=datetime(2026, 9, 22, 10, 30), op=5, pattern="/XXXXXXXXXX/", prov_num=1, prov_hyg=2,
        is_hygiene=True, pat_num=1001,
        note="[FSD-AI] Cleaning — booked by AI receptionist (demo). conv=abc",
    )
    od.add_appointment(start=datetime(2026, 9, 22, 14, 0), op=1, pattern="/XXXX/", prov_num=1, pat_num=1002)
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def login(client):
    r = client.post("/schedule/login", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303
    return r


def test_password_required(client):
    body = client.get("/schedule").text
    assert "Staff schedule viewer" in body and "Cleaning" not in body


def test_wrong_password_rejected(client):
    r = client.post("/schedule/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 401 and "was not recognized" in r.text
    assert "Cleaning" not in client.get("/schedule").text


def test_shows_appointments_after_login(client):
    login(client)
    body = client.get("/schedule").text
    assert "Cleaning" in body and "Avery Testpatient" in body
    assert "10:30 AM–11:30 AM" in body and "60 min" in body
    assert "Tina, our hygienist" in body and "booked by Joy" in body
    assert "booked in practice" in body  # the non-AI appointment
    assert "AptNum" in body


def test_day_range_and_navigation(client):
    login(client)
    assert "Nothing booked" in client.get("/schedule?date_from=2026-12-01&days=1").text
    body = client.get("/schedule?date_from=2026-09-22&days=1").text
    assert "Cleaning" in body and "Earlier" in body and "Later" in body


def test_logout_clears_session(client):
    login(client)
    client.get("/schedule/logout", follow_redirects=False)
    assert "Staff schedule viewer" in client.get("/schedule").text


def test_tampered_cookie_rejected(client):
    login(client)
    client.cookies.set("fsd_schedule", "adm_tampered.signature")
    assert "Staff schedule viewer" in client.get("/schedule").text


def test_disabled_without_password(settings, od):
    with TestClient(create_app(settings, od)) as c:
        r = c.get("/schedule")
        assert r.status_code == 404 and "disabled" in r.text


def test_page_never_writes(client, od):
    login(client)
    client.get("/schedule")
    assert not any(c.startswith("POST") for c in od.calls)


def test_empty_password_shows_form_not_json_error(client):
    r = client.post("/schedule/login", data={"password": "  "}, follow_redirects=False)
    assert r.status_code == 400 and "Please enter the password" in r.text


def test_password_is_trimmed_on_both_sides(settings, od):
    app = create_app(replace(settings, admin_password=f"  {PASSWORD}  "), od)
    app.state.availability.now_local = lambda: FIXED_NOW
    with TestClient(app) as c:
        assert c.post("/schedule/login", data={"password": PASSWORD}, follow_redirects=False).status_code == 303
