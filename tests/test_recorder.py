"""Testy: TickRecorder — round-trip zapis/odczyt realnych ticków."""
import asyncio

from backend.adapters.market import SyntheticSource
from backend.core.bus import EventBus
from backend.core.events import Event, EventType
from backend.research.recorder import TickRecorder, load_ticks, tick_from_dict, tick_to_dict


async def _collect(source):
    out = []
    async for tick in source.ticks():
        out.append(tick)
    return out


def test_tick_dict_roundtrip():
    ticks = asyncio.run(_collect(SyntheticSource(steps=3)))
    for t in ticks:
        back = tick_from_dict(tick_to_dict(t))
        assert back.asset == t.asset
        assert back.spot == t.spot and back.perp == t.perp and back.index == t.index
        assert back.funding_rate == t.funding_rate
        assert back.basis_bps == t.basis_bps          # property odtworzona z pól


def test_recorder_writes_and_loads(tmp_path):
    path = str(tmp_path / "ticks.jsonl")
    bus = EventBus()
    rec = TickRecorder(path)
    rec.attach(bus)

    ticks = asyncio.run(_collect(SyntheticSource(steps=5)))

    async def feed():
        for t in ticks:
            await bus.publish(Event(EventType.MARKET_TICK, t.ts, "s", payload=t))

    asyncio.run(feed())
    rec.close()

    loaded = load_ticks(path)
    assert len(loaded) == len(ticks)
    assert loaded[0].asset == ticks[0].asset
    assert loaded[-1].perp == ticks[-1].perp
