"""
LifeCity Bot - Gestion de la pause manuelle
Persiste l'état dans pause_state.json.
"""

import json
import os

_STATE_FILE = os.path.join(os.path.dirname(__file__), "pause_state.json")
_paused = False


def _load() -> None:
    global _paused
    try:
        with open(_STATE_FILE) as f:
            data = json.load(f)
            _paused = bool(data.get("paused", False))
    except (FileNotFoundError, json.JSONDecodeError):
        _paused = False


def _save() -> None:
    with open(_STATE_FILE, "w") as f:
        json.dump({"paused": _paused}, f)


_load()


def is_paused() -> bool:
    return _paused


def set_paused(value: bool) -> None:
    global _paused
    _paused = value
    _save()
