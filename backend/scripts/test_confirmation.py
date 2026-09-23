"""Send a real confirmation for an existing, verified Open Dental appointment.

    python backend/scripts/test_confirmation.py --apt 53 --channel telegram
    python backend/scripts/test_confirmation.py --apt 53 --channel email --to you@example.com

Goes through the same path as the voice agent: verify in Open Dental -> n8n -> channel.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import build_od_client  # noqa: E402
from app.opendental.models import OD_DATETIME  # noqa: E402
from app.practice_config import load_config  # noqa: E402
from app.services.availability import AvailabilityService  # noqa: E402
from app.services.booking import NOTE_TAG, BookingService  # noqa: E402
from app.services.notifications import ConfirmationSender  # noqa: E402
from app.settings import Settings  # noqa: E402


async def main(apt_num: int, channel: str, to: str | None, first_name: str) -> int:
    settings = Settings.from_env()
    cfg = load_config(settings.config_dir)
    od = build_od_client(settings)
    notifier = ConfirmationSender(settings.n8n_confirmation_webhook_url, settings.n8n_webhook_secret, settings.telegram_chat_id,
                                  header_name=settings.n8n_webhook_header)
    try:
        apt = await od.get_appointment(apt_num)
        if apt is None:
            print("Appointment not found in Open Dental")
            return 1
        type_key = next((t.key for t in cfg.appointment_types.values() if f"{NOTE_TAG} {t.display_name}" in apt.note), None)
        if type_key is None:
            print("Not an appointment booked by this system")
            return 1
        booking = BookingService(cfg, od, AvailabilityService(cfg, od))
        expected = {"pat": apt.pat_num, "start": apt.start.strftime(OD_DATETIME), "op": apt.op,
                    "len": len(apt.pattern), "prov": apt.prov_num, "t": type_key}
        v = await booking.verify(apt_num, expected)
        print(f"verified={v.verified} reason={v.reason}")
        if not v.verified:
            return 1
        destination = to or settings.telegram_chat_id
        details = {"practice_name": cfg.name, "practice_phone": cfg.phone_display, "practice_city": cfg.city,
                   "patient_first_name": first_name, **booking.summary(v.appointment, type_key)}
        r = await notifier.send(apt_num, channel, destination, details, "test-script")
        print(f"sent={r.sent} reason={r.reason}")
        return 0 if r.sent else 1
    finally:
        await od.aclose()
        await notifier.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apt", type=int, required=True)
    ap.add_argument("--channel", choices=["email", "sms", "telegram"], default="telegram")
    ap.add_argument("--to")
    ap.add_argument("--first-name", default="Morgan")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.apt, a.channel, a.to, a.first_name)))
