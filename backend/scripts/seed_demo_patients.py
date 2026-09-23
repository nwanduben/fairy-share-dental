"""Create SYNTHETIC existing patients in Open Dental so demo calls can be identified.

    python backend/scripts/seed_demo_patients.py          # create (skips ones that exist)
    python backend/scripts/seed_demo_patients.py --list   # just show the demo card

These are invented people for demos only. Never add real patient data.
Re-running is safe: each person is matched on name + date of birth before creating.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import build_od_client  # noqa: E402
from app.services.patients import PatientService  # noqa: E402
from app.settings import Settings  # noqa: E402

# (first, last, date of birth, phone) — all invented; 555 numbers are reserved for fiction.
DEMO_PATIENTS = [
    ("Benjamin", "Johnson", date(1990, 5, 14), "2145550111"),
    ("Sarah", "Mitchell", date(1985, 9, 2), "2145550112"),
    ("Marcus", "Reed", date(1978, 3, 21), "2145550113"),
    ("Aisha", "Bello", date(1996, 12, 8), "2145550114"),
    ("David", "Nguyen", date(2001, 7, 30), "2145550115"),
    ("Morgan", "Fsdtest", date(1991, 2, 14), "2145550188"),
]


def card() -> str:
    lines = ["", "Demo patients (say the name, then the date of birth):", "-" * 62]
    for first, last, dob, phone in DEMO_PATIENTS:
        name = f"{first} {last}"
        lines.append(f"  {name:<20} born {f"{dob:%B %-d, %Y}":<18}  phone {phone}")
    lines += ["-" * 62, "Anyone else is treated as a new patient.", ""]
    return "\n".join(lines)


async def main(list_only: bool) -> int:
    if list_only:
        print(card())
        return 0
    settings = Settings.from_env()
    if settings.od_mode != "live":
        print("Set OD_MODE=live to seed the Open Dental test database.")
        return 2
    od = build_od_client(settings)
    patients = PatientService(od)
    try:
        for first, last, dob, phone in DEMO_PATIENTS:
            patient, created = await patients.find_or_create(first, last, dob, phone)
            print(f"  {'created' if created else 'already there'}: {first} {last} (PatNum {patient.pat_num})")
    finally:
        await od.aclose()
    print(card())
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args().list)))
