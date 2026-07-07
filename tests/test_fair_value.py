"""Testy M4: Fair Value Model."""
import asyncio

from backend.adapters.market import MarketDataAdapter, SyntheticSource
from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import EventType
from backend.model import FairValueModel
from tests.test_core import make_tick


def test_fair_basis_equals_funding_when_no_carry():
    model = FairValueModel(carry_cost_bps=0.0)
    tick = make_tick(predicted_funding=0.0002)  # 2 bps
    fv = model.evaluate(tick)
    assert fv.fair_basis_bps == 0.0002 * 1e4  # 2.0 bps
    assert fv.expected_funding_bps == 2.0


def test_carry_cost_added():
    model = FairValueModel(carry_cost_bps=0.5)
    fv = model.evaluate(make_tick(predicted_funding=0.0002))
    assert fv.fair_basis_bps == 2.5


def test_confidence_drops_with_lag():
    model = FairValueModel()
    high = model.evaluate(make_tick(data_lag_ms=5.0)).confidence
    low = model.evaluate(make_tick(data_lag_ms=1500.0)).confidence
    assert 0.0 <= low < high <= 1.0


def test_time_scale_reduces_fair_basis_near_settlement():
    model = FairValueModel(time_scale=True, funding_period_s=8 * 3600.0)
    # blisko settlementu (mało czasu) → mniejszy oczekiwany funding
    near = model.evaluate(make_tick(predicted_funding=0.0002,
                                    next_funding_ts=1000.0 + 60, ts=1000.0))
    far = model.evaluate(make_tick(predicted_funding=0.0002,
                                   next_funding_ts=1000.0 + 7200, ts=1000.0))
    assert near.fair_basis_bps < far.fair_basis_bps


def test_attach_publishes_fair_value_per_tick():
    bus = EventBus()
    seen = {"n": 0}
    bus.subscribe(EventType.FAIR_VALUE, lambda e: seen.__setitem__("n", seen["n"] + 1))

    model = FairValueModel()
    model.attach(bus)
    src = SyntheticSource(steps=5)
    adapter = MarketDataAdapter(src, bus, SimClock())
    n = asyncio.run(adapter.run())
    assert seen["n"] == n
