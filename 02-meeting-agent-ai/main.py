"""
Main FastAPI app — multi-user, Zoom App backend.
All endpoints are per-user, keyed by Zoom session cookie.
"""
import secrets
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Cookie, Response, Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app import google_client, outlook_client, agent, prompt_builder
from app.config import settings
from app.schemas import (
    ChecklistPayload, StandingTemplate, AddReminderRequest,
    NormalizedMeeting, ProfileRequest, MeetingSetupRequest,
)
from app.user_store import (
    get_google_token, set_google_token,
    get_user_profile, set_user_profile,
    get_user_templates, set_user_template,
    get_user,
)
from app.zoom_auth import (
    get_zoom_auth_url, exchange_zoom_code, get_zoom_user,
    make_session_token, read_session_token, SESSION_COOKIE,
)
from app.storage import JSONStore
from app.calendar_write import request_confirmation_token, ConfirmationError, WriteNotAuthorized

import os
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

app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_id(mp_session: Optional[str]) -> str:
    if not mp_session:
        raise HTTPException(401, "Not authenticated — open the app in Zoom first")
    uid = read_session_token(mp_session)
    if not uid:
        raise HTTPException(401, "Session expired — please reconnect")
    return uid


def _get_google_token_or_401(user_id: str) -> dict:
    token = get_google_token(user_id)
    if not token:
        raise HTTPException(403, "Google Calendar not connected — visit /auth/google/login")
    return token


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Zoom OAuth — entry point for the Zoom App sidebar
# ---------------------------------------------------------------------------

@app.get("/auth/zoom/login")
def zoom_login():
    state = secrets.token_urlsafe(16)
    _sessions[state] = None
    return RedirectResponse(get_zoom_auth_url(state))


@app.get("/auth/zoom/callback")
def zoom_callback(code: str, state: str, response: Response):
    if state not in _sessions:
        raise HTTPException(400, "Invalid OAuth state")
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


# ---------------------------------------------------------------------------
# Google Calendar OAuth — per user
# ---------------------------------------------------------------------------

@app.get("/auth/google/login")
def google_login(write: bool = False, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    redirect_uri = f"{os.getenv('APP_BASE_URL', 'http://localhost:8000')}/auth/google/callback"
    state = secrets.token_urlsafe(16)
    _sessions[state] = user_id
    url = google_client.get_login_url(include_write_scope=write, redirect_uri=redirect_uri)
    return RedirectResponse(url + f"&state={state}")


@app.get("/auth/google/callback")
def google_callback(code: str, state: str = "", write: bool = False):
    user_id = _sessions.pop(state, None)
    if not user_id:
        raise HTTPException(400, "Invalid OAuth state")
    redirect_uri = f"{os.getenv('APP_BASE_URL', 'http://localhost:8000')}/auth/google/callback"
    try:
        token_data = google_client.handle_callback(code, include_write_scope=write, redirect_uri=redirect_uri)
        set_google_token(user_id, token_data)
        return JSONResponse({"status": "google connected", "scopes": token_data.get("scopes")})
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# Outlook OAuth — per user
# ---------------------------------------------------------------------------

@app.get("/auth/outlook/login")
def outlook_login(write: bool = False, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    redirect_uri = f"{os.getenv('APP_BASE_URL', 'http://localhost:8000')}/auth/outlook/callback"
    state = secrets.token_urlsafe(16)
    _sessions[state] = user_id
    url = outlook_client.get_login_url(include_write_scope=write, redirect_uri=redirect_uri)
    return RedirectResponse(url + f"&state={state}")


@app.get("/auth/outlook/callback")
def outlook_callback(code: str, state: str = "", write: bool = False):
    user_id = _sessions.pop(state, None)
    if not user_id:
        raise HTTPException(400, "Invalid OAuth state")
    redirect_uri = f"{os.getenv('APP_BASE_URL', 'http://localhost:8000')}/auth/outlook/callback"
    try:
        token_data = outlook_client.handle_callback(code, include_write_scope=write, redirect_uri=redirect_uri)
        from app.user_store import set_user
        set_user(user_id, {"outlook_token": token_data})
        return JSONResponse({"status": "outlook connected"})
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

@app.get("/api/me")
def get_me(mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    profile = get_user_profile(user_id)
    user_data = get_user(user_id)
    return {
        "profile": profile,
        "google_connected": bool(user_data.get("google_token")),
        "outlook_connected": bool(user_data.get("outlook_token")),
    }


@app.post("/api/me/profile")
def save_profile(req: ProfileRequest, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    set_user_profile(user_id, req.dict())
    return {"status": "saved"}


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------

@app.get("/api/meetings/upcoming")
def upcoming_meetings(hours: int = 48, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    user_data = get_user(user_id)
    results = []
    errors = {}

    google_token = user_data.get("google_token")
    if google_token:
        try:
            results += [m.model_dump() for m in google_client.list_upcoming(google_token, hours)]
        except Exception as e:
            errors["google"] = str(e)

    outlook_token = user_data.get("outlook_token")
    if outlook_token:
        try:
            results += [m.model_dump() for m in outlook_client.list_upcoming(outlook_token, hours)]
        except Exception as e:
            errors["outlook"] = str(e)

    results.sort(key=lambda m: m["start"])
    return {"meetings": results, "errors": errors}


@app.get("/api/meetings/{source}/{event_id}/checklist")
def meeting_checklist(source: str, event_id: str, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    user_data = get_user(user_id)

    if source == "google":
        token = user_data.get("google_token")
        if not token:
            raise HTTPException(403, "Google not connected")
        meeting = google_client.get_event(token, event_id)
    elif source == "outlook":
        token = user_data.get("outlook_token")
        if not token:
            raise HTTPException(403, "Outlook not connected")
        meeting = outlook_client.get_event(token, event_id)
    else:
        raise HTTPException(404, "source must be google or outlook")

    templates = get_user_templates(user_id)
    matched = None
    if meeting.series_key and meeting.series_key in templates:
        from app.schemas import StandingTemplate
        matched = StandingTemplate(**templates[meeting.series_key])

    needs_manual = ["goals", "must_ask_questions", "boundary_overrides"]
    if meeting.agenda_missing:
        needs_manual.insert(0, "agenda")
    if not matched:
        needs_manual.append("relationship_context")

    return ChecklistPayload(
        meeting_name=meeting.title,
        date_time=meeting.start,
        attendees=meeting.attendees,
        agenda_from_invite=meeting.agenda_extracted,
        agenda_missing=meeting.agenda_missing,
        matched_template=matched,
        needs_manual_input=needs_manual,
    )


# ---------------------------------------------------------------------------
# Meeting session — live agent
# ---------------------------------------------------------------------------

@app.post("/api/meetings/start")
def start_meeting(req: MeetingSetupRequest, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    user_data = get_user(user_id)
    profile = get_user_profile(user_id)

    checklist = meeting_checklist(req.source, req.event_id, mp_session)

    system_prompt = prompt_builder.build_system_prompt(
        checklist=checklist,
        owner_name=profile.get("name", req.owner_name or "the user"),
        owner_title=profile.get("title", req.owner_title or ""),
        owner_company=profile.get("company", req.owner_company or ""),
        owner_style=req.owner_style or "professional",
        goals=req.goals or "",
        avoid=req.avoid or "",
        must_ask=req.must_ask or [],
        financial_cap=req.financial_cap or "$0 — flag all",
        timeline_cap=req.timeline_cap or "1 week",
        off_limits=req.off_limits or "",
        formality=req.formality or "professional",
        directness=req.directness or "get straight to the point",
        owner_phrases=req.owner_phrases or "",
        flag_everything=req.flag_everything or False,
    )

    _meeting_sessions[user_id] = {
        "history": [],
        "system_prompt": system_prompt,
        "event_id": req.event_id,
        "source": req.source,
        "decisions": [],
        "flags": [],
        "commitments": [],
    }

    return {"status": "meeting started", "system_prompt_preview": system_prompt[:200] + "..."}


@app.post("/api/meetings/transcript")
def process_transcript(
    speaker: str,
    text: str,
    mp_session: Optional[str] = Cookie(None),
):
    user_id = _get_user_id(mp_session)
    session = _meeting_sessions.get(user_id)
    if not session:
        raise HTTPException(400, "No active meeting session — call /api/meetings/start first")

    reply, updated_history = agent.respond_to_turn(
        system_prompt=session["system_prompt"],
        history=session["history"],
        speaker=speaker,
        text=text,
    )
    session["history"] = updated_history

    return {"reply": reply, "silent": reply is None}


@app.post("/api/meetings/end")
def end_meeting(mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    session = _meeting_sessions.get(user_id)
    if not session:
        raise HTTPException(400, "No active meeting session")

    deliverables = agent.produce_deliverables(
        system_prompt=session["system_prompt"],
        history=session["history"],
    )

    _meeting_sessions.pop(user_id, None)
    return {"deliverables": deliverables}


# ---------------------------------------------------------------------------
# Calendar reminders (write-gated)
# ---------------------------------------------------------------------------

@app.post("/api/meetings/{source}/{event_id}/add-reminder/request-token")
def request_reminder_token(
    source: str, event_id: str,
    title: str, start: str, end: str,
    notes: Optional[str] = None,
    mp_session: Optional[str] = Cookie(None),
):
    _get_user_id(mp_session)
    return request_confirmation_token(
        source=source, related_event_id=event_id,
        title=title, notes=notes, start=start, end=end,
    )


@app.post("/api/meetings/{source}/{event_id}/add-reminder")
def add_reminder(
    source: str, event_id: str,
    req: AddReminderRequest,
    mp_session: Optional[str] = Cookie(None),
):
    user_id = _get_user_id(mp_session)
    user_data = get_user(user_id)

    from app.calendar_write import _consume_token
    try:
        _consume_token(source, event_id, req)
    except ConfirmationError as e:
        raise HTTPException(409, str(e))

    body = {
        "summary": req.title,
        "description": req.notes or "",
        "start": {"dateTime": req.start},
        "end": {"dateTime": req.end},
    }

    if source == "google":
        token = user_data.get("google_token")
        if not token:
            raise HTTPException(403, "Google not connected")
        created = google_client.create_event(token, body)
        return {"status": "reminder created", "event_id": created["id"], "html_link": created.get("htmlLink")}
    elif source == "outlook":
        token = user_data.get("outlook_token")
        if not token:
            raise HTTPException(403, "Outlook not connected")
        ol_body = {
            "subject": req.title,
            "body": {"contentType": "text", "content": req.notes or ""},
            "start": {"dateTime": req.start, "timeZone": "UTC"},
            "end": {"dateTime": req.end, "timeZone": "UTC"},
        }
        created = outlook_client.create_event(token, ol_body)
        return {"status": "reminder created", "event_id": created["id"]}
    raise HTTPException(404, "source must be google or outlook")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

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
        raise HTTPException(404, "No template for that series_key")
    return templates[series_key]


# ---------------------------------------------------------------------------
# Zoom App frontend
# ---------------------------------------------------------------------------

@app.get("/app", response_class=HTMLResponse)
def zoom_app():
    with open("static/index.html") as f:
        return f.read()
