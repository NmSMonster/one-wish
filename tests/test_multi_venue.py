"""Testy Tier B (pomiar): parsery funding per venue + porównanie/uplift."""
import pytest

from backend.adapters.market.venues import (
    BYBIT_SYMBOL,
    OKX_INST,
    FundingPoint,
    parse_binance_funding,
    parse_bybit_funding,
    parse_okx_funding,
    venue_symbol,
)
from backend.core.types import ASSETS, Asset, Venue
from backend.research.multi_venue import (
    compare,
    infer_settles_per_year,
    quote,
    summarize,
)

_H = 3600.0


def _points(rate: float, n: int, interval_h: float = 8.0) -> list[FundingPoint]:
    return [FundingPoint(ts=i * interval_h * _H, rate=rate) for i in range(n)]


# -- parsery (fixture'y w realnych kształtach odpowiedzi) -------------------- #
def test_parse_bybit_shape():
    payload = {"retCode": 0, "result": {"list": [
        {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingRateTimestamp": "1700000000000"},
        {"symbol": "BTCUSDT", "fundingRate": "-0.00005", "fundingRateTimestamp": "1699971200000"},
        {"symbol": "BTCUSDT", "fundingRate": "zepsute", "fundingRateTimestamp": "x"},
    ]}}
    pts = parse_bybit_funding(payload)
    assert len(pts) == 2                                   # zepsuty wiersz pominięty
    assert pts[0].ts < pts[1].ts                           # chronologicznie
    assert pts[1].rate == pytest.approx(0.0001)


def test_parse_okx_shape():
    payload = {"code": "0", "data": [
        {"instId": "BTC-USDT-SWAP", "fundingRate": "0.0002", "fundingTime": "1700000000000"},
        {"instId": "BTC-USDT-SWAP", "fundingRate": "0.0001", "fundingTime": "1699971200000"},
    ]}
    pts = parse_okx_funding(payload)
    assert [p.rate for p in pts] == [pytest.approx(0.0001), pytest.approx(0.0002)]


def test_parse_binance_shape():
    payload = [{"fundingRate": "0.0001", "fundingTime": 1700000000000},
               {"fundingRate": "bad", "fundingTime": None}]
    pts = parse_binance_funding(payload)
    assert len(pts) == 1 and pts[0].rate == pytest.approx(0.0001)


def test_parsers_tolerate_empty_and_garbage():
    assert parse_bybit_funding({}) == []
    assert parse_okx_funding({"data": None}) == []
    assert parse_binance_funding(None) == []


# -- annualizacja z realnego interwału --------------------------------------- #
def test_infer_settles_per_year_by_interval():
    assert infer_settles_per_year(_points(0.0001, 10, 8.0)) == pytest.approx(1095.0)
    assert infer_settles_per_year(_points(0.0001, 10, 4.0)) == pytest.approx(2190.0)
    assert infer_settles_per_year(_points(0.0001, 10, 1.0)) == pytest.approx(8760.0)
    assert infer_settles_per_year([]) == pytest.approx(1095.0)     # fallback 8h


def test_quote_annualizes_with_real_interval():
    # 1 bps co 4h = 2190 rozliczeń → 21.9%/rok; przy złym założeniu 8h byłoby 10.95%
    q = quote(Venue.BYBIT, Asset.SOL, _points(0.0001, 20, 4.0))
    assert q.annualized_pct == pytest.approx(21.9, abs=0.01)


# -- porównanie i uplift ------------------------------------------------------ #
def test_best_venue_and_uplift():
    cmpn = compare(Asset.BTC, {
        Venue.BINANCE: _points(0.0001, 20, 8.0),           # 10.95%/rok
        Venue.BYBIT: _points(0.00015, 20, 8.0),            # 16.43%/rok
        Venue.OKX: _points(0.00005, 20, 8.0),              # 5.48%/rok
    })
    assert cmpn.best.venue == Venue.BYBIT
    assert cmpn.uplift_vs(Venue.BINANCE) == pytest.approx(5.475, abs=0.01)


def test_uplift_none_when_base_missing():
    cmpn = compare(Asset.HYPE, {Venue.BYBIT: _points(0.0001, 10, 8.0)})
    assert cmpn.uplift_vs(Venue.BINANCE) is None           # nie ma bazy → nie porównuj


def test_summarize_sorts_by_uplift_and_reports_all_venues():
    rows = summarize([
        compare(Asset.BTC, {Venue.BINANCE: _points(0.0001, 10),
                            Venue.BYBIT: _points(0.0001, 10)}),      # uplift 0
        compare(Asset.SOL, {Venue.BINANCE: _points(0.0001, 10),
                            Venue.OKX: _points(0.0003, 10)}),        # uplift duży
    ])
    assert rows[0]["asset"] == "SOL" and rows[0]["best_venue"] == "OKX"
    assert rows[0]["uplift_pp"] > rows[1]["uplift_pp"]
    assert "BINANCE" in rows[0]["venues"] and "OKX" in rows[0]["venues"]


# -- mapy symboli -------------------------------------------------------------- #
def test_symbol_maps_cover_all_assets_or_explicit_none():
    for a in ASSETS:
        assert a in BYBIT_SYMBOL and a in OKX_INST         # świadoma decyzja per aktywo
        assert venue_symbol(Venue.BINANCE, a) is not None  # baza zawsze notuje
    assert venue_symbol(Venue.BYBIT, Asset.VELVET) is None  # brak notowania = pomiń
    assert venue_symbol(Venue.OKX, Asset.BTC) == "BTC-USDT-SWAP"
