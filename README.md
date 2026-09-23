# Fairy Share Dental — AI Receptionist (Milestone 1: Book an Appointment)

A production-style voice receptionist for **Fairy Share Dental**, a fictional practice in Dallas, TX.
Callers phone the practice directly. It is not a marketplace.

**Stack:** ElevenLabs Agents → FastAPI tools (Python) → Open Dental Remote API → Open Dental developer test database.

> ⚠️ **Demo only.** It uses Open Dental test data and synthetic patients. It is **not HIPAA compliant**.

## Core rule: CHECK → BOOK → VERIFY → CONFIRM
The agent may only tell a caller they are booked after the backend has read the appointment
back from Open Dental and confirmed that every field matches. See `docs/ARCHITECTURE.md`.

## Quick start
```bash
uv venv .venv --python 3.12
```
```bash
uv pip install -p .venv -r backend/requirements.txt
```
```bash
cp .env.example .env
```
Edit `.env`:
- `OD_MODE=mock` works offline.
- `OD_MODE=live` needs the Open Dental test keys from https://www.opendental.com/site/apisetup.html.

Run the tests:
```bash
.venv/bin/python -m pytest -q
```
Check the live connection (read-only; add `--write-test` to book and verify one synthetic appointment):
```bash
.venv/bin/python backend/scripts/check_opendental.py
```
Run the API:
```bash
.venv/bin/uvicorn app.main:create_app --factory --app-dir backend --port 8000
```

## Tools (ElevenLabs webhooks)
| Tool | Purpose |
|---|---|
| `lookup_patient` | Exact match on name + date of birth. Returns a status and an opaque `patient_ref` |
| `get_appointments` | Upcoming appointments for a verified patient |
| `get_availability` | Up to 3 real open times, each with a signed `slot_id` |
| `create_appointment` | Re-checks the slot, then books. Returns `created_pending_verification` (never "booked") |
| `verify_appointment` | Reads the appointment back from Open Dental. `verified: true` is the only confirmation |
| `send_confirmation` | Email, SMS or Telegram confirmation through n8n. Re-verifies first; never sent for unverified appointments |

## Layout
```
backend/app/        FastAPI app, services, Open Dental adapter + mock
backend/scripts/    check_opendental.py (live discovery + write test)
config/             practice.yaml (hours, providers, rooms), appointment_types.yaml (durations, patterns)
elevenlabs/         SYSTEM_PROMPT.md, TOOLS.md, KNOWLEDGE_BASE.md, SETUP.md, provision_agent.py
n8n/                fsd_appointment_confirmation.workflow.json + setup README
tests/              unit/, contract/ (full flow on mock), live/ (opt-in)
docs/               ARCHITECTURE.md, OPEN_DENTAL_NOTES.md, TEST_SCRIPTS.md
```

## Status
- ✅ **V1: booking.** The live Open Dental booking and verification were confirmed on 2026-09-22.
- ✅ **V1: confirmations.** Email, SMS or Telegram, sent through n8n after verification.
- ⏭️ **V2 (later):** reschedule_appointment, cancel_appointment, human_handoff, urgent_call_escalation.
