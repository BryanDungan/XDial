# main.py

import os
import json
import logging
import re
import requests
import openai
import urllib.parse
from uuid import uuid4
from datetime import datetime
from gpt_utils import safe_json_parse, client
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather
from audio_utils import wait_for_valid_recording
from ivr_utils import classify_ivr_type, heard_open_ended_prompt, looks_like_menu, crawl_phase_handler
from session_memory import session_store
from audio_utils import detect_prompt_time
from fastapi.responses import JSONResponse
import asyncio
import os
os.makedirs("recordings", exist_ok=True)

from firebase_client import (
    update_session_status,
    get_session_status,
    get_session_from_firebase,
    delete_session,
)
from twilio_utils import (
    initiate_twilio_call,
    get_ngrok_url,
    router as twilio_router
)
from tree import update_tree_branch, save_tree_snapshot




openai.api_key = os.getenv("OPENAI_API_KEY")
client = openai.OpenAI()
twilio_client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")




def get_node_by_path(tree, path):
    parts = path.split(".")
    node = tree
    for part in parts[1:]:  # Skip root
        if "children" in node and part in node["children"]:
            node = node["children"][part]
        else:
            return None
    return node




app = FastAPI()
app.include_router(twilio_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


logging.basicConfig(level=logging.INFO)

class ReconRequest(BaseModel):
    query: str
    user_id: str

class SessionInitResponse(BaseModel):
    session_id: str
    status: str
    created_at: str





def get_phone_number_from_query(query: str) -> str:
    known_numbers = {
        "southwest airlines": "1-800-435-9792",
        "delta airlines": "1-800-221-1212",
        "american airlines": "1-800-433-7300",
        "parish medical center in titusville": "1-321-268-6111",
        "fedex": "1-800-463-3339",
        "ups": "1-800-742-5877",
        "social security administration": "1-800-772-1213",
        "california dmv": "1-800-777-0133",
        "capital one": "1-800-227-4825",
        "amtrak": "1-800-872-7245"
    }
    for name, number in known_numbers.items():
        if name in query.lower():
            return number
    raise ValueError("No known number for this query. Integrate live lookup.")


@app.get("/session-ready/{session_id}")
def session_ready(session_id: str):
    session = get_session_status(session_id)
    return {"ready": session.get("recording_ready") and session.get("whisper_finished")}



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
async def start_crawl(request: Request):
    data = await request.json()
    session_id = data.get("session_id")
    say_query = data.get("say_query", False)
    number = data.get("phone_number")

    existing_session = session_store.get(session_id) or get_session_status(session_id) or {}

    query = data.get("query") or existing_session.get("query", "Hello. Please route this call.")
    to_number = number or existing_session.get("resolved_number")

    if not to_number or not session_id:
        raise HTTPException(status_code=400, detail="Missing phone number or session ID")

    # 🚫 BLOCK if whisper isn't finished and trying to speak query
    if say_query and not existing_session.get("whisper_finished"):
        logging.info(f"[BLOCKED CALL] Whisper not finished, deferring say_query call for {session_id}")
        existing_session["query_pending"] = True
        update_session_status(session_id, existing_session)
        return JSONResponse({"status": "waiting_for_whisper"}, status_code=202)

    # 🧠 Prime session for IVR crawling
    session_store[session_id] = {
        **existing_session,
        "resolved_number": to_number,
        "query": query,
        "status": "starting",
        "path": "root",
        "pending_digits": [],
        "reroute_count": 0,
        "retry_attempts": 0,
        "should_check_speech": True,
        "speech_history": [],
        "last_menu": {},
        "menu_repeat_count": 0,
        "tree_path_stack": [],
    }

    session = session_store.get(session_id) or get_session_status(session_id)

    # ⏳ If whisper done, wait before speaking query
    if session.get("recording_ready") and session.get("whisper_finished") and not say_query:
        pause_len = session.get("calculated_pause", 20)
        logging.info(f"[WHISPER TIMING] Pausing {pause_len}s before proceeding")
        await asyncio.sleep(pause_len)

    try:
        logging.info(f"[TRACE CALL] Recalling with say_query={say_query} for session: {session_id}")
        # 🔁 STEP 4: Follow-up loop logic depending on Whisper IVR type
        if session.get("whisper_finished"):
            ivr_type = session.get("ivr_type")
            if ivr_type == "menu":
                logging.info("[LOOP 2 MENU] Recalling to simulate DTMF press flow.")
            elif ivr_type == "open-ended":
                logging.info("[LOOP 2 OPEN-ENDED] Recalling to inject GPT-shortened query.")

        call_sid = initiate_twilio_call(to_number=to_number, session_id=session_id, say_query=say_query)
        session["twilio_call_sid"] = call_sid
        update_session_status(session_id, session)

        logging.info(f"[CRAWLER STARTED] Calling {to_number} | SID: {call_sid}")
        return {
            "status": "calling",
            "call_sid": call_sid,
            "session_id": session_id,
            "query": query,
            "created_at": existing_session.get("created_at"),
            "tree": existing_session.get("tree", {})
        }

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
            transcribe="true",
            action=f'/twilio/crawler-branch?session_id={session_id}',
            method='POST'
        )
        gather.say("Please hold while we gather the IVR options...")
        vr.append(gather)
        vr.record(
            maxLength=90,
            playBeep=False,
            transcribe="true",
            action=f"/twilio/recording-status?session_id={session_id}",
            method="POST"
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

        # Pull dynamic pause from previous session timing analysis

        pause_length = session.get("calculated_pause", 19)
        logging.info(f"[CRAWLER ENTRY] Say query mode - Pausing {pause_length}s before speaking...")

        vr.pause(length=pause_length)
        vr.say(query_to_speak, voice='alice')

        # After saying the query, listen for any menu/audio (speech or digits)
        gather = Gather(
            input='dtmf speech',
            timeout=90,
            speech_timeout='auto',
            action=f"/twilio/crawler-branch?session_id={session_id}",
            method='POST'
        )
        vr.append(gather)

        # Add long enough recording to capture all next steps
        extended_record_time = 90  # Can bump this if needed
        logging.info(f"[RECORDING] Listening for up to {extended_record_time}s after speaking query.")
        vr.record(
            maxLength=90,
            playBeep=False,
            transcribe="true",
            action=f"/twilio/recording-status?session_id={session_id}",
            method="POST"
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

        total_passive_listen = 90  # Increase passive listening time here
        logging.info(f"[DELAY] Using extended passive listen: {total_passive_listen}s")

        # ⏸ Let Twilio record for the full duration to catch entire IVR prompt
        vr.record(
            maxLength=total_passive_listen,
            playBeep=False,
            transcribe="true",
            action=f"/twilio/crawler-branch?session_id={session_id}",
            method="POST",
            trim="do-not-trim"
        )
        return Response(content=str(vr), media_type="application/xml")















from difflib import SequenceMatcher

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

    # Prefer Whisper transcript if available
    if session.get("whisper_segments"):
        combined_speech = " ".join(seg["text"] for seg in session["whisper_segments"])
    else:
        last_speech = session.get("last_speech", "")
        combined_speech = f"{last_speech} {speech}".strip()

    session["last_speech"] = combined_speech
    logging.info(f"[COMBINED SPEECH] → {combined_speech}")

    session["speech_history"].append(speech.strip())
    if len(session["speech_history"]) > 3:
        session["speech_history"].pop(0)

    def all_similar(history, threshold=0.9):
        return all(
            SequenceMatcher(None, history[0], h).ratio() > threshold
            for h in history[1:]
        )

    if len(session["speech_history"]) == 3 and all_similar(session["speech_history"]):
        logging.warning("[REPEATED MENU DETECTED] Breaking recursion to prevent infinite loop.")
        update_session_status(session_id, {"loop_detected": True})
        vr = VoiceResponse()
        vr.say("We’ve reached a repeated menu. Ending session to avoid a loop. Goodbye.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    session = get_session_status(session_id)
    if session["ivr_type"] == "menu" and not session.get("last_menu"):
        result = crawl_phase_handler(session, combined_speech, digit=branch_digit)

    ivr_type = result["ivr_type"]
    action = result["action"]
    logging.info(f"[PHASE HANDLER] Phase={session.get('ivr_phase')} | Action={action} | IVR Type={ivr_type}")

    session["ivr_type"] = ivr_type
    session_store[session_id] = session

    if not session.get("whisper_finished"):
        logging.warning(f"[BLOCK INJECTION] Whisper not finished. Delaying second call for session {session_id}")
        return  # Skip injection call until whisper is done

    if action == "inject_query" and not session.get("query_spoken"):
        logging.info("[QUERY INJECTION] Phase handler says to inject query")
        session["query_spoken"] = True
        update_session_status(session_id, session)
        call = initiate_twilio_call(to_number=form.get("To"), session_id=session_id, say_query=True)
        vr = VoiceResponse()
        vr.say("Redirecting with your request.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")


    if action == "parse_menu":
        try:
            parsed_raw = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract only the spoken menu options from the transcript. "
                            "Return a flat JSON object and nothing else."
                        )
                    },
                    {"role": "user", "content": combined_speech}
                ]
            ).choices[0].message.content.strip()

            logging.warning(f"[GPT PARSE] Raw: {parsed_raw}")
            parsed_options = safe_json_parse(parsed_raw)
            if parsed_options:
                session["tree"] = {str(k): v for k, v in parsed_options.items()}
                update_session_status(session_id, session)
                logging.info(f"[TREE PARSED] Updated session tree with menu options: {parsed_options}")

            # Inject parsed menu into the tree
            path = session.get("tree_path_stack", [])
            node = session["tree"]
            for digit in path:
                node = node.setdefault(digit, {"label": "", "children": {}})["children"]

            for k, v in parsed_options.items():
                node[k] = {"label": v, "children": {}}
                update_tree_branch(session["tree"], session["path"], parsed_options, ivr_type="menu")
                save_tree_snapshot(session["query"], session_id, session["tree"])
                update_session_status(session_id, session)
            logging.info(f"[TREE UPDATE] Injected menu at path {' > '.join(path)}")


            if not parsed_options:
                logging.warning("[GPT PARSE ERROR] Empty or malformed JSON from GPT.")
                fallback_tree_path = session.get("path", "root")
                fallback_node = get_node_by_path(session["tree"], fallback_tree_path)
                if fallback_node:
                    fallback_node["label"] += " [GPT parse error]"
                    fallback_node["parse_error"] = True
                update_session_status(session_id, session)

            if parsed_options and parsed_options != session.get("last_menu"):
                session["last_menu"] = parsed_options
                session["pending_digits"] = list(parsed_options.keys())
                session["menu_repeat_count"] = 0
            else:
                session["menu_repeat_count"] += 1

            update_session_status(session_id, session)

        except Exception as e:
            logging.error(f"[MENU PARSE ERROR] {e}")

    

    if action == "store_branch":
        logging.info("[BRANCH STORAGE ACTION] Placeholder for storing submenu branch")

    if action == "wait":
        logging.info("[PHASE HANDLER] No action required yet — waiting for next speech input")

    if not session["pending_digits"]:
        session["completed"] = True
        logging.info("[TREE COMPLETED] All digits explored — marking session complete")
        update_session_status(session_id, session)

    # ⬇️ Trigger the next call recursively
    if session["pending_digits"]:
        next_digit = session["pending_digits"].pop(0)
        call = twilio_client.calls.create(
            to=form.get("To"),
            from_=FROM_NUMBER,
            url=f"{get_ngrok_url()}/twilio/crawler-entry?session_id={session_id}&digit={next_digit}&branch_digit={next_digit}"
        )
        logging.info(f"[RECURSE DTMF] {next_digit} | SID: {call.sid}")
        session["tree_path_stack"].append(next_digit)
    # ⬇️ Dump current tree to terminal for inspection
        logging.info(f"[TREE DUMP] {json.dumps(session['tree'], indent=2)[:1000]}")
        update_session_status(session_id, session)


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


