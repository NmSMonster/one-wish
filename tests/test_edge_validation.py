"""Testy M3.5: brama walidacji edge."""
import asyncio

from backend.adapters.market import SyntheticSource
from backend.model.costs import CostModel
from backend.model.fair_value import FairValueModel
from backend.research import EdgeValidator
from backend.signal.repricing import RepricingDetector
from tests.test_repricing import tick_with


async def _collect(source):
    out = []
    async for tick in source.ticks():
        out.append(tick)
    return out


def _validator():
    # M3.5 to brama strony DYSLOKACJI (carry walidujemy osobno: study_funding)
    return EdgeValidator(RepricingDetector(FairValueModel(), CostModel(), entry_mode="dislocation"))


def test_synthetic_stream_finds_edges():
    ticks = asyncio.run(_collect(SyntheticSource(steps=500, seed=3)))
    rep = _validator().analyze(ticks, label="synthetic")
    assert rep.edges > 0
    assert rep.verdict in ("PASS", "MARGINAL")
    assert rep.p95_dislocation_bps > 0


def test_flat_market_fails_gate():
    # rynek bez dyslokacji: basis stale ≈ fair → zero okazji
    ticks = [tick_with(2.0) for _ in range(200)]
    rep = _validator().analyze(ticks, label="flat")
    assert rep.edges == 0
    assert rep.verdict == "FAIL"
    assert "za mała dyslokacja" in rep.reason_counts


def test_report_summary_renders():
    ticks = asyncio.run(_collect(SyntheticSource(steps=120, seed=2)))
    text = _validator().analyze(ticks, label="x").summary()
    assert "EDGE VALIDATION" in text
    assert "WERDYKT" in text
