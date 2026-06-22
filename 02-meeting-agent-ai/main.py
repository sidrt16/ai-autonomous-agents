"""
Meeting Proxy Agent — Complete Three-Phase System
Phase 1: Setup (calendar auth + profile)
Phase 2: Context Prep (upcoming meetings + context editor)  
Phase 3: Live Execution (auto-detect + live transcript + deliverables)
"""
import os
import secrets
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Query, Cookie, Response, Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Stub imports — these exist in your repo
try:
    import google_client
    import outlook_client
    import agent
    import prompt_builder
except ImportError:
    # Graceful fallback for testing without actual clients
    google_client = outlook_client = agent = prompt_builder = None

from config import settings
from user_store import get_user, set_user, get_user_profile, set_user_profile
from zoom_auth import (
    get_zoom_auth_url, exchange_zoom_code, get_zoom_user,
    make_session_token, read_session_token, SESSION_COOKIE,
    make_connect_token, read_connect_token, CONNECT_TOKEN_MAX_AGE,
)

app = FastAPI(title="Meeting Proxy Agent — Three Phases")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Runtime state
_sessions = {}  # state -> zoom_user_id (CSRF)
_meeting_contexts = {}  # user_id -> {meeting_id -> {goals, avoid, caps, ...}}
_active_meetings = {}  # meeting_id -> {history, system_prompt, metadata}

# ============================================================================
# Models
# ============================================================================

class MeetingContext(BaseModel):
    meeting_id: str
    title: str
    start_time: Optional[str] = None
    goals: Optional[str] = None
    avoid: Optional[str] = None
    financial_cap: Optional[str] = "$0 — flag all"
    timeline_cap: Optional[str] = "1 week"
    off_limits: Optional[str] = None
    formality: Optional[str] = "professional"
    directness: Optional[str] = "balanced"

class TranscriptTurn(BaseModel):
    speaker: str
    text: str

class ProfileData(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    communication_style: Optional[str] = "professional"

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

def _get_user_id_for_oauth_start(mp_session: Optional[str], connect_token: Optional[str]) -> str:
    """Fallback for OAuth flows that lose the session cookie crossing browser contexts."""
    if mp_session:
        uid = read_session_token(mp_session)
        if uid:
            return uid
    if connect_token:
        uid = read_connect_token(connect_token)
        if uid:
            return uid
    raise HTTPException(401, "Not authenticated")

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
# Phase 1: Setup (Calendar Auth + Profile)
# ============================================================================

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/auth/zoom/login")
def zoom_login():
    state = secrets.token_urlsafe(16)
    _sessions[state] = None
    return RedirectResponse(get_zoom_auth_url(state))

@app.get("/auth/zoom/callback")
def zoom_callback(code: str, response: Response, state: Optional[str] = None):
    if state and state not in _sessions:
        raise HTTPException(400, "Invalid OAuth state")
    if state:
        _sessions.pop(state)
    try:
        token_data = exchange_zoom_code(code)
        zoom_user = get_zoom_user(token_data["access_token"])
        user_id = zoom_user["id"]
        profile = get_user_profile(user_id) or {}
        if not profile.get("name"):
            set_user_profile(user_id, {
                "name": zoom_user.get("display_name", ""),
                "email": zoom_user.get("email", ""),
            })
        session_token = make_session_token(user_id)
        response = RedirectResponse("/app")
        response.set_cookie(SESSION_COOKIE, session_token, httponly=True, samesite="lax", max_age=60*60*24*7)
        return response
    except Exception as e:
        raise HTTPException(400, str(e))

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

# Google Calendar
@app.get("/auth/google/login")
def google_login(write: bool = False, connect_token: Optional[str] = None, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id_for_oauth_start(mp_session, connect_token)
    redirect_uri = f"{os.getenv('APP_BASE_URL', 'http://localhost:8000')}/auth/google/callback"
    state = secrets.token_urlsafe(16)
    _sessions[state] = user_id
    url = google_client.get_login_url(include_write_scope=write, redirect_uri=redirect_uri, state=state)
    return RedirectResponse(url)

@app.get("/auth/google/callback")
def google_callback(code: str, state: str = "", write: bool = False):
    user_id = _sessions.pop(state, None)
    if not user_id:
        return _connect_result_page(False, "Google Calendar", "This link expired or was already used.")
    redirect_uri = f"{os.getenv('APP_BASE_URL', 'http://localhost:8000')}/auth/google/callback"
    try:
        token_data = google_client.handle_callback(code, include_write_scope=write, redirect_uri=redirect_uri)
        user_data = get_user(user_id) or {}
        user_data["google_token"] = token_data
        set_user(user_id, user_data)
        return _connect_result_page(True, "Google Calendar")
    except Exception as e:
        return _connect_result_page(False, "Google Calendar", str(e))

# Outlook Calendar
@app.get("/auth/outlook/login")
def outlook_login(write: bool = False, connect_token: Optional[str] = None, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id_for_oauth_start(mp_session, connect_token)
    redirect_uri = f"{os.getenv('APP_BASE_URL', 'http://localhost:8000')}/auth/outlook/callback"
    state = secrets.token_urlsafe(16)
    _sessions[state] = user_id
    url = outlook_client.get_login_url(include_write_scope=write, redirect_uri=redirect_uri, state=state)
    return RedirectResponse(url)

@app.get("/auth/outlook/callback")
def outlook_callback(code: str, state: str = "", write: bool = False):
    user_id = _sessions.pop(state, None)
    if not user_id:
        return _connect_result_page(False, "Outlook Calendar", "This link expired or was already used.")
    redirect_uri = f"{os.getenv('APP_BASE_URL', 'http://localhost:8000')}/auth/outlook/callback"
    try:
        token_data = outlook_client.handle_callback(code, include_write_scope=write, redirect_uri=redirect_uri)
        user_data = get_user(user_id) or {}
        user_data["outlook_token"] = token_data
        set_user(user_id, user_data)
        return _connect_result_page(True, "Outlook Calendar")
    except Exception as e:
        return _connect_result_page(False, "Outlook Calendar", str(e))

# ============================================================================
# Phase 2: Context Prep (Upcoming Meetings + Context Editor)
# ============================================================================

@app.get("/api/meetings/upcoming")
def upcoming_meetings(hours: int = 48, mp_session: Optional[str] = Cookie(None)):
    """Fetch upcoming meetings from connected calendars."""
    user_id = _get_user_id(mp_session)
    user_data = get_user(user_id) or {}
    results = []
    errors = {}

    # Google Calendar
    google_token = user_data.get("google_token")
    if google_token:
        try:
            results += google_client.list_upcoming(google_token, hours)
        except Exception as e:
            errors["google"] = str(e)

    # Outlook Calendar
    outlook_token = user_data.get("outlook_token")
    if outlook_token:
        try:
            results += outlook_client.list_upcoming(outlook_token, hours)
        except Exception as e:
            errors["outlook"] = str(e)

    results.sort(key=lambda m: m.get("start", ""))
    return {"meetings": results, "errors": errors}

@app.get("/api/meetings/{meeting_id}/context")
def get_meeting_context(meeting_id: str, mp_session: Optional[str] = Cookie(None)):
    """Get saved context for a specific meeting."""
    user_id = _get_user_id(mp_session)
    contexts = _meeting_contexts.get(user_id, {})
    context = contexts.get(meeting_id)
    if not context:
        return {"meeting_id": meeting_id, "goals": None, "avoid": None}
    return context

@app.post("/api/meetings/{meeting_id}/context")
def save_meeting_context(meeting_id: str, context: MeetingContext, mp_session: Optional[str] = Cookie(None)):
    """Save context for a specific meeting."""
    user_id = _get_user_id(mp_session)
    if user_id not in _meeting_contexts:
        _meeting_contexts[user_id] = {}
    _meeting_contexts[user_id][meeting_id] = context.dict()
    return {"status": "saved", "meeting_id": meeting_id}

# ============================================================================
# Phase 3: Live Execution (Auto-Detect + Transcript + Deliverables)
# ============================================================================

@app.post("/api/proxy/start")
def start_proxy(meeting_id: str, context: Optional[MeetingContext] = None, mp_session: Optional[str] = Cookie(None)):
    """Start the agent for a meeting (auto-detected or manual trigger)."""
    user_id = _get_user_id(mp_session)
    profile = get_user_profile(user_id) or {}
    
    # Use pre-saved context or use provided context
    if not context:
        contexts = _meeting_contexts.get(user_id, {})
        context_dict = contexts.get(meeting_id)
        if context_dict:
            context = MeetingContext(**context_dict)
        else:
            context = MeetingContext(meeting_id=meeting_id, title="Meeting")
    
    # Build system prompt
    system_prompt = (
        f"You are attending a meeting as {profile.get('name', 'the user')} "
        f"({profile.get('title', 'Professional')}) from {profile.get('company', 'the company')}.\n\n"
        f"MEETING: {context.title}\n"
        f"GOALS: {context.goals or 'No specific goals set'}\n"
        f"AVOID: {context.avoid or 'Nothing flagged'}\n"
        f"FINANCIAL CAP: {context.financial_cap}\n"
        f"TIMELINE CAP: {context.timeline_cap}\n"
        f"OFF-LIMITS: {context.off_limits or 'None'}\n\n"
        f"Maintain a transcript, track decisions made, flag any commitments against your boundaries, "
        f"and prepare deliverables (notes, actions, flagged items) when the meeting ends."
    )
    
    _active_meetings[meeting_id] = {
        "user_id": user_id,
        "meeting_context": context.dict(),
        "system_prompt": system_prompt,
        "history": [],
        "transcript": [],
        "decisions": [],
        "actions": [],
        "flags": [],
    }
    
    return {"status": "agent started", "meeting_id": meeting_id}

@app.post("/api/proxy/transcript/{meeting_id}")
def inject_transcript(meeting_id: str, turn: TranscriptTurn):
    """Inject a transcript turn and get agent response."""
    session = _active_meetings.get(meeting_id)
    if not session:
        raise HTTPException(400, f"No active proxy for meeting {meeting_id}")
    
    # Store turn
    session["transcript"].append({"speaker": turn.speaker, "text": turn.text})
    
    # Mock agent response (replace with real agent.respond_to_turn)
    if agent:
        reply, updated_history = agent.respond_to_turn(
            session["system_prompt"],
            session["history"],
            turn.speaker,
            turn.text
        )
        session["history"] = updated_history
    else:
        # Fallback for testing
        reply = f"[Agent understood: {turn.text}]"
    
    return {"reply": reply, "turn_count": len(session["transcript"])}

@app.post("/api/proxy/end/{meeting_id}")
def end_proxy(meeting_id: str):
    """End the meeting and produce deliverables."""
    session = _active_meetings.get(meeting_id)
    if not session:
        raise HTTPException(400, f"No active meeting {meeting_id}")
    
    # Mock deliverables (replace with real agent.produce_deliverables)
    if agent:
        deliverables = agent.produce_deliverables(session["system_prompt"], session["history"])
    else:
        # Fallback
        deliverables = json.dumps({
            "meeting_summary": f"Meeting {meeting_id} concluded",
            "transcript": session["transcript"],
            "decisions": session["decisions"],
            "actions": session["actions"],
            "flagged_items": session["flags"],
        }, indent=2)
    
    result = {
        "meeting_id": meeting_id,
        "deliverables": deliverables,
        "transcript_turns": len(session["transcript"]),
    }
    
    _active_meetings.pop(meeting_id, None)
    return result

# ============================================================================
# UI Delivery
# ============================================================================

@app.get("/app", response_class=HTMLResponse)
def zoom_app():
    with open("static/index.html") as f:
        html = f.read()
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )
