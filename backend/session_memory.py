# backend/session_memory.py

"""
Shared in-memory session store for X-Dial backend.
Used to persist session states across requests without needing constant Firebase reads.
"""

# Global in-memory session dictionary
session_store = {}
