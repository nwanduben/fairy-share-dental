"""Read-only smoke tests against the real Open Dental test API.

    OD_LIVE_TESTS=1 OD_MODE=live pytest -m live
"""
import asyncio
import os

import pytest

from app.main import build_od_client
from app.settings import Settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("OD_LIVE_TESTS") != "1", reason="set OD_LIVE_TESTS=1 to hit Open Dental"),
]


def test_live_operatories_and_providers_match_config(cfg):
    od = build_od_client(Settings.from_env())

    async def go():
        try:
            return await od.list_operatories(), await od.list_providers()
        finally:
            await od.aclose()

    ops, provs = asyncio.run(go())
    op_nums = {o.op_num for o in ops if not o.is_hidden}
    assert {1, 2, 5, 6} <= op_nums
    assert set(cfg.providers) <= {p.prov_num for p in provs}
