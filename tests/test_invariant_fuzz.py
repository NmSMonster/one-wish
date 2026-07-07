"""Fuzz inwariantu bezpieczeństwa: „delta-neutral albo flat — a jeśli świat
odmówi, GŁOŚNA eskalacja. NIGDY cichy orphan leg."

Pojedyncze testy chaosu sprawdzają wybrane scenariusze. Fuzz sprawdza PRZESTRZEŃ
scenariuszy: broker losowo odrzuca, częściowo wypełnia i gubi acki (z losowym
wynikiem uzgodnienia) przez wiele ziaren i wiele cykli otwórz/zamknij — a
własność systemowa musi trzymać po KAŻDEJ operacji:

    pozycja flat  LUB  zbilansowana  LUB  (niezbilansowana I wyemitowany
    EMERGENCY_STOP — risk kill + alert operatora + retry domknięcia).

Ten fuzz ZNALAZŁ realną lukę (cichy orphan po nieudanej kompensacji, gdy giełda
odrzuca także zlecenia zamykające) — naprawioną eskalacją w OrderManagerze.
"""
import asyncio
import random

from backend.adapters.exchange.base import ExchangeAdapter, OrderResult
from backend.core.bus import EventBus
from backend.core.events import EventType
from backend.core.types import Asset, Fill, Leg, OrderRequest, OrderStatus, Side
from backend.execution import OrderManager, PositionBook

_TOL = 1e-9


class RandomChaosBroker(ExchangeAdapter):
    """Broker-chaos: na każde zlecenie losuje reject / partial / pełny fill /
    zgubiony ack. Przy zgubionym acku zlecenie MOGŁO się wykonać — query_order
    zwraca spójną odpowiedź (fill, który realnie „zaszedł", albo None)."""
    name = "random_chaos"
    is_live = False

    def __init__(self, seed: int, *, p_reject=0.25, p_partial=0.25, p_uncertain=0.2) -> None:
        self._rng = random.Random(seed)
        self.p_reject = p_reject
        self.p_partial = p_partial
        self.p_uncertain = p_uncertain
        self._ghost_fills: dict[str, Fill] = {}   # coid → fill wykonany mimo zgubionego acku

    def _fill(self, req: OrderRequest, qty: float) -> Fill:
        return Fill(req.client_order_id, req.asset, req.leg, req.side,
                    req.price, qty, 0.0, req.ts)

    async def submit(self, req: OrderRequest) -> OrderResult:
        r = self._rng.random()
        if r < self.p_reject:
            return OrderResult(OrderStatus.REJECTED, [], "chaos: reject")
        r -= self.p_reject
        if r < self.p_uncertain:
            # zgubiony ack: w połowie przypadków zlecenie NAPRAWDĘ się wykonało
            if self._rng.random() < 0.5:
                self._ghost_fills[req.client_order_id] = self._fill(req, req.qty)
            return OrderResult(OrderStatus.REJECTED, [], "chaos: lost ack", uncertain=True)
        r -= self.p_uncertain
        if r < self.p_partial:
            return OrderResult(OrderStatus.PARTIALLY_FILLED,
                               [self._fill(req, req.qty * self._rng.uniform(0.2, 0.8))])
        return OrderResult(OrderStatus.FILLED, [self._fill(req, req.qty)])

    async def query_order(self, asset, leg, coid):
        ghost = self._ghost_fills.pop(coid, None)
        if ghost is None:
            return None                            # nie złożone → bezpieczne ponowienie
        return OrderResult(OrderStatus.FILLED, [ghost])


def _make(seed: int, **chaos):
    broker = RandomChaosBroker(seed, **chaos)
    book = PositionBook()
    bus = EventBus()
    emergencies: list = []
    bus.subscribe(EventType.EMERGENCY_STOP, lambda e: emergencies.append(e.payload))
    om = OrderManager(broker, book, max_attempts=3, bus=bus)
    return om, book, emergencies


def _assert_property(book: PositionBook, asset: Asset, emergencies: list, ctx: str) -> None:
    """Własność systemowa: flat LUB zbilansowana LUB głośna eskalacja.
    „Niezbilansowana" liczona z zapasem 2× ponad tolerancję OM (0.05), żeby test
    nie flagował przypadków brzegowych, które OM świadomie uznaje za balans."""
    pos = book.position(asset)
    if pos is None or not pos.is_open:
        return                                     # flat = OK
    one_leg_dead = abs(pos.spot_qty) < _TOL or abs(pos.perp_qty) < _TOL
    gross_delta = abs(pos.net_delta) > 0.10 * max(abs(pos.spot_qty), abs(pos.perp_qty), _TOL)
    if one_leg_dead or gross_delta:
        # niezbilansowana JEST dopuszczalna wyłącznie z głośną eskalacją
        assert emergencies, (f"CICHY ORPHAN ({ctx}): spot={pos.spot_qty} "
                             f"perp={pos.perp_qty} — zero eskalacji")


def test_property_holds_across_random_chaos_seeds():
    for seed in range(60):
        om, book, emergencies = _make(seed)
        rng = random.Random(1000 + seed)

        async def scenario():
            for i in range(12):
                if rng.random() < 0.6:
                    qty = rng.choice([0.01, 0.5, 1.0])
                    await om.open_pair(Asset.BTC, qty, qty, 100.0, 100.0)
                    _assert_property(book, Asset.BTC, emergencies, f"seed={seed} op={i} open")
                else:
                    await om.close_pair(Asset.BTC, spot_px=rng.uniform(80, 140),
                                        perp_px=rng.uniform(80, 140))
                    _assert_property(book, Asset.BTC, emergencies, f"seed={seed} op={i} close")

        asyncio.run(scenario())


def test_property_holds_under_pure_lost_ack_storm():
    """Najgroźniejsza ścieżka osobno: WYŁĄCZNIE zgubione acki (połowa wykonana).
    Po każdej próbie: własność systemowa + zero podwójnych filli (idempotencja)."""
    for seed in range(30):
        om, book, emergencies = _make(seed, p_reject=0.0, p_partial=0.0, p_uncertain=1.0)

        async def scenario():
            opens = 0
            for i in range(6):
                await om.open_pair(Asset.ETH, 1.0, 1.0, 2000.0, 2000.0)
                opens += 1
                _assert_property(book, Asset.ETH, emergencies, f"lost-ack seed={seed} op={i}")
                pos = book.position(Asset.ETH)
                if pos is not None and pos.is_open:
                    # zero PODWÓJNYCH filli z retry po zgubionym acku: sumaryczna
                    # wielkość nie może przekroczyć łącznie ZAMÓWIONEJ ilości
                    # (OM legalnie dokłada do żywej pozycji — duplikaty wejść
                    # blokuje polityka, nie OM; stąd limit = liczba open × qty)
                    assert abs(pos.spot_qty) <= opens + _TOL
                    assert abs(pos.perp_qty) <= opens + _TOL
                await om.close_pair(Asset.ETH, spot_px=2100.0, perp_px=2050.0)
                _assert_property(book, Asset.ETH, emergencies, f"lost-ack seed={seed} close={i}")

        asyncio.run(scenario())


def test_silent_orphan_regression_escalates_loudly():
    """Deterministyczna regresja luki znalezionej fuzzem: spot nie wchodzi w ogóle,
    perp wchodzi, a potem giełda odrzuca WSZYSTKO (też kompensację). Przed fixem:
    ciche ABORTED z gołym shortem. Po fixie: EMERGENCY_STOP z powodem orphan."""

    class SpotDeadThenAllDead(ExchangeAdapter):
        name = "spot_dead_then_all_dead"
        is_live = False

        def __init__(self):
            self.calls = 0

        async def submit(self, req: OrderRequest) -> OrderResult:
            self.calls += 1
            if req.leg == Leg.PERP and req.side == Side.SELL:
                return OrderResult(OrderStatus.FILLED,
                                   [Fill(req.client_order_id, req.asset, req.leg, req.side,
                                         req.price, req.qty, 0.0, req.ts)])
            return OrderResult(OrderStatus.REJECTED, [], "dead")

    book = PositionBook()
    bus = EventBus()
    emergencies: list = []
    bus.subscribe(EventType.EMERGENCY_STOP, lambda e: emergencies.append(e.payload))
    om = OrderManager(SpotDeadThenAllDead(), book, max_attempts=2, bus=bus)

    asyncio.run(om.open_pair(Asset.BTC, 1.0, 1.0, 100.0, 100.0))

    pos = book.position(Asset.BTC)
    assert pos is not None and pos.perp_qty < 0          # goły short istnieje...
    assert emergencies, "orphan leg MUSI eskalować EMERGENCY_STOP"
    assert "orphan" in emergencies[0]["reason"]

    # eskalacja raz per aktywo (bez zalewania szyny) — kolejna próba nie dubluje
    asyncio.run(om.close_pair(Asset.BTC, spot_px=100.0, perp_px=100.0))
    assert len(emergencies) == 1
