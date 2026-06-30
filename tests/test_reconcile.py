"""Testy uzgadniania funding: model (FUNDING_ACCRUED) vs realny income z giełdy."""
import asyncio

from backend.adapters.exchange.binance_live import BinanceLiveAdapter
from backend.core.bus import EventBus
from backend.core.events import Event, EventType
from backend.core.types import Asset
from backend.monitoring import FundingReconciler


def _armed_transport() -> BinanceLiveAdapter:
    a = BinanceLiveAdapter(live_enabled=True, api_key="k", api_secret="s",
                           transport_implemented=True, testnet=True)
    a.arm("I_UNDERSTAND_REAL_MONEY")
    return a


# -- agregacja income ------------------------------------------------------- #
def test_aggregate_income_maps_symbols_and_sums():
    recs = [
        {"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": "0.10"},
        {"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": "0.05"},
        {"symbol": "ETHUSDT", "incomeType": "FUNDING_FEE", "income": "0.20"},
    ]
    out = FundingReconciler.aggregate_income(recs)
    assert abs(out[Asset.BTC] - 0.15) < 1e-9
    assert abs(out[Asset.ETH] - 0.20) < 1e-9


def test_aggregate_income_ignores_other_types_and_unknown_symbols():
    recs = [
        {"symbol": "BTCUSDT", "incomeType": "COMMISSION", "income": "9.0"},   # nie funding
        {"symbol": "ZZZUSDT", "incomeType": "FUNDING_FEE", "income": "1.0"},  # nieznany symbol
        {"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": "0.30"},
    ]
    out = FundingReconciler.aggregate_income(recs)
    assert out == {Asset.BTC: 0.30}


# -- akumulacja modelu z eventów -------------------------------------------- #
def test_model_accumulates_from_funding_accrued_events():
    bus = EventBus()
    rec = FundingReconciler()
    rec.attach(bus)

    async def run():
        for amt in (0.10, 0.05):
            await bus.publish(Event(EventType.FUNDING_ACCRUED, 1.0, "f",
                                    payload={"asset": "BTC", "amount": amt, "funding_rate": 0.0001}))
    asyncio.run(run())
    assert abs(rec.model_by_asset[Asset.BTC] - 0.15) < 1e-9


# -- reconcile -------------------------------------------------------------- #
def test_reconcile_matches_within_tolerance():
    rec = FundingReconciler(tol_usd=0.5, tol_frac=0.05)
    rec.model_by_asset = {Asset.BTC: 1.00}
    out = rec.reconcile({Asset.BTC: 1.02})       # diff 0.02 < tol
    assert out["per_asset"]["BTC"]["diverged"] is False
    assert out["diverged"] is False
    assert abs(out["diff_total"] - 0.02) < 1e-9


def test_reconcile_flags_divergence_beyond_both_tolerances():
    rec = FundingReconciler(tol_usd=0.5, tol_frac=0.05)
    rec.model_by_asset = {Asset.BTC: 1.00}
    out = rec.reconcile({Asset.BTC: 3.00})       # diff 2.0 > 0.5 i > 5%
    assert out["per_asset"]["BTC"]["diverged"] is True
    assert out["diverged"] is True


def test_reconcile_small_absolute_diff_not_flagged_even_if_large_pct():
    rec = FundingReconciler(tol_usd=0.5, tol_frac=0.05)
    rec.model_by_asset = {Asset.BTC: 0.01}
    out = rec.reconcile({Asset.BTC: 0.02})       # +100% ale tylko 0.01$ < tol_usd
    assert out["per_asset"]["BTC"]["diverged"] is False


def test_reconcile_covers_assets_in_either_side():
    rec = FundingReconciler()
    rec.model_by_asset = {Asset.BTC: 1.0}
    out = rec.reconcile({Asset.ETH: 0.5})        # ETH tylko po stronie giełdy
    assert set(out["per_asset"]) == {"BTC", "ETH"}
    assert out["per_asset"]["ETH"]["model"] == 0.0
    assert out["per_asset"]["BTC"]["real"] == 0.0


# -- integracja end-to-end -------------------------------------------------- #
def test_end_to_end_model_vs_aggregated_income():
    rec = FundingReconciler()
    rec.model_by_asset = {Asset.BTC: 0.15, Asset.ETH: 0.20}
    income = [
        {"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": "0.15"},
        {"symbol": "ETHUSDT", "incomeType": "FUNDING_FEE", "income": "0.18"},
    ]
    out = rec.reconcile(FundingReconciler.aggregate_income(income))
    assert out["per_asset"]["BTC"]["diverged"] is False
    assert abs(out["per_asset"]["ETH"]["diff"] + 0.02) < 1e-9   # real 0.18 - model 0.20


# -- adapter funding_income ------------------------------------------------- #
def test_funding_income_builds_params_and_returns_records():
    a = _armed_transport()
    captured = {}

    def fake(method, base, path, params):
        captured.update(method=method, base=base, path=path, params=params)
        return [{"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": "0.1"}]
    a._signed_request = fake

    out = asyncio.run(a.funding_income(symbol="BTCUSDT", start_ms=1000))
    assert len(out) == 1
    assert captured["method"] == "GET" and captured["path"].endswith("/income")
    assert captured["params"]["incomeType"] == "FUNDING_FEE"
    assert captured["params"]["symbol"] == "BTCUSDT"
    assert captured["params"]["startTime"] == 1000


def test_funding_income_empty_when_transport_off():
    a = BinanceLiveAdapter(live_enabled=True, api_key="k", api_secret="s",
                           transport_implemented=False, testnet=True)
    a.arm("I_UNDERSTAND_REAL_MONEY")
    assert asyncio.run(a.funding_income()) == []
