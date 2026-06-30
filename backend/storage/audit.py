"""AuditTrail (M8) — odtwarzanie decyzji z zapisanych eventów.

Pozwala odpowiedzieć na pytanie „dlaczego bot wszedł albo nie wszedł" dla danego
aktywa i momentu, rekonstruując łańcuch: sygnał → zamiar → decyzja ryzyka → fill.
Działa na trwałym audit trail z tabeli `events`.
"""
from __future__ import annotations

from ..core.events import EventType
from .db import Database

_SIGNAL_TYPES = {EventType.EDGE_DETECTED.value, EventType.NO_TRADE_CONDITION.value,
                 EventType.EDGE_LOST.value}
_RISK_TYPES = {EventType.RISK_APPROVED.value, EventType.RISK_REJECTED.value}


def _asset_of(event: dict) -> str | None:
    p = event.get("payload")
    if not isinstance(p, dict):
        return None
    if "asset" in p:
        return p["asset"]
    for key in ("intent", "req"):
        inner = p.get(key)
        if isinstance(inner, dict) and "asset" in inner:
            return inner["asset"]
    return None


class AuditTrail:
    def __init__(self, db: Database) -> None:
        self.db = db

    def timeline(self, asset: str | None = None, since: float | None = None,
                 until: float | None = None) -> list[dict]:
        out = []
        for e in self.db.all_events():
            if asset is not None and _asset_of(e) != asset:
                continue
            if since is not None and e["ts"] < since:
                continue
            if until is not None and e["ts"] > until:
                continue
            out.append(e)
        return out

    def explain(self, asset: str, around_ts: float, window: float = 3.0) -> str:
        evs = [e for e in self.db.all_events()
               if _asset_of(e) == asset and abs(e["ts"] - around_ts) <= window]

        def last(types: set) -> dict | None:
            return next((e for e in reversed(evs) if e["type"] in types), None)

        sig = last(_SIGNAL_TYPES)
        intent = last({EventType.TRADE_INTENT.value})
        risk = last(_RISK_TYPES)
        fill = last({EventType.FILL.value, EventType.PARTIAL_FILL.value})

        parts = [f"{asset} @ {around_ts:.0f}:"]
        if sig:
            p = sig["payload"] or {}
            parts.append(f"sygnał={sig['type']} ({p.get('reason', '')})")
        else:
            parts.append("brak sygnału w oknie")
        if intent:
            parts.append(f"zamiar={(intent['payload'] or {}).get('action', '?')}")
        if risk:
            dec = (risk["payload"] or {}).get("decision", {})
            verdict = "APPROVED" if dec.get("approved") else "REJECTED"
            parts.append(f"ryzyko={verdict} ({dec.get('reason', '')})")
        parts.append("→ WSZEDŁ (fill)" if fill else "→ brak wejścia")
        return " | ".join(parts)

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for e in self.db.all_events():
            counts[e["type"]] = counts.get(e["type"], 0) + 1
        return counts
