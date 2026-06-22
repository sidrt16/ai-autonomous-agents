"""
Main FastAPI app — multi-user, Zoom App backend.
All endpoints are per-user, keyed by Zoom session cookie.
"""
import os
import secrets
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Cookie, Response, Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import google_client, outlook_client, agent, prompt_builder
from config import settings
from schemas import (
    ChecklistPayload, StandingTemplate, AddReminderRequest,
    NormalizedMeeting, ProfileRequest, MeetingSetupRequest,
)
from user_store import (
    get_google_token, set_google_token,
    get_user_profile, set_user_profile,
    get_user_templates, set_user_template,
    get_user,
)
from zoom_auth import (
    get_zoom_auth_url, exchange_zoom_code, get_zoom_user,
    make_session_token, read_session_token, SESSION_COOKIE,
    make_connect_token, read_connect_token, CONNECT_TOKEN_MAX_AGE,
)
from storage import JSONStore
from calendar_write import request_confirmation_token, ConfirmationError, WriteNotAuthorized

_sessions = {}  # state -> zoom_user_id (in-memory CSRF store)
_meeting_sessions = {}  # zoom_user_id -> {history, system_prompt, event_id, source}
_confirmation_store = JSONStore(settings.CONFIRMATION_STORE_PATH)

app = FastAPI(title="Meeting Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://appssdk.zoom.us; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self' https://appssdk.zoom.us"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")

def _get_user_id(mp_session: Optional[str]) -> str:
    if not mp_session:
        return "default_zoom_user"
    user_id = read_session_token(mp_session)
    if not user_id:
        return "default_zoom_user"
    return user_id

# ---------------------------------------------------------------------------
# Calendar Authorization Gateways
# ---------------------------------------------------------------------------

@app.get("/auth/google/login")
def google_login(mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    state = make_connect_token(user_id)
    return RedirectResponse(google_client.get_login_url(state=state))

@app.get("/auth/google/callback")
def google_callback(code: str, state: str):
    user_id = read_connect_token(state) or "default_zoom_user"
    token_data = google_client.handle_callback(code)
    set_google_token(user_id, token_data)
    return HTMLResponse("<h3>Google Calendar Linked Successfully! You can close this window and refresh your Zoom app.</h3>")

# ---------------------------------------------------------------------------
# Core APIs
# ---------------------------------------------------------------------------

@app.get("/api/meetings")
def list_meetings(source: str = "google", mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    if source == "google":
        token = get_google_token(user_id)
        if not token:
            return {"connected": False, "meetings": []}
        try:
            return {"connected": True, "meetings": [m.dict() for m in google_client.list_upcoming(token)]}
        except Exception as e:
            return {"connected": True, "meetings": [], "error": str(e)}
    else:
        user_data = get_user(user_id)
        token = user_data.get("outlook_token")
        if not token:
            return {"connected": False, "meetings": []}
        try:
            return {"connected": True, "meetings": [m.dict() for m in outlook_client.list_upcoming(token)]}
        except Exception as e:
            return {"connected": True, "meetings": [], "error": str(e)}

@app.post("/api/meetings/active-match")
async def active_match_meeting(request: Request, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    
    zoom_id = str(payload.get("meetingId", "")).strip()
    zoom_uuid = str(payload.get("meetingUUID", "")).strip()
    
    meetings = []
    source = "google"
    
    google_token = get_google_token(user_id)
    if google_token:
        try:
            meetings = google_client.list_upcoming(google_token, hours=24)
            source = "google"
        except Exception:
            pass

    if zoom_id or zoom_uuid:
        for meeting in meetings:
            link = meeting.join_link or ""
            if (zoom_id and zoom_id in link) or (zoom_uuid and zoom_uuid in link):
                return {
                    "matched": True,
                    "source": source,
                    "event_id": meeting.event_id,
                    "meeting": meeting.dict()
                }
            
    return {
        "matched": False,
        "available_meetings": [m.dict() for m in meetings],
        "source": source
    }

@app.post("/api/meetings/setup")
def setup_meeting(req: MeetingSetupRequest, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    if req.source == "google":
        token = get_google_token(user_id)
        mtg = google_client.get_event(token, req.event_id)
    else:
        user_data = get_user(user_id)
        mtg = outlook_client.get_event(user_data.get("outlook_token"), req.event_id)
        
    profile = get_user_profile(user_id)
    sys_prompt = prompt_builder.build_system_prompt(profile, mtg, req)
    
    _meeting_sessions[user_id] = {
        "history": [],
        "system_prompt": sys_prompt,
        "event_id": req.event_id,
        "source": req.source
    }
    return {"status": "ready", "checklist": {"meeting_name": mtg.title, "agenda_missing": mtg.agenda_missing}}

@app.post("/api/meetings/transcript")
def add_transcript(payload: dict, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    session = _meeting_sessions.get(user_id)
    if not session:
        raise HTTPException(status_code=400, detail="No active meeting session running.")
        
    reply, updated_history = agent.respond_to_turn(
        session["system_prompt"],
        session["history"],
        payload.get("speaker", "Unknown"),
        payload.get("text", "")
    )
    session["history"] = updated_history
    return {"reply": reply}

@app.post("/api/meetings/end")
def end_meeting(mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    session = _meeting_sessions.get(user_id)
    if not session:
        raise HTTPException(status_code=400, detail="No active session found.")
    deliverables = agent.produce_deliverables(session["system_prompt"], session["history"])
    return {"deliverables": deliverables}

@app.get("/app", response_class=HTMLResponse)
def zoom_app():
    with open("static/index.html") as f:
        html = f.read()
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store, no-cache"})
