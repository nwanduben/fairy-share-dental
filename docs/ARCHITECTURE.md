# Architecture

```
Caller ──► ElevenLabs Agent (voice + LLM + SYSTEM_PROMPT)
              │  5 webhook tools, HTTPS, X-Tool-Secret header
              ▼
        FastAPI backend  (backend/app)
          routers/tools.py       validate input, shape responses, agent_instruction
          services/patients.py   exact name+DOB match, dedupe on create
          services/availability  CHECK: hours/Slots → candidates → conflict filter → 3 spread options
          services/booking.py    BOOK: idempotency → fresh re-check → POST
                                 VERIFY: GET back, field compare, post-write clash check
          services/schedule.py   Candidate + find_conflict (room OR busy provider overlap)
          refs.py                HMAC-signed slot_id / patient_ref / appointment_ref
          practice_config.py     config/*.yaml → validated models
          opendental/client.py   adapter only: auth, throttle, 429 retry, paging, field stripping
          opendental/mock.py     same interface, in-memory, for tests/offline
              │
              ▼
        Open Dental Remote API ──► eConnector ──► practice DB (test DB for the demo)
```

## Design rules
- **ElevenLabs never talks to Open Dental directly.** All decisions are made in the services, not in the LLM or the adapter.
- **CHECK → BOOK → VERIFY → CONFIRM:**
  - `create_appointment` can only return `created_pending_verification`.
  - Only `verify_appointment` can return `verified: true`, which is the only state the prompt allows to be spoken as "booked".
- **Opaque signed references.** The LLM never sees PatNum or AptNum and can't invent or alter a slot. Tokens are stateless, so they survive restarts, and slot offers expire after 15 minutes.
- **Minimum data to the LLM.** Match status and spoken strings only. No record fields are read back.
- **Logs contain no health information.** Only tool name, conversation id, outcome, and AptNum or op.
- **Idempotent booking.** A retried `create_appointment` finds the existing appointment for that patient, time and room instead of creating a second one.
- **Latency.** At 1 request per second, a booking is:
  - Existing patient: idempotency check, re-check, POST (3 requests), then verify (2 more). About 5 seconds in total, across two turns.
  - New patient: add 1–2 requests.

## Availability sources
| `availability_source` | Windows come from | Conflicts checked against |
|---|---|---|
| `config` (demo) | `office_hours`, breaks and `closed_dates` in practice.yaml | Live `GET /appointments` |
| `slots` (real practice) | Open Dental `GET /appointments/Slots` per provider and room | Live `GET /appointments` (extra safety) |
