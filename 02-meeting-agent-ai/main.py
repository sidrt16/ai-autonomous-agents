"""
Main FastAPI app — Multi-user, Zoom App backend context engine.
All endpoints are per-user, keyed by Zoom session cookie.
"""
import os
import secrets
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query, Cookie, Response, Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

import google_client
import outlook_client
import agent
import prompt_builder
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

app = FastAPI(title="Meeting Proxy")

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security Headers Middleware
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

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Runtime in-memory registry for active meeting sessions
_sessions = {}  # state -> zoom_user_id (in-memory CSRF store)
_meeting_sessions = {}  # meeting_id/zoom_user_id -> runtime data state

class ActiveProxySetupRequest(BaseModel):
    meeting_id: str
    title: str
    goals: Optional[str] = None
    avoid: Optional[str] = None
    financial_cap: Optional[str] = "$0 — flag all"
    timeline_cap: Optional[str] = "1 week"
    off_limits: Optional[str] = None
    formality: Optional[str] = "professional"
    directness: Optional[str] = "balanced"

class TranscriptPayload(BaseModel):
    speaker: str
    text: str

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

def _get_user_id(mp_session: Optional[str]) -> str:
    if not mp_session:
        return "default_zoom_user"
    uid = read_session_token(mp_session)
    return uid if uid else "default_zoom_user"

def _get_user_id_for_oauth_start(mp_session: Optional[str], connect_token: Optional[str]) -> str:
    if mp_session:
        uid = read_session_token(mp_session)
        if uid:
            return uid
    if connect_token:
        uid = read_connect_token(connect_token)
        if uid:
            return uid
    return "default_zoom_user"

# ---------------------------------------------------------------------------
# Core Context Promotion Architecture (Fixed & Safe)
# ---------------------------------------------------------------------------

@app.post("/api/proxy/active-promote")
def promote_active_proxy(req: ActiveProxySetupRequest, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    profile = get_user_profile(user_id) or {}
    
    # Pack meeting properties matching standard object expectations
    mock_meeting_schema = {
        "title": req.title,
        "event_id": req.meeting_id,
        "agenda_missing": not bool(req.goals)
    }
    
    # Pack setup variables including runtime UI fields and fallback profile properties
    # to maintain compatibility with whatever keys prompt_builder relies on.
    setup_dict = {
        "standing_goals": req.goals or "",
        "relationship_context": f"Active Zoom Proxy. Avoidance Guardrail: {req.avoid or 'None'}",
        "boundary_financial_cap": req.financial_cap,
        "boundary_timeline_cap": req.timeline_cap,
        "off_limits_topics": req.off_limits,
        "formality": req.formality,
        "directness": req.directness,
        # Flattened profile bindings to prevent missing key errors inside the templates
        "name": profile.get("name", "User"),
        "title_role": profile.get("title", "Executive"),
        "company": profile.get("company", ""),
        "style": profile.get("style", "professional"),
        "phrases": profile.get("phrases", "")
    }
    
    try:
        # Eliminate 'profile=profile' entirely to avoid signature mismatches.
        # Support positional unpacking fallbacks natively if required by the environment.
        try:
            system_prompt = prompt_builder.build_system_prompt(
                meeting=mock_meeting_schema,
                setup=setup_dict
            )
        except TypeError:
            system_prompt = prompt_builder.build_system_prompt(
                mock_meeting_schema, 
                setup_dict
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prompt Compilation Failure: {str(e)}")
    
    _meeting_sessions[req.meeting_id] = {
        "history": [],
        "system_prompt": system_prompt,
        "metadata": req.dict()
    }
    
    return {"status": "ready", "meeting_id": req.meeting_id}

@app.post("/api/proxy/transcript/{meeting_id}")
def add_transcript(meeting_id: str, payload: TranscriptPayload):
    session = _meeting_sessions.get(meeting_id)
    if not session:
        raise HTTPException(status_code=400, detail="No active proxy deployed for this meeting space.")
        
    reply, updated_history = agent.respond_to_turn(
        session["system_prompt"],
        session["history"],
        payload.speaker,
        payload.text
    )
    session["history"] = updated_history
    return {"reply": reply}

@app.post("/api/proxy/end/{meeting_id}")
def end_meeting_proxy(meeting_id: str):
    session = _meeting_sessions.get(meeting_id)
    if not session:
        raise HTTPException(status_code=400, detail="No active runtime session found.")
    
    deliverables = agent.produce_deliverables(session["system_prompt"], session["history"])
    return {"deliverables": deliverables}

# ---------------------------------------------------------------------------
# Health & Auth Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}

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
        profile = get_user_profile(user_id)
        if not profile.get("name"):
            set_user_profile(user_id, {
                "name": zoom_user.get("display_name", ""),
                "email": zoom_user.get("email", ""),
                "zoom_user_id": user_id,
            })
        session_token = make_session_token(user_id)
        response = RedirectResponse("/app")
        response.set_cookie(SESSION_COOKIE, session_token, httponly=True, samesite="lax", max_age=60*60*24*7)
        return response
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/connect-token")
def connect_token(mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    return {"connect_token": make_connect_token(user_id), "expires_in": CONNECT_TOKEN_MAX_AGE}

# ---------------------------------------------------------------------------
# Third-Party Integrations OAuth Pass-Through
# ---------------------------------------------------------------------------

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
        return _connect_result_page(False, "Google Calendar", "Expired link.")
    redirect_uri = f"{os.getenv('APP_BASE_URL', 'http://localhost:8000')}/auth/google/callback"
    try:
        token_data = google_client.handle_callback(code, include_write_scope=write, redirect_uri=redirect_uri)
        set_google_token(user_id, token_data)
        return _connect_result_page(True, "Google Calendar")
    except Exception as e:
        return _connect_result_page(False, "Google Calendar", str(e))

# ---------------------------------------------------------------------------
# Profile & Template Management Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/profile")
def save_profile(profile: ProfileRequest, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    set_user_profile(user_id, profile.dict())
    return {"status": "saved"}

@app.get("/api/profile")
def get_profile(mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    return get_user_profile(user_id)

@app.post("/api/templates")
def save_template(template: StandingTemplate, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    set_user_template(user_id, template.series_key, template.dict())
    return template

@app.get("/api/templates/{series_key}")
def get_template(series_key: str, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    templates = get_user_templates(user_id)
    if series_key not in templates:
        raise HTTPException(404, "No template found for specified series key.")
    return templates[series_key]

# ---------------------------------------------------------------------------
# Zoom App UI Delivery Hook
# ---------------------------------------------------------------------------

@app.get("/app", response_class=HTMLResponse)
def zoom_app():
    with open("static/index.html") as f:
        html = f.read()
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store, no-cache"})
