import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    GOOGLE_SCOPES_READONLY = ["https://www.googleapis.com/auth/calendar.readonly"]
    GOOGLE_SCOPES_OWN_CALENDAR_EVENTS = ["https://www.googleapis.com/auth/calendar.events.owned"]

    MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
    MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")
    MS_TENANT_ID = os.getenv("MS_TENANT_ID", "common")
    MS_REDIRECT_URI = os.getenv("MS_REDIRECT_URI", "http://localhost:8000/auth/outlook/callback")
    MS_SCOPES_READONLY = ["Calendars.Read", "User.Read"]
    MS_SCOPES_OWN_CALENDAR_EVENTS = ["Calendars.ReadWrite"]

    ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID", "")
    ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET", "")
    ZOOM_REDIRECT_URI = os.getenv("ZOOM_REDIRECT_URI", "http://localhost:8000/auth/zoom/callback")

    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "")
    CONFIRMATION_TOKEN_TTL_MINUTES = int(os.getenv("CONFIRMATION_TOKEN_TTL_MINUTES", "15"))

    TOKEN_STORE_PATH = os.getenv("TOKEN_STORE_PATH", "token_store.json")
    TEMPLATES_STORE_PATH = os.getenv("TEMPLATES_STORE_PATH", "templates_store.json")
    CONFIRMATION_STORE_PATH = os.getenv("CONFIRMATION_STORE_PATH", "confirmation_store.json")
    USER_STORE_PATH = os.getenv("USER_STORE_PATH", "user_store.json")

    ENV = os.getenv("ENV", "development")

    @property
    def is_production(self):
        return self.ENV == "production"


settings = Settings()
