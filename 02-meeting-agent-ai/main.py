"""
Main FastAPI app — Multi-user Meeting Proxy Context Engine.
All configurations are tied to the active Zoom Meeting context.
"""
import os
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Cookie, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

import agent
import prompt_builder
from config import settings
from schemas import StandingTemplate, ProfileRequest
from user_store import (
    get_user_profile, set_user_profile,
    get_user_templates, set_user_template,
)
from zoom_auth import read_session_token

app = FastAPI(title="Meeting Proxy")

# CORS Setup
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
app.mount("/static", StaticFiles(directory="static"), name="static")

# Runtime in-memory registry for active meeting contexts
_meeting_sessions = {}  # meeting_id/zoom_user_id -> {history, system_prompt, metadata}

def _get_user_id(mp_session: Optional[str]) -> str:
    if not mp_session:
        return "default_zoom_user"
    user_id = read_session_token(mp_session)
    return user_id if user_id else "default_zoom_user"

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
# Core Context Promotion Architecture
# ---------------------------------------------------------------------------

@app.post("/api/proxy/active-promote")
def promote_active_proxy(req: ActiveProxySetupRequest, mp_session: Optional[str] = Cookie(None)):
    user_id = _get_user_id(mp_session)
    profile = get_user_profile(user_id)
    
    # Structural shim transforming active Zoom runtime fields into agent prompt schema
    mock_meeting_schema = {
        "title": req.title,
        "event_id": req.meeting_id,
        "agenda_missing": not bool(req.goals)
    }
    
    # Construct complete high-fidelity context payload for Claude
    system_prompt = prompt_builder.build_system_prompt(
        profile=profile,
        meeting=mock_meeting_schema,
        setup=req
    )
    
    # Bind session history container keying directly on the live meeting ID
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
# Settings & Global Profiles Management
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
        raise HTTPException(404, "No template for that series_key")
    return templates[series_key]

@app.get("/app", response_class=HTMLResponse)
def zoom_app():
    with open("static/index.html") as f:
        html = f.read()
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store, no-cache"})
