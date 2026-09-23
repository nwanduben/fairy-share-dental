# ElevenLabs Agent Setup

> The ElevenLabs UI changes often. Field names below were current when written. Check them against
> https://elevenlabs.io/docs/agents-platform (server tools and dynamic variables pages).

## 1. Run the backend publicly
```bash
cd fairy-share-dental
.venv/bin/uvicorn app.main:create_app --factory --app-dir backend --port 8000
```
Then expose it over HTTPS with a tunnel (either works):
```bash
cloudflared tunnel --url http://localhost:8000
```
```bash
ngrok http 8000
```
Check it with `GET https://<tunnel>/health`. It should return `{"status":"ok","mode":"live",...}`.

## 2. Fastest path: provision with one command
Fill in `ELEVENLABS_API_KEY` and `PUBLIC_BACKEND_URL` in `.env`. Optionally set `ELEVENLABS_VOICE_ID` and `ELEVENLABS_LLM`. Then preview what will be created:
```bash
.venv/bin/python elevenlabs/provision_agent.py --dry-run
```
Then create it for real:
```bash
.venv/bin/python elevenlabs/provision_agent.py
```
This creates:
- the tool secret
- the knowledge base document
- all 6 webhook tools, with `conversation_id` bound to `system__conversation_id` and a 20-second timeout
- the agent itself

The IDs are saved to `elevenlabs/.provisioned.json`. If the API rejects the LLM name, set `ELEVENLABS_LLM` to a model listed in your dashboard and run it again.

Steps 3–5 below are the manual alternative.

## 2b. Create the agent manually
1. ElevenLabs → **Agents** → **Create agent** → Blank.
2. **First message:** `Thank you for calling Fairy Share Dental. How can I help you today?`
3. **System prompt:** paste the prompt from `SYSTEM_PROMPT.md`.
4. **LLM:** a fast model with strong tool calling. Keep temperature low (about 0.3).
5. **Voice:** a warm, natural English (US) voice. Enable the Turbo/Flash model for low latency.
6. **Language:** English.

## 3. Add the tool secret
- Add a secret dynamic variable `secret__tool_secret` whose value equals `TOOL_SECRET` in your `.env`.
  Variables prefixed with `secret__` are only used in headers and are never sent to the LLM.
- Alternatively, create a workspace auth connection of type "custom header" (`X-Tool-Secret`).

## 4. Add the six webhook tools
Create each tool from `TOOLS.md`: `lookup_patient`, `get_appointments`, `get_availability`, `create_appointment`, `verify_appointment`, `send_confirmation`.
- **Method:** POST. **URL:** `https://<tunnel>/tools/<name>`.
- **Header:** `X-Tool-Secret` = `{{secret__tool_secret}}`.
- **Body parameters:** exactly as listed in `TOOLS.md`, with the descriptions copied in (the LLM reads them).
- **`conversation_id`:** value type **Dynamic variable** = `system__conversation_id`.
- **`appointment_type` and `time_of_day`:** use enums.
- **Timeout:** set the tool response timeout to **at least 20 seconds**. Open Dental's Remote API allows 1 request per second, and `create_appointment` makes 3–5 requests.
- **Speaking during tool calls:** enable pre-tool speech or a tool-call sound if available, so the agent keeps talking while a tool runs.

## 5. Knowledge base
Upload `KNOWLEDGE_BASE.md`.

## 6. Test
Use the ElevenLabs **Test agent** widget first, then a phone number (Twilio or SIP) if you want real calls.
Scripted scenarios are in `docs/TEST_SCRIPTS.md`.

## Not HIPAA compliant
This demo uses synthetic data only. A real deployment would need Business Associate Agreements with
Open Dental, ElevenLabs, the hosting provider and the telephony provider, plus access controls,
audit logging and data-retention settings.
