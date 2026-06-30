"""FundingReconciler — uzgadnianie zainkasowanego funding: MODEL vs GIEŁDA.

Edge carry to suma drobnych przepływów funding co 8h. Model (`FundingAccrual`)
nalicza je teoretycznie; realne konto dostaje faktyczny FUNDING_FEE. Jeśli te dwie
liczby się rozjeżdżają, to sygnał błędu (zła wielkość pozycji, przegapione
rozliczenie, zła stawka) — a przy cienkim edge rozjazd zjada zysk. Reconciler
porównuje je per aktywo i podnosi flagę przy istotnej rozbieżności.

`model` bierzemy z eventów FUNDING_ACCRUED (sumujemy per aktywo), `real` z
income FUNDING_FEE z konta futures (zagregowane przez `aggregate_income`).
"""
from __future__ import annotations

from ..core.bus import EventBus
from ..core.events import Event, EventType
from ..core.types import BINANCE_SYMBOL, Asset

_SYMBOL_TO_ASSET = {sym: a for a, sym in BINANCE_SYMBOL.items()}


class FundingReconciler:
    SOURCE = "funding_reconciler"

    def __init__(self, *, tol_usd: float = 0.5, tol_frac: float = 0.05) -> None:
        # rozbieżność istotna dopiero gdy przekracza OBA progi (absolutny i względny)
        self.tol_usd = tol_usd
        self.tol_frac = tol_frac
        self.model_by_asset: dict[Asset, float] = {}

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(EventType.FUNDING_ACCRUED, self._on_funding)

    async def _on_funding(self, event: Event) -> None:
        p = event.payload
        if isinstance(p, dict) and "asset" in p and "amount" in p:
            try:
                a = Asset(p["asset"])
            except ValueError:
                return
            self.model_by_asset[a] = self.model_by_asset.get(a, 0.0) + float(p["amount"])

    @staticmethod
    def aggregate_income(records: list) -> dict[Asset, float]:
        """Sumuje surowe rekordy income FUNDING_FEE z Binance (per symbol) do USD
        per aktywo. Ignoruje inne typy income i nieznane symbole."""
        out: dict[Asset, float] = {}
        for r in records:
            if str(r.get("incomeType", "FUNDING_FEE")) != "FUNDING_FEE":
                continue
            asset = _SYMBOL_TO_ASSET.get(r.get("symbol"))
            if asset is None:
                continue
            out[asset] = out.get(asset, 0.0) + float(r.get("income", 0.0) or 0.0)
        return out

    def _diverged(self, model: float, real: float) -> bool:
        diff = abs(real - model)
        return diff > self.tol_usd and diff > self.tol_frac * max(abs(model), 1e-9)

    def reconcile(self, real_by_asset: dict) -> dict:
        """Porównuje model vs realny funding per aktywo. Zwraca raport per aktywo
        (model/real/diff/diverged) + totale i zbiorczą flagę rozbieżności."""
        assets = set(self.model_by_asset) | set(real_by_asset)
        per_asset: dict[str, dict] = {}
        model_total = real_total = 0.0
        any_div = False
        for a in sorted(assets, key=lambda x: x.value):
            model = self.model_by_asset.get(a, 0.0)
            real = real_by_asset.get(a, 0.0)
            diverged = self._diverged(model, real)
            per_asset[a.value] = {"model": model, "real": real,
                                  "diff": real - model, "diverged": diverged}
            model_total += model
            real_total += real
            any_div = any_div or diverged
        return {
            "per_asset": per_asset,
            "model_total": model_total,
            "real_total": real_total,
            "diff_total": real_total - model_total,
            "diverged": any_div,
        }
