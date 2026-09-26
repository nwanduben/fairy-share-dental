"""Create the Fairy Share Dental receptionist in ElevenLabs via the API.

    python elevenlabs/provision_agent.py --dry-run   # print what would be created
    python elevenlabs/provision_agent.py             # create secret, knowledge base, 6 tools, agent

Needs in .env:  ELEVENLABS_API_KEY, PUBLIC_BACKEND_URL (https tunnel/host), TOOL_SECRET
Optional:       ELEVENLABS_VOICE_ID, ELEVENLABS_LLM, ELEVENLABS_TTS_MODEL

Endpoints used (ElevenLabs API reference, checked 2026-09-22):
  POST /v1/convai/secrets              {type:"new", name, value} -> secret_id
  POST /v1/convai/knowledge-base/text  {text, name}              -> id
  POST /v1/convai/tools                {tool_config:{type:"webhook", ...}} -> id
  POST /v1/convai/agents/create        {name, conversation_config:{...}} -> agent_id
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / ".provisioned.json"
API = "https://api.elevenlabs.io"

APPOINTMENT_TYPES = ["new_patient_exam", "cleaning", "emergency_exam", "general_consultation", "cosmetic_consultation"]
CONV_ID = {"type": "string", "dynamic_variable": "system__conversation_id"}  # only one of description/dynamic_variable allowed


def s(desc: str, **kw) -> dict:
    return {"type": "string", "description": desc, **kw}


TOOLS = [
    {
        "name": "lookup_patient",
        "description": "Find an EXISTING Fairy Share Dental patient by exact first name, last name and date of birth. Only for callers who say they have visited before. Returns only a match status and a patient_ref.",
        "properties": {
            "first_name": s("Patient's first name"),
            "last_name": s("Patient's last name"),
            "date_of_birth": s("Date of birth in YYYY-MM-DD"),
            "phone": s("Phone number on file. Only send when a previous call returned 'multiple'."),
        },
        "required": ["first_name", "last_name", "date_of_birth"],
    },
    {
        "name": "get_appointments",
        "description": "List a verified patient's upcoming appointments to avoid duplicate bookings. Use after lookup_patient returned found.",
        "properties": {"patient_ref": s("Exact patient_ref from lookup_patient")},
        "required": ["patient_ref"],
    },
    {
        "name": "get_availability",
        "description": "Find real open times for an appointment type. Returns up to 3 options. Offer ONLY these exact times; they are not held or booked.",
        "properties": {
            "appointment_type": s("Appointment type key", enum=APPOINTMENT_TYPES),
            "date_from": s("First day to search, YYYY-MM-DD. Default today."),
            "date_to": s("Last day to search, YYYY-MM-DD. Default date_from + 6 days (max 21)."),
            "time_of_day": s("Preferred time of day", enum=["morning", "afternoon", "any"]),
            "is_new_patient": {"type": "boolean", "description": "true if the caller has never been to Fairy Share Dental"},
        },
        "required": ["appointment_type"],
    },
    {
        "name": "create_appointment",
        "timeout": 45,
        "description": "Book the specific offered time the caller clearly agreed to. The result is NOT a confirmation: you must call verify_appointment next.",
        "properties": {
            "slot_id": s("Exact slot_id of the chosen option from get_availability"),
            "patient_ref": s("Exact patient_ref for an existing patient"),
            "new_patient": {
                "type": "object",
                "description": "Only for NEW patients (instead of patient_ref)",
                "properties": {
                    "first_name": s("First name"),
                    "last_name": s("Last name"),
                    "date_of_birth": s("Date of birth YYYY-MM-DD"),
                    "phone": s("Callback phone number"),
                },
                "required": ["first_name", "last_name", "date_of_birth", "phone"],
            },
        },
        "required": ["slot_id"],
    },
    {
        "name": "verify_appointment",
        "timeout": 45,
        "description": "Confirm the appointment exists in the practice schedule with the right details. Only after verified=true may you tell the caller they are booked.",
        "properties": {"appointment_ref": s("Exact appointment_ref from create_appointment")},
        "required": ["appointment_ref"],
    },
    {
        "name": "send_confirmation",
        "timeout": 45,
        "description": "Send a written confirmation of a VERIFIED appointment by email, text (SMS), WhatsApp or Telegram. Only call after verify_appointment returned verified=true and the caller asked for a confirmation.",
        "properties": {
            "appointment_ref": s("Exact appointment_ref of the verified appointment"),
            "channel": s("Where to send it", enum=["email", "sms", "whatsapp", "telegram"]),
            "email": s("Email address, spelled back and confirmed by the caller (email only)"),
            "phone": s("Mobile number for sms or whatsapp. 10 digits for a US number; include the country code "
                       "for WhatsApp outside the US. Default to the caller's number if they agree."),
        },
        "required": ["appointment_ref", "channel"],
    },
]


def tool_payload(t: dict, backend: str, secret_id: str) -> dict:
    props = {**t["properties"], "conversation_id": CONV_ID}
    return {
        "tool_config": {
            "type": "webhook",
            "name": t["name"],
            "description": t["description"],
            "response_timeout_secs": t.get("timeout", 30),
            "api_schema": {
                "url": f"{backend.rstrip('/')}/tools/{t['name']}",
                "method": "POST",
                "request_headers": {"X-Tool-Secret": {"secret_id": secret_id}},
                "request_body_schema": {
                    "type": "object",
                    "description": f"Arguments for {t['name']}",
                    "properties": props,
                    "required": t["required"],
                },
            },
        }
    }


def system_prompt() -> str:
    text = (HERE / "SYSTEM_PROMPT.md").read_text()
    return text.split("\n---\n", 1)[1].strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="create again even if .provisioned.json exists")
    ap.add_argument("--update", action="store_true", help="push SYSTEM_PROMPT.md + LLM settings to the existing agent")
    ap.add_argument("--retarget", action="store_true", help="point existing tools at the current PUBLIC_BACKEND_URL (new tunnel)")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    backend = os.getenv("PUBLIC_BACKEND_URL", "")
    tool_secret = os.getenv("TOOL_SECRET", "")
    voice_id = (os.getenv("ELEVENLABS_VOICE_ID") or "cjVigY5qzO86Huf0OWal")
    llm = (os.getenv("ELEVENLABS_LLM") or "claude-haiku-4-5")
    tts_model = (os.getenv("ELEVENLABS_TTS_MODEL") or "eleven_flash_v2")

    missing = [n for n, v in (("PUBLIC_BACKEND_URL", backend), ("TOOL_SECRET", tool_secret)) if not v]
    if not args.dry_run and not api_key:
        missing.append("ELEVENLABS_API_KEY")
    if missing:
        print("Missing in .env:", ", ".join(missing))
        return 2
    if not backend.startswith("https://"):
        print("PUBLIC_BACKEND_URL must be https:// (ElevenLabs calls it from the internet)")
        return 2
    state: dict = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() and not args.force else {}
    if args.retarget:
        if not state.get("tool_ids"):
            print("Nothing to retarget: no tools in .provisioned.json.")
            return 1
        for name, tool_id in state["tool_ids"].items():
            t = next(t for t in TOOLS if t["name"] == name)
            r = httpx.patch(f"{API}/v1/convai/tools/{tool_id}", headers={"xi-api-key": api_key}, timeout=30,
                            json=tool_payload(t, backend, state["secret_id"]))
            if r.status_code >= 300:
                print(f"ERROR retarget {name} -> {r.status_code}: {r.text[:400]}")
                return 1
            print(f"retargeted: {name}")
        state["backend"] = backend
        STATE_FILE.write_text(json.dumps(state, indent=2))
        print(f"All tools now point at {backend}")
        return 0
    if args.update:
        if not state.get("agent_id"):
            print("Nothing to update: no agent in .provisioned.json. Run without --update first.")
            return 1
        r = httpx.patch(
            f"{API}/v1/convai/agents/{state['agent_id']}",
            headers={"xi-api-key": api_key},
            timeout=30,
            json={"conversation_config": {"agent": {
                "first_message": "Thank you for calling Fairy Share Dental. How can I help you today?",
                "prompt": {"prompt": system_prompt(), "llm": llm, "temperature": 0.3},
            }}},
        )
        if r.status_code >= 300:
            print(f"ERROR update -> {r.status_code}: {r.text[:800]}")
            return 1
        print(f"Agent {state['agent_id']} updated (llm={llm}).")
        return 0
    if state.get("agent_id") and not args.dry_run:
        print(f"Already provisioned: agent {state['agent_id']} (see {STATE_FILE.name}). Use --force to create a new copy.")
        return 1

    agent_body = lambda tool_ids, kb: {  # noqa: E731
        "name": "Fairy Share Dental - Receptionist (demo)",
        "conversation_config": {
            "agent": {
                "first_message": "Thank you for calling Fairy Share Dental. How can I help you today?",
                "language": "en",
                "prompt": {
                    "prompt": system_prompt(),
                    "llm": llm,
                    "temperature": 0.3,
                    "tool_ids": tool_ids,
                    "knowledge_base": kb,
                },
            },
            "tts": {"voice_id": voice_id, "model_id": tts_model},
        },
    }

    if args.dry_run:
        print(json.dumps([tool_payload(t, backend, "<secret_id>") for t in TOOLS], indent=2)[:3000], "...")
        print(json.dumps(agent_body(["<tool ids>"], [{"type": "text", "name": "Fairy Share Dental FAQ", "id": "<kb id>", "usage_mode": "auto"}]), indent=2)[:1500], "...")
        return 0

    http = httpx.Client(base_url=API, headers={"xi-api-key": api_key}, timeout=30)

    def call(path: str, body: dict) -> dict:
        r = http.post(path, json=body)
        if r.status_code >= 300:
            print(f"ERROR {path} -> {r.status_code}: {r.text[:800]}")
            sys.exit(1)
        return r.json()

    def save() -> None:
        STATE_FILE.write_text(json.dumps(state, indent=2))

    if "secret_id" not in state:
        state["secret_id"] = call("/v1/convai/secrets", {"type": "new", "name": "fsd_tool_secret", "value": tool_secret})["secret_id"]
        save()
        print("secret created")
    else:
        print("secret reused")
    if "knowledge_base_id" not in state:
        kb = call("/v1/convai/knowledge-base/text", {"name": "Fairy Share Dental FAQ", "text": (HERE / "KNOWLEDGE_BASE.md").read_text()})
        state["knowledge_base_id"] = kb["id"]
        save()
        print("knowledge base created")
    else:
        print("knowledge base reused")
    state.setdefault("tool_ids", {})
    for t in TOOLS:
        if t["name"] in state["tool_ids"]:
            continue
        state["tool_ids"][t["name"]] = call("/v1/convai/tools", tool_payload(t, backend, state["secret_id"]))["id"]
        save()
        print(f"tool created: {t['name']}")
    agent = call("/v1/convai/agents/create", agent_body(
        list(state["tool_ids"].values()),
        [{"type": "text", "name": "Fairy Share Dental FAQ", "id": state["knowledge_base_id"], "usage_mode": "auto"}],
    ))
    state["agent_id"] = agent["agent_id"]
    state["backend"] = backend
    save()
    print(f"\nAgent created: {state['agent_id']}")
    print(f"Open: https://elevenlabs.io/app/agents/agents/{state['agent_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
