"""Testy walidatora portfelowego Tier A: parytet ze smoothed, wagi, drawdown."""
import pytest

from backend.research.funding_study import simulate_carry_smoothed
from backend.research.portfolio_study import (
    align_tail,
    equal_weights,
    funding_weights,
    normalize_weights,
    return_on_capital,
    simulate_portfolio,
    smoothed_equity_curve,
    top_n_assets,
    walk_forward,
)

FEE = 18.6


def _const(rate: float, n: int) -> list[float]:
    return [rate] * n


# -- parytet z dotychczasowym werdyktem (co do bps) -------------------------- #
def test_curve_final_matches_simulate_carry_smoothed():
    # mieszana historia: dodatnia, dip, odwrócenie reżimu, powrót
    rates = ([0.0002] * 30 + [-0.0001] * 2 + [0.0003] * 20
             + [-0.0004] * 15 + [0.0002] * 40)
    curve = smoothed_equity_curve(rates, FEE)
    ref = simulate_carry_smoothed(rates, FEE)
    assert len(curve) == len(rates)
    assert abs(curve[-1] - ref.net_bps) < 1e-9        # identyczny wynik końcowy


def test_single_asset_portfolio_equals_smoothed_annualized():
    rates = _const(0.0002, 300)
    res = simulate_portfolio({"BTC": rates}, {"BTC": 1.0}, round_trip_fee_bps=FEE)
    ref = simulate_carry_smoothed(rates, FEE)
    assert abs(res.final_net_bps - ref.net_bps) < 1e-9
    assert abs(res.annualized_net_pct - ref.annualized_net_pct) < 1e-9


# -- wagi -------------------------------------------------------------------- #
def test_equal_weights_sum_to_one():
    w = equal_weights(["A", "B", "C", "D"])
    assert all(abs(v - 0.25) < 1e-12 for v in w.values())


def test_normalize_drops_nonpositive_and_sums_to_one():
    w = normalize_weights({"A": 2.0, "B": 0.0, "C": -1.0, "D": 2.0})
    assert set(w) == {"A", "D"}
    assert abs(sum(w.values()) - 1.0) < 1e-12


def test_normalize_all_zero_raises():
    with pytest.raises(ValueError):
        normalize_weights({"A": 0.0, "B": -1.0})


def test_funding_weights_use_production_sizer_semantics():
    # BTC ~0.96 bps, DOGE ~2.53 bps (UNIVERSE_SCAN) → DOGE dostaje więcej kapitału
    w = funding_weights({"BTC": 0.96, "DOGE": 2.53})
    assert w["DOGE"] > w["BTC"]
    assert abs(sum(w.values()) - 1.0) < 1e-12
    # dokładny stosunek = stosunek wag sizera (0.96/1.5 vs 2.53/1.5, bez clampu)
    assert abs(w["DOGE"] / w["BTC"] - 2.53 / 0.96) < 1e-9


def test_funding_weights_clamped_for_extremes():
    # ekstremalny funding nie zasysa całego kapitału (max_mult), ujemny nie zeruje (min_mult)
    w = funding_weights({"HOT": 500.0, "COLD": -3.0})
    assert abs(w["HOT"] / w["COLD"] - 2.0 / 0.5) < 1e-9   # stosunek = max_mult/min_mult


def test_top_n_picks_best_by_metric():
    m = {"BTC": 10.5, "ETH": 18.9, "SOL": 22.2, "XRP": 21.9, "DOGE": 27.7}
    assert set(top_n_assets(m, 3)) == {"DOGE", "SOL", "XRP"}


# -- align ------------------------------------------------------------------- #
def test_align_tail_trims_to_common_recent_window():
    out = align_tail({"A": [1.0, 2.0, 3.0, 4.0], "B": [9.0, 8.0]})
    assert out["A"] == [3.0, 4.0]                 # ogon, nie początek
    assert out["B"] == [9.0, 8.0]


def test_align_tail_drops_empty_histories():
    assert "B" not in align_tail({"A": [1.0], "B": []})


# -- portfel: zwrot i ryzyko ------------------------------------------------- #
def test_weighted_beats_equal_when_funding_differs():
    rates = {"LOW": _const(0.0001, 600), "HIGH": _const(0.0003, 600)}
    mean_bps = {"LOW": 1.0, "HIGH": 3.0}
    eq = simulate_portfolio(rates, equal_weights(["LOW", "HIGH"]), round_trip_fee_bps=FEE)
    fw = simulate_portfolio(rates, funding_weights(mean_bps, ref_funding_bps=1.5),
                            round_trip_fee_bps=FEE)
    assert fw.annualized_net_pct > eq.annualized_net_pct   # sedno Tier A


def test_drawdown_measured_on_negative_stretch():
    # 30 dodatnich → 12 mocno ujemnych (smoothed trzyma przez część minusów) → odbicie
    rates = _const(0.0002, 30) + _const(-0.0005, 12) + _const(0.0002, 30)
    res = simulate_portfolio({"X": rates}, {"X": 1.0}, round_trip_fee_bps=FEE)
    assert res.max_drawdown_pct > 0
    assert res.worst_settlement_bps < 0
    assert res.calmar is not None


def test_calmar_none_when_no_drawdown():
    res = simulate_portfolio({"X": _const(0.0003, 200)}, {"X": 1.0},
                             round_trip_fee_bps=0.0)     # bez fee krzywa tylko rośnie
    assert res.max_drawdown_pct == 0.0
    assert res.calmar is None


def test_missing_history_for_weighted_asset_raises():
    with pytest.raises(ValueError):
        simulate_portfolio({"A": _const(0.0002, 10)}, {"A": 0.5, "GHOST": 0.5})


def test_weights_are_normalized_in_result():
    res = simulate_portfolio({"A": _const(0.0002, 50), "B": _const(0.0002, 50)},
                             {"A": 3.0, "B": 1.0})
    assert abs(res.weights["A"] - 0.75) < 1e-12
    assert abs(sum(res.weights.values()) - 1.0) < 1e-12


# -- zwrot na kapitale (nie nominale) --------------------------------------- #
def test_return_on_capital_applies_leverage_factor():
    # kapitał = nominał(1+1/lev); ROC = zwrot_nom × lev/(lev+1)
    assert abs(return_on_capital(18.0, 3.0) - 13.5) < 1e-9    # ×0.75
    assert abs(return_on_capital(20.0, 4.0) - 16.0) < 1e-9    # ×0.80
    assert abs(return_on_capital(20.0, 1.0) - 10.0) < 1e-9    # ×0.50


def test_return_on_capital_zero_leverage_safe():
    assert return_on_capital(18.0, 0.0) == 0.0


# -- walk-forward: selekcja out-of-sample ----------------------------------- #
def test_walk_forward_selects_on_train_measures_on_test():
    rates = {"LOW": _const(0.0001, 900), "HIGH": _const(0.0003, 900)}
    wf = walk_forward(rates, train=300, test=150, top_n=1, round_trip_fee_bps=FEE)
    # top-1 na treningu MUSI wskazać HIGH (wyższy średni funding) w każdym oknie
    assert wf.windows
    for w in wf.windows:
        assert set(w.weights) == {"HIGH"}
    assert wf.n_test_periods == len(wf.windows) * 150


def test_walk_forward_annualized_positive_for_positive_regime():
    wf = walk_forward({"A": _const(0.0002, 900), "B": _const(0.00025, 900)},
                      train=300, test=300, round_trip_fee_bps=FEE)
    assert wf.annualized_net_pct > 0


def test_walk_forward_no_positive_asset_means_no_trade_window():
    # trening widzi tylko ujemny funding → okno nie handluje (wkład 0, uczciwie liczone)
    wf = walk_forward({"A": _const(-0.0002, 600)}, train=300, test=150, round_trip_fee_bps=FEE)
    assert all(w.weights == {} for w in wf.windows)
    assert wf.total_net_bps == 0.0
    assert wf.n_test_periods > 0                 # czas liczony mimo braku handlu


def test_walk_forward_raises_when_history_too_short():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        walk_forward({"A": _const(0.0002, 100)}, train=300, test=150)


def test_walk_forward_out_of_sample_not_wildly_above_in_sample():
    # na stacjonarnym reżimie OOS ≈ in-sample (selekcja to nie szczęście).
    rng_rates = {"BTC": _const(0.0001, 1200), "DOGE": _const(0.00028, 1200),
                 "XRP": _const(0.00023, 1200)}
    wf = walk_forward(rng_rates, train=300, test=150, top_n=2, round_trip_fee_bps=FEE)
    ins = simulate_portfolio(rng_rates, funding_weights(
        {"BTC": 1.0, "DOGE": 2.8, "XRP": 2.3}), round_trip_fee_bps=FEE)
    # OOS nie może być RAŻĄCO wyższy niż in-sample (to byłby sygnał błędu/look-ahead)
    assert wf.annualized_net_pct <= ins.annualized_net_pct * 1.2 + 1.0


# -- settle_per_year: poprawna annualizacja portfela/WF dla altów 4h --------- #
def test_simulate_portfolio_annualization_scales_with_settle_per_year():
    rates = {"A": _const(0.0002, 600)}
    p8 = simulate_portfolio(rates, {"A": 1.0}, round_trip_fee_bps=FEE)
    p4 = simulate_portfolio(rates, {"A": 1.0}, round_trip_fee_bps=FEE, settle_per_year=6 * 365)
    # ten sam per-period wynik, 4h = 2× więcej rozliczeń/rok → 2× wyższy roczny
    assert abs(p4.annualized_net_pct - 2 * p8.annualized_net_pct) < 1e-6


def test_walk_forward_uses_settle_per_year_for_annualization():
    rates = {"A": _const(0.00025, 900), "B": _const(0.0002, 900)}
    wf8 = walk_forward(rates, train=300, test=300, round_trip_fee_bps=FEE)
    wf4 = walk_forward(rates, train=300, test=300, round_trip_fee_bps=FEE, settle_per_year=6 * 365)
    assert wf4.annualized_net_pct > wf8.annualized_net_pct
    assert wf4.settle_per_year == 6 * 365


def test_walk_forward_default_settle_per_year_unchanged():
    # brak podania settle_per_year = stare zachowanie (8h)
    wf = walk_forward({"A": _const(0.0002, 600)}, train=300, test=150, round_trip_fee_bps=FEE)
    assert abs(wf.settle_per_year - 3 * 365) < 1e-9


# -- walk-forward: ryzyko OOS (drawdown/Calmar ze zszytej krzywej) ----------- #
def test_portfolio_returns_per_settlement_series():
    res = simulate_portfolio({"A": _const(0.0002, 50)}, {"A": 1.0}, round_trip_fee_bps=FEE)
    assert len(res.per_settlement_bps) == 50
    # suma przyrostów = wynik końcowy
    assert abs(sum(res.per_settlement_bps) - res.final_net_bps) < 1e-9


def test_walk_forward_reports_drawdown_on_negative_stretch():
    # dodatni reżim treningowy, potem mocny ujemny w oknie testowym → realne obsunięcie
    rates = {"A": _const(0.0003, 400) + _const(-0.0004, 200) + _const(0.0003, 400)}
    wf = walk_forward(rates, train=300, test=150, round_trip_fee_bps=FEE)
    assert wf.max_drawdown_pct > 0
    assert wf.worst_settlement_bps < 0
    assert wf.calmar is not None


def test_walk_forward_no_drawdown_when_monotonic():
    wf = walk_forward({"A": _const(0.0003, 600)}, train=300, test=150, round_trip_fee_bps=0.0)
    assert wf.max_drawdown_pct == 0.0
    assert wf.calmar is None            # brak obsunięcia → Calmar nieokreślony


def test_walk_forward_no_trade_windows_flat_equity_no_drawdown():
    # sam ujemny funding → wszystkie okna bez handlu → płaski kapitał, zero obsunięcia
    wf = walk_forward({"A": _const(-0.0002, 600)}, train=300, test=150, round_trip_fee_bps=FEE)
    assert wf.max_drawdown_pct == 0.0
    assert wf.total_net_bps == 0.0
