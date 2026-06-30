"""Testy wzbogacenia danych: forward funding, mark/premium, open interest."""
from collections import defaultdict

from backend.adapters.market.binance_public import BinancePublicSource, predict_funding
from backend.adapters.market.synthetic import _DEFAULT_PRICES
from backend.core.types import ASSETS, BINANCE_SYMBOL, Asset, MarketTick
from backend.risk.margin import DEFAULT_MAINTENANCE_BY_ASSET


def _tick(**kw) -> MarketTick:
    base = dict(asset=Asset.BTC, ts=1.0, spot=100.0, perp=100.5, index=100.0,
                funding_rate=0.0, predicted_funding=0.0, next_funding_ts=2.0,
                spot_bid=99.9, spot_ask=100.1, perp_bid=100.4, perp_ask=100.6,
                spot_depth_usd=1e6, perp_depth_usd=1e6)
    base.update(kw)
    return MarketTick(**base)


def test_predict_funding_premium_clamped():
    # premia 0.1% (mark 100.1 vs index 100), interest 0.01% → clamp do -0.05%
    # funding = 0.001 + clamp(0.0001 - 0.001) = 0.001 - 0.0005 = 0.0005
    assert abs(predict_funding(100.1, 100.0, 0.0001) - 0.0005) < 1e-12


def test_predict_funding_small_premium_equals_interest():
    # mark == index → premia 0 → funding = interest_rate
    assert abs(predict_funding(100.0, 100.0, 0.0001) - 0.0001) < 1e-12


def test_predict_funding_negative_premium():
    # mark poniżej index → premia ujemna → funding ujemny (short płaci)
    f = predict_funding(99.9, 100.0, 0.0001)
    assert f < 0


def test_predict_funding_zero_index_safe():
    assert predict_funding(100.0, 0.0, 0.0001) == 0.0


def test_market_tick_new_fields_default_zero():
    t = _tick()
    assert t.mark_price == 0.0
    assert t.open_interest_usd == 0.0
    assert t.interest_rate == 0.0
    assert t.premium_bps == 0.0          # mark=0 → property bezpieczna


def test_market_tick_premium_bps():
    t = _tick(mark_price=100.2)           # (100.2-100)/100 = 0.002 = 20 bps
    assert abs(t.premium_bps - 20.0) < 1e-6


# -- spójność rozszerzonego uniwersum --------------------------------------- #
_EXPANDED = {Asset.DOGE, Asset.ZEC, Asset.VELVET, Asset.TAC, Asset.HYPE}


def test_expanded_universe_present():
    assert _EXPANDED.issubset(set(ASSETS))
    assert _EXPANDED.issubset(set(Asset))


def test_every_asset_has_binance_symbol_usdt():
    for a in ASSETS:
        assert a in BINANCE_SYMBOL
        assert BINANCE_SYMBOL[a].endswith("USDT")


def test_every_asset_has_maintenance_and_synth_price():
    for a in ASSETS:
        assert a in DEFAULT_MAINTENANCE_BY_ASSET, f"brak bracketu maintenance dla {a}"
        assert DEFAULT_MAINTENANCE_BY_ASSET[a] > 0
        assert a in _DEFAULT_PRICES, f"brak ceny demo dla {a}"


def test_riskier_alts_have_higher_maintenance_than_btc():
    # nowsze/mniej płynne alty muszą mieć NIE niższy bracket niż BTC (konserwatywnie)
    for a in _EXPANDED:
        assert DEFAULT_MAINTENANCE_BY_ASSET[a] >= DEFAULT_MAINTENANCE_BY_ASSET[Asset.BTC]


# -- Optymalizacja recordera: kadencja cen vs OI/głębokość ------------------ #
SYMBOL = "BTCUSDT"


def _fake_get(counter: dict, *, depth_value: float = 5.0, raise_heavy: bool = False):
    """Stub _get: liczy zapytania per kategoria; ceny zawsze działają, ciężkie
    (OI/głębokość) opcjonalnie rzucają, by sprawdzić zachowanie stale-on-error."""
    def fake(url: str):
        is_fapi = "fapi.binance.com" in url
        if "ticker/bookTicker" in url and not is_fapi:
            counter["spot_book"] += 1
            return [{"symbol": SYMBOL, "bidPrice": "100", "askPrice": "100.2"}]
        if "premiumIndex" in url:
            counter["prem"] += 1
            return [{"symbol": SYMBOL, "indexPrice": "100", "markPrice": "100.1",
                     "lastFundingRate": "0.0001", "interestRate": "0.0001",
                     "nextFundingTime": "2000", "time": "1000"}]
        if "ticker/bookTicker" in url and is_fapi:
            counter["fut_book"] += 1
            return [{"symbol": SYMBOL, "bidPrice": "100.05", "askPrice": "100.15"}]
        if "openInterest" in url:
            counter["oi"] += 1
            if raise_heavy:
                raise OSError("net")
            return {"openInterest": "10"}
        if "depth" in url:
            key = "perp_depth" if is_fapi else "spot_depth"
            counter[key] += 1
            if raise_heavy:
                raise OSError("net")
            return {"asks": [["100", str(depth_value)]], "bids": [["99", str(depth_value)]]}
        raise AssertionError(f"nieoczekiwany URL: {url}")
    return fake


def _src(**kw) -> BinancePublicSource:
    return BinancePublicSource(assets=(Asset.BTC,), fetch_depth=True,
                               fetch_extras=True, **kw)


def test_heavy_fetch_cached_between_cycles():
    counter = defaultdict(int)
    src = _src(slow_every=3)
    src._get = _fake_get(counter)

    src._fetch_once(refresh_heavy=True)    # cykl 0: ceny + ciężkie
    src._fetch_once(refresh_heavy=False)   # cykl 1: tylko ceny (cache)
    src._fetch_once(refresh_heavy=False)   # cykl 2: tylko ceny (cache)

    assert counter["prem"] == 3            # ceny pobrane co cykl
    assert counter["oi"] == 1              # OI tylko raz (cache)
    assert counter["spot_depth"] == 1      # głębokość tylko raz (cache)
    assert counter["perp_depth"] == 1


def test_cached_depth_and_oi_used_between_refreshes():
    counter = defaultdict(int)
    src = _src(slow_every=5)
    src._get = _fake_get(counter)

    src._fetch_once(refresh_heavy=True)
    t = src._fetch_once(refresh_heavy=False)[0]

    assert t.spot_depth_usd == 100 * 5     # asks top: cena×ilość z cache
    assert t.perp_depth_usd == 99 * 5      # bids top
    assert abs(t.open_interest_usd - 10 * 100.1) < 1e-9   # coiny z cache × świeży mark


def test_heavy_error_keeps_stale_cache():
    counter = defaultdict(int)
    src = _src()
    src._get = _fake_get(counter, depth_value=5.0)
    src._fetch_once(refresh_heavy=True)    # zapełnia cache dobrymi wartościami

    src._get = _fake_get(counter, raise_heavy=True)
    src._fetch_once(refresh_heavy=True)    # próba odświeżenia kończy się błędem
    t = src._fetch_once(refresh_heavy=False)[0]

    assert t.spot_depth_usd == 100 * 5     # zachowana ostatnia dobra wartość
    assert abs(t.open_interest_usd - 10 * 100.1) < 1e-9


def test_first_cycle_without_cache_uses_default_depth():
    counter = defaultdict(int)
    src = _src(depth_usd_default=777.0)
    src._get = _fake_get(counter, raise_heavy=True)   # ciężkie padają od razu

    t = src._fetch_once(refresh_heavy=True)[0]

    assert t.spot_depth_usd == 777.0       # brak cache → placeholder
    assert t.open_interest_usd == 0.0      # brak OI w cache → 0
