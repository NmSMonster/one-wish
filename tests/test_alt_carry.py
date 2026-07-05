"""Testy doradcy bezpiecznych wejść w carry na altach."""
from backend.strategy import AltCandidate, AltCarryAdvisor


def _advisor(**kw) -> AltCarryAdvisor:
    base = dict(base_notional_usd=100.0, min_funding_oos_pct=8.0, min_basis_buffer=0.40,
                min_depth_mult=10.0, min_history_days=180)
    base.update(kw)
    return AltCarryAdvisor(**base)


def _velvet(**kw) -> AltCandidate:
    base = dict(asset="VELVET", funding_oos_pct=16.0, worst_move_frac=1.466,
                depth_usd=5_000_000.0, history_days=354)
    base.update(kw)
    return AltCandidate(**base)


def test_good_alt_included_with_cross_margin_required():
    d = _advisor().evaluate(_velvet())
    assert d.include is True
    assert d.requires_cross_margin is True          # ZAWSZE cross dla altów
    assert d.max_leverage in (1.5, 2.0, 3.0)
    assert d.max_notional_usd > 0
    assert d.basis_buffer_frac >= 0.40


def test_low_funding_alt_excluded():
    d = _advisor().evaluate(_velvet(asset="HYPE", funding_oos_pct=3.0))
    assert d.include is False
    assert any("funding OOS" in r for r in d.reasons)
    assert d.max_notional_usd == 0.0


def test_fresh_contract_excluded():
    d = _advisor().evaluate(_velvet(history_days=90))   # < 180 dni
    assert d.include is False
    assert any("świeży" in r or "historia" in r for r in d.reasons)


def test_thin_liquidity_shrinks_or_excludes():
    # głębokość ledwie na mniejszy nominał → nominał ścięty, nadal included
    d = _advisor().evaluate(_velvet(depth_usd=500.0))   # 500/10 = 50$ bezpiecznego nominału
    assert d.max_notional_usd <= 50.0
    assert any("płynność" in r for r in d.reasons)


def test_zero_liquidity_excluded():
    d = _advisor().evaluate(_velvet(depth_usd=5.0))     # < min_depth_mult → <1$ nominału
    assert d.include is False
    assert any("płynność" in r for r in d.reasons)


def test_leverage_chosen_to_keep_basis_buffer():
    # przy bardzo dużym najgorszym ruchu doradca schodzi z dźwignią, by trzymać bufor
    calm = _advisor().evaluate(_velvet(worst_move_frac=0.3))
    wild = _advisor().evaluate(_velvet(worst_move_frac=3.0))
    assert calm.max_leverage >= wild.max_leverage      # dziksze → ostrożniejsza dźwignia (lub równa)
    assert wild.basis_buffer_frac >= 0.0


def test_evaluate_all_batch():
    adv = _advisor()
    cands = [_velvet(), _velvet(asset="HYPE", funding_oos_pct=3.0)]
    out = adv.evaluate_all(cands)
    assert [d.asset for d in out] == ["VELVET", "HYPE"]
    assert out[0].include is True and out[1].include is False


def test_cross_margin_always_required_even_if_excluded():
    d = _advisor().evaluate(_velvet(funding_oos_pct=1.0))
    assert d.include is False
    assert d.requires_cross_margin is True             # wymóg cross jest bezwarunkowy
