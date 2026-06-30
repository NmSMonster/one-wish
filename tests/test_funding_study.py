"""Testy analizy carry na historii funding."""
from backend.research.funding_study import (
    analyze_funding,
    rates_from_history,
    simulate_carry_always_in,
    simulate_carry_positive_only,
    simulate_carry_smoothed,
)


def test_rates_from_history_parsing():
    hist = [{"fundingRate": "0.0001", "fundingTime": 1},
            {"fundingRate": "0.0002"}, {"brak": 1}]
    assert rates_from_history(hist) == [0.0001, 0.0002]


def test_analyze_funding_stats():
    st = analyze_funding([0.0001, 0.0002, -0.0001])
    assert st.n == 3
    assert abs(st.mean_bps - 2 / 3) < 1e-9        # (1+2-1)/3
    assert st.median_bps == 1.0
    assert abs(st.pct_positive - 200 / 3) < 1e-9  # 2 z 3
    assert st.max_bps == 2.0 and st.min_bps == -1.0


def test_analyze_funding_empty():
    st = analyze_funding([])
    assert st.n == 0 and st.mean_bps == 0.0


def test_carry_always_in():
    r = simulate_carry_always_in([0.0001, 0.0002, -0.0001], round_trip_fee_bps=18.6)
    assert r.funding_collected_bps == 2.0          # 1+2-1
    assert r.fees_bps == 18.6
    assert r.net_bps == 2.0 - 18.6
    assert r.num_cycles == 1


def test_carry_positive_only():
    # 1bp wejscie, 2bp trzymaj, -1bp wyjscie
    r = simulate_carry_positive_only([0.0001, 0.0002, -0.0001], round_trip_fee_bps=18.6)
    assert r.funding_collected_bps == 3.0          # 1+2 (nie liczymy ujemnego — wyjscie)
    assert r.fees_bps == 18.6
    assert r.num_cycles == 1
    assert r.periods_held == 2


def test_carry_always_in_positive_regime_profitable():
    # rok stabilnie dodatniego funding 1bp/8h: ~1095 bps brutto, fee raz -> mocno dodatni
    rates = [0.0001] * 1095
    r = simulate_carry_always_in(rates, round_trip_fee_bps=18.6)
    assert r.net_bps > 1000
    assert r.annualized_net_pct > 9.0              # ~10.7% rocznie


def test_smoothed_holds_through_single_negative_beats_churn():
    # 8 dodatnich, 1 ujemny blip, 8 dodatnich
    rates = [0.0002] * 8 + [-0.0001] + [0.0002] * 8
    sm = simulate_carry_smoothed(rates, round_trip_fee_bps=18.6, window=9)
    pos = simulate_carry_positive_only(rates, round_trip_fee_bps=18.6)
    assert sm.num_cycles == 1          # trzyma przez blip (jedno wejscie)
    assert pos.num_cycles == 2         # churn: wyjscie+ponowne wejscie
    assert sm.net_bps > pos.net_bps    # smoothed lepszy (mniej prowizji)
