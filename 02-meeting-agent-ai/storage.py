"""
Minimal local JSON file storage. Matches the simplicity trade-off documented
in calendar-integration-README.md: plaintext local files, single-user,
single-machine, NOT for shared/production use.
"""
import json
import os
import threading
from typing import Any, Optional

_lock = threading.Lock()


def _read(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _write(path: str, data: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


class JSONStore:
    def __init__(self, path: str):
        self.path = path

    def get(self, key: str) -> Optional[Any]:
        with _lock:
            return _read(self.path).get(key)

    def set(self, key: str, value: Any) -> None:
        with _lock:
            data = _read(self.path)
            data[key] = value
            _write(self.path, data)

    def delete(self, key: str) -> None:
        with _lock:
            data = _read(self.path)
            if key in data:
                del data[key]
                _write(self.path, data)

    def all(self) -> dict:
        with _lock:
            return _read(self.path)
