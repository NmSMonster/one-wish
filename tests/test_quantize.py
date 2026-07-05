"""Testy: filtry symbolu i kwantyzacja zleceń."""
from backend.core.types import Asset, Leg
from backend.execution.quantize import FilterSet, SymbolFilters, floor_to_step


def test_floor_to_step_exact():
    assert floor_to_step(0.123456, 0.001) == 0.123
    assert floor_to_step(100.127, 0.01) == 100.12
    assert floor_to_step(7.0, 0.0) == 7.0          # brak kroku → bez zmian
    assert floor_to_step(0.0009, 0.001) == 0.0     # poniżej kroku → 0


def test_symbol_filters_quantize_and_validate():
    f = SymbolFilters("BTCUSDT", tick_size=0.1, step_size=0.001,
                      min_qty=0.001, min_notional=10.0)
    assert f.q_price(60000.17) == 60000.1
    assert f.q_qty(0.0123456) == 0.012
    assert f.ok(60000.0, 0.001) is True            # 60$ ≥ 10$, qty ≥ minQty
    assert f.ok(60000.0, 0.0001) is False          # poniżej minQty


def test_filterset_quantize_and_passthrough():
    fs = FilterSet(
        spot={Asset.BTC: SymbolFilters("BTCUSDT", 0.01, 0.00001, 0.00001, 5.0)},
        perp={Asset.BTC: SymbolFilters("BTCUSDT", 0.1, 0.001, 0.001, 5.0)},
    )
    # spot: drobniejszy step
    q_qty, q_price = fs.quantize(Asset.BTC, Leg.SPOT, 0.0123456, 100.117)
    assert q_qty == 0.01234 and q_price == 100.11
    # perp: grubszy step
    q_qty, q_price = fs.quantize(Asset.BTC, Leg.PERP, 0.0123456, 100.117)
    assert q_qty == 0.012 and q_price == 100.1
    # brak filtrów dla aktywa → passthrough
    assert fs.quantize(Asset.ETH, Leg.SPOT, 1.2345, 9.99) == (1.2345, 9.99)


def test_order_manager_with_quantizer_rounds_qty():
    import asyncio

    from backend.adapters.exchange import PaperBrokerAdapter
    from backend.core.clock import SimClock
    from backend.execution import OrderManager, PositionBook

    fs = FilterSet(
        spot={Asset.BTC: SymbolFilters("BTCUSDT", 0.01, 0.001, 0.001, 1.0)},
        perp={Asset.BTC: SymbolFilters("BTCUSDT", 0.01, 0.001, 0.001, 1.0)},
    )
    book = PositionBook()
    om = OrderManager(PaperBrokerAdapter(slippage_bps=0.0), book, clock=SimClock(), quantizer=fs)
    # qty 0.0129 → kwantyzacja do 0.012
    asyncio.run(om.open_pair(Asset.BTC, 0.0129, 0.0129, 100.0, 100.0))
    pos = book.position(Asset.BTC)
    assert abs(pos.spot_qty - 0.012) < 1e-9
    assert abs(pos.perp_qty + 0.012) < 1e-9        # short


def test_order_manager_rejects_below_min_notional_and_stays_flat():
    """Regresja: filtr minNotional MUSI być egzekwowany PRZED wysyłką. Zlecenie
    poniżej minimum giełdy nie idzie na giełdę (byłby reject/orphan) — obie nogi
    odrzucone, pozycja pozostaje FLAT (invariant delta-neutral albo flat)."""
    import asyncio

    from backend.adapters.exchange import PaperBrokerAdapter
    from backend.core.clock import SimClock
    from backend.execution import OrderManager, PositionBook
    from backend.execution.order_manager import PairState

    # minNotional 50$, a nominał zlecenia = 0.001 * 100 = 0.1$ → poniżej minimum
    fs = FilterSet(
        spot={Asset.BTC: SymbolFilters("BTCUSDT", 0.01, 0.0001, 0.0001, 50.0)},
        perp={Asset.BTC: SymbolFilters("BTCUSDT", 0.01, 0.0001, 0.0001, 50.0)},
    )
    book = PositionBook()
    om = OrderManager(PaperBrokerAdapter(slippage_bps=0.0), book, clock=SimClock(), quantizer=fs)
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.001, 0.001, 100.0, 100.0))
    assert pair.state in (PairState.ABORTED, PairState.OPEN)
    assert not book.is_open(Asset.BTC)             # nic nie wysłane → FLAT, zero orphana
