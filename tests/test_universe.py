"""Testy: ranking uniwersum po carry yield."""
from backend.research.universe import (
    AssetCarry,
    filter_liquid,
    filter_quality,
    rank_by_carry,
)


def test_rank_orders_by_smoothed_net_desc():
    items = [
        ("LOWUSDT", [0.00005] * 60, 200e6),    # niski funding
        ("HIGHUSDT", [0.0004] * 60, 300e6),    # wysoki funding
        ("MIDUSDT", [0.0002] * 60, 100e6),
    ]
    ranked = rank_by_carry(items)
    assert [a.symbol for a in ranked] == ["HIGHUSDT", "MIDUSDT", "LOWUSDT"]
    assert ranked[0].smoothed_net_pct > ranked[-1].smoothed_net_pct


def test_filter_liquid_removes_thin():
    ranked = [
        AssetCarry("A", 60, 20.0, 90.0, 18.0, 500e6),
        AssetCarry("B", 60, 30.0, 85.0, 25.0, 10e6),    # cienki
    ]
    out = filter_liquid(ranked, min_volume_usd=50e6)
    assert [a.symbol for a in out] == ["A"]


def test_filter_quality_removes_short_history_and_unstable():
    ranked = [
        AssetCarry("GOOD", 1200, 20.0, 85.0, 18.0, 500e6),     # długa historia, stabilny
        AssetCarry("FRESH", 80, 60.0, 50.0, 57.0, 800e6),      # świeży listing, niestabilny
        AssetCarry("CHOPPY", 1200, 5.0, 60.0, 9.0, 500e6),     # długi, ale mało dodatnich
    ]
    out = filter_quality(ranked, min_n=1000, min_pct_positive=75.0)
    assert [a.symbol for a in out] == ["GOOD"]


def test_filter_quality_excludes_negative_mean_funding():
    # 81% dodatnich, ale średnia −46%/rok (ogromne ujemne spike'i) → odpada
    ranked = [
        AssetCarry("TRAP", 1200, -46.0, 81.0, 12.0, 360e6),
        AssetCarry("REAL", 1200, 20.0, 85.0, 18.0, 500e6),
    ]
    out = filter_quality(ranked, min_n=1000, min_pct_positive=75.0, min_annualized_pct=5.0)
    assert [a.symbol for a in out] == ["REAL"]
