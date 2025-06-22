import firebase_admin
from firebase_admin import credentials, db
import os
import logging


# 🌐 Firebase setup
FIREBASE_CERT_PATH = os.getenv("FIREBASE_CERT_PATH", "firebase-key.json")
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "https://xdial-default-rtdb.firebaseio.com/")

if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_CERT_PATH)
    firebase_admin.initialize_app(cred, {
        'databaseURL': FIREBASE_DB_URL
    })

#  Write to Firebase
def update_session_status(session_id: str, updates: dict):
    ref = db.reference(f"sessions/{session_id}")
    ref.update(updates)
    logging.info(f"  Firebase update - /sessions/{session_id}:\n{updates}")

#  Restore from Firebase (used if local session_store is missing)
def get_session_status(session_id: str):
    ref = db.reference(f"/sessions/{session_id}")
    session = ref.get()
    print(f"  Restored from Firebase - /sessions/{session_id}:\n{session}".encode('utf-8', errors='ignore').decode())
    return session or {}

def get_session_from_firebase(session_id: str) -> dict:
    try:
        ref = db.reference(f"/sessions/{session_id}")
        data = ref.get()
        return data if data else None
    except Exception as e:
        logging.error(f"[FIREBASE READ ERROR] Could not fetch session {session_id}: {e}")
        return None

def delete_session(session_id: str):
    ref = db.reference(f"/sessions/{session_id}")
    ref.delete()
    logging.info(f"[FIREBASE DELETE] Session {session_id} removed.")
