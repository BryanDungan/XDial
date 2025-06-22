# backend/twilio_utils.py

import logging
import requests
import urllib.parse
from fastapi import Request
from fastapi.responses import Response
from twilio.rest import Client
from audio_utils import detect_prompt_time
from firebase_client import update_session_status
from session_memory import session_store
import time
from pydub.utils import mediainfo
from firebase_client import get_session_status
from session_memory import session_store  # 👈 create this shared memory
from fastapi import APIRouter
import os

from dotenv import load_dotenv

load_dotenv()  # Make sure this is run at the top of your script

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
os.makedirs("recordings", exist_ok=True)
router = APIRouter()

# Twilio + env setup
from dotenv import load_dotenv
load_dotenv()

openai_api_key = os.getenv("OPENAI_API_KEY")
twilio_client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")

# Needed for Whisper if used
os.environ["PATH"] += r";C:\ffmpeg\bin"


def get_ngrok_url():
    try:
        res = requests.get("http://127.0.0.1:4040/api/tunnels")
        public_url = res.json()["tunnels"][0]["public_url"]
        return public_url
    except Exception as e:
        logging.error(f"[NGROK ERROR] {e}")
        return None


def initiate_twilio_call(to_number: str = None, session_id: str = "", say_query: bool = False, branch_digit: str = None):
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

    # Build URL with query parameters
    params = {"session_id": session_id}
    if say_query:
        params["say_query"] = "true"
    if branch_digit:
        params["branch_digit"] = branch_digit
    full_url = f"{ngrok_url}/twilio/crawler-entry?{urllib.parse.urlencode(params)}"

    # Construct callbacks
    recording_callback = f"{ngrok_url}/twilio/recording-status?session_id={session_id}"
    status_callback = f"{ngrok_url}/twilio/status-callback?session_id={session_id}"

    logging.info(f"[INITIATE CALL] SID: {session_id} | to={to_number} | say_query={say_query} | URL: {full_url}")

    try:
        call = twilio_client.calls.create(
            to=to_number,
            from_=FROM_NUMBER,
            url=full_url,
            method="POST",
            record=True,
            recording_channels="mono",
            recording_status_callback=recording_callback,
            recording_status_callback_method="POST",
            recording_status_callback_event=["completed"],
            status_callback=status_callback,
            status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"]
        )
        logging.info(f"[TWILIO CALL CREATED] Call SID: {call.sid}")
        return call.sid
    except Exception as e:
        logging.error(f"[CALL INITIATION ERROR] {e}")
        return None



# FastAPI route for recording status

@router.post("/twilio/recording-status")
async def recording_status_callback(request: Request):
    form = await request.form()
    recording_url = form.get("RecordingUrl")
    call_sid = form.get("CallSid")
    session_id = request.query_params.get("session_id")
    if not session_id:
        logging.error("[RECORDING CALLBACK] Missing session_id in callback URL")
        return Response(status_code=400)

    logging.info(f"[RECORDING COMPLETED] CallSid={call_sid} | URL={recording_url}")

    # Step 1: Delay to give Twilio time to finalize the recording
    import time
    time.sleep(10)  # Give Twilio a head start before MP3 fetch

    # Step 2: Download the audio file
    local_path = f"recordings/{call_sid}.mp3"
    os.makedirs("recordings", exist_ok=True)
    try:
        r = requests.get(
            recording_url + ".mp3",
            auth=(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        )
        with open(local_path, "wb") as f:
            f.write(r.content)
    except Exception as e:
        logging.error(f"[RECORDING DOWNLOAD FAIL] {e}")
        return Response(status_code=204)

    # Step 3: Ensure file is ready before running Whisper
    for attempt in range(5):
        try:
            if os.path.exists(local_path) and os.path.getsize(local_path) > 10000:
                try:
                    info = mediainfo(local_path)
                    if "duration" in info:
                        logging.info(f"[MP3 READY] Valid MP3 file confirmed: {info['duration']}s")
                        break
                except Exception as err:
                    logging.warning(f"[MP3 VALIDATION FAIL] {err}")
            logging.info(f"[MP3 CHECK] Not valid yet. Retrying... ({attempt+1}/5)")
            time.sleep(2)
        except Exception as e:
            logging.warning(f"[MP3 CHECK ERROR] {e}")
            time.sleep(2)

    # Step 4: Analyze with Whisper
    try:
        pause_info = detect_prompt_time(local_path)
        full_transcript = " ".join(seg["text"] for seg in pause_info.get("segments", []))
        ivr_type = None

        # Step 5: Detect type from Whisper first
        if pause_info.get("menu_start") is not None:
            ivr_type = "menu"
            logging.info("[WHISPER DETECTED] Menu prompt")
        elif pause_info.get("open_ended_start") is not None:
            ivr_type = "open-ended"
            logging.info("[WHISPER DETECTED] Open-ended prompt")
        else:
            from ivr_utils import classify_ivr_type
            session = session_store.get(session_id) or get_session_status(session_id)
            user_query = session.get("query", "")
            ivr_type = classify_ivr_type(full_transcript, user_query)
            logging.info(f"[GPT FALLBACK] Classified as: {ivr_type}")

    except Exception as e:
        logging.error(f"[WHISPER ERROR] {e}")
        return Response(status_code=500)

    # Step 6: Save session updates to Firebase
    session = session_store.get(session_id) or get_session_status(session_id)
    session["calculated_pause"] = pause_info["calculated_pause"]
    session["timing_debug"] = {
        "open_ended_start": pause_info["open_ended_start"],
        "menu_start": pause_info["menu_start"],
        "calculated_pause": pause_info["calculated_pause"]
    }
    session["whisper_segments"] = pause_info["segments"]
    session["whisper_finished"] = True
    session["recording_ready"] = True
    session["ivr_type"] = ivr_type
    update_session_status(session_id, session)

    # Step 7: Retry say_query if it was waiting for Whisper to finish
    if session.get("query_pending"):
        logging.info(f"[RESUME INJECTION] Whisper done. Retrying say_query for {session_id}")
        try:
            initiate_twilio_call(
                to_number=session.get("resolved_number"),
                session_id=session_id,
                say_query=True
            )
            session["query_spoken"] = True
            session["query_pending"] = False
            update_session_status(session_id, session)
        except Exception as e:
            logging.error(f"[RETRY INJECTION FAIL] {e}")

    logging.info(f"[WHISPER TIMING] Pause: {pause_info['calculated_pause']}s — Full: {pause_info}")
    return Response(status_code=204)


