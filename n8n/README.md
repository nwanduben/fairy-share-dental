# n8n — Appointment Confirmation Workflow

`fsd_appointment_confirmation.workflow.json` delivers a confirmation after a **verified** booking.

```
Backend send_confirmation ──POST (X-FSD-Secret)──► Webhook
  → Build Message (validate payload, build text + HTML)
  → Route by Channel ─┬─ email    → Gmail
                      ├─ sms      → Twilio
                      └─ telegram → Telegram bot
  → Respond to Backend {ok, sent, channel, error}
```

The backend decides *whether* and *what* to send: it re-verifies the appointment in Open Dental first.
n8n only delivers the message. Send failures are returned as `sent: false`, never as a crash, so the
agent can say "you're still booked; the office will follow up."

## Setup (about 10 minutes)
1. **Import.** In n8n: Workflows → Import from File → `fsd_appointment_confirmation.workflow.json`.
2. **Webhook security.** Open the *Verified Appointment Webhook* node → Credential → new **Header Auth**. The header name and value must match `N8N_WEBHOOK_HEADER` and `N8N_WEBHOOK_SECRET` in the backend `.env` (the value may include a `Bearer ` prefix):
   - Name: `X-FSD-Secret`
   - Value: the `N8N_WEBHOOK_SECRET` from the backend `.env`
3. **Telegram** (free, easiest for the demo):
   - In Telegram, message **@BotFather** → `/newbot` → copy the bot token.
   - In n8n, open *Send Telegram* → new Telegram credential → paste the token.
   - Send any message to your new bot. Then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id`.
   - Put that number in the backend `.env` as `TELEGRAM_CHAT_ID`.
4. **Email** (free):
   - Open *Send Email (Gmail)* → connect a Gmail account through OAuth.
   - Alternatively, swap the node for **Send Email (SMTP)**.
5. **WhatsApp** (free to test with Meta's Cloud API):
   - Go to developers.facebook.com → **Create App** → **Business** → add the **WhatsApp** product.
   - Meta gives you a **test phone number**, a **Phone number ID** and a temporary access token (24 hours; generate a
     permanent one via a System User later).
   - Under **API Setup**, add your own number to the recipient list and verify the code. Up to 5 test recipients.
   - **Message the test number from your phone first.** WhatsApp only allows free-form business messages inside a
     24-hour window after the user writes; outside it you need an approved message template.
   - In n8n, open *Send WhatsApp* → new WhatsApp credential → paste the access token. Put the Phone number ID in the
     node's **Phone Number ID** field.
   - Optionally set `WHATSAPP_NUMBER` in the backend `.env` as the fallback recipient.

6. **SMS** (optional; Twilio is paid):
   - Connect Twilio in *Send SMS (Twilio)*, and replace `from` with your Twilio number.
   - Real US SMS also requires A2P 10DLC registration.
   - If you skip Twilio, the agent should offer email or Telegram only.
7. **Activate** the workflow. Copy the **Production URL** of the webhook, e.g.
   `https://<your-n8n-host>/webhook/fsd-appointment-confirmation`, into the backend `.env` as
   `N8N_CONFIRMATION_WEBHOOK_URL`. Restart the backend; `/health` should show `"confirmations":"enabled"`.

## Important: Telegram can't message a phone number
A Telegram bot can only message people who have started a chat with it. Patients can't be reached by their phone number.
In this demo, "Telegram" sends to one configured chat (yours), standing in for the patient's phone.
For real patients, use SMS (Twilio) or email.

## Payload the backend sends
```json
{
  "event": "appointment.verified",
  "channel": "email|sms|whatsapp|telegram",
  "to": "address, +1XXXXXXXXXX, or chat id",
  "appointment_id": 53,
  "conversation_id": "...",
  "practice_name": "Fairy Share Dental",
  "practice_phone": "(214) 555-0142",
  "practice_city": "Dallas, TX",
  "patient_first_name": "Morgan",
  "appointment_type": "Cleaning",
  "spoken_time": "Thursday, October 8th at 8 AM",
  "date": "2026-10-08",
  "time": "08:00",
  "duration_minutes": 60,
  "provider_name": "Tina, our hygienist"
}
```
No date of birth, last name or Open Dental patient number is ever sent.
