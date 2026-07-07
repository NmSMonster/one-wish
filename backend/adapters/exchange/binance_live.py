"""BinanceLiveAdapter (M12) — realny transport zleceń. DOMYŚLNIE ZABLOKOWANY.

To jedyne miejsce, które może wysłać prawdziwe zlecenie. Obwarowane wieloma
bramkami; realny transport (podpisane zlecenia spot + USDT-M perp) jest
zaimplementowany, ale uruchamia się WYŁĄCZNIE po jawnym włączeniu i — dla
bezpieczeństwa pilota — domyślnie tylko na TESTNECIE (fałszywe pieniądze).

Żeby submit() w ogóle wysłał zlecenie, MUSZĄ być spełnione wszystkie warunki:
  1. live_enabled = True             (świadoma decyzja / konfiguracja),
  2. osobne klucze API z env         (ONEWISH_BINANCE_KEY/SECRET, nigdy z repo),
  3. jawne arm(confirm=token)        (potwierdzenie operatora),
  4. transport_implemented = True    (świadome włączenie realnej wysyłki),
  5. testnet = True LUB allow_mainnet (mainnet wymaga osobnej, jawnej zgody),
  6. nominał ≤ max_notional_usd      (twardy limit pojedynczego zlecenia).

Strategia jest delta-neutral z natury: long spot + short USDT-M perp, bez dźwigni
na spot, bez shortów spot. Transport składa zlecenia MARKET z idempotentnym
client_order_id (ten sam coid = brak duplikatu na giełdzie).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from ...core.types import BINANCE_SYMBOL, Asset, Fill, Leg, OrderRequest, OrderStatus, Side
from .base import ExchangeAdapter, OrderResult

log = logging.getLogger("onewish.binance_live")

_CONFIRM_TOKEN = "I_UNDERSTAND_REAL_MONEY"

# Endpointy: testnet (pilot, fałszywe pieniądze) vs mainnet (realne pieniądze).
_SPOT_TESTNET = "https://testnet.binance.vision"
_FUT_TESTNET = "https://testnet.binancefuture.com"
_SPOT_MAINNET = "https://api.binance.com"
_FUT_MAINNET = "https://fapi.binance.com"

_SPOT_ORDER_PATH = "/api/v3/order"
_FUT_ORDER_PATH = "/fapi/v1/order"
_SPOT_OPEN_ORDERS = "/api/v3/openOrders"
_FUT_OPEN_ORDERS = "/fapi/v1/openOrders"
_SPOT_ACCOUNT = "/api/v3/account"
_FUT_BALANCE = "/fapi/v2/balance"
_FUT_INCOME = "/fapi/v1/income"


class LiveTradingBlocked(RuntimeError):
    """Podniesione, gdy próbuje się uzbroić adapter bez spełnienia zabezpieczeń."""


class BinanceLiveAdapter(ExchangeAdapter):
    name = "binance_live"
    is_live = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        live_enabled: bool = False,
        max_notional_usd: float = 25.0,
        transport_implemented: bool = False,  # świadomie False — brak realnej wysyłki
        testnet: bool = True,                 # pilot domyślnie na testnecie
        allow_mainnet: bool = False,          # mainnet wymaga osobnej, jawnej zgody
        timeout: float = 8.0,
        recv_window_ms: int = 5000,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.live_enabled = live_enabled
        self.max_notional_usd = max_notional_usd
        self.transport_implemented = transport_implemented
        self.testnet = testnet
        self.allow_mainnet = allow_mainnet
        self.timeout = timeout
        self.recv_window_ms = recv_window_ms
        self._armed = False
        self.spot_base = _SPOT_TESTNET if testnet else _SPOT_MAINNET
        self.fut_base = _FUT_TESTNET if testnet else _FUT_MAINNET

    @classmethod
    def from_env(cls, *, live_enabled: bool = False, max_notional_usd: float = 25.0,
                 transport_implemented: bool = False, testnet: bool = True,
                 allow_mainnet: bool = False) -> "BinanceLiveAdapter":
        return cls(
            api_key=os.environ.get("ONEWISH_BINANCE_KEY"),
            api_secret=os.environ.get("ONEWISH_BINANCE_SECRET"),
            live_enabled=live_enabled,
            max_notional_usd=max_notional_usd,
            transport_implemented=transport_implemented,
            testnet=testnet,
            allow_mainnet=allow_mainnet,
        )

    @property
    def is_armed(self) -> bool:
        return self._armed

    def arm(self, confirm: str) -> None:
        """Podwójne zabezpieczenie uruchomienia live. Musi przejść wszystkie bramki."""
        if not self.live_enabled:
            raise LiveTradingBlocked("live_trading_enabled = false (konfiguracja blokuje live)")
        if confirm != _CONFIRM_TOKEN:
            raise LiveTradingBlocked("brak/niepoprawny token potwierdzenia operatora")
        if not (self.api_key and self.api_secret):
            raise LiveTradingBlocked("brak osobnych kluczy API (env ONEWISH_BINANCE_KEY/SECRET)")
        self._armed = True
        net = "TESTNET" if self.testnet else "MAINNET"
        log.warning("BinanceLiveAdapter UZBROJONY (%s, transport=%s).", net, self.transport_implemented)

    def disarm(self) -> None:
        self._armed = False

    # -- podpisywanie i transport (jedyny styk I/O) ------------------------- #
    def _sign(self, query: str) -> str:
        """HMAC-SHA256 query stringa kluczem sekretnym (podpis zlecenia Binance)."""
        return hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    def _signed_request(self, method: str, base: str, path: str, params: dict):
        """Podpisane zapytanie REST do Binance. Blokujące — wołaj w executorze.
        To jedyne miejsce realnego I/O; w testach jest stubowane."""
        p = dict(params)
        p["timestamp"] = int(time.time() * 1000)
        p["recvWindow"] = self.recv_window_ms
        query = urllib.parse.urlencode(p)
        url = f"{base}{path}?{query}&signature={self._sign(query)}"
        req = urllib.request.Request(
            url, method=method,
            headers={"X-MBX-APIKEY": self.api_key, "User-Agent": "one-wish/0.1"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def _base_and_path(self, leg: Leg) -> tuple[str, str]:
        if leg == Leg.SPOT:
            return self.spot_base, _SPOT_ORDER_PATH
        return self.fut_base, _FUT_ORDER_PATH

    @staticmethod
    def _parse_order(resp: dict, req: OrderRequest) -> OrderResult:
        """Zamienia odpowiedź Binance (spot lub futures MARKET) na OrderResult."""
        executed = float(resp.get("executedQty", 0.0) or 0.0)

        fills = resp.get("fills")
        if fills:  # spot: lista fillów z ceną/ilością/prowizją
            qty = sum(float(f["qty"]) for f in fills)
            notional = sum(float(f["price"]) * float(f["qty"]) for f in fills)
            avg = notional / qty if qty > 0 else float(req.price)
            fee = sum(float(f.get("commission", 0.0) or 0.0) for f in fills)
            executed = executed or qty
        else:      # futures: avgPrice; spot GET order: cummulativeQuoteQty/executedQty
            cqq = float(resp.get("cummulativeQuoteQty", 0.0) or 0.0)
            avg = float(resp.get("avgPrice", 0.0) or 0.0)
            if not avg and executed > 0 and cqq > 0:
                avg = cqq / executed
            avg = avg or float(req.price)
            fee = 0.0   # prowizja nie zawsze w odpowiedzi → reconcile/ledger

        raw = str(resp.get("status", "")).upper()
        try:
            status = OrderStatus(raw)
        except ValueError:
            status = OrderStatus.FILLED if executed > 0 else OrderStatus.REJECTED

        if executed <= 0:
            return OrderResult(OrderStatus.REJECTED, [], resp.get("msg", "brak wypełnienia"))

        fill = Fill(req.client_order_id, req.asset, req.leg, req.side,
                    avg, executed, fee, req.ts)
        return OrderResult(status, [fill])

    async def _send_order(self, req: OrderRequest) -> OrderResult:
        symbol = BINANCE_SYMBOL[req.asset]
        base, path = self._base_and_path(req.leg)
        params = {
            "symbol": symbol,
            "side": req.side.value,
            "type": req.order_type.value,
            "quantity": _fmt_qty(req.qty),
            "newClientOrderId": req.client_order_id,
        }
        # Zamknięcie nogi perp MUSI redukować pozycję. Bez reduceOnly na koncie w
        # hedge-mode BUY otworzyłby LONGA zamiast domknąć shorta — hedge by pękł,
        # a bot myślałby, że jest flat. (One-way mode: reduceOnly też chroni przed
        # przekręceniem pozycji na drugą stronę przy rozjechanym stanie.)
        if req.leg == Leg.PERP and req.intent_action == "CLOSE":
            params["reduceOnly"] = "true"
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, self._signed_request, "POST", base, path, params)
        return self._parse_order(resp, req)

    async def submit(self, req: OrderRequest) -> OrderResult:
        if not self._armed:
            return OrderResult(OrderStatus.REJECTED, [], "LIVE ZABLOKOWANY — adapter nieuzbrojony")
        if req.price <= 0:
            # bez ceny referencyjnej nie da się zweryfikować limitu nominału — a limit
            # jest po to, żeby JEDEN błąd nie wysłał dużego zlecenia. Nie zgadujemy.
            return OrderResult(OrderStatus.REJECTED, [],
                               "brak ceny referencyjnej (price<=0) — limit nominału niesprawdzalny")
        notional = abs(req.price * req.qty)
        if notional > self.max_notional_usd:
            return OrderResult(OrderStatus.REJECTED, [],
                               f"nominał {notional:.2f}$ > limit {self.max_notional_usd:.2f}$")
        if not self.transport_implemented:
            return OrderResult(OrderStatus.REJECTED, [],
                               "realny transport zleceń wyłączony w tej wersji (pilot)")
        if not self.testnet and not self.allow_mainnet:
            return OrderResult(OrderStatus.REJECTED, [],
                               "mainnet zablokowany — transport dozwolony tylko na testnecie")
        try:
            return await self._send_order(req)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            # ack zgubiony: zlecenie mogło dojść do giełdy → uncertain (reconcile przed retry)
            log.warning("Błąd transportu zlecenia %s: %s", req.client_order_id, exc)
            return OrderResult(OrderStatus.REJECTED, [], f"błąd transportu: {exc}", uncertain=True)

    async def reconcile(self) -> list[dict]:
        """Otwarte zlecenia na giełdzie (spot + futures) do uzgodnienia po restarcie."""
        if not (self._armed and self.transport_implemented):
            return []
        loop = asyncio.get_event_loop()
        try:
            spot = await loop.run_in_executor(None, self._signed_request,
                                              "GET", self.spot_base, _SPOT_OPEN_ORDERS, {})
            fut = await loop.run_in_executor(None, self._signed_request,
                                             "GET", self.fut_base, _FUT_OPEN_ORDERS, {})
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            log.warning("Reconcile nieudany: %s", exc)
            return []
        return list(spot or []) + list(fut or [])

    async def account_state(self) -> dict:
        """Stan konta: salda spot + futures (do sizingu i kontroli marginu)."""
        if not (self._armed and self.transport_implemented):
            return {}
        loop = asyncio.get_event_loop()
        try:
            spot = await loop.run_in_executor(None, self._signed_request,
                                              "GET", self.spot_base, _SPOT_ACCOUNT, {})
            fut = await loop.run_in_executor(None, self._signed_request,
                                             "GET", self.fut_base, _FUT_BALANCE, {})
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            log.warning("Pobranie stanu konta nieudane: %s", exc)
            return {}
        return {"spot": spot, "futures": fut}

    async def funding_income(self, *, symbol: str | None = None, start_ms: int | None = None,
                             end_ms: int | None = None, limit: int = 1000) -> list[dict]:
        """Realnie zainkasowany funding (FUNDING_FEE) z konta futures — do uzgodnienia
        z modelem (FundingReconciler). Pusta lista, gdy transport wyłączony/błąd."""
        if not (self._armed and self.transport_implemented):
            return []
        params: dict = {"incomeType": "FUNDING_FEE", "limit": limit}
        if symbol:
            params["symbol"] = symbol
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._signed_request,
                                              "GET", self.fut_base, _FUT_INCOME, params)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            log.warning("Pobranie funding income nieudane: %s", exc)
            return []

    async def query_order(self, asset: Asset, leg: Leg, coid: str) -> OrderResult | None:
        """Stan zlecenia po client_order_id (reconcile-before-retry przy zgubionym ack).
        None = zlecenia nie ma na giełdzie (nie złożone). Wyjątek = stanu nie ustalono."""
        if not (self._armed and self.transport_implemented):
            return None
        base, path = self._base_and_path(leg)
        params = {"symbol": BINANCE_SYMBOL[asset], "origClientOrderId": coid}
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(None, self._signed_request, "GET", base, path, params)
        except urllib.error.HTTPError as exc:
            if _is_order_absent(exc):
                return None              # -2013 Order does not exist → nie złożone
            raise                        # inny błąd HTTP → nieznany stan (OM przerwie)
        return _parse_query_order(resp, asset, leg, coid)


def _is_order_absent(exc: urllib.error.HTTPError) -> bool:
    """Czy odpowiedź błędu to Binance -2013 'Order does not exist.'"""
    try:
        data = json.loads(exc.read().decode("utf-8"))
        return int(data.get("code", 0)) == -2013
    except (ValueError, OSError, AttributeError):
        return False


def _parse_query_order(resp: dict, asset: Asset, leg: Leg, coid: str) -> OrderResult:
    """Zamienia odpowiedź GET order na OrderResult (z fillem, jeśli coś wykonane)."""
    executed = float(resp.get("executedQty", 0.0) or 0.0)
    raw = str(resp.get("status", "")).upper()
    try:
        status = OrderStatus(raw)
    except ValueError:
        status = OrderStatus.FILLED if executed > 0 else OrderStatus.REJECTED
    if executed <= 0:
        return OrderResult(status, [], "zlecenie istnieje, brak wypełnienia")
    side = Side(resp.get("side", "BUY"))
    cqq = float(resp.get("cummulativeQuoteQty", 0.0) or 0.0)
    avg = float(resp.get("avgPrice", 0.0) or 0.0)
    if not avg and cqq > 0:
        avg = cqq / executed
    avg = avg or float(resp.get("price", 0.0) or 0.0)
    ts = float(resp.get("updateTime", 0.0) or 0.0) / 1000.0
    return OrderResult(status, [Fill(coid, asset, leg, side, avg, executed, 0.0, ts)])


def _fmt_qty(qty: float) -> str:
    """Formatuje ilość bez notacji naukowej, bez zbędnych zer (kwantyzację do kroku
    symbolu robi wcześniej OrderManager/quantizer — tu tylko czysty zapis)."""
    s = f"{qty:.8f}".rstrip("0").rstrip(".")
    return s or "0"
