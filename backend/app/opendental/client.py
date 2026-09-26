"""Open Dental Remote API adapter.

Only documented endpoints are used (see docs/OPEN_DENTAL_NOTES.md). This layer
translates requests/responses; it makes no booking decisions.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime
from typing import Any, Protocol

import httpx

from .models import (
    OD_DATE,
    OD_DATETIME,
    ODAppointment,
    ODOperatory,
    ODPatient,
    ODProvider,
    OpenDentalError,
)

log = logging.getLogger("fsd.opendental")

PAGE_SIZE = 100          # Remote API returns at most 100 items per request
MAX_PAGES = 20           # safety cap
MAX_429_RETRIES = 3
APPOINTMENT_CACHE_SECONDS = 90   # a background task refreshes this; see keep_cache_warm
SLOW_API_SECONDS = 3.0           # above this, Open Dental counts as degraded
SLOW_MODE_MAX_AGE_SECONDS = 25   # when degraded, a safety check may use a snapshot this recent
MAX_RETRY_AFTER_SECONDS = 10


class OpenDentalAPI(Protocol):
    async def list_operatories(self) -> list[ODOperatory]: ...
    async def list_providers(self) -> list[ODProvider]: ...
    async def search_patients(
        self, last_name: str, first_name: str, birthdate: date | None
    ) -> list[ODPatient]: ...
    async def get_patient(self, pat_num: int) -> ODPatient | None: ...
    async def create_patient(
        self, last_name: str, first_name: str, birthdate: date, phone: str | None
    ) -> ODPatient: ...
    async def list_appointments(
        self, date_start: date, date_end: date, pat_num: int | None = None, fresh: bool = False
    ) -> list[ODAppointment]: ...
    async def get_appointment(self, apt_num: int) -> ODAppointment | None: ...
    async def create_appointment(self, payload: dict[str, Any]) -> ODAppointment: ...
    async def get_slots(
        self, date_start: date, date_end: date, prov_num: int, op_num: int | None, length_minutes: int
    ) -> list[tuple[datetime, datetime, int, int]]: ...
    async def aclose(self) -> None: ...


class LiveOpenDentalClient:
    def __init__(
        self,
        base_url: str,
        developer_key: str,
        customer_key: str,
        min_interval_seconds: float = 1.1,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"ODFHIR {developer_key}/{customer_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout_seconds,
            transport=transport,
        )
        self._min_interval = min_interval_seconds
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        # (date_start, date_end, pat_num) -> (fetched_at, appointments)
        self._appt_cache: dict[tuple, tuple[float, list[ODAppointment]]] = {}
        self._latency = 0.0  # rolling average, used to detect a degraded API

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------- transport ----------
    async def _request(self, method: str, path: str, *, params=None, json=None) -> Any:
        for attempt in range(MAX_429_RETRIES + 1):
            async with self._lock:  # serialize requests to respect the Remote API throttle
                wait = self._min_interval - (time.monotonic() - self._last_request)
                if wait > 0:
                    await asyncio.sleep(wait)
                started = time.monotonic()
                try:
                    resp = await self._http.request(method, path, params=params, json=json)
                except httpx.TimeoutException as exc:
                    raise OpenDentalError(504, "timeout contacting Open Dental") from exc
                except httpx.HTTPError as exc:
                    raise OpenDentalError(503, f"network error: {type(exc).__name__}") from exc
                finally:
                    self._last_request = time.monotonic()

            took = self._last_request - started  # how long Open Dental itself took
            self._latency = round(0.6 * self._latency + 0.4 * took, 3) if self._latency else round(took, 3)
            log.info("od %s %s -> %s (%.1fs avg)", method, path, resp.status_code, self._latency)
            if resp.status_code == 429 and attempt < MAX_429_RETRIES:
                retry_after = _retry_after(resp)
                await asyncio.sleep(retry_after)
                continue
            if resp.status_code >= 400:
                raise OpenDentalError(resp.status_code, _error_text(resp))
            if not resp.content:
                return None
            return resp.json()
        raise OpenDentalError(429, "rate limited")

    async def _get_all(self, path: str, params: dict) -> list[dict]:
        items: list[dict] = []
        for page in range(MAX_PAGES):
            batch = await self._request("GET", path, params={**params, "Offset": page * PAGE_SIZE})
            batch = batch or []
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                return items
        log.warning("od paging cap reached for %s", path)
        return items

    # ---------- resources ----------
    async def list_operatories(self) -> list[ODOperatory]:
        return [ODOperatory.from_api(d) for d in await self._request("GET", "/operatories") or []]

    async def list_providers(self) -> list[ODProvider]:
        return [ODProvider.from_api(d) for d in await self._request("GET", "/providers") or []]

    async def search_patients(self, last_name, first_name, birthdate) -> list[ODPatient]:
        params = {"LName": last_name, "FName": first_name}
        if birthdate:
            params["Birthdate"] = birthdate.strftime(OD_DATE)
        return [ODPatient.from_api(d) for d in await self._get_all("/patients/Simple", params)]

    async def get_patient(self, pat_num) -> ODPatient | None:
        try:
            data = await self._request("GET", f"/patients/{int(pat_num)}")
        except OpenDentalError as exc:
            if exc.status == 404:
                return None
            raise
        return ODPatient.from_api(data) if data else None

    async def create_patient(self, last_name, first_name, birthdate, phone) -> ODPatient:
        body = {"LName": last_name, "FName": first_name, "Birthdate": birthdate.strftime(OD_DATE)}
        if phone:
            body["WirelessPhone"] = phone
        return ODPatient.from_api(await self._request("POST", "/patients", json=body))

    @property
    def is_degraded(self) -> bool:
        """Open Dental's own API is responding slowly."""
        return self._latency > SLOW_API_SECONDS

    async def list_appointments(self, date_start, date_end, pat_num=None, fresh=False) -> list[ODAppointment]:
        """Reads are cached for a few seconds because one phone call re-reads the same
        days several times. Safety checks (is the slot still free? did we double-book?)
        pass fresh=True and always hit Open Dental."""
        key = (date_start, date_end, pat_num)
        hit = self._appt_cache.get(key)
        age = (time.monotonic() - hit[0]) if hit else None
        if not fresh:
            if hit and age < APPOINTMENT_CACHE_SECONDS:
                log.debug("od appointments cache hit %s", key)
                return hit[1]
        elif hit and self.is_degraded and age < SLOW_MODE_MAX_AGE_SECONDS:
            # Open Dental is slow enough that a live check would time out the call.
            # Use the background-refreshed snapshot instead and say so in the logs.
            log.warning("od degraded (%.1fs); safety check used a %.0fs-old snapshot", self._latency, age)
            return hit[1]
        params: dict[str, Any] = {
            "dateStart": date_start.strftime(OD_DATE),
            "dateEnd": date_end.strftime(OD_DATE),
        }
        if pat_num is not None:
            params["PatNum"] = pat_num
        appts = [ODAppointment.from_api(d) for d in await self._get_all("/appointments", params)]
        self._appt_cache[key] = (time.monotonic(), appts)
        return appts

    async def get_appointment(self, apt_num) -> ODAppointment | None:
        try:
            data = await self._request("GET", f"/appointments/{int(apt_num)}")
        except OpenDentalError as exc:
            if exc.status == 404:
                return None
            raise
        return ODAppointment.from_api(data) if data else None

    async def create_appointment(self, payload) -> ODAppointment:
        apt = ODAppointment.from_api(await self._request("POST", "/appointments", json=payload))
        self._appt_cache.clear()  # the schedule just changed
        return apt

    async def get_slots(self, date_start, date_end, prov_num, op_num, length_minutes):
        params: dict[str, Any] = {
            "dateStart": date_start.strftime(OD_DATE),
            "dateEnd": date_end.strftime(OD_DATE),
            "ProvNum": prov_num,
            "lengthMinutes": length_minutes,
        }
        if op_num:
            params["OpNum"] = op_num
        data = await self._request("GET", "/appointments/Slots", params=params) or []
        return [
            (
                datetime.strptime(s["DateTimeStart"], OD_DATETIME),
                datetime.strptime(s["DateTimeEnd"], OD_DATETIME),
                int(s.get("ProvNum") or 0),
                int(s.get("OpNum") or 0),
            )
            for s in data
        ]


def _retry_after(resp: httpx.Response) -> float:
    try:
        return min(float(resp.headers.get("Retry-After", "1")), MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        return 1.0


def _error_text(resp: httpx.Response) -> str:
    text = resp.text.strip()
    return text[:300] if text else resp.reason_phrase
