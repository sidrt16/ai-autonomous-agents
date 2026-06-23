"""
Meeting Proxy Agent — Production Backend
Fully integrated with all real modules + robust error handling
"""
import os
import sys
import secrets
import logging
import traceback as _traceback
from datetime import datetime
from typing import Optional, List

# Configure logging FIRST
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logger.info("=" * 60)
logger.info("Meeting Proxy Agent Starting")
logger.info("=" * 60)
logger.info(f"Python version: {sys.version}")
logger.info(f"Working directory: {os.getcwd()}")

# Check critical files/directories exist
if not os.path.exists("static"):
    logger.warning("static/ directory not found - creating it")
    os.makedirs("static", exist_ok=True)

if not os.path.exists("static/index.html"):
    logger.warning("static/index.html not found - app will fail to load UI")

# Now import FastAPI
try:
    from fastapi import FastAPI, HTTPException, Cookie
    from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.requests import Request as _Request
    from starlette.middleware.base import BaseHTTPMiddleware
    from pydantic import BaseModel
    logger.info("✓ FastAPI imported")
except Exception as e:
    logger.error(f"✗ Failed to import FastAPI: {e}")
    raise

# Try importing all real modules with detailed error reporting
try:
    from config import settings
    logger.info(f"✓ config imported (APP_SECRET_KEY set: {bool(settings.APP_SECRET_KEY)})")
except Exception as e:
    logger.error(f"✗ config import failed: {e}")
    raise

try:
    from zoom_auth import (
        get_zoom_auth_url, exchange_zoom_code, get_zoom_user,
        make_session_token, read_session_token, SESSION_COOKIE, SESSION_MAX_AGE,
        make_connect_token, read_connect_token, CONNECT_TOKEN_MAX_AGE,
    )
    logger.info("✓ zoom_auth imported")
except Exception as e:
    logger.error(f"✗ zoom_auth import failed: {e}")
    raise

try:
    from user_store import get_user, set_user, get_user_profile, set_user_profile
    logger.info("✓ user_store imported")
except Exception as e:
    logger.error(f"✗ user_store import failed: {e}")
    raise

try:
    from google_client import (
        get_login_url as google_get_login_url,
        handle_callback as google_handle_callback,
        list_upcoming as google_list_upcoming,
    )
    logger.info("✓ google_client imported")
except Exception as e:
    logger.error(f"✗ google_client import failed: {e}")
    raise

try:
    from outlook_client import (
        get_login_url as outlook_get_login_url,
        handle_callback as outlook_handle_callback,
        list_upcoming as outlook_list_upcoming,
    )
    logger.info("✓ outlook_client imported")
except Exception as e:
    logger.error(f"✗ outlook_client import failed: {e}")
    raise

try:
    from agent import respond_to_turn, produce_deliverables
    logger.info("✓ agent imported")
except Exception as e:
    logger.error(f"✗ agent import failed: {e}")
    raise

try:
    from prompt_builder import build_system_prompt
    logger.info("✓ prompt_builder imported")
except Exception as e:
    logger.error(f"✗ prompt_builder import failed: {e}")
    raise

try:
    from schemas import ChecklistPayload, Attendee as SchemaAttendee
    logger.info("✓ schemas (ChecklistPayload, Attendee) imported")
except Exception as e:
    logger.error(f"✗ schemas import failed: {e}")
    raise

logger.info("=" * 60)
logger.info("All modules imported successfully!")
logger.info("=" * 60)

# Now initialize FastAPI
app = FastAPI(title="Meeting Proxy Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Restored from the last known-working deployment. Explicitly allowlists
    appssdk.zoom.us for script-src/connect-src so the Zoom Apps SDK script
    (loaded in static/index.html) and its calls back to the Zoom client are
    never blocked, including when the app is opened from inside a live
    meeting where Zoom's own client may be stricter about what it'll render.
    """
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

# Mount static files if they exist
if os.path.exists("static"):
    try:
        app.mount("/static", StaticFiles(directory="static"), name="static")
        logger.info("✓ Static files mounted")
    except Exception as e:
        logger.warning(f"Could not mount static files: {e}")

@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: _Request, exc: Exception):
    """
    Last-resort safety net. Any exception NOT already caught and handled
    inside a route (i.e. a genuine bug) lands here instead of producing a
    bare 500 with no body / crashing the worker. Full traceback goes to
    the Render log; the client gets a clean, structured JSON error.
    """
    logger.error(f"UNHANDLED EXCEPTION on {request.method} {request.url.path}: {exc}")
    logger.error(_traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"Internal error: {type(exc).__name__}: {str(exc)}",
            "path": str(request.url.path),
        },
    )

# Runtime state
_sessions = {}
_meeting_contexts = {}
_active_meetings = {}

# Models
class AttendeeIn(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    response_status: Optional[str] = None

class MeetingContext(BaseModel):
    meeting_id: str
    title: Optional[str] = None
    source: Optional[str] = None          # "google" | "outlook" | "impromptu"
    start: Optional[str] = None           # ISO timestamp, used as date_time in the prompt
    attendees: Optional[List[AttendeeIn]] = []
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

# Helpers
def _get_user_id(mp_session: Optional[str]) -> str:
    if not mp_session:
        raise HTTPException(401, "Not authenticated — sign in with Zoom first")
    uid = read_session_token(mp_session)
    if not uid:
        raise HTTPException(401, "Session expired — sign in again")
    return uid

def _get_user_id_for_oauth_start(mp_session: Optional[str], connect_token: Optional[str]) -> str:
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

# Routes
@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/status")
def status():
    return {
        "status": "running",
        "version": "1.0.0",
        "zoom_configured": bool(settings.ZOOM_CLIENT_ID),
        "google_configured": bool(settings.GOOGLE_CLIENT_ID),
        "outlook_configured": bool(settings.MS_CLIENT_ID),
        "anthropic_configured": bool(settings.ANTHROPIC_API_KEY),
    }

@app.get("/auth/zoom/login")
def zoom_login():
    state = secrets.token_urlsafe(16)
    _sessions[state] = None
    return RedirectResponse(get_zoom_auth_url(state))

@app.get("/auth/zoom/callback")
def zoom_callback(code: str, state: Optional[str] = None):
    if state and state not in _sessions:
        raise HTTPException(400, "Invalid OAuth state")
    if state:
        _sessions.pop(state, None)
    
    try:
        token_data = exchange_zoom_code(code)
        zoom_user = get_zoom_user(token_data["access_token"])
        user_id = zoom_user["id"]
        
        profile = get_user_profile(user_id) or {}
        if not profile.get("name"):
            set_user_profile(user_id, {
                "name": zoom_user.get("display_name", "User"),
                "email": zoom_user.get("email", ""),
            })
        
        session_token = make_session_token(user_id)
        response = RedirectResponse("/app")
        response.set_cookie(SESSION_COOKIE, session_token, httponly=True, samesite="lax", max_age=SESSION_MAX_AGE)
        logger.info(f"Zoom auth successful for {user_id}")
        return response
    except Exception as e:
        logger.error(f"Zoom callback failed: {e}")
        raise HTTPException(400, f"Zoom auth failed: {str(e)}")

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
    logger.info(f"Profile saved for {user_id}")
    return {"status": "saved"}

@app.get("/api/connect-token")
def connect_token(mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    token = make_connect_token(user_id)
    return {"connect_token": token, "expires_in": CONNECT_TOKEN_MAX_AGE}

@app.get("/auth/google/login")
def google_login(write: bool = False, connect_token: Optional[str] = None, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id_for_oauth_start(mp_session, connect_token)
    state = secrets.token_urlsafe(16)
    _sessions[state] = user_id
    
    try:
        url = google_get_login_url(include_write_scope=write, redirect_uri=settings.GOOGLE_REDIRECT_URI, state=state)
        return RedirectResponse(url)
    except Exception as e:
        logger.error(f"Google login failed: {e}")
        raise HTTPException(400, str(e))

@app.get("/auth/google/callback")
def google_callback(code: str, state: str = "", write: bool = False):
    user_id = _sessions.pop(state, None)
    if not user_id:
        return _connect_result_page(False, "Google Calendar", "Link expired.")
    
    try:
        token_data = google_handle_callback(code, include_write_scope=write, redirect_uri=settings.GOOGLE_REDIRECT_URI)
        user_data = get_user(user_id) or {}
        user_data["google_token"] = token_data
        set_user(user_id, user_data)
        logger.info(f"Google connected for {user_id}")
        return _connect_result_page(True, "Google Calendar")
    except Exception as e:
        logger.error(f"Google callback failed: {e}")
        return _connect_result_page(False, "Google Calendar", str(e))

@app.get("/auth/outlook/login")
def outlook_login(write: bool = False, connect_token: Optional[str] = None, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id_for_oauth_start(mp_session, connect_token)
    state = secrets.token_urlsafe(16)
    _sessions[state] = user_id
    
    try:
        url = outlook_get_login_url(include_write_scope=write, redirect_uri=settings.MS_REDIRECT_URI, state=state)
        return RedirectResponse(url)
    except Exception as e:
        logger.error(f"Outlook login failed: {e}")
        raise HTTPException(400, str(e))

@app.get("/auth/outlook/callback")
def outlook_callback(code: str, state: str = "", write: bool = False):
    user_id = _sessions.pop(state, None)
    if not user_id:
        return _connect_result_page(False, "Outlook Calendar", "Link expired.")
    
    try:
        token_data = outlook_handle_callback(code, include_write_scope=write, redirect_uri=settings.MS_REDIRECT_URI)
        user_data = get_user(user_id) or {}
        user_data["outlook_token"] = token_data
        set_user(user_id, user_data)
        logger.info(f"Outlook connected for {user_id}")
        return _connect_result_page(True, "Outlook Calendar")
    except Exception as e:
        logger.error(f"Outlook callback failed: {e}")
        return _connect_result_page(False, "Outlook Calendar", str(e))

@app.post("/api/meetings/impromptu")
def create_impromptu_meeting(title: str = "Impromptu Meeting", mp_session: Optional[str] = Cookie(None)):
    """
    Create an ad-hoc meeting that has no calendar entry — e.g. someone pings
    you for an unscheduled call. Returns a meeting_id the same shape as a
    calendar-derived one, so the rest of the Context Prep / Live Execution
    flow doesn't need to know the difference.
    """
    user_id = _get_user_id(mp_session)
    meeting_id = f"impromptu-{secrets.token_hex(6)}"
    meeting = {
        "meeting_id": meeting_id,
        "event_id": meeting_id,
        "source": "impromptu",
        "title": title,
        "start": datetime.utcnow().isoformat() + "Z",
        "attendees": [],
    }
    logger.info(f"Impromptu meeting created: {meeting_id} for {user_id}")
    return meeting

@app.get("/api/meetings/upcoming")
def upcoming_meetings(hours: int = 48, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    user_data = get_user(user_id) or {}
    results = []
    errors = {}

    google_token = user_data.get("google_token")
    if google_token:
        try:
            meetings = google_list_upcoming(google_token, hours)
            if meetings:
                results.extend([
                    m.dict() if hasattr(m, 'dict') else 
                    m.model_dump() if hasattr(m, 'model_dump') else 
                    m 
                    for m in meetings
                ])
        except Exception as e:
            logger.error(f"Google fetch failed: {e}")
            errors["google"] = str(e)

    outlook_token = user_data.get("outlook_token")
    if outlook_token:
        try:
            meetings = outlook_list_upcoming(outlook_token, hours)
            if meetings:
                results.extend([
                    m.dict() if hasattr(m, 'dict') else 
                    m.model_dump() if hasattr(m, 'model_dump') else 
                    m 
                    for m in meetings
                ])
        except Exception as e:
            logger.error(f"Outlook fetch failed: {e}")
            errors["outlook"] = str(e)

    results.sort(key=lambda m: m.get("start", "") if isinstance(m, dict) else getattr(m, "start", ""))
    return {"meetings": results, "errors": errors}
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
    logger.info(f"Context saved for {meeting_id}")
    return {"status": "saved", "meeting_id": meeting_id}

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
            context = MeetingContext(meeting_id=meeting_id, title="Impromptu Meeting", source="impromptu")
    
    # Build a real ChecklistPayload from whatever meeting metadata we have.
    # Attendees/start/title come from the saved context (carried through from
    # the calendar list when the user set context); falls back sanely for
    # impromptu meetings with no calendar entry at all.
    try:
        attendees = [
            SchemaAttendee(
                name=a.name,
                email=a.email or "unknown@unknown",
                response_status=a.response_status,
            )
            for a in (context.attendees or [])
        ]
        checklist = ChecklistPayload(
            meeting_name=context.title or "Meeting",
            date_time=context.start or "Not scheduled (impromptu)",
            attendees=attendees,
            agenda_from_invite=None,
            agenda_missing=True,
            matched_template=None,
            needs_manual_input=[],
        )
        system_prompt = build_system_prompt(
            checklist=checklist,
            owner_name=profile.get("name") or "the user",
            owner_title=profile.get("title") or "Professional",
            owner_company=profile.get("company") or "the company",
            owner_style=profile.get("communication_style") or "professional",
            goals=context.goals or "",
            avoid=context.avoid or "",
            must_ask=[],
            financial_cap=context.financial_cap or "$0 — flag all",
            timeline_cap=context.timeline_cap or "1 week",
            off_limits=context.off_limits or "",
            formality=context.formality or "professional",
            directness=context.directness or "balanced",
            owner_phrases="",
            flag_everything=False,
        )
        logger.info(f"Real system prompt built for {meeting_id} ({len(system_prompt)} chars)")
    except Exception as e:
        # Should not happen given the construction above, but never let a
        # malformed prompt take down meeting start — fall back to a minimal
        # but still usable prompt instead of a 500.
        logger.error(f"Prompt build failed, using fallback: {e}")
        system_prompt = (
            f"You are attending '{context.title}' as a stand-in for {profile.get('name', 'the user')}.\n"
            f"Goals: {context.goals or 'none specified'}\n"
            f"Avoid: {context.avoid or 'none specified'}\n"
            f"Financial cap: {context.financial_cap}\nTimeline cap: {context.timeline_cap}\n"
            f"Off-limits: {context.off_limits or 'none'}\n"
        )
    
    _active_meetings[meeting_id] = {
        "user_id": user_id,
        "context": context.dict(),
        "system_prompt": system_prompt,
        "history": [],
        "transcript": [],
    }
    
    logger.info(f"Proxy started for {meeting_id}")
    return {"status": "agent started", "meeting_id": meeting_id, "title": context.title}

@app.post("/api/proxy/transcript/{meeting_id}")
def inject_transcript(meeting_id: str, turn: TranscriptTurn):
    session = _active_meetings.get(meeting_id)
    if not session:
        raise HTTPException(400, f"No active proxy for {meeting_id}")
    
    session["transcript"].append({"speaker": turn.speaker, "text": turn.text})
    
    try:
        reply, updated_history = respond_to_turn(
            session["system_prompt"],
            session["history"],
            turn.speaker,
            turn.text
        )
        session["history"] = updated_history
    except Exception as e:
        logger.error(f"Agent response failed: {e}")
        reply = f"[Processing: {turn.text}]"
    
    return {"reply": reply, "turn_count": len(session["transcript"])}

@app.post("/api/proxy/end/{meeting_id}")
def end_proxy(meeting_id: str):
    session = _active_meetings.get(meeting_id)
    if not session:
        raise HTTPException(400, f"No active meeting {meeting_id}")
    
    try:
        deliverables = produce_deliverables(session["system_prompt"], session["history"])
    except Exception as e:
        logger.error(f"Deliverables failed: {e}")
        deliverables = f"Error: {str(e)}"
    
    _active_meetings.pop(meeting_id, None)
    logger.info(f"Proxy ended for {meeting_id}")
    
    return {
        "meeting_id": meeting_id,
        "deliverables": deliverables,
        "transcript_turns": len(session["transcript"]),
    }

@app.api_route("/app", methods=["GET", "HEAD"], response_class=HTMLResponse)
def zoom_app():
    try:
        with open("static/index.html") as f:
            html = f.read()
    except FileNotFoundError:
        html = "<h1>App UI not found</h1>"
    
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return RedirectResponse("/app")

@app.on_event("startup")
def startup():
    logger.info("=" * 60)
    logger.info("Application Started Successfully!")
    logger.info(f"Routes available: {len(app.routes)}")
    logger.info("=" * 60)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting uvicorn on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
