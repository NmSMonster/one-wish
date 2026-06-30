"""EventBus — asynchroniczna szyna pub/sub.

Obsługuje subskrybentów synchronicznych i asynchronicznych. Trzyma ograniczoną
historię eventów (in-memory) na potrzeby audytu i testów; trwały audit trail
zapisuje Storage (M8).
"""
from __future__ import annotations

import inspect
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Deque

from .events import Event, EventType

log = logging.getLogger("onewish.bus")

Handler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    def __init__(self, history_limit: int = 10_000) -> None:
        self._subs: dict[EventType, list[Handler]] = defaultdict(list)
        self._all: list[Handler] = []
        self._history: Deque[Event] = deque(maxlen=history_limit)

    # -- subskrypcja --------------------------------------------------------- #
    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._subs[event_type].append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        """Subskrypcja wszystkich eventów (np. Storage, GUI API, Monitoring)."""
        self._all.append(handler)

    # -- publikacja ---------------------------------------------------------- #
    async def publish(self, event: Event) -> None:
        self._history.append(event)
        handlers = list(self._subs.get(event.type, ())) + list(self._all)
        for handler in handlers:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 — jeden zły subskrybent nie kładzie szyny
                log.exception("Handler błąd dla eventu %s", event.type)

    # -- audyt --------------------------------------------------------------- #
    @property
    def history(self) -> list[Event]:
        return list(self._history)

    def history_of(self, *types: EventType) -> list[Event]:
        wanted = set(types)
        return [e for e in self._history if e.type in wanted]
