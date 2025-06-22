# /x-dial/backend/main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from uuid import uuid4
from datetime import datetime
import logging
from dotenv import load_dotenv
load_dotenv()
import os
import re
import json
import requests
import openai
from fastapi import Body
from firebase_client import update_session_status, get_session_status, get_session_from_firebase, delete_session
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from tree import update_tree_branch, save_tree_snapshot
import asyncio
import urllib.parse
import whisper
import subprocess


openai.api_key = os.getenv("OPENAI_API_KEY")
twilio_client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")


# Set this once before any whisper.transcribe call
os.environ["PATH"] += r";C:\ffmpeg\bin"

import time

def wait_for_valid_recording(url: str, path: str, max_retries: int = 3, delay: float = 2.5) -> bool:
    import requests
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url)
            if response.ok and len(response.content) > 5000:  # ✅ 5KB+ is usually safe
                with open(path, "wb") as f:
                    f.write(response.content)
                return True
            logging.warning(f"[RETRY] Attempt {attempt}: audio too small ({len(response.content)} bytes)")
        except Exception as e:
            logging.warning(f"[RETRY] Attempt {attempt} failed: {e}")
        time.sleep(delay)
    return False


OPEN_ENDED_TRIGGERS = [
    "how can i help",
    "how may i help",
    "how can we assist",
    "how may we assist",
    "what can i do for you",
    "how we can assist",
    "tell me how i can help",
    "please state your request",
    "tell me what you're calling about",
    "in a few words, tell me what you're calling about",
    "how can i assist you today",
    "please describe your issue",
    "what are you calling about",
]

import re

OPEN_ENDED_PATTERNS = [
    r"(in a few words|briefly),?\s?(what.*you.*calling about|how can I help|state.*your.*reason)",
    r"(say something like|you can say).{0,60}(change flight|check.*status|new reservation|agent)",
    r"how can (i|we) help.*\?",
    r"what can (i|we) do for you",
    r"tell me.*(you.*calling about|what.*need)",
    r"(say|please say) (your|the) reason.*",
]

OPEN_ENDED_KEYWORDS = [
    "what you're calling about",
    "how can I help",
    "how can we help",
    "in a few words",
    "you can say things like",
    "say your reason",
    "briefly tell me",
    "state your request"
]

USE_GPT_CLASSIFIER = False  # Set to True if you want to use GPT as backup

def get_node_by_path(tree, path):
    parts = path.split(".")
    node = tree
    for part in parts[1:]:  # Skip root
        if "children" in node and part in node["children"]:
            node = node["children"][part]
        else:
            return None
    return node

def heard_open_ended_prompt(transcript: str, session_id=None) -> bool:
    transcript = transcript.lower().strip()
    
    # ✅ Regex pattern match
    for pattern in OPEN_ENDED_PATTERNS:
        if re.search(pattern, transcript):
            logging.info(f"[PROMPT DETECTION] Regex match → {pattern}")
            return True

    # ✅ Keyword heuristic fallback
    matches = sum(1 for k in OPEN_ENDED_KEYWORDS if k in transcript)
    if matches >= 2:
        logging.info(f"[PROMPT DETECTION] Keyword match count: {matches}")
        return True

    # 🧠 GPT fallback (optional, slower)
    if USE_GPT_CLASSIFIER:
        from openai import OpenAI  # assumes you're using the `client` var elsewhere
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": (
                    "You are helping detect when a phone system has finished its introduction and is now ready to hear the user's reason for calling.\n"
                    "Return 'true' if the transcript contains a prompt asking for input, like 'how can I help you?' or 'tell me what you're calling about'.\n"
                    "Only return 'true' or 'false'."
                )},
                {"role": "user", "content": transcript}
            ]
        )
        verdict = response.choices[0].message.content.strip().lower()
        logging.info(f"[GPT PROMPT DETECTION] Transcript → {verdict}")
        return "true" in verdict

    return False


model = whisper.load_model("base")  # or "medium" for more accuracy

def detect_prompt_time(audio_path: str) -> int:
    result = model.transcribe(audio_path, word_timestamps=True)
    segments = result.get("segments", [])
    
    for segment in segments:
        text = segment["text"].lower()
        if any(trigger in text for trigger in OPEN_ENDED_TRIGGERS):
            start = int(segment["start"])
            logging.info(f"[AUTO-TIMING] Prompt trigger found at {start} seconds → {text}")
            return start + 2  # Add 2s buffer

    logging.warning("[AUTO-TIMING] No trigger prompt found — defaulting to 35s")
    return 35

def heard_open_ended_prompt(speech: str) -> bool:
    return any(phrase in speech.lower() for phrase in OPEN_ENDED_TRIGGERS)


def looks_like_menu(speech_text: str) -> bool:
    speech_text = speech_text.lower()
    digit_matches = re.findall(r"press\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|zero)", speech_text)
    logging.info(f"[MENU DETECTION] Found digit-like phrases: {digit_matches}")
    return len(digit_matches) >= 2



def safe_json_parse(raw: str):
    import json
    import re
    try:
        # Remove ```json or ``` if present
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.IGNORECASE).strip()
        return json.loads(cleaned)
    except Exception as e:
        logging.warning(f"[GPT FIX FAILED] {e}")
        return {}

        
def should_say_query_now(speech: str) -> bool:
    """
    Ask GPT if this is a good time to speak the user’s original query.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a smart IVR strategist. A bot is navigating a phone call system. "
                        "The bot can either press buttons (DTMF) or say a natural language query like "
                        "'I need to change my Delta flight.' Based on the following IVR transcript, "
                        "decide if now is a good time to speak the query.\n\n"
                        "Respond ONLY with JSON: { \"say_query_now\": true } if the system is waiting "
                        "for user input, or { \"say_query_now\": false } if it's still playing audio or "
                        "handling something else."
                    )
                },
                {"role": "user", "content": speech}
            ]
        )
        parsed = safe_json_parse(response.choices[0].message.content.strip())
        return parsed.get("say_query_now", False)
    except Exception as e:
        logging.warning(f"[SAY QUERY DECISION ERROR] {e}")
        return False


def classify_ivr_type(transcribed_text: str, user_query: str = "") -> str:
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an IVR type classifier. Based on the transcript provided, classify the type of phone system interaction. "
                    "Respond ONLY with valid JSON. Never say anything else. "
                    "Return one of: 'menu', 'open-ended', 'confirmation', or 'repeat'.\n\n"
                    "Example: {\"type\": \"menu\"}"
                )

            }
        ]

        # Optionally include user query for context
        if user_query:
            messages.append({"role": "user", "content": f"User query: {user_query}"})

        messages.append({"role": "user", "content": transcribed_text})

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages
        )
        raw = response.choices[0].message.content.strip()
        logging.info(f"[CLASSIFIER RAW RESPONSE] {raw}")

        parsed = safe_json_parse(raw)

        ivr_type = parsed.get("type")
        if ivr_type in ["menu", "open-ended", "confirmation", "repeat"]:
            return ivr_type

        logging.warning(f"[CLASSIFIER FALLBACK] Invalid or missing type → {ivr_type}. Defaulting to 'unknown'")
        return "unknown"

    except Exception as e:
        logging.warning(f"[CLASSIFIER ERROR] {e}")
        return "unknown"



app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

session_store = {}
logging.basicConfig(level=logging.INFO)

class ReconRequest(BaseModel):
    query: str
    user_id: str

class SessionInitResponse(BaseModel):
    session_id: str
    status: str
    created_at: str

client = openai.OpenAI()

def get_ngrok_url():
    try:
        res = requests.get("http://127.0.0.1:4040/api/tunnels")
        public_url = res.json()["tunnels"][0]["public_url"]
        return public_url
    except Exception as e:
        logging.error(f"[NGROK ERROR] {e}")
        return None

def initiate_twilio_call(to_number: str = None, session_id: str = "", say_query: bool = False):
    ngrok_url = get_ngrok_url()
    if not ngrok_url:
        raise RuntimeError("Ngrok URL could not be retrieved")

    session = session_store.get(session_id)
    if not session:
        raise RuntimeError(f"No session found for ID {session_id}")

    if not to_number:
        to_number = session.get("resolved_number")
        if not to_number:
            raise RuntimeError("No destination phone number provided or found in session")

    params = {
        "session_id": session_id,
    }
    if say_query:
        params["say_query"] = "true"

    full_url = f"{ngrok_url}/twilio/crawler-entry?{urllib.parse.urlencode(params)}"

    logging.info(f"[INITIATE CALL] SID: {session_id} | say_query={say_query} | URL: {full_url}")

    call = twilio_client.calls.create(
        to=to_number,
        from_=FROM_NUMBER,
        url=full_url,
        record=True,
        recording_channels="mono",
        recording_status_callback=f"{get_ngrok_url()}/twilio/recording-status",
        recording_status_callback_method="POST",
        recording_status_callback_event=["completed"],

        # 🔗 Persist the CallSid → session_id link
        status_callback=f"{get_ngrok_url()}/twilio/status-callback?session_id={session_id}",
        status_callback_method="POST"
    )
    return call.sid








def get_phone_number_from_query(query: str) -> str:
    known_numbers = {
        "southwest airlines": "1-800-435-9792",
        "delta airlines": "1-800-221-1212",
        "american airlines": "1-800-433-7300",
        "parish medical center in titusville": "1-321-268-6111"
    }
    for name, number in known_numbers.items():
        if name in query.lower():
            return number
    raise ValueError("No known number for this query. Integrate live lookup.")

def generate_tree_from_query(query: str):
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a phone tree generator. Only respond with a JSON dictionary using numbered keys. "
                        "Each node must include:\n"
                        "- key (string)\n"
                        "- label (string)\n"
                        "- selected (boolean, default false)\n"
                        "- children (object, can be empty)\n\n"
                        "Return only valid JSON. Do not wrap in markdown or explain anything."
                    )
                },
                {"role": "user", "content": f"Convert this into a phone tree: {query}"}
            ]
        )
        content = response.choices[0].message.content.strip()
        logging.info(f"[TREE RAW GPT OUTPUT] {repr(content)}")

        # ✅ Clean up markdown-style wrapping
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "").strip()

        tree = safe_json_parse(content)
        if not isinstance(tree, dict):
            logging.warning(f"[TREE PARSE FAILED] Got non-dict output: {tree}")
            return {}

        return tree

    except Exception as e:
        logging.error(f"[GPT TREE PARSE ERROR] {e}")
        return {}



@app.post("/start-recon")
async def start_recon(request: ReconRequest):
    session_id = str(uuid4())
    timestamp = datetime.utcnow().isoformat()
    tree = tree = {}
    phone_number = get_phone_number_from_query(request.query)

    session = {
        "user_id": request.user_id,
        "query": request.query,
        "created_at": timestamp,
        "status": "initializing",
        "path": "root",
        "tree": tree,
        "resolved_number": phone_number,
        "speech_history": [],
        "last_menu": {},
        "pending_digits": [],
        "menu_repeat_count": 0,
        "tree_path_stack": [],
    }
    session_store[session_id] = session

    logging.info(f"[LOOKUP] Resolved phone number: {phone_number}")
    logging.info(f"[SESSION CREATED] ID: {session_id} for query: '{request.query}'")
    logging.info(f"[FIREBASE UPDATE] Session Tree: {json.dumps(session.get('tree', {}), indent=2)[:1000]}")


    update_session_status(session_id, {
        "status": "initializing",
        "query": request.query,
        "created_at": timestamp,
        "tree": tree
    })

    return {
        "session_id": session_id,
        "status": "initializing",
        "created_at": timestamp,
        "query": request.query,
        "tree": tree,
        "resolved_number": phone_number
    }

@app.post("/twilio/status-callback")
async def status_callback(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid")
    session_id = request.query_params.get("session_id")

    if not session_id:
        logging.warning("[STATUS CALLBACK] Missing session_id in callback")
        return Response(status_code=400)

    session = get_session_status(session_id)
    if not session:
        logging.warning(f"[STATUS CALLBACK] No session found for session_id: {session_id}")
        return Response(status_code=404)

    session["twilio_call_sid"] = call_sid
    update_session_status(session_id, session)
    logging.info(f"[CALL LINKED] CallSid {call_sid} now mapped to session {session_id}")
    return Response(status_code=204)


@app.post("/start-crawl")
async def start_crawl(data: dict = Body(...)):
    number = data.get("phone_number")
    session_id = data.get("session_id")
    existing_session = session_store.get(session_id) or get_session_status(session_id) or {}
    query = data.get("query") or existing_session.get("query", "Hello. Please route this call.")

    if not number or not session_id:
        raise HTTPException(status_code=400, detail="Missing phone number or session ID")

    # 🧠 Prime session memory for GPT timing + IVR tracking
    # Preserve full session, just update runtime keys
    session_store[session_id] = {
        **existing_session,
        "resolved_number": existing_session.get("resolved_number"), 
        "query": query,
        "status": "starting",
        "path": "root",
        "pending_digits": [],
        "reroute_count": 0,
        "retry_attempts": 0,
        "should_check_speech": True,
        "speech_history": [],
        "last_menu": {},
        "pending_digits": [],
        "menu_repeat_count": 0,
        "tree_path_stack": [],
            }

    try:
        logging.info(f"[TRACE CALL] Recalling with say_query=False for session: {session_id}")
        call_sid = initiate_twilio_call(number, session_id, say_query=False)
        logging.info(f"[CRAWLER STARTED] Calling {number} | SID: {call_sid}")
        return {"status": "calling", "call_sid": call_sid}
    except Exception as e:
        logging.error(f"[CRAWLER ERROR] Failed to initiate call: {e}")
        raise HTTPException(status_code=500, detail="Twilio call failed to start.")



@app.post("/twilio/voice")
async def handle_twilio_voice(request: Request):
    vr = VoiceResponse()
    gather = Gather(
        input='speech dtmf',
        timeout=90,
        num_digits=1,
        action='/twilio/gather',
        method='POST'
    )
    gather.say("Welcome to X Dial: now powered by AI-fueled wizardry and Twilio teleportation magic. Your call is being routed through a phone tree so smart, it could probably do your taxes. Press 1 if you want to talk business, 2 if you want to celebrate Bryan’s genius. Or just stay on the line and bask in the glow of this badass upgrade. Let’s gooo!")
    vr.append(gather)
    vr.say("We did not receive input. Goodbye.")
    vr.hangup()
    return Response(content=str(vr), media_type="application/xml")

from fastapi import Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather

@app.post("/twilio/crawler-entry")
async def crawler_entry(request: Request):
    session_id = request.query_params.get("session_id")
    digit = request.query_params.get("digit")
    say_query_flag = request.query_params.get("say_query") == "true"

    session = session_store.get(session_id) or get_session_status(session_id)
    session.setdefault("speech_history", [])
    session.setdefault("last_menu", {})
    session.setdefault("pending_digits", [])
    session.setdefault("menu_repeat_count", 0)
    session.setdefault("tree_path_stack", [])

    if not session:
        logging.warning(f"[FALLBACK] No session found for {session_id}")
        vr = VoiceResponse()
        vr.say("We could not find your session. Goodbye.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    user_query = session.get("query", "Hello. Please route this call.")
    logging.info(f"[CRAWLER ENTRY] SID={session_id} | digit={digit} | say_query_flag={say_query_flag} | user_query={user_query}")

    vr = VoiceResponse()

    # 🧠 Digit-based follow-up
    if digit:
        logging.info(f"[CRAWLER ENTRY] Pressed digit: {digit}")
        gather = Gather(
            input='speech dtmf',
            timeout=90,
            speech_timeout='auto',
            action=f'/twilio/crawler-branch?session_id={session_id}',
            method='POST'
        )
        gather.say("Please hold while we gather the IVR options...")
        vr.append(gather)
        vr.record(
            maxLength=90,
            playBeep=False
        )
        return Response(content=str(vr), media_type="application/xml")

    # 🎙️ Say the rephrased user query
    elif say_query_flag:
        try:
            gpt_response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a voice assistant helping users navigate automated phone menus (IVR systems).\n"
                            "Compress the user's request into a short, action-oriented phrase the system will understand.\n"
                            "- Use 2–5 words max\n"
                            "- Cut filler like 'please' or 'I need'\n"
                            "- Output only the command\n"
                            "Example: 'Can I change my flight?' → 'Change flight'\n"
                        )
                    },
                    {"role": "user", "content": user_query}
                ]
            )
            query_to_speak = gpt_response.choices[0].message.content.strip()
            logging.info(f"[QUERY REPHRASED] → {query_to_speak}")
        except Exception as e:
            logging.warning(f"[GPT REPHRASE FAIL] Falling back to raw query: {e}")
            query_to_speak = user_query

        logging.info("[CRAWLER ENTRY] Say query mode - Pausing 20s before speaking...")
        original_sid = session.get("initial_call_sid", session_id)
        pause_length = session.get("calculated_pause", 30)
        logging.info(f"[DELAY] Using dynamic pause from SID {original_sid}: {pause_length}s")
        logging.info(f"[DELAY] Using dynamic pause before query: {pause_length}s")
        vr.pause(length=pause_length)
        vr.say(query_to_speak, voice='alice')

        gather = Gather(
            input='dtmf speech',
            timeout=90,
            speech_timeout='auto',
            action=f"/twilio/crawler-branch?session_id={session_id}",
            method='POST'
        )

        vr.append(gather)
        original_sid = session.get("initial_call_sid", session_id)
        pause_length = session.get("calculated_pause", 30)
        logging.info(f"[DELAY] Using dynamic pause before query: {pause_length}s")
        vr.pause(length=pause_length)

        vr.record(
            maxLength=90,
            playBeep=False
        )
        return Response(content=str(vr), media_type="application/xml")

    # 🧠 Initial Discovery Phase
    else:
        if session.get("query_spoken"):
            logging.info("[CRAWLER ENTRY] Query already spoken, ending session.")
            vr.say("Ending session. Goodbye.")
            vr.hangup()
            return Response(content=str(vr), media_type="application/xml")

        logging.info("[CRAWLER ENTRY] Passive listen mode (Init Discovery)")
        session["should_check_speech"] = True
        session["ivr_type"] = "unknown"
        session["ivr_phase"] = "init_discovery"
        session_store[session_id] = session

        logging.info("[CRAWLER ENTRY] Initial Discovery Phase Pausing 20 seconds before prompting user...")
        original_sid = session.get("initial_call_sid", session_id)
        pause_length = session.get("calculated_pause", 30)
        logging.info(f"[DELAY] Using dynamic pause before query: {pause_length}s")
        vr.pause(length=pause_length)


        gather = Gather(
            input='dtmf speech',
            timeout=90,
            speech_timeout='auto',
            max_speech_time=90,
            hints="agent, reservation, help, refund, baggage",
            action=f"/twilio/crawler-branch?session_id={session_id}",
            method='POST'
        )
        vr.append(gather)

        original_sid = session.get("initial_call_sid", session_id)
        pause_length = session.get("calculated_pause", 30)
        logging.info(f"[DELAY] Using dynamic pause before query: {pause_length}s")
        vr.pause(length=pause_length)

        vr.redirect(f"/twilio/crawler-branch?session_id={session_id}&fallback=true")

        vr.record(
            maxLength=90,
            playBeep=False
        )
        return Response(content=str(vr), media_type="application/xml")














@app.post("/twilio/crawler-branch")
async def crawler_branch(request: Request):
    form = await request.form()
    digits = form.get("Digits")
    speech = (form.get("SpeechResult") or "").strip()
    logging.info(f"[SPEECH RECEIVED] → {speech}")
    session_id = request.query_params.get("session_id")
    branch_digit = request.query_params.get("branch_digit") 
    session = session_store.get(session_id) or get_session_status(session_id)
    session.setdefault("speech_history", [])
    session.setdefault("last_menu", {})
    session.setdefault("pending_digits", [])
    session.setdefault("menu_repeat_count", 0)
    session.setdefault("tree_path_stack", [])

    if not session:
        logging.warning(f"[FALLBACK] Session ID {session_id} not found in memory. Attempting Firebase pull...")
        session = get_session_from_firebase(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session_store[session_id] = session
        logging.info("[FALLBACK SUCCESS] Session restored from Firebase.")

    # Short speech retry safeguard
    if len(speech) < 6 and session.get("retry_attempts", 0) < 1:
        logging.info("[SHORT SPEECH] Retrying IVR capture due to empty/short speech")
        session["retry_attempts"] = 1
        call = twilio_client.calls.create(
            to=form.get("To"),
            from_=FROM_NUMBER,
            url=f"{get_ngrok_url()}/twilio/crawler-entry?session_id={session_id}&branch_digit={branch_digit}"
        )
        logging.info(f"[SHORT SPEECH RECALL] SID: {call.sid}")
        vr = VoiceResponse()
        vr.say("Retrying. Please hold.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    # Combine speech context
    last_speech = session.get("last_speech", "")
    combined_speech = f"{last_speech} {speech}".strip()
    if "trouble understanding you" in combined_speech.lower() and not session.get("query_spoken"):
        logging.info("[QUERY INJECTION] Forced query injection due to fallback phrase.")
        session["say_query_flag"] = True
        session["query_spoken"] = True
        update_session_status(session_id, session)
        call = initiate_twilio_call(to_number=form.get("To"), session_id=session_id, say_query=True)
        vr = VoiceResponse()
        vr.say("Redirecting with your request.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    session["last_speech"] = combined_speech
    logging.info(f"[COMBINED SPEECH] → {combined_speech}")

    # Detect repeated menu loop
    speech_history = session.setdefault("speech_history", [])
    speech_history.append(speech.strip())

    # Keep last 3 entries for analysis
    if len(speech_history) > 3:
        speech_history.pop(0)

    # Check if the last 3 transcripts are near-identical
    if len(set(speech_history)) == 1 and len(speech_history) == 3:
        logging.warning("[REPEATED MENU DETECTED] Breaking recursion to prevent infinite loop.")
        update_session_status(session_id, {"loop_detected": True})
        vr = VoiceResponse()
        vr.say("We’ve reached a repeated menu. Ending session to avoid a loop. Goodbye.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    session["speech_history"] = speech_history

    # 🚨 Check if it’s a good time to say the query
    if not session.get("query_spoken") and session.get("ivr_type") == "open-ended":
        try:
            should_speak = should_say_query_now(combined_speech)
            if should_speak:
                logging.info("[GPT DECISION] IVR ready → speaking query")

                # Speak the query
                vr = VoiceResponse()
                vr.pause(length=3)
                vr.say(session.get("query", ""))
                vr.hangup()

                session["query_spoken"] = True
                update_session_status(session_id, session)
                return Response(content=str(vr), media_type="application/xml")
        except Exception as e:
            logging.warning(f"[SAY QUERY CHECK ERROR] {e}")

    phase = session.get("ivr_phase", "init_discovery")
    logging.info(f"[PHASE] IVR Phase: {phase}")



    if phase == "init_discovery":
        ivr_type = classify_ivr_type(combined_speech, session.get("query", ""))


        # 🛡️ OVERRIDE HERE BEFORE WRITING TO SESSION
        if looks_like_menu(combined_speech):
            logging.warning(f"[OVERRIDE] Detected digit-based menu in speech — forcing IVR type to 'menu'")
            ivr_type = "menu"
        session["ivr_type"] = ivr_type
        session["ivr_phase"] = "active_response"
        session_store[session_id] = session
        logging.info(f"[PHASE] init_discovery | IVR Type: {ivr_type}")
        
        logging.debug(f"[TREE SNAPSHOT] After IVR Type Decision → {json.dumps(session.get('tree', {}), indent=2)}")


        vr = VoiceResponse()
        gather = Gather(
            input='speech dtmf',
            timeout=90,
            speech_timeout='auto',
            hints="agent, reservation, refund, baggage, support, help",
            action=f"/twilio/crawler-branch?session_id={session_id}",
            method='POST'
        )

        vr.append(gather)
        vr.say("We didn’t hear anything. Goodbye.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    ivr_type = session.get("ivr_type")
    # 🚨 Check if it’s a good time to say the query
    if not session.get("query_spoken") and session.get("ivr_type") == "open-ended":
        try:
            should_speak = should_say_query_now(combined_speech)
            if should_speak:
                logging.info("[GPT DECISION] IVR ready → speaking query")

                # Speak the query
                vr = VoiceResponse()
                vr.pause(length=3)
                vr.say(session.get("query", ""))
                vr.hangup()

                session["query_spoken"] = True
                update_session_status(session_id, session)
                return Response(content=str(vr), media_type="application/xml")
        except Exception as e:
            logging.warning(f"[SAY QUERY CHECK ERROR] {e}")

    phase = session.get("ivr_phase", "init_discovery")
    logging.info(f"[PHASE] {phase} | IVR Type: {ivr_type}")

    if phase == "active_response":
        # 🛡️ Late override to menu if digits are clearly present
        if ivr_type == "open-ended" and looks_like_menu(combined_speech):
            logging.warning("[LATE OVERRIDE] Detected digit-based menu after initial GPT classification — switching IVR Type to 'menu'")
            session["ivr_type"] = "menu"
            session_store[session_id] = session
            ivr_type = "menu"  # 🔄 Update local var too
            logging.info(f"[LATE OVERRIDE APPLIED] IVR Type updated to: {ivr_type}")

        # 🗣️ If still open-ended and we hear a prompt, inject the user query
    elif ivr_type == "open-ended" and heard_open_ended_prompt(combined_speech):
        if not session.get("query_spoken"):
            logging.info("[QUERY INJECTION] Detected open-ended prompt → injecting query")
            session["query_spoken"] = True
            update_session_status(session_id, session)
            call = initiate_twilio_call(to_number=form.get("To"), session_id=session_id, say_query=True)

            vr = VoiceResponse()
            vr.say("Redirecting with your request.")
            vr.hangup()
            return Response(content=str(vr), media_type="application/xml")
        else:
            logging.info("[SKIP INJECTION] Query already spoken once — not repeating.")



        
    if session.get("query_spoken"):
        vr = VoiceResponse()
        vr.say("Continuing tree crawl.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    if ivr_type == "open-ended" and session.get("should_check_speech", True):
        session["should_check_speech"] = False
        try:
            answer = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are listening to an IVR phone system.\n"
                            "If the system is asking the caller what they want or prompting for a response — even implicitly — reply ONLY with 'YES'.\n"
                            "Otherwise, reply 'NO'.\n\n"
                            "Examples of YES:\n"
                            "- 'Change my reservation or upgrade...'\n"
                            "- 'Tell me what you're calling about'\n"
                            "- 'Press 1 for this, 2 for that'\n\n"
                            "Now listen to this IVR audio transcript and decide:\n"
                        )
                    },
                    {"role": "user", "content": combined_speech}
                ]
            ).choices[0].message.content.strip().lower()

            logging.info(f"[GPT DECISION] Should say query now? → {answer}")


            if "yes" in answer:
                session["query_spoken"] = True
                update_session_status(session_id, session)
                call = initiate_twilio_call(to_number=form.get("To"), session_id=session_id, say_query=True)
                vr = VoiceResponse()
                vr.say("Redirecting with your request.")
                vr.hangup()
                return Response(content=str(vr), media_type="application/xml")

        except Exception as e:
            logging.error(f"[GPT CHECK ERROR] {e}")

    if ivr_type == "menu":
        try:
            parsed_raw = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract only the spoken menu options from the transcript. "
                            "For example, if the user hears 'For reservations press 1, for baggage press 2', "
                            "return: {\"1\": \"Reservations\", \"2\": \"Baggage\"}. "
                            "Do NOT guess or hallucinate. Use only what was said exactly. "
                            "Skip non-numbered phrases. Return a flat JSON object and nothing else."
                        )
                    },
                    {"role": "user", "content": combined_speech}
                ]
            ).choices[0].message.content.strip()

            logging.warning(f"[GPT PARSE] Raw: {parsed_raw}")


            parsed_options = safe_json_parse(parsed_raw)
            # Menu change detection
            if parsed_options and parsed_options != session.get("last_menu"):
                session["last_menu"] = parsed_options
                session["pending_digits"] = list(parsed_options.keys())
                session["menu_repeat_count"] = 0
            else:
                session["menu_repeat_count"] = session.get("menu_repeat_count", 0) + 1

            from difflib import SequenceMatcher

            def prioritize_menu(menu, query):
                def score(text): return SequenceMatcher(None, text.lower(), query.lower()).ratio()
                return sorted(menu, key=lambda k: -score(menu[k]))

            # Prioritize pending digits by match to user query
            if parsed_options:
                prioritized = prioritize_menu(parsed_options, session.get("query", ""))
                session["pending_digits"] = prioritized


            default_fallback = {"1": "Sales", "2": "Support", "3": "Billing"}
            if parsed_options == default_fallback:
                logging.warning("[GPT PARSE WARNING] Detected fallback values. Skipping tree update.")
                parsed_options = {}

            current_path = session.get("path", "root")
            branch_key = str(digits or branch_digit or "").strip()
            is_speech_path = not branch_key

            # Only build a speech path if parsed_options exist
            if is_speech_path and not parsed_options:
                logging.warning("[SPEECH SKIPPED] No valid options found in speech — skipping tree update.")
                vr = VoiceResponse()
                vr.say("We didn’t detect any options. Ending call.")
                vr.hangup()
                return Response(content=str(vr), media_type="application/xml")

            # Build tree path normally
            next_key = branch_key or "speech"
            path = f"{current_path}.{next_key}"
            session["path"] = path
            logging.info(f"[TREE PATH] New path → {path}")

            # Prevent redundant recursion
            if "visited_paths" not in session:
                session["visited_paths"] = []

            if path in session["visited_paths"]:
                logging.warning(f"[DUPLICATE PATH] Skipping already visited path: {path}")
                session["pending_digits"] = [d for d in scored if d not in session["tree_path_stack"]]
                vr = VoiceResponse()
                vr.say("This path was already explored. Ending.")
                vr.hangup()
                return Response(content=str(vr), media_type="application/xml")

            session["visited_paths"].append(path)


            if parsed_options:
                updated_tree = update_tree_branch(session["tree"], path, parsed_options, ivr_type=ivr_type)
                current_node = get_node_by_path(session["tree"], session.get("path", "root"))
                if current_node:
                    current_node["ivr_type"] = ivr_type

                # Optionally mark loop on the current tree node
                if session.get("loop_detected"):
                    parts = path.strip().split(".")
                    node = updated_tree
                    for part in parts[1:]:  # skip 'root'
                        node = node.get("children", {}).get(part, {})
                    if node:
                        node["loop_detected"] = True
                        node["label"] += " [loop detected]"

                session["tree"] = updated_tree
                import difflib

                user_query = session.get("query", "").lower()
                ordered_digits = list(parsed_options.keys())

                def relevance_score(label, query):
                    return difflib.SequenceMatcher(None, label, query).ratio()

                # Score and sort all digits by how well their label matches the user query
                scored = sorted(
                    ordered_digits,
                    key=lambda d: -relevance_score(parsed_options[d].lower(), user_query)
                )

                session["pending_digits"] = scored
                logging.info(f"[PRIORITIZED DIGITS] → {session['pending_digits']}")


                save_tree_snapshot(session.get("query", "unknown"), session_id, updated_tree)
                logging.info(f"[TREE UPDATED] Branch: {path} | Keys: {list(parsed_options.keys())}")
                logging.info(f"[SNAPSHOT DATA] {json.dumps(updated_tree, indent=2)[:1000]}")


                update_session_status(session_id, {
                    "path": path,
                    "tree": updated_tree,
                    "pending_digits": session["pending_digits"]
                })

                if session["pending_digits"]:
                    next_digit = session["pending_digits"].pop(0)
                    call = twilio_client.calls.create(
                        to=form.get("To"),
                        from_=FROM_NUMBER,
                        url=f"{get_ngrok_url()}/twilio/crawler-entry?session_id={session_id}&digit={next_digit}&branch_digit={next_digit}"
                    )
                    logging.info(f"[RECURSE DTMF] {next_digit} | SID: {call.sid}")
                    session["tree_path_stack"].append(next_digit)
        except Exception as e:
            logging.error(f"[PARSE MENU ERROR] {e}")
            # If we've exhausted all pending digits, mark node as complete
        if not session["pending_digits"]:
            current_node = get_node_by_path(session["tree"], session.get("path", "root"))
            if current_node:
                current_node["exhausted"] = True
                logging.info(f"[NODE EXHAUSTED] {session.get('path')} fully crawled")



    vr = VoiceResponse()
    vr.say("Thanks. Response captured. Goodbye.")
    vr.hangup()
    return Response(content=str(vr), media_type="application/xml")










@app.post("/update-path")
async def update_path(data: dict):
    session_id = data.get("session_id")
    path = data.get("path")
    logging.info(f"[PATH UPDATED] {session_id} → {path}")
    update_session_status(session_id, {"path": path})
    return {"ok": True}

@app.get("/session/{session_id}")
async def get_session(session_id: str):
    session = session_store.get(session_id) or get_session_status(session_id)
    session_store[session_id] = session
    logging.info(f"[FIREBASE UPDATE] Session Tree: {json.dumps(session.get('tree', {}), indent=2)[:1000]}")

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

@app.post("/twilio/gather")
async def gather_result(request: Request):
    form = await request.form()
    digits = form.get("Digits")
    speech = form.get("SpeechResult")
    session_id = request.query_params.get("session_id")

    os.makedirs("logs", exist_ok=True)
    with open(f"logs/{session_id}_{datetime.utcnow().isoformat()}.txt", "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] Speech: {speech}\n")

    logging.info(f"[TWILIO] DTMF: {digits}, Speech: {speech}")
    vr = VoiceResponse()
    vr.say(f"You selected option {digits or speech}. Goodbye.")
    vr.hangup()
    return Response(content=str(vr), media_type="application/xml")

@app.post("/clear-session")
def clear(session_id: str):
    delete_session(session_id)
    return {"status": "deleted"}


