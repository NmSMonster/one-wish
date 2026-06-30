"""TickRecorder — zapis realnych ticków do JSONL i wczytanie do replayu.

Pozwala zebrać prawdziwy strumień Binance przez dłuższy czas (godziny/dni), a
potem przepuścić go offline przez bramę edge / backtest (ReplaySource). To jedyna
uczciwa droga do werdyktu o realnej przewadze — zwłaszcza carry, który gra się
przez 8-godzinne cykle funding.
"""
from __future__ import annotations

import json
from dataclasses import fields

from ..core.bus import EventBus
from ..core.events import Event, EventType
from ..core.types import Asset, MarketTick

_FIELDS = [f.name for f in fields(MarketTick)]


def tick_to_dict(tick: MarketTick) -> dict:
    d = {name: getattr(tick, name) for name in _FIELDS}
    d["asset"] = tick.asset.value
    return d


def tick_from_dict(d: dict) -> MarketTick:
    d = {k: v for k, v in d.items() if k in _FIELDS}
    d["asset"] = Asset(d["asset"])
    return MarketTick(**d)


class TickRecorder:
    def __init__(self, path: str, *, flush_every: int = 50,
                 heartbeat_every: int = 0, on_heartbeat=None) -> None:
        self.path = path
        self._fh = open(path, "a", encoding="utf-8")
        self.count = 0
        self.errors = 0
        self.flush_every = flush_every          # zrzut na dysk co N ticków (bezpieczeństwo długiego biegu)
        self.heartbeat_every = heartbeat_every  # 0 = bez heartbeatu
        self._on_heartbeat = on_heartbeat

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(EventType.MARKET_TICK, self._on_tick)

    def _on_tick(self, event: Event) -> None:
        if not isinstance(event.payload, MarketTick):
            return
        try:
            self._fh.write(json.dumps(tick_to_dict(event.payload)) + "\n")
            self.count += 1
            if self.flush_every and self.count % self.flush_every == 0:
                self._fh.flush()
            if (self.heartbeat_every and self.count % self.heartbeat_every == 0
                    and self._on_heartbeat is not None):
                self._on_heartbeat(self.count)
        except OSError:
            # transient błąd zapisu nie może zabić wielodniowego biegu
            self.errors += 1

    def close(self) -> None:
        try:
            self._fh.flush()
        finally:
            self._fh.close()


def load_ticks(path: str) -> list[MarketTick]:
    out: list[MarketTick] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(tick_from_dict(json.loads(line)))
    return out
