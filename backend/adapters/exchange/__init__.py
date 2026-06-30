"""Adaptery egzekucji: paper (symulacja) i — w M12 — realny Binance (zablokowany)."""
from .base import ExchangeAdapter, OrderResult
from .paper import PaperBrokerAdapter

__all__ = ["ExchangeAdapter", "OrderResult", "PaperBrokerAdapter"]
