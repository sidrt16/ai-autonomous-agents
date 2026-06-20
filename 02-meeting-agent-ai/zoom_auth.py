"""
Zoom OAuth + session management.

Flow:
  1. User opens the Zoom App sidebar
  2. Zoom SDK provides a short-lived context token
  3. We exchange it for a real user token via /auth/zoom/callback
  4. We store zoom_user_id in a signed session cookie
  5. All subsequent requests use that session to look up the user's data
"""
import os
import requests
from base64 import b64encode
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from config import settings

SESSION_COOKIE = "mp_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# Short-lived bridge token: minted while still inside Zoom's authenticated
# webview (so the mp_session cookie is available), then carried as a query
# param into the external browser, where that cookie can't follow. This is
# what lets /auth/google/login and /auth/outlook/login know who's connecting
# even on a request with no cookie at all.
CONNECT_TOKEN_MAX_AGE = 60 * 10  # 10 minutes — long enough to click through Google's consent screen, short enough to not be a standing risk if leaked (e.g. via a shared screen or browser history)

_signer = URLSafeTimedSerializer(settings.APP_SECRET_KEY)

ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"
ZOOM_USER_URL = "https://api.zoom.us/v2/users/me"


def get_zoom_auth_url(state: str) -> str:
    return (
        f"https://zoom.us/oauth/authorize"
        f"?response_type=code"
        f"&client_id={settings.ZOOM_CLIENT_ID}"
        f"&redirect_uri={settings.ZOOM_REDIRECT_URI}"
        f"&state={state}"
    )


def exchange_zoom_code(code: str) -> dict:
    """Exchange auth code for Zoom access token. Returns token dict."""
    credentials = b64encode(
        f"{settings.ZOOM_CLIENT_ID}:{settings.ZOOM_CLIENT_SECRET}".encode()
    ).decode()
    resp = requests.post(
        ZOOM_TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.ZOOM_REDIRECT_URI,
        },
    )
    resp.raise_for_status()
    return resp.json()


def get_zoom_user(access_token: str) -> dict:
    """Fetch Zoom user profile — gives us the stable user ID."""
    resp = requests.get(
        ZOOM_USER_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    return resp.json()


def make_session_token(zoom_user_id: str) -> str:
    return _signer.dumps(zoom_user_id)


def read_session_token(token: str) -> str | None:
    try:
        return _signer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def make_connect_token(zoom_user_id: str) -> str:
    return _signer.dumps(zoom_user_id, salt="connect-token")


def read_connect_token(token: str) -> str | None:
    """Distinct salt from session tokens — a leaked/expired connect token
    can never be replayed as a session cookie, or vice versa."""
    try:
        return _signer.loads(token, max_age=CONNECT_TOKEN_MAX_AGE, salt="connect-token")
    except (BadSignature, SignatureExpired):
        return None
