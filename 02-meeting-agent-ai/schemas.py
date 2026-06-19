from typing import Optional, List
from pydantic import BaseModel


class Attendee(BaseModel):
    name: Optional[str] = None
    email: str
    response_status: Optional[str] = None


class NormalizedMeeting(BaseModel):
    source: str
    event_id: str
    series_key: Optional[str] = None
    title: str
    start: str
    end: str
    timezone: Optional[str] = None
    attendees: List[Attendee] = []
    description_raw: Optional[str] = None
    agenda_extracted: Optional[str] = None
    agenda_missing: bool = True
    join_link: Optional[str] = None
    is_recurring: bool = False


class StandingTemplate(BaseModel):
    series_key: str
    relationship_context: Optional[str] = None
    standing_goals: Optional[str] = None
    boundary_financial_cap: Optional[str] = None
    boundary_timeline_cap: Optional[str] = None
    off_limits_topics: Optional[str] = None


class ChecklistPayload(BaseModel):
    meeting_name: str
    date_time: str
    attendees: List[Attendee]
    agenda_from_invite: Optional[str] = None
    agenda_missing: bool
    matched_template: Optional[StandingTemplate] = None
    needs_manual_input: List[str]


class AddReminderRequest(BaseModel):
    title: str
    notes: Optional[str] = None
    start: str
    end: str
    confirmation_token: str


class TokenRequestResponse(BaseModel):
    confirmation_token: str
    expires_at: str
    preview: dict
    message: str


class ProfileRequest(BaseModel):
    name: str
    title: Optional[str] = None
    company: Optional[str] = None
    style: Optional[str] = "professional"
    phrases: Optional[str] = None


class MeetingSetupRequest(BaseModel):
    source: str
    event_id: str
    goals: Optional[str] = None
    avoid: Optional[str] = None
    must_ask: Optional[List[str]] = []
    financial_cap: Optional[str] = "$0 — flag all"
    timeline_cap: Optional[str] = "1 week"
    off_limits: Optional[str] = None
    flag_everything: Optional[bool] = False
    formality: Optional[str] = "professional"
    directness: Optional[str] = "get straight to the point"
    owner_name: Optional[str] = None
    owner_title: Optional[str] = None
    owner_company: Optional[str] = None
    owner_style: Optional[str] = None
    owner_phrases: Optional[str] = None
