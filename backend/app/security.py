"""Shared-secret check for ElevenLabs webhook calls."""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request


async def require_tool_secret(request: Request, x_tool_secret: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.tool_secret
    if not x_tool_secret or not hmac.compare_digest(x_tool_secret, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
