"""Testy M3: źródła danych i Market Data Adapter."""
import asyncio

from backend.adapters.market import MarketDataAdapter, ReplaySource, SyntheticSource
from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import EventType
from backend.core.types import ASSETS, Asset


async def _collect(source):
    out = []
    async for tick in source.ticks():
        out.append(tick)
    return out


def test_synthetic_counts_and_monotonic_time():
    ticks = asyncio.run(_collect(SyntheticSource(steps=20)))
    assert len(ticks) == 20 * len(ASSETS)
    ts = [t.ts for t in ticks]
    assert ts == sorted(ts)


def test_synthetic_has_dislocations_and_positive_funding():
    ticks = asyncio.run(_collect(SyntheticSource(steps=120, seed=1)))
    btc_basis = [t.basis_bps for t in ticks if t.asset == Asset.BTC]
    assert max(btc_basis) > 8.0           # widoczna dyslokacja ponad fair
    assert all(t.funding_rate > 0 for t in ticks)  # reżim v0 funding dodatni


def test_adapter_publishes_ticks_and_lag_warnings():
    bus = EventBus()
    counts = {EventType.MARKET_TICK: 0, EventType.DATA_LAG_WARNING: 0}
    for et in counts:
        bus.subscribe(et, lambda e, et=et: counts.__setitem__(et, counts[et] + 1))

    src = SyntheticSource(steps=5, lag_ms=2000.0)  # lag > próg → ostrzeżenia
    adapter = MarketDataAdapter(src, bus, SimClock(), lag_warn_ms=1000.0)
    n = asyncio.run(adapter.run())

    assert n == 5 * len(ASSETS)
    assert counts[EventType.MARKET_TICK] == n
    assert counts[EventType.DATA_LAG_WARNING] == n


def test_adapter_max_ticks_limit():
    bus = EventBus()
    src = SyntheticSource(steps=100)
    adapter = MarketDataAdapter(src, bus, SimClock())
    n = asyncio.run(adapter.run(max_ticks=10))
    assert n == 10


def test_replay_roundtrip():
    ticks = asyncio.run(_collect(SyntheticSource(steps=10)))
    out = asyncio.run(_collect(ReplaySource(ticks)))
    assert [t.ts for t in out] == [t.ts for t in ticks]
    assert len(out) == len(ticks)
