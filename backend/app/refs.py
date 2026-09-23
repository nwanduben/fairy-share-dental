"""Opaque, tamper-proof references handed to the voice agent.

The agent never sees raw Open Dental IDs. It receives signed tokens
(slot_id, patient_ref, appointment_ref) and passes them back verbatim.
The server validates the signature and expiry, so the LLM cannot invent
or alter a slot, patient or appointment. Stateless: survives restarts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


class InvalidRef(ValueError):
    pass


class RefSigner:
    def __init__(self, key: str):
        self._key = key.encode()

    def _sig(self, body: bytes) -> str:
        return _b64(hmac.new(self._key, body, hashlib.sha256).digest()[:12])

    def sign(self, kind: str, data: dict, ttl_seconds: int | None = None) -> str:
        payload = {"k": kind, **data}
        if ttl_seconds:
            payload["exp"] = int(time.time()) + ttl_seconds
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return f"{kind}_{_b64(body)}.{self._sig(body)}"

    def verify(self, kind: str, token: str) -> dict:
        try:
            prefix, rest = token.split("_", 1)
            b64body, sig = rest.rsplit(".", 1)
            body = _unb64(b64body)
        except Exception as exc:
            raise InvalidRef("malformed reference") from exc
        if prefix != kind or not hmac.compare_digest(sig, self._sig(body)):
            raise InvalidRef("invalid reference")
        payload = json.loads(body)
        if payload.get("k") != kind:
            raise InvalidRef("invalid reference")
        if "exp" in payload and payload["exp"] < time.time():
            raise InvalidRef("expired reference")
        return payload


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
