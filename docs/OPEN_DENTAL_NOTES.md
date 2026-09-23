# Open Dental — Verified Notes (2026-09-22)

Sources: the official Open Dental API docs. Links are at the bottom.
"Live" rows were observed against the Open Dental public developer test database.

## Access
- **Base URL:** `https://api.opendental.com/api/v1` (Remote API). Requires the office's **eConnector** to be running.
- **Auth header:** `Authorization: ODFHIR {DeveloperKey}/{CustomerKey}`.
- **Test key:** a public test key is published on the API Setup page. Live: ✅ works (read and write).
- **Production keys:**
  - Developer key: from `vendor.relations@opendental.com`. A BAA is required.
  - Customer key: enabled per practice.
  - Monthly fee per location, paid by the practice: Read All is free; write tiers are $15, $30 or $35. Confirm which tier covers Patients + Appointments writes.
- **Throttle (Remote API):**
  - 1 request per second for keys with write permissions.
  - 1 request per 5 seconds for keys with Read All only.
  - Over the limit returns 429 with `Retry-After`.
  - Our adapter serializes requests at 1.1 seconds apart and retries 429s.
- **Limits:** 100 items per page (paged with `Offset`). Requests time out after 60 seconds (504).
- **Datetimes:** `yyyy-MM-dd HH:mm:ss`, with no timezone. We treat them as practice-local (America/Chicago).

## Endpoints used (all documented)
| Purpose | Endpoint | Live result |
|---|---|---|
| Operatories | `GET /operatories` | ✅ Ops 1, 2, 3, 5, 6 visible; Op 4 hidden |
| Providers | `GET /providers` | ✅ 1 Albert (DDS), 2 Tina (hygienist), 3 Lexington (DDS), 4 Bruce (hygienist) |
| Appointment types | `GET /appointmenttypes` | Only "WebSched New Patient Default" `//XX//` |
| Patient search | `GET /patients/Simple?LName&FName&Birthdate` | ✅ |
| Create patient | `POST /patients` (requires LName, FName) | ✅ PatNum 22 (synthetic "Morgan Fsdtest") |
| Existing appointments | `GET /appointments?dateStart&dateEnd[&PatNum]` | ✅ |
| Create appointment | `POST /appointments` (requires PatNum, Op, AptDateTime; we also send Pattern, ProvNum, ProvHyg, IsHygiene, IsNewPatient, Note) | ✅ AptNum 53 |
| Read back | `GET /appointments/{AptNum}` | ✅ All fields matched → verified |
| Slots | `GET /appointments/Slots` | `[]`: there are no provider schedules |
| Schedules | `GET /schedules?dateStart&dateEnd` | `[]` |

## Key findings
1. **Slots can't be used on the test database.** It has no provider schedules, and schedules can't be created through the API.
   - We therefore use `availability_source: config`: office hours from config, minus live Open Dental appointments.
   - Switch to `slots` for a practice whose Open Dental schedules are set up.
2. **The docs don't mention double-booking protection on `POST /appointments`.** We handle it ourselves:
   - Re-check the slot right before POSTing.
   - After POSTing, check for a clash in the same room or with the same provider. The lower AptNum wins.
3. **Appointment length comes from `Pattern`** (5 minutes per character). We always send it. `AppointmentTypeNum` is optional per type.
4. **The test server clock runs on US Pacific time.** Open Dental datetimes have no timezone, so we never convert them.
5. **Provider responses include SSN, license and NPI fields,** even in test data. The adapter drops everything except ProvNum, Abbr and the hidden/secondary flags.

## The test database resets
Observed 2026-09-23: patients and appointments created the day before (PatNum 22/26-30, AptNum 53) were gone.
The public developer test database is periodically wiped, so:
- Re-run `backend/scripts/seed_demo_patients.py` before a demo to recreate the demo patients.
- Never rely on anything booked there persisting overnight.
- A practice's own Open Dental database does not behave this way.

## Leftover test data
- The write test left synthetic patient **Morgan Fsdtest** (PatNum 22).
- It also left a cleaning, **AptNum 53**, on 2026-10-08 at 08:00 in Op 5.
- Cancellation is out of scope for Milestone 1, so it wasn't removed.

## Still unverified
- Whether the test database is shared with other developers (it probably is).
- The ElevenLabs webhook timeout default and whether ElevenLabs offers a BAA.

## Sources
- https://www.opendental.com/site/apisetup.html
- https://www.opendental.com/site/apiimplementation.html
- https://www.opendental.com/site/apipermissions.html
- https://www.opendental.com/site/apiappointments.html
- https://www.opendental.com/site/apipatients.html
- https://www.opendental.com/site/apiappointmenttypes.html
- https://www.opendental.com/site/apischedules.html
- https://www.opendental.com/site/apioperatories.html
- https://www.opendental.com/site/apiproviders.html
