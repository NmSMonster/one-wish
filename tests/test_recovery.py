"""Testy crash-safe recovery: odbudowa księgi z trwałego audit trailu."""
import asyncio

import pytest

from backend.adapters.exchange import PaperBrokerAdapter
from backend.app.pipeline import Pipeline
from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import Event, EventType
from backend.core.types import Asset, Fill, Leg, Side
from backend.execution import restore_book
from backend.storage import Database
from tests.test_repricing import tick_with
from tests.test_runner import _generous


def _record_pair(db: Database, *, qty=1.0, px=100.0, funding=0.12) -> None:
    db.record_fill(Fill("c1", Asset.BTC, Leg.SPOT, Side.BUY, px, qty, 0.05, 10.0))
    db.record_fill(Fill("c2", Asset.BTC, Leg.PERP, Side.SELL, px, qty, 0.02, 11.0))
    # FUNDING_ACCRUED idzie przez audit trail eventów
    asyncio.run(_publish_funding(db, amount=funding, ts=12.0))


async def _publish_funding(db: Database, *, amount: float, ts: float) -> None:
    bus = EventBus()
    db.attach(bus)
    await bus.publish(Event(EventType.FUNDING_ACCRUED, ts, "funding_accrual",
                            payload={"asset": "BTC", "amount": amount, "funding_rate": 0.0002}))


def test_restore_book_rebuilds_open_position_with_funding():
    db = Database()
    _record_pair(db)
    book = restore_book(db)
    pos = book.position(Asset.BTC)
    assert pos is not None and pos.is_open
    assert pos.spot_qty == pytest.approx(1.0)
    assert pos.perp_qty == pytest.approx(-1.0)          # short odzyskany
    assert pos.funding_accrued == pytest.approx(0.12)
    assert book.fees_paid == pytest.approx(0.07)
    assert book.funding_collected == pytest.approx(0.12)


def test_restore_book_closed_position_stays_flat_with_realized():
    db = Database()
    _record_pair(db, funding=0.0)
    # zamknięcie po rynku: spot 130 / perp 120 → realized +30 − 20 = +10
    db.record_fill(Fill("c3", Asset.BTC, Leg.SPOT, Side.SELL, 130.0, 1.0, 0.0, 20.0))
    db.record_fill(Fill("c4", Asset.BTC, Leg.PERP, Side.BUY, 120.0, 1.0, 0.0, 21.0))
    book = restore_book(db)
    assert not book.is_open(Asset.BTC)
    assert book.realized_pnl == pytest.approx(10.0)


def test_restore_book_skips_unknown_assets():
    db = Database()
    _record_pair(db)
    # stary plik DB może mieć aktywo usunięte z enuma — nie może wywrócić odbudowy
    db._conn.execute("INSERT INTO fills VALUES (?,?,?,?,?,?,?,?)",
                     (30.0, "cx", "DELISTED", "SPOT", "BUY", 5.0, 1.0, 0.0))
    db._conn.commit()
    book = restore_book(db)
    assert book.is_open(Asset.BTC)                      # znane aktywo odzyskane


def test_restart_recovers_positions_end_to_end(tmp_path):
    """E2E crash-restart: sesja 1 otwiera parę na PLIKOWEJ bazie i "pada" (bez
    zamknięcia). Sesja 2 na tej samej bazie odbudowuje księgę — pozycja wraca pod
    nadzór (ryzyko zna ekspozycję, polityka nie dubluje wejścia)."""
    path = str(tmp_path / "onewish.db")

    # sesja 1: pełny pipeline otwiera pozycję przez łańcuch sygnał→ryzyko→fill
    bus1 = EventBus()
    db1 = Database(path)
    db1.attach(bus1)
    pipe1 = Pipeline(bus1, risk_config=_generous(), broker=PaperBrokerAdapter(seed=1),
                     clock=SimClock())
    asyncio.run(bus1.publish(Event(EventType.MARKET_TICK, 1000.0, "s", payload=tick_with(60.0))))
    assert pipe1.book.is_open(Asset.BTC)
    db1.close()                                          # crash (księga w RAM znika)

    # sesja 2: świeży pipeline + odbudowa z tej samej bazy
    bus2 = EventBus()
    db2 = Database(path)
    pipe2 = Pipeline(bus2, risk_config=_generous(), broker=PaperBrokerAdapter(seed=1),
                     clock=SimClock())
    assert not pipe2.book.is_open(Asset.BTC)             # przed odbudową: nic
    restore_book(db2, pipe2.book)
    pos = pipe2.book.position(Asset.BTC)
    assert pos is not None and pos.is_open
    assert pos.spot_qty > 0 and pos.perp_qty < 0         # para delta-neutral wróciła
    assert abs(pos.net_delta) < 1e-9

    # nadzór: ekspozycja ryzyka + polityka (jak w runnerze)
    pipe2.risk.restore_exposure(Asset.BTC, abs(pos.spot_qty * pos.spot_entry))
    pipe2.policy.restore_holding(Asset.BTC)
    assert pipe2.risk.exposure[Asset.BTC] > 0
    assert pipe2.risk.trades_today == 0                  # odzysk ≠ dzisiejsza transakcja
    db2.close()


def test_report_counts_are_per_session_on_persistent_db(tmp_path):
    """Regresja: na trwałej bazie zliczenia raportu sumowały eventy WSZYSTKICH
    poprzednich sesji („wejścia: 6" w sesji, która nic nie otwierała). Granicą
    sesji jest rowid (ts nie rozdziela — SimClock każdej sesji startuje od zera)."""
    from backend.app.report import build_report

    path = str(tmp_path / "onewish.db")
    # sesja 1: zapisuje eventy
    bus1 = EventBus()
    db1 = Database(path)
    db1.attach(bus1)
    asyncio.run(bus1.publish(Event(EventType.MARKET_TICK, 1000.0, "s", payload=tick_with(60.0))))
    db1.close()

    # sesja 2: granica = max rowid PRZED nowymi eventami
    db2 = Database(path)
    boundary = db2.max_event_rowid()
    bus2 = EventBus()
    db2.attach(bus2)
    asyncio.run(bus2.publish(Event(EventType.MARKET_TICK, 1000.0, "s", payload=tick_with(60.0))))

    rep_session = build_report(db2, PositionBook(), since_rowid=boundary)
    rep_all = build_report(db2, PositionBook())
    assert rep_session.counts.get("MARKET_TICK", 0) == 1     # tylko ta sesja
    assert rep_all.counts.get("MARKET_TICK", 0) == 2         # cała historia pliku
    db2.close()


def test_restore_into_fresh_book_matches_original(tmp_path):
    """Inwariant: replay audit trailu odtwarza DOKŁADNIE stan księgi sprzed padu
    (pozycje, realized, fees, funding) — bo używa tej samej logiki księgowania."""
    path = str(tmp_path / "onewish.db")
    bus = EventBus()
    db = Database(path)
    db.attach(bus)
    pipe = Pipeline(bus, risk_config=_generous(), broker=PaperBrokerAdapter(seed=1),
                    clock=SimClock())
    # otwarcie + rozliczenie funding (drugi tick przekracza next_funding_ts)
    asyncio.run(bus.publish(Event(EventType.MARKET_TICK, 1000.0, "s", payload=tick_with(60.0))))
    asyncio.run(bus.publish(Event(EventType.MARKET_TICK, 4700.0, "s",
                                  payload=tick_with(60.0, sec_to_funding=7200.0))))
    original = pipe.book

    restored = restore_book(Database(path))
    orig_pos = original.position(Asset.BTC)
    rest_pos = restored.position(Asset.BTC)
    assert rest_pos.spot_qty == pytest.approx(orig_pos.spot_qty)
    assert rest_pos.perp_qty == pytest.approx(orig_pos.perp_qty)
    assert rest_pos.funding_accrued == pytest.approx(orig_pos.funding_accrued)
    assert restored.realized_pnl == pytest.approx(original.realized_pnl)
    assert restored.fees_paid == pytest.approx(original.fees_paid)
    assert restored.funding_collected == pytest.approx(original.funding_collected)
    db.close()
