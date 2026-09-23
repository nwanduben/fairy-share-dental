from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.opendental.mock import MockOpenDentalClient
from app.practice_config import load_config
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-secret"
# Tuesday 2026-09-22 09:00 practice-local
FIXED_NOW = datetime(2026, 9, 22, 9, 0, 0)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        od_mode="mock", od_base_url="http://mock", od_developer_key="", od_customer_key="",
        od_min_interval_seconds=0, od_timeout_seconds=5, tool_secret=SECRET,
        ref_signing_key="test-signing-key", config_dir=ROOT / "config", log_level="WARNING",
    )


@pytest.fixture
def cfg(settings):
    return load_config(settings.config_dir)


@pytest.fixture
def od() -> MockOpenDentalClient:
    return MockOpenDentalClient()


@pytest.fixture
def app(settings, od):
    app = create_app(settings, od)
    app.state.availability.now_local = lambda: FIXED_NOW
    return app


@pytest.fixture
def client(app):
    with TestClient(app, headers={"X-Tool-Secret": SECRET}) as c:
        yield c
