# /x-dial/backend/main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from uuid import uuid4
from datetime import datetime
import logging
from dotenv import load_dotenv
load_dotenv()

from firebase_client import update_session_status

app = FastAPI()

# Allow CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session memory (mock database)
session_store = {}

# Logger
logging.basicConfig(level=logging.INFO)

# Request Model
class ReconRequest(BaseModel):
    query: str
    user_id: str

# Response Model
class SessionInitResponse(BaseModel):
    session_id: str
    status: str
    created_at: str


@app.post("/start-recon", response_model=SessionInitResponse)
async def start_recon(request: ReconRequest):
    session_id = str(uuid4())
    timestamp = datetime.utcnow().isoformat()

    session_store[session_id] = {
        "user_id": request.user_id,
        "query": request.query,
        "created_at": timestamp,
        "status": "initializing"
    }

    logging.info(f"[SESSION CREATED] ID: {session_id} for query: '{request.query}'")

    # 🔥 Firebase update with test phone tree
    update_session_status(session_id, {
        "status": "initializing",
        "query": request.query,
        "created_at": timestamp,
        "tree": {
            "1": {"key": "1", "label": "1: Sales", "selected": False},
            "2": {"key": "2", "label": "2: Support", "selected": False},
            "3": {"key": "3", "label": "3: Billing", "selected": False}
        }
    })

    return SessionInitResponse(
        session_id=session_id,
        status="initializing",
        created_at=timestamp
    )


@app.get("/session/{session_id}")
async def get_session(session_id: str):
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
