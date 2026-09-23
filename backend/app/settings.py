"""Environment settings. Credentials only ever come from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    od_mode: str
    od_base_url: str
    od_developer_key: str
    od_customer_key: str
    od_min_interval_seconds: float
    od_timeout_seconds: float
    tool_secret: str
    ref_signing_key: str
    config_dir: Path
    log_level: str
    n8n_confirmation_webhook_url: str = ""
    n8n_webhook_secret: str = ""
    n8n_webhook_header: str = "X-FSD-Secret"
    telegram_chat_id: str = ""
    demo_sms_via_telegram: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        config_dir = Path(os.getenv("CONFIG_DIR", "config"))
        if not config_dir.is_absolute():
            config_dir = PROJECT_ROOT / config_dir
        s = cls(
            od_mode=os.getenv("OD_MODE", "mock").strip().lower(),
            od_base_url=os.getenv("OD_BASE_URL", "https://api.opendental.com/api/v1").rstrip("/"),
            od_developer_key=os.getenv("OD_DEVELOPER_KEY", ""),
            od_customer_key=os.getenv("OD_CUSTOMER_KEY", ""),
            od_min_interval_seconds=float(os.getenv("OD_MIN_INTERVAL_SECONDS", "1.1")),
            od_timeout_seconds=float(os.getenv("OD_TIMEOUT_SECONDS", "30")),
            tool_secret=os.getenv("TOOL_SECRET", ""),
            ref_signing_key=os.getenv("REF_SIGNING_KEY", ""),
            config_dir=config_dir,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            n8n_confirmation_webhook_url=os.getenv("N8N_CONFIRMATION_WEBHOOK_URL", ""),
            n8n_webhook_secret=os.getenv("N8N_WEBHOOK_SECRET", ""),
            n8n_webhook_header=os.getenv("N8N_WEBHOOK_HEADER", "X-FSD-Secret"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            demo_sms_via_telegram=os.getenv("DEMO_SMS_VIA_TELEGRAM", "false").strip().lower() in {"1", "true", "yes"},
        )
        s.validate()
        return s

    def validate(self) -> None:
        if self.od_mode not in {"mock", "live"}:
            raise ValueError("OD_MODE must be 'mock' or 'live'")
        if self.od_mode == "live" and not (self.od_developer_key and self.od_customer_key):
            raise ValueError("OD_MODE=live requires OD_DEVELOPER_KEY and OD_CUSTOMER_KEY")
        if not self.tool_secret or not self.ref_signing_key:
            raise ValueError("TOOL_SECRET and REF_SIGNING_KEY must be set")
        if self.n8n_confirmation_webhook_url and not self.n8n_webhook_secret:
            raise ValueError("N8N_CONFIRMATION_WEBHOOK_URL requires N8N_WEBHOOK_SECRET")
