"""BinanceLiveAdapter (M12) — realny handel. DOMYŚLNIE ZABLOKOWANY.

To jest jedyne miejsce, które mogłoby wysłać prawdziwe zlecenie za prawdziwe
pieniądze — dlatego jest obwarowane PODWÓJNYM zabezpieczeniem i w tej wersji
celowo NIE wysyła zleceń (realny transport zostawiamy do świadomego wdrożenia po
pozytywnej walidacji edge — patrz EDGE_VALIDATION.md).

Aby adapter w ogóle mógł cokolwiek wysłać, MUSZĄ być spełnione wszystkie warunki:
  1. live_enabled = True            (z konfiguracji / świadomej decyzji),
  2. obecne osobne klucze API       (z env, nigdy z repo),
  3. jawne arm(confirm=…)           (token potwierdzenia operatora),
  4. nominał ≤ max_notional_usd     (twardy limit pojedynczego zlecenia).
Nawet po uzbrojeniu submit() zwraca REJECTED, bo realny transport jest wyłączony.
Brak dźwigni, brak shortów spot — strategia jest delta-neutral z natury.
"""
from __future__ import annotations

import logging
import os

from ...core.types import OrderRequest, OrderStatus
from .base import ExchangeAdapter, OrderResult

log = logging.getLogger("onewish.binance_live")

_CONFIRM_TOKEN = "I_UNDERSTAND_REAL_MONEY"


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
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.live_enabled = live_enabled
        self.max_notional_usd = max_notional_usd
        self.transport_implemented = transport_implemented
        self._armed = False

    @classmethod
    def from_env(cls, *, live_enabled: bool = False, max_notional_usd: float = 25.0) -> "BinanceLiveAdapter":
        return cls(
            api_key=os.environ.get("ONEWISH_BINANCE_KEY"),
            api_secret=os.environ.get("ONEWISH_BINANCE_SECRET"),
            live_enabled=live_enabled,
            max_notional_usd=max_notional_usd,
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
        log.warning("BinanceLiveAdapter UZBROJONY (realny transport nadal wyłączony).")

    def disarm(self) -> None:
        self._armed = False

    async def submit(self, req: OrderRequest) -> OrderResult:
        if not self._armed:
            return OrderResult(OrderStatus.REJECTED, [], "LIVE ZABLOKOWANY — adapter nieuzbrojony")
        notional = abs(req.price * req.qty)
        if notional > self.max_notional_usd:
            return OrderResult(OrderStatus.REJECTED, [],
                               f"nominał {notional:.2f}$ > limit {self.max_notional_usd:.2f}$")
        if not self.transport_implemented:
            # Bezpiecznik ostateczny: realna wysyłka jest świadomie niezaimplementowana.
            return OrderResult(OrderStatus.REJECTED, [],
                               "realny transport zleceń wyłączony w tej wersji (pilot)")
        # Tu w przyszłości: podpisane zlecenie do Binance (spot + USDT-M perp).
        return OrderResult(OrderStatus.REJECTED, [], "nieosiągalne")

    async def reconcile(self) -> list[dict]:
        # W realnym wdrożeniu: pobierz otwarte zlecenia z giełdy i uzgodnij ze stanem.
        return []
