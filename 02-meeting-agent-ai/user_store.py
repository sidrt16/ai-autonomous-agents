"""
Multi-user storage. Each Zoom user gets their own isolated slot in a
JSON file keyed by zoom_user_id. Replaces the single-user token_store
used in local dev — same JSONStore underneath, different key scheme.
"""
import os
from storage import JSONStore

_store = JSONStore(os.getenv("USER_STORE_PATH", "user_store.json"))


def get_user(zoom_user_id: str) -> dict:
    return _store.get(zoom_user_id) or {}


def set_user(zoom_user_id: str, data: dict) -> None:
    existing = get_user(zoom_user_id)
    existing.update(data)
    _store.set(zoom_user_id, existing)


def get_google_token(zoom_user_id: str) -> dict | None:
    return get_user(zoom_user_id).get("google_token")


def set_google_token(zoom_user_id: str, token_data: dict) -> None:
    set_user(zoom_user_id, {"google_token": token_data})


def get_user_profile(zoom_user_id: str) -> dict:
    return get_user(zoom_user_id).get("profile", {})


def set_user_profile(zoom_user_id: str, profile: dict) -> None:
    set_user(zoom_user_id, {"profile": profile})


def get_user_templates(zoom_user_id: str) -> dict:
    return get_user(zoom_user_id).get("templates", {})


def set_user_template(zoom_user_id: str, series_key: str, template: dict) -> None:
    templates = get_user_templates(zoom_user_id)
    templates[series_key] = template
    set_user(zoom_user_id, {"templates": templates})


def delete_user_data(zoom_user_id: str) -> None:
    """
    Permanently removes everything stored for this user (google_token,
    outlook_token, profile, templates — the whole record) in one shot,
    since get_user/set_user keep all of it under a single key.

    Required for Zoom's mandatory data-deletion on app deauthorization/
    uninstall — see /webhook/zoom/deauth in main.py.
    """
    _store.delete(zoom_user_id)
