"""Check the live Open Dental connection using the real service code.

    python backend/scripts/check_opendental.py              # read-only discovery
    python backend/scripts/check_opendental.py --write-test # also books + verifies one SYNTHETIC appointment

Requires OD_MODE=live and keys in .env. Write test uses a synthetic "Fsdtest" patient only.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import build_od_client, check_config_against_open_dental  # noqa: E402
from app.practice_config import load_config  # noqa: E402
from app.services.availability import AvailabilityService  # noqa: E402
from app.services.booking import BookingService  # noqa: E402
from app.services.patients import PatientService  # noqa: E402
from app.settings import Settings  # noqa: E402

SYNTHETIC = dict(first_name="Morgan", last_name="Fsdtest", dob=date(1991, 2, 14), phone="2145550188")


async def main(write_test: bool) -> int:
    settings = Settings.from_env()
    if settings.od_mode != "live":
        print("Set OD_MODE=live in .env first.")
        return 2
    cfg = load_config(settings.config_dir)
    od = build_od_client(settings)
    try:
        print("== Config vs Open Dental")
        problems = await check_config_against_open_dental(cfg, od)
        print("  OK" if not problems else "\n".join(f"  ! {p}" for p in problems))

        avail = AvailabilityService(cfg, od)
        now = avail.now_local()
        start = now.date() + timedelta(days=14)
        end = start + timedelta(days=4)
        print(f"== Practice-local now: {now}  | availability_source={cfg.availability_source}")
        print(f"== Slots endpoint (Tina, {start}..{end}):",
              len(await od.get_slots(start, end, 2, 5, 60)), "slots")
        existing = await od.list_appointments(start, end)
        print(f"== Existing appointments {start}..{end}: {len(existing)}")
        opts = await avail.find_options(cfg.appointment_types["cleaning"], start, end, "any", now)
        print("== Cleaning options:")
        for o in opts:
            print(f"  - {o.spoken_time} | op {o.candidate.op} | {o.provider_name}")

        if not write_test:
            return 0
        if not opts:
            print("No options to book.")
            return 1

        print("== WRITE TEST (synthetic patient)")
        patients = PatientService(od)
        booking = BookingService(cfg, od, avail)
        patient, created = await patients.find_or_create(
            SYNTHETIC["first_name"], SYNTHETIC["last_name"], SYNTHETIC["dob"], SYNTHETIC["phone"]
        )
        print(f"  patient PatNum={patient.pat_num} created={created}")
        cand = opts[-1].candidate
        res = await booking.create(cand, patient.pat_num, created, "check-script")
        print(f"  create: {res.status} {res.reason or ''}")
        if res.status != "created_pending_verification":
            return 1
        expected = {"a": res.appointment.apt_num, "pat": patient.pat_num, "start": cand.start.strftime("%Y-%m-%d %H:%M:%S"),
                    "op": cand.op, "len": len(cand.pattern), "prov": cand.prov_num, "t": cand.type_key}
        v = await booking.verify(res.appointment.apt_num, expected)
        print(f"  verify: verified={v.verified} reason={v.reason} AptNum={res.appointment.apt_num}")
        return 0 if v.verified else 1
    finally:
        await od.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-test", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args().write_test)))
