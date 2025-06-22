# backend/gpt_utils.py

import re
import json
import logging
import openai
from dotenv import load_dotenv

load_dotenv()
client = openai.OpenAI()


def safe_json_parse(raw: str):
    """
    Clean and parse GPT-generated JSON.
    Removes code block wrappers like ```json and fallback parses invalid JSON with logging.
    """
    try:
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

