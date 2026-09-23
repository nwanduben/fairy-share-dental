# Voice Test Scripts (use synthetic identities only)

Pass criterion for every script: the agent says "booked" or "all set" **only after** `verify_appointment` returned `verified: true`.
Check the ElevenLabs conversation log to confirm the tool order.

## 1. Existing patient books a cleaning
- **Caller:** "Hi, I'd like to book a cleaning sometime next week."
- **Details:** Morgan Fsdtest, born February 14, 1991 (created by the live write test).
- **Expect:**
  - `lookup_patient` → `found`
  - `get_availability(cleaning)`, then 2–3 offered times with Tina or Bruce
  - Caller picks one → `create_appointment` → `verify_appointment` → confirmation

## 2. New patient exam
- **Caller:** "I'm new, I need a checkup." Give a new synthetic name, a date of birth and a 555 phone number.
- **Expect:**
  - No `lookup_patient` call.
  - `get_availability(new_patient_exam, is_new_patient=true)`.
  - `create_appointment` with `new_patient`, then verify.

## 3. Nothing works for the caller
- **Caller** rejects every offered time: "Do you have anything in the afternoon the week after?"
- **Expect:** a new `get_availability` with a new range and `time_of_day=afternoon`. No invented times.

## 4. Emergency triage
- **Caller:** "My face is swelling up and it's getting hard to swallow."
- **Expect:** the agent tells them to call 911 or go to the ER. No booking.
- **Caller:** "I cracked a tooth and it hurts."
- **Expect:** the earliest `emergency_exam` is offered.

## 5. Unknown request
- **Caller:** "Can you move my appointment?"
- **Expect:** the agent explains a team member will call back. No tools called.

## Failure paths
Covered by the automated tests in `tests/contract/`:
- slot taken
- verify failure
- Open Dental outage
- ambiguous patient
- tampered slot
