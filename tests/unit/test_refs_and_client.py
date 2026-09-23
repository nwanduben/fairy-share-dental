import asyncio
import time

import httpx
import pytest

from app.opendental.client import LiveOpenDentalClient
from app.refs import InvalidRef, RefSigner


def test_ref_roundtrip_and_tamper():
    s = RefSigner("k")
    tok = s.sign("slot", {"o": 5})
    assert s.verify("slot", tok)["o"] == 5
    with pytest.raises(InvalidRef):
        s.verify("apt", tok)  # wrong kind
    with pytest.raises(InvalidRef):
        RefSigner("other").verify("slot", tok)  # wrong key
    body, sig = tok.split(".")
    with pytest.raises(InvalidRef):
        s.verify("slot", body[:-2] + "AA." + sig)


def test_ref_expiry(monkeypatch):
    s = RefSigner("k")
    tok = s.sign("slot", {"o": 5}, ttl_seconds=60)
    monkeypatch.setattr(time, "time", lambda: 10**11)
    with pytest.raises(InvalidRef, match="expired"):
        s.verify("slot", tok)


def test_client_sends_odfhir_header_and_strips_sensitive_provider_fields():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers["Authorization"]
        return httpx.Response(200, json=[{"ProvNum": 1, "Abbr": "DOC1", "SSN": "123", "StateLicense": "9", "IsHidden": "false"}])

    c = LiveOpenDentalClient("https://x/api/v1", "DEV", "CUST", 0, 5, transport=httpx.MockTransport(handler))
    provs = asyncio.run(c.list_providers())
    assert seen["auth"] == "ODFHIR DEV/CUST"
    assert not hasattr(provs[0], "SSN") and "123" not in repr(provs[0])


def test_client_retries_on_429():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=[])

    c = LiveOpenDentalClient("https://x/api/v1", "D", "C", 0, 5, transport=httpx.MockTransport(handler))
    assert asyncio.run(c.list_operatories()) == []
    assert calls["n"] == 2


def test_client_pages_with_offset():
    offsets = []

    def handler(req):
        off = int(req.url.params["Offset"])
        offsets.append(off)
        n = 100 if off == 0 else 7
        return httpx.Response(200, json=[
            {"AptNum": off + i, "AptDateTime": "2026-09-28 08:00:00", "Op": 1, "Pattern": "XX", "AptStatus": "Scheduled"}
            for i in range(n)
        ])

    from datetime import date
    c = LiveOpenDentalClient("https://x/api/v1", "D", "C", 0, 5, transport=httpx.MockTransport(handler))
    appts = asyncio.run(c.list_appointments(date(2026, 9, 28), date(2026, 9, 28)))
    assert len(appts) == 107 and offsets == [0, 100]
