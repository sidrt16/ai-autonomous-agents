from typing import Optional
from config import settings
from storage import JSONStore
from schemas import StandingTemplate

_store = JSONStore(settings.TEMPLATES_STORE_PATH)


def save_template(template: StandingTemplate) -> StandingTemplate:
    _store.set(template.series_key, template.model_dump())
    return template


def get_template(series_key: str) -> Optional[StandingTemplate]:
    data = _store.get(series_key)
    return StandingTemplate(**data) if data else None
