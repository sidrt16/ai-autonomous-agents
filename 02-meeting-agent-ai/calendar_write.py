"""
The ONE write capability this service has: creating a new event on the
authenticated user's OWN primary calendar — e.g. "Follow up: confirm Q4
budget with Acme" after a meeting. This matches the explicitly allowed
action in meeting-proxy-agent-prompt.md Section 3 ("can add notes or
reminders to your own calendar") and runbook.md's one-time setup note.

Everything this module deliberately still refuses to do, no matter what's
passed in:
  - accept / decline / reschedule any existing event
  - modify any event this service didn't itself create
  - add or modify attendees on any event
  - send invitations to anyone
  - touch any calendar other than the authenticated user's primary

Two-step confirmation:
  1. POST .../add-reminder/request-token  -> returns a short-lived,
     single-use confirmation_token and a preview of exactly what will be
     created. Nothing is written yet.
  2. POST .../add-reminder with that token in the body -> only then does
     the event actually get created.

This means an agent can *propose* a reminder, but a human (or a human-
controlled side channel, per runbook.md's "DURING the meeting" section)
has to actually approve it before anything touches the calendar. Tokens
expire in CONFIRMATION_TOKEN_TTL_MINUTES and are deleted on first use, so
a token can't be replayed and a stale proposal can't execute late.
"""
import datetime
import secrets
from typing import Optional

from config import settings
from storage import JSONStore
from schemas import AddReminderRequest, TokenRequestResponse

_confirmations = JSONStore(settings.CONFIRMATION_STORE_PATH)


class WriteNotAuthorized(Exception):
    """Raised when the connected account hasn't granted the narrow
    own-calendar-events write scope. Read-only connection is not enough."""


class ConfirmationError(Exception):
    """Raised when a confirmation token is missing, expired, already used,
    or doesn't match the request it was issued for."""


def request_confirmation_token(
    source: str, related_event_id: str, title: str, notes: Optional[str], start: str, end: str
) -> TokenRequestResponse:
    token = secrets.token_urlsafe(24)
    now = datetime.datetime.utcnow()
    expires_at = now + datetime.timedelta(
        minutes=settings.CONFIRMATION_TOKEN_TTL_MINUTES
    )
    preview = {
        "calendar": "primary (your own calendar only)",
        "title": title,
        "notes": notes,
        "start": start,
        "end": end,
        "related_event_id": related_event_id,
        "source": source,
    }
    _confirmations.set(
        token,
        {
            "source": source,
            "related_event_id": related_event_id,
            "title": title,
            "notes": notes,
            "start": start,
            "end": end,
            "expires_at": expires_at.isoformat(),
            "used": False,
        },
    )
    return TokenRequestResponse(
        confirmation_token=token,
        expires_at=expires_at.isoformat(),
        preview=preview,
        message=(
            "Nothing has been written yet. POST this token to "
            f"/meetings/{source}/{related_event_id}/add-reminder within "
            f"{settings.CONFIRMATION_TOKEN_TTL_MINUTES} minutes to confirm, "
            "or let it expire to discard it."
        ),
    )


def _consume_token(source: str, related_event_id: str, req: AddReminderRequest) -> None:
    record = _confirmations.get(req.confirmation_token)
    if not record:
        raise ConfirmationError("Unknown or already-used confirmation token.")
    if record["used"]:
        raise ConfirmationError("This confirmation token has already been used.")
    expires_at = datetime.datetime.fromisoformat(record["expires_at"])
    if datetime.datetime.utcnow() > expires_at:
        _confirmations.delete(req.confirmation_token)
        raise ConfirmationError(
            "This confirmation token expired. Request a new one via "
            f"/meetings/{source}/{related_event_id}/add-reminder/request-token."
        )
    if (
        record["source"] != source
        or record["related_event_id"] != related_event_id
        or record["title"] != req.title
        or record["start"] != req.start
        or record["end"] != req.end
    ):
        raise ConfirmationError(
            "Confirmation token does not match this request's details. "
            "Request a fresh token for the exact event you want to create."
        )
    # Single-use: delete immediately so it can never be replayed.
    _confirmations.delete(req.confirmation_token)


def add_reminder_google(event_id: str, req: AddReminderRequest) -> dict:
    from app import google_client

    if not google_client.has_write_scope():
        raise WriteNotAuthorized(
            "Google Calendar is connected with read-only scope. To use "
            "add-reminder, re-authenticate via /auth/google/login?write=true "
            "to grant the narrow calendar.events.owned scope."
        )
    _consume_token("google", event_id, req)

    service = google_client._service()
    body = {
        "summary": req.title,
        "description": req.notes or "",
        "start": {"dateTime": req.start},
        "end": {"dateTime": req.end},
        # Deliberately no "attendees" key — this never invites anyone.
    }
    created = (
        service.events().insert(calendarId="primary", body=body).execute()
    )
    return {
        "status": "reminder created on your primary calendar",
        "event_id": created["id"],
        "html_link": created.get("htmlLink"),
        "related_event_id": event_id,
    }


def add_reminder_outlook(event_id: str, req: AddReminderRequest) -> dict:
    from app import outlook_client
    import httpx

    if not outlook_client.has_write_scope():
        raise WriteNotAuthorized(
            "Outlook is connected with read-only scope. To use add-reminder, "
            "re-authenticate via /auth/outlook/login?write=true to grant "
            "Calendars.ReadWrite (used here only for your own primary "
            "calendar, never for editing other people's events)."
        )
    _consume_token("outlook", event_id, req)

    body = {
        "subject": req.title,
        "body": {"contentType": "text", "content": req.notes or ""},
        "start": {"dateTime": req.start, "timeZone": "UTC"},
        "end": {"dateTime": req.end, "timeZone": "UTC"},
        # No "attendees" key — this never invites anyone.
    }
    resp = httpx.post(
        "https://graph.microsoft.com/v1.0/me/events",
        headers=outlook_client._headers(),
        json=body,
    )
    resp.raise_for_status()
    created = resp.json()
    return {
        "status": "reminder created on your primary calendar",
        "event_id": created["id"],
        "html_link": created.get("webLink"),
        "related_event_id": event_id,
    }
