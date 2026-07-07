"""Publiczne (read-only) źródła historii funding z innych giełd — fundament Tier B.

Tier B = multi-venue carry: short perp tam, gdzie funding (annualizowany) płaci
najwięcej; spot na najpłynniejszym venue. Zanim powstanie egzekucja, potrzebny
jest POMIAR — czy przewaga innych venue jest realna i trwała, czy iluzoryczna.

Jak wszędzie w projekcie: parsery są czyste (testowane na fixture'ach), jedyny
styk I/O to `_http_get_json`. Symbole, których venue nie listuje, pomijamy
z gracją (None w mapie → aktywo poza zasięgiem tego venue).
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from ...core.types import Asset, Venue

_BYBIT_BASE = "https://api.bybit.com"
_OKX_BASE = "https://www.okx.com"

#: Symbole per venue. None = venue nie listuje (albo świadomie poza zasięgiem).
BYBIT_SYMBOL: dict[Asset, str | None] = {
    Asset.BTC: "BTCUSDT", Asset.ETH: "ETHUSDT", Asset.SOL: "SOLUSDT",
    Asset.XRP: "XRPUSDT", Asset.DOGE: "DOGEUSDT", Asset.ZEC: "ZECUSDT",
    Asset.VELVET: None, Asset.TAC: None, Asset.HYPE: "HYPEUSDT",
}
OKX_INST: dict[Asset, str | None] = {
    Asset.BTC: "BTC-USDT-SWAP", Asset.ETH: "ETH-USDT-SWAP", Asset.SOL: "SOL-USDT-SWAP",
    Asset.XRP: "XRP-USDT-SWAP", Asset.DOGE: "DOGE-USDT-SWAP", Asset.ZEC: "ZEC-USDT-SWAP",
    Asset.VELVET: None, Asset.TAC: None, Asset.HYPE: "HYPE-USDT-SWAP",
}


@dataclass(frozen=True)
class FundingPoint:
    ts: float      # epoch s rozliczenia
    rate: float    # stawka na JEDNO rozliczenie (ułamek, np. 0.0001 = 1bps)


def _http_get_json(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={"User-Agent": "one-wish/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


# -- parsery (czyste — testowane offline na fixture'ach) --------------------- #
def parse_bybit_funding(payload: dict) -> list[FundingPoint]:
    """GET /v5/market/funding/history → result.list[{fundingRate, fundingRateTimestamp}]."""
    rows = ((payload.get("result") or {}).get("list")) or []
    out = []
    for r in rows:
        try:
            out.append(FundingPoint(ts=float(r["fundingRateTimestamp"]) / 1000.0,
                                    rate=float(r["fundingRate"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda p: p.ts)


def parse_okx_funding(payload: dict) -> list[FundingPoint]:
    """GET /api/v5/public/funding-rate-history → data[{fundingRate, fundingTime}]."""
    rows = payload.get("data") or []
    out = []
    for r in rows:
        try:
            out.append(FundingPoint(ts=float(r["fundingTime"]) / 1000.0,
                                    rate=float(r["fundingRate"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda p: p.ts)


def parse_binance_funding(payload: list) -> list[FundingPoint]:
    """GET /fapi/v1/fundingRate → [{fundingRate, fundingTime}] (kształt jak w
    funding_study; parser tutaj dla jednolitego typu FundingPoint w porównaniu)."""
    out = []
    for r in payload or []:
        try:
            out.append(FundingPoint(ts=float(r["fundingTime"]) / 1000.0,
                                    rate=float(r["fundingRate"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda p: p.ts)


# -- pobieranie (jedyny styk I/O) -------------------------------------------- #
def fetch_bybit_funding(symbol: str, limit: int = 200, timeout: float = 8.0) -> list[FundingPoint]:
    url = (_BYBIT_BASE + "/v5/market/funding/history"
           f"?category=linear&symbol={symbol}&limit={limit}")
    return parse_bybit_funding(_http_get_json(url, timeout))


def fetch_okx_funding(inst_id: str, limit: int = 100, timeout: float = 8.0) -> list[FundingPoint]:
    url = _OKX_BASE + f"/api/v5/public/funding-rate-history?instId={inst_id}&limit={limit}"
    return parse_okx_funding(_http_get_json(url, timeout))


def venue_symbol(venue: Venue, asset: Asset) -> str | None:
    from ...core.types import BINANCE_SYMBOL
    if venue == Venue.BINANCE:
        return BINANCE_SYMBOL.get(asset)
    if venue == Venue.BYBIT:
        return BYBIT_SYMBOL.get(asset)
    if venue == Venue.OKX:
        return OKX_INST.get(asset)
    return None
