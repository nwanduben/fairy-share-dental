"""Loads and validates config/practice.yaml and config/appointment_types.yaml."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PATTERN_RE = re.compile(r"^[X/]+$")


def _t(value: str) -> time:
    return time.fromisoformat(value)


@dataclass(frozen=True)
class DayHours:
    open: time
    close: time
    breaks: tuple[tuple[time, time], ...] = ()


@dataclass(frozen=True)
class Provider:
    prov_num: int
    spoken_name: str
    role: str  # dentist | hygienist


@dataclass(frozen=True)
class Operatory:
    op_num: int
    name: str
    kind: str  # dentist | hygiene
    dentist: int | None
    hygienist: int | None = None


@dataclass(frozen=True)
class AppointmentType:
    key: str
    display_name: str
    spoken_name: str
    duration_minutes: int
    pattern: str
    provider_role: str
    operatories: tuple[int, ...]
    is_hygiene: bool
    new_patients_only: bool
    open_dental_appointment_type_num: int | None


@dataclass(frozen=True)
class BookingRules:
    min_notice_minutes: int
    max_search_days: int
    start_increment_minutes: int
    max_options_offered: int
    slot_offer_ttl_minutes: int


@dataclass(frozen=True)
class PracticeConfig:
    name: str
    timezone: ZoneInfo
    availability_source: str
    rules: BookingRules
    hours: dict[int, DayHours | None]  # weekday index (0=Mon) -> hours
    closed_dates: frozenset[date]
    providers: dict[int, Provider]
    operatories: dict[int, Operatory]
    appointment_types: dict[str, AppointmentType] = field(default_factory=dict)
    city: str = ""
    phone_display: str = ""


class ConfigError(ValueError):
    pass


def load_config(config_dir: Path) -> PracticeConfig:
    practice = yaml.safe_load((config_dir / "practice.yaml").read_text())
    types_raw = yaml.safe_load((config_dir / "appointment_types.yaml").read_text())

    hours: dict[int, DayHours | None] = {}
    for idx, day in enumerate(WEEKDAYS):
        raw = practice["office_hours"].get(day)
        if raw is None:
            hours[idx] = None
            continue
        hours[idx] = DayHours(
            open=_t(raw["open"]),
            close=_t(raw["close"]),
            breaks=tuple((_t(a), _t(b)) for a, b in raw.get("breaks", [])),
        )

    providers = {
        int(num): Provider(int(num), p["spoken_name"], p["role"])
        for num, p in practice["providers"].items()
    }
    operatories = {
        int(num): Operatory(int(num), o["name"], o["kind"], o.get("dentist"), o.get("hygienist"))
        for num, o in practice["operatories"].items()
    }

    rules_raw = practice["booking_rules"]
    rules = BookingRules(**{k: int(v) for k, v in rules_raw.items()})

    cfg = PracticeConfig(
        name=practice["practice"]["name"],
        timezone=ZoneInfo(practice["practice"]["timezone"]),
        availability_source=practice.get("availability_source", "config"),
        rules=rules,
        hours=hours,
        closed_dates=frozenset(date.fromisoformat(d) for d in practice.get("closed_dates") or []),
        providers=providers,
        operatories=operatories,
        city=f"{practice['practice'].get('city', '')}, {practice['practice'].get('state', '')}".strip(", "),
        phone_display=practice["practice"].get("phone_display", ""),
    )

    for key, t in types_raw["appointment_types"].items():
        cfg.appointment_types[key] = AppointmentType(
            key=key,
            display_name=t["display_name"],
            spoken_name=t["spoken_name"],
            duration_minutes=int(t["duration_minutes"]),
            pattern=t["pattern"],
            provider_role=t["provider_role"],
            operatories=tuple(int(o) for o in t["operatories"]),
            is_hygiene=bool(t["is_hygiene"]),
            new_patients_only=bool(t.get("new_patients_only", False)),
            open_dental_appointment_type_num=t.get("open_dental_appointment_type_num"),
        )

    _validate(cfg)
    return cfg


def _validate(cfg: PracticeConfig) -> None:
    if cfg.availability_source not in {"config", "slots"}:
        raise ConfigError("availability_source must be 'config' or 'slots'")
    for t in cfg.appointment_types.values():
        if not PATTERN_RE.match(t.pattern):
            raise ConfigError(f"{t.key}: pattern may only contain 'X' and '/'")
        if len(t.pattern) * 5 != t.duration_minutes:
            raise ConfigError(
                f"{t.key}: pattern is {len(t.pattern) * 5} min but duration_minutes is {t.duration_minutes}"
            )
        for op_num in t.operatories:
            op = cfg.operatories.get(op_num)
            if op is None:
                raise ConfigError(f"{t.key}: operatory {op_num} not defined in practice.yaml")
            busy = op.hygienist if t.is_hygiene else op.dentist
            if busy is None:
                raise ConfigError(f"{t.key}: operatory {op_num} has no {t.provider_role} assigned")
            if cfg.providers[busy].role != t.provider_role:
                raise ConfigError(f"{t.key}: operatory {op_num} provider {busy} is not a {t.provider_role}")
