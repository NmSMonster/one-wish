"""Interfejs adaptera egzekucji.

`submit` przyjmuje OrderRequest i zwraca OrderResult (status + fille). Ten sam
interfejs implementuje PaperBrokerAdapter (M7) i — w M12 — realny adapter Binance
(domyślnie zablokowany).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ...core.types import Fill, OrderRequest, OrderStatus


@dataclass
class OrderResult:
    status: OrderStatus
    fills: list[Fill] = field(default_factory=list)
    reason: str = ""


class ExchangeAdapter(ABC):
    name: str = "abstract"
    is_live: bool = False

    @abstractmethod
    async def submit(self, req: OrderRequest) -> OrderResult:
        ...

    async def reconcile(self) -> list[dict]:
        """Zwraca otwarte zlecenia na giełdzie (do uzgodnienia po restarcie)."""
        return []
