"""Appointment confirmations (email / SMS / Telegram) delivered by an n8n workflow.

Rule: a confirmation is only sent for an appointment that verify() has just confirmed
in Open Dental. The backend decides WHAT to send; n8n only delivers it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger("fsd.notify")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True)
class SendResult:
    sent: bool
    reason: str | None = None


class ConfirmationSender:
    def __init__(
        self,
        webhook_url: str,
        webhook_secret: str,
        telegram_chat_id: str = "",
        header_name: str = "X-FSD-Secret",
        demo_sms_via_telegram: bool = False,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret
        self.telegram_chat_id = telegram_chat_id
        self.header_name = header_name
        self.demo_sms_via_telegram = demo_sms_via_telegram
        self._http = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)
        self._sent: set[tuple[int, str]] = set()  # (AptNum, channel): don't send duplicates

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, apt_num: int, channel: str, destination: str, details: dict, conversation_id: str | None) -> SendResult:
        if not self.enabled:
            return SendResult(False, "confirmations_not_configured")
        if (apt_num, channel) in self._sent:
            return SendResult(True, "already_sent")
        payload = {
            "event": "appointment.verified",
            "channel": channel,
            "to": destination,
            "appointment_id": apt_num,
            "conversation_id": conversation_id,
            **details,
        }
        try:
            resp = await self._http.post(self.webhook_url, json=payload, headers={self.header_name: self.webhook_secret})
        except httpx.HTTPError as exc:
            log.warning("confirmation webhook error apt=%s channel=%s %s", apt_num, channel, type(exc).__name__)
            return SendResult(False, "delivery_service_unreachable")
        # Only an explicit {"sent": true} from the workflow counts. n8n can answer 200 with an
        # empty body when the workflow fails before its Respond node, which must not read as "sent".
        try:
            body = resp.json()
        except ValueError:
            body = {}
        ok = resp.status_code < 300 and isinstance(body, dict) and body.get("sent") is True
        log.info("confirmation apt=%s channel=%s status=%s ok=%s", apt_num, channel, resp.status_code, ok)
        if not ok:
            return SendResult(False, f"delivery_failed_{resp.status_code}")
        self._sent.add((apt_num, channel))
        return SendResult(True)
