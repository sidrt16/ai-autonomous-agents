import re
from typing import Optional

AGENDA_HEADER_RE = re.compile(
    r"(?:^|\n)\s*(agenda|topics?)\s*:?\s*\n", re.IGNORECASE
)

JOIN_LINK_RE = re.compile(
    r"https?://(?:[\w-]+\.)?zoom\.us/j/\S+"
    r"|https?://teams\.microsoft\.com/l/meetup-join/\S+"
    r"|https?://meet\.google\.com/\S+",
    re.IGNORECASE,
)


def extract_agenda(description: Optional[str]) -> Optional[str]:
    """
    Looks for an explicit 'Agenda:' or 'Topics:' section in a free-text
    invite description. Returns None if nothing matches — per project
    philosophy (see calendar-integration-README.md), an unclear agenda
    is a signal to ask the human, not a gap to guess at.
    """
    if not description:
        return None
    match = AGENDA_HEADER_RE.search(description)
    if not match:
        return None
    rest = description[match.end():]
    # Stop at the next blank-line-delimited section header, if any
    stop = re.search(r"\n\s*\n[A-Z][a-zA-Z ]{2,20}:\s*\n", rest)
    agenda_text = rest[: stop.start()] if stop else rest
    agenda_text = agenda_text.strip()
    return agenda_text or None


def extract_join_link(description: Optional[str]) -> Optional[str]:
    if not description:
        return None
    match = JOIN_LINK_RE.search(description)
    return match.group(0) if match else None
