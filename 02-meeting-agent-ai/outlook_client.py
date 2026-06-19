from typing import Optional
import msal
import httpx

from app.config import settings
from app.schemas import NormalizedMeeting, Attendee
from app.normalize import extract_agenda, extract_join_link

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _msal_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.MS_CLIENT_ID,
        client_credential=settings.MS_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{settings.MS_TENANT_ID}",
    )


def get_login_url(include_write_scope: bool = False, redirect_uri: str = None) -> str:
    scopes = settings.MS_SCOPES_READONLY + (
        settings.MS_SCOPES_OWN_CALENDAR_EVENTS if include_write_scope else []
    )
    return _msal_app().get_authorization_request_url(
        scopes, redirect_uri=redirect_uri or settings.MS_REDIRECT_URI
    )


def handle_callback(code: str, include_write_scope: bool = False, redirect_uri: str = None) -> dict:
    scopes = settings.MS_SCOPES_READONLY + (
        settings.MS_SCOPES_OWN_CALENDAR_EVENTS if include_write_scope else []
    )
    result = _msal_app().acquire_token_by_authorization_code(
        code, scopes=scopes, redirect_uri=redirect_uri or settings.MS_REDIRECT_URI
    )
    if "access_token" not in result:
        raise RuntimeError(f"Outlook auth failed: {result.get('error_description')}")
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token"),
        "scopes": scopes,
    }


def has_write_scope(token_data: dict) -> bool:
    return "Calendars.ReadWrite" in token_data.get("scopes", [])


def _headers(token_data: dict) -> dict:
    return {"Authorization": f"Bearer {token_data['access_token']}"}


def _normalize_event(raw: dict) -> NormalizedMeeting:
    body = (raw.get("body") or {}).get("content")
    agenda = extract_agenda(body)
    attendees = [
        Attendee(
            name=(a.get("emailAddress") or {}).get("name"),
            email=(a.get("emailAddress") or {}).get("address", ""),
            response_status=(a.get("status") or {}).get("response"),
        )
        for a in raw.get("attendees", [])
        if (a.get("emailAddress") or {}).get("address")
    ]
    return NormalizedMeeting(
        source="outlook",
        event_id=raw["id"],
        series_key=raw.get("seriesMasterId"),
        title=raw.get("subject", "(no title)"),
        start=(raw.get("start") or {}).get("dateTime", ""),
        end=(raw.get("end") or {}).get("dateTime", ""),
        timezone=(raw.get("start") or {}).get("timeZone"),
        attendees=attendees,
        description_raw=body,
        agenda_extracted=agenda,
        agenda_missing=agenda is None,
        join_link=extract_join_link(body) or (raw.get("onlineMeeting") or {}).get("joinUrl"),
        is_recurring=bool(raw.get("seriesMasterId")),
    )


def list_upcoming(token_data: dict, hours: int = 24) -> list[NormalizedMeeting]:
    import datetime
    now = datetime.datetime.utcnow()
    end = now + datetime.timedelta(hours=hours)
    params = {
        "startDateTime": now.isoformat() + "Z",
        "endDateTime": end.isoformat() + "Z",
        "$orderby": "start/dateTime",
    }
    resp = httpx.get(f"{_GRAPH_BASE}/me/calendarView", headers=_headers(token_data), params=params)
    resp.raise_for_status()
    return [_normalize_event(e) for e in resp.json().get("value", [])]


def get_event(token_data: dict, event_id: str) -> NormalizedMeeting:
    resp = httpx.get(f"{_GRAPH_BASE}/me/events/{event_id}", headers=_headers(token_data))
    resp.raise_for_status()
    return _normalize_event(resp.json())


def create_event(token_data: dict, body: dict) -> dict:
    resp = httpx.post(f"{_GRAPH_BASE}/me/events", headers=_headers(token_data), json=body)
    resp.raise_for_status()
    return resp.json()
