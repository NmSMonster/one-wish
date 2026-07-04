"""Testy oceny ryzyka likwidacji nogi short (ryzyko niewidoczne dla funding-study)."""
from backend.research.liquidation_risk import (
    assess_short_liquidation,
    liquidation_move_frac,
    max_run_up,
)


def test_liquidation_move_frac_matches_leverage():
    assert abs(liquidation_move_frac(3.0, 0.02) - (1 / 3 - 0.02)) < 1e-9   # ~+31.3%
    assert abs(liquidation_move_frac(5.0, 0.02) - (0.2 - 0.02)) < 1e-9     # +18%
    assert abs(liquidation_move_frac(1.0, 0.0) - 1.0) < 1e-9               # 1x → +100%


def test_liquidation_move_frac_zero_leverage_safe():
    assert liquidation_move_frac(0.0, 0.02) == float("inf")


def test_max_run_up_finds_worst_upward_move():
    prices = [100, 90, 150, 80, 100]     # od 90 do 150 = +66.7%
    assert abs(max_run_up(prices, len(prices)) - (150 / 90 - 1)) < 1e-9


def test_max_run_up_respects_window():
    prices = [100, 101, 102, 200]        # skok do 200 dopiero na końcu
    # okno 1: najgorszy ruch w obrębie 2 sąsiednich barów (102→200 = +96%)
    assert abs(max_run_up(prices, 1) - (200 / 102 - 1)) < 1e-6


def test_max_run_up_flat_series_zero():
    assert max_run_up([100, 100, 100], 2) == 0.0


def test_assess_flags_liquidation_when_move_exceeds_threshold():
    # +40% skok: likwiduje 3x (próg ~31%), NIE likwiduje... sprawdźmy oba
    highs = [100, 140, 100, 100]
    a = assess_short_liquidation(highs, window=1, leverages=(3.0, 5.0), mmr=0.02)
    assert a.worst_window_up > 0.39
    assert a.breaches[3.0]["liquidated"] is True      # +40% > +31.3% → likwidacja
    assert a.breaches[5.0]["liquidated"] is True      # +40% > +18% → tym bardziej


def test_assess_no_liquidation_for_calm_series():
    highs = [100, 105, 103, 107, 104]                 # maks ~+7%
    a = assess_short_liquidation(highs, window=2, leverages=(3.0,), mmr=0.02)
    assert a.breaches[3.0]["liquidated"] is False     # spokojny → short przeżywa
    assert a.worst_window_up < 0.31


def test_assess_reports_metadata():
    a = assess_short_liquidation([100, 110, 105], window=1, symbol="VELVETUSDT")
    assert a.symbol == "VELVETUSDT"
    assert a.n_bars == 3


def test_intrabar_up_from_opens():
    # bar 2: open 100, high 130 → +30% wewnątrz bara
    highs = [100, 130, 105]
    opens = [99, 100, 104]
    a = assess_short_liquidation(highs, opens, window=1)
    assert abs(a.worst_1bar_up - 0.30) < 1e-9


def test_worst_1bar_zero_without_opens():
    a = assess_short_liquidation([100, 130, 105], window=1)   # brak opens
    assert a.worst_1bar_up == 0.0
