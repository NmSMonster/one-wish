"""Filtry symbolu Binance i kwantyzacja zleceń.

Realne zlecenia muszą trafiać w siatkę giełdy: tickSize (cena), stepSize (ilość),
minQty, minNotional. Inaczej → reject albo dust (resztki psujące delta-neutralność).
Floaty są OK w paperze, ale przy realnych zleceniach kwantyzujemy precyzyjnie
(Decimal floor). Moduł jest opt-in: bez FilterSet OrderManager działa jak dotąd.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from ..core.types import ASSETS, BINANCE_SYMBOL, Asset, Leg

_SPOT_BASE = "https://api.binance.com"
_FUT_BASE = "https://fapi.binance.com"


def floor_to_step(value: float, step: float) -> float:
    """Zaokrągla w dół do wielokrotności `step` (dokładnie, bez fp-dustu)."""
    if step <= 0:
        return value
    d = Decimal(str(value))
    s = Decimal(str(step))
    return float((d / s).to_integral_value(rounding=ROUND_DOWN) * s)


@dataclass
class SymbolFilters:
    symbol: str
    tick_size: float = 0.0
    step_size: float = 0.0
    min_qty: float = 0.0
    min_notional: float = 0.0

    def q_price(self, price: float) -> float:
        return floor_to_step(price, self.tick_size) if self.tick_size > 0 else price

    def q_qty(self, qty: float) -> float:
        return floor_to_step(qty, self.step_size) if self.step_size > 0 else qty

    def ok(self, price: float, qty: float) -> bool:
        return qty >= self.min_qty and price * qty >= self.min_notional


class FilterSet:
    def __init__(self, spot: dict, perp: dict) -> None:
        self.spot = spot
        self.perp = perp

    def get(self, asset: Asset, leg: Leg) -> SymbolFilters | None:
        return (self.perp if leg == Leg.PERP else self.spot).get(asset)

    def quantize(self, asset: Asset, leg: Leg, qty: float, price: float) -> tuple[float, float]:
        f = self.get(asset, leg)
        if f is None:
            return qty, price
        return f.q_qty(qty), f.q_price(price)


def _parse(info: dict, assets: tuple[Asset, ...]) -> dict:
    wanted = {BINANCE_SYMBOL[a]: a for a in assets}
    out: dict = {}
    for s in info.get("symbols", []):
        sym = s.get("symbol")
        if sym not in wanted:
            continue
        tick = step = min_qty = min_notional = 0.0
        for f in s.get("filters", []):
            ft = f.get("filterType")
            if ft == "PRICE_FILTER":
                tick = float(f.get("tickSize", 0) or 0)
            elif ft == "LOT_SIZE":
                step = float(f.get("stepSize", 0) or 0)
                min_qty = float(f.get("minQty", 0) or 0)
            elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                min_notional = float(f.get("minNotional", f.get("notional", 0)) or 0)
        out[wanted[sym]] = SymbolFilters(sym, tick, step, min_qty, min_notional)
    return out


def _get(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "one-wish/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_filters(assets: tuple[Asset, ...] = ASSETS, timeout: float = 8.0) -> FilterSet:
    spot = _parse(_get(_SPOT_BASE + "/api/v3/exchangeInfo", timeout), assets)
    perp = _parse(_get(_FUT_BASE + "/fapi/v1/exchangeInfo", timeout), assets)
    return FilterSet(spot, perp)
