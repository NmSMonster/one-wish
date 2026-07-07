"""Database — sqlite dla One Wish.

Zapisuje:
- pełny audit trail eventów (tabela `events`) — każdy event z szyny,
- typowane obserwacje do analityki: ticki, sygnały, fille, PnL.

Sqlite jest w zupełności wystarczające dla naszej przepustowości, jest w stdlib
i daje trwały, odpytywalny log. Audit trail umożliwia odtworzenie "dlaczego bot
wszedł / nie wszedł" (M8).
"""
from __future__ import annotations

import json
import logging
import sqlite3

from ..core.bus import EventBus
from ..core.events import Event, EventType
from ..core.serialize import to_jsonable
from ..core.types import Fill, MarketTick, PnLSnapshot, Signal

log = logging.getLogger("onewish.db")


class Database:
    def __init__(self, path: str = ":memory:", *, tick_commit_every: int = 25) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Commit per event dusi pipeline przy żywym feedzie (fsync na każdy tick).
        # Ticki batchujemy (co N); WSZYSTKO inne (sygnały, fille, decyzje, PnL)
        # commitujemy natychmiast — to są dane decyzyjne, ticki są odtwarzalne.
        # Odczyty na tym samym połączeniu widzą niezcommitowane wiersze (bez zmian).
        self.tick_commit_every = max(1, tick_commit_every)
        self._ticks_pending = 0
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, ts REAL, type TEXT, source TEXT,
                severity TEXT, payload TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

            CREATE TABLE IF NOT EXISTS ticks (
                ts REAL, asset TEXT, spot REAL, perp REAL, "index" REAL,
                basis_bps REAL, funding_rate REAL, data_lag_ms REAL
            );
            CREATE INDEX IF NOT EXISTS idx_ticks_asset ON ticks(asset, ts);

            CREATE TABLE IF NOT EXISTS signals (
                ts REAL, asset TEXT, state TEXT, observed_basis_bps REAL,
                fair_basis_bps REAL, dislocation_bps REAL,
                expected_net_edge_bps REAL, cost_bps REAL, reason TEXT
            );

            CREATE TABLE IF NOT EXISTS fills (
                ts REAL, client_order_id TEXT, asset TEXT, leg TEXT, side TEXT,
                price REAL, qty REAL, fee REAL
            );

            CREATE TABLE IF NOT EXISTS pnl (
                ts REAL, realized REAL, unrealized REAL, funding_collected REAL,
                fees_paid REAL, net REAL
            );
            """
        )
        self._conn.commit()

    # -- spięcie z szyną (audit trail) -------------------------------------- #
    def attach(self, bus: EventBus) -> None:
        """Subskrybuje wszystkie eventy i zapisuje je do bazy."""
        bus.subscribe_all(self._on_event)

    def _on_event(self, event: Event) -> None:
        try:
            payload = json.dumps(to_jsonable(event.payload))
            self._conn.execute(
                "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)",
                (event.id, event.ts, event.type.value, event.source,
                 event.severity.value, payload),
            )
            # routing do typowanych tabel
            p = event.payload
            if event.type == EventType.MARKET_TICK and isinstance(p, MarketTick):
                self.record_tick(p)
            elif event.type in (EventType.EDGE_DETECTED, EventType.EDGE_LOST,
                                EventType.NO_TRADE_CONDITION) and isinstance(p, Signal):
                self.record_signal(p)
            elif event.type in (EventType.FILL, EventType.PARTIAL_FILL) and isinstance(p, Fill):
                self.record_fill(p)
            elif event.type == EventType.PNL_UPDATE and isinstance(p, PnLSnapshot):
                self.record_pnl(p)
            if event.type == EventType.MARKET_TICK:
                self._ticks_pending += 1
                if self._ticks_pending >= self.tick_commit_every:
                    self._conn.commit()
                    self._ticks_pending = 0
            else:
                self._conn.commit()
                self._ticks_pending = 0
        except Exception:  # noqa: BLE001
            log.exception("Nie udało się zapisać eventu %s", event.type)

    # -- zapisy typowane ---------------------------------------------------- #
    def record_tick(self, t: MarketTick) -> None:
        self._conn.execute(
            'INSERT INTO ticks VALUES (?,?,?,?,?,?,?,?)',
            (t.ts, t.asset.value, t.spot, t.perp, t.index, t.basis_bps,
             t.funding_rate, t.data_lag_ms),
        )

    def record_signal(self, s: Signal) -> None:
        self._conn.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?)",
            (s.ts, s.asset.value, s.state.value, s.observed_basis_bps,
             s.fair_basis_bps, s.dislocation_bps, s.expected_net_edge_bps,
             s.cost_bps, s.reason),
        )

    def record_fill(self, f: Fill) -> None:
        self._conn.execute(
            "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?)",
            (f.ts, f.client_order_id, f.asset.value, f.leg.value, f.side.value,
             f.price, f.qty, f.fee),
        )

    def record_pnl(self, p: PnLSnapshot) -> None:
        self._conn.execute(
            "INSERT INTO pnl VALUES (?,?,?,?,?,?)",
            (p.ts, p.realized, p.unrealized, p.funding_collected, p.fees_paid, p.net),
        )

    # -- odczyt ------------------------------------------------------------- #
    def count(self, table: str) -> int:
        cur = self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608 — stała nazwa
        return int(cur.fetchone()["n"])

    def recent_events(self, limit: int = 50, event_type: EventType | None = None) -> list[dict]:
        if event_type is not None:
            cur = self._conn.execute(
                "SELECT * FROM events WHERE type=? ORDER BY ts DESC LIMIT ?",
                (event_type.value, limit),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in cur.fetchall()]

    def ticks_for(self, asset: str) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM ticks WHERE asset=? ORDER BY ts", (asset,)
        )
        return [dict(r) for r in cur.fetchall()]

    def all_events(self) -> list[dict]:
        """Wszystkie eventy w kolejności wystąpienia (ts, potem kolejność wstawienia),
        z payloadem sparsowanym z JSON. Podstawa audytu/replay (M8)."""
        cur = self._conn.execute("SELECT * FROM events ORDER BY ts ASC, rowid ASC")
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"]) if d["payload"] else None
            except (ValueError, TypeError):
                pass
            rows.append(d)
        return rows

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()
