"""Testy M5: CostModel i RepricingDetector."""
import asyncio

from backend.core.bus import EventBus
from backend.core.events import Event, EventType
from backend.core.types import Asset, MarketTick, SignalState
from backend.model.costs import CostModel
from backend.model.fair_value import FairValueModel
from backend.signal import RepricingDetector
from backend.strategy import FundingWeightedSizer


def tick_with(basis_bps, *, funding=0.0002, spot_spread_bps=1.0, perp_spread_bps=1.0,
              depth=500_000.0, lag=5.0, sec_to_funding=3600.0) -> MarketTick:
    index = 60_000.0
    spot = index
    perp = index * (1.0 + basis_bps / 1e4)
    hs = spot * spot_spread_bps / 1e4 / 2.0
    hp = perp * perp_spread_bps / 1e4 / 2.0
    return MarketTick(
        asset=Asset.BTC, ts=1000.0, spot=spot, perp=perp, index=index,
        funding_rate=funding, predicted_funding=funding,
        next_funding_ts=1000.0 + sec_to_funding,
        spot_bid=spot - hs, spot_ask=spot + hs, perp_bid=perp - hp, perp_ask=perp + hp,
        spot_depth_usd=depth, perp_depth_usd=depth, data_lag_ms=lag,
    )


def make_detector(**kw) -> RepricingDetector:
    kw.setdefault("entry_mode", "dislocation")
    return RepricingDetector(FairValueModel(), CostModel(), **kw)


# -- CostModel -------------------------------------------------------------- #
def test_cost_positive_and_scales_with_notional():
    cm = CostModel()
    small = cm.round_trip_cost_bps(tick_with(0), notional_usd=1_000.0)
    big = cm.round_trip_cost_bps(tick_with(0), notional_usd=400_000.0)
    assert small.total_bps > 0
    assert big.slippage_bps > small.slippage_bps
    # prowizje round-trip = 2*(7.5+1.8) = 18.6 bps
    assert small.fees_bps == 18.6


# -- Detector --------------------------------------------------------------- #
def test_strong_dislocation_is_edge():
    sig = make_detector().evaluate(tick_with(50.0))
    assert sig.state == SignalState.EDGE_DETECTED
    assert sig.expected_net_edge_bps > 2.0
    assert sig.dislocation_bps > 40.0


def test_small_basis_is_no_trade():
    sig = make_detector().evaluate(tick_with(3.0))
    assert sig.state == SignalState.NO_TRADE
    assert "dislocation" in sig.reason


def test_negative_funding_blocks_entry():
    sig = make_detector().evaluate(tick_with(60.0, funding=-0.0001))
    assert sig.state == SignalState.NO_TRADE
    assert "funding" in sig.reason


def test_wide_spread_blocks_entry():
    sig = make_detector().evaluate(tick_with(60.0, perp_spread_bps=25.0))
    assert sig.state == SignalState.NO_TRADE
    assert "spread" in sig.reason


def test_thin_liquidity_blocks_entry():
    # próg względny: depth_mult(5) × notional(200) = 1000; depth 500 < 1000 → blok
    sig = make_detector().evaluate(tick_with(60.0, depth=500.0))
    assert sig.state == SignalState.NO_TRADE


def test_near_settlement_blocks_entry():
    sig = make_detector().evaluate(tick_with(60.0, sec_to_funding=10.0))
    assert sig.state == SignalState.NO_TRADE


def make_carry_detector(**kw) -> RepricingDetector:
    return RepricingDetector(FairValueModel(), CostModel(), entry_mode="carry", **kw)


def test_carry_enters_on_positive_funding_without_dislocation():
    # basis ≈ fair (brak dyslokacji), ale funding dodatni → carry WCHODZI
    sig = make_carry_detector().evaluate(tick_with(2.0, funding=0.0002))
    assert sig.state == SignalState.EDGE_DETECTED
    assert "carry" in sig.reason


def test_carry_skips_negative_funding():
    sig = make_carry_detector().evaluate(tick_with(2.0, funding=-0.0001))
    assert sig.state == SignalState.NO_TRADE
    assert "funding" in sig.reason


def test_carry_skips_inverted_basis():
    sig = make_carry_detector().evaluate(tick_with(-20.0, funding=0.0002))
    assert sig.state == SignalState.NO_TRADE
    assert "odwrócony" in sig.reason


def test_edge_then_lost_transition_emits_events():
    bus = EventBus()
    captured: list = []
    for et in (EventType.EDGE_DETECTED, EventType.EDGE_LOST, EventType.NO_TRADE_CONDITION):
        bus.subscribe(et, lambda e: captured.append(e.type))

    det = make_detector()
    det.attach(bus)

    async def run():
        await bus.publish(Event(EventType.MARKET_TICK, 1.0, "t", payload=tick_with(50.0)))
        await bus.publish(Event(EventType.MARKET_TICK, 2.0, "t", payload=tick_with(2.0)))

    asyncio.run(run())
    assert EventType.EDGE_DETECTED in captured
    assert EventType.EDGE_LOST in captured  # przejście edge → brak edge


# -- Tier A: sizer-aware depth/koszt w detektorze (spójność z realnym nominałem) -- #
def _carry_detector(sizer=None, **kw) -> RepricingDetector:
    kw.setdefault("depth_mult", 5.0)
    return RepricingDetector(FairValueModel(), CostModel(), entry_mode="carry",
                             notional_usd=200.0, sizer=sizer, **kw)


def test_sizer_scaled_notional_fails_depth_that_flat_notional_would_pass():
    # funding=2bps → sizer (ref=1.0, min=1.0, max=2.0) waży ×2.0 → efektywny nominał 400$
    # → wymagana głębokość 5×400=2000. depth=1500 przechodzi próg PŁASKI (5×200=1000),
    # ale NIE przechodzi progu ważonego sizerem — to właśnie ta luka, którą naprawiliśmy.
    sizer = FundingWeightedSizer(base_notional_usd=200.0, ref_funding_bps=1.0,
                                 min_mult=1.0, max_mult=2.0)
    tick = tick_with(5.0, funding=0.0002, depth=1500.0)   # funding_bps = 2.0

    flat = _carry_detector(sizer=None).evaluate(tick)
    weighted = _carry_detector(sizer=sizer).evaluate(tick)

    assert flat.state == SignalState.EDGE_DETECTED          # płaski nominał: depth wystarcza
    assert weighted.state == SignalState.NO_TRADE            # ważony nominał: depth NIE wystarcza
    assert "płynność" in weighted.reason


def test_sizer_scaled_notional_increases_cost_estimate():
    sizer = FundingWeightedSizer(base_notional_usd=200.0, ref_funding_bps=1.0,
                                 min_mult=1.0, max_mult=2.0)
    tick = tick_with(5.0, funding=0.0002, depth=50_000.0)   # depth spokojnie wystarcza obu

    flat = _carry_detector(sizer=None).evaluate(tick)
    weighted = _carry_detector(sizer=sizer).evaluate(tick)

    assert flat.state == SignalState.EDGE_DETECTED
    assert weighted.state == SignalState.EDGE_DETECTED
    # ważony nominał (2×) → wyższy realny poślizg → wyższy szacowany koszt
    assert weighted.cost_bps > flat.cost_bps


def test_sizer_ignored_in_dislocation_mode():
    sizer = FundingWeightedSizer(base_notional_usd=200.0, ref_funding_bps=1.0,
                                 min_mult=1.0, max_mult=2.0)
    det = RepricingDetector(FairValueModel(), CostModel(), entry_mode="dislocation",
                            notional_usd=200.0, sizer=sizer)
    tick = tick_with(50.0, funding=0.0002, depth=1500.0)
    sig = det.evaluate(tick)
    # dislocation: sizer NIE wpływa (semantyka funding_bps specyficzna dla carry)
    assert sig.cost_bps == RepricingDetector(FairValueModel(), CostModel(),
                                             entry_mode="dislocation", notional_usd=200.0
                                             ).evaluate(tick).cost_bps
