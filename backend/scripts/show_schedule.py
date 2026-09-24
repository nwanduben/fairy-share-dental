"""Show every appointment in Open Dental, with full detail. Read-only.

    python backend/scripts/show_schedule.py                 # today + next 14 days
    python backend/scripts/show_schedule.py --days 30
    python backend/scripts/show_schedule.py --from 2026-09-28 --to 2026-10-02
    python backend/scripts/show_schedule.py --raw            # full Open Dental JSON per appointment

Shows each appointment's date/time, length, room, provider, patient, status and note,
plus who booked it (AI receptionist bookings carry the [FSD-AI] tag).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import build_od_client  # noqa: E402
from app.practice_config import load_config  # noqa: E402
from app.services.booking import NOTE_TAG  # noqa: E402
from app.settings import Settings  # noqa: E402


async def main(date_from: date, date_to: date, raw: bool) -> int:
    settings = Settings.from_env()
    cfg = load_config(settings.config_dir)
    od = build_od_client(settings)
    try:
        appts = sorted(await od.list_appointments(date_from, date_to), key=lambda a: (a.start, a.op))
        print(f"\n{cfg.name} — appointments {date_from} to {date_to}")
        print(f"Source: Open Dental ({settings.od_base_url}), {len(appts)} appointment(s)\n")
        if not appts:
            print("  (nothing booked in this range)\n")
            return 0

        # Patient names: one extra request each (Remote API allows ~1/second).
        names: dict[int, str] = {}
        for pat_num in sorted({a.pat_num for a in appts if a.pat_num}):
            p = await od.get_patient(pat_num)
            names[pat_num] = f"{p.first_name} {p.last_name}".strip() if p else f"(patient {pat_num})"

        current_day = None
        for a in appts:
            if a.start.date() != current_day:
                current_day = a.start.date()
                print(f"── {current_day:%A, %B %-d, %Y} " + "─" * 30)
            op = cfg.operatories.get(a.op)
            busy = a.prov_hyg if (a.is_hygiene and a.prov_hyg) else a.prov_num
            prov = cfg.providers.get(busy)
            booked_by = "AI receptionist" if NOTE_TAG in a.note else "other/manual"
            appt_type = next(
                (t.display_name for t in cfg.appointment_types.values() if f"{NOTE_TAG} {t.display_name}" in a.note),
                "—",
            )
            print(
                f"  {a.start:%H:%M}-{a.end:%H:%M}  {appt_type:<22} {names.get(a.pat_num, '—'):<20}"
                f" {(prov.spoken_name if prov else f'prov {busy}'):<24}"
                f" {(op.name if op else f'op {a.op}'):<14} {a.status:<10} AptNum {a.apt_num}"
            )
            print(f"      length {len(a.pattern) * 5} min (pattern {a.pattern}) · booked by {booked_by}")
            if a.note:
                print(f"      note: {a.note[:110]}")
            if raw:
                full = await od.get_appointment(a.apt_num)
                print("      raw:", json.dumps(full.__dict__, default=str)[:600])
        print()
        return 0
    finally:
        await od.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--raw", action="store_true")
    a = ap.parse_args()
    start = date.fromisoformat(a.date_from) if a.date_from else date.today()
    end = date.fromisoformat(a.date_to) if a.date_to else start + timedelta(days=a.days)
    sys.exit(asyncio.run(main(start, end, a.raw)))
