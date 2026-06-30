"""Testy budżetu kapitału (forward paper-trade z fikcyjnym kapitałem)."""
import asyncio

from backend.app.budget import (
    USD_PER_PLN,
    BudgetTracker,
    TwoWalletLedger,
    budget_risk_config,
    pln_to_usd,
    wallet_split,
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


# -- dwa portfele (spot vs futures) ----------------------------------------- #
def test_wallet_split_sums_to_budget():
    s = wallet_split(40.0, perp_leverage=3.0)
    assert abs(s["spot"] - 30.0) < 1e-9          # 40*3/4
    assert abs(s["futures"] - 10.0) < 1e-9       # 40/4
    assert abs(s["spot"] + s["futures"] - 40.0) < 1e-9


def test_two_wallet_from_budget_matches_split():
    led = TwoWalletLedger.from_budget(40.0, perp_leverage=3.0)
    assert abs(led.spot_balance - 30.0) < 1e-9
    assert abs(led.futures_balance - 10.0) < 1e-9


def test_two_wallet_committed_splits_legs():
    led = TwoWalletLedger(spot_balance=30.0, futures_balance=10.0, perp_leverage=3.0)
    book = _book_with_pair(notional=15.0, entry=1.0)   # spot 15, futures 15/3=5
    c = led.committed(book)
    assert abs(c["spot"] - 15.0) < 1e-9
    assert abs(c["futures"] - 5.0) < 1e-9
    f = led.free(book)
    assert abs(f["spot"] - 15.0) < 1e-9
    assert abs(f["futures"] - 5.0) < 1e-9


def test_two_wallet_can_open_requires_both_wallets():
    # spot bogaty, futures pusty → mimo wolnej gotówki spot NIE wolno otwierać
    led = TwoWalletLedger(spot_balance=100.0, futures_balance=0.0, perp_leverage=3.0)
    assert not led.can_open(10.0, PositionBook())
    # oba portfele wystarczające
    led2 = TwoWalletLedger(spot_balance=10.0, futures_balance=4.0, perp_leverage=3.0)
    assert led2.can_open(10.0, PositionBook())     # potrzeba spot 10, futures 3.33
    assert not led2.can_open(13.0, PositionBook())  # futures 4.33 > 4


def test_two_wallet_snapshot_structure():
    led = TwoWalletLedger.from_budget(37.5, perp_leverage=3.0)
    snap = led.snapshot(PositionBook())
    assert set(snap) == {"spot", "futures"}
    assert set(snap["spot"]) == {"balance", "committed", "free"}
