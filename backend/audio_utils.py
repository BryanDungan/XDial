# backend/audio_utils.py

import time
import logging
import requests
from dotenv import load_dotenv
import whisper
import logging
load_dotenv()


def wait_for_valid_recording(url: str, path: str, max_retries: int = 3, delay: float = 2.5) -> bool:
    """
    Attempts to download an audio file from a URL. Fails if size is too small.
    Returns True on success, False on failure after all retries.
    """
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url)
            if response.ok and len(response.content) > 5000:  # 5KB minimum
                with open(path, "wb") as f:
                    f.write(response.content)
                return True
            logging.warning(f"[RETRY] Attempt {attempt}: audio too small ({len(response.content)} bytes)")
        except Exception as e:
            logging.warning(f"[RETRY] Attempt {attempt} failed: {e}")
        time.sleep(delay)
    return False

def detect_prompt_time(audio_path: str, trigger_phrases=None) -> dict:
    """
    Transcribes audio and detects IVR timing events:
    - open-ended prompt start
    - menu prompt start

    Returns dict with timestamps.
    """

    if trigger_phrases is None:
        trigger_phrases = [
            "how can i help",
            "what are you calling about",
            "press 1",
            "press 2",
            "to make a new reservation",
            "to speak with an agent"
        ]

    model = whisper.load_model("base")
    result = model.transcribe(audio_path, word_timestamps=True)
    full_segments = result.get("segments", [])

    segments = result.get("segments", [])

    open_ended_start = None
    menu_start = None

    for segment in segments:
        text = segment.get("text", "").lower()
        start_time = segment.get("start")
        if not text:
            continue

        # Check for open-ended type phrases
        if any(p in text for p in ["how can i help", "what are you calling about"]):
            if open_ended_start is None:
                open_ended_start = int(start_time)
                logging.info(f"[WHISPER] Open-ended prompt detected at {open_ended_start}s → '{text}'")

        # Check for menu prompts
        if any(p in text for p in ["press 1", "press 2", "to make a new reservation"]):
            if menu_start is None:
                menu_start = int(start_time)
                logging.info(f"[WHISPER] Menu prompt detected at {menu_start}s → '{text}'")

    if not open_ended_start and not menu_start:
        logging.warning("[WHISPER TIMING] No trigger phrases found. Default pause will be used.")

    return {
        "open_ended_start": open_ended_start,
        "menu_start": menu_start,
        "calculated_pause": (menu_start or open_ended_start or 30) + 2,
        "segments": full_segments  # <- 🔥 Add this
    }
