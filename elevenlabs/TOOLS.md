# ElevenLabs Tools (Webhook / server tools)

All six tools are configured the same way in the ElevenLabs agent:

| Setting | Value |
|---|---|
| Type | Webhook |
| Method | `POST` |
| URL | `https://<your-public-backend>/tools/<tool_name>` |
| Header | `X-Tool-Secret: {{secret__tool_secret}}` (secret dynamic variable, never sent to the LLM) |
| Body param `conversation_id` | Value type **Dynamic variable** → `system__conversation_id` |

Responses are always HTTP 200 JSON with `ok` and `agent_instruction`. The agent must follow `agent_instruction`.

---

## 1. `lookup_patient`
**Description:** Find an existing Fairy Share Dental patient by exact first name, last name and date of birth. Use only for callers who say they have been to the practice before. Returns only a match status, never record details.

| Body param | Type | Required | Description |
|---|---|---|---|
| first_name | string | yes | Patient's legal first name |
| last_name | string | yes | Patient's last name |
| date_of_birth | string | yes | Date of birth, `YYYY-MM-DD` |
| phone | string | no | Phone number on file. Only needed when a previous call returned `multiple` |
| conversation_id | string | no | Dynamic variable `system__conversation_id` |

**Returns:** `status`: `found` (+ `patient_ref`) \| `multiple` \| `not_found`.

## 2. `get_appointments`
**Description:** List a verified patient's upcoming appointments, to avoid booking a duplicate. Call it after `lookup_patient` returns `found`, if the caller may already have something scheduled.

| Body param | Type | Required | Description |
|---|---|---|---|
| patient_ref | string | yes | Exact `patient_ref` from `lookup_patient` |
| conversation_id | string | no | `system__conversation_id` |

**Returns:** `upcoming_appointments[]`, each `{appointment_type, spoken_time}`.

## 3. `get_availability`
**Description:** Find real open times at Fairy Share Dental for an appointment type. Returns up to 3 options. Offer only these exact times. They are not held or booked.

| Body param | Type | Required | Description |
|---|---|---|---|
| appointment_type | string (enum) | yes | One of `new_patient_exam`, `cleaning`, `emergency_exam`, `general_consultation`, `cosmetic_consultation` |
| date_from | string | no | First day to search, `YYYY-MM-DD`. Default: today |
| date_to | string | no | Last day to search, `YYYY-MM-DD`. Default: date_from + 6 days (maximum 21 days) |
| time_of_day | string (enum) | no | `morning`, `afternoon`, or `any` (default) |
| is_new_patient | boolean | no | `true` if the caller is new to the practice. Required for `new_patient_exam` |
| conversation_id | string | no | `system__conversation_id` |

**Returns:** `options[]`, each `{slot_id, spoken_time, provider_name}`, plus `duration_minutes`, `searched_from`, `searched_to`.
Each `slot_id` expires after 15 minutes.

## 4. `create_appointment`
**Description:** Book the time the caller chose. Call only after the caller clearly agrees to a specific offered time. The result is NOT a confirmation. You must call `verify_appointment` next.

| Body param | Type | Required | Description |
|---|---|---|---|
| slot_id | string | yes | Exact `slot_id` of the chosen option from `get_availability` |
| patient_ref | string | one of | Exact `patient_ref` for an existing patient |
| new_patient | object | one of | For new patients: `{first_name, last_name, date_of_birth (YYYY-MM-DD), phone}` |
| conversation_id | string | no | `system__conversation_id` (used as the idempotency/audit key) |

**Returns:**
- `status`: `created_pending_verification` (+ `appointment_ref`), `slot_taken`, `slot_expired`, `patient_ambiguous`, `type_for_new_patients_only`, or an error.
- For new patients, also returns `patient_ref`.

## 5. `verify_appointment`
**Description:** Confirm that the appointment really exists in the practice schedule with the right patient, time, room, provider and length. Only after this returns `verified: true` may you tell the caller they are booked.

| Body param | Type | Required | Description |
|---|---|---|---|
| appointment_ref | string | yes | Exact `appointment_ref` from `create_appointment` |
| conversation_id | string | no | `system__conversation_id` |

**Returns:** `verified: true` with `appointment {appointment_type, spoken_time, date, time, duration_minutes, provider_name}`, or `verified: false` with a `reason`.

## 6. `send_confirmation`
**Description:** Send a written confirmation of a VERIFIED appointment by email, text (SMS) or Telegram. Call only after `verify_appointment` returned `verified: true` and the caller asked for a confirmation.

| Body param | Type | Required | Description |
|---|---|---|---|
| appointment_ref | string | yes | Exact `appointment_ref` of the verified appointment |
| channel | string (enum) | yes | `email`, `sms`, or `telegram` |
| email | string | email only | Address spelled back and confirmed by the caller |
| phone | string | sms only | 10-digit US mobile number. Default to the caller's number if they agree |
| conversation_id | string | no | `system__conversation_id` |

**Returns:** `sent: true`, or `sent: false` with a `reason`:
- `invalid_email`, `invalid_phone`, `appointment_not_verified`, `confirmations_not_configured`, or a delivery failure.

The backend re-verifies the appointment in Open Dental before sending. Delivery is done by the n8n workflow (see `n8n/README.md`).

---

### Example: happy path (existing patient)
```
lookup_patient      → {"status":"found","patient_ref":"pat_…"}
get_availability    → {"options":[{"slot_id":"slot_…","spoken_time":"Tuesday, October 6th at 8 AM","provider_name":"Tina, our hygienist"}, …]}
create_appointment  → {"status":"created_pending_verification","appointment_ref":"apt_…","agent_instruction":"NOT CONFIRMED YET…"}
verify_appointment  → {"verified":true,"appointment":{"appointment_type":"Cleaning","spoken_time":"Tuesday, October 6th at 8 AM",…}}
```
