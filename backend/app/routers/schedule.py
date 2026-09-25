"""Read-only schedule viewer for staff: /schedule

Shows what is actually in Open Dental — never a local copy. Password protected
(ADMIN_PASSWORD); the page is disabled entirely when no password is configured.
Nothing here writes to Open Dental.
"""
from __future__ import annotations

import hmac
import logging
from datetime import date, datetime, timedelta
from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..opendental.models import ODAppointment, OpenDentalError
from ..refs import InvalidRef
from ..services.booking import NOTE_TAG

log = logging.getLogger("fsd.schedule")
router = APIRouter()

COOKIE = "fsd_schedule"
SESSION_HOURS = 12
MAX_DAYS = 60
MAX_NAME_LOOKUPS = 40  # each costs one Open Dental request (~1/second)


def _authed(request: Request) -> bool:
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    try:
        request.app.state.signer.verify("adm", token)
        return True
    except InvalidRef:
        return False


@router.get("/schedule", response_class=HTMLResponse)
async def schedule(request: Request, date_from: str | None = None, days: int = 7):
    s = request.app.state
    if not s.settings.admin_password:
        return HTMLResponse(_shell("Schedule", "<p class='muted'>The schedule viewer is disabled. Set ADMIN_PASSWORD to enable it.</p>"), 404)
    if not _authed(request):
        return HTMLResponse(_login_page())

    try:
        start = date.fromisoformat(date_from) if date_from else _today(request)
    except ValueError:
        start = _today(request)
    days = max(1, min(days, MAX_DAYS))
    end = start + timedelta(days=days - 1)

    try:
        # Only what is actually on the appointment book: Planned and UnschedList appointments
        # have been taken off the schedule, so staff should not see them here.
        appts = sorted(
            (a for a in await s.od.list_appointments(start, end) if a.occupies_schedule),
            key=lambda a: (a.start, a.op),
        )
        names = await _patient_names(request, appts)
    except OpenDentalError as exc:
        log.warning("schedule read failed: %s", exc)
        return HTMLResponse(_shell("Schedule", f"<p class='muted'>Could not reach Open Dental ({exc.status}). Try again shortly.</p>"))

    return HTMLResponse(_schedule_page(request, appts, names, start, end, days))


@router.post("/schedule/login")
async def login(request: Request, password: str = Form(default="")):
    s = request.app.state
    password = (password or "").strip()
    if not password:
        return HTMLResponse(_login_page("Please enter the password."), 400)
    if not s.settings.admin_password or not hmac.compare_digest(password, s.settings.admin_password.strip()):
        log.info("schedule login failed")
        return HTMLResponse(_login_page("That password was not recognized."), 401)
    resp = RedirectResponse("/schedule", status_code=303)
    resp.set_cookie(
        COOKIE,
        s.signer.sign("adm", {"v": 1}, ttl_seconds=SESSION_HOURS * 3600),
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return resp


@router.get("/schedule/logout")
async def logout():
    resp = RedirectResponse("/schedule", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


# ---------- data ----------
def _today(request: Request) -> date:
    return request.app.state.availability.now_local().date()


async def _patient_names(request: Request, appts: list[ODAppointment]) -> dict[int, str]:
    cache: dict[int, str] = request.app.state.__dict__.setdefault("patient_name_cache", {})
    wanted = sorted({a.pat_num for a in appts if a.pat_num and a.pat_num not in cache})
    for pat_num in wanted[:MAX_NAME_LOOKUPS]:
        p = await request.app.state.od.get_patient(pat_num)
        cache[pat_num] = f"{p.first_name} {p.last_name}".strip() if p else f"Patient {pat_num}"
    return cache


# ---------- rendering ----------
CSS = """
:root{--bg:#08090a;--bg-2:#0e1011;--line:rgba(255,255,255,.1);--line-2:rgba(255,255,255,.16);
--text:#f4f5f4;--text-2:#9aa3a1;--text-3:#6b7472;--blue:#8fb6ff;--green:#4cc9f0;--amber:#e8c46a;
--sans:'Schibsted Grotesk',system-ui,-apple-system,'Segoe UI',sans-serif;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);font-size:16px;line-height:1.6}
.wrap{width:min(1080px,100% - 32px);margin-inline:auto;padding-bottom:64px}
a{color:var(--blue)}
header.top{padding:18px 0;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.mark{display:flex;align-items:center;gap:10px;font-weight:600;letter-spacing:-.01em}
.mark i{width:26px;height:26px;border-radius:7px;background:linear-gradient(140deg,#8fb6ff,#2b4c8f);
display:grid;place-items:center;color:#061225;font-size:11.5px;font-style:normal;font-weight:700}
.chip{margin-left:auto;display:inline-flex;align-items:center;gap:7px;font-size:12.5px;color:var(--text-2);
background:rgba(255,255,255,.05);border:1px solid var(--line);border-radius:999px;padding:5px 12px}
h1{font-size:22px;margin:28px 0 4px;letter-spacing:-.02em}
.muted{color:var(--text-2);font-size:14px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:18px 0 8px}
.btn{display:inline-block;border:1px solid var(--line-2);background:rgba(255,255,255,.05);color:var(--text);
padding:7px 13px;border-radius:9px;text-decoration:none;font-size:14px;cursor:pointer}
.btn:hover{background:rgba(255,255,255,.1)}
.btn.on{background:var(--blue);color:#061225;border-color:transparent;font-weight:600}
.day{margin:26px 0 10px;font-size:14px;color:var(--text-2);text-transform:uppercase;letter-spacing:.08em}
.card{border:1px solid var(--line);background:var(--bg-2);border-radius:14px;padding:14px 16px;margin-bottom:10px}
.row1{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.time{font-variant-numeric:tabular-nums;font-weight:600;font-size:17px;white-space:nowrap}
.type{font-weight:600}
.who{color:var(--text-2)}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.tag{font-size:12px;color:var(--text-2);border:1px solid var(--line);border-radius:999px;padding:3px 9px;white-space:nowrap}
.tag.ai{color:var(--green);border-color:rgba(76,201,240,.35)}
.tag.manual{color:var(--amber);border-color:rgba(232,196,106,.35)}
.tag.status{color:var(--text)}
.note{margin-top:8px;font-size:13px;color:var(--text-3);word-break:break-word}
.empty{border:1px dashed var(--line);border-radius:14px;padding:26px;text-align:center;color:var(--text-2)}
form.login{max-width:340px;margin:12vh auto 0;text-align:center}
input[type=password]{width:100%;padding:11px 13px;border-radius:10px;border:1px solid var(--line-2);
background:var(--bg-2);color:var(--text);font-size:16px;margin:14px 0}
.err{color:#e2726a;font-size:14px}
footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);color:var(--text-3);font-size:13px}
@media(max-width:560px){.time{font-size:16px}h1{font-size:19px}}
"""


def _shell(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{escape(title)} — Fairy Share Dental</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="wrap">{body}</div></body></html>"""


def _login_page(error: str = "") -> str:
    err = f'<p class="err">{escape(error)}</p>' if error else ""
    return _shell("Schedule", f"""
<form class="login" method="post" action="/schedule/login" enctype="application/x-www-form-urlencoded">
  <div class="mark" style="justify-content:center"><i>FS</i> Fairy Share Dental</div>
  <p class="muted">Staff schedule viewer</p>
  {err}
  <input type="password" name="password" placeholder="Password" autofocus required
         autocomplete="current-password" autocapitalize="off" autocorrect="off" spellcheck="false"
         aria-label="Password">
  <button class="btn on" type="submit" style="width:100%">Open schedule</button>
</form>""")


def _schedule_page(request: Request, appts, names, start: date, end: date, days: int) -> str:
    cfg = request.app.state.cfg
    today = _today(request)
    ai_count = sum(1 for a in appts if NOTE_TAG in a.note)

    ranges = "".join(
        f'<a class="btn{" on" if days == d else ""}" href="/schedule?date_from={start}&days={d}">{label}</a>'
        for d, label in ((1, "Day"), (7, "Week"), (14, "2 weeks"), (30, "Month"))
    )
    prev_day = start - timedelta(days=days)
    next_day = start + timedelta(days=days)
    nav = (
        f'<a class="btn" href="/schedule?date_from={prev_day}&days={days}">← Earlier</a>'
        f'<a class="btn" href="/schedule?date_from={today}&days={days}">Today</a>'
        f'<a class="btn" href="/schedule?date_from={next_day}&days={days}">Later →</a>'
    )

    body = [
        '<header class="top"><div class="mark"><i>FS</i> Fairy Share Dental</div>'
        f'<span class="chip">live from Open Dental · <b>{len(appts)}</b> appointment(s), <b>{ai_count}</b> by Joy</span></header>',
        f"<h1>{start:%A, %B %-d}" + (f" – {end:%A, %B %-d, %Y}" if days > 1 else f", {start:%Y}") + "</h1>",
        f'<p class="muted">Read-only view of the practice schedule. Times are {cfg.name} local time.</p>',
        f'<div class="bar">{ranges}</div><div class="bar">{nav}'
        f'<a class="btn" href="/schedule?date_from={start}&days={days}">Refresh</a>'
        '<a class="btn" href="/schedule/logout" style="margin-left:auto">Sign out</a></div>',
    ]

    if not appts:
        body.append('<div class="empty">Nothing booked in this range.</div>')
    else:
        current = None
        for a in appts:
            if a.start.date() != current:
                current = a.start.date()
                label = f"{current:%A, %B %-d, %Y}" + ("  ·  today" if current == today else "")
                body.append(f'<div class="day">{escape(label)}</div>')
            body.append(_appointment_card(cfg, a, names))

    body.append(
        '<footer>Demo system · synthetic data · not HIPAA compliant. '
        "Bookings tagged “by Joy” were made by the AI receptionist and verified in Open Dental before the caller was told.</footer>"
    )
    return _shell("Schedule", "".join(body))


def _appointment_card(cfg, a: ODAppointment, names: dict[int, str]) -> str:
    op = cfg.operatories.get(a.op)
    busy = a.prov_hyg if (a.is_hygiene and a.prov_hyg) else a.prov_num
    prov = cfg.providers.get(busy)
    appt_type = next(
        (t.display_name for t in cfg.appointment_types.values() if f"{NOTE_TAG} {t.display_name}" in a.note), ""
    )
    by_ai = NOTE_TAG in a.note
    minutes = len(a.pattern) * 5
    note = f'<div class="note">{escape(a.note[:160])}</div>' if a.note else ""
    return f"""<div class="card">
  <div class="row1">
    <span class="time">{a.start:%-I:%M %p}–{a.end:%-I:%M %p}</span>
    <span class="type">{escape(appt_type or "Appointment")}</span>
    <span class="who">{escape(names.get(a.pat_num, "—"))}</span>
  </div>
  <div class="tags">
    <span class="tag status">{escape(a.status)}</span>
    <span class="tag">{minutes} min</span>
    <span class="tag">{escape(prov.spoken_name if prov else f"Provider {busy}")}</span>
    <span class="tag">{escape(op.name if op else f"Room {a.op}")}</span>
    <span class="tag {'ai' if by_ai else 'manual'}">{'booked by Joy' if by_ai else 'booked in practice'}</span>
    <span class="tag">AptNum {a.apt_num}</span>
  </div>{note}
</div>"""
