"""Get everything ready for a demo or a recording. Run this first, every time.

    python backend/scripts/demo_reset.py            # seed patients + book a few appointments
    python backend/scripts/demo_reset.py --via https://your-backend   # go through the hosted backend
    python backend/scripts/demo_reset.py --no-book  # seed patients only
    python backend/scripts/demo_reset.py --wake https://fairy-share-dental-2.onrender.com

The Open Dental test database is wiped nightly, so the demo patients and every
appointment disappear overnight. This recreates them, books a small day's worth
of appointments through the real check -> book -> verify path, and prints the
schedule so you can see what the page will show.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import build_od_client  # noqa: E402
from app.opendental.models import OD_DATETIME  # noqa: E402
from app.practice_config import load_config  # noqa: E402
from app.services.availability import AvailabilityService  # noqa: E402
from app.services.booking import BookingService  # noqa: E402
from app.services.patients import PatientService  # noqa: E402
from app.settings import Settings  # noqa: E402
from seed_demo_patients import DEMO_PATIENTS, card  # noqa: E402

# (patient index, appointment type, "morning"|"afternoon"|"any", days ahead)
DEMO_BOOKINGS = [
    (0, "cleaning", "morning", 0),               # Benjamin Johnson, today
    (1, "general_consultation", "afternoon", 0),  # Sarah Mitchell, today
    (2, "cleaning", "morning", 1),               # Marcus Reed, tomorrow
]


def via_backend(base: str, tool_secret: str, book: bool) -> int:
    """Do everything through the deployed tools API — useful when this machine
    cannot reach Open Dental directly but the hosted backend can."""
    base = base.rstrip("/")
    http = httpx.Client(base_url=base, headers={"X-Tool-Secret": tool_secret}, timeout=90)

    def call(tool: str, body: dict) -> dict:
        r = http.post(f"/tools/{tool}", json=body)
        r.raise_for_status()
        return r.json()

    print(f"== Through {base}")
    print(" ", call("get_availability", {"appointment_type": "cleaning"}).get("ok") and "backend healthy")
    if not book:
        return 0
    print("\n== Demo patients and appointments")
    for idx, type_key, when, days_ahead in DEMO_BOOKINGS:
        first, last, dob, phone = DEMO_PATIENTS[idx]
        avail = call("get_availability", {"appointment_type": type_key, "time_of_day": when})
        options = avail.get("options") or []
        if not options:
            print(f"  skipped {first} {last}: nothing free for {type_key} ({when})")
            continue
        slot = options[min(days_ahead, len(options) - 1)]
        created = call("create_appointment", {
            "slot_id": slot["slot_id"],
            "new_patient": {"first_name": first, "last_name": last,
                            "date_of_birth": dob.isoformat(), "phone": phone},
            "conversation_id": "demo-reset",
        })
        if created.get("status") != "created_pending_verification":
            print(f"  failed {first} {last}: {created.get('status')} {created.get('reason', '')}")
            continue
        v = call("verify_appointment", {"appointment_ref": created["appointment_ref"]})
        mark = "verified" if v.get("verified") else f"NOT VERIFIED ({v.get('reason')})"
        appt = v.get("appointment") or {}
        print(f"  {first} {last}: {appt.get('appointment_type', type_key)} "
              f"{appt.get('spoken_time', slot['spoken_time'])} with "
              f"{appt.get('provider_name', slot.get('provider_name'))} — {mark}")
    print(card())
    print(f"Schedule page: {base}/schedule\n")
    return 0


async def main(book: bool, wake_url: str | None) -> int:
    if wake_url:
        print(f"== Waking {wake_url}")
        for attempt in range(6):
            try:
                r = httpx.get(f"{wake_url.rstrip('/')}/health", timeout=60)
                print(f"  {r.status_code} {r.text[:90]}")
                break
            except httpx.HTTPError as exc:
                print(f"  attempt {attempt + 1}: {type(exc).__name__}")

    settings = Settings.from_env()
    if settings.od_mode != "live":
        print("Set OD_MODE=live in .env first.")
        return 2
    cfg = load_config(settings.config_dir)
    od = build_od_client(settings)
    patients = PatientService(od)
    avail = AvailabilityService(cfg, od)
    booking = BookingService(cfg, od, avail)

    try:
        print("\n== Demo patients")
        created = {}
        for first, last, dob, phone in DEMO_PATIENTS:
            patient, was_new = await patients.find_or_create(first, last, dob, phone)
            created[(first, last)] = patient
            print(f"  {'created' if was_new else 'already there'}: {first} {last} (PatNum {patient.pat_num})")

        if book:
            now = avail.now_local()
            print("\n== Demo appointments")
            for idx, type_key, when, days_ahead in DEMO_BOOKINGS:
                first, last, _, _ = DEMO_PATIENTS[idx]
                patient = created[(first, last)]
                day = (now + timedelta(days=days_ahead)).date()
                options = await avail.find_options(cfg.appointment_types[type_key], day, day, when, now)
                if not options:
                    print(f"  skipped {first} {last} ({type_key}, {when} {day}): nothing free")
                    continue
                cand = options[0].candidate
                res = await booking.create(cand, patient.pat_num, False, "demo-reset")
                if res.status != "created_pending_verification":
                    print(f"  failed {first} {last}: {res.status} {res.reason or ''}")
                    continue
                expected = {"a": res.appointment.apt_num, "pat": patient.pat_num,
                            "start": cand.start.strftime(OD_DATETIME), "op": cand.op,
                            "len": len(cand.pattern), "prov": cand.prov_num, "t": type_key}
                v = await booking.verify(res.appointment.apt_num, expected)
                mark = "verified" if v.verified else f"NOT VERIFIED ({v.reason})"
                print(f"  {first} {last}: {cfg.appointment_types[type_key].display_name} "
                      f"{options[0].spoken_time} with {options[0].provider_name} — {mark}")

        print(card())
        print("Schedule page: add /schedule to your backend URL.")
        print("Check what is on it with: python backend/scripts/show_schedule.py --days 7\n")
        return 0
    finally:
        await od.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-book", action="store_true", help="only recreate the patients")
    ap.add_argument("--wake", help="backend URL to wake first (free hosting sleeps)")
    ap.add_argument("--via", help="run through this deployed backend instead of calling Open Dental directly")
    a = ap.parse_args()
    if a.via:
        import os

        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        secret = os.getenv("TOOL_SECRET", "")
        if not secret:
            print("TOOL_SECRET missing from .env")
            sys.exit(2)
        sys.exit(via_backend(a.via, secret, not a.no_book))
    sys.exit(asyncio.run(main(not a.no_book, a.wake)))
