# ivr_utils.py

import os
import re
import json
import logging
import whisper
import openai
from dotenv import load_dotenv
from difflib import SequenceMatcher
from gpt_utils import safe_json_parse, client
from firebase_client import update_session_status, get_session_status
from tree import update_tree_branch, save_tree_snapshot
from audio_utils import wait_for_valid_recording

load_dotenv()

# Whisper model preload
model = whisper.load_model("base")


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

OPEN_ENDED_TRIGGERS = ["how can i help", "say your request", "please tell us", "tell me how we can help"]
MENU_TRIGGERS = ["press 1", "press one", "for reservations", "press 2", "main menu", "for more options"]


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

USE_GPT_CLASSIFIER = True  # Set to True if you want to use GPT as backup

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

def detect_prompt_time(audio_path: str) -> dict:
    result = model.transcribe(audio_path, word_timestamps=True)
    segments = result.get("segments", [])

    open_ended_start = None
    menu_start = None
    calculated_pause = None

    for segment in segments:
        text = segment["text"].lower()
        if open_ended_start is None and any(trigger in text for trigger in OPEN_ENDED_TRIGGERS):
            open_ended_start = int(segment["start"])
            logging.info(f"[WHISPER] Open-ended detected at {open_ended_start}s → '{text}'")

        if menu_start is None and any(trigger in text for trigger in MENU_TRIGGERS):
            menu_start = int(segment["start"])
            logging.info(f"[WHISPER] Menu prompt detected at {menu_start}s → '{text}'")

    # Calculate fallback pause logic
    if menu_start is not None:
        calculated_pause = menu_start + 2
    elif open_ended_start is not None:
        calculated_pause = open_ended_start + 2
    else:
        logging.warning("[AUTO-TIMING] No trigger prompt found — defaulting to 35s")
        calculated_pause = 35

    return {
        "segments": segments,
        "open_ended_start": open_ended_start,
        "menu_start": menu_start,
        "calculated_pause": calculated_pause
    }


def looks_like_menu(speech_text: str) -> bool:
    speech_text = speech_text.lower()
    digit_matches = re.findall(r"press\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|zero)", speech_text)
    logging.info(f"[MENU DETECTION] Found digit-like phrases: {digit_matches}")
    return len(digit_matches) >= 2

def heard_open_ended_prompt(speech: str) -> bool:
    return any(phrase in speech.lower() for phrase in OPEN_ENDED_TRIGGERS)

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


def crawl_phase_handler(session: dict, combined_speech: str, digit: str | None = None):
    """
    Handles IVR classification, menu parsing, open-ended injection, and branch crawling
    based on current session['phase'].
    """
    phase = session.get("phase", "init_discovery")
    ivr_type = session.get("ivr_type")
    query = session.get("query", "")

    result = {
        "action": None,       # e.g., "inject_query", "parse_menu", "wait", "complete"
        "ivr_type": ivr_type,
        "branch_update": None,
        "parsed_menu": None,
        "should_inject_query": False,
    }

    if phase == "init_discovery":
        ivr_type = classify_ivr_type(combined_speech, query)
        session["ivr_type"] = ivr_type
        result["ivr_type"] = ivr_type

        if ivr_type == "menu":
            result["action"] = "parse_menu"
        elif ivr_type == "open-ended":
            result["action"] = "inject_query"
        elif ivr_type == "hybrid":
            result["action"] = "inject_query"  # fallback
        else:
            result["action"] = "wait"

    elif phase == "active_response":
        if looks_like_menu(combined_speech):
            result["action"] = "parse_menu"
        elif heard_open_ended_prompt(combined_speech):
            result["action"] = "inject_query"
        else:
            result["action"] = "wait"

    elif phase == "digit_branch":
        result["action"] = "store_branch"
        result["ivr_type"] = ivr_type

    return result
