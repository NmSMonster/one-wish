"""Testy Tier A: FundingWeightedSizer — ważenie nominału siłą forward funding."""
from backend.strategy import FundingWeightedSizer


def test_weight_at_reference_is_one():
    s = FundingWeightedSizer(base_notional_usd=100.0, ref_funding_bps=1.5,
                             min_mult=0.5, max_mult=2.0)
    assert abs(s.weight(1.5) - 1.0) < 1e-9
    assert abs(s.size(1.5) - 100.0) < 1e-9


def test_higher_funding_gets_more_capital():
    s = FundingWeightedSizer(base_notional_usd=100.0, ref_funding_bps=1.5,
                             min_mult=0.1, max_mult=5.0)
    low = s.size(0.96)     # ~BTC forward bps/8h
    high = s.size(2.53)    # ~DOGE forward bps/8h
    assert high > low
    assert abs(low - 100.0 * 0.96 / 1.5) < 1e-9
    assert abs(high - 100.0 * 2.53 / 1.5) < 1e-9


def test_weight_clamped_to_min_mult_above():
    s = FundingWeightedSizer(base_notional_usd=100.0, ref_funding_bps=1.0,
                             min_mult=0.5, max_mult=2.0)
    assert s.weight(0.1) == 0.5           # bardzo niski funding → floor
    assert s.size(0.1) == 50.0


def test_weight_clamped_to_max_mult():
    s = FundingWeightedSizer(base_notional_usd=100.0, ref_funding_bps=1.0,
                             min_mult=0.5, max_mult=2.0)
    assert s.weight(50.0) == 2.0          # ekstremalny funding → cap
    assert s.size(50.0) == 200.0


def test_non_positive_funding_gets_floor_weight():
    s = FundingWeightedSizer(base_notional_usd=100.0, ref_funding_bps=1.5,
                             min_mult=0.5, max_mult=2.0)
    assert s.weight(0.0) == 0.5
    assert s.weight(-3.0) == 0.5          # ujemny funding: nie nagradzamy kapitałem


def test_absolute_notional_bounds_respected():
    s = FundingWeightedSizer(base_notional_usd=10.0, ref_funding_bps=1.0,
                             min_mult=0.1, max_mult=100.0,
                             min_notional_usd=20.0, max_notional_usd=50.0)
    assert s.size(0.01) == 20.0           # bardzo mały wynik → floor absolutny
    assert s.size(1000.0) == 50.0         # bardzo duży wynik → cap absolutny (RiskManager)


def test_zero_ref_funding_defaults_to_min_mult():
    s = FundingWeightedSizer(base_notional_usd=100.0, ref_funding_bps=0.0)
    assert s.weight(5.0) == s.min_mult    # brak sensownego punktu odniesienia → bezpieczny floor


def test_default_reference_reflects_universe_scan_ordering():
    # BTC/ETH/SOL/XRP/DOGE forward bps/8h ~ annualized_pct/100/1095*1e4 (UNIVERSE_SCAN.md)
    s = FundingWeightedSizer(base_notional_usd=100.0)   # domyślny ref_funding_bps
    btc = s.size(0.96)
    eth = s.size(1.73)
    doge = s.size(2.53)
    assert btc < eth < doge                # kapitał realnie rośnie z funding
    assert btc < 100.0 < doge              # BTC poniżej bazy, DOGE powyżej (przy domyślnym ref)
