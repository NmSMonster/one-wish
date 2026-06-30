"""Testy budżetu kapitału (forward paper-trade z fikcyjnym kapitałem)."""
import asyncio

from backend.app.budget import (
    USD_PER_PLN,
    BudgetTracker,
    budget_risk_config,
    pln_to_usd,
)
from backend.app.runner import OneWishApp
from backend.core.types import Asset, Fill, Leg, Side
from backend.execution import PositionBook
from backend.risk.manager import RiskConfig


def _book_with_pair(notional=20.0, *, asset=Asset.XRP, entry=1.0) -> PositionBook:
    qty = notional / entry
    book = PositionBook()
    book.apply_fill(Fill("s", asset, Leg.SPOT, Side.BUY, entry, qty, 0.0, 1.0), 1.0)
    book.apply_fill(Fill("p", asset, Leg.PERP, Side.SELL, entry, qty, 0.0, 1.0), 1.0)
    return book


# -- konwersja -------------------------------------------------------------- #
def test_pln_to_usd():
    assert pln_to_usd(150.0) == 150.0 * USD_PER_PLN
    assert pln_to_usd(0.0) == 0.0


# -- budget_risk_config ----------------------------------------------------- #
def test_budget_risk_config_caps_exposure_to_budget():
    cfg = budget_risk_config(40.0, perp_leverage=3.0)
    # cap = 40 / (1 + 1/3) = 30
    assert abs(cfg.max_total_exposure_usd - 30.0) < 1e-9
    assert cfg.max_asset_exposure_usd == cfg.max_total_exposure_usd
    assert cfg.max_trade_notional_usd == cfg.max_total_exposure_usd
    assert cfg.perp_leverage == 3.0


def test_budget_risk_config_preserves_base_fields_and_overrides():
    base = RiskConfig(max_daily_loss_usd=7.5, max_open_positions=9)
    cfg = budget_risk_config(40.0, perp_leverage=3.0, base=base, max_open_positions=1)
    assert cfg.max_daily_loss_usd == 7.5      # zachowane z base
    assert cfg.max_open_positions == 1        # override


# -- BudgetTracker ---------------------------------------------------------- #
def test_committed_counts_spot_full_and_perp_margin():
    bt = BudgetTracker(budget_usd=100.0, perp_leverage=4.0)
    book = _book_with_pair(notional=20.0)     # spot 20 + perp 20/4 = 5 → 25
    assert abs(bt.committed(book) - 25.0) < 1e-9
    assert abs(bt.free(book) - 75.0) < 1e-9
    assert abs(bt.utilization(book) - 0.25) < 1e-9


def test_can_open_respects_free_budget():
    bt = BudgetTracker(budget_usd=30.0, perp_leverage=3.0)
    empty = PositionBook()
    # koszt pary = notional*(1+1/3); 22.5 * 1.333 = 30 → mieści się dokładnie
    assert bt.can_open(22.5, empty)
    assert not bt.can_open(25.0, empty)       # 33.3 > 30


def test_can_open_false_when_budget_used_up():
    bt = BudgetTracker(budget_usd=30.0, perp_leverage=3.0)
    book = _book_with_pair(notional=22.5, entry=1.0)   # zużywa ~30
    assert not bt.can_open(5.0, book)


def test_snapshot_structure_and_values():
    bt = BudgetTracker(budget_usd=40.0, perp_leverage=3.0)
    book = _book_with_pair(notional=30.0)     # spot 30 + perp 30/3=10 → 40 (pełne)
    snap = bt.snapshot(book)
    assert abs(snap["committed_usd"] - 40.0) < 1e-9
    assert abs(snap["free_usd"] - 0.0) < 1e-9
    assert abs(snap["utilization_pct"] - 100.0) < 1e-9


def test_empty_book_full_budget_free():
    bt = BudgetTracker(budget_usd=37.5)
    assert bt.free(PositionBook()) == 37.5
    assert bt.utilization(PositionBook()) == 0.0


# -- integracja z runnerem -------------------------------------------------- #
def test_runner_applies_budget_to_risk_config():
    app = OneWishApp(mode="synthetic", steps=10, gui=False, budget_pln=150.0)
    assert app.budget_tracker is not None
    assert abs(app.budget_usd - pln_to_usd(150.0)) < 1e-9
    # ekspozycja ograniczona do budżetu (nie domyślne 1500$)
    assert app.risk_config.max_total_exposure_usd < app.budget_usd
    assert app.risk_config.max_total_exposure_usd <= 1500.0


def test_runner_without_budget_unchanged():
    app = OneWishApp(mode="synthetic", steps=10, gui=False)
    assert app.budget_tracker is None
    assert app.risk_config.max_total_exposure_usd == RiskConfig().max_total_exposure_usd


def test_runner_with_budget_runs_green():
    app = OneWishApp(mode="synthetic", steps=120, seed=3, gui=False, budget_pln=150.0)
    report = asyncio.run(app.run())
    assert report.counts.get("MARKET_TICK", 0) > 0   # przeszło bez wyjątku z budżetem
