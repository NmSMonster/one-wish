"""Serializacja dowolnych obiektów domenowych do struktur JSON-safe.

Używane przez Storage (audit trail) i GUI API (WebSocket). Obsługuje dataklasy,
enumeracje, dicty, listy i typy proste.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any


def to_jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, str):
        return obj
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    return str(obj)
