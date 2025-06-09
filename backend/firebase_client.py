# backend/firebase_client.py

import firebase_admin
from firebase_admin import credentials, db
import os

FIREBASE_CREDENTIAL_PATH = os.getenv("FIREBASE_CREDENTIAL_PATH", "firebase-key.json")
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL")

if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_CREDENTIAL_PATH)
    firebase_admin.initialize_app(cred, {
        'databaseURL': FIREBASE_DB_URL
    })

def update_session_status(session_id: str, data: dict):
    ref = db.reference(f"/sessions/{session_id}")
    print(f"🔥 Writing to Firebase → /sessions/{session_id}:\n{data}")
    ref.update(data)
