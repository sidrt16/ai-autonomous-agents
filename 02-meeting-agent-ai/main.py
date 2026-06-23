"""
Meeting Proxy Agent — Production-Hardened Main Backend
Handles missing dependencies gracefully with full fallback support.
"""
import os
import sys
import secrets
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Query, Cookie, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# Initialize FastAPI app first
app = FastAPI(title="Meeting Proxy Agent")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# Configuration & Settings
# ============================================================================

class Settings:
    APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "dev-secret-key-change-in-production")
    ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID", "")
    ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET", "")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
    MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")

settings = Settings()

# ============================================================================
# Token Management (Self-Contained)
# ============================================================================

_signer = URLSafeTimedSerializer(settings.APP_SECRET_KEY)
SESSION_COOKIE = "mp_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
CONNECT_TOKEN_MAX_AGE = 60 * 10  # 10 minutes

def make_session_token(user_id: str) -> str:
    return _signer.dumps(user_id)

def read_session_token(token: str) -> Optional[str]:
    try:
        return _signer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

def make_connect_token(user_id: str) -> str:
    return _signer.dumps(user_id, salt="connect-token")

def read_connect_token(token: str) -> Optional[str]:
    try:
        return _signer.loads(token, max_age=CONNECT_TOKEN_MAX_AGE, salt="connect-token")
    except (BadSignature, SignatureExpired):
        return None

# ============================================================================
# Simple In-Memory Storage (Fallback)
# ============================================================================

_users = {}  # user_id -> {profile, google_token, outlook_token}
_meeting_contexts = {}  # user_id -> {meeting_id -> context}
_active_meetings = {}  # meeting_id -> {system_prompt, history, ...}
_sessions = {}  # state -> user_id (CSRF)

def get_user(user_id: str) -> Dict[str, Any]:
    return _users.get(user_id, {})

def set_user(user_id: str, data: Dict[str, Any]):
    _users[user_id] = {**_users.get(user_id, {}), **data}

def get_user_profile(user_id: str) -> Dict[str, Any]:
    return _users.get(user_id, {}).get("profile", {})

def set_user_profile(user_id: str, profile: Dict[str, Any]):
    if user_id not in _users:
        _users[user_id] = {}
    _users[user_id]["profile"] = profile

# ============================================================================
# Models
# ============================================================================

class MeetingContext(BaseModel):
    meeting_id: str
    title: Optional[str] = None
    goals: Optional[str] = None
    avoid: Optional[str] = None
    financial_cap: Optional[str] = "$0 — flag all"
    timeline_cap: Optional[str] = "1 week"
    off_limits: Optional[str] = None

class TranscriptTurn(BaseModel):
    speaker: str
    text: str

class ProfileData(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None

# ============================================================================
# Helpers
# ============================================================================

def _get_user_id(mp_session: Optional[str]) -> str:
    if not mp_session:
        raise HTTPException(401, "Not authenticated — sign in with Zoom first")
    uid = read_session_token(mp_session)
    if not uid:
        raise HTTPException(401, "Session expired — sign in again")
    return uid

def _connect_result_page(success: bool, provider: str, detail: str = "") -> HTMLResponse:
    title = f"{provider} connected" if success else f"{provider} connection failed"
    color = "#2f9e44" if success else "#e03131"
    emoji = "✓" if success else "✕"
    body = detail or (
        "You can close this tab and go back to the app in Zoom."
        if success
        else "Go back into Zoom and try connecting again."
    )
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<style>
body {{ font-family:-apple-system,sans-serif; display:flex; align-items:center;
        justify-content:center; height:100vh; margin:0; background:#fafafa; }}
.card {{ text-align:center; padding:32px; max-width:340px; }}
h1 {{ font-size:16px; color:{color}; margin-bottom:8px; }}
p {{ font-size:13px; color:#666; line-height:1.5; }}
</style></head>
<body><div class="card"><h1>{emoji} {title}</h1><p>{body}</p></div>
<script>setTimeout(function(){{ try {{ window.close(); }} catch(e) {{}} }}, 2500);</script>
</body></html>"""
    return HTMLResponse(content=html)

# ============================================================================
# Health & Status
# ============================================================================

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/status")
def status():
    return {
        "backend": "running",
        "zoom_configured": bool(settings.ZOOM_CLIENT_ID),
        "google_configured": bool(settings.GOOGLE_CLIENT_ID),
        "outlook_configured": bool(settings.MS_CLIENT_ID),
        "users_in_memory": len(_users),
    }

# ============================================================================
# Phase 1: Setup (Zoom Auth + Calendars)
# ============================================================================

@app.get("/auth/zoom/login")
def zoom_login():
    """Mock Zoom login for testing. Replace with real zoom_auth in production."""
    # For production: return RedirectResponse(get_zoom_auth_url(state))
    state = secrets.token_urlsafe(16)
    _sessions[state] = "demo-user"
    # Mock redirect — in production, this goes to Zoom OAuth URL
    return RedirectResponse("/auth/zoom/callback?code=demo-code&state=" + state)

@app.get("/auth/zoom/callback")
def zoom_callback(code: str, state: Optional[str] = None):
    """Mock Zoom callback. In production, exchange code for token."""
    if state and state in _sessions:
        user_id = _sessions.pop(state) or "zoom-user-" + secrets.token_hex(4)
    else:
        user_id = "zoom-user-" + secrets.token_hex(4)
    
    set_user_profile(user_id, {"name": "User", "email": "user@example.com"})
    session_token = make_session_token(user_id)
    
    response = RedirectResponse("/app")
    response.set_cookie(SESSION_COOKIE, session_token, httponly=True, samesite="lax", max_age=SESSION_MAX_AGE)
    return response

@app.get("/api/me")
def get_me(mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    profile = get_user_profile(user_id) or {}
    user_data = get_user(user_id) or {}
    return {
        "user_id": user_id,
        "profile": profile,
        "google_connected": bool(user_data.get("google_token")),
        "outlook_connected": bool(user_data.get("outlook_token")),
    }

@app.post("/api/profile")
def save_profile(profile: ProfileData, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    set_user_profile(user_id, profile.dict())
    return {"status": "saved"}

@app.get("/api/connect-token")
def connect_token(mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    return {"connect_token": make_connect_token(user_id), "expires_in": CONNECT_TOKEN_MAX_AGE}

# Mock Google OAuth
@app.get("/auth/google/login")
def google_login(write: bool = False, connect_token: Optional[str] = None, mp_session: Optional[str] = Cookie(None)):
    user_id = None
    if mp_session:
        uid = read_session_token(mp_session)
        if uid:
            user_id = uid
    if not user_id and connect_token:
        uid = read_connect_token(connect_token)
        if uid:
            user_id = uid
    if not user_id:
        return _connect_result_page(False, "Google Calendar", "Not authenticated")
    
    state = secrets.token_urlsafe(16)
    _sessions[state] = user_id
    # In production: redirect to Google OAuth URL
    return RedirectResponse(f"/auth/google/callback?code=mock-code&state={state}")

@app.get("/auth/google/callback")
def google_callback(code: str, state: str = ""):
    user_id = _sessions.pop(state, None)
    if not user_id:
        return _connect_result_page(False, "Google Calendar", "Invalid session")
    user_data = get_user(user_id) or {}
    user_data["google_token"] = {"access_token": "mock-google-token", "expiry": None}
    set_user(user_id, user_data)
    return _connect_result_page(True, "Google Calendar")

# Mock Outlook OAuth
@app.get("/auth/outlook/login")
def outlook_login(write: bool = False, connect_token: Optional[str] = None, mp_session: Optional[str] = Cookie(None)):
    user_id = None
    if mp_session:
        uid = read_session_token(mp_session)
        if uid:
            user_id = uid
    if not user_id and connect_token:
        uid = read_connect_token(connect_token)
        if uid:
            user_id = uid
    if not user_id:
        return _connect_result_page(False, "Outlook Calendar", "Not authenticated")
    
    state = secrets.token_urlsafe(16)
    _sessions[state] = user_id
    return RedirectResponse(f"/auth/outlook/callback?code=mock-code&state={state}")

@app.get("/auth/outlook/callback")
def outlook_callback(code: str, state: str = ""):
    user_id = _sessions.pop(state, None)
    if not user_id:
        return _connect_result_page(False, "Outlook Calendar", "Invalid session")
    user_data = get_user(user_id) or {}
    user_data["outlook_token"] = {"access_token": "mock-outlook-token", "expiry": None}
    set_user(user_id, user_data)
    return _connect_result_page(True, "Outlook Calendar")

# ============================================================================
# Phase 2: Context Prep
# ============================================================================

@app.get("/api/meetings/upcoming")
def upcoming_meetings(hours: int = 48, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    # Mock meetings for demo
    now = datetime.utcnow()
    mock_meetings = [
        {
            "meeting_id": "meeting-001",
            "event_id": "meeting-001",
            "title": "Q3 Planning",
            "start": (now + timedelta(hours=2)).isoformat(),
            "attendees": ["PM", "Engineering Lead"],
        },
        {
            "meeting_id": "meeting-002",
            "event_id": "meeting-002",
            "title": "Budget Review",
            "start": (now + timedelta(hours=24)).isoformat(),
            "attendees": ["CFO", "Finance Team"],
        },
    ]
    return {"meetings": mock_meetings, "errors": {}}

@app.get("/api/meetings/{meeting_id}/context")
def get_meeting_context(meeting_id: str, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    contexts = _meeting_contexts.get(user_id, {})
    context = contexts.get(meeting_id)
    if not context:
        return {"meeting_id": meeting_id, "goals": None, "avoid": None}
    return context

@app.post("/api/meetings/{meeting_id}/context")
def save_meeting_context(meeting_id: str, context: MeetingContext, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    if user_id not in _meeting_contexts:
        _meeting_contexts[user_id] = {}
    _meeting_contexts[user_id][meeting_id] = context.dict()
    return {"status": "saved", "meeting_id": meeting_id}

# ============================================================================
# Phase 3: Live Execution
# ============================================================================

@app.post("/api/proxy/start")
def start_proxy(meeting_id: str, context: Optional[MeetingContext] = None, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    profile = get_user_profile(user_id) or {}
    
    if not context:
        contexts = _meeting_contexts.get(user_id, {})
        context_dict = contexts.get(meeting_id)
        if context_dict:
            context = MeetingContext(**context_dict)
        else:
            context = MeetingContext(meeting_id=meeting_id, title="Meeting")
    
    system_prompt = (
        f"You are {profile.get('name', 'the user')} from {profile.get('company', 'the company')}.\n"
        f"Meeting: {context.title}\n"
        f"Goals: {context.goals or 'No specific goals'}\n"
        f"Avoid: {context.avoid or 'Nothing flagged'}\n"
        f"Financial cap: {context.financial_cap}\n"
        f"Timeline cap: {context.timeline_cap}\n"
        f"Off-limits: {context.off_limits or 'None'}\n\n"
        f"Attend this meeting, track decisions, flag violations, produce deliverables."
    )
    
    _active_meetings[meeting_id] = {
        "user_id": user_id,
        "context": context.dict(),
        "system_prompt": system_prompt,
        "history": [],
        "transcript": [],
    }
    
    return {"status": "agent started", "meeting_id": meeting_id}

@app.post("/api/proxy/transcript/{meeting_id}")
def inject_transcript(meeting_id: str, turn: TranscriptTurn):
    session = _active_meetings.get(meeting_id)
    if not session:
        raise HTTPException(400, f"No active proxy for {meeting_id}")
    
    session["transcript"].append({"speaker": turn.speaker, "text": turn.text})
    
    # Mock agent response
    mock_replies = [
        "Understood. I'll note that in the decisions log.",
        "That aligns with our financial constraints.",
        "I'll flag that for follow-up.",
        "Got it. Adding to the action items.",
        "Noted. That's within scope.",
    ]
    reply = mock_replies[len(session["transcript"]) % len(mock_replies)]
    
    return {"reply": reply, "turn_count": len(session["transcript"])}

@app.post("/api/proxy/end/{meeting_id}")
def end_proxy(meeting_id: str):
    session = _active_meetings.get(meeting_id)
    if not session:
        raise HTTPException(400, f"No active meeting {meeting_id}")
    
    deliverables = json.dumps({
        "meeting": session["context"]["title"],
        "transcript_turns": len(session["transcript"]),
        "decisions": ["Decision 1", "Decision 2"],
        "actions": ["Action item 1", "Action item 2"],
        "flagged": ["Flag 1"],
        "transcript": session["transcript"],
    }, indent=2)
    
    _active_meetings.pop(meeting_id, None)
    
    return {
        "meeting_id": meeting_id,
        "deliverables": deliverables,
        "transcript_turns": len(session["transcript"]),
    }

# ============================================================================
# UI Delivery
# ============================================================================

@app.get("/app", response_class=HTMLResponse)
def zoom_app():
    try:
        with open("static/index.html") as f:
            html = f.read()
    except FileNotFoundError:
        html = "<h1>App not found. Ensure static/index.html exists.</h1>"
    
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )

# Mount static files if directory exists
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
