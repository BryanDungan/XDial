# test_firebase_write.py
from dotenv import load_dotenv
load_dotenv()

from firebase_client import update_session_status

update_session_status("test-session-id", {
    "status": "testing",
    "query": "debug-check"
})

