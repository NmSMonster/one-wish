"""Testy analizy carry na historii funding."""
from backend.research.funding_study import (
    analyze_funding,
    count_funding_gaps,
    infer_settle_per_year,
    rates_from_history,
    simulate_carry_always_in,
    simulate_carry_positive_only,
    simulate_carry_smoothed,
)

_H = 3_600_000  # 1h w ms


def _history(rate: float, n: int, interval_h: float, start: int = 1_600_000_000_000):
    return [{"fundingRate": rate, "fundingTime": start + i * int(interval_h * _H)} for i in range(n)]


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


# -- interwał funding + poprawna annualizacja (fix na realny werdykt) -------- #
def test_infer_settle_per_year_8h_is_standard():
    iv = infer_settle_per_year(_history(0.0001, 300, 8.0))
    assert abs(iv["interval_hours"] - 8.0) < 0.1
    assert iv["standard_8h"] is True
    assert abs(iv["settle_per_year"] - 3 * 365) < 1.0


def test_infer_settle_per_year_4h_is_non_standard():
    iv = infer_settle_per_year(_history(0.0001, 300, 4.0))
    assert abs(iv["interval_hours"] - 4.0) < 0.1
    assert iv["standard_8h"] is False
    assert abs(iv["settle_per_year"] - 6 * 365) < 1.0     # 4h → 6/dobę


def test_infer_settle_per_year_1h():
    iv = infer_settle_per_year(_history(0.0001, 300, 1.0))
    assert abs(iv["interval_hours"] - 1.0) < 0.1
    assert iv["standard_8h"] is False


def test_infer_settle_per_year_too_few_defaults_8h():
    iv = infer_settle_per_year([{"fundingRate": 0.0001, "fundingTime": 1}])
    assert iv["standard_8h"] is True and iv["interval_hours"] == 8.0


def test_annualization_uses_real_interval():
    # ten sam per-period rate: 4h aktywo ma 2× wyższy roczny funding niż 8h
    rate = 0.0001357
    ann_8h = analyze_funding([rate] * 100).annualized_pct
    ann_4h = analyze_funding([rate] * 100, settle_per_year=6 * 365).annualized_pct
    assert abs(ann_4h - 2 * ann_8h) < 1e-6                # NIE zaniżamy 4h-aktywów


def test_smoothed_net_scales_with_settle_per_year():
    rates = [0.0002] * 200
    net_8h = simulate_carry_smoothed(rates, 18.6).annualized_net_pct
    net_4h = simulate_carry_smoothed(rates, 18.6, settle_per_year=6 * 365).annualized_net_pct
    assert net_4h > net_8h                                # gęstsze rozliczenia → wyższy roczny


def test_count_funding_gaps_flags_4h_as_irregular():
    # 4h historia: wszystkie odstępy < 8h → oznaczone jako nieregularne (jak w realnym werdykcie)
    g = count_funding_gaps(_history(0.0001, 100, 4.0))
    assert g["irregular_gaps"] == 99
    assert g["missing_settlements"] == 0
