"""Fairy Share Dental AI receptionist backend (demo — NOT HIPAA compliant)."""
from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .opendental.client import LiveOpenDentalClient, OpenDentalAPI
from .opendental.mock import MockOpenDentalClient
from .opendental.models import OpenDentalError
from .practice_config import load_config
from .refs import RefSigner
from .routers import schedule as schedule_router
from .routers import tools
from .services.availability import AvailabilityService
from .services.booking import BookingService
from .services.notifications import ConfirmationSender
from .services.patients import PatientService
from .settings import Settings

log = logging.getLogger("fsd")


def build_od_client(settings: Settings) -> OpenDentalAPI:
    if settings.od_mode == "live":
        return LiveOpenDentalClient(
            settings.od_base_url,
            settings.od_developer_key,
            settings.od_customer_key,
            settings.od_min_interval_seconds,
            settings.od_timeout_seconds,
        )
    return MockOpenDentalClient()


async def check_config_against_open_dental(cfg, od: OpenDentalAPI) -> list[str]:
    """Warn (don't crash) if configured operatories/providers don't exist in Open Dental."""
    problems: list[str] = []
    ops = {o.op_num: o for o in await od.list_operatories()}
    provs = {p.prov_num: p for p in await od.list_providers()}
    used_ops = {op for t in cfg.appointment_types.values() for op in t.operatories}
    for op_num in sorted(used_ops):
        op = ops.get(op_num)
        if op is None:
            problems.append(f"operatory {op_num} not found in Open Dental")
        elif op.is_hidden:
            problems.append(f"operatory {op_num} is hidden in Open Dental")
    for prov_num in cfg.providers:
        if prov_num not in provs:
            problems.append(f"provider {prov_num} not found in Open Dental")
    return problems


def create_app(
    settings: Settings | None = None,
    od: OpenDentalAPI | None = None,
    check_on_startup: bool = True,
    notifier: ConfirmationSender | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config(settings.config_dir)
    od = od or build_od_client(settings)
    notifier = notifier or ConfirmationSender(
        settings.n8n_confirmation_webhook_url, settings.n8n_webhook_secret, settings.telegram_chat_id,
        header_name=settings.n8n_webhook_header,
        demo_sms_via_telegram=settings.demo_sms_via_telegram,
        whatsapp_number=settings.whatsapp_number,
    )

    async def keep_cache_warm() -> None:
        """Open Dental can be slow (seconds per call). Refreshing in the background means
        callers are served from cache instead of waiting on it."""
        while True:
            try:
                await availability.warm_cache()
            except Exception as exc:  # never let the warmer kill the app
                log.warning("cache warm failed: %s", type(exc).__name__)
            await asyncio.sleep(settings.cache_warm_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        warmer = asyncio.create_task(keep_cache_warm()) if settings.cache_warm_seconds else None
        if check_on_startup:
            try:
                for p in await check_config_against_open_dental(cfg, od):
                    log.warning("config check: %s", p)
            except OpenDentalError as exc:
                log.warning("config check skipped: %s", exc)
        yield
        if warmer:
            warmer.cancel()
        await od.aclose()
        await notifier.aclose()

    availability = AvailabilityService(cfg, od)
    app = FastAPI(title="Fairy Share Dental — AI Receptionist Tools", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.cfg = cfg
    app.state.od = od
    app.state.signer = RefSigner(settings.ref_signing_key)
    app.state.patients = PatientService(od)
    app.state.availability = availability
    app.state.booking = BookingService(cfg, od, availability)
    app.state.notifier = notifier
    app.include_router(tools.router)
    app.include_router(schedule_router.router)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "mode": settings.od_mode,
            "availability_source": cfg.availability_source,
            "confirmations": "enabled" if notifier.enabled else "disabled",
            "open_dental_latency_secs": getattr(od, "_latency", None),
            "open_dental_degraded": getattr(od, "is_degraded", False),
        }

    return app
