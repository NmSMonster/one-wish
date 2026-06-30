"""Testy wzbogacenia danych: forward funding, mark/premium, open interest."""
from backend.adapters.market.binance_public import predict_funding
from backend.core.types import Asset, MarketTick


def _tick(**kw) -> MarketTick:
    base = dict(asset=Asset.BTC, ts=1.0, spot=100.0, perp=100.5, index=100.0,
                funding_rate=0.0, predicted_funding=0.0, next_funding_ts=2.0,
                spot_bid=99.9, spot_ask=100.1, perp_bid=100.4, perp_ask=100.6,
                spot_depth_usd=1e6, perp_depth_usd=1e6)
    base.update(kw)
    return MarketTick(**base)


def test_predict_funding_premium_clamped():
    # premia 0.1% (mark 100.1 vs index 100), interest 0.01% → clamp do -0.05%
    # funding = 0.001 + clamp(0.0001 - 0.001) = 0.001 - 0.0005 = 0.0005
    assert abs(predict_funding(100.1, 100.0, 0.0001) - 0.0005) < 1e-12


def test_predict_funding_small_premium_equals_interest():
    # mark == index → premia 0 → funding = interest_rate
    assert abs(predict_funding(100.0, 100.0, 0.0001) - 0.0001) < 1e-12


def test_predict_funding_negative_premium():
    # mark poniżej index → premia ujemna → funding ujemny (short płaci)
    f = predict_funding(99.9, 100.0, 0.0001)
    assert f < 0


def test_predict_funding_zero_index_safe():
    assert predict_funding(100.0, 0.0, 0.0001) == 0.0


def test_market_tick_new_fields_default_zero():
    t = _tick()
    assert t.mark_price == 0.0
    assert t.open_interest_usd == 0.0
    assert t.interest_rate == 0.0
    assert t.premium_bps == 0.0          # mark=0 → property bezpieczna


def test_market_tick_premium_bps():
    t = _tick(mark_price=100.2)           # (100.2-100)/100 = 0.002 = 20 bps
    assert abs(t.premium_bps - 20.0) < 1e-6
