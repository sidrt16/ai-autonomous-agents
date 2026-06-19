from typing import Optional
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import settings
from schemas import NormalizedMeeting, Attendee
from normalize import extract_agenda, extract_join_link


def _flow(scopes, redirect_uri=None) -> Flow:
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri or settings.GOOGLE_REDIRECT_URI],
            }
        },
        scopes=scopes,
        redirect_uri=redirect_uri or settings.GOOGLE_REDIRECT_URI,
    )


def get_login_url(include_write_scope: bool = False, redirect_uri: str = None) -> str:
    scopes = settings.GOOGLE_SCOPES_READONLY + (
        settings.GOOGLE_SCOPES_OWN_CALENDAR_EVENTS if include_write_scope else []
    )
    flow = _flow(scopes, redirect_uri)
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    return auth_url


def handle_callback(code: str, include_write_scope: bool = False, redirect_uri: str = None) -> dict:
    scopes = settings.GOOGLE_SCOPES_READONLY + (
        settings.GOOGLE_SCOPES_OWN_CALENDAR_EVENTS if include_write_scope else []
    )
    flow = _flow(scopes, redirect_uri)
    flow.oauth2session.scope = None
    flow.fetch_token(code=code)
    creds = flow.credentials
    granted_scopes = creds.granted_scopes or []
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": granted_scopes,
    }


def has_write_scope(token_data: dict) -> bool:
    granted = set(token_data.get("scopes", []))
    return set(settings.GOOGLE_SCOPES_OWN_CALENDAR_EVENTS).issubset(granted)


def _get_credentials(token_data: dict) -> Credentials:
    return Credentials(
        token=token_data["token"],
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"],
    )


def _service(token_data: dict):
    return build("calendar", "v3", credentials=_get_credentials(token_data))


def _normalize_event(raw: dict) -> NormalizedMeeting:
    description = raw.get("description")
    attendees = [
        Attendee(
            name=a.get("displayName"),
            email=a.get("email", ""),
            response_status=a.get("responseStatus"),
        )
        for a in raw.get("attendees", [])
        if a.get("email")
    ]
    agenda = extract_agenda(description)
    start = raw.get("start", {})
    end = raw.get("end", {})
    return NormalizedMeeting(
        source="google",
        event_id=raw["id"],
        series_key=raw.get("recurringEventId"),
        title=raw.get("summary", "(no title)"),
        start=start.get("dateTime", start.get("date", "")),
        end=end.get("dateTime", end.get("date", "")),
        timezone=start.get("timeZone"),
        attendees=attendees,
        description_raw=description,
        agenda_extracted=agenda,
        agenda_missing=agenda is None,
        join_link=extract_join_link(description) or raw.get("hangoutLink"),
        is_recurring=bool(raw.get("recurringEventId") or raw.get("recurrence")),
    )


def list_upcoming(token_data: dict, hours: int = 24) -> list[NormalizedMeeting]:
    import datetime
    svc = _service(token_data)
    now = datetime.datetime.utcnow()
    time_max = now + datetime.timedelta(hours=hours)
    resp = (
        svc.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat() + "Z",
            timeMax=time_max.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return [_normalize_event(e) for e in resp.get("items", [])]


def get_event(token_data: dict, event_id: str) -> NormalizedMeeting:
    svc = _service(token_data)
    raw = svc.events().get(calendarId="primary", eventId=event_id).execute()
    return _normalize_event(raw)


def create_event(token_data: dict, body: dict) -> dict:
    svc = _service(token_data)
    return svc.events().insert(calendarId="primary", body=body).execute()
